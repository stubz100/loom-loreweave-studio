"""Orchestrator app factory + handshake (M0) + generate/queue + grid (M1/M2).

Run (dev):
    python -m orchestrator.main        # prints its bound URL + token, then serves

M2: POST /generate enqueues an N-image batch onto the single-worker runner; the UI
polls /jobs and streams results into a grid; /outputs/<name> serves the PNGs.

M5: generation is **project-scoped**. `POST /project` (loom init) creates a workspace
on the work disk (empty-folder + free-space validated); the durable queue + outputs +
per-job logs live inside `<project>/`. `/generate` 409s until a project is open; the
last project re-opens on launch (queue resume-paused). `/project/estimate` projects the
PNG-master footprint to suggest a size cap.
"""

from __future__ import annotations

import os
import random
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Support both `python -m orchestrator.main` (package) and a direct run.
try:
    from .config import CONFIG
    from . import __version__, SCHEMA_VERSION
    from .adapters import JobSpec
    from .adapters import zimage as zimage_adapter
    from .adapters import multi as multi_adapter
    from .adapters import sd35 as sd35_adapter
    from .adapters import flux2 as flux2_adapter
    from .adapters import krea2 as krea2_adapter
    from .adapters import birefnet as birefnet_adapter
    from .adapters import identity as identity_adapter
    from .adapters import face_restore as face_restore_adapter
    from .adapters import ltxv as ltxv_adapter
    from .adapters import frame_harvest as frame_harvest_adapter
    from .runner import RUNNER, WORKER_REAP, ADAPTERS, estimate_vram
    from . import projects
    from . import workspace as ws_mod
    from . import components
    from .diskguard import DiskGuard
    from . import logsetup
    from . import bible
    from . import assets
    from . import postproc
    from . import coverage
    from . import model_catalog
    from . import recipe
    from . import training
except ImportError:  # pragma: no cover - direct-run convenience
    from config import CONFIG  # type: ignore
    from adapters import JobSpec  # type: ignore
    from adapters import zimage as zimage_adapter  # type: ignore
    from adapters import multi as multi_adapter  # type: ignore
    from adapters import sd35 as sd35_adapter  # type: ignore
    from adapters import flux2 as flux2_adapter  # type: ignore
    from adapters import krea2 as krea2_adapter  # type: ignore
    from adapters import birefnet as birefnet_adapter  # type: ignore
    from adapters import identity as identity_adapter  # type: ignore
    from adapters import face_restore as face_restore_adapter  # type: ignore
    from adapters import ltxv as ltxv_adapter  # type: ignore
    from adapters import frame_harvest as frame_harvest_adapter  # type: ignore
    from runner import RUNNER, WORKER_REAP, ADAPTERS, estimate_vram  # type: ignore
    import projects  # type: ignore
    import workspace as ws_mod  # type: ignore
    import components  # type: ignore
    from diskguard import DiskGuard  # type: ignore
    import logsetup  # type: ignore
    import bible  # type: ignore
    import assets  # type: ignore
    import postproc  # type: ignore
    import coverage  # type: ignore
    import model_catalog  # type: ignore
    import recipe  # type: ignore
    import training  # type: ignore
    __version__ = "0.0.1"
    SCHEMA_VERSION = 1


# Cached launch report (set by the gate at startup, refreshed after a fetch) so /health
# can report launch_ok without re-running the presence checks every probe.
_LAUNCH: dict = {"launch_ok": True}


_STARTED_AT = time.time()
MAX_BATCH = 8  # batch cap for the smoke grid (≤ R38's cap)

# Disk guard (M6, §9): reads the active workspace, wakes the runner when a hard-stop
# clears so dispatch-held jobs resume.
GUARD = DiskGuard(get_workspace=lambda: RUNNER.workspace, on_change=RUNNER.wake,
                  poll_s=CONFIG.disk_poll_s)
LOG = logsetup.get_logger()


class GenerateRequest(BaseModel):
    """M2 generate payload — an N-image batch (count) fired into the grid.

    `extra="forbid"` so unknown/unsupported params 422 instead of being silently
    dropped (review #1). Only **t2i** is wired at P0; img2img/inpaint (with
    init_image/mask_image/strength + mode-specific validation) arrive in **P1**.
    """

    model_config = ConfigDict(extra="forbid")

    pipeline: Literal["zimage", "multi", "sd35", "flux2", "krea2"] = "zimage"
    mode: Literal["t2i", "ideate", "img2img", "inpaint", "ref"] = "t2i"
    prompt: str
    count: int = Field(default=3, ge=1, le=MAX_BATCH)
    # P1/M2 multi casting: one cast = ONE job → a pool of num_candidates × pipelines
    # candidates (R38: num_candidates ≤ 5). `ideation_mode` picks the weight preset.
    num_candidates: int = Field(default=2, ge=1, le=5)
    ideation_mode: Literal["fast", "refined"] = "fast"
    seed: int | None = None         # if set, image i uses seed+i; else random per image
    # Validated at the API boundary (review #2) so bad dims fail BEFORE a model load:
    # zimage requires width/height divisible by 16.
    width: int = Field(default=1280, ge=256, le=2048, multiple_of=16)
    height: int = Field(default=720, ge=256, le=2048, multiple_of=16)
    model_name: str | None = None
    num_steps: int | None = Field(default=None, ge=1, le=200)
    guidance_scale: float | None = Field(default=None, ge=0.0, le=30.0)
    negative_prompt: str | None = None
    # P1/M3 Stage-B expansion (img2img/inpaint via zimage/sd35). init_image/mask_image are
    # output names relative to the active project's out/ (the hero / a prior candidate) —
    # resolved + traversal-guarded server-side. strength: img2img 0..1 sweep.
    init_image: str | None = None
    mask_image: str | None = None
    strength: float | None = Field(default=None, ge=0.0, le=1.0)
    # P1/M3 catalog-validated tunables channel: model-specific / long-tail params (e.g. sd35
    # skip-layer-guidance, prompt-3, dtype; zimage cfg-normalization, attention-backend) keyed
    # by their catalog name. Validated against GET /models for the chosen pipeline+mode, then
    # mapped to CLI flags by model_catalog.emit_argv. (Common params stay top-level above.)
    params: dict = Field(default_factory=dict)
    dry_run: bool = False           # return the argv without running the GPU job (testing)
    # P1/M1: scope a batch to an AssetProfile version (lineage stage + requester); the L1
    # style fragment auto-prepends unless apply_style is unticked (the R104 override).
    asset_id: str | None = None
    version_id: str | None = None   # default = the asset's active version
    stage: Literal["A", "B", "C"] | None = None
    # Tri-state (review): True/False = explicit per-gen override (R104); None/omitted =
    # fall back to the StoryBible's saved `enabled_default`.
    apply_style: bool | None = None
    # Which L1 style to apply (2026-06-13: styles are a collection); None = the active default.
    style_id: str | None = None

    @field_validator("prompt")
    @classmethod
    def _prompt_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("prompt must not be empty")
        return v


class CreateProjectRequest(BaseModel):
    """`loom init` payload (M5/P0-9). `dest` is the (empty) project folder; `format`
    defaults to Wan 1280×720 (R56) and `size_cap_gb` to 250 (R164) when omitted."""

    model_config = ConfigDict(extra="forbid")
    dest: str
    name: str
    format: dict | None = None
    size_cap_gb: float = Field(default=ws_mod.DEFAULT_SIZE_CAP_GB, ge=ws_mod.MIN_SIZE_CAP_GB)


class OpenProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class EstimateRequest(BaseModel):
    """Footprint estimator inputs (R161/R164): episode length × resolution × fps."""

    model_config = ConfigDict(extra="forbid")
    length_s: float = Field(ge=0)
    width: int = Field(default=1280, ge=16)
    height: int = Field(default=720, ge=16)
    fps: int = Field(default=24, ge=1)
    size_cap_gb: float | None = None


class CreateAssetRequest(BaseModel):
    """Create an AssetProfile (P1/M1). `asset_class` defaults to characters."""

    model_config = ConfigDict(extra="forbid")
    name: str
    asset_class: Literal["characters", "props", "scenes"] = "characters"


class StyleRequest(BaseModel):
    """Edit a style's fragment/global-negative (the ACTIVE one unless `style_id` given) + the
    story-level default-on gate (R104 auto-append; M8 global negative). 2026-06-13: L1 styles
    are a COLLECTION — see /bible/styles for add/delete/set-active."""

    model_config = ConfigDict(extra="forbid")
    fragment: str | None = None
    enabled_default: bool | None = None
    global_negative: str | None = None
    style_id: str | None = None        # edit a specific style; omit = the active one


class AddStyleRequest(BaseModel):
    """Add a named style to the L1 collection (2026-06-13)."""

    model_config = ConfigDict(extra="forbid")
    name: str
    fragment: str = ""
    global_negative: str = ""


