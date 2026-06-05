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

Active phases default to `{"P0"}` (override `LOOM_ACTIVE_PHASES`, comma-separated).
"""

from __future__ import annotations

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
    return {"P0"}


def CONFIG_active_phases_env() -> str | None:  # tiny indirection so tests can monkeypatch
    return os.environ.get("LOOM_ACTIVE_PHASES")


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


_CODE_CHECKS = {
    "zimage": ("P0", _check_zimage),
    "queue": ("P0", _check_queue),
    "workspace_io": ("P0", _check_workspace_io),
}


# --- model-weight manifest ------------------------------------------------------

def _load_models_manifest() -> dict:
    path = CONFIG.app_repo_root / "models.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"models": []}


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
    comps: list[Component] = []
    for e in _load_models_manifest().get("models", []):
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
    weights_missing = [c for c in comps
                       if c.kind == "model_weight" and c.phase in active and not c.present]
    code_ok = not blocking
    return {
        "active_phases": sorted(active),
        "code_ok": code_ok,                       # False -> orchestrator refuses to start
        "weights_ok": not weights_missing,        # False -> UI offers fetch; /generate gated
        "launch_ok": code_ok and not weights_missing,
        "blocking": [{"id": c.id, "detail": c.detail} for c in blocking],
        "weights_missing": [c.id for c in weights_missing],
        "components": [{"id": c.id, "kind": c.kind, "phase": c.phase,
                        "present": c.present, "state": c.state(active), "detail": c.detail}
                       for c in comps],
    }


def weights_ok() -> tuple[bool, list[str]]:
    """Light check (weights only, no code roundtrip) for the `/generate` precondition:
    are all active-phase weights present? Returns (ok, missing_ids)."""
    active = active_phases()
    missing = [e.get("id", "?") for e in _load_models_manifest().get("models", [])
               if str(e.get("phase", "")).upper() in active and not _weight_present(e)[0]]
    return (not missing, missing)


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

def fetch_missing_weights() -> dict:
    """Fetch the active-phase weights that are currently missing, from the manifest
    (hf_diffusers → `snapshot_download`; file → `hf_hub_download` against the companion
    repo). Explicit + on-demand (never auto at startup). Checksum verify is TODO until
    the companion repo publishes sha256s; presence is re-checked after."""
    active = active_phases()
    manifest = _load_models_manifest()
    companion = manifest.get("companion_repo", "")
    results: list[dict] = []
    for e in manifest.get("models", []):
        if str(e.get("phase", "")).upper() not in active:
            continue
        present, _ = _weight_present(e)
        if present:
            continue
        try:
            if e["type"] == "hf_diffusers":
                from huggingface_hub import snapshot_download
                snapshot_download(e["repo_id"])
            elif e["type"] == "file":
                from huggingface_hub import hf_hub_download
                repo = companion.split("huggingface.co/")[-1].rstrip("/")
                hf_hub_download(repo_id=repo, filename=e["filename"])
            else:
                raise RuntimeError(f"unknown weight type {e.get('type')!r}")
            ok, detail = _weight_present(e)
            results.append({"id": e.get("id"), "fetched": ok, "detail": detail})
        except Exception as ex:  # noqa: BLE001 - report, don't crash the endpoint
            results.append({"id": e.get("id"), "fetched": False, "error": str(ex)})
    return {"results": results, "report": launch_report()}
