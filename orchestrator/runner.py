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
    from .adapters import zimage as zimage_adapter
    from .config import CONFIG
    from . import lineage
    from . import workspace as ws_mod
    from .workspace import Workspace, new_id
except ImportError:  # pragma: no cover - direct-run convenience
    from adapters import JobSpec  # type: ignore
    from adapters import zimage as zimage_adapter  # type: ignore
    from config import CONFIG  # type: ignore
    import lineage  # type: ignore
    import workspace as ws_mod  # type: ignore
    from workspace import Workspace, new_id  # type: ignore

ADAPTERS = {"zimage": zimage_adapter}
SCHEMA_VERSION = 1


def _warn(msg: str) -> None:
    print(f"[loom] WARNING: {msg}", file=sys.stderr, flush=True)


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

# Static per-pipeline VRAM estimate (GB) for admission (§7); refined by observed
# peaks later. zimage-turbo @720p with cpu_offload peaks ~10–12 GB on the 16 GB rig.
VRAM_ESTIMATES = {"zimage": 11.0}
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
        self._canceled: set[str] = set()
        self._paused = False
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

    # --- persistence ------------------------------------------------------
    def _persist_locked(self, clean_shutdown: bool = False) -> None:
        if self._ws is None:   # nothing to persist with no project open (M5)
            return
        data = {
            "schema_version": SCHEMA_VERSION,
            "paused": self._paused,
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
                j["note"] = "re-queued after graceful shutdown (partial discarded)"
                self._discard_partial(j["id"])
            else:
                j["status"] = "failed"
                j["finished_at"] = _now()
                j["note"] = "orchestrator crashed mid-job"
                j["result"] = {"ok": False, "returncode": -1, "outputs": [],
                               "error": "orchestrator crashed mid-job", "stderr_tail": ""}
                self._discard_partial(j["id"])
        # Resume PAUSED whenever there is pending work (R88).
        self._paused = any(j["status"] == "queued" for j in self.jobs.values())
        self._persist_locked()

    def graceful_shutdown(self) -> None:
        """From the lifespan shutdown (clean stop): **terminate the live worker** (so it
        never outlives us — review #1) and mark a clean stop so the reload re-queues the
        in-flight job (R159 graceful branch). Leaves the job `running` in the file; the
        reload's clean-shutdown branch re-queues it (and the worker, if it reaches
        finalize first, re-queues it via the `_shutting_down` guard)."""
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
            self._persist_locked()

    def unpause(self) -> None:
        with self._cv:
            self._paused = False
            self._persist_locked()
            self._cv.notify()

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def submit(self, *, pipeline: str, mode: str, params: dict,
               batch_id: str, index: int, batch_size: int,
               requester_id: str = "sandbox") -> str:
        job_id = new_id("job", 8)
        with self._cv:
            self.jobs[job_id] = {
                "id": job_id,
                "schema_version": SCHEMA_VERSION,
                "pipeline": pipeline,
                "mode": mode,
                "params": params,
                "requester_id": requester_id,
                "vram_estimate_gb": VRAM_ESTIMATES.get(pipeline, DEFAULT_VRAM_GB),
                "resumable": False,                # P0 jobs don't checkpoint (R159)
                "retry_count": 0,
                "status": "queued",
                "progress": 0.0,
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
        return job_id

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued/running job. Cancel = terminate then kill (M3)."""
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
                self._persist_locked()
        if proc is not None and proc.poll() is None:
            proc.terminate()
            threading.Thread(target=self._grace_kill, args=(proc,), daemon=True).start()
        return True

    @staticmethod
    def _grace_kill(proc: subprocess.Popen, grace_s: float = 5.0) -> None:
        try:
            proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
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
            return {"paused": self._paused, "vram_budget_gb": CONFIG.vram_budget_gb,
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
                while (self._ws is None or self._paused or self._disk_blocked()
                       or self._next_queued_id_locked() is None):
                    self._cv.wait()
                job_id = self._next_queued_id_locked()
                job = self.jobs[job_id]
                if job_id in self._canceled:
                    job["status"] = "canceled"
                    job["finished_at"] = _now()
                    self._persist_locked()
                    continue
                job["status"] = "running"
                job["started_at"] = _now()
                job["progress"] = 0.0
                pipeline, mode, params = job["pipeline"], job["mode"], dict(job["params"])
                self._persist_locked()
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
        proc = subprocess.Popen(
            argv, cwd=str(script.parents[2]),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        _assign_to_kill_job(proc)   # die with the orchestrator — never orphan the GPU (review #1)
        with self._lock:
            self._procs[job_id] = proc

        # Full subprocess stdout/stderr → a persisted per-job log (P0-14); the in-memory
        # tail still drives the live UI pane. The log survives the process for post-mortem.
        tail: deque[str] = deque(maxlen=60)
        log_fp = None
        try:
            ws.logs_dir.mkdir(parents=True, exist_ok=True)
            log_fp = open(ws.log_path(job_id), "w", encoding="utf-8")
        except OSError as e:
            _warn(f"could not open job log for {job_id}: {e}")
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    tail.append(line)
                    if log_fp is not None:
                        log_fp.write(line + "\n")
                    pr = adapter.progress(line)
                    if pr is not None:
                        with self._lock:
                            j = self.jobs.get(job_id)
                            if j:
                                j["progress"] = pr
                                j["log_tail"] = "\n".join(tail)
        finally:
            if log_fp is not None:
                log_fp.close()
        proc.wait()
        rc = proc.returncode
        log_text = "\n".join(tail)
        with self._lock:
            self._procs.pop(job_id, None)
            canceled = job_id in self._canceled
            retry_count = self.jobs[job_id].get("retry_count", 0)
            shutting = self._shutting_down

        # Shutdown wins over everything: re-queue the in-flight job (don't mark it
        # failed because we killed its worker) so it survives the restart (review #1).
        if shutting:
            with self._cv:
                j = self.jobs[job_id]
                j["status"] = "queued"
                j["progress"] = 0.0
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
                j["log_tail"] = log_text
                j["note"] = f"OOM — auto-retry {retry_count + 1}/{MAX_OOM_RETRIES}"
                self._persist_locked()
                self._cv.notify()
            return

        result = rec.to_dict()
        if rec.outputs:
            result["output_name"] = f"{job_id}/{os.path.basename(rec.outputs[0])}"
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
            self._persist_locked()
            job_snapshot = dict(j) if rec.ok else None

        # Lineage edge per successful output (R98) — written after the job is durable so
        # the index only ever references a persisted result. Best-effort: a lineage write
        # failure must not fail an otherwise-good generation (the index is rebuildable).
        if job_snapshot is not None:
            try:
                lineage.record_output(ws, job_snapshot)
            except Exception as e:  # noqa: BLE001 - lineage is non-critical, never block output
                _warn(f"lineage write failed for {job_id}: {e}")


# Process-wide singleton.
RUNNER = JobRunner()
