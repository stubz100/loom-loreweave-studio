"""M0c (P2) — PROJECT-LEVEL postprocess stacks.

User decision 2026-06-18: postprocess **any** image, regardless of origin (the unscoped
Sandbox or any character, any pipeline). So a stack is keyed by its **base image** (an
out/-relative output name, globally unique within a project) and persisted at
`<project>/postproc_stacks.json` — NOT on a character version. Consequences (intended):
it is a project-wide image scratchpad, so a stack is **not** part of profile export and is
**not** frozen by a version finalize-lock; if you want a postprocessed image to belong to a
character, keep it into the ref_set via Stage-C curation (which still enforces the lock).

A stack is a linear chain: a step's `source` is the previous step's `output` (or the base);
steps append/remove at the tail. This module only persists the record — `main.py` resolves
presets, validates, builds + submits the job, and (via the completion observer) records the
produced output by job id.
"""

from __future__ import annotations

from datetime import datetime, timezone

try:
    from . import workspace as ws_mod
    from . import logsetup
    from .workspace import Workspace, new_id
except ImportError:  # pragma: no cover - direct-run convenience
    import workspace as ws_mod  # type: ignore
    import logsetup  # type: ignore
    from workspace import Workspace, new_id  # type: ignore

LOG = logsetup.get_logger()

_STORE = "postproc_stacks.json"
_SCHEMA = "postproc_store.schema.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(ws: Workspace):
    return ws.path / _STORE


def _load(ws: Workspace) -> dict:
    """The project's stack store ({"stacks": [...]}). A corrupt/invalid file is reset to
    empty (logged) rather than crashing — postproc is a scratchpad, never load-bearing."""
    p = _path(ws)
    if not p.is_file():
        return {"stacks": []}
    try:
        data = ws_mod.read_json(p)
        ws_mod.validate(data, _SCHEMA)
        return data
    except ws_mod.WorkspaceError as e:
        LOG.warning("postproc store %s invalid (%s) — starting empty", p, e)
        return {"stacks": []}


def _save(ws: Workspace, store: dict) -> dict:
    ws_mod.validate(store, _SCHEMA)
    ws_mod.atomic_write_json(_path(ws), store)
    return store


def _find_stack(store: dict, base: str) -> dict | None:
    return next((s for s in store["stacks"] if s["base"] == base), None)


def _find_step(store: dict, step_id: str):
    """`(stack, step)` for a step id, or `(None, None)`."""
    for stack in store["stacks"]:
        for step in stack["steps"]:
            if step["id"] == step_id:
                return stack, step
    return None, None


def list_stacks(ws: Workspace) -> list[dict]:
    return _load(ws)["stacks"]


def add_step(ws: Workspace, *, base: str, preset: str, backend: str, mode: str,
             params: dict, mask: str | None = None, requires_mask: bool = False) -> dict:
    """Append a CONFIGURED step to `base`'s stack (source = the prior step's output, or
    `base` when empty). Persisted, NOT queued. Refuses to stack onto a step that hasn't
    produced an output yet, so a step's source is always a real image. Returns the store."""
    store = _load(ws)
    stack = _find_stack(store, base)
    if stack is None:
        stack = {"base": base, "steps": []}
        store["stacks"].append(stack)
    steps = stack["steps"]
    if steps:
        if not steps[-1].get("output"):
            raise ws_mod.WorkspaceError(
                "queue and finish the previous step before adding another")
        source = steps[-1]["output"]
    else:
        source = base
    steps.append({
        "id": new_id("pps"), "preset": preset, "backend": backend, "mode": mode,
        "params": params, "mask": mask, "requires_mask": requires_mask,
        "source": source, "output": None, "job_id": None,
        "status": "configured", "added_at": _now(),
    })
    return _save(ws, store)


def remove_step(ws: Workspace, *, step_id: str) -> dict:
    """Remove the LAST step of its stack (a chain — removing a middle step would orphan the
    sources below it) and prune an emptied stack. Returns the store."""
    store = _load(ws)
    stack, step = _find_step(store, step_id)
    if step is None:
        raise ws_mod.WorkspaceError(f"unknown postproc step {step_id!r}")
    if stack["steps"][-1]["id"] != step_id:
        raise ws_mod.WorkspaceError("only the last step of a stack can be removed")
    stack["steps"].pop()
    if not stack["steps"]:
        store["stacks"].remove(stack)
    return _save(ws, store)


def resolve_step(ws: Workspace, step_id: str) -> dict:
    """The step record for a step id; raises on unknown."""
    _stack, step = _find_step(_load(ws), step_id)
    if step is None:
        raise ws_mod.WorkspaceError(f"unknown postproc step {step_id!r}")
    return step


def mark_queued(ws: Workspace, *, step_id: str, job_id: str) -> dict:
    """Stamp a step queued + link the firing job (the observer matches on this job_id)."""
    store = _load(ws)
    _stack, step = _find_step(store, step_id)
    if step is None:
        raise ws_mod.WorkspaceError(f"unknown postproc step {step_id!r}")
    step["status"] = "queued"
    step["job_id"] = job_id
    step["output"] = None
    return _save(ws, store)


def reconcile(ws: Workspace, resolve) -> list[dict]:
    """Sync queued/running steps with live job state before returning the stacks — the
    completion observer only fires for SUCCESSFUL jobs, so a step whose job failed, was
    canceled, or was deleted from the queue would otherwise stay stuck 'queued' (blocking
    the stack). `resolve(job_id)` returns `(status, output)` for the job, or **None** if it's
    gone (deleted/pruned → the step is treated as canceled). Persists corrections so the
    state survives a reload; returns the stacks. Caller (main.py) owns the runner glue."""
    store = _load(ws)
    changed = False
    for stack in store["stacks"]:
        for st in stack["steps"]:
            if st.get("status") not in ("queued", "running") or not st.get("job_id"):
                continue
            info = resolve(st["job_id"])
            if info is None:                                  # job gone (deleted/pruned)
                st["status"] = "canceled"
                changed = True
                continue
            status, output = info
            if status in ("done", "failed", "canceled") and status != st["status"]:
                st["status"] = status
                if status == "done" and output:
                    st["output"] = output
                changed = True
            elif status == "running" and st["status"] != "running":
                st["status"] = "running"
                changed = True
    if changed:
        _save(ws, store)
    return store["stacks"]


def record_result(ws: Workspace, job_id: str, *, output: str | None, ok: bool) -> bool:
    """Completion-observer side: find the step whose `job_id` matches the finished job and
    record its produced `output` + final status. Best-effort; True when a step was updated.
    A no-op for non-postproc jobs (no step matches)."""
    store = _load(ws)
    for stack in store["stacks"]:
        for step in stack["steps"]:
            if step.get("job_id") == job_id and step.get("status") in ("queued", "running"):
                step["status"] = "done" if ok else "failed"
                if ok and output:
                    step["output"] = output
                _save(ws, store)
                return True
    return False
