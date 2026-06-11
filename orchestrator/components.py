"""Component manifest + launch gate (M7, P0-11 / §11, R91/R97/R163).

The launch gate hard-requires only what's essential to the phases **actually built**
(for P0: `zimage` + the queue + workspace I/O). It is **presence-only** (not version —
R97) and **phase-scoped** via a three-state model (§11):

- **phase-essential** — its phase is active *now*, so a missing one is launch-blocking;
- **installed-but-unavailable** — present, but its phase isn't active yet (reported, not
  blocking) — this is what stops P0 from demanding P3/P6 components like `trellis2`;
- **missing** — only blocking if it's phase-essential now.

Two kinds of component:

- **code** — a missing phase-essential code component → **clear error, refuse to start**
  (code can't be auto-fetched).
- **model_weight** (from `models.json`) — a missing phase-essential weight does **not**
  hard-refuse at startup; instead the gate reports it so the UI can **offer an explicit
  on-demand HF fetch** (R163, §11.1, no-surprise posture). Launch then fails fast — same
  refuse-to-start outcome — only if the fetch is unavailable/declined/fails checksum.

Active phases default to `{"P0", "P1"}` — both are runnable now (P0 spine + P1's L1/L2
record layer ships in the UI), so a broken P1 schema *should* block startup, not just be
reported (review). Override via `LOOM_ACTIVE_PHASES` (comma-separated). This pulls in only
the **code** components for the active phases; P1 declares no phase-scoped *weight* in
`models.json`, so the phase gate (`weights_ok`) stays zimage-only. `multi`'s casting
weights (M2) are **preset-scoped, not phase-scoped** — checked per selected ideation
preset at `/generate` (`multi_weights_status`, from the `multi_presets` block), so they
don't over-gate startup; see the preset-aware section below.
"""

from __future__ import annotations

import functools
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    from .config import CONFIG
    from .adapters import zimage as zimage_adapter
    from . import workspace as ws_mod
except ImportError:  # pragma: no cover - direct-run convenience
    from config import CONFIG  # type: ignore
    from adapters import zimage as zimage_adapter  # type: ignore
    import workspace as ws_mod  # type: ignore

_ALL_SCHEMAS = ("project.schema.json", "job.schema.json", "queue.schema.json",
                "manifest.schema.json", "lineage.schema.json")


class LaunchError(RuntimeError):
    """A phase-essential **code** component is missing — refuse to start (§11)."""


def active_phases() -> set[str]:
    env = CONFIG_active_phases_env()
    if env:
        return {p.strip().upper() for p in env.split(",") if p.strip()}
    return {"P0", "P1"}   # both runnable now (review); P1 has code components, no weight yet


def CONFIG_active_phases_env() -> str | None:  # tiny indirection so tests can monkeypatch
    # Route through the central config loader (real env > .env.local > .env) so the
    # committed `.env` is honored like every other setting (review), not just the
    # process environment.
    return CONFIG.active_phases_raw


@dataclass
class Component:
    id: str
    kind: str            # "code" | "model_weight"
    phase: str           # "P0".."P6"
    present: bool
    detail: str

    def state(self, active: set[str]) -> str:
        """Three-state model (§11), as a function of (present, phase active)."""
        phase_active = self.phase in active
        if not self.present:
            return "missing" if phase_active else "declared"   # declared = future, not fetched
        return "phase-essential" if phase_active else "installed-but-unavailable"

    def blocking(self, active: set[str]) -> bool:
        """A missing **code** component whose phase is active blocks launch. A missing
        **weight** is handled by the fetch flow (reported, not a hard code-block)."""
        return self.kind == "code" and not self.present and self.phase in active


# --- presence checks ------------------------------------------------------------

def _check_zimage() -> tuple[bool, str]:
    script = zimage_adapter.resolve_script(CONFIG.pipeline_roots)
    if script is None:
        return False, ("zimage worker not found in any pipeline root "
                       f"({[str(r) for r in CONFIG.pipeline_roots]})")
    return True, f"worker at {script}"


