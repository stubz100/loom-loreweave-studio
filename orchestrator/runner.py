"""In-memory job runner (M1/M2, Phase A) — single background worker, FIFO.

Naive on purpose: no persistence, no resume, no cancel, no VRAM admission. M4
replaces this with the durable, resume-*paused* `queue.py` (queue.json + single
GPU worker + cancel + VRAM-aware admission, R69/R78/R88). The invariant kept here
is the important one: **one job runs at a time** — never co-load two heavy models
on the 16 GB GPU (§7). The UI streams results by polling /jobs.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import uuid
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
                "created_at": _now(),
                "started_at": None,
                "finished_at": None,
                "wall_s": None,
                "result": None,
                "batch_id": batch_id,
                "index": index,
                "batch_size": batch_size,
            }
        self._q.put(job_id)
        return job_id

    def snapshot(self) -> dict:
        with self._lock:
            return {jid: dict(j) for jid, j in self.jobs.items()}

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            j = self.jobs.get(job_id)
            return dict(j) if j else None

    def counts(self) -> dict:
        c = {"queued": 0, "running": 0, "done": 0, "failed": 0}
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
                                       "stderr_tail": f"runner error: {e}"}
            finally:
                self._q.task_done()

    def _run_one(self, job_id: str) -> None:
        with self._lock:
            job = self.jobs[job_id]
            job["status"] = "running"
            job["started_at"] = _now()
            pipeline, mode, params = job["pipeline"], job["mode"], dict(job["params"])

        adapter = ADAPTERS[pipeline]
        out_dir = CONFIG.dev_out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        spec = JobSpec(pipeline=pipeline, mode=mode, params=params, output_dir=out_dir)
        argv = adapter.build_argv(spec, CONFIG.venv_python, CONFIG.pipelines_root)

        t0 = time.time()
        proc = subprocess.run(argv, cwd=str(CONFIG.monorepo_root), capture_output=True, text=True)
        rec = adapter.parse_result(proc.returncode, proc.stdout, proc.stderr, out_dir)
        result = rec.to_dict()
        # Enrich for the UI: a servable basename + the resolved seed (from manifest).
        if rec.outputs:
            result["output_name"] = os.path.basename(rec.outputs[0])
        if rec.manifest_path and Path(rec.manifest_path).is_file():
            try:
                m = json.loads(Path(rec.manifest_path).read_text(encoding="utf-8"))
                result["seed"] = m.get("seed")
            except (json.JSONDecodeError, OSError):
                pass

        with self._lock:
            job = self.jobs[job_id]
            job["status"] = "done" if rec.ok else "failed"
            job["result"] = result
            job["finished_at"] = _now()
            job["wall_s"] = round(time.time() - t0, 2)


# Process-wide singleton.
RUNNER = JobRunner()
