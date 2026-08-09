"""M0c (P2) — PROJECT-LEVEL postprocess stacks.

User decision 2026-06-18: postprocess **any** image, regardless of origin (the unscoped
Sandbox or any character, any pipeline). So a stack is keyed by its **base image** (an
out/-relative output name, globally unique within a project) and persisted at
`<project>/postproc_stacks.json` — NOT on a character version. Consequences (intended):
it is a project-wide image scratchpad, so a stack is **not** part of profile export and is
**not** frozen by a version finalize-lock; if you want a postprocessed image to belong to a
character, keep it into the ref_set via Stage-C curation (which still enforces the lock).

A stack is a **tree** (author 2026-08-08): a step's `source` is the base image or the output of
any FINISHED step in the same stack, so one base can carry several independent lines — two
strengths, a clean *and* a restore. It was a linear chain until then, and `add_step` still
continues the newest line when no `source` is given. Removal is leaf-based (nothing may be
orphaned) and takes the step's image with it (author 2026-08-09: a step and its output are one
thing, in both directions) — `main.py` owns that, since deleting the job is the runner's job.

This module only persists the record — `main.py` resolves presets, validates, builds + submits
the job, and (via the completion observer) records the produced output by job id. `reconcile`
is authoritative on every read: it heals steps whose job failed, was canceled, or was deleted.
"""

from __future__ import annotations

import functools
import threading
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

# M2.8 #3: the store is load-modify-save and has TWO writer threads — the API threadpool
# (add/remove/mark/reconcile) and the runner's completion-observer thread (record_result) —
# so every mutation holds this lock to prevent a lost update. Reads (list/resolve) stay
# lock-free: the atomic file write guarantees they always see a consistent snapshot.
_STORE_LOCK = threading.Lock()