def _check_queue() -> tuple[bool, str]:
    """Durable queue I/O: the runner module + the queue/job schemas it relies on."""
    try:
        try:
            from . import runner  # noqa: F401
        except ImportError:
            import runner  # type: ignore  # noqa: F401
        for s in ("queue.schema.json", "job.schema.json"):
            ws_mod.load_schema(s)
        return True, "runner + queue/job schemas present"
    except Exception as e:  # noqa: BLE001
        return False, f"queue subsystem unavailable: {e}"


def _check_workspace_io() -> tuple[bool, str]:
    """Bundle I/O: every schema loads + an atomic write→read→validate roundtrip works."""
    try:
        for s in _ALL_SCHEMAS:
            ws_mod.load_schema(s)
        with tempfile.TemporaryDirectory() as d:
            probe = Path(d) / "probe.json"
            payload = {"schema_version": 1, "id": "prj_000000", "name": "probe",
                       "created_at": "t", "workspace_path": d,
                       "format": ws_mod.DEFAULT_FORMAT, "size_cap_gb": 50}
            ws_mod.atomic_write_json(probe, payload)
            ws_mod.validate_project(ws_mod.read_json(probe))
        return True, "schemas load + atomic roundtrip OK"
    except Exception as e:  # noqa: BLE001
        return False, f"workspace I/O broken: {e}"


_P1_SCHEMAS = ("story.schema.json", "profile.schema.json", "version.schema.json",
               "coverage_cell.schema.json")


def _check_p1_records() -> tuple[bool, str]:
    """P1 L1/L2 record support (StoryBible style + AssetProfile + ProfileVersion): the new
    schemas load and validate a sample of each record. **Phase-scoped to P1** — reported
    but not launch-blocking while only P0 is active, and blocking once P1 is activated
    (review: the gate must cover P1's new schema dependencies, not silently start with
    broken record support and fail later at /assets or /bible/style)."""
    try:
        for s in _P1_SCHEMAS:
            ws_mod.load_schema(s)
        ws_mod.validate({"schema_version": 1, "id": "sto_000000",
                         "style": {"id": "sty_000000", "fragment": "x",
                                   "enabled_default": True}}, "story.schema.json")
        ws_mod.validate({"schema_version": 1, "id": "ast_000000", "name": "probe",
                         "asset_class": "characters", "created_at": "t",
                         "active_version": "ver_000000", "versions": ["ver_000000"]},
                        "profile.schema.json")
        ws_mod.validate({"schema_version": 1, "id": "ver_000000", "name": "v1_base",
                         "finalized": False, "saved_at": "t", "prompt_template": "",
                         "ref_set": [], "casting": []}, "version.schema.json")
        ws_mod.validate({"shot_size": "waist_up", "angle": "profile_left",
                         "expression": "neutral", "background": "market"},
                        "coverage_cell.schema.json")
        return True, "P1 record schemas load + validate"
    except Exception as e:  # noqa: BLE001
        return False, f"P1 record support broken: {e}"


# --- model-weight manifest ------------------------------------------------------

class ManifestError(RuntimeError):
    """`models.json` is missing or malformed — the weight contract can't be read."""


