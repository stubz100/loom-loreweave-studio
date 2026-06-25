"""Durable, resume-paused job queue (M4) — single GPU worker over a PERSISTED job
table (`queue.json`). Replaces the in-memory runner (M1–M3).

What M4 adds (kb-loom-p0.md §7, R69/R78/R88/R159):
- **Durable**: every state change is written atomically to `queue.json`; the queue
  survives a restart.
- **Resume *paused*** (R88): on relaunch the queue loads but does NOT auto-run — the
  dock shows pending work + an [unpause] control.
- **One-job lifecycle** (R159): a `running` job at load is reconciled —
  graceful shutdown → `queued` (partial discarded); crash → `failed` (user retries);
  resumable (P2 training) → recovered. P0 jobs are all non-resumable.
- **VRAM-estimate admission** (§7): a static per-pipeline estimate vs the budget.
- **Capped auto-retry on OOM** with a visible note.

Invariant unchanged: **one job at a time** on the single 16 GB GPU (§7). Cancel
(M3) is preserved. Disk-guard gating is M6.

What M5 adds (P0-8): the runner is now **workspace-bound** — `queue.json`, per-job
logs, and outputs live in the active project (`<project>/jobs/queue.json`,
`<project>/jobs/logs/<id>.log`, `<project>/out/<id>/`), not the interim `.loom_state/`
+ `.dev_out/`. `bind()` points the runner at a project (loads its queue, resume-paused);
the worker idles until a project is bound. Each successful output writes a lineage edge.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

try:
    from .adapters import JobSpec
    from .adapters import _batch as batch_helpers
    from .adapters import zimage as zimage_adapter
    from .adapters import multi as multi_adapter
    from .adapters import sd35 as sd35_adapter
    from .adapters import flux2 as flux2_adapter
    from .adapters import birefnet as birefnet_adapter
    from .adapters import identity as identity_adapter
    from .adapters import face_restore as face_restore_adapter
    from .adapters import ltxv as ltxv_adapter
    from .adapters import frame_harvest as frame_harvest_adapter
    from .adapters import zimage_trainer as zimage_trainer_adapter
    from .config import CONFIG
    from . import lineage
    from . import workspace as ws_mod
    from .workspace import Workspace, new_id
    from .logsetup import get_logger
except ImportError:  # pragma: no cover - direct-run convenience
    from adapters import JobSpec  # type: ignore
    from adapters import _batch as batch_helpers  # type: ignore
    from adapters import zimage as zimage_adapter  # type: ignore
    from adapters import multi as multi_adapter  # type: ignore
    from adapters import sd35 as sd35_adapter  # type: ignore
    from adapters import flux2 as flux2_adapter  # type: ignore
    from adapters import birefnet as birefnet_adapter  # type: ignore
    from adapters import identity as identity_adapter  # type: ignore
    from adapters import face_restore as face_restore_adapter  # type: ignore
    from adapters import ltxv as ltxv_adapter  # type: ignore
    from adapters import frame_harvest as frame_harvest_adapter  # type: ignore
    from adapters import zimage_trainer as zimage_trainer_adapter  # type: ignore
    from config import CONFIG  # type: ignore
    import lineage  # type: ignore
    import workspace as ws_mod  # type: ignore
    from workspace import Workspace, new_id  # type: ignore
    from logsetup import get_logger  # type: ignore

ADAPTERS = {"zimage": zimage_adapter, "multi": multi_adapter, "sd35": sd35_adapter,
            "flux2": flux2_adapter,
            "birefnet": birefnet_adapter, "identity": identity_adapter,
            "face_restore": face_restore_adapter, "ltxv": ltxv_adapter,
            "frame_harvest": frame_harvest_adapter,
            "zimage_trainer": zimage_trainer_adapter}
SCHEMA_VERSION = 1
LOG = get_logger()


def _warn(msg: str) -> None:
    LOG.warning(msg)


def _make_kill_on_close_job():
    """Windows Job Object with KILL_ON_JOB_CLOSE: worker subprocesses assigned to it
    die when the orchestrator process dies — for ANY reason, incl. a hard kill by the
    Tauri shell on app exit (review #1: no orphaned GPU process). No-op off Windows.
    The handle is held for the process lifetime; on death it closes → job closes → kill.

    Failures are LOUD (a silent failure here would silently re-expose orphaned GPU);
    the active mode is surfaced as `WORKER_REAP` / /version `worker_reap`.
    """
    if sys.platform != "win32":
        return None  # not a failure — POSIX reaping (PDEATHSIG/process-group) is a later add
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = k32.CreateJobObjectW(None, None)
        if not job:
            _warn(f"CreateJobObjectW failed (err={ctypes.get_last_error()}); "
                  "GPU workers will NOT be auto-reaped on a hard kill")
            return None

        class _BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = _EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        # 9 = JobObjectExtendedLimitInformation
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            _warn(f"SetInformationJobObject failed (err={ctypes.get_last_error()}); "
                  "GPU workers will NOT be auto-reaped on a hard kill")
            return None
        return job
    except Exception as e:
        _warn(f"Job Object setup failed ({e}); GPU workers will NOT be auto-reaped on a hard kill")
        return None


_KILL_JOB = _make_kill_on_close_job()
# How worker subprocesses are reaped if the orchestrator dies — surfaced in /version.
WORKER_REAP = "job_object" if _KILL_JOB is not None else "none"


def _assign_to_kill_job(proc: subprocess.Popen) -> None:
    """Assign a spawned worker to the kill-on-close job so it can't outlive us."""
    if _KILL_JOB is None or sys.platform != "win32":
        return
    try:
        import ctypes
        if not ctypes.WinDLL("kernel32", use_last_error=True).AssignProcessToJobObject(
                _KILL_JOB, int(proc._handle)):
            _warn(f"AssignProcessToJobObject failed (err={ctypes.get_last_error()}) "
                  f"for worker pid={proc.pid}; this worker may orphan on a hard kill")
    except Exception as e:
        _warn(f"AssignProcessToJobObject raised ({e}) for worker pid={proc.pid}; "
              "this worker may orphan on a hard kill")


# --- per-job cancel Job Object (review 2026-06-13, High) ----------------------------
# Cancel must fell the WHOLE worker tree — `multi`/`flux2` do GPU work in a grandchild.
# `taskkill /T` walks the tree but its success was unchecked + it can miss a reparented
# descendant on some hosts. A per-job Job Object is authoritative: assign the worker to a
# fresh job at spawn; `TerminateJobObject` kills every process in it (incl. descendants)
# ATOMICALLY, no tree-walk. Nested under the process-wide `_KILL_JOB` (Win8+); additive —
# `taskkill` stays as the fallback when the Job Object path is unavailable.

def _create_kill_job():
    """A fresh KILL_ON_JOB_CLOSE Job Object handle (int), or None (non-win32 / failure)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None

        class _BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = _EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(job)
            return None
        return job
    except Exception:  # noqa: BLE001
        return None


def _assign_to_job(proc: subprocess.Popen, job) -> bool:
    if job is None or sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.WinDLL("kernel32", use_last_error=True)
                    .AssignProcessToJobObject(job, int(proc._handle)))
    except Exception:  # noqa: BLE001
        return False


def _terminate_job(job) -> bool:
    if job is None or sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.WinDLL("kernel32", use_last_error=True).TerminateJobObject(job, 1))
    except Exception:  # noqa: BLE001
        return False


def _close_job(job) -> None:
    if job is None or sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(job)
    except Exception:  # noqa: BLE001
        pass

# Static per-pipeline VRAM estimate (GB) for admission (§7); refined by observed
# peaks later. zimage-turbo @720p with cpu_offload peaks ~10–12 GB on the 16 GB rig.
# multi runs its pipelines one-at-a-time as isolated subprocesses (VRAM isolation), so the
# peak is a single pipeline (flux2-klein ~ the largest), not the sum — admission vs 16 GB.
# sd35 (Stage-B img2img/inpaint) with cpu_offload + T5 peaks ~13 GB on the 16 GB rig.
VRAM_ESTIMATES = {"zimage": 11.0, "multi": 14.0, "sd35": 13.0, "birefnet": 4.0,
                  "flux2": 13.0,                          # klein-4b flow+AE peak; Qwen3 encoder
                                                          # freed before the flow loads (§11)
                  "identity": 1.0, "face_restore": 1.0,   # onnx CPU — effectively no VRAM
                  "ltxv": 12.0,                           # 2B + T5-XXL w/ model offload
                  "frame_harvest": 1.0,                   # OpenCV CPU
                  "zimage_trainer": 15.0}                 # P2 Z-Image LoRA train, qfloat8/low_vram
DEFAULT_VRAM_GB = 8.0
MAX_OOM_RETRIES = 1
_OOM_MARKERS = (
    "out of memory", "hip out of memory", "cuda out of memory",
    "outofmemoryerror", "hiperror: out of memory",
)


def estimate_vram(pipeline: str) -> float:
    """Static per-pipeline VRAM estimate (GB) for admission (§7)."""
    return VRAM_ESTIMATES.get(pipeline, DEFAULT_VRAM_GB)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_oom(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _OOM_MARKERS)


class JobRunner:
    """Single-worker FIFO over a durable job table."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self._ws: Workspace | None = None    # active project workspace (M5); None = no project open
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._procs: dict[str, subprocess.Popen] = {}
        self._cancel_jobs: dict[str, int] = {}   # job_id -> per-job kill Job Object handle (win32)
        self._canceled: set[str] = set()
        self._paused = False
        # Why the queue is paused — "resume" (resume-paused load, R88) | "user" (explicit
        # /queue/pause) | None. Surfaced via state()/queue.json so the UI can say WHY
        # (review 2026-06-10: a bare "paused" with no visible jobs reads as a stuck pipeline).
        self._pause_reason: str | None = None
        self._disk_gate: "callable | None" = None   # () -> bool: True = disk hard-stop (M6)
        self._shutting_down = False
        self._worker = threading.Thread(target=self._run_loop, name="loom-gpu-worker", daemon=True)
        self._started = False

    def start(self) -> None:
        """Start the worker thread. It idles until a project is `bind()`-ed (M5) — the
        queue + outputs are per-project, so there's nothing to run with no project open."""
        if self._started:
            return
        self._started = True
        self._worker.start()

    # --- workspace binding (M5) ------------------------------------------
    @property
    def workspace(self) -> Workspace | None:
        with self._lock:
            return self._ws

    def has_running(self) -> bool:
        with self._lock:
            return any(j["status"] == "running" for j in self.jobs.values())

    # --- disk guard hook (M6) --------------------------------------------
    def set_disk_gate(self, fn) -> None:
        """Inject the disk-guard predicate (`() -> bool`, True = hard-stop). The worker
        won't START a queued job while it returns True (running jobs finish, §9)."""
        self._disk_gate = fn

    def _disk_blocked(self) -> bool:
        return bool(self._disk_gate()) if self._disk_gate is not None else False

    def wake(self) -> None:
        """Re-evaluate the dispatch condition (called by the disk guard when a hard-stop
        clears, so jobs held at dispatch resume the instant space frees)."""
        with self._cv:
            self._cv.notify()

    def bind(self, ws: Workspace) -> None:
        """Make `ws` the active project: load its `queue.json` (reconciling in-flight
        jobs, R159) and resume **paused** (R88). Refuses while a job is running so we
        never strand a live GPU worker on the previous project."""
        with self._cv:
            if self._ws is not None and any(j["status"] == "running" for j in self.jobs.values()):
                raise RuntimeError("cannot switch project while a job is running")
            self._ws = ws
            self.jobs = {}
            self._canceled.clear()
            self._shutting_down = False   # binding a project = operational again (clears a prior
                                          # graceful_shutdown flag on an in-process re-bind)
            self._load_locked()
            self._cv.notify()

    def unbind(self) -> None:
        """Close the active project (review 2026-06-10 #2): detach the runner so the app
        runs project-less (project-scoped endpoints 409 again, exactly like pre-open).
        Refuses while a job is running, like bind(). The project's `queue.json` stays on
        disk exactly as last persisted — reopening resumes it (paused, R88)."""
        with self._cv:
            if self._ws is None:
                return
            if any(j["status"] == "running" for j in self.jobs.values()):
                raise RuntimeError("cannot close the project while a job is running")
            LOG.info("project closed: %s", self._ws.path)
            self._ws = None
            self.jobs = {}
            self._canceled.clear()
            self._paused = False
            self._pause_reason = None
            self._cv.notify()

    # --- persistence ------------------------------------------------------
    def _persist_locked(self, clean_shutdown: bool = False) -> None:
        if self._ws is None:   # nothing to persist with no project open (M5)
            return
        data = {
            "schema_version": SCHEMA_VERSION,
            "paused": self._paused,
            "pause_reason": self._pause_reason,
            "clean_shutdown": clean_shutdown,
            "jobs": self.jobs,
        }
        ws_mod.atomic_write_json(self._ws.queue_path, data)   # temp + fsync + replace (§6)

    def _discard_partial(self, job_id: str) -> None:
        if self._ws is not None:
            shutil.rmtree(self._ws.out_dir / job_id, ignore_errors=True)

    def _quarantine_queue(self, reason: str) -> bool:
        """Move a corrupt/invalid `queue.json` ASIDE (rename to `queue.corrupt-<ts>.json`)
        before we start a fresh empty queue — so recoverable job history is **never
        silently overwritten** by the next write (review: High). Returns True if the bad
        file was preserved (safe to write a fresh empty queue), False if it couldn't be
        moved (then we must not overwrite it)."""
        path = self._ws.queue_path
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = path.with_name(f"queue.corrupt-{stamp}.json")
        n = 1
        while target.exists():
            target = path.with_name(f"queue.corrupt-{stamp}.{n}.json")
            n += 1
        try:
            os.replace(path, target)
            _warn(f"{reason}; quarantined to {target.name} — starting with an empty queue")
            return True
        except OSError as e:
            _warn(f"{reason}; could NOT quarantine queue.json ({e}) — starting empty but "
                  "leaving the original in place (it will not be overwritten at load)")
            return False

    def _load_locked(self) -> None:
        """Load the active project's queue, reconcile in-flight jobs (R159), resume
        paused (R88). Called under the lock from `bind()`.

        A corrupt/partial queue, a malformed envelope, or any job record that fails
        `job.schema.json` is **quarantined** (preserved on disk) and the project opens
        with a fresh empty queue — never a silent half-state, and never an overwrite of
        recoverable history (review: High). Per-record validation also stops the worker
        from later touching a job missing `id`/`status`."""
        path = self._ws.queue_path
        if not path.is_file():
            self._paused = False
            self._pause_reason = None
            return
        try:
            data = ws_mod.read_json(path)            # refuses partial/corrupt JSON
        except ws_mod.WorkspaceError as e:
            if self._quarantine_queue(f"queue.json unreadable/partial ({e})"):
                self.jobs = {}
                self._persist_locked()
            else:
                self.jobs = {}
            self._paused = False
            self._pause_reason = None
            return

        # Validate the **envelope** (schema_version/paused/clean_shutdown/jobs types) and
        # then **every job record** before trusting any field — a bad wrapper (e.g.
        # clean_shutdown:"false" as a string) must not be coerced into a graceful/crash
        # decision (review: Med). Any failure quarantines the whole queue.
        invalid: str | None = None
        try:
            ws_mod.validate(data, "queue.schema.json")
            for jid, j in data["jobs"].items():
                ws_mod.validate(j, "job.schema.json")
                if j.get("id") != jid:
                    raise ws_mod.WorkspaceError(f"job key {jid!r} != record id {j.get('id')!r}")
        except ws_mod.WorkspaceError as e:
            invalid = str(e)
        if invalid is not None:
            if self._quarantine_queue(f"queue.json invalid ({invalid})"):
                self.jobs = {}
                self._persist_locked()
            else:
                self.jobs = {}
            self._paused = False
            self._pause_reason = None
            return

        clean = data["clean_shutdown"]   # validated boolean (no string coercion)
        self.jobs = data["jobs"]
        # Reconcile in-flight jobs per the one-job lifecycle (R159).
        for j in self.jobs.values():
            if j.get("status") != "running":
                continue
            if j.get("resumable"):
                j["status"] = "queued"
                j["note"] = "recovered (resumable) — resumes on unpause"
            elif clean:
                j["status"] = "queued"
                j["progress"] = 0.0
                j["partial_outputs"] = []
                j["note"] = "re-queued after graceful shutdown (partial discarded)"
                self._discard_partial(j["id"])
            else:
                j["status"] = "failed"
                j["finished_at"] = _now()
                j["note"] = "orchestrator crashed mid-job"
                j["result"] = {"ok": False, "returncode": -1, "outputs": [],
                               "error": "orchestrator crashed mid-job", "stderr_tail": ""}
                self._discard_partial(j["id"])
        # Resume PAUSED whenever there is pending work (R88) — tagged "resume" so the UI
        # can say "resumed from last session — review & unpause", not a bare "paused".
        self._paused = any(j["status"] == "queued" for j in self.jobs.values())
        self._pause_reason = "resume" if self._paused else None
        self._persist_locked()

    def graceful_shutdown(self) -> None:
        """From the lifespan shutdown (clean stop): **terminate the live worker** (so it
        never outlives us — review #1) and mark a clean stop so the reload re-queues the
        in-flight job (R159 graceful branch). Leaves the job `running` in the file; the
        reload's clean-shutdown branch re-queues it (and the worker, if it reaches
        finalize first, re-queues it via the `_shutting_down` guard)."""
        LOG.info("graceful shutdown — re-queue in-flight + clean stop")
        with self._lock:
            self._shutting_down = True
            for proc in list(self._procs.values()):
                if proc.poll() is None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
            self._persist_locked(clean_shutdown=True)

    # --- queue control ----------------------------------------------------
    def pause(self) -> None:
        with self._cv:
            self._paused = True
            self._pause_reason = "user"
            self._persist_locked()

    def unpause(self) -> None:
        with self._cv:
            self._paused = False
            self._pause_reason = None
            self._persist_locked()
            self._cv.notify()

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def _clear_pause_if_empty_locked(self) -> None:
        """Drop a now-meaningless paused state: paused exists to hold QUEUED work for
        review (R88) — once the last queued job is canceled/deleted there is nothing to
        hold, and a sticky '⏸ paused (0 queued)' reads as a stuck pipeline (review
        2026-06-10). Called under the lock after any operation that removes queued work."""
        if self._paused and not any(j["status"] == "queued" for j in self.jobs.values()):
            self._paused = False
            self._pause_reason = None
            self._persist_locked()
            LOG.info("queue auto-unpaused (no queued work left to hold)")

    def submit(self, *, pipeline: str, mode: str, params: dict,
               batch_id: str, index: int, batch_size: int,
               requester_id: str = "sandbox",
               profile_version_id: str | None = None, stage: str | None = None,
               coverage_cell: dict | None = None,
               post_passes: list | None = None,
               chained_from: str | None = None, pass_name: str | None = None,
               resumable: bool = False) -> str:
        job_id = new_id("job", 8)
        with self._cv:
            self.jobs[job_id] = {
                "id": job_id,
                "schema_version": SCHEMA_VERSION,
                "pipeline": pipeline,
                "mode": mode,
                "params": params,
                "requester_id": requester_id,
                "profile_version_id": profile_version_id,   # P1: AssetProfile version (lineage)
                "stage": stage,                             # P1: bootstrap stage A|B|C
                "coverage_cell": coverage_cell,             # P1/M3: Stage-B recipe cell (→ ref_set, P2)
                "post_passes": post_passes or [],           # clean/polish chained after success
                "chained_from": chained_from,               # parent job when THIS is a pass
                "pass": pass_name,                          # "clean" | "polish" | None
                "vram_estimate_gb": VRAM_ESTIMATES.get(pipeline, DEFAULT_VRAM_GB),
                "resumable": bool(resumable),      # P2 trainer jobs checkpoint/resume (R159)
                "retry_count": 0,
                "status": "queued",
                "progress": 0.0,
                "partial_outputs": [],             # interim results (multi pool streams in)
                "note": "",
                "created_at": _now(),
                "started_at": None,
                "finished_at": None,
                "wall_s": None,
                "result": None,
                "log_tail": "",
                "batch_id": batch_id,
                "index": index,
                "batch_size": batch_size,
            }
            self._persist_locked()
            self._cv.notify()
        LOG.info("queued %s (%s/%s, batch=%s %d/%d)", job_id, pipeline, mode,
                 batch_id, index + 1, batch_size)
        return job_id

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued/running job. Cancel = kill the worker **tree** (M3; review
        2026-06-10 — terminating only the direct child left `multi`'s grandchild holding
        the GPU)."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None or job["status"] in ("done", "failed", "canceled"):
                return False
            proc = self._procs.get(job_id)
            if job["status"] == "running" and proc is not None and proc.poll() is not None:
                return False  # already exited -> finalization in flight (race, M3 #3)
            self._canceled.add(job_id)
            if job["status"] == "queued":
                job["status"] = "canceled"
                job["finished_at"] = _now()
                self._canceled.discard(job_id)   # terminal immediately — no stale entry
                self._persist_locked()
                self._clear_pause_if_empty_locked()   # don't leave "paused (0 queued)"
        if proc is not None and proc.poll() is None:
            cancel_job = self._cancel_jobs.get(job_id)
            threading.Thread(target=self._kill_tree, args=(proc,),
                             kwargs={"job_handle": cancel_job}, daemon=True).start()
        return True

    # Injected completion hook (M4 review — durable anchor verification). Class-level
    # default so the process-wide singleton works without an __init__ change.
    _observer = None

    def set_completion_observer(self, fn) -> None:
        """Register a best-effort `fn(job_snapshot)` fired after every OK job finalizes
        (after lineage + pass-chaining). One observer; the API layer owns the glue."""
        self._observer = fn

    def stop_batch(self, job_id: str) -> bool:
        """Gracefully stop a RUNNING batch job (a job whose params carry `batch_items`):
        drop a `STOP` file into its out dir — the worker finishes the current item, marks
        the rest skipped, and exits cleanly, so completed images stay valid (vs `cancel`,
        which kills the tree and discards the partial dir). Returns False if the job
        isn't a running batch."""
        with self._lock:
            job = self.jobs.get(job_id)
            ws = self._ws
            if (job is None or ws is None or job["status"] != "running"
                    or not job.get("params", {}).get("batch_items")):
                return False
        try:
            (ws.out_dir / job_id / "STOP").write_text("stop", encoding="utf-8")
        except OSError as e:
            _warn(f"could not write STOP file for {job_id}: {e}")
            return False
        LOG.info("stop requested for batch %s (finishes current item, keeps completed)", job_id)
        return True

    def delete(self, job_id: str) -> bool:
        """Delete a **terminal** job and **all** its artifacts atomically: the queue
        entry, the per-job output dir (`out/<id>/` — PNG + sidecar manifest), the per-job
        log (`jobs/logs/<id>.log`), and the lineage edge. A safe replacement for hand-
        deleting files (which orphans the manifest/log/queue/lineage). Running/queued jobs
        must be canceled first → returns False (409). Frees disk for the guard (§9)."""
        with self._cv:
            job = self.jobs.get(job_id)
            if job is None or job["status"] not in ("done", "failed", "canceled"):
                return False
            ws = self._ws
            # Drop the durable record FIRST (+persist) so a crash mid-delete leaves at
            # worst orphaned files (harmless, swept by disk usage) — never a queue/lineage
            # entry pointing at deleted files.
            self.jobs.pop(job_id, None)
            self._canceled.discard(job_id)
            self._persist_locked()
            self._clear_pause_if_empty_locked()   # deleting the last queued job too
        if ws is not None:
            shutil.rmtree(ws.out_dir / job_id, ignore_errors=True)
            try:
                ws.log_path(job_id).unlink(missing_ok=True)
            except OSError as e:
                _warn(f"could not remove job log for {job_id}: {e}")
            try:
                lineage.remove_edge(ws, job_id)
            except Exception as e:  # noqa: BLE001 - lineage is rebuildable, never block delete
                _warn(f"lineage edge cleanup failed for {job_id}: {e}")
        LOG.info("deleted %s + all artifacts", job_id)
        return True

    def delete_output(self, job_id: str, output: str) -> str:
        """Delete a **single** output image of a terminal MULTI-output job (a `multi`-cast
        candidate or a Stage-B batch tile) — strictly individual deletion, leaving the rest of
        the pool intact (user 2026-06-21: whole-job delete was nuking the whole batch). Prunes
        the file + sidecar from `out/`, drops the name from `result.output_names`/`output_meta`/
        `partial_outputs`, and persists. When it's the job's **last/only** output, the whole job
        is removed (→ `delete()`). Returns `"output"` | `"job"` | `"missing"` (unknown job /
        not-terminal / `output` not one of its outputs)."""
        with self._cv:
            job = self.jobs.get(job_id)
            if job is None or job["status"] not in ("done", "failed", "canceled"):
                return "missing"
            res = job.get("result") or {}
            names = list(res.get("output_names")
                         or ([res["output_name"]] if res.get("output_name") else []))
            if output not in names:
                return "missing"
            remaining = [n for n in names if n != output]
            if not remaining:                      # last/only output → drop the whole job
                drop_whole = True
            else:
                drop_whole = False
                res["output_names"] = remaining
                meta = res.get("output_meta")
                if isinstance(meta, dict):
                    meta.pop(output, None)
                if res.get("output_name") == output:
                    res["output_name"] = remaining[0]
                job["partial_outputs"] = [p for p in (job.get("partial_outputs") or [])
                                          if p != output]
                self._persist_locked()
            ws = self._ws
        if drop_whole:
            self.delete(job_id)                    # handles rmtree + log + lineage + persist
            return "job"
        if ws is not None:                         # prune just this image + its sidecar manifest
            try:
                f = (ws.out_dir / output)
                f.unlink(missing_ok=True)
                f.with_suffix(".json").unlink(missing_ok=True)
            except OSError as e:
                _warn(f"could not remove output {output} of {job_id}: {e}")
        LOG.info("deleted output %s of %s (%d remain)", output, job_id, len(remaining))
        return "output"

    @staticmethod
    def _kill_tree(proc: subprocess.Popen, grace_s: float = 5.0, job_handle=None) -> None:
        """Kill the worker and ALL its descendants. `multi`/`flux2` do GPU work in a
        grandchild (stage_runner / -m subprocess); `terminate()` on the direct child orphans
        it mid-generation (the process-wide Job Object only reaps on orchestrator death, not
        per-job cancel).

        Windows: the **per-job Job Object** (`job_handle`) is authoritative —
        `TerminateJobObject` fells every process in it atomically, no tree-walk. `taskkill /T`
        is the fallback (its return code is now CHECKED + retried — review 2026-06-13). POSIX:
        terminate → kill (process-group kill is a later add, like the PDEATHSIG reap path).
        Either way the handle is `wait()`ed (and re-waited after an escalated kill) so it's
        never left unreaped."""
        if sys.platform == "win32":
            felled = _terminate_job(job_handle)   # atomic tree kill (preferred)
            if not felled:
                ok = False
                for attempt in (1, 2):
                    try:
                        r = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                           capture_output=True, text=True, timeout=15)
                    except Exception as e:  # noqa: BLE001
                        _warn(f"taskkill /T raised for pid={proc.pid} (attempt {attempt}): {e}")
                        continue
                    # rc 0 = felled; 128 = pid not found (already gone) — both acceptable.
                    if r.returncode in (0, 128):
                        ok = True
                        break
                    _warn(f"taskkill /T rc={r.returncode} for pid={proc.pid} (attempt "
                          f"{attempt}): {(r.stderr or r.stdout or '').strip()[:200]}")
                if not ok:
                    # Couldn't confirm a tree kill — at least fell the direct child so the
                    # worker stops (descendants logged above; the Job Object path is the fix).
                    try:
                        proc.kill()
                    except Exception:
                        pass
        else:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=grace_s)   # reap the handle after the escalated kill
            except Exception:
                pass

    # --- read views -------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return {jid: dict(j) for jid, j in self.jobs.items()}

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            j = self.jobs.get(job_id)
            return dict(j) if j else None

    def counts(self) -> dict:
        c = {"queued": 0, "running": 0, "done": 0, "failed": 0, "canceled": 0}
        with self._lock:
            for j in self.jobs.values():
                c[j["status"]] = c.get(j["status"], 0) + 1
        return c

    def state(self) -> dict:
        with self._lock:
            return {"paused": self._paused, "pause_reason": self._pause_reason,
                    "vram_budget_gb": CONFIG.vram_budget_gb,
                    "counts": self.counts()}

    # --- worker -----------------------------------------------------------
    def _next_queued_id_locked(self) -> str | None:
        pend = [j for j in self.jobs.values()
                if j["status"] == "queued" and j["id"] not in self._canceled]
        if not pend:
            return None
        pend.sort(key=lambda j: j["created_at"])  # FIFO (ISO timestamps sort chronologically)
        return pend[0]["id"]

    def _run_loop(self) -> None:
        while True:
            with self._cv:
                while (self._ws is None or self._paused or self._shutting_down
                       or self._disk_blocked() or self._next_queued_id_locked() is None):
                    self._cv.wait()
                job_id = self._next_queued_id_locked()
                job = self.jobs[job_id]
                if job_id in self._canceled:
                    job["status"] = "canceled"
                    job["finished_at"] = _now()
                    self._canceled.discard(job_id)   # terminal — no stale entries
                    self._persist_locked()
                    continue
                job["status"] = "running"
                job["started_at"] = _now()
                job["progress"] = 0.0
                pipeline, mode, params = job["pipeline"], job["mode"], dict(job["params"])
                self._persist_locked()
            LOG.info("running %s (%s/%s)", job_id, pipeline, mode)
            try:
                self._execute(job_id, pipeline, mode, params)
            except Exception as e:  # never let the worker die
                with self._lock:
                    j = self.jobs.get(job_id)
                    if j:
                        j["status"] = "failed"
                        j["finished_at"] = _now()
                        j["result"] = {"ok": False, "returncode": -1, "outputs": [],
                                       "error": f"runner error: {e}", "stderr_tail": str(e)}
                        self._persist_locked()

    def _execute(self, job_id: str, pipeline: str, mode: str, params: dict) -> None:
        adapter = ADAPTERS[pipeline]
        script = adapter.resolve_script(CONFIG.pipeline_roots)
        if script is None:
            raise RuntimeError(f"{pipeline} worker not found in any pipeline root")
        ws = self._ws                              # stable: bind() refuses while running (M5)
        if ws is None:
            raise RuntimeError("no project workspace bound")

        out_dir = ws.out_dir / job_id              # per-job isolation, in <project>/out/ (M5)
        out_dir.mkdir(parents=True, exist_ok=True)
        spec = JobSpec(pipeline=pipeline, mode=mode, params=params, output_dir=out_dir)
        argv = adapter.build_argv(spec, CONFIG.venv_python, script)

        t0 = time.time()
        # Force the worker (and its sub-subprocesses, e.g. multi's stage_runner) to flush stdout
        # line-by-line — Python block-buffers stdout to a pipe by default, which made a running
        # multi cast look hung (no progress, empty per-job log) for minutes. PYTHONUNBUFFERED is
        # inherited by the child's children (stage_runner copies os.environ), so the whole tree
        # streams live → real-time progress + log.
        # PYTHONIOENCODING=utf-8 forces the worker (+ its children) to ENCODE stdout as UTF-8
        # instead of the Windows console default (cp1252), which crashed on any non-cp1252
        # char a worker prints (e.g. flux2's "→" offload line, "★", "≤"). We DECODE the pipe
        # as utf-8/replace below so the two sides agree and a stray byte never fells the read
        # loop. Both env keys are inherited by sub-subprocesses (multi's stage_runner).
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.Popen(
            argv, cwd=str(script.parents[2]), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            encoding="utf-8", errors="replace",
        )
        _assign_to_kill_job(proc)   # die with the orchestrator — never orphan the GPU (review #1)
        # Per-job kill Job Object (review 2026-06-13): cancel = TerminateJobObject → the WHOLE
        # tree atomically (multi/flux2 grandchildren included). Nested under _KILL_JOB (Win8+);
        # no-ops off win32 / on failure (cancel falls back to taskkill /T). Closed at finalize.
        cancel_job = _create_kill_job()
        if cancel_job is not None and not _assign_to_job(proc, cancel_job):
            _close_job(cancel_job)            # couldn't nest → drop it, taskkill /T covers cancel
            cancel_job = None
        with self._lock:
            self._procs[job_id] = proc
            if cancel_job is not None:
                self._cancel_jobs[job_id] = cancel_job

        # Full subprocess stdout/stderr → a persisted per-job log (P0-14); the in-memory
        # tail still drives the live UI pane. The log survives the process for post-mortem.
        tail: deque[str] = deque(maxlen=60)
        log_fp = None
        try:
            ws.logs_dir.mkdir(parents=True, exist_ok=True)
            log_fp = open(ws.log_path(job_id), "w", encoding="utf-8")
        except OSError as e:
            _warn(f"could not open job log for {job_id}: {e}")
        # Stateful per-job progress when the adapter offers it (`make_progress(params)`
        # — e.g. multi counts per-candidate completions for a real fraction); else the
        # stateless coarse `progress(line)` markers.
        make_progress = getattr(adapter, "make_progress", None)
        progress_fn = make_progress(params) if make_progress else adapter.progress
        # Interim results (user request 2026-06-10): adapters with a `collect_output`
        # hook announce each finished image as it lands, so a long multi cast streams
        # tiles into the grid instead of appearing all-at-once at the end.
        collect_fn = getattr(adapter, "collect_output", None)
        out_root = ws.out_dir.resolve()
        last_tail = 0.0
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    tail.append(line)
                    if log_fp is not None:
                        log_fp.write(line + "\n")
                    pr = progress_fn(line)
                    partial: str | None = None
                    if collect_fn is not None:
                        raw = collect_fn(line)
                        if raw:
                            try:    # serve-guard: only paths inside the project out/
                                partial = Path(raw).resolve().relative_to(out_root).as_posix()
                            except (ValueError, OSError):
                                partial = None
                    # Keep the live tail fresh on EVERY line (throttled), not only at
                    # coarse stage markers — a multi cast used to show a frozen tail for
                    # minutes (review 2026-06-10). In-memory only; no disk write here.
                    now = time.time()
                    if pr is not None or partial is not None or now - last_tail >= 0.5:
                        last_tail = now
                        with self._lock:
                            j = self.jobs.get(job_id)
                            if j:
                                if pr is not None:
                                    j["progress"] = pr
                                if partial is not None:
                                    prev = j.get("partial_outputs") or []
                                    if partial not in prev:
                                        j["partial_outputs"] = [*prev, partial]
                                j["log_tail"] = "\n".join(tail)
        finally:
            if log_fp is not None:
                log_fp.close()
        proc.wait()
        rc = proc.returncode
        log_text = "\n".join(tail)
        with self._lock:
            self._procs.pop(job_id, None)
            cancel_job = self._cancel_jobs.pop(job_id, None)   # worker exited → free the handle
            canceled = job_id in self._canceled
            retry_count = self.jobs[job_id].get("retry_count", 0)
            shutting = self._shutting_down
        _close_job(cancel_job)   # worker is dead (proc.wait above) — just frees the kernel handle

        # Shutdown wins over everything: re-queue the in-flight job (don't mark it
        # failed because we killed its worker) so it survives the restart (review #1).
        # Discard the partial here too (the subprocess is dead by now) so 'quit mid-job →
        # partial discarded' holds deterministically, not only via the reload race (P0-15).
        if shutting:
            self._discard_partial(job_id)
            with self._cv:
                j = self.jobs[job_id]
                j["status"] = "queued"
                j["progress"] = 0.0
                j["partial_outputs"] = []
                j["note"] = "re-queued at shutdown"
                self._persist_locked(clean_shutdown=True)
            return

        rec = adapter.parse_result(rc, log_text, "", out_dir)

        # Cancel wins only if the run didn't actually complete (M3 #3).
        if canceled and not rec.ok:
            self._discard_partial(job_id)
            with self._lock:
                j = self.jobs[job_id]
                j["status"] = "canceled"
                j["finished_at"] = _now()
                j["wall_s"] = round(time.time() - t0, 2)
                j["log_tail"] = log_text
                self._canceled.discard(job_id)   # terminal — no stale entries (review)
                self._persist_locked()
            return

        # Capped auto-retry on OOM (§7). retry-WITH-offload: ask the adapter to escalate
        # its offload mode for the retry. zimage has no heavier offload (cpu_offload is
        # already default) so it's a plain retry; the video pipelines that DO have
        # group/sequential offload (P3) implement `escalate_offload` for real escalation.
        if (not rec.ok and not canceled and retry_count < MAX_OOM_RETRIES
                and _is_oom((rec.error or "") + " " + (rec.stderr_tail or ""))):
            self._discard_partial(job_id)
            escalate = getattr(adapter, "escalate_offload", None)
            new_params = escalate(dict(params), retry_count + 1) if escalate else params
            with self._cv:
                j = self.jobs[job_id]
                j["status"] = "queued"
                j["retry_count"] = retry_count + 1
                j["params"] = new_params
                j["progress"] = 0.0
                j["partial_outputs"] = []
                j["log_tail"] = log_text
                j["note"] = f"OOM — auto-retry {retry_count + 1}/{MAX_OOM_RETRIES}"
                self._persist_locked()
                self._cv.notify()
            return

        result = rec.to_dict()
        # Surface outputs as paths **relative to the project out/ dir** so /outputs can serve
        # them (works for nested per-pipeline candidate trees, not just basenames). A multi
        # run yields N candidates → `output_names` is the pool; `output_name` stays the
        # primary (first) for back-compat (zimage = exactly one).
        names: list[str] = []
        for o in rec.outputs:
            try:
                names.append(Path(o).resolve().relative_to(out_root).as_posix())
            except ValueError:
                names.append(f"{job_id}/{os.path.basename(o)}")   # defensive fallback
        if names:
            result["output_name"] = names[0]
            result["output_names"] = names
        # Batch jobs: per-output metadata (parallel to outputs) keyed by the served name —
        # e.g. each Stage-B image's coverage_cell + seed (curation reads it per output).
        if rec.outputs_meta and len(rec.outputs_meta) == len(names):
            result["output_meta"] = {n: m for n, m in zip(names, rec.outputs_meta)
                                     if m is not None}
        if rec.manifest_path and Path(rec.manifest_path).is_file():
            try:
                m = json.loads(Path(rec.manifest_path).read_text(encoding="utf-8"))
                result["seed"] = m.get("seed")
            except (json.JSONDecodeError, OSError):
                pass

        with self._lock:
            j = self.jobs[job_id]
            j["status"] = "done" if rec.ok else "failed"
            j["result"] = result
            j["finished_at"] = _now()
            j["wall_s"] = round(time.time() - t0, 2)
            j["progress"] = 1.0 if rec.ok else j.get("progress", 0.0)
            j["log_tail"] = log_text
            # A partial/stopped batch must never read as a silent green done (review
            # 2026-06-10): surface "partial dataset: ok/count …" as the job note.
            if rec.ok:
                note = batch_helpers.partial_note(result.get("batch"))
                # A user stop also suppresses the chained clean/polish passes (review
                # 2026-06-11, enforced in _submit_chained) — say so on the note.
                if rec.manifest_status == "stopped" and j.get("post_passes"):
                    skipped = " → ".join(p.get("pass", "?") for p in j["post_passes"])
                    note = f"{note or 'stopped early'}; {skipped} pass(es) not chained"
                if note:
                    j["note"] = note
            self._canceled.discard(job_id)   # terminal — no stale entries (review)
            self._persist_locked()
            job_snapshot = dict(j) if rec.ok else None
        if rec.ok:
            LOG.info("done %s in %.1fs -> %s", job_id, round(time.time() - t0, 1),
                     result.get("output_name"))
        else:
            LOG.warning("failed %s (rc=%s): %s", job_id, rc, (rec.error or "")[:160])

        # Lineage edges — ONE PER OUTPUT (R98; review 2026-06-10 — a batch/multi job
        # yields N outputs and recording only the first lost provenance for the rest).
        # Written after the job is durable so the index only ever references a persisted
        # result. Best-effort: a lineage write failure must not fail an otherwise-good
        # generation (the index is rebuildable).
        if job_snapshot is not None:
            try:
                lineage.record_output(ws, job_snapshot)
            except Exception as e:  # noqa: BLE001 - lineage is non-critical, never block output
                _warn(f"lineage write failed for {job_id}: {e}")
            # Chain the next post-pass (clean/polish, 2026-06-11): one batch img2img job
            # over this job's outputs. Best-effort: a chain failure leaves the parent done.
            if job_snapshot.get("post_passes"):
                try:
                    self._submit_chained(job_snapshot)
                except Exception as e:  # noqa: BLE001
                    _warn(f"post-pass chain failed for {job_id}: {e}")
            # Completion observer (M4 review): an injected, best-effort hook the API layer
            # uses to persist facts derived from a finished job (anchor verification) —
            # keeps the runner asset-agnostic (same injection pattern as the disk gate).
            if self._observer is not None:
                try:
                    self._observer(job_snapshot)
                except Exception as e:  # noqa: BLE001
                    _warn(f"completion observer failed for {job_id}: {e}")

    def _submit_chained(self, parent: dict) -> None:
        """Submit the FIRST remaining post-pass of `parent` as a chained **batch img2img
        job** over the parent's outputs (user request 2026-06-11: clean/polish on ANY
        run). One item per output (init_image = that image; its own prompt/seed/coverage
        cell from `output_meta` when present); the rest of the pass list rides on the
        chained job, so polish chains off clean's outputs. The chained job inherits the
        parent's batch/requester/version/stage (it lands in the same grid) — and being a
        normal batch job, its tiles STREAM per item (the old in-worker multi passes were
        piped and only appeared at the end)."""
        ws = self._ws
        passes = list(parent.get("post_passes") or [])
        result = parent.get("result") or {}
        # A user-stopped batch must NOT chain (review 2026-06-11): ⏹ means "wind down" —
        # a stopped batch is still ok (≥1 output), but silently enqueueing a clean/polish
        # pass over the partial outputs would undo the stop. The skip is surfaced on the
        # parent's note (finalize); re-run the pass from the drawer once curated.
        if result.get("manifest_status") == "stopped":
            LOG.info("skip %s pass chain for %s — batch was user-stopped",
                     passes[0].get("pass", "?") if passes else "?", parent.get("id"))
            return
        names = result.get("output_names") or ([result["output_name"]]
                                               if result.get("output_name") else [])
        if ws is None or not passes or not names:
            return
        spec, rest = passes[0], passes[1:]
        meta_map = result.get("output_meta") or {}
        pparams = parent.get("params") or {}
        # M4/M6/M7: the identity + restore + harvest passes are file→file operations over
        # the outputs (no diffusion backbone) — batch-shaped like clean/polish but a
        # different worker vocabulary: items carry `input` (no prompt/init_image); the
        # pass tunables ride the shared params (identity: the anchor; restore: the blend;
        # harvest: every/max_frames).
        io_pass = spec.get("pass") in ("identity", "restore", "harvest")
        items: list[dict] = []
        for n in names:
            m = meta_map.get(n) or {}
            abs_path = str((ws.out_dir / n).resolve())
            if io_pass:
                item: dict = {"input": abs_path}
            else:
                item = {
                    "prompt": spec.get("prompt") or m.get("prompt") or pparams.get("prompt") or "",
                    "init_image": abs_path,
                }
            seed = spec.get("seed", m.get("seed", pparams.get("seed")))
            if seed is not None:
                item["seed"] = seed
            carry = {k: m[k] for k in ("coverage_cell", "method", "index") if k in m}
            if not carry and parent.get("coverage_cell"):
                # M7: a video-sketch parent has no per-output meta (one mp4) — the
                # sketch's TARGET cell is the job-level field; harvested frames inherit
                # it so curation/keep work exactly like recipe cells.
                carry = {"coverage_cell": parent["coverage_cell"]}
            if carry:
                item["meta"] = carry            # curation survives the pass (Stage-B)
            items.append(item)
        if io_pass:
            params: dict = {
                "prompt": f"[{spec['pass']} pass of {parent['id']} · {len(items)} image(s)]",
                "batch_items": items,
                # Display metadata only (the io workers don't read dims): the grid derives
                # the tile aspect from job params — without these a 1024² pass output
                # rendered in a 1280×720 tile (user finding 2026-06-12).
                "width": pparams.get("width", 1024),
                "height": pparams.get("height", 1024),
            }
            if spec["pass"] == "identity":
                params["anchor_image"] = spec["anchor"]
                params["min_det_score"] = spec.get("min_det_score", 0.5)
                backend, mode = "identity", "lock"
            elif spec["pass"] == "harvest":
                params["every"] = spec.get("every", 6)
                params["max_frames"] = spec.get("max_frames", 24)
                backend, mode = "frame_harvest", "harvest"
            else:
                params["blend"] = spec.get("blend", 0.8)
                backend, mode = "face_restore", "restore"
        else:
            params = {
                "prompt": f"[{spec['pass']} pass of {parent['id']} · {len(items)} image(s)]",
                "batch_items": items,
                "width": pparams.get("width", 1024),
                "height": pparams.get("height", 1024),
            }
            if spec.get("strength") is not None:
                params["strength"] = spec["strength"]
            if spec.get("negative_prompt"):
                params["negative_prompt"] = spec["negative_prompt"]
            backend, mode = spec["backend"], "img2img"
        if spec.get("model_name"):
            params["model_name"] = spec["model_name"]
        jid = self.submit(pipeline=backend, mode=mode, params=params,
                          batch_id=parent.get("batch_id", ""), index=0, batch_size=1,
                          requester_id=parent.get("requester_id", "sandbox"),
                          profile_version_id=parent.get("profile_version_id"),
                          stage=parent.get("stage"),
                          post_passes=rest,
                          chained_from=parent["id"], pass_name=spec["pass"])
        LOG.info("chained %s pass for %s -> %s (%d image(s), backend %s)",
                 spec["pass"], parent["id"], jid, len(items), backend)


# Process-wide singleton.
RUNNER = JobRunner()
