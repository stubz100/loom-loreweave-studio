"""Project lifecycle (M5, P0-9) — create / open / resume the active project workspace.

Ties three things together:
- `workspace.Workspace` — the on-disk `<project>/` tree + atomic bundle I/O.
- `runner.RUNNER` — the durable queue, which is **bound** to the active project.
- the **app-level pointer** (`<app state>/app.json`) — records the last-opened project
  so a relaunch re-opens it (and the queue resumes *paused*, R88).

The pointer lives OUTSIDE any project (it records *which* project is active), so it is
the one piece of state that legitimately stays in the cross-project app-state dir.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

try:
    from .config import CONFIG
    from .runner import RUNNER
    from . import workspace as ws_mod
    from .workspace import Workspace
    from .logsetup import get_logger
except ImportError:  # pragma: no cover - direct-run convenience
    from config import CONFIG  # type: ignore
    from runner import RUNNER  # type: ignore
    import workspace as ws_mod  # type: ignore
    from workspace import Workspace  # type: ignore
    from logsetup import get_logger  # type: ignore

POINTER_SCHEMA_VERSION = 1
_MAX_RECENT = 20
LOG = get_logger()


def _warn(msg: str) -> None:
    LOG.warning(msg)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- app pointer ----------------------------------------------------------------

def read_pointer() -> dict:
    path = CONFIG.app_pointer_path
    if not path.is_file():
        return {"schema_version": POINTER_SCHEMA_VERSION, "active_project": None, "recent": []}
    try:
        return ws_mod.read_json(path)
    except ws_mod.WorkspaceError:
        return {"schema_version": POINTER_SCHEMA_VERSION, "active_project": None, "recent": []}


def write_pointer(active_path: Path) -> None:
    p = str(Path(active_path).resolve())
    data = read_pointer()
    recent = [r for r in data.get("recent", []) if r != p]
    recent.insert(0, p)
    ws_mod.atomic_write_json(CONFIG.app_pointer_path, {
        "schema_version": POINTER_SCHEMA_VERSION,
        "active_project": p,
        "recent": recent[:_MAX_RECENT],
    })


def list_projects() -> dict:
    """The project **registry** for the picker (app-level, machine-local — NOT in git):
    the most-recent project paths enriched from each `project.json` (name/id/cap), with a
    liveness check so a moved/deleted project shows `exists:false` (and can be forgotten).
    Ordered most-recent-first."""
    data = read_pointer()
    active = data.get("active_project")
    projects: list[dict] = []
    for p in data.get("recent", []):
        entry = {"path": p, "active": p == active, "exists": False,
                 "name": None, "id": None, "size_cap_gb": None}
        try:
            ws = Workspace(Path(p))
            if ws.project_json.is_file():
                pj = ws.load_project()
                entry.update(exists=True, name=pj["name"], id=pj["id"],
                             size_cap_gb=pj["size_cap_gb"])
        except Exception:  # noqa: BLE001 - a corrupt/odd entry just shows exists:false
            pass
        projects.append(entry)
    return {"active": active, "projects": projects}


def forget_project(path: Path) -> dict:
    """Drop a project from the registry's recent list (e.g. a moved/deleted one). Does
    NOT touch the files or unbind the active project — purely a list cleanup."""
    p = str(Path(path).resolve())
    data = read_pointer()
    ws_mod.atomic_write_json(CONFIG.app_pointer_path, {
        "schema_version": POINTER_SCHEMA_VERSION,
        "active_project": data.get("active_project"),
        "recent": [r for r in data.get("recent", []) if r != p],
    })
    return list_projects()


# --- lifecycle ------------------------------------------------------------------

def _bind(ws: Workspace) -> dict:
    """Bind the runner to `ws`, record the pointer, return the project info."""
    RUNNER.bind(ws)
    write_pointer(ws.path)
    info = ws.info()
    LOG.info("project open: %s (%s) at %s", info.get("name"), info.get("id"), ws.path)
    return info


def create_project(dest: Path, *, name: str, fmt: dict | None = None,
                   size_cap_gb: float = ws_mod.DEFAULT_SIZE_CAP_GB) -> dict:
    """`loom init`: validate + create the workspace, bind the (empty) queue, open it.

    Refuses while a job is running (we'd strand the live worker on the old project)."""
    if RUNNER.has_running():
        raise ws_mod.WorkspaceError("a job is running — pause/cancel it before switching project")
    ws = Workspace.create(Path(dest), name=name, fmt=fmt, size_cap_gb=size_cap_gb)
    return _bind(ws)


def open_project(path: Path) -> dict:
    """Open an existing project and resume its queue (paused, R88)."""
    if RUNNER.has_running():
        raise ws_mod.WorkspaceError("a job is running — pause/cancel it before switching project")
    ws = Workspace.open(Path(path))
    return _bind(ws)


def active_info() -> dict:
    ws = RUNNER.workspace
    return ws.info() if ws is not None else {"open": False}


def resolve_startup() -> None:
    """Called from the lifespan startup: pick the project to open, if any.

    Order: an explicit `LOOM_PROJECT_DIR` override (open, or create-with-defaults for
    tests/CI/GPU-verify) → else the last-opened project from the pointer → else nothing
    (the app opens with no project; `/generate` 409s until one is created/opened). Any
    failure is non-fatal: the orchestrator still serves, just project-less."""
    override = CONFIG.project_dir_override
    if override is not None:
        try:
            if (override / "project.json").is_file():
                open_project(override)
            else:
                # Dev/test affordance: provision at the floor cap (R79) so the override
                # works on any disk with ≥50 GB free, not the production 250 GB default.
                create_project(override, name=override.name or "loom-project",
                               size_cap_gb=ws_mod.MIN_SIZE_CAP_GB)
            return
        except Exception as e:  # noqa: BLE001 - never block boot on a bad override
            _warn(f"LOOM_PROJECT_DIR={override} could not be opened/created: {e}")
            return

    last = read_pointer().get("active_project")
    if not last:
        return
    try:
        open_project(Path(last))
    except Exception as e:  # noqa: BLE001 - a moved/deleted last project shouldn't block boot
        _warn(f"last project {last} could not be re-opened: {e}")
