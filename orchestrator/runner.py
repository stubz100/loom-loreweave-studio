"""In-memory job runner (M1/M2 + M3 contract) — single background worker, FIFO.

Naive on persistence (no durable queue/resume — that's M4), but M3 adds the real
adapter-contract behaviours: a per-job **isolated output dir**, **coarse progress**
streamed from the worker's stdout, a captured **log tail**, and **cancellation =
subprocess terminate/kill**. The invariant stays: **one job at a time** — never
co-load two heavy models on the 16 GB GPU (§7). The UI streams by polling /jobs.
"""

from __future__ import annotations

import json
import os
import queue
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

# pipeline name -> adapter module (M1: only zimage; others onboard per phase, §8)
ADAPTERS = {"zimage": zimage_adapter}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobRunner:
    """A single-worker FIFO runner over an in-memory job table."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self._q: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}   # running job -> process (for cancel)
        self._canceled: set[str] = set()
        self._worker = threading.Thread(target=self._run_loop, name="loom-gpu-worker", daemon=True)
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._worker.start()

    def submit(self, *, pipeline: str, mode: str, params: dict,
               batch_id: str, index: int, batch_size: int) -> str:
        job_id = "job_" + uuid.uuid4().hex[:8]
        with self._lock:
            self.jobs[job_id] = {
                "id": job_id,
                "pipeline": pipeline,
                "mode": mode,
                "params": params,
                "status": "queued",
                "progress": 0.0,
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
        self._q.put(job_id)
        return job_id

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued or running job (review/contract: cancel = subprocess kill)."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None or job["status"] in ("done", "failed", "canceled"):
                return False
            self._canceled.add(job_id)
            proc = self._procs.get(job_id)
            if job["status"] == "queued":
                job["status"] = "canceled"
                job["finished_at"] = _now()
        if proc is not None:
            proc.terminate()  # running -> the worker loop finalizes it as canceled
        return True

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

    # --- worker -----------------------------------------------------------
    def _run_loop(self) -> None:
        while True:
            job_id = self._q.get()
            try:
                self._run_one(job_id)
            except Exception as e:  # never let the worker thread die
                with self._lock:
                    j = self.jobs.get(job_id)
                    if j:
                        j["status"] = "failed"
                        j["finished_at"] = _now()
                        j["result"] = {"ok": False, "returncode": -1, "outputs": [],
                                       "error": f"runner error: {e}", "stderr_tail": str(e)}
            finally:
                self._q.task_done()

    def _run_one(self, job_id: str) -> None:
        with self._lock:
            job = self.jobs[job_id]
            if job_id in self._canceled:           # canceled while queued -> skip
                job["status"] = "canceled"
                job["finished_at"] = _now()
                return
            job["status"] = "running"
            job["started_at"] = _now()
            pipeline, mode, params = job["pipeline"], job["mode"], dict(job["params"])

        adapter = ADAPTERS[pipeline]
        script = adapter.resolve_script(CONFIG.pipeline_roots)
        if script is None:
            raise RuntimeError(f"{pipeline} worker not found in any pipeline root")

        # Per-job isolated output dir (M3 / review #4): the manifest in this dir is
        # unambiguously THIS job's, so parse_result can't misattribute a stale PNG.
        out_dir = CONFIG.dev_out_dir / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        spec = JobSpec(pipeline=pipeline, mode=mode, params=params, output_dir=out_dir)
        argv = adapter.build_argv(spec, CONFIG.venv_python, script)

        t0 = time.time()
        # stderr merged into stdout so the worker's stage prints + tqdm share one
        # stream we can read line-by-line for coarse progress + a log tail.
        proc = subprocess.Popen(
            argv, cwd=str(script.parents[2]),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        with self._lock:
            self._procs[job_id] = proc

        tail: deque[str] = deque(maxlen=60)
        if proc.stdout is not None:
            for line in proc.stdout:
                line = line.rstrip("\n")
                tail.append(line)
                pr = adapter.progress(line)
                with self._lock:
                    j = self.jobs.get(job_id)
                    if j:
                        if pr is not None:
                            j["progress"] = pr
                        j["log_tail"] = "\n".join(tail)
        proc.wait()
        rc = proc.returncode
        log_text = "\n".join(tail)
        with self._lock:
            self._procs.pop(job_id, None)
            canceled = job_id in self._canceled

        if canceled:
            shutil.rmtree(out_dir, ignore_errors=True)   # drop partial output
            with self._lock:
                j = self.jobs[job_id]
                j["status"] = "canceled"
                j["finished_at"] = _now()
                j["wall_s"] = round(time.time() - t0, 2)
                j["log_tail"] = log_text
            return

        rec = adapter.parse_result(rc, log_text, "", out_dir)
        result = rec.to_dict()
        if rec.outputs:
            # served via /outputs/<job_id>/<file> (per-job dir)
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


# Process-wide singleton.
RUNNER = JobRunner()