class UpdateStyleRequest(BaseModel):
    """Edit a style by id (name/fragment/global-negative)."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    fragment: str | None = None
    global_negative: str | None = None


class ActiveStyleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    style_id: str


class StyleSampleRequest(BaseModel):
    """Pass 2 — set a style's persistent SAMPLE thumbnail from a finished generation output
    (an out/-relative image). `prompt`/`model` are echoed for display only."""

    model_config = ConfigDict(extra="forbid")
    job_id: str | None = None
    output: str
    prompt: str | None = None
    model: str | None = None


class StageZImageLoraRequest(BaseModel):
    """P2/M2 — prepare a Z-Image LoRA trainer job as a STAGED record.

    This writes captions/context/dataset/config and `jobs/staged.json`, but does
    not add anything to queue.json. The explicit queue transition is
    POST /training/staged/{id}/queue.
    """

    model_config = ConfigDict(extra="forbid")
    version_id: str | None = None
    trigger_token: str | None = None
    runtime_overlay: str | None = None
    steps: int = Field(default=500, ge=1, le=10000)
    resolution: int = Field(default=512, ge=256, le=2048, multiple_of=16)
    rank: int = Field(default=16, ge=1, le=256)
    alpha: int = Field(default=16, ge=1, le=256)
    learning_rate: float = Field(default=0.0001, gt=0, le=1.0)


class WorldRequest(BaseModel):
    """M8 — set the long-form world summary (markdown)."""

    model_config = ConfigDict(extra="forbid")
    world: str


class PremiseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    premise: str


class SpineCharacterRequest(BaseModel):
    """M8 — add (no character_id) or edit (character_id given) a spine character."""

    model_config = ConfigDict(extra="forbid")
    character_id: str | None = None
    name: str | None = None
    snippet: str | None = None


class SpineStubRequest(BaseModel):
    """M8 — materialize a spine character into a stub AssetProfile (R55 manual)."""

    model_config = ConfigDict(extra="forbid")
    character_id: str
    asset_class: str = "characters"


class StarRequest(BaseModel):
    """Star/un-star a completed Stage-A candidate into a version's casting set (M2, R44).
    `job_id` is the completed casting job; `output` selects a specific candidate when the
    job produced a pool (multi) — omit for a single-output job (zimage). `starred=False`
    toggles the hero off."""

    model_config = ConfigDict(extra="forbid")
    job_id: str
    output: str | None = None          # specific candidate output_name (multi pool)
    version_id: str | None = None
    starred: bool = True


class StageBRequest(BaseModel):
    """Stage-B expansion (P1/M3, §7.1): expand the starred hero into a coverage-matrix dataset.
    Picks a recipe `preset`, auto-generates the per-cell prompts (no freeform typing, R107), and
    fires **one batch job per realization group** (the worker loads once, loops the cells) — each
    cell carrying its frozen `coverage_cell` (P1→P2). Realization by `pipeline`: **zimage/sd35**
    img2img (+ `realize="mixed"` adds an inpaint background-diversity group, M3.5); **flux2** is
    reference-conditioned (`ref` mode, §11/R147 — the hero rides as an in-context reference, so
    identity carries into new poses/scenes that img2img can't reach). `character_clause` defaults
    to the version's `prompt_template` (R112)."""

    model_config = ConfigDict(extra="forbid")
    version_id: str | None = None
    preset: str = recipe.DEFAULT_PRESET
    character_clause: str | None = None
    pipeline: Literal["zimage", "sd35", "flux2"] = "zimage"
    model_name: str | None = None
    strength: float = Field(default=0.55, ge=0.0, le=1.0)
    width: int = Field(default=1024, ge=256, le=2048, multiple_of=16)
    height: int = Field(default=1024, ge=256, le=2048, multiple_of=16)
    base_seed: int | None = None
    apply_style: bool | None = None
    style_id: str | None = None        # which L1 style; None = the active default
    params: dict = Field(default_factory=dict)
    dry_run: bool = False
    # M3.5 — mixed realization (background-diversity axis, §7.1): inpaint-method cells
    # repaint the BACKGROUND around the held subject. Needs the hero's bg mask (an
    # out/-relative name from a `birefnet` matte job — POST /assets/{id}/stage-b/matte).
    realize: Literal["img2img", "mixed"] = "img2img"
    bg_mask: str | None = None
    inpaint_strength: float = Field(default=0.95, ge=0.0, le=1.0)
    # M4 — identity-lock pass (R93): None = ON when the version has a face anchor
    # (default-when-available), False = opt out, True = require (422 without an anchor).
    identity: bool | None = None
    identity_min_det_score: float = Field(default=0.5, ge=0.0, le=1.0)
    # M0d Part A (flux2) — build each cell prompt from the explicit camera+pose DIRECTIVE form
    # (flux2_prompt) instead of the flat coverage phrase: the loose-pose fix for ref-mode. Only
    # meaningful for flux2 (the UI exposes it there); harmless on zimage/sd35.
    advanced_prompt: bool = False


class CreateVersionRequest(BaseModel):
    """M5 copy-on-create (R50/R58/R59): deep-duplicate ANY prior version (default: the
    active one) into a fresh, unlocked version that becomes active."""

    model_config = ConfigDict(extra="forbid")
    parent_version_id: str | None = None
    name: str | None = None


class ActivateVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str


class RejectRefRequest(BaseModel):
    """P1-12: mark/unmark a Stage-B candidate output rejected during Stage-C culling."""

    model_config = ConfigDict(extra="forbid")
    job_id: str
    output: str | None = None
    version_id: str | None = None
    rejected: bool = True


class SketchRequest(BaseModel):
    """M7 — video-sketch harvest (R11/§4.1): a cheap low-res `ltxv` i2v motion sketch
    from the hero ★, AIMED at a target coverage cell; a chained `frame_harvest` pass
    extracts stills carrying that cell — multi-angle/pose coverage without 3D."""

    model_config = ConfigDict(extra="forbid")
    version_id: str | None = None
    shot_size: str = "waist_up"
    angle: str = "profile_left"
    expression: str = "neutral"
    motion_prompt: str | None = None
    character_clause: str | None = None
    every: int = Field(default=6, ge=1, le=60)
    max_frames: int = Field(default=24, ge=1, le=120)
    apply_style: bool | None = None
    style_id: str | None = None        # which L1 style; None = the active default
    params: dict = Field(default_factory=dict)
    dry_run: bool = False


class DeriveAnchorRequest(BaseModel):
    """M6.1 (user idea, R94): derive a dedicated face PORTRAIT from an owned output —
    the restored, aligned 512² crop of the largest face. A far better anchor base than a
    small face inside a full-body shot; the result is a normal job output, so the
    existing '⚓ set as face anchor' picks it up."""

    model_config = ConfigDict(extra="forbid")
    version_id: str | None = None
    job_id: str
    output: str | None = None
    blend: float = Field(default=0.8, ge=0.0, le=1.0)


class AnchorRequest(BaseModel):
    """Set (job_id+output) or clear (job_id=None) the version's face anchor (R94)."""

    model_config = ConfigDict(extra="forbid")
    version_id: str | None = None
    job_id: str | None = None
    output: str | None = None        # out/-relative image name (defaults to the job's primary)


class MatteRequest(BaseModel):
    """Matte the version's hero ★ (M3.5): one `birefnet` job → matte / cutout / bg mask.
    The bg mask feeds `realize="mixed"` Stage-B expansion. `params` = the catalog channel
    for the birefnet tunables (threshold/dilate_px/…)."""

    model_config = ConfigDict(extra="forbid")
    version_id: str | None = None
    params: dict = Field(default_factory=dict)
    dry_run: bool = False


class HeroRequest(BaseModel):
    """Set (or clear, `candidate_id=null`) the hero among already-recorded candidates."""

    model_config = ConfigDict(extra="forbid")
    candidate_id: str | None = None
    version_id: str | None = None


class KeepRefRequest(BaseModel):
    """Stage-C curation (M3): keep a completed Stage-B candidate into the version's curated
    `ref_set`. `job_id` is the Stage-B img2img job (it carries the coverage_cell); `output`
    selects a specific output if the job produced more than one (img2img = one, so optional).
    `allow_unlocked` (M4 review): explicit escape hatch to curate a NON-terminal output —
    one whose job still has pending post-passes (e.g. the pre-identity-lock image of a
    stopped chain). Default-safe: off."""

    model_config = ConfigDict(extra="forbid")
    job_id: str
    output: str | None = None
    version_id: str | None = None
    allow_unlocked: bool = False


class CullRefRequest(BaseModel):
    """Cull (un-keep) a curated ref by its `ref_id`."""

    model_config = ConfigDict(extra="forbid")
    ref_id: str
    version_id: str | None = None


class SaveProfileRequest(BaseModel):
    """Save AssetProfile (R119, the MVP done-line): persist the editable identity clause
    (`prompt_template`). Saved, not Finalized — still editable."""

    model_config = ConfigDict(extra="forbid")
    prompt_template: str | None = None
    version_id: str | None = None


class AddPostprocStepRequest(BaseModel):
    """M0c: configure (NOT queue) one postprocess step onto a base image's PROJECT-level stack
    (any image, regardless of origin — Sandbox or a character). `base` is the out/-relative
    image the stack postprocesses (the first step reads it; later steps read the previous
    step's output). `preset` picks Clean/Refine (img2img strength presets) or restore (GFPGAN
    face-restore). `backend` picks the i2i family (zimage|sd35); `params` carries
    strength/prompt/negative_prompt/model_name (i2i) or blend (restore). `mask` is the
    mask-ready contract (out/-relative; stored + carried for future mask-aware steps, not
    consumed in M0)."""

    model_config = ConfigDict(extra="forbid")
    base: str
    preset: Literal["clean", "refine", "restore", "upscale"] = "clean"
    backend: str | None = None
    params: dict = Field(default_factory=dict)
    mask: str | None = None
    requires_mask: bool = False


class QueuePostprocStepRequest(BaseModel):
    """M0c: fire a configured step's job over its source image. `requester_id` + `stage` route
    the produced tile into a specific grid (the UI's current context — a character version +
    bootstrap stage); omitted ⇒ inherit the source's producing job, else the project (Sandbox).
    `dry_run` returns the planned job/argv without spending GPU."""

    model_config = ConfigDict(extra="forbid")
    requester_id: str | None = None
    stage: str | None = None
    dry_run: bool = False


def require_token(x_loom_token: str | None = Header(default=None)) -> None:
    """Auth gate for mutating/expensive endpoints (review #1, R101 transport).

    The loopback bind already blocks off-machine callers; the token blocks *local*
    cross-site requests from spending GPU (the no-surprise-GPU posture, R141–143).
    """
    if x_loom_token != CONFIG.token:
        raise HTTPException(status_code=401, detail="missing or invalid X-Loom-Token")


def _identity_job_locked(job: dict) -> bool:
    """True if an identity job actually SWAPPED ≥1 face. A passthrough-only run (the anchor —
    or every target — had no detectable face, e.g. a heavily-stylized character that evades the
    photoreal SCRFD detector) completes 'ok' but locked nothing, so it must NOT verify the
    anchor (else default-on identity would arm a permanent no-op)."""
    ometa = (job.get("result") or {}).get("output_meta") or {}
    return any((m or {}).get("identity") == "locked" for m in ometa.values())


def _persist_anchor_verification(job: dict) -> None:
    """Runner completion observer (M4 review, Medium): the moment an identity job
    finishes OK **and actually locked a face**, stamp `verified_at`/`verified_by_job` on the
    version's anchor — DURABLE, so deleting/pruning the verifying job never silently
    un-verifies a good anchor (default-on identity reads this fact first, job history only as
    fallback)."""
    if (job.get("pipeline") != "identity" or not (job.get("result") or {}).get("ok")
            or not _identity_job_locked(job)):
        return
    ws = RUNNER.workspace
    vid = job.get("profile_version_id")
    anchor_image = (job.get("params") or {}).get("anchor_image")
    if not (ws and vid and anchor_image):
        return
    try:
        assets.mark_anchor_verified(ws, vid, anchor_path=str(anchor_image),
                                    job_id=job.get("id", ""))
    except Exception as e:  # noqa: BLE001 - observer is best-effort, never fail the job
        logsetup.get_logger().warning("anchor verification persist failed: %s", e)


def _record_postproc_output(job: dict) -> None:
    """Completion observer (M0c): when a queued postprocess-STEP job finishes, record its
    produced output + final status on the matching PROJECT-level stack step (matched by
    job_id). A no-op for non-postproc jobs (no step matches). Best-effort — never fails the
    job (mirrors the anchor-verification observer)."""
    ws = RUNNER.workspace
    if ws is None:
        return
    result = job.get("result") or {}
    output = result.get("output_name") or (result.get("output_names") or [None])[0]
    try:
        postproc.record_result(ws, job.get("id", ""),
                               output=output, ok=bool(result.get("ok")))
    except Exception as e:  # noqa: BLE001 - observer is best-effort, never fail the job
        logsetup.get_logger().warning("postproc result persist failed: %s", e)


def _on_job_complete(job: dict) -> None:
    """The single runner completion observer — fans out to the per-feature persisters."""
    _persist_anchor_verification(job)
    _record_postproc_output(job)


def _image_dims(path, default: tuple[int, int] = (1024, 1024)) -> tuple[int, int]:
    """(width, height) of an image, or `default` if it can't be read. Used so a postprocess
    step preserves its SOURCE aspect (img2img output dims + the grid's tile aspect) instead
    of defaulting to the project 1280×720."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:  # noqa: BLE001 - best-effort; fall back to a square default
        return default


def _round16(x: float) -> int:
    """Snap a dimension to a multiple of 16 (every worker requires it), clamped to the catalog
    width/height range [256, 2048]."""
    return max(256, min(2048, int(round(x / 16.0)) * 16))


def _postproc_target_dims(src_dims: tuple[int, int], params_in: dict) -> tuple[int, int]:
    """M0e Part B/C — the OUTPUT dims for an i2i / tile-upscale postproc step. Default is the
    source dims (today's behaviour — preserve aspect). An optional output size enlarges it:
    explicit `width`+`height` win (both required, snapped to /16); else a `scale` factor over the
    source; else the source unchanged. So a Clean/Refine (or Upscale) step can re-diffuse larger =
    a creative upscale, while a step with no size override behaves exactly as before."""
    sw, sh = src_dims
    ow, oh = params_in.get("width"), params_in.get("height")
    if isinstance(ow, (int, float)) and isinstance(oh, (int, float)):
        return _round16(ow), _round16(oh)
    scale = params_in.get("scale")
    if isinstance(scale, (int, float)) and float(scale) > 0 and float(scale) != 1.0:
        return _round16(sw * scale), _round16(sh * scale)
    return sw, sh


def _validate_postproc_size(params: dict) -> None:
    """M0e Part B — validate an optional postproc OUTPUT size on a step's params. `width`/`height`:
    int multiple of 16 in [256, 2048], set together (an explicit pair); `scale`: number in
    [0.25, 4.0] (≥1 enlarges, <1 reduces — e.g. ×0.5/×0.75; `_round16` clamps to the 256 floor).
    Raises HTTPException(422) on a bad value. Absent keys = preserve source dims."""
    for dim in ("width", "height"):
        if dim in params:
            v = params[dim]
            if not isinstance(v, int) or isinstance(v, bool) or not (256 <= v <= 2048):
                raise HTTPException(422, f"postproc {dim} must be an int in [256, 2048] (got {v!r})")
            if v % 16 != 0:
                raise HTTPException(422, f"postproc {dim}={v} must be a multiple of 16")
    if ("width" in params) != ("height" in params):
        raise HTTPException(422, "postproc width and height must be set together (or use scale)")
    if "scale" in params:
        s = params["scale"]
        if not isinstance(s, (int, float)) or isinstance(s, bool) or not (0.25 <= float(s) <= 4.0):
            raise HTTPException(422, f"postproc scale must be a number in [0.25, 4.0] (got {s!r})")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the worker, then re-open the last project (M5: the queue is per-project, so
    # the worker idles until a project is bound — resolve_startup binds the last one,
    # resume-paused). Then emit READY — lifespan startup runs AFTER uvicorn binds the
    # socket (review #3), so a port conflict fails before any false READY line.
    # Configure logging first (level from .env LOOM_LOG_LEVEL) so everything below logs.
    log = logsetup.configure(CONFIG.log_level, CONFIG.log_dir)
    log.info("starting orchestrator v%s (python %s)", __version__, sys.version.split()[0])

    # Relocate the HF weights cache to a shared dir on the work disk (off the system drive,
    # next to projects). Must be set BEFORE huggingface_hub is first imported (the launch
    # gate's weight checks below), so it governs both the orchestrator's own presence checks
    # and every pipeline subprocess (which inherit os.environ). Real env wins. (P1/M2.5)
    if not os.environ.get("HF_HOME"):
        try:
            CONFIG.hf_home.mkdir(parents=True, exist_ok=True)
            os.environ["HF_HOME"] = str(CONFIG.hf_home)
            log.info("HF cache → %s (shared across projects)", CONFIG.hf_home)
        except OSError as e:
            log.warning("could not create HF cache dir %s: %s", CONFIG.hf_home, e)

    # Propagate an HF token from the central config (`.env.local`) into the process env so
    # pipeline subprocesses (multi → flux2/sd35 hf_hub_download) inherit it — gated weights
    # (FLUX.2-dev, sd3.5-large) need it. Real env wins; we only fill what's unset (P1/M2).
    for _hf in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_HUB_TOKEN"):
        _tok = CONFIG.hf_token
        if _tok and not os.environ.get(_hf):
            os.environ[_hf] = _tok
    if CONFIG.hf_token:
        log.info("HF token present (gated-weight downloads enabled)")

    # Launch gate (M7, §11): refuse to start on a missing P0-essential CODE component
    # (clear error, no degraded mode). A missing P0-essential WEIGHT does not abort —
    # it's reported so the UI can offer an explicit HF fetch (R163).
    global _LAUNCH
    try:
        _LAUNCH = components.gate()
    except components.LaunchError as e:
        log.error("LAUNCH REFUSED — %s", e)
        print(f"LOOM_ORCH_LAUNCH_REFUSED {e}", file=sys.stderr, flush=True)
        raise
    log.info("launch gate OK (active=%s, weights_ok=%s)",
             _LAUNCH["active_phases"], _LAUNCH["weights_ok"])
    if not _LAUNCH.get("weights_ok", True):
        log.warning("P0 weights missing %s — fetch via POST /components/fetch before generating",
                    _LAUNCH["weights_missing"])

    RUNNER.start()
    projects.resolve_startup()
    # Disk guard (M6): gate the worker's dispatch on the hard-stop, then start polling.
    RUNNER.set_disk_gate(GUARD.is_hard_blocked)
    # Anchor verification (M4 review): persist the fact the moment an identity job
    # succeeds — durable on version.anchor, independent of queue history/pruning.
    RUNNER.set_completion_observer(_on_job_complete)
    GUARD.start()
    log.info("ready at %s", CONFIG.base_url)
    print(f"LOOM_ORCH_READY url={CONFIG.base_url} token={CONFIG.token}", flush=True)
    yield
    # Graceful shutdown: stop the guard, then re-queue any running job + mark a clean stop
    # so a reload re-queues (not fails) it (R159 graceful branch). Runs on a clean uvicorn
    # stop; a hard kill skips this -> reload treats running jobs as a crash (-> failed).
    log.info("orchestrator stopping (clean)")
    GUARD.stop()
    RUNNER.graceful_shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="Loreweave Studio orchestrator", version=__version__, lifespan=lifespan)

    # Restrict to known dev/Tauri origins (review #1) — was `*`. Defense-in-depth;
    # the token on /generate is the real gate.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CONFIG.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        """Liveness + identity. Unauthenticated so the sidecar can probe boot."""
        return {
            "status": "ok",
            "app_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "pid": os.getpid(),
            "uptime_s": round(time.time() - _STARTED_AT, 3),
            "launch_ok": _LAUNCH.get("launch_ok", True),
            "weights_ok": _LAUNCH.get("weights_ok", True),
        }

    @app.get("/version")
    def version() -> dict:
        """Resolved runtime facts — recorded, not assumed (P0-16, R103)."""
        return {
            "app_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "python": sys.version.split()[0],
            "venv_python": CONFIG.venv_python,
            "pipeline_roots": [str(r) for r in CONFIG.pipeline_roots],
            "zimage_worker": str(zimage_adapter.resolve_script(CONFIG.pipeline_roots) or ""),
            "models_dir": str(CONFIG.models_dir),
            "cors_origins": CONFIG.cors_origins,
            "token_required": ["POST /generate", "POST /jobs/{id}/cancel",
                               "POST /jobs/{id}/stop",
                               "DELETE /jobs/{id}", "POST /queue/pause",
                               "POST /queue/unpause", "POST /project", "POST /project/open",
                               "POST /project/close",
                               "POST /project/forget", "PUT /bible/style",
                               "POST /bible/styles", "PUT /bible/styles/{id}",
                               "DELETE /bible/styles/{id}", "POST /bible/styles/active",
                               "PUT /bible/world",
                               "PUT /bible/spine/premise", "POST /bible/spine/character",
                               "DELETE /bible/spine/character/{cid}",
                               "POST /bible/spine/character/stub",
                               "POST /bible/spine/character/{cid}/resync", "POST /assets",
                               "POST /assets/import", "GET /assets/{id}/export",
                               "POST /assets/{id}/casting/star", "POST /assets/{id}/casting/hero",
                               "POST /assets/{id}/stage-b", "POST /assets/{id}/stage-b/matte",
                               "POST /assets/{id}/stage-b/sketch",
                               "POST /assets/{id}/anchor", "POST /assets/{id}/anchor/derive",
                               "POST /assets/{id}/versions",
                               "POST /assets/{id}/versions/{vid}/finalize",
                               "POST /assets/{id}/versions/activate",
                               "POST /assets/{id}/refs/keep",
                               "POST /assets/{id}/refs/reject",
                               "POST /assets/{id}/refs/cull", "POST /assets/{id}/save",
                               "POST /components/fetch", "POST /shutdown"],
            "worker_reap": WORKER_REAP,
            "work_disk_root": str(CONFIG.work_disk_root),
            "hf_home": os.environ.get("HF_HOME") or str(CONFIG.hf_home),
            "active_project": (str(RUNNER.workspace.path) if RUNNER.workspace else None),
            "log_level": CONFIG.log_level,
            "log_file": str(CONFIG.log_dir / "orchestrator.log"),
        }

    # Chained post-pass defaults (clean = mid-strength fix-up, polish = low-strength finish).
    _POST_DEFAULTS = {"clean": {"backend": "zimage", "strength": 0.5},
                      "polish": {"backend": "sd35", "strength": 0.22}}

    def _extract_post_passes(merged: dict, *, dry_run: bool) -> list[dict]:
        """Pop the clean/polish post-pass params (catalog `post: True`) out of `merged`
        and build the chained-pass specs the runner consumes (2026-06-11: post-passes
        work on ANY run — the runner chains one batch img2img job per pass over the
        parent's outputs). Enforces the opt-in footgun guard (sub-params without their
        toggle → 422), backend-family consistency of an explicit model (→ 422), and —
        unless dry_run — the backend weight pre-flight (→ 412) + VRAM admission (→ 422)."""
        post_names = {p["name"] for p in model_catalog.POST_PARAMS}
        post = {k: merged.pop(k) for k in list(merged) if k in post_names}
        passes: list[dict] = []
        for name in ("clean", "polish"):
            orphans = sorted(k for k in post if k.startswith(f"{name}_"))
            if not post.get(name):
                if orphans:
                    raise HTTPException(422, f"{orphans} given but {name!r} is off — set "
                                             f"params.{name}=true to run that pass")
                continue
            defaults = _POST_DEFAULTS[name]
            backend = post.get(f"{name}_backend") or defaults["backend"]
            model = post.get(f"{name}_model")
            if model and model_catalog.find_variant(backend, model) is None:
                raise HTTPException(422, f"{name}_model {model!r} is not a {backend} variant "
                                         f"— pick a model from the chosen {name}_backend family")
            if not dry_run:
                resolved = model or model_catalog.default_model(backend)
                variant = model_catalog.find_variant(backend, resolved)
                if variant and not components.variant_weights_present(variant):
                    raise HTTPException(412, {
                        "error": f"{name} backend model {variant['id']!r} not in cache",
                        "repo_id": variant["repo_id"], "gated": variant["gated"],
                        "hint": "fetch it first (gated repos need a HF license + token)"})
                if estimate_vram(backend) > CONFIG.vram_budget_gb:
                    raise HTTPException(422, f"{name} backend {backend!r} needs "
                                             f"~{estimate_vram(backend)} GB VRAM > budget "
                                             f"{CONFIG.vram_budget_gb} GB")
            spec = {"pass": name, "backend": backend, "model_name": model,
                    "strength": post.get(f"{name}_strength", defaults["strength"]),
                    "prompt": post.get(f"{name}_prompt"),
                    "negative_prompt": post.get(f"{name}_negative_prompt")}
            if name == "polish" and post.get("polish_seed") is not None:
                spec["seed"] = post["polish_seed"]
            passes.append(spec)
        # M6 — restore pass (GFPGAN onnx, CPU): appended LAST among the extracted passes
        # (it must run after any re-diffusing pass; stage_b inserts identity BEFORE it so
        # the restore also fixes the swap's 128px softness — the M4 pairing).
        r_orphans = sorted(k for k in post if k.startswith("restore_"))
        if not post.get("restore"):
            if r_orphans:
                raise HTTPException(422, f"{r_orphans} given but 'restore' is off — set "
                                         "params.restore=true to run that pass")
        else:
            if not dry_run:
                r_ok, r_missing = components.postproc_weights_status("face_restore")
                if not r_ok:
                    raise HTTPException(412, {
                        "error": "face-restore weight(s) missing", "missing": r_missing,
                        "hint": "POST /components/fetch?postproc=face_restore "
                                "(GFPGAN onnx, ungated)"})
            passes.append({"pass": "restore", "backend": "face_restore",
                           "blend": post.get("restore_blend", 0.8)})
        return passes

    @app.post("/generate")
    def generate(req: GenerateRequest, _auth: None = Depends(require_token)) -> dict:
        """Enqueue work onto the single-worker runner. Token-gated; needs an open project.

        `zimage` (t2i) → an N-image batch (count jobs). `multi` (ideate) → **one** casting
        job that fans out into a pool of `num_candidates × pipelines` candidates (P1/M2)."""
        # Resolve the adapter + coerce the mode to the one this pipeline wires.
        adapter = ADAPTERS.get(req.pipeline)
        if adapter is None:
            raise HTTPException(400, f"unknown pipeline {req.pipeline!r}")
        # Resolve the mode: multi is always the casting `ideate`; otherwise honor req.mode
        # (t2i / img2img / inpaint) but only if the adapter actually wires it (honest contract,
        # never claim a mode the worker won't accept).
        if req.pipeline == "multi":
            mode = "ideate"
        elif req.mode == "ideate":
            raise HTTPException(400, "mode 'ideate' is only valid for the multi pipeline")
        else:
            mode = req.mode
        wired = set(getattr(adapter, "WIRED_MODES", ()))
        if mode not in wired:
            raise HTTPException(400, f"{req.pipeline} does not wire mode {mode!r} "
                                     f"(wired: {sorted(wired)})")
        script = adapter.resolve_script(CONFIG.pipeline_roots)
        if script is None:
            raise HTTPException(503, f"{req.pipeline} worker not found in any pipeline root "
                                     f"({[str(r) for r in CONFIG.pipeline_roots]})")

        ws = RUNNER.workspace
        if ws is None:
            raise HTTPException(409, "no project open — create or open a project first "
                                     "(POST /project or /project/open)")
        project = ws.load_project()

        # Launch-gate precondition (M7/§11.1): a phase-essential weight must be present —
        # offer the explicit fetch rather than failing the GPU run mid-flight.
        ok, missing = components.weights_ok()
        if not ok:
            raise HTTPException(412, f"required model weight(s) missing: {missing} — "
                                     "fetch via POST /components/fetch first")

        # Resolve the L2 scope (P1/M1): which AssetProfile version this batch is for, and
        # therefore the lineage requester. Default requester = the project (P0 sandbox).
        requester_id = project["id"]
        profile_version_id = None
        if req.asset_id is not None:
            try:
                profile_version_id = assets.resolve_version(ws, req.asset_id, req.version_id)
            except ws_mod.WorkspaceError as e:
                raise HTTPException(404, str(e))
            requester_id = profile_version_id

        base = req.model_dump(exclude={"pipeline", "mode", "dry_run", "count",
                                       "asset_id", "version_id", "stage", "apply_style",
                                       "num_candidates", "ideation_mode", "params"})
        is_multi = req.pipeline == "multi"
        if is_multi:
            # Catalog-validated multi tunables: width/height/seed + the clean/polish
            # post-pass params (extracted below — they chain as separate jobs).
            if req.params:
                try:
                    base.update(model_catalog.validate_params("multi", mode, req.params))
                except model_catalog.CatalogError as e:
                    raise HTTPException(422, str(e))
            base["num_candidates"] = req.num_candidates
            base["ideation_mode"] = req.ideation_mode
            # The TOP-LEVEL width/height defaults (1280×720 — the P0/Wan project default)
            # must not shadow the cast catalog's native 1024² (M6 review #2: the drawer
            # advertises the catalog default, so an unset cast must actually GET it).
            # Explicit values — top-level (model_fields_set) or params channel — still win.
            for dim in ("width", "height"):
                if dim not in req.model_fields_set and dim not in req.params:
                    base[dim] = model_catalog.param_default("multi", dim)
            # Preset-aware pre-flight (review follow-up): `multi`'s gated flux2/sd35
            # checkpoints aren't in the phase weight gate above, so check the SELECTED
            # ideation preset's weight set here and fail fast — never start the GPU run
            # only to die inside the subprocess on a missing/unauthorized weight. Skip
            # for dry_run (a no-GPU argv preview shouldn't require any weight).
            if not req.dry_run:
                m_ok, m_missing = components.multi_weights_status(req.ideation_mode)
                if not m_ok:
                    raise HTTPException(412, {
                        "error": f"multi '{req.ideation_mode}' preset is missing weight(s)",
                        "preset": req.ideation_mode,
                        "missing": m_missing,
                        "hint": "accept the gated repo licenses on huggingface.co + set "
                                "HF_TOKEN, then POST /components/fetch?multi_preset="
                                f"{req.ideation_mode}",
                    })
        else:
            # Reject an unknown explicit model_name up front (422) — never let a bogus model
            # reach the worker (where argparse would fail the subprocess after a spawn).
            try:
                model_catalog.validate_model(req.pipeline, req.model_name)
            except model_catalog.CatalogError as e:
                raise HTTPException(422, str(e))
            # Catalog-validated tunables channel (M3 step 4b): merge model-specific / long-tail
            # params (validated vs GET /models for this pipeline+mode) into the flat param set
            # build_argv reads. The common params stay top-level; these override on key clash.
            if req.params:
                try:
                    base.update(model_catalog.validate_params(req.pipeline, mode, req.params))
                except model_catalog.CatalogError as e:
                    raise HTTPException(422, str(e))
            # Same display==reality fix the multi branch got (M6 review #2), now for the
            # single pipelines: the TOP-LEVEL width/height Pydantic defaults (1280×720 — the
            # P0/Wan project default) must not shadow THIS pipeline's native catalog default
            # that the drawer advertises (sd35/zimage 1024², flux2 1360×768). An UNSET cast
            # must actually GET the advertised default — else a 1024² sd35 cast silently ran
            # at 1280×720 (user-reported 2026-06-14). Explicit values — top-level
            # (model_fields_set) or via the params channel — still win.
            # M0e Part A: the default is now MODEL-aware — resolve from the effective model
            # (params-channel override > top-level > catalog default, the same precedence the
            # weight pre-flight below uses) so an unset `flux.2-dev` cast resolves to its 512²
            # variant default, not flux2's 1360×768 pipeline default. Non-dev models keep the
            # pipeline default (model_size_default returns None,None for them).
            eff_model = base.get("model_name") or model_catalog.default_model(req.pipeline)
            md_w, md_h = model_catalog.model_size_default(req.pipeline, eff_model)
            model_dim_default = {"width": md_w, "height": md_h}
            for dim in ("width", "height"):
                if dim not in req.model_fields_set and dim not in req.params:
                    d = model_dim_default[dim]
                    if d is None:
                        d = model_catalog.param_default(req.pipeline, dim)
                    if d is not None:
                        base[dim] = d
            # Per-model weight pre-flight keyed to the model the worker will ACTUALLY load
            # (review 2026-06-11): `model_name` is also a catalog param and the params
            # channel overrides the top-level field on merge, so resolve from the MERGED
            # set — checking only the explicit/default model let params={model_name:…}
            # slip an uncached model past the gate. Skip for dry_run (no-GPU preview).
            if not req.dry_run:
                chosen = base.get("model_name") or model_catalog.default_model(req.pipeline)
                variant = model_catalog.find_variant(req.pipeline, chosen)
                if variant and not components.variant_weights_present(variant):
                    raise HTTPException(412, {
                        "error": f"{req.pipeline} model {variant['id']!r} not in cache",
                        "repo_id": variant["repo_id"], "gated": variant["gated"],
                        "hint": "fetch it first (gated repos need a huggingface.co license "
                                "accept + HF_TOKEN)",
                    })

        # Stage-B img2img/inpaint inputs (P1/M3): the worker needs a base image (+ a mask for
        # inpaint). init_image/mask_image are out/-relative names; resolve + traversal-guard
        # them to absolute paths the subprocess can read. (dry_run echoes the raw names.)
        if mode in ("img2img", "inpaint"):
            if not req.init_image:
                raise HTTPException(422, f"mode {mode!r} requires init_image")
            if mode == "inpaint" and not req.mask_image:
                raise HTTPException(422, "mode 'inpaint' requires mask_image")
            if not req.dry_run:
                obase = ws.out_dir.resolve()
                for key, val in (("init_image", req.init_image), ("mask_image", req.mask_image)):
                    if not val:
                        continue
                    if ".." in val or "\\" in val:
                        raise HTTPException(400, f"invalid {key} {val!r}")
                    p = (obase / val).resolve()
                    if not p.is_relative_to(obase) or not p.is_file():
                        raise HTTPException(404, f"{key} {val!r} not found in out/")
                    base[key] = str(p)

        # Clean/polish post-passes (2026-06-11): pulled OUT of the param set — they run
        # as chained batch img2img jobs after this run completes (works on any pipeline).
        post_passes = _extract_post_passes(base, dry_run=req.dry_run)

        # L1 style fragment auto-applied (R104). Per-gen `apply_style` overrides; when it's
        # omitted, honor the StoryBible's saved `enabled_default` (review: that flag was
        # stored but never consulted). **Appended, not prepended** (user decision 2026-06-10,
        # amends R104's wording): the character/user prompt leads — front tokens dominate,
        # and the style mostly restates the look the model already renders.
        _apply, fragment, global_neg = bible.resolve_l1(ws, req.apply_style, req.style_id)
        if fragment:
            base["prompt"] = f"{base['prompt']}, {fragment}"
        # L1 global negative (M8) pairs with the fragment under the same gate. Skipped for
        # multi (the ideate worker takes no negative arg); the worker warns harmlessly
        # where a variant ignores negatives.
        if global_neg and not is_multi:
            base["negative_prompt"] = bible.join_negative(base.get("negative_prompt"), global_neg)

        if req.dry_run:
            spec = JobSpec(pipeline=req.pipeline, mode=mode, params=base, output_dir=ws.out_dir)
            argv = adapter.build_argv(spec, CONFIG.venv_python, script)
            return {"dry_run": True, "pipeline": req.pipeline,
                    "count": 1 if is_multi else req.count,
                    "num_candidates": req.num_candidates if is_multi else None,
                    "argv": argv, "prompt": base["prompt"],
                    "post_passes": post_passes,
                    "requester_id": requester_id, "profile_version_id": profile_version_id,
                    "cwd": str(script.parents[2]), "output_dir": str(ws.out_dir)}

        # VRAM admission (§7) — enforce, don't just record (review #2): refuse a job
        # whose estimate exceeds the budget rather than queueing a guaranteed OOM.
        est = estimate_vram(req.pipeline)
        if est > CONFIG.vram_budget_gb:
            raise HTTPException(
                422, f"{req.pipeline} needs ~{est} GB VRAM > budget {CONFIG.vram_budget_gb} GB — "
                     f"reduce size/steps or raise LOOM_VRAM_BUDGET_GB")

        # Disk-guard admission (§9/R96): refuse to admit a space-consuming job under a
        # hard stop (running jobs finish). Resolve by raising the cap or freeing space.
        if GUARD.is_hard_blocked():
            raise HTTPException(507, f"disk hard-stop — {GUARD.block_reason()}; "
                                     "free space or raise the project size cap")

        batch_id = "bat_" + uuid.uuid4().hex[:8]
        job_ids: list[str] = []
        # multi = one casting job (it fans out internally to N candidates); zimage = N jobs.
        n_jobs = 1 if is_multi else req.count
        for i in range(n_jobs):
            params = dict(base)
            if req.seed is not None and not is_multi:
                params["seed"] = req.seed + i      # distinct but reproducible per image
            elif req.seed is not None:
                params["seed"] = req.seed          # multi derives its own per-candidate seeds
            jid = RUNNER.submit(pipeline=req.pipeline, mode=mode, params=params,
                                batch_id=batch_id, index=i, batch_size=n_jobs,
                                requester_id=requester_id,          # project or asset version (R98)
                                profile_version_id=profile_version_id, stage=req.stage,
                                post_passes=post_passes)
            job_ids.append(jid)
        LOG.info("generate: %s %s (%s%s) for %s%s",
                 "cast" if is_multi else "batch", batch_id, req.pipeline,
                 f" ×{req.num_candidates}" if is_multi else f" of {req.count}",
                 requester_id, f" stage={req.stage}" if req.stage else "")
        return {"batch_id": batch_id, "count": len(job_ids), "job_ids": job_ids,
                "num_candidates": req.num_candidates if is_multi else None}

    @app.get("/project")
    def get_project() -> dict:
        """Active project info (or {open:false}). Unauthenticated read."""
        return projects.active_info()

    @app.get("/projects")
    def list_projects() -> dict:
        """Project registry for the picker — recent projects (name/path/cap/exists),
        most-recent-first. App-level machine state (not in git). Unauthenticated read."""
        return projects.list_projects()

    @app.post("/project/forget")
    def forget_project(req: OpenProjectRequest, _auth: None = Depends(require_token)) -> dict:
        """Remove a project from the registry's recent list (a moved/deleted one). Does
        not touch files or the active project. Token-gated."""
        return projects.forget_project(Path(req.path))

    # --- P1: L1 style fragment + L2 asset library -------------------------
    def _require_ws():
        ws = RUNNER.workspace
        if ws is None:
            raise HTTPException(409, "no project open")
        return ws

    def _require_job_owned_by(ws, asset_id: str, version_id: str | None, job: dict) -> str:
        """Resolve the target version id for `asset_id` and assert the job was produced for it
        (its `profile_version_id`), so a candidate can only be curated/starred into the version
        that generated it — never cross-asset. Returns the resolved version id."""
        try:
            vid = assets.resolve_version(ws, asset_id, version_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))
        if job.get("profile_version_id") != vid:
            raise HTTPException(409, "that job was generated for a different asset/version — "
                                     "curate/star it into the version that produced it")
        return vid

    @app.get("/bible/style")
    def get_style() -> dict:
        """The ACTIVE L1 style (the mirror: id/fragment/enabled_default/global_negative).
        Unauthenticated read. (The full collection is GET /bible/styles.)"""
        return bible.load_style(_require_ws())

    @app.put("/bible/style")
    def put_style(req: StyleRequest, _auth: None = Depends(require_token)) -> dict:
        """Edit a style's fragment/global-negative (the ACTIVE one unless `style_id`) + the
        default-on gate (writes story.json). Token-gated."""
        try:
            return bible.set_style(_require_ws(), fragment=req.fragment,
                                   enabled_default=req.enabled_default,
                                   global_negative=req.global_negative, style_id=req.style_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))

    # --- L1 style COLLECTION (2026-06-13): multiple named styles, selectable per gen ---
    @app.get("/bible/styles")
    def get_styles() -> dict:
        """The L1 style collection `{styles[], active_style_id, enabled_default}` — drives
        the L1 manager + the per-generation style selectors. Unauthenticated read."""
        return bible.list_styles(_require_ws())

    @app.post("/bible/styles")
    def add_style(req: AddStyleRequest, _auth: None = Depends(require_token)) -> dict:
        """Add a named style to the collection. Token-gated."""
        try:
            return bible.add_style(_require_ws(), name=req.name, fragment=req.fragment,
                                   global_negative=req.global_negative)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))

    @app.put("/bible/styles/{style_id}")
    def update_style(style_id: str, req: UpdateStyleRequest,
                     _auth: None = Depends(require_token)) -> dict:
        """Edit a style by id (name/fragment/global-negative). Token-gated."""
        try:
            return bible.update_style(_require_ws(), style_id, name=req.name,
                                      fragment=req.fragment, global_negative=req.global_negative)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))

    @app.delete("/bible/styles/{style_id}")
    def delete_style(style_id: str, _auth: None = Depends(require_token)) -> dict:
        """Delete a style (refuses the last one; re-points the active default). Token-gated."""
        try:
            return bible.remove_style(_require_ws(), style_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))

    @app.post("/bible/styles/active")
    def set_active_style(req: ActiveStyleRequest, _auth: None = Depends(require_token)) -> dict:
        """Set the default style (used when a generation doesn't pick one). Token-gated."""
        try:
            return bible.set_active_style(_require_ws(), req.style_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))

    # --- Pass 2 (2026-06-21): per-style persistent SAMPLE thumbnail (the L1 tile image) ---
    @app.post("/bible/styles/{style_id}/sample")
    def set_style_sample(style_id: str, req: StyleSampleRequest,
                         _auth: None = Depends(require_token)) -> dict:
        """Persist a finished generation's output as this style's sample thumbnail (durable copy
        in bible/styles/, like a face anchor — survives source-job deletion). Token-gated. 404 on
        an unknown style or an output not in out/."""
        try:
            return bible.set_style_sample(_require_ws(), style_id, job_id=req.job_id,
                                          source_output=req.output, prompt=req.prompt,
                                          model=req.model)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))

    @app.delete("/bible/styles/{style_id}/sample")
    def clear_style_sample(style_id: str, _auth: None = Depends(require_token)) -> dict:
        """Remove a style's sample thumbnail (drops the field + deletes the copy). Token-gated."""
        try:
            return bible.clear_style_sample(_require_ws(), style_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))

    @app.get("/bible/styles/{style_id}/sample/file")
    def get_style_sample_file(style_id: str):
        """Serve a style's sample thumbnail. Unauthenticated read (mirrors ref/anchor file serving)."""
        try:
            return FileResponse(bible.style_sample_path(_require_ws(), style_id))
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))

    @app.get("/bible")
    def get_bible() -> dict:
        """The full L1 World record (style + world prose + spine). Unauthenticated read."""
        return bible.load_story(_require_ws())

    @app.put("/bible/world")
    def put_world(req: WorldRequest, _auth: None = Depends(require_token)) -> dict:
        """M8 — set the long-form world summary. Token-gated."""
        return bible.set_world(_require_ws(), req.world)

    @app.put("/bible/spine/premise")
    def put_premise(req: PremiseRequest, _auth: None = Depends(require_token)) -> dict:
        """M8 — set the spine premise. Token-gated."""
        return bible.set_premise(_require_ws(), req.premise)

    @app.post("/bible/spine/character")
    def upsert_spine_character(req: SpineCharacterRequest,
                               _auth: None = Depends(require_token)) -> dict:
        """M8 — add or edit a spine character (name + prompt-template snippet). Editing the
        snippet here does NOT touch a linked profile (R55 — re-sync is explicit). Token-gated."""
        try:
            return bible.upsert_spine_character(
                _require_ws(), character_id=req.character_id, name=req.name,
                snippet=req.snippet)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))

    @app.delete("/bible/spine/character/{character_id}")
    def remove_spine_character(character_id: str,
                               _auth: None = Depends(require_token)) -> dict:
        """M8 — drop a spine character (a linked AssetProfile is left intact). Token-gated."""
        return bible.remove_spine_character(_require_ws(), character_id)

    @app.post("/bible/spine/character/stub")
    def create_spine_stub(req: SpineStubRequest,
                          _auth: None = Depends(require_token)) -> dict:
        """M8 (§6, R55) — materialize a spine character into a **stub AssetProfile**: a new
        profile whose v1_base prompt_template = the character's snippet (R112), linked back
        to the spine entry. Refuses if already linked to a live profile. Token-gated."""
        ws = _require_ws()
        ch = bible.spine_character(ws, req.character_id)
        if ch is None:
            raise HTTPException(404, f"spine character {req.character_id!r} not found")
        if ch.get("linked_asset_id") and assets.get_asset(ws, ch["linked_asset_id"]):
            raise HTTPException(409, f"{ch['name']!r} is already linked to a profile "
                                     f"({ch['linked_asset_id']}) — re-sync instead")
        try:
            res = assets.create_asset(ws, name=ch["name"], asset_class=req.asset_class,
                                      prompt_template=ch.get("snippet", ""))
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))
        bible.link_spine_character(ws, req.character_id, res["profile"]["id"])
        LOG.info("spine stub: %s -> %s", ch["name"], res["profile"]["id"])
        return {"profile": res["profile"], "linked_asset_id": res["profile"]["id"]}

    @app.post("/bible/spine/character/{character_id}/resync")
    def resync_spine_stub(character_id: str,
                          _auth: None = Depends(require_token)) -> dict:
        """M8 (R55) — push the spine character's snippet into its linked profile's ACTIVE
        version prompt_template. **Manual + explicit, never automatic** (the author chose to
        overwrite hand-edits). Refuses if unlinked / finalized. Token-gated."""
        ws = _require_ws()
        ch = bible.spine_character(ws, character_id)
        if ch is None:
            raise HTTPException(404, f"spine character {character_id!r} not found")
        if not ch.get("linked_asset_id"):
            raise HTTPException(409, "not linked to a profile — create a stub first")
        try:
            version = assets.save_profile(ws, ch["linked_asset_id"],
                                          prompt_template=ch.get("snippet", ""))
        except ws_mod.WorkspaceError as e:
            raise HTTPException(409, str(e))
        return {"linked_asset_id": ch["linked_asset_id"],
                "prompt_template": version.get("prompt_template", "")}

    @app.get("/assets")
    def get_assets() -> dict:
        """L2 library tree — AssetProfiles in the open project. Unauthenticated read."""
        return assets.list_assets(_require_ws())

    @app.post("/assets")
    def create_asset(req: CreateAssetRequest, _auth: None = Depends(require_token)) -> dict:
        """Create an AssetProfile + a single v1_base version (P1/M1). Token-gated."""
        try:
            res = assets.create_asset(_require_ws(), name=req.name, asset_class=req.asset_class)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))
        LOG.info("asset created: %s (%s) %s", res["profile"]["name"],
                 res["profile"]["asset_class"], res["profile"]["id"])
        return res

    @app.post("/assets/import")
    async def import_profile(request: Request,
                             _auth: None = Depends(require_token)) -> dict:
        """M9 (R66/R67) — import an AssetProfile bundle (the .zip from `GET …/export`) as a
        **brand-new profile**: fresh ids, rename-on-collision, never a merge. The zip bytes
        are the raw request body (no multipart dep). Token-gated."""
        ws = _require_ws()
        # Read the body with a RUNNING byte cap (M10 review): a chunked or lying
        # Content-Length client could otherwise buffer an oversized body into memory
        # before any check. Stream the chunks and abort the instant the running total
        # exceeds the cap — memory is bounded by MAX_BUNDLE_BYTES no matter what the
        # header says. (An honest Content-Length just lets us reject even sooner.)
        cap = assets.MAX_BUNDLE_BYTES
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > cap:
                    raise HTTPException(413, f"bundle too large (> {cap} bytes)")
            except ValueError:
                pass  # malformed header — the streaming cap below still bounds memory
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > cap:
                raise HTTPException(413, f"bundle too large (> {cap} bytes)")
        data = bytes(buf)
        if not data:
            raise HTTPException(400, "empty body — POST the .zip bundle bytes "
                                     "(Content-Type: application/zip)")
        ws.temp_dir.mkdir(parents=True, exist_ok=True)
        tmp = ws.temp_dir / f"import_{uuid.uuid4().hex[:8]}.zip"
        tmp.write_bytes(data)
        try:
            res = assets.import_profile(ws, tmp)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))
        finally:
            tmp.unlink(missing_ok=True)
        LOG.info("imported profile %s (%s)%s", res["profile"]["id"], res["profile"]["name"],
                 f" — renamed from {res['renamed_from']!r}" if res.get("renamed_from") else "")
        return res

    @app.get("/assets/{asset_id}")
    def get_asset(asset_id: str) -> dict:
        """Full AssetProfile + its versions, by id."""
        res = assets.get_asset(_require_ws(), asset_id)
        if res is None:
            raise HTTPException(404, f"no such asset {asset_id!r}")
        return res

    @app.get("/assets/{asset_id}/export")
    def export_profile(asset_id: str, _auth: None = Depends(require_token)) -> FileResponse:
        """M9 (R66) — download a portable bundle of the profile + ALL its versions (.zip).
        **Token-gated** (M9 review): unlike the per-image serves this packages every version
        and file into one portable archive, so it's gated like the other bulk operations; the
        UI downloads via fetch + X-Loom-Token + object URL."""
        try:
            path = assets.export_profile(_require_ws(), asset_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))
        return FileResponse(path, media_type="application/zip", filename=path.name)

    @app.get("/training/staged")
    def get_staged_training() -> dict:
        """P2/M2 staged trainer records. Unauthenticated read for the future Train panel."""
        try:
            return training.list_staged(_require_ws())
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))

    @app.post("/assets/{asset_id}/lora/zimage/stage")
    def stage_zimage_lora(asset_id: str, req: StageZImageLoraRequest,
                          _auth: None = Depends(require_token)) -> dict:
        """P2/M2 — materialize a Z-Image LoRA trainer job as a staged record.

        This prepares deterministic captions, caption policy, training context,
        temp dataset, ai-toolkit config, and `jobs/staged.json`; it deliberately
        does NOT enqueue. The user must explicitly queue the staged id.
        """
        settings = {
            "steps": req.steps,
            "resolution": req.resolution,
            "rank": req.rank,
            "alpha": req.alpha,
            "learning_rate": req.learning_rate,
        }
        try:
            record = training.stage_zimage_lora(
                _require_ws(), asset_id,
                version_id=req.version_id,
                trigger_token=req.trigger_token,
                runtime_overlay=req.runtime_overlay,
                settings=settings,
            )
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))
        LOG.info("staged zimage LoRA: %s asset=%s version=%s",
                 record["id"], asset_id, record["version_id"])
        return record

    @app.post("/training/staged/{staged_id}/queue")
    def queue_staged_training(staged_id: str, _auth: None = Depends(require_token)) -> dict:
        """P2/M2 staged → queued transition. This is the first moment GPU work may start."""
        try:
            res = training.queue_staged(_require_ws(), staged_id, RUNNER)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))
        LOG.info("queued staged training %s -> %s", staged_id, res["job_id"])
        return res

    @app.delete("/training/staged/{staged_id}")
    def delete_staged_training(staged_id: str, _auth: None = Depends(require_token)) -> dict:
        """Delete a staged trainer record without touching any queued/running job."""
        try:
            return training.delete_staged(_require_ws(), staged_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))

    @app.post("/assets/{asset_id}/casting/star")
    def star_casting(asset_id: str, req: StarRequest,
                     _auth: None = Depends(require_token)) -> dict:
        """Promote a completed Stage-A candidate into the version's casting[] as the hero ★
        (M2, R44) — persists into version.json + copies the image into casting/. Token-gated."""
        ws = _require_ws()
        job = RUNNER.get(req.job_id)
        if job is None:
            raise HTTPException(404, f"no such job {req.job_id!r}")
        if job.get("status") != "done":
            raise HTTPException(409, "can only star a completed (done) candidate")
        # Scope guard (same as keep): only star a candidate into the version that produced it.
        vid = _require_job_owned_by(ws, asset_id, req.version_id, job)
        result = job.get("result") or {}
        pool = result.get("output_names") or ([result["output_name"]]
                                              if result.get("output_name") else [])
        # Pick the candidate: an explicit `output` must belong to this job's pool; otherwise
        # default to the job's single output (zimage) / the first of the pool.
        if req.output is not None:
            if req.output not in pool:
                raise HTTPException(409, f"output {req.output!r} not in job {req.job_id!r}")
            output = req.output
        elif len(pool) == 1:
            output = pool[0]
        elif not pool:
            raise HTTPException(409, "candidate job has no output to star")
        else:
            raise HTTPException(409, "this is a multi-candidate job — specify which `output`")
        # Per-candidate provenance: pipeline = job pipeline for zimage; for a multi pool the
        # candidate's pipeline/seed live in its path (…/ideate/<pipeline>/seed_<seed>/…).
        pipeline, seed = job.get("pipeline"), result.get("seed")
        m = re.search(r"/_inter/.*/ideate/([^/]+)/seed_(\d+)/", "/" + output)
        if m:
            pipeline, seed = m.group(1), int(m.group(2))
        try:
            version = assets.star_candidate(
                ws, asset_id, job_id=req.job_id, source_output=output,
                version_id=vid, pipeline=pipeline, seed=seed, starred=req.starred)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))
        LOG.info("casting %s: job %s -> asset %s (%s)",
                 "star" if req.starred else "unstar", req.job_id, asset_id, version["id"])
        return version

    @app.post("/assets/{asset_id}/stage-b")
    def stage_b(asset_id: str, req: StageBRequest,
                _auth: None = Depends(require_token)) -> dict:
        """Stage-B expansion (P1/M3, §7.1): expand the starred hero into a coverage-matrix
        dataset. Builds the recipe (auto prompts) and fires **one batch job per realization
        group** (the worker loads once, loops the cells) from the hero — zimage/sd35 via
        img2img (+ `realize="mixed"` inpaint group), **flux2 via reference-conditioning**
        (`ref` mode, §11) — each cell carrying its frozen `coverage_cell` (→ Stage-C curation
        → ref_set → P2). Token-gated; needs an open project + a starred hero. `dry_run` previews."""
        ws = _require_ws()
        # Hero (Stage-A pick) seeds every img2img cell.
        try:
            version, _hero, hero_path = assets.resolve_hero(ws, asset_id, req.version_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(409, str(e))
        vid = version["id"]

        # flux2 (§11/R147) realizes Stage-B by REFERENCE-conditioning: the hero rides as an
        # in-context reference so identity carries into new poses/scenes (the variation img2img
        # can't do). Its realization mode is `ref` — the img2img/inpaint `mixed` axis doesn't
        # apply (flux2 changes the scene via the prompt, not an inpaint mask).
        is_flux2 = req.pipeline == "flux2"
        if is_flux2 and req.realize == "mixed":
            raise HTTPException(422, "flux2 expansion is reference-conditioned (identity-preserving) "
                                     "— realize='mixed' (inpaint background-diversity) is for "
                                     "zimage/sd35; use those for mixed, or flux2 for identity-locked "
                                     "pose/angle coverage")

        clause = (req.character_clause or version.get("prompt_template") or "").strip()
        if not clause:
            raise HTTPException(422, "no character_clause and the version has no prompt_template "
                                     "— set one (the fixed identity clause for the dataset, R112)")

        # L1 style fragment (R104) applies to every cell prompt unless opted out (the
        # recipe places it LAST: cell fragment → clause → style, user 2026-06-10).
        # L1 gate, single source of truth (M8 review): the fragment weaves into each recipe
        # prompt, the global negative rides the batch params — both under the same gate.
        _apply, style_fragment, global_neg = bible.resolve_l1(ws, req.apply_style, req.style_id)

        # M0d Part A: directive-led prompts are the flux2 pose fix; gate to flux2 so zimage/sd35
        # keep their flat phrasing. flux.2-dev (Mistral VLM) gets the directives as structured
        # JSON (it parses JSON precisely). Effective model precedence mirrors `model_name` below
        # (params channel overrides the top-level field).
        adv_prompt = req.advanced_prompt and is_flux2
        eff_model = (req.params or {}).get("model_name") or req.model_name
        json_prompt = adv_prompt and eff_model == "flux.2-dev"
        # Seed (user 2026-06-27): ONE seed for the whole sweep — the value from the params section
        # if given, else a freshly-drawn random seed (so an unset seed isn't a fixed 0,1,2…). Every
        # cell shares it; pose/angle/expression are the only things that vary across the dataset.
        eff_seed = req.base_seed if req.base_seed is not None else random.randrange(2**31)
        try:
            built = recipe.build_recipe(req.preset, character_clause=clause,
                                        style_fragment=style_fragment, base_seed=eff_seed,
                                        shared_seed=True,
                                        realize=req.realize, advanced_prompt=adv_prompt,
                                        json_prompt=json_prompt)
        except recipe.RecipeError as e:
            raise HTTPException(422, str(e))

        # M3.5 — mixed realization needs the hero's BG MASK (white = repaint background,
        # subject protected). PROVENANCE, not just existence (review 2026-06-11 High): the
        # name must be the `role == "bgmask"` output of a **completed birefnet job for THIS
        # version** — any other file under out/ (a stale mask, another asset's mask, the
        # matte/cutout sibling, a hand-dropped PNG) would silently poison the Stage-B/P2
        # corpus. (dry_run echoes the name unchecked — it's a no-GPU preview.)
        bg_mask_abs: str | None = None
        if req.realize == "mixed":
            if not req.bg_mask:
                raise HTTPException(422, "realize='mixed' needs bg_mask — matte the hero first "
                                         "(POST /assets/{id}/stage-b/matte) and pass its "
                                         "*_bgmask.png output name (out/-relative)")
            if ".." in req.bg_mask or "\\" in req.bg_mask:
                raise HTTPException(400, f"invalid bg_mask {req.bg_mask!r}")
            if req.dry_run:
                bg_mask_abs = req.bg_mask
            else:
                src = next((j for j in list(RUNNER.jobs.values())
                            if j.get("pipeline") == "birefnet" and j.get("status") == "done"
                            and j.get("profile_version_id") == vid
                            and req.bg_mask in ((j.get("result") or {}).get("output_names") or [])),
                           None)
                if src is None:
                    raise HTTPException(422, f"bg_mask {req.bg_mask!r} is not an output of a "
                                             "completed birefnet matte job for this version — "
                                             "matte the hero first (POST /assets/{id}/stage-b/matte)")
                ometa = ((src.get("result") or {}).get("output_meta") or {}).get(req.bg_mask) or {}
                if ometa.get("role") != "bgmask":
                    raise HTTPException(422, f"bg_mask {req.bg_mask!r} has role "
                                             f"{ometa.get('role') or 'unknown'!r} — pass the "
                                             "*_bgmask.png artifact (role 'bgmask'), not the "
                                             "matte/cutout sibling")
                obase = ws.out_dir.resolve()
                p = (obase / req.bg_mask).resolve()
                if not p.is_relative_to(obase) or not p.is_file():
                    raise HTTPException(404, f"bg_mask {req.bg_mask!r} not found in out/")
                bg_mask_abs = str(p)

        # Catalog-validate the model + the shared advanced params for the whole batch
        # (unknown model / param → 422 up front, never a per-cell worker failure). Under
        # mixed realization the one params channel feeds BOTH jobs, so it must be valid
        # for both modes (img2img + inpaint).
        try:
            model_catalog.validate_model(req.pipeline, req.model_name)
            val_mode = "ref" if is_flux2 else "img2img"
            extra = model_catalog.validate_params(req.pipeline, val_mode, req.params)
            if req.realize == "mixed":
                model_catalog.validate_params(req.pipeline, "inpaint", req.params)
        except model_catalog.CatalogError as e:
            raise HTTPException(422, str(e))
        # The EFFECTIVE model for the whole batch: the params-channel value overrides the
        # top-level field (same precedence as /generate), and everything downstream —
        # dry-run preview, weight pre-flight, worker params — reads this one resolution
        # (review 2026-06-11: the pre-flight checked only the explicit/default model, so
        # params={model_name:…} could slip an uncached model past the gate).
        model_name = extra.pop("model_name", None) or req.model_name
        # M0e Part A, extended to Stage-B: the per-cell output size is now MODEL-aware. An UNSET
        # size resolves to the effective model's default — `flux.2-dev` → 512² (it runs far faster
        # at low res on 16 GB ROCm; the 1024² StageBRequest default was a ~4k-token job, tens of
        # minutes for dev) — instead of the 1024² request default. Explicit dims still win (the
        # FE sends width/height top-level only when typed; a params-channel dim rides `**extra`).
        # Mirrors /generate so the drawer's model-aware placeholder is honest for Stage-B too.
        md_w, md_h = model_catalog.model_size_default(req.pipeline, model_name)
        size_unset_w = "width" not in req.model_fields_set and "width" not in (req.params or {})
        size_unset_h = "height" not in req.model_fields_set and "height" not in (req.params or {})
        eff_width = md_w if (size_unset_w and md_w is not None) else req.width
        eff_height = md_h if (size_unset_h and md_h is not None) else req.height
        # Clean/polish post-passes chain over the finished dataset too (2026-06-11) —
        # e.g. polish every kept-worthy cell right after the sweep, cells stay curatable
        # (their coverage_cell rides along into the pass outputs).
        post_passes = _extract_post_passes(extra, dry_run=req.dry_run)
        # L1 global negative folded into the shared batch params (M8 review): both the
        # dry-run preview and every realization group spread `**extra`, so this reaches
        # every cell of every Stage-B batch job. Skipped for flux2 (distilled FLUX.2 takes no
        # negative prompt — same as multi ideate).
        if global_neg and not is_flux2:
            extra["negative_prompt"] = bible.join_negative(extra.get("negative_prompt"), global_neg)

        # M4 — identity-lock pass (R86/R93): swap every cell's face to the version's
        # anchor (spike: anchor cos 0.105→0.870, CPU). ON by default once an anchor
        # exists **and is VERIFIED** (M4 review, Medium: a no-face anchor only fails
        # inside the worker — default-on must not arm a bad anchor); opt-out with
        # identity=false; identity=true = allowed even unverified (that run IS the
        # verification — the worker hard-fails on a faceless anchor with a clear error,
        # and a later sweep sees the successful run and defaults on). Verification is
        # COMPUTED from job history, no stored flag: a done+ok identity job for this
        # version using this anchor file, started after the anchor was (re-)picked.
        # Appended LAST so the lock is the final word (a later polish would re-diffuse
        # the swapped face). No-face cells (back views) pass through unchanged.
        anchor_rec = version.get("anchor")
        anchor_path = assets.anchor_file_path(ws, asset_id, req.version_id)
        anchor_ok = False
        if anchor_path is not None:
            # Durable fact first (review 2026-06-11: queue history can be pruned — the
            # persisted stamp keeps Saved profiles identity-ready); job-history scan as
            # fallback for runs that finished before the stamp existed, promoted lazily.
            anchor_ok = bool((anchor_rec or {}).get("verified_at"))
            if not anchor_ok:
                set_at = (anchor_rec or {}).get("set_at") or ""
                target = str(anchor_path)
                proof = next(
                    (j for j in list(RUNNER.jobs.values())
                     if j.get("pipeline") == "identity" and j.get("status") == "done"
                     and j.get("profile_version_id") == vid
                     and (j.get("params") or {}).get("anchor_image") == target
                     and (j.get("result") or {}).get("ok")
                     and _identity_job_locked(j)        # a passthrough run doesn't verify
                     and (j.get("created_at") or "") >= set_at), None)
                if proof is not None:
                    anchor_ok = True
                    try:
                        assets.mark_anchor_verified(ws, vid, anchor_path=target,
                                                    job_id=proof.get("id", ""))
                    except ws_mod.WorkspaceError:
                        pass                      # promotion is best-effort
        identity_note = None
        if req.identity is not None:
            want_identity = req.identity
        elif is_flux2:
            # flux2 reference-conditioning already carries identity — don't auto-stack the
            # inswapper swap on top (set identity=true to add it explicitly).
            want_identity = False
            if anchor_path is not None:
                identity_note = ("flux2 reference-conditioning carries identity; the inswapper "
                                 "lock is NOT auto-applied (set identity=true to add it)")
        else:
            want_identity = anchor_path is not None and anchor_ok
            if anchor_path is not None and not anchor_ok:
                identity_note = ("anchor UNVERIFIED — identity defaulted OFF; run once with "
                                 "identity:true (the worker verifies the anchor face on its "
                                 "first run) to enable default-on")
        if want_identity:
            if anchor_path is None:
                raise HTTPException(422, "identity pass needs a face anchor — pick one first "
                                         "(POST /assets/{id}/anchor with a face image output)")
            if not req.dry_run:
                i_ok, i_missing = components.postproc_weights_status("identity")
                if not i_ok:
                    raise HTTPException(412, {
                        "error": "identity-lock weight(s) missing", "missing": i_missing,
                        "hint": "POST /components/fetch?postproc=identity "
                                "(inswapper_128 — research/non-commercial license)"})
            # Insert BEFORE a restore pass (M6): the lock first, then GFPGAN fixes the
            # swap's 128px softness — restore stays the chain's final word.
            identity_spec = {
                "pass": "identity", "backend": "identity",
                "anchor": str(anchor_path),
                "min_det_score": req.identity_min_det_score,
            }
            r_idx = next((i for i, p in enumerate(post_passes)
                          if p.get("pass") == "restore"), len(post_passes))
            post_passes = [*post_passes[:r_idx], identity_spec, *post_passes[r_idx:]]

        script = ADAPTERS[req.pipeline].resolve_script(CONFIG.pipeline_roots)
        if script is None:
            raise HTTPException(503, f"{req.pipeline} worker not found")

        # Realization groups (M3.5): img2img sweep cells + (mixed only) inpaint cells that
        # repaint the background around the held subject. Each group = ONE batch job. flux2
        # (§11) is ONE `ref` group — every cell is reference-conditioned on the hero.
        cells = built["cells"]
        if is_flux2:
            groups = [("ref", cells, None)]
        elif req.realize == "mixed":
            i2i_cells = [c for c in cells if c["method"] != "inpaint"]
            inp_cells = [c for c in cells if c["method"] == "inpaint"]
            groups = [g for g in (("img2img", i2i_cells, req.strength),
                                  ("inpaint", inp_cells, req.inpaint_strength)) if g[1]]
        else:
            groups = [("img2img", cells, req.strength)]
        split = {mode: len(gcells) for mode, gcells, _ in groups}
        # M2.7: zimage/sd35 (+ flux2) Expansion streams INDIVIDUAL warm cell-jobs (one resident worker
        # per realization group, model loaded once) so each tile is its own queue entry that survives
        # pause/resume. Phase 2b: post-passes (identity/clean/polish) now chain PER CELL off the warm
        # cell-job (each pass tile its own pause-safe job too), so a sweep with post-passes no longer
        # falls back to the cold batch. `realize="mixed"` (the inpaint background-diversity axis) still
        # rides the cold batch — its two-group bg-mask realization comes to the warm path in a later
        # phase; that's the only remaining cold-batch Expansion case.
        warm_cells = is_flux2 or (req.realize != "mixed"
                                  and hasattr(ADAPTERS.get(req.pipeline), "serve_argv"))
        planned = len(cells) if warm_cells else len(groups)

        if req.dry_run:
            # Preview with a single-cell argv (a batch argv would write jobs.json into
            # out/); the real run is ONE --jobs-file batch job per realization group.
            cell0 = built["cells"][0]
            if is_flux2:
                p0 = {"prompt": cell0["prompt"], "ref_images": [str(hero_path)],
                      "width": eff_width, "height": eff_height, "seed": cell0["seed"],
                      "model_name": model_name, **extra}
                spec = JobSpec(pipeline="flux2", mode="ref", params=p0, output_dir=ws.out_dir)
            else:
                p0 = {"prompt": cell0["prompt"], "init_image": str(hero_path),
                      "strength": req.strength, "width": eff_width, "height": eff_height,
                      "seed": cell0["seed"], "model_name": model_name, **extra}
                spec = JobSpec(pipeline=req.pipeline, mode="img2img", params=p0, output_dir=ws.out_dir)
            return {"dry_run": True, "preset": req.preset, "pipeline": req.pipeline,
                    "planned_jobs": planned, "items": built["target"], "split": split,
                    "realize": req.realize, "bg_mask": req.bg_mask,
                    "advanced_prompt": built["advanced_prompt"],
                    "json_prompt": built["json_prompt"],
                    "identity": want_identity, "identity_note": identity_note,
                    "kept_target": built["kept_target"], "post_passes": post_passes,
                    "hero": str(hero_path), "first_cell": cell0,
                    "first_argv": ADAPTERS[req.pipeline].build_argv(spec, CONFIG.venv_python, script)}

        # Pre-flight: the EFFECTIVE (or default) img2img model must be cached (fail fast, not mid-run).
        variant = model_catalog.find_variant(
            req.pipeline, model_name or model_catalog.default_model(req.pipeline))
        if variant and not components.variant_weights_present(variant):
            raise HTTPException(412, {"error": f"{req.pipeline} model {variant['id']!r} not in cache",
                                      "repo_id": variant["repo_id"], "gated": variant["gated"],
                                      "hint": "fetch it first (gated repos need a HF license + token)"})
        est = estimate_vram(req.pipeline)
        if est > CONFIG.vram_budget_gb:
            raise HTTPException(422, f"{req.pipeline} needs ~{est} GB VRAM > budget "
                                     f"{CONFIG.vram_budget_gb} GB")
        if GUARD.is_hard_blocked():
            raise HTTPException(507, f"disk hard-stop — {GUARD.block_reason()}")

        batch_id = "bat_" + uuid.uuid4().hex[:8]
        # ONE batch job per realization group (review 2026-06-10 #1 — the batch-mode
        # worker): the worker loads the model once and loops every cell (`--jobs-file`).
        # Each item carries its frozen coverage_cell as opaque `meta`, echoed back
        # per-output (`result.output_meta`) for Stage-C curation; images stream into the
        # grid as interim results. Mixed realization (M3.5) adds a SECOND batch job: the
        # inpaint-method cells repaint the background (white bg mask) around the held
        # subject — identity-safe, restores the §7.1 background-diversity axis.
        job_ids: list[str] = []
        if is_flux2:
            # M2.7: flux2 Expansion → N INDIVIDUAL cell-jobs (each a persistent tile that survives
            # pause/resume), serviced by ONE warm worker so the model loads once for the sweep. The
            # `warm_group` is the load-bound params + hero, so the whole sweep (and a later sweep
            # with the same model/hero/size) shares the resident model. Each cell rides the hero as
            # its in-context reference (§11). ⚠ Phase 1: post-passes (identity/clean/polish) are NOT
            # chained onto warm cells yet (per-cell chaining = Phase 2); raw ref cells stream in.
            wg = "|".join(["flux2", str(model_name), f"{eff_width}x{eff_height}", str(hero_path),
                           f"turbo={bool(extra.get('turbo'))}", f"te={extra.get('text_encoder')}",
                           f"mm={extra.get('fp8_matmul')}"])
            for i, c in enumerate(cells):
                cparams = {"prompt": c["prompt"], "seed": c["seed"], "ref_images": [str(hero_path)],
                           "width": eff_width, "height": eff_height,
                           "meta": {"coverage_cell": c["coverage_cell"], "method": "ref"}, **extra}
                if model_name:
                    cparams["model_name"] = model_name
                job_ids.append(RUNNER.submit(
                    pipeline="flux2", mode="ref", params=cparams,
                    batch_id=batch_id, index=i, batch_size=len(cells),
                    requester_id=vid, profile_version_id=vid, stage="B",
                    coverage_cell=c["coverage_cell"], warm_group=wg,
                    post_passes=post_passes))   # Phase 2b: each cell chains its own post-passes
        else:
          for gmode, gcells, gstrength in groups:
            if warm_cells:
                # M2.7 Phase 2a: N INDIVIDUAL cell-jobs per realization group, each a persistent tile
                # serviced by ONE warm worker (the model is the load-bound part of the warm_group, so
                # a group's cells share the resident pipeline; img2img and inpaint groups bind distinct
                # groups and run back-to-back — one evict between them, cells contiguous so no thrash).
                wg = "|".join([req.pipeline, str(model_name), gmode, f"{eff_width}x{eff_height}",
                               str(hero_path), f"s={gstrength}"])
                for c in gcells:
                    cparams = {"prompt": c["prompt"], "seed": c["seed"],
                               "width": eff_width, "height": eff_height,
                               "meta": {"coverage_cell": c["coverage_cell"], "method": c["method"]},
                               **extra}
                    cparams["init_image"] = str(hero_path)
                    cparams["strength"] = gstrength
                    if gmode == "inpaint":
                        cparams["mask_image"] = bg_mask_abs
                    if model_name:
                        cparams["model_name"] = model_name
                    job_ids.append(RUNNER.submit(
                        pipeline=req.pipeline, mode=gmode, params=cparams,
                        batch_id=batch_id, index=len(job_ids), batch_size=len(cells),
                        requester_id=vid, profile_version_id=vid, stage="B",
                        coverage_cell=c["coverage_cell"], warm_group=wg,
                        post_passes=post_passes))   # Phase 2b: each cell chains its own post-passes
                continue
            # Cold batch fallback (post-passes present): ONE --jobs-file job per group, post-passes
            # chained after the sweep. Provenance method = the ACTUAL realization; zimage/sd35 keep
            # the recipe method (in mixed it already equals the group; in plain img2img the cell's).
            items = [{"prompt": c["prompt"], "seed": c["seed"],
                      "meta": {"coverage_cell": c["coverage_cell"],
                               "method": "ref" if gmode == "ref" else c["method"]}}
                     for c in gcells]
            params = {"prompt": f"[dataset {req.preset} · {len(gcells)} {gmode} cells] {clause}",
                      "width": eff_width, "height": eff_height,
                      "batch_items": items, **extra}
            if gmode == "ref":
                # flux2 (§11): the hero rides as an in-context reference for every cell
                # (encoded once by the batch worker) — no init_image/strength.
                params["ref_images"] = [str(hero_path)]
            else:
                params["init_image"] = str(hero_path)
                params["strength"] = gstrength
                if gmode == "inpaint":
                    params["mask_image"] = bg_mask_abs
            if model_name:
                params["model_name"] = model_name
            job_ids.append(RUNNER.submit(
                pipeline=req.pipeline, mode=gmode, params=params,
                batch_id=batch_id, index=len(job_ids), batch_size=len(groups),
                requester_id=vid, profile_version_id=vid, stage="B",
                post_passes=post_passes))
        LOG.info("stage-b: %s preset=%s realize=%s -> %d batch job(s) %s for %s (hero %s)",
                 batch_id, req.preset, req.realize, len(job_ids), split, vid, hero_path.name)
        return {"batch_id": batch_id, "preset": req.preset, "count": len(job_ids),
                "items": len(cells), "split": split, "realize": req.realize,
                "identity": want_identity, "identity_note": identity_note,
                "job_ids": job_ids, "kept_target": built["kept_target"]}

    @app.post("/assets/{asset_id}/stage-b/matte")
    def stage_b_matte(asset_id: str, req: MatteRequest,
                      _auth: None = Depends(require_token)) -> dict:
        """Matte the version's hero ★ (M3.5, first postproc-class job): one `birefnet`
        run → subject matte + RGBA cutout + the **background-inpaint mask** that
        `realize="mixed"` Stage-B expansion consumes. Weight pre-flight is TOOL-scoped
        (412 + fetch hint — never folded into the phase gate; same posture as the multi
        presets). Token-gated; `dry_run` previews the argv without the GPU."""
        ws = _require_ws()
        try:
            version, _hero, hero_path = assets.resolve_hero(ws, asset_id, req.version_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(409, str(e))
        vid = version["id"]
        try:
            extra = model_catalog.validate_params("birefnet", "matte", req.params)
        except model_catalog.CatalogError as e:
            raise HTTPException(422, str(e))
        script = ADAPTERS["birefnet"].resolve_script(CONFIG.pipeline_roots)
        if script is None:
            raise HTTPException(503, "birefnet worker not found in any pipeline root")
        params = {"input_image": str(hero_path), **extra}
        if req.dry_run:
            spec = JobSpec(pipeline="birefnet", mode="matte", params=params,
                           output_dir=ws.out_dir)
            return {"dry_run": True, "pipeline": "birefnet", "hero": str(hero_path),
                    "argv": ADAPTERS["birefnet"].build_argv(spec, CONFIG.venv_python, script)}
        # Variant-aware weight gate (review 2026-06-11 Medium): probe the repo the worker
        # will ACTUALLY load — params.model_name="birefnet-hr" must not clear a gate that
        # only checked the default variant and then die mid-worker on the missing HR repo.
        chosen = extra.get("model_name") or model_catalog.default_model("birefnet")
        ok, missing = components.postproc_weights_status("birefnet", chosen)
        if not ok:
            raise HTTPException(412, {
                "error": f"birefnet matting weight(s) missing for variant {chosen!r}",
                "missing": missing,
                "hint": f"POST /components/fetch?postproc=birefnet&postproc_variant={chosen} "
                        "(ungated, ~0.9 GB)"})
        if estimate_vram("birefnet") > CONFIG.vram_budget_gb:
            raise HTTPException(422, f"birefnet needs ~{estimate_vram('birefnet')} GB VRAM "
                                     f"> budget {CONFIG.vram_budget_gb} GB")
        if GUARD.is_hard_blocked():
            raise HTTPException(507, f"disk hard-stop — {GUARD.block_reason()}")
        batch_id = "bat_" + uuid.uuid4().hex[:8]
        jid = RUNNER.submit(pipeline="birefnet", mode="matte", params=params,
                            batch_id=batch_id, index=0, batch_size=1,
                            requester_id=vid, profile_version_id=vid, stage="B")
        LOG.info("stage-b matte: %s for %s (hero %s)", jid, vid, hero_path.name)
        return {"job_id": jid, "batch_id": batch_id, "hero": str(hero_path)}

    @app.post("/assets/{asset_id}/versions")
    def create_version(asset_id: str, req: CreateVersionRequest,
                       _auth: None = Depends(require_token)) -> dict:
        """M5 — copy-on-create (R50/R58/R59): a FULL deep-duplicate of any prior version
        (refs, casting, face anchor incl. its verification; `derived_from` recorded),
        fresh + unlocked, made active. Big change → new *profile* instead (author's
        call, no hints — R61). Token-gated."""
        try:
            return assets.create_version(_require_ws(), asset_id,
                                         parent_version_id=req.parent_version_id,
                                         name=req.name)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))

    @app.post("/assets/{asset_id}/versions/{version_id}/finalize")
    def finalize_version(asset_id: str, version_id: str,
                         _auth: None = Depends(require_token)) -> dict:
        """M5 — finalize = pure-intent lock (R60): the version becomes immutable (every
        mutator refuses); change means a new version. Idempotent. Token-gated."""
        try:
            return assets.finalize_version(_require_ws(), asset_id, version_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))

    @app.post("/assets/{asset_id}/versions/{version_id}/unfinalize")
    def unfinalize_version(asset_id: str, version_id: str,
                           _auth: None = Depends(require_token)) -> dict:
        """Unlock a finalized version (user 2026-06-21) so its curation/refs/casting can be
        cleaned up — the explicit escape hatch from the finalize lock. Idempotent. Token-gated."""
        try:
            return assets.unfinalize_version(_require_ws(), asset_id, version_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))

    @app.delete("/assets/{asset_id}")
    def delete_asset(asset_id: str, _auth: None = Depends(require_token)) -> dict:
        """Delete a whole character/AssetProfile + ALL its versions (refs/casting/faces/anchors)
        — the profile directory (user 2026-06-21). out/ generations + lineage are left (project-
        level, rebuildable). 404 on an unknown asset. Token-gated."""
        try:
            return assets.delete_asset(_require_ws(), asset_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))

    @app.post("/assets/{asset_id}/versions/activate")
    def activate_version(asset_id: str, req: ActivateVersionRequest,
                         _auth: None = Depends(require_token)) -> dict:
        """M5 — switch the active version (the selector); grids/casting/curation/Stage-B
        all scope to it. Returns the updated profile. Token-gated."""
        try:
            return assets.set_active_version(_require_ws(), asset_id, req.version_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))

    @app.post("/assets/{asset_id}/stage-b/sketch")
    def stage_b_sketch(asset_id: str, req: SketchRequest,
                       _auth: None = Depends(require_token)) -> dict:
        """M7 — video-sketch harvest: ONE `ltxv` i2v job from the hero ★ (the target
        coverage cell rides as the job's first-class field) + a chained `frame_harvest`
        pass whose extracted stills inherit that cell — they stream into the Stage-B grid
        and curate exactly like recipe cells. Token-gated; `dry_run` previews the argv."""
        ws = _require_ws()
        try:
            version, _hero, hero_path = assets.resolve_hero(ws, asset_id, req.version_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(409, str(e))
        vid = version["id"]
        cell = {"shot_size": req.shot_size, "angle": req.angle,
                "expression": req.expression, "background": ""}
        try:
            coverage.validate_cell(cell)
        except coverage.CoverageError as e:
            raise HTTPException(422, str(e))
        clause = (req.character_clause or version.get("prompt_template") or "").strip()
        if not clause:
            raise HTTPException(422, "no character_clause and the version has no "
                                     "prompt_template — set one (R112)")
        try:
            extra = model_catalog.validate_params("ltxv", "i2v", req.params)
        except model_catalog.CatalogError as e:
            raise HTTPException(422, str(e))
        model_name = extra.pop("model_name", None)
        # L1 gate, single source of truth (M8 review). Prompt order (user 2026-06-10):
        # cell fragment leads → clause → motion → style; the global negative rides params.
        _apply, fragment, global_neg = bible.resolve_l1(ws, req.apply_style, req.style_id)
        motion = (req.motion_prompt or "").strip() or \
            "slow steady camera, the character turns and moves naturally"
        prompt = ", ".join(p for p in
                           (recipe._cell_prompt_fragment(cell), clause, motion, fragment)
                           if p)
        script = ADAPTERS["ltxv"].resolve_script(CONFIG.pipeline_roots)
        if script is None:
            raise HTTPException(503, "ltxv worker not found in any pipeline root")
        harvest_spec = {"pass": "harvest", "backend": "frame_harvest",
                        "every": req.every, "max_frames": req.max_frames}
        params = {"prompt": prompt, "init_image": str(hero_path), **extra}
        if global_neg:
            params["negative_prompt"] = bible.join_negative(params.get("negative_prompt"), global_neg)
        if model_name:
            params["model_name"] = model_name
        # Persist the ltxv default dims when unset (review 2026-06-12): the catalog default
        # is 704×480, but without these on the job params the CHAINED harvest pass falls
        # back to 1024² display metadata → harvested stills render square in the grid.
        for dim in ("width", "height"):
            if dim not in params:
                params[dim] = model_catalog.param_default("ltxv", dim)
        if req.dry_run:
            spec = JobSpec(pipeline="ltxv", mode="i2v", params=params,
                           output_dir=ws.out_dir)
            return {"dry_run": True, "pipeline": "ltxv", "cell": cell, "prompt": prompt,
                    "post_passes": [harvest_spec], "hero": str(hero_path),
                    "argv": ADAPTERS["ltxv"].build_argv(spec, CONFIG.venv_python, script)}
        chosen = model_name or model_catalog.default_model("ltxv")
        variant = model_catalog.find_variant("ltxv", chosen)
        if variant and not components.variant_weights_present(variant):
            raise HTTPException(412, {
                "error": f"ltxv model {variant['id']!r} not in cache",
                "repo_id": variant["repo_id"], "gated": variant["gated"],
                "hint": "fetch it first (ungated diffusers repo)"})
        if estimate_vram("ltxv") > CONFIG.vram_budget_gb:
            raise HTTPException(422, f"ltxv needs ~{estimate_vram('ltxv')} GB VRAM "
                                     f"> budget {CONFIG.vram_budget_gb} GB")
        if GUARD.is_hard_blocked():
            raise HTTPException(507, f"disk hard-stop — {GUARD.block_reason()}")
        jid = RUNNER.submit(pipeline="ltxv", mode="i2v", params=params,
                            batch_id="bat_" + uuid.uuid4().hex[:8], index=0, batch_size=1,
                            requester_id=vid, profile_version_id=vid, stage="B",
                            coverage_cell=cell, post_passes=[harvest_spec])
        LOG.info("stage-b sketch: %s cell=%s/%s/%s for %s (hero %s)",
                 jid, cell["shot_size"], cell["angle"], cell["expression"], vid,
                 hero_path.name)
        return {"job_id": jid, "cell": cell, "prompt": prompt,
                "harvest": {"every": req.every, "max_frames": req.max_frames}}

    @app.post("/assets/{asset_id}/anchor/derive")
    def derive_anchor(asset_id: str, req: DeriveAnchorRequest,
                      _auth: None = Depends(require_token)) -> dict:
        """M6.1 — queue a face-PORTRAIT derivation (face_restore mode `portrait`) from an
        owned output: aligned 512² crop of the largest face, GFPGAN-restored. Lands in the
        Stage-A grid; anchor it with '⚓ set as face anchor'. Ownership-guarded;
        tool-scoped weight 412. Token-gated."""
        ws = _require_ws()
        job = RUNNER.get(req.job_id)
        if job is None or job.get("status") != "done":
            raise HTTPException(404, f"no completed job {req.job_id!r}")
        vid = _require_job_owned_by(ws, asset_id, req.version_id, job)
        result = job.get("result") or {}
        pool = result.get("output_names") or ([result["output_name"]]
                                              if result.get("output_name") else [])
        output = req.output or (pool[0] if len(pool) == 1 else None)
        if not output or output not in pool:
            raise HTTPException(409, f"output {req.output!r} is not one of job "
                                     f"{req.job_id!r}'s outputs")
        obase = ws.out_dir.resolve()
        src = (obase / output).resolve()
        if not src.is_relative_to(obase) or not src.is_file():
            raise HTTPException(404, f"output {output!r} not found in out/")
        ok, missing = components.postproc_weights_status("face_restore")
        if not ok:
            raise HTTPException(412, {
                "error": "face-restore weight(s) missing", "missing": missing,
                "hint": "POST /components/fetch?postproc=face_restore"})
        if GUARD.is_hard_blocked():
            raise HTTPException(507, f"disk hard-stop — {GUARD.block_reason()}")
        params = {"prompt": f"[face portrait of {output}]",
                  "batch_items": [{"input": str(src), "seed": 0,
                                   "meta": {"source_output": output}}],
                  "blend": req.blend, "min_det_score": 0.5,
                  "width": 512, "height": 512}        # display dims (512² crop)
        jid = RUNNER.submit(pipeline="face_restore", mode="portrait", params=params,
                            batch_id="bat_" + uuid.uuid4().hex[:8], index=0, batch_size=1,
                            requester_id=vid, profile_version_id=vid, stage="A")
        LOG.info("anchor derive: %s from %s for %s", jid, output, vid)
        return {"job_id": jid, "source_output": output}

    @app.post("/assets/{asset_id}/anchor")
    def set_anchor(asset_id: str, req: AnchorRequest,
                   _auth: None = Depends(require_token)) -> dict:
        """Set or clear the version's **face anchor** (M4, R94). Set: `job_id` + an
        `output` of that job (defaults to its primary) — ownership-guarded like
        refs/keep, then copied into the version's `faces/` dir (self-contained). Clear:
        `job_id=null`. The anchor drives the Stage-B identity-lock pass (R93:
        default-on once present). Token-gated."""
        ws = _require_ws()
        if req.job_id is None:
            try:
                return assets.clear_anchor(ws, asset_id, req.version_id)
            except ws_mod.WorkspaceError as e:
                raise HTTPException(404, str(e))
        job = RUNNER.get(req.job_id)
        if job is None or job.get("status") != "done":
            raise HTTPException(404, f"no completed job {req.job_id!r}")
        _require_job_owned_by(ws, asset_id, req.version_id, job)   # never cross-asset
        result = job.get("result") or {}
        output = req.output or result.get("output_name")
        names = result.get("output_names") or ([result["output_name"]]
                                               if result.get("output_name") else [])
        if not output or output not in names:
            raise HTTPException(422, f"output {output!r} is not one of job "
                                     f"{req.job_id!r}'s outputs")
        try:
            return assets.set_anchor(ws, asset_id, job_id=req.job_id,
                                     source_output=output, version_id=req.version_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))

    @app.get("/assets/{asset_id}/anchor/file")
    def get_anchor(asset_id: str, version_id: str | None = None) -> FileResponse:
        """Serve the version's anchor image (unauthenticated read, mirrors /outputs)."""
        ws = _require_ws()
        try:
            path = assets.anchor_file_path(ws, asset_id, version_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))
        if path is None:
            raise HTTPException(404, "no anchor set for this version")
        return FileResponse(path)

    @app.post("/assets/{asset_id}/casting/hero")
    def set_hero(asset_id: str, req: HeroRequest,
                 _auth: None = Depends(require_token)) -> dict:
        """Set/clear the hero ★ among already-recorded casting candidates. Token-gated."""
        try:
            return assets.set_hero(_require_ws(), asset_id,
                                   candidate_id=req.candidate_id, version_id=req.version_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))

    @app.get("/assets/{asset_id}/casting/{file}")
    def get_casting(asset_id: str, file: str,
                    version_id: str | None = None) -> FileResponse:
        """Serve a saved casting candidate image from the version's casting/ dir
        (traversal-guarded). Unauthenticated read (mirrors /outputs)."""
        try:
            path = assets.casting_file_path(_require_ws(), asset_id, file, version_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))
        return FileResponse(path)

    # --- P1/M3: Stage-C curation (keep/cull → ref_set) + Save AssetProfile ---
    @app.post("/assets/{asset_id}/refs/keep")
    def keep_ref(asset_id: str, req: KeepRefRequest,
                 _auth: None = Depends(require_token)) -> dict:
        """Keep a completed Stage-B candidate into the version's curated `ref_set` (M3) — copies
        the image into `refs/` + records its frozen coverage_cell (from the job). Token-gated."""
        ws = _require_ws()
        job = RUNNER.get(req.job_id)
        if job is None:
            raise HTTPException(404, f"no such job {req.job_id!r}")
        if job.get("status") != "done":
            raise HTTPException(409, "can only keep a completed (done) candidate")
        # Terminal-output guard (M4 review, High): a job with PENDING post-passes is not the
        # end of its chain — its outputs are pre-clean/pre-polish/pre-IDENTITY-LOCK images,
        # and curating them silently poisons the ref_set/P2 corpus (the whole point of
        # identity-on-by-default). Curate the terminal pass job's outputs instead. The
        # explicit `allow_unlocked` escape covers the legitimate case (e.g. a ⏹-stopped
        # chain whose passes were deliberately not run).
        pending = job.get("post_passes") or []
        if pending and not req.allow_unlocked:
            names = " → ".join(p.get("pass", "?") for p in pending)
            raise HTTPException(409, f"this output is NOT the end of its pass chain — "
                                     f"{names} pass(es) pending/un-run; curate the terminal "
                                     "pass outputs instead, or set allow_unlocked=true to "
                                     "keep the un-processed image deliberately")
        # Scope guard: the job must belong to the asset/version being curated into — never let a
        # stale / cross-asset / manual call write Asset A's output into Asset B's ref_set.
        vid = _require_job_owned_by(ws, asset_id, req.version_id, job)
        result = job.get("result") or {}
        pool = result.get("output_names") or ([result["output_name"]]
                                              if result.get("output_name") else [])
        if req.output is not None:
            if req.output not in pool:
                raise HTTPException(409, f"output {req.output!r} not in job {req.job_id!r}")
            output = req.output
        elif len(pool) == 1:
            output = pool[0]
        elif not pool:
            raise HTTPException(409, "candidate job has no output to keep")
        else:
            raise HTTPException(409, "multi-output job — specify which `output`")
        # M7: the sketch VIDEO carries the job-level cell but is not a ref — only its
        # harvested FRAMES are (the chained frame_harvest tiles).
        if output.lower().endswith((".mp4", ".webm", ".mov")):
            raise HTTPException(422, "videos are not refs — curate the harvested frames "
                                     "(the chained frame_harvest tiles), not the sketch")
        # The frozen coverage_cell: per-job for legacy single-cell jobs; per-OUTPUT
        # (result.output_meta, echoed from the batch manifest) for batch dataset jobs.
        ometa = (result.get("output_meta") or {}).get(output) or {}
        cov = job.get("coverage_cell") or ometa.get("coverage_cell")
        if not cov:
            raise HTTPException(422, "output has no coverage_cell — only Stage-B outputs are "
                                     "curated into the ref_set (cast via /assets/{id}/stage-b)")
        try:
            version = assets.keep_ref(
                ws, asset_id, job_id=req.job_id, source_output=output, coverage_cell=cov,
                version_id=vid, pipeline=job.get("pipeline"),
                seed=ometa.get("seed", result.get("seed")),
                method=ometa.get("method", job.get("mode")))
        except (ws_mod.WorkspaceError, coverage.CoverageError) as e:
            raise HTTPException(400, str(e))
        LOG.info("curate keep: job %s -> asset %s (%d refs)",
                 req.job_id, asset_id, len(version.get("ref_set", [])))
        return version

    @app.post("/assets/{asset_id}/refs/reject")
    def reject_ref(asset_id: str, req: RejectRefRequest,
                   _auth: None = Depends(require_token)) -> dict:
        """P1-12 curation throughput: mark/unmark a Stage-B candidate output as REJECTED —
        a persistent cull-from-view list (`version.rejected[]`, out/-relative names, no
        image copy) so the ~100→~30 reject sweep survives reloads. Ownership-guarded like
        refs/keep; a kept output 409s (cull first). Token-gated."""
        ws = _require_ws()
        job = RUNNER.get(req.job_id)
        if job is None:
            raise HTTPException(404, f"no such job {req.job_id!r}")
        if job.get("status") != "done":
            raise HTTPException(409, "can only reject a completed (done) candidate")
        vid = _require_job_owned_by(ws, asset_id, req.version_id, job)
        result = job.get("result") or {}
        pool = result.get("output_names") or ([result["output_name"]]
                                              if result.get("output_name") else [])
        output = req.output or (pool[0] if len(pool) == 1 else None)
        if not output or output not in pool:
            raise HTTPException(409, f"output {req.output!r} is not one of job "
                                     f"{req.job_id!r}'s outputs")
        # Same Stage-C contract as refs/keep (review 2026-06-11 Low: the endpoint claimed
        # Stage-B candidates but accepted anything owned) — only coverage-bearing dataset
        # outputs belong in the cull record.
        ometa = (result.get("output_meta") or {}).get(output) or {}
        if not (job.get("coverage_cell") or ometa.get("coverage_cell")):
            raise HTTPException(422, "output has no coverage_cell — only Stage-B dataset "
                                     "outputs are rejected (the Stage-C cull list)")
        try:
            return assets.reject_output(ws, asset_id, source_output=output,
                                        version_id=vid, rejected=req.rejected)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(409, str(e))

    @app.post("/assets/{asset_id}/refs/cull")
    def cull_ref(asset_id: str, req: CullRefRequest,
                 _auth: None = Depends(require_token)) -> dict:
        """Cull (un-keep) a curated ref by id — drops it from `ref_set` + deletes its refs/
        copy. Token-gated."""
        try:
            return assets.remove_ref(_require_ws(), asset_id, ref_id=req.ref_id,
                                     version_id=req.version_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))

    @app.post("/assets/{asset_id}/save")
    def save_profile(asset_id: str, req: SaveProfileRequest,
                     _auth: None = Depends(require_token)) -> dict:
        """Save AssetProfile (R119, the MVP done-line): persist the identity clause
        (`prompt_template`) + re-stamp saved_at. Saved, not Finalized. Token-gated."""
        try:
            return assets.save_profile(_require_ws(), asset_id,
                                       prompt_template=req.prompt_template,
                                       version_id=req.version_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(409, str(e))

    @app.get("/assets/{asset_id}/refs/{file}")
    def get_ref(asset_id: str, file: str, version_id: str | None = None) -> FileResponse:
        """Serve a curated ref image from the version's refs/ dir (traversal-guarded).
        Unauthenticated read (mirrors /outputs + /casting)."""
        try:
            path = assets.ref_file_path(_require_ws(), asset_id, file, version_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))
        return FileResponse(path)

    # --- M0c (P2): PROJECT-LEVEL postprocess stack (any image, regardless of origin) ----
    _PP_PRESETS = {
        "clean":   {"backend": "zimage", "mode": "img2img", "params": {"strength": 0.5}},
        "refine":  {"backend": "zimage", "mode": "img2img", "params": {"strength": 0.25}},
        "restore": {"backend": "face_restore", "mode": "restore", "params": {"blend": 0.8}},
        # M0e Part C — creative upscale via SD3.5 Tile ControlNet: a single-run sd35 cn-inpaint
        # job. The tile CN is the conditioner (no init_image); diffusers resizes it to the target
        # H×W, so source→control + a larger size = the upscale. sd35-fixed (SD3-medium tile CN).
        "upscale": {"backend": "sd35", "mode": "cn-inpaint",
                    "params": {"controlnet": "tile", "cn_scale": "0.6", "scale": 2}},
    }

    def _producing_job(src: str):
        """The completed job whose outputs include `src` (out/-relative), or None — used to
        route a postproc OUTPUT into the SAME grid as its source (inherit requester/version),
        so postprocessing a character image lands in that character's grid, a Sandbox image in
        the Sandbox. Best-effort over a snapshot of the job table."""
        for j in list(RUNNER.jobs.values()):
            res = j.get("result") or {}
            names = res.get("output_names") or ([res["output_name"]]
                                                if res.get("output_name") else [])
            if src in names:
                return j
        return None

    def _job_state(jid: str):
        """`(status, ok-output)` for a job, or None if it's gone from the queue (deleted/
        pruned). Feeds postproc.reconcile so a step tracks its job's real state."""
        j = RUNNER.get(jid)
        if j is None:
            return None
        res = j.get("result") or {}
        out = res.get("output_name") or (res.get("output_names") or [None])[0]
        return (j.get("status"), out if res.get("ok") else None)

    @app.get("/postproc/stacks")
    def get_postproc_stacks() -> dict:
        """The project's postprocess stacks (M0c) — reconciled with the live job queue first
        (a step whose job failed / was canceled / was deleted no longer stays stuck 'queued').
        Unauthenticated read (mirrors /jobs)."""
        return {"stacks": postproc.reconcile(_require_ws(), _job_state)}

    @app.post("/postproc/step")
    def add_postproc_step(req: AddPostprocStepRequest,
                          _auth: None = Depends(require_token)) -> dict:
        """M0c — configure (persist, NOT queue) a postprocess step onto a base image's
        PROJECT-level stack (any image, any origin). Clean/Refine = img2img presets (backend
        zimage|sd35); restore = GFPGAN. A separate queue call fires the job. Token-gated."""
        spec = _PP_PRESETS[req.preset]
        is_i2i = spec["mode"] == "img2img"
        is_upscale = spec["mode"] == "cn-inpaint"   # M0e Part C — sd35 tile-CN creative upscale
        backend = (req.backend or spec["backend"]) if is_i2i else spec["backend"]
        # M0d Part C — flux2 joins zimage/sd35 as an i2i backend (structured-JSON i2i on
        # flux.2-dev: edit/re-pose an existing image). flux2 i2i is a SINGLE-run job (the
        # worker's batch path is t2i/ref only) — handled in the queue endpoint.
        if is_i2i and backend not in ("zimage", "sd35", "flux2"):
            raise HTTPException(422, f"img2img backend must be zimage|sd35|flux2, got {backend!r}")
        if not is_i2i and req.backend and req.backend != spec["backend"]:
            raise HTTPException(422, f"{req.preset!r} backend is fixed ({spec['backend']})")
        if is_i2i:
            allowed = {"strength", "prompt", "negative_prompt", "model_name"}
            # M0e Part B — an optional OUTPUT SIZE (scale factor + explicit W×H) so a Clean/Refine
            # step can re-diffuse larger = an i2i creative upscale. zimage/sd35 only: flux2 i2i
            # (the M0d dev-JSON re-pose) keeps source dims — its job is edit-in-place, not enlarge.
            if backend != "flux2":
                allowed |= {"width", "height", "scale"}
        elif is_upscale:
            # M0e Part C — tile-CN upscale: prompt (defaults to source), the output size (Part B
            # resolver), CN conditioning scale, model. `controlnet` is fixed (tile) by the preset.
            allowed = {"prompt", "model_name", "cn_scale", "width", "height", "scale"}
        else:
            allowed = {"blend"}
        params = dict(spec["params"])
        for k, v in req.params.items():
            if k not in allowed:
                raise HTTPException(422, f"param {k!r} not valid for a {req.preset!r} step "
                                         f"(allowed: {sorted(allowed)})")
            params[k] = v
        model = params.get("model_name")
        if model and model_catalog.find_variant(backend, model) is None:
            raise HTTPException(422, f"model {model!r} is not a {backend} variant")
        # The InstantX SD3 tile CN is SD3-MEDIUM (hidden-dim match) — the worker errors on large;
        # so the upscale preset needs the sd3.5-medium base (the sd35 default when unset).
        if is_upscale and model and model != "sd3.5-medium":
            raise HTTPException(422, "tile-CN upscale needs the sd3.5-medium base (the InstantX "
                                     "SD3 tile ControlNet is SD3-medium)")
        _validate_postproc_size(params)
        try:
            return postproc.add_step(_require_ws(), base=req.base, preset=req.preset,
                                     backend=backend, mode=spec["mode"], params=params,
                                     mask=req.mask, requires_mask=req.requires_mask)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(409, str(e))

    @app.post("/postproc/step/{step_id}/queue")
    def queue_postproc_step(step_id: str, req: QueuePostprocStepRequest,
                            _auth: None = Depends(require_token)) -> dict:
        """M0c — fire a configured step's job over its source image: one batch job (img2img
        for clean/refine; the GFPGAN restore worker for restore). The output is routed into
        the same grid as the source (inherits its requester/version). On completion the runner
        observer records the produced output on the step. `dry_run` previews the job.
        Token-gated; weight pre-flight (412) + VRAM admission (422)."""
        ws = _require_ws()
        try:
            step = postproc.resolve_step(ws, step_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(404, str(e))
        # Block only if the linked job is ACTUALLY still active — a canceled/failed/deleted
        # job leaves a stale 'queued' status, but the step should be re-queueable (the queue
        # is the source of truth, not the persisted status). A live re-fire resets the step.
        live = RUNNER.get(step["job_id"]) if step.get("job_id") else None
        if live and live.get("status") in ("queued", "running"):
            raise HTTPException(409, f"step already {live['status']}")
        backend, mode = step["backend"], step["mode"]
        is_upscale = mode == "cn-inpaint"    # M0e Part C — sd35 tile-CN creative upscale (single-run)
        params_in = step.get("params") or {}
        # Resolve + traversal-guard the source image (out/-relative, like init_image).
        obase = ws.out_dir.resolve()
        src = step["source"]
        if ".." in src or "\\" in src:
            raise HTTPException(400, f"invalid source {src!r}")
        src_abs = (obase / src).resolve()
        if not src_abs.is_relative_to(obase) or not src_abs.is_file():
            raise HTTPException(404, f"source image {src!r} not found in out/")
        # Weight pre-flight + VRAM admission (skipped on dry_run — a no-GPU preview).
        if not req.dry_run:
            if backend == "face_restore":
                ok, missing = components.postproc_weights_status("face_restore")
                if not ok:
                    raise HTTPException(412, {"error": "face-restore weight(s) missing",
                                              "missing": missing,
                                              "hint": "POST /components/fetch?postproc=face_restore"})
            else:
                resolved = params_in.get("model_name") or model_catalog.default_model(backend)
                variant = model_catalog.find_variant(backend, resolved)
                if variant and not components.variant_weights_present(variant):
                    raise HTTPException(412, {
                        "error": f"{backend} model {variant['id']!r} not in cache",
                        "repo_id": variant["repo_id"], "gated": variant["gated"],
                        "hint": "fetch it first (gated repos need a HF license + token)"})
                # M0e Part C — the tile-CN upscale also needs the InstantX SD3 Tile ControlNet
                # weight (separate from the sd3.5-medium base above; a config.json repo, not a
                # pipeline) — offer its fetch rather than dying inside the worker.
                if is_upscale:
                    cn_ok, cn_missing = components.postproc_weights_status("sd35_tile_cn")
                    if not cn_ok:
                        raise HTTPException(412, {
                            "error": "SD3.5 Tile ControlNet weight missing",
                            "missing": cn_missing,
                            "hint": "POST /components/fetch?postproc=sd35_tile_cn"})
            if estimate_vram(backend) > CONFIG.vram_budget_gb:
                raise HTTPException(422, f"{backend} needs ~{estimate_vram(backend)} GB VRAM "
                                         f"> budget {CONFIG.vram_budget_gb} GB")
        # img2img (clean/refine) + tile-CN upscale re-render the source, so they NEED a prompt —
        # the worker rejects an empty-prompt item (the batch returns 2 → the whole job fails; a
        # cn-inpaint t2i+CN run needs a prompt too). Default to the SOURCE image's own prompt (the
        # natural "process THIS image" behavior, mirroring the chained clean/polish passes): the
        # source's producing job, or — for a chained step — the previous step's per-output meta
        # prompt; else an author-typed prompt; else 422.
        parent = _producing_job(src) or {}
        needs_prompt = mode in ("img2img", "cn-inpaint")
        is_io = mode == "restore"
        item_prompt = ""
        if needs_prompt:
            pmeta = (parent.get("result") or {}).get("output_meta") or {}
            src_prompt = ((pmeta.get(src) or {}).get("prompt")
                          or (parent.get("params") or {}).get("prompt"))
            item_prompt = (params_in.get("prompt") or src_prompt or "").strip()
            if not item_prompt:
                raise HTTPException(422, "this image has no source prompt to re-render from — "
                                         "type a prompt for the clean/refine/upscale step")
        w, h = _image_dims(src_abs)   # source dims (restore + flux2 i2i preserve these)
        is_flux2 = backend == "flux2"
        if is_flux2:
            # M0d Part C — flux2 i2i is a SINGLE-run job (the worker's batch run_jobs does only
            # t2i/ref; img2img is its single-run run_img2img path). No batch_items: the adapter's
            # single-run build_argv emits --init-image/--strength/--prompt for mode=img2img.
            job_params: dict = {"prompt": item_prompt,
                                "init_image": str(src_abs), "width": w, "height": h}
            if params_in.get("strength") is not None:
                job_params["strength"] = params_in["strength"]
            if params_in.get("model_name"):
                job_params["model_name"] = params_in["model_name"]
        elif is_upscale:
            # M0e Part C — SD3.5 Tile ControlNet creative upscale: a SINGLE-run sd35 cn-inpaint
            # job (the worker's batch run_jobs is t2i/img2img/inpaint only; CN modes are single-
            # run). The tile CN is the CONDITIONER (no init_image); diffusers resizes the control
            # image to the target H×W, so source→control + a larger size IS the upscale. The
            # single-run sd35 build_argv → emit_argv gates --controlnet/--control-image/--cn-scale
            # to mode=cn-inpaint. Output dims = the Part B resolver (preset default ×2).
            tw, th = _postproc_target_dims((w, h), params_in)
            job_params = {"prompt": item_prompt, "control_image": str(src_abs),
                          "controlnet": params_in.get("controlnet", "tile"),
                          "width": tw, "height": th}
            if params_in.get("cn_scale") is not None:
                job_params["cn_scale"] = params_in["cn_scale"]
            if params_in.get("model_name"):
                job_params["model_name"] = params_in["model_name"]
        elif is_io:
            # restore (GFPGAN io-worker): preserve source dims (it's a face pass, not a resize).
            job_params = {"prompt": f"[{step['preset']} postproc of {src}]",
                          "batch_items": [{"input": str(src_abs)}], "width": w, "height": h,
                          "blend": params_in.get("blend", 0.8)}
        else:
            # img2img (clean/refine, zimage/sd35) — a batch job over the source. M0e Part B: the
            # output dims default to the source but an optional scale/explicit-W×H ENLARGES them
            # (creative i2i upscale); diffusers resizes the init image to the requested H×W.
            tw, th = _postproc_target_dims((w, h), params_in)
            job_params = {"prompt": f"[{step['preset']} postproc of {src}]",
                          "batch_items": [{"prompt": item_prompt, "init_image": str(src_abs)}],
                          "width": tw, "height": th}
            if params_in.get("strength") is not None:
                job_params["strength"] = params_in["strength"]
            if params_in.get("negative_prompt"):
                job_params["negative_prompt"] = params_in["negative_prompt"]
            if params_in.get("model_name"):
                job_params["model_name"] = params_in["model_name"]
        if req.dry_run:
            return {"dry_run": True, "pipeline": backend, "mode": mode,
                    "source": src, "params": job_params, "step_id": step_id}
        # Route the produced tile into a grid: the UI's explicit current context
        # (requester_id + stage) when given — so a queued tile appears where the author is
        # working — else inherit the source's producing job, else the project (Sandbox). A
        # scoped requester IS the version id (matches /generate's requester==version rule).
        if req.requester_id:
            requester, pvid, stage = req.requester_id, req.requester_id, req.stage
        else:
            requester = parent.get("requester_id") or ws.load_project()["id"]
            pvid, stage = parent.get("profile_version_id"), parent.get("stage")
        jid = RUNNER.submit(pipeline=backend, mode=mode, params=job_params,
                            batch_id="", index=0, batch_size=1, requester_id=requester,
                            profile_version_id=pvid, stage=stage, pass_name=step["preset"])
        try:
            return postproc.mark_queued(ws, step_id=step_id, job_id=jid)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(409, str(e))

    @app.delete("/postproc/step/{step_id}")
    def remove_postproc_step(step_id: str, _auth: None = Depends(require_token)) -> dict:
        """M0c — remove the LAST step of its stack (the chain tail; prunes an empty stack).
        Token-gated."""
        try:
            return postproc.remove_step(_require_ws(), step_id=step_id)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(409, str(e))

    @app.post("/project")
    def create_project(req: CreateProjectRequest, _auth: None = Depends(require_token)) -> dict:
        """`loom init` — create a project workspace (empty-folder + free-space validated,
        R80) and open it. Token-gated."""
        try:
            info = projects.create_project(Path(req.dest), name=req.name, fmt=req.format,
                                           size_cap_gb=req.size_cap_gb)
            GUARD.refresh()   # re-measure now that a new project is active (don't wait for the poll)
            return info
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    @app.post("/project/open")
    def open_project(req: OpenProjectRequest, _auth: None = Depends(require_token)) -> dict:
        """Open an existing project; its queue resumes **paused** (R88). Token-gated."""
        try:
            info = projects.open_project(Path(req.path))
            GUARD.refresh()   # re-measure now that a different project is active
            return info
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    @app.post("/project/close")
    def close_project(_auth: None = Depends(require_token)) -> dict:
        """Close the active project (user request 2026-06-10 #2) — the app runs
        project-less (generate/assets 409) until one is created/opened, and a relaunch
        won't auto-reopen it. 409 while a job is running. Token-gated."""
        try:
            info = projects.close_project()
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        GUARD.refresh()
        return info

    @app.post("/project/estimate")
    def estimate_project(req: EstimateRequest) -> dict:
        """Footprint estimator (R161/R164): projected PNG-master size + suggested cap.
        Pure calculation — unauthenticated."""
        return ws_mod.footprint_report(length_s=req.length_s, width=req.width,
                                       height=req.height, fps=req.fps,
                                       size_cap_gb=req.size_cap_gb)

    @app.get("/jobs")
    def list_jobs() -> dict:
        st = RUNNER.state()
        return {"jobs": RUNNER.snapshot(), "counts": st["counts"],
                "paused": st["paused"], "pause_reason": st["pause_reason"],
                "vram_budget_gb": st["vram_budget_gb"],
                "disk": GUARD.status()}     # live usage + warn/hard for the dock (M6)

    @app.get("/disk")
    def disk() -> dict:
        """Live disk-guard status (two measures × two thresholds, §9). Unauthenticated read."""
        return GUARD.status()

    @app.get("/components")
    def get_components() -> dict:
        """Live launch report — phase-scoped 3-state component manifest (§11). The
        orchestrator only started if `code_ok`; `weights_ok=false` means the UI should
        offer a fetch. Unauthenticated read."""
        return components.launch_report()

    @app.post("/components/fetch")
    def fetch_components(multi_preset: str | None = None,
                         postproc: str | None = None,
                         postproc_variant: str | None = None,
                         _auth: None = Depends(require_token)) -> dict:
        """Explicit, on-demand fetch of missing weights (R163, §11.1). Token-gated; never
        an auto-download. With `?multi_preset=fast|refined` fetches that `multi` casting
        preset's (mostly gated) HF weight set; with `?postproc=birefnet[&postproc_variant=…]`
        a postproc tool's set (M3.5 — variant-scoped so asking for the default never pulls
        the HR model); otherwise the active-phase manifest weights + refreshes the report."""
        global _LAUNCH
        if multi_preset is not None:
            return components.fetch_multi_preset(multi_preset)
        if postproc is not None:
            return components.fetch_postproc(postproc, postproc_variant)
        res = components.fetch_missing_weights()
        _LAUNCH = res["report"]
        return res

    @app.post("/shutdown")
    def shutdown(_auth: None = Depends(require_token)) -> dict:
        """Graceful-shutdown handshake (P0-15): re-queue the in-flight job + mark a clean
        stop so a relaunch resumes it **paused/queued** (not failed). The Tauri shell calls
        this **before** hard-killing the sidecar on app exit, so the desktop 'quit mid-job'
        takes the R159 graceful branch. The process stays up (Tauri kills it next) — the
        durable state is already clean. Idempotent + token-gated."""
        GUARD.stop()
        RUNNER.graceful_shutdown()
        return {"stopped": True, "clean_shutdown": True}

    @app.post("/queue/pause")
    def queue_pause(_auth: None = Depends(require_token)) -> dict:
        RUNNER.pause()
        return RUNNER.state()

    @app.post("/queue/unpause")
    def queue_unpause(_auth: None = Depends(require_token)) -> dict:
        """Resume the GPU worker (the [unpause] control after a resume-paused load, R88)."""
        RUNNER.unpause()
        return RUNNER.state()

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        job = RUNNER.get(job_id)
        if job is None:
            raise HTTPException(404, f"no such job {job_id!r}")
        return job

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, _auth: None = Depends(require_token)) -> dict:
        """Cancel a queued/running job (cancel = worker-tree kill, §8/§15). Token-gated."""
        if not RUNNER.cancel(job_id):
            raise HTTPException(409, f"job {job_id!r} is unknown or already finished")
        return {"job_id": job_id, "canceling": True}

    @app.post("/jobs/{job_id}/stop")
    def stop_job(job_id: str, _auth: None = Depends(require_token)) -> dict:
        """Gracefully stop a RUNNING **batch** job: the worker finishes the current item,
        skips the rest, and every already-generated image stays valid (vs cancel = tree
        kill + partial discard). Token-gated; 409 if the job isn't a running batch."""
        if not RUNNER.stop_batch(job_id):
            raise HTTPException(409, f"job {job_id!r} is not a running batch job")
        return {"job_id": job_id, "stopping": True}

    @app.delete("/jobs/{job_id}")
    def delete_job(job_id: str, _auth: None = Depends(require_token)) -> dict:
        """Delete a **finished** generation and **all** its artifacts (output dir + sidecar
        manifest, per-job log, queue entry, lineage edge) — atomic + orchestrator-owned, so
        no orphaned files (the safe alternative to hand-deleting, R80). Cancel a
        running/queued job first → 409. Token-gated."""
        if not RUNNER.delete(job_id):
            raise HTTPException(409, f"job {job_id!r} is unknown or not finished — cancel a "
                                     "running/queued job before deleting")
        GUARD.refresh()   # usage dropped — refresh the dock meter immediately (M6)
        return {"job_id": job_id, "deleted": True}

    @app.delete("/jobs/{job_id}/output")
    def delete_job_output(job_id: str, output: str,
                          _auth: None = Depends(require_token)) -> dict:
        """Delete **one** output image of a multi-output job (a `multi`-cast candidate or a
        Stage-B batch tile) — strictly individual, leaving the rest of the pool intact (user
        2026-06-21). When `output` is the job's last/only image, the whole job is removed (same
        effect as DELETE /jobs/{id}). 409 if the job is unknown/not finished; 404 if `output`
        isn't one of its outputs. Token-gated. `output` is the out/-relative name."""
        res = RUNNER.delete_output(job_id, output)
        if res == "missing":
            job = RUNNER.get(job_id)
            if job is None or job.get("status") not in ("done", "failed", "canceled"):
                raise HTTPException(409, f"job {job_id!r} is unknown or not finished — cancel a "
                                         "running/queued job before deleting")
            raise HTTPException(404, f"{output!r} is not an output of job {job_id!r}")
        GUARD.refresh()
        return {"job_id": job_id, "output": output, "outcome": res}

    @app.get("/capabilities")
    def capabilities() -> dict:
        """Declared adapter contract — modes/params/presence (§8). Drives the UI."""
        return {"pipelines": {
            "zimage": zimage_adapter.capabilities(CONFIG.pipeline_roots),
            "multi": multi_adapter.capabilities(CONFIG.pipeline_roots),
            "sd35": sd35_adapter.capabilities(CONFIG.pipeline_roots),
            "flux2": flux2_adapter.capabilities(CONFIG.pipeline_roots),
            "krea2": krea2_adapter.capabilities(CONFIG.pipeline_roots),
            "birefnet": birefnet_adapter.capabilities(CONFIG.pipeline_roots),
            "identity": identity_adapter.capabilities(CONFIG.pipeline_roots),
            "face_restore": face_restore_adapter.capabilities(CONFIG.pipeline_roots),
            "ltxv": ltxv_adapter.capabilities(CONFIG.pipeline_roots),
            "frame_harvest": frame_harvest_adapter.capabilities(CONFIG.pipeline_roots),
        }}

    @app.get("/models")
    def models() -> dict:
        """Full model catalog (P1/M3) — every flux2/sd35/zimage variant + every adjustable
        parameter (incl. ones earlier adapters hardcoded), plus the `multi` casting tunables
        (clean/polish toggles + sub-params). Drives the UI's model picker + parameter
        controls; `/generate` validates a request's tunables against it. Unauth read."""
        return {"catalog_version": model_catalog.CATALOG_VERSION,
                "models": model_catalog.catalog_for_api()}

    @app.get("/outputs/{name:path}")
    def get_output(name: str) -> FileResponse:
        """Serve a generated PNG from the **active project's** out/ dir, incl. per-job
        subdirs (M5). Traversal-guarded."""
        if ".." in name or "\\" in name:
            raise HTTPException(400, "invalid name")
        ws = RUNNER.workspace
        if ws is None:
            raise HTTPException(409, "no project open")
        base = ws.out_dir.resolve()
        path = (base / name).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise HTTPException(404, f"no such output {name!r}")
        return FileResponse(path)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    # READY is emitted from the lifespan startup (after the socket binds) so it is
    # the sidecar handshake contract only once the service is actually listening.
    uvicorn.run(app, host=CONFIG.host, port=CONFIG.port, log_level="info")


if __name__ == "__main__":
    main()
