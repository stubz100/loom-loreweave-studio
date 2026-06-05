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
(M3) is preserved. Disk-guard gating is M6; per-project relocation is M5.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

try:
    from .adapters import JobSpec
    from .adapters import zimage as zimage_adapter
    from .config import CONFIG
except ImportError:  # pragma: no cover - direct-run convenience
    from adapters import JobSpec  # type: ignore
    from adapters import zimage as zimage_adapter  # type: ignore
    from config import CONFIG  # type: ignore

ADAPTERS = {"zimage": zimage_adapter}
SCHEMA_VERSION = 1

# Static per-pipeline VRAM estimate (GB) for admission (§7); refined by observed
# peaks later. zimage-turbo @720p with cpu_offload peaks ~10–12 GB on the 16 GB rig.
VRAM_ESTIMATES = {"zimage": 11.0}
DEFAULT_VRAM_GB = 8.0
MAX_OOM_RETRIES = 1
_OOM_MARKERS = (
    "out of memory", "hip out of memory", "cuda out of memory",
    "outofmemoryerror", "hiperror: out of memory",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_oom(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _OOM_MARKERS)


class JobRunner:
    """Single-worker FIFO over a durable job table."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._procs: dict[str, subprocess.Popen] = {}
        self._canceled: set[str] = set()
        self._paused = False
        self._worker = threading.Thread(target=self._run_loop, name="loom-gpu-worker", daemon=True)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._load()
        self._worker.start()

    # --- persistence ------------------------------------------------------
    def _persist_locked(self, clean_shutdown: bool = False) -> None:
        data = {
            "schema_version": SCHEMA_VERSION,
            "paused": self._paused,
            "clean_shutdown": clean_shutdown,
            "jobs": self.jobs,
        }
        path = CONFIG.queue_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic

    def _discard_partial(self, job_id: str) -> None:
        shutil.rmtree(CONFIG.dev_out_dir / job_id, ignore_errors=True)

    def _load(self) -> None:
        path = CONFIG.queue_path
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        jobs = data.get("jobs") or {}
        clean = bool(data.get("clean_shutdown"))
        with self._lock:
            self.jobs = jobs
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
        """From the lifespan shutdown: re-queue running jobs + mark a clean stop so a
        reload re-queues (not fails) them (R159 graceful branch)."""
        with self._lock:
            for j in self.jobs.values():
                if j.get("status") == "running" and not j.get("resumable"):
                    j["status"] = "queued"
                    j["progress"] = 0.0
                    j["note"] = "re-queued at graceful shutdown"
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
        job_id = "job_" + uuid.uuid4().hex[:8]
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
                while self._paused or self._next_queued_id_locked() is None:
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

        out_dir = CONFIG.dev_out_dir / job_id   # per-job isolation (M3)
        out_dir.mkdir(parents=True, exist_ok=True)
        spec = JobSpec(pipeline=pipeline, mode=mode, params=params, output_dir=out_dir)
        argv = adapter.build_argv(spec, CONFIG.venv_python, script)

        t0 = time.time()
        proc = subprocess.Popen(
            argv, cwd=str(script.parents[2]),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        with self._lock:
            self._procs[job_id] = proc

        tail: deque[str] = deque(maxlen=60)
        if proc.stdout is not None:
            for line in proc.stdout:
                line = line.rstrip("\n")
                tail.append(line)
                pr = adapter.progress(line)
                if pr is not None:
                    with self._lock:
                        j = self.jobs.get(job_id)
                        if j:
                            j["progress"] = pr
                            j["log_tail"] = "\n".join(tail)
        proc.wait()
        rc = proc.returncode
        log_text = "\n".join(tail)
        with self._lock:
            self._procs.pop(job_id, None)
            canceled = job_id in self._canceled
            retry_count = self.jobs[job_id].get("retry_count", 0)

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

        # Capped auto-retry on OOM (§7) — visible via the note.
        if (not rec.ok and not canceled and retry_count < MAX_OOM_RETRIES
                and _is_oom((rec.error or "") + " " + (rec.stderr_tail or ""))):
            self._discard_partial(job_id)
            with self._cv:
                j = self.jobs[job_id]
                j["status"] = "queued"
                j["retry_count"] = retry_count + 1
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


# Process-wide singleton.
RUNNER = JobRunner()