def _load_models_manifest() -> dict:
    """Load + structurally validate `models.json`. **Raises `ManifestError`** on a
    missing/unparseable/shapeless manifest — a broken weight contract must NOT silently
    degrade to 'no weights required' (review: that masked a real launch-gate failure).
    A valid manifest with an empty `models` list is fine (genuinely no weights)."""
    path = CONFIG.app_repo_root / "models.json"
    if not path.is_file():
        raise ManifestError(f"models.json missing at {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ManifestError(f"models.json unreadable/malformed: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        raise ManifestError("models.json must be an object with a 'models' list")
    return data


def manifest_status() -> tuple[bool, str]:
    """Presence check for the manifest itself (a P0-essential code component)."""
    try:
        data = _load_models_manifest()
        return True, f"{len(data['models'])} entries"
    except ManifestError as e:
        return False, str(e)


_CODE_CHECKS = {
    "zimage": ("P0", _check_zimage),
    "queue": ("P0", _check_queue),
    "workspace_io": ("P0", _check_workspace_io),
    # The weight contract is itself P0-essential: a broken models.json → refuse to start.
    "models_manifest": ("P0", manifest_status),
    # P1 record support — phase-scoped (reported under P0, blocking once P1 is active).
    "p1_records": ("P1", _check_p1_records),
}


def _weight_present(entry: dict) -> tuple[bool, str]:
    kind = entry.get("type")
    if kind == "hf_diffusers":
        try:
            from huggingface_hub import try_to_load_from_cache
            hit = try_to_load_from_cache(entry["repo_id"], "model_index.json")
            if isinstance(hit, str) and os.path.exists(hit):
                return True, f"cached: {entry['repo_id']}"
            return False, f"not in HF cache: {entry['repo_id']}"
        except Exception as e:  # noqa: BLE001
            return False, f"cache probe failed: {e}"
    if kind == "file":
        # `target` is a path relative to the monorepo root (e.g. src/village_ai/models/…);
        # accept an absolute target too.
        target = entry.get("target", "")
        if os.path.isabs(target) and Path(target).is_file():
            return True, f"present: {target}"
        for base in (CONFIG.monorepo_root, CONFIG.app_repo_root):
            if (base / target).is_file():
                return True, f"present: {base / target}"
        return False, f"missing file: {target}"
    return False, f"unknown weight type: {kind}"


def _weight_components() -> list[Component]:
    try:
        models = _load_models_manifest()["models"]
    except ManifestError:
        return []   # the `models_manifest` code component carries the failure (→ code_ok False)
    comps: list[Component] = []
    for e in models:
        present, detail = _weight_present(e)
        comps.append(Component(id=e.get("id", "?"), kind="model_weight",
                               phase=str(e.get("phase", "P?")).upper(),
                               present=present, detail=detail))
    return comps


# --- report + gate --------------------------------------------------------------

def components() -> list[Component]:
    comps = [Component(id=cid, kind="code", phase=phase, present=ok, detail=detail)
             for cid, (phase, check) in _CODE_CHECKS.items()
             for ok, detail in [check()]]
    comps += _weight_components()
    return comps


def launch_report() -> dict:
    active = active_phases()
    comps = components()
    blocking = [c for c in comps if c.blocking(active)]   # missing P0-essential CODE
    w_ok, w_missing = weights_ok()                        # also False if the manifest is broken
    code_ok = not blocking
    return {
        "active_phases": sorted(active),
        "code_ok": code_ok,                       # False -> orchestrator refuses to start
        "weights_ok": w_ok,                       # False -> UI offers fetch; /generate gated
        "launch_ok": code_ok and w_ok,
        "blocking": [{"id": c.id, "detail": c.detail} for c in blocking],
        "weights_missing": w_missing,
        "components": [{"id": c.id, "kind": c.kind, "phase": c.phase,
                        "present": c.present, "state": c.state(active), "detail": c.detail}
                       for c in comps],
    }


def weights_ok() -> tuple[bool, list[str]]:
    """Light check (weights only, no code roundtrip) for the `/generate` precondition:
    are all active-phase weights present? Returns (ok, missing_ids). A **broken manifest**
    is reported as not-ok (never silently 'no weights required')."""
    try:
        models = _load_models_manifest()["models"]
    except ManifestError as e:
        return (False, [f"models.json: {e}"])
    active = active_phases()
    missing = [e.get("id", "?") for e in models
               if str(e.get("phase", "")).upper() in active and not _weight_present(e)[0]]
    return (not missing, missing)


# --- preset-aware multi weights (NOT phase-scoped) ------------------------------
# The `multi` casting pipeline pulls a different trio of (mostly HF-gated) checkpoints
# per ideation preset (`fast`|`refined`; see pipeline/multi IDEATION_PRESETS). These
# are intentionally kept OUT of the phase-scoped `models` list / `weights_ok()` —
# folding them in would force every preset's gated weights onto every rig at launch
# (over-gating). Instead `/generate` checks just the SELECTED preset at cast time and
# fails fast (412) rather than dying inside the GPU subprocess (review follow-up).

def _hf_cache_probe(repo_id: str, probe: str) -> bool:
    """Best-effort presence: is `probe` (a representative file) cached for `repo_id`?
    A cache hit means the repo was at least fetched; not a deep integrity check."""
    if not repo_id:
        return False
    try:
        from huggingface_hub import try_to_load_from_cache
        hit = try_to_load_from_cache(repo_id, probe or "config.json")
        return isinstance(hit, str) and os.path.exists(hit)
    except Exception:  # noqa: BLE001
        return False


def image_model_present(repo_id: str) -> bool:
    """Is a diffusers image-model variant (a sd35/zimage catalog model) cached? Probes
    `model_index.json` (the standalone Stage-B weight check, keyed to the chosen model —
    closes the gap that the multi-only preset gate left for standalone zimage/sd35)."""
    return _hf_cache_probe(repo_id, "model_index.json")


def multi_preset_weights(preset: str) -> list[dict]:
    """The HF weight entries the `multi` pipeline needs for an ideation preset, read
    from models.json's `multi_presets` block (separate from the phase `models` list).
    Unknown preset → []."""
    try:
        data = _load_models_manifest()
    except ManifestError:
        return []
    presets = data.get("multi_presets") or {}
    return [e for e in (presets.get(preset) or []) if isinstance(e, dict)]


@functools.lru_cache(maxsize=1)
def _needs_fp8_workaround() -> bool:
    """Faithful mirror of flux2/stage1_load_models._needs_fp8_workaround: FP8 Qwen3 text
    encoders need a DTensor path unavailable on **Windows ROCm**, so the flux2 loader
    falls back to the non-FP8 Qwen3 repo there (the RX 9070 XT target). We replicate the
    same test so the gate checks the EXACT repo that will load. Cached + torch-import is
    lazy (the gate doesn't otherwise need torch); any import failure ⇒ assume non-ROCm."""
    if os.name != "nt":
        return False
    try:
        import torch
        return getattr(torch.version, "hip", None) is not None
    except Exception:  # noqa: BLE001
        return False


def _entry_resolve_repo(e: dict) -> str:
    """The EXACT repo this entry needs on this platform. For the flux2 Klein text encoder
    that's the non-FP8 `repo_id` on Windows ROCm and the `fp8_repo_id` elsewhere — mirroring
    flux2's `_load_text_encoder_safe`, so the gate + fetch target what actually loads."""
    fp8 = e.get("fp8_repo_id")
    if fp8 and not _needs_fp8_workaround():
        return fp8
    return e.get("repo_id", "")


def _entry_present(e: dict) -> bool:
    """Is the platform-resolved repo for this entry cached?"""
    return _hf_cache_probe(_entry_resolve_repo(e), e.get("probe", "config.json"))


def multi_weights_status(preset: str) -> tuple[bool, list[dict]]:
    """(ok, missing[]) for a `multi` ideation preset. Each missing entry carries
    id/repo_id/gated/pipeline so the API/UI can tell the user exactly what to accept
    (the gated repos) + fetch — `repo_id` is the **platform-resolved** repo (the exact one
    that will load), so a ROCm rig is told about the non-FP8 Qwen3 it actually needs. Best-
    effort cache probe — covers the gated checkpoints that make a cast die without HF access
    (the M2 failure mode). **Fails closed**: a preset with no configured weight set (unknown,
    or dropped from models.json) returns not-ok so the 412 pre-flight can't be silently
    disabled by a manifest edit."""
    entries = multi_preset_weights(preset)
    if not entries:
        return (False, [{"id": None, "repo_id": None, "gated": False, "pipeline": None,
                         "error": f"no weight set configured for multi preset {preset!r}"}])
    missing = [
        {"id": e.get("id"), "repo_id": _entry_resolve_repo(e),
         "gated": bool(e.get("gated")), "pipeline": e.get("pipeline")}
        for e in entries if not _entry_present(e)
    ]
    return (not missing, missing)


def fetch_multi_preset(preset: str) -> dict:
    """Explicit, on-demand fetch of a `multi` ideation preset's HF weights
    (snapshot_download per repo, with the configured HF_TOKEN for the gated ones).
    Mirrors fetch_missing_weights but for the preset set. NOTE: gated repos still
    require the user to have accepted the license on huggingface.co first — a 401/403
    surfaces here as a per-repo error (a token alone can't bypass license acceptance)."""
    entries = multi_preset_weights(preset)
    if not entries:
        return {"preset": preset, "results": [], "error": f"unknown preset {preset!r}"}
    token = CONFIG.hf_token
    results: list[dict] = []
    for e in entries:
        repo_id = _entry_resolve_repo(e)   # the exact repo this platform loads
        if _entry_present(e):
            results.append({"id": e.get("id"), "repo_id": repo_id,
                            "fetched": True, "detail": "already cached"})
            continue
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id, token=token)
            results.append({"id": e.get("id"), "repo_id": repo_id,
                            "fetched": _entry_present(e),
                            "gated": bool(e.get("gated"))})
        except Exception as ex:  # noqa: BLE001 - report per-repo, don't crash the endpoint
            results.append({"id": e.get("id"), "repo_id": repo_id, "fetched": False,
                            "gated": bool(e.get("gated")), "error": str(ex)})
    ok, missing = multi_weights_status(preset)
    return {"preset": preset, "results": results, "ok": ok, "missing": missing}