def _mutates_store(fn):
    """Serialize a load→modify→save mutation of the project stack store."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _STORE_LOCK:
            return fn(*args, **kwargs)
    return wrapper


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


def stack_sources(ws: Workspace, base: str) -> list[dict]:
    """Every image in `base`'s stack that a NEW step may branch from: the base itself plus
    each finished step's output. Author 2026-08-08 — the picker reads this so a branch point
    is chosen, not guessed."""
    stack = _find_stack(_load(ws), base)
    out = [{"output": base, "from": None, "preset": None, "step_id": None}]
    for st in (stack or {}).get("steps", []):
        if st.get("output"):
            out.append({"output": st["output"], "from": st.get("source"),
                        "preset": st.get("preset"), "step_id": st["id"]})
    return out


@_mutates_store
def add_step(ws: Workspace, *, base: str, preset: str, backend: str, mode: str,
             params: dict, mask: str | None = None, requires_mask: bool = False,
             source: str | None = None) -> dict:
    """Add a CONFIGURED step to `base`'s stack. Persisted, NOT queued.

    **A stack is a TREE, not a chain (author 2026-08-08).** It used to append strictly onto
    the previous step's output, so one base image could only ever have ONE line of
    postprocessing — you could not try two strengths, or a clean *and* a restore, from the
    same source without destroying the first. `source` now names the branch point: the base
    image, or the output of any FINISHED step in this stack. Omitted, it keeps the old
    behaviour (continue from the newest finished output, else the base), so existing callers
    and the "stack another pass on what I'm looking at" flow are unchanged.

    A step's source is always a real image on disk — an unfinished step has no output, so it
    cannot be branched from yet."""
    store = _load(ws)
    stack = _find_stack(store, base)
    if stack is None:
        stack = {"base": base, "steps": []}
        store["stacks"].append(stack)
    steps = stack["steps"]
    if source:
        allowed = {base} | {s["output"] for s in steps if s.get("output")}
        if source not in allowed:
            raise ws_mod.WorkspaceError(
                f"cannot branch from {source!r} — it is not this stack's base or a finished "
                "step's output")
    elif steps:
        finished = [s for s in steps if s.get("output")]
        if not finished:
            raise ws_mod.WorkspaceError(
                "queue and finish the previous step before adding another")
        source = finished[-1]["output"]
    else:
        source = base
    steps.append({
        "id": new_id("pps"), "preset": preset, "backend": backend, "mode": mode,
        "params": params, "mask": mask, "requires_mask": requires_mask,
        "source": source, "output": None, "job_id": None,
        "status": "configured", "added_at": _now(),
    })
    return _save(ws, store)


@_mutates_store
def remove_step(ws: Workspace, *, step_id: str) -> dict:
    """Remove a LEAF step — one nothing else branches from — and prune an emptied stack.

    Was "only the last step", which is the same rule while a stack is a straight chain. Now
    that it branches (see `add_step`), "last" is meaningless: what matters is that removing a
    step must never orphan the ones sourced from it. Returns the store."""
    store = _load(ws)
    stack, step = _find_step(store, step_id)
    if step is None:
        raise ws_mod.WorkspaceError(f"unknown postproc step {step_id!r}")
    out = step.get("output")
    if out and any(s.get("source") == out for s in stack["steps"]):
        raise ws_mod.WorkspaceError(
            "another step branches from this one — remove those first")
    stack["steps"] = [s for s in stack["steps"] if s["id"] != step_id]
    if not stack["steps"]:
        store["stacks"].remove(stack)
    return _save(ws, store)


def resolve_step(ws: Workspace, step_id: str) -> dict:
    """The step record for a step id; raises on unknown."""
    _stack, step = _find_step(_load(ws), step_id)
    if step is None:
        raise ws_mod.WorkspaceError(f"unknown postproc step {step_id!r}")
    return step


@_mutates_store
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


@_mutates_store
def reconcile(ws: Workspace, resolve) -> list[dict]:
    """Sync queued/running steps with live job state before returning the stacks — the
    completion observer only fires for SUCCESSFUL jobs, so a step whose job failed, was
    canceled, or was deleted from the queue would otherwise stay stuck 'queued' (blocking
    the stack). `resolve(job_id)` returns `(status, output)` for the job, or **None** if it's
    gone (deleted/pruned → the step is treated as canceled). Persists corrections so the
    state survives a reload; returns the stacks. Caller (main.py) owns the runner glue."""
    store = _load(ws)
    changed = False

    # --- Prune what no longer exists (author 2026-08-09) -------------------------------
    # Deleting an image never touched this store, and the loop below only ever revisited
    # QUEUED/RUNNING steps — so a *done* step kept a job_id and an `output` pointing at
    # files that were gone. The author's live project had 13 dangling bases, 18 dangling
    # outputs and 19 dangling jobs across 26 stacks. Reconcile is read on every stacks
    # fetch, so making it authoritative self-heals the store with no migration.
    # The authoritative signal is the JOB, not the file: `resolve` already reports None for a
    # job that was deleted or pruned, and keying on that avoids filesystem races (and is what
    # actually happened — the image went when its job did).
    def _alive(name: str | None) -> bool:
        return bool(name) and (ws.out_dir / name).is_file()

    for stack in list(store["stacks"]):
        # A DONE step whose producing job is gone points at an image that went with it. Keep
        # it only while something still branches from its output — then it is a tombstone
        # holding the chain together; otherwise it is a dead record and goes.
        sources = {s.get("source") for s in stack["steps"]}
        kept = []
        for st in stack["steps"]:
            gone = bool(st.get("job_id")) and resolve(st["job_id"]) is None
            if st.get("status") == "done" and gone:
                if st.get("output") in sources:
                    if not st.get("deleted"):
                        st["deleted"] = True          # tombstone: keeps the chain linked
                        changed = True
                    kept.append(st)
                else:
                    changed = True                    # leaf with nothing behind it → drop
                continue
            kept.append(st)
        if kept != stack["steps"]:
            stack["steps"] = kept
            changed = True
        # A stack is over once nothing of it survives: no steps left, and no base image.
        if not stack["steps"] and not _alive(stack["base"]):
            store["stacks"].remove(stack)
            changed = True

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


@_mutates_store
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
