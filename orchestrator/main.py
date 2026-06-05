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
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
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
    from .runner import RUNNER, WORKER_REAP, ADAPTERS, estimate_vram
    from . import projects
    from . import workspace as ws_mod
    from . import components
    from .diskguard import DiskGuard
    from . import logsetup
    from . import bible
    from . import assets
except ImportError:  # pragma: no cover - direct-run convenience
    from config import CONFIG  # type: ignore
    from adapters import JobSpec  # type: ignore
    from adapters import zimage as zimage_adapter  # type: ignore
    from adapters import multi as multi_adapter  # type: ignore
    from runner import RUNNER, WORKER_REAP, ADAPTERS, estimate_vram  # type: ignore
    import projects  # type: ignore
    import workspace as ws_mod  # type: ignore
    import components  # type: ignore
    from diskguard import DiskGuard  # type: ignore
    import logsetup  # type: ignore
    import bible  # type: ignore
    import assets  # type: ignore
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

    pipeline: Literal["zimage", "multi"] = "zimage"
    mode: Literal["t2i", "ideate"] = "t2i"
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
    dry_run: bool = False           # return the argv without running the GPU job (testing)
    # P1/M1: scope a batch to an AssetProfile version (lineage stage + requester); the L1
    # style fragment auto-prepends unless apply_style is unticked (the R104 override).
    asset_id: str | None = None
    version_id: str | None = None   # default = the asset's active version
    stage: Literal["A", "B", "C"] | None = None
    # Tri-state (review): True/False = explicit per-gen override (R104); None/omitted =
    # fall back to the StoryBible's saved `enabled_default`.
    apply_style: bool | None = None

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
    """Edit the L1 style fragment (R104 fixed prepend + default-on toggle)."""

    model_config = ConfigDict(extra="forbid")
    fragment: str | None = None
    enabled_default: bool | None = None


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


class HeroRequest(BaseModel):
    """Set (or clear, `candidate_id=null`) the hero among already-recorded candidates."""

    model_config = ConfigDict(extra="forbid")
    candidate_id: str | None = None
    version_id: str | None = None


def require_token(x_loom_token: str | None = Header(default=None)) -> None:
    """Auth gate for mutating/expensive endpoints (review #1, R101 transport).

    The loopback bind already blocks off-machine callers; the token blocks *local*
    cross-site requests from spending GPU (the no-surprise-GPU posture, R141–143).
    """
    if x_loom_token != CONFIG.token:
        raise HTTPException(status_code=401, detail="missing or invalid X-Loom-Token")


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
                               "DELETE /jobs/{id}", "POST /queue/pause",
                               "POST /queue/unpause", "POST /project", "POST /project/open",
                               "POST /project/forget", "PUT /bible/style", "POST /assets",
                               "POST /assets/{id}/casting/star", "POST /assets/{id}/casting/hero",
                               "POST /components/fetch", "POST /shutdown"],
            "worker_reap": WORKER_REAP,
            "work_disk_root": str(CONFIG.work_disk_root),
            "hf_home": os.environ.get("HF_HOME") or str(CONFIG.hf_home),
            "active_project": (str(RUNNER.workspace.path) if RUNNER.workspace else None),
            "log_level": CONFIG.log_level,
            "log_file": str(CONFIG.log_dir / "orchestrator.log"),
        }

    @app.post("/generate")
    def generate(req: GenerateRequest, _auth: None = Depends(require_token)) -> dict:
        """Enqueue work onto the single-worker runner. Token-gated; needs an open project.

        `zimage` (t2i) → an N-image batch (count jobs). `multi` (ideate) → **one** casting
        job that fans out into a pool of `num_candidates × pipelines` candidates (P1/M2)."""
        # Resolve the adapter + coerce the mode to the one this pipeline wires.
        adapter = ADAPTERS.get(req.pipeline)
        if adapter is None:
            raise HTTPException(400, f"unknown pipeline {req.pipeline!r}")
        mode = "ideate" if req.pipeline == "multi" else "t2i"
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
                                       "num_candidates", "ideation_mode"})
        is_multi = req.pipeline == "multi"
        if is_multi:
            base["num_candidates"] = req.num_candidates
            base["ideation_mode"] = req.ideation_mode

        # L1 style fragment auto-prepend (R104). Per-gen `apply_style` overrides; when it's
        # omitted, honor the StoryBible's saved `enabled_default` (review: that flag was
        # stored but never consulted).
        style = bible.load_style(ws)
        apply_style = req.apply_style if req.apply_style is not None \
            else bool(style.get("enabled_default", True))
        if apply_style:
            fragment = (style.get("fragment") or "").strip()
            if fragment:
                base["prompt"] = f"{fragment}, {base['prompt']}"

        if req.dry_run:
            spec = JobSpec(pipeline=req.pipeline, mode=mode, params=base, output_dir=ws.out_dir)
            argv = adapter.build_argv(spec, CONFIG.venv_python, script)
            return {"dry_run": True, "pipeline": req.pipeline,
                    "count": 1 if is_multi else req.count,
                    "num_candidates": req.num_candidates if is_multi else None,
                    "argv": argv, "prompt": base["prompt"],
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
                                profile_version_id=profile_version_id, stage=req.stage)
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

    @app.get("/bible/style")
    def get_style() -> dict:
        """The L1 style fragment (auto-prepended to generation, R104). Unauthenticated read."""
        return bible.load_style(_require_ws())

    @app.put("/bible/style")
    def put_style(req: StyleRequest, _auth: None = Depends(require_token)) -> dict:
        """Edit the style fragment / default-on flag (writes story.json). Token-gated."""
        return bible.set_style(_require_ws(), fragment=req.fragment,
                               enabled_default=req.enabled_default)

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

    @app.get("/assets/{asset_id}")
    def get_asset(asset_id: str) -> dict:
        """Full AssetProfile + its versions, by id."""
        res = assets.get_asset(_require_ws(), asset_id)
        if res is None:
            raise HTTPException(404, f"no such asset {asset_id!r}")
        return res

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
                version_id=req.version_id, pipeline=pipeline, seed=seed, starred=req.starred)
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))
        LOG.info("casting %s: job %s -> asset %s (%s)",
                 "star" if req.starred else "unstar", req.job_id, asset_id, version["id"])
        return version

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
                "paused": st["paused"], "vram_budget_gb": st["vram_budget_gb"],
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
    def fetch_components(_auth: None = Depends(require_token)) -> dict:
        """Explicit, on-demand fetch of missing active-phase weights from the manifest
        (R163, §11.1). Token-gated; never an auto-download. Refreshes the cached report."""
        global _LAUNCH
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
        """Cancel a queued/running job (cancel = subprocess kill, §8/§15). Token-gated."""
        if not RUNNER.cancel(job_id):
            raise HTTPException(409, f"job {job_id!r} is unknown or already finished")
        return {"job_id": job_id, "canceling": True}

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

    @app.get("/capabilities")
    def capabilities() -> dict:
        """Declared adapter contract — modes/params/presence (§8). Drives the UI."""
        return {"pipelines": {"zimage": zimage_adapter.capabilities(CONFIG.pipeline_roots)}}

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
