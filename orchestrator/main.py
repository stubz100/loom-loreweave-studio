"""Orchestrator app factory + the M0 health handshake (R101).

Run (dev):
    python -m uvicorn orchestrator.main:app --host 127.0.0.1 --port 8765
or:
    python -m orchestrator.main        # prints its bound URL + token, then serves

M0 scope: only the handshake endpoints. The sidecar (Tauri, later) spawns this
process, reads the READY line from stdout to learn the URL, and probes /health.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Support both `python -m orchestrator.main` (package) and a direct run.
try:
    from .config import CONFIG
    from . import __version__, SCHEMA_VERSION
    from .adapters import JobSpec
    from .adapters import zimage as zimage_adapter
except ImportError:  # pragma: no cover - direct-run convenience
    from config import CONFIG  # type: ignore
    from adapters import JobSpec  # type: ignore
    from adapters import zimage as zimage_adapter  # type: ignore
    __version__ = "0.0.1"
    SCHEMA_VERSION = 1


_STARTED_AT = time.time()

# M1: in-memory job registry (naive — replaced by the durable queue.json at M4).
JOBS: dict[str, dict] = {}


class GenerateRequest(BaseModel):
    """M1 generate payload. One image, synchronous (the N-image grid is M2)."""

    pipeline: str = "zimage"
    mode: str = "t2i"
    prompt: str
    seed: int | None = None
    width: int = 1280
    height: int = 720
    model_name: str | None = None
    num_steps: int | None = None
    guidance_scale: float | None = None
    negative_prompt: str | None = None
    dry_run: bool = False  # return the argv without running the GPU job (testing)


def create_app() -> FastAPI:
    app = FastAPI(title="Loreweave Studio orchestrator", version=__version__)

    @app.get("/health")
    def health() -> dict:
        """Liveness + identity. Unauthenticated so the sidecar can probe boot.

        Mutating endpoints (M1+) will require the X-Loom-Token header (R101, P0-16).
        """
        return {
            "status": "ok",
            "app_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "pid": os.getpid(),
            "uptime_s": round(time.time() - _STARTED_AT, 3),
        }

    @app.get("/version")
    def version() -> dict:
        """Resolved runtime facts — recorded, not assumed (P0-16, R103)."""
        return {
            "app_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "python": sys.version.split()[0],
            "venv_python": CONFIG.venv_python,
            "pipelines_root": str(CONFIG.pipelines_root),
            "models_dir": str(CONFIG.models_dir),
            "pipelines_root_exists": CONFIG.pipelines_root.is_dir(),
        }

    @app.post("/generate")
    def generate(req: GenerateRequest) -> dict:
        """M1 smoke path: typed params → zimage adapter → subprocess → envelope.

        Synchronous + in-memory (naive). The persistent, resume-paused queue with
        a single GPU worker is M4; the N-image batch grid is M2.
        """
        if req.pipeline != "zimage":
            raise HTTPException(400, f"M1 supports only the 'zimage' adapter (got {req.pipeline!r})")
        if not zimage_adapter.present(CONFIG.pipelines_root):
            raise HTTPException(503, f"zimage worker not found under {CONFIG.pipelines_root}")

        out_dir = CONFIG.dev_out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        params = req.model_dump(exclude={"pipeline", "mode", "dry_run"})
        spec = JobSpec(pipeline=req.pipeline, mode=req.mode, params=params, output_dir=out_dir)
        argv = zimage_adapter.build_argv(spec, CONFIG.venv_python, CONFIG.pipelines_root)

        if req.dry_run:
            return {"dry_run": True, "argv": argv, "cwd": str(CONFIG.monorepo_root), "output_dir": str(out_dir)}

        job_id = "job_" + uuid.uuid4().hex[:8]
        JOBS[job_id] = {"status": "running", "pipeline": req.pipeline, "mode": req.mode, "params": params}
        t0 = time.time()
        proc = subprocess.run(argv, cwd=str(CONFIG.monorepo_root), capture_output=True, text=True)
        rec = zimage_adapter.parse_result(proc.returncode, proc.stdout, proc.stderr, out_dir)
        JOBS[job_id].update({
            "status": "done" if rec.ok else "failed",
            "result": rec.to_dict(),
            "wall_s": round(time.time() - t0, 2),
        })
        return {"job_id": job_id, "wall_s": JOBS[job_id]["wall_s"], **rec.to_dict()}

    @app.get("/jobs")
    def list_jobs() -> dict:
        return {"jobs": JOBS}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        if job_id not in JOBS:
            raise HTTPException(404, f"no such job {job_id!r}")
        return JOBS[job_id]

    return app


app = create_app()


def main() -> None:
    import uvicorn

    # The READY line is the sidecar handshake contract: a single parseable stdout
    # line carrying the base URL + token. Keep the prefix stable.
    print(f"LOOM_ORCH_READY url={CONFIG.base_url} token={CONFIG.token}", flush=True)
    uvicorn.run(app, host=CONFIG.host, port=CONFIG.port, log_level="info")


if __name__ == "__main__":
    main()