def gate() -> dict:
    """Run the launch gate. Raise `LaunchError` if a phase-essential **code** component
    is missing (refuse to start). A missing phase-essential **weight** does NOT raise —
    it's returned in the report for the UI to offer an explicit fetch (R163)."""
    report = launch_report()
    if not report["code_ok"]:
        lines = "; ".join(f"{b['id']} ({b['detail']})" for b in report["blocking"])
        raise LaunchError(
            f"refusing to start — missing P0-essential code component(s): {lines}")
    return report


# --- explicit, on-demand fetch (R163, §11.1) ------------------------------------

def _resolve_target(target: str) -> Path:
    """Resolve a manifest `target` (relative to the monorepo root, or absolute)."""
    return Path(target) if os.path.isabs(target) else (CONFIG.monorepo_root / target)


def fetch_missing_weights() -> dict:
    """Fetch the active-phase weights that are currently missing, from the manifest
    (hf_diffusers → `snapshot_download` into the hub cache; file → `hf_hub_download`
    **into the declared `target` dir** so the file lands where `_weight_present` looks —
    review: the cache-only download didn't satisfy its own presence check). Explicit +
    on-demand (never auto at startup). Checksum verify is TODO until the companion repo
    publishes sha256s; presence is re-checked after each fetch."""
    try:
        manifest = _load_models_manifest()
    except ManifestError as e:
        return {"results": [], "error": str(e), "report": launch_report()}
    active = active_phases()
    companion = manifest.get("companion_repo", "")
    results: list[dict] = []
    for e in manifest["models"]:
        if str(e.get("phase", "")).upper() not in active:
            continue
        if _weight_present(e)[0]:
            continue
        try:
            if e["type"] == "hf_diffusers":
                from huggingface_hub import snapshot_download
                snapshot_download(e["repo_id"])
            elif e["type"] == "file":
                from huggingface_hub import hf_hub_download
                repo = companion.split("huggingface.co/")[-1].rstrip("/")
                dest = _resolve_target(e["target"])
                dest.parent.mkdir(parents=True, exist_ok=True)
                # local_dir places the file at <dest.parent>/<filename> == target, so the
                # presence check (which looks at `target`) is satisfied after the fetch.
                hf_hub_download(repo_id=repo, filename=e["filename"],
                                local_dir=str(dest.parent))
            else:
                raise RuntimeError(f"unknown weight type {e.get('type')!r}")
            ok, detail = _weight_present(e)
            results.append({"id": e.get("id"), "fetched": ok, "detail": detail})
        except Exception as ex:  # noqa: BLE001 - report, don't crash the endpoint
            results.append({"id": e.get("id"), "fetched": False, "error": str(ex)})
    return {"results": results, "report": launch_report()}
