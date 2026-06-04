"""Orchestrator app factory + handshake (M0) + generate/queue + grid (M1/M2).

Run (dev):
    python -m orchestrator.main        # prints its bound URL + token, then serves

M2: POST /generate enqueues an N-image batch onto the single-worker in-memory
runner; the UI polls /jobs and streams results into a grid; /outputs/<name>
serves the PNGs. Durable queue + cancel + VRAM admission is M4.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# Support both `python -m orchestrator.main` (package) and a direct run.
try:
    from .config import CONFIG
    from . import __version__, SCHEMA_VERSION
    from .adapters import JobSpec
    from .adapters import zimage as zimage_adapter
    from .runner import RUNNER
except ImportError:  # pragma: no cover - direct-run convenience
    from config import CONFIG  # type: ignore
    from adapters import JobSpec  # type: ignore
    from adapters import zimage as zimage_adapter  # type: ignore
    from runner import RUNNER  # type: ignore
    __version__ = "0.0.1"
    SCHEMA_VERSION = 1


_STARTED_AT = time.time()
MAX_BATCH = 8  # batch cap for the smoke grid (≤ R38's cap)


class GenerateRequest(BaseModel):
    """M2 generate payload — an N-image batch (count) fired into the grid."""

    pipeline: str = "zimage"
    mode: str = "t2i"
    prompt: str
    count: int = Field(default=3, ge=1, le=MAX_BATCH)
    seed: int | None = None         # if set, image i uses seed+i; else random per image
    width: int = 1280
    height: int = 720
    model_name: str | None = None
    num_steps: int | None = None
    guidance_scale: float | None = None
    negative_prompt: str | None = None
    dry_run: bool = False           # return the argv without running the GPU job (testing)


def create_app() -> FastAPI:
    app = FastAPI(title="Loreweave Studio orchestrator", version=__version__)

    # Loopback-only service; allow the dev UI (localhost:1420) + Tauri webview to
    # fetch /jobs etc. cross-origin. (Token enforcement is P0-16.)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    RUNNER.start()

    @app.get("/health")
    def health() -> dict:
        """Liveness + identity. Unauthenticated so the sidecar can probe boot."""
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
            "pipeline_roots": [str(r) for r in CONFIG.pipeline_roots],
            "zimage_worker": str(zimage_adapter.resolve_script(CONFIG.pipeline_roots) or ""),
            "models_dir": str(CONFIG.models_dir),
        }

    @app.post("/generate")
    def generate(req: GenerateRequest) -> dict:
        """Enqueue an N-image batch onto the single-worker runner (M2)."""
        if req.pipeline != "zimage":
            raise HTTPException(400, f"only the 'zimage' adapter is wired (got {req.pipeline!r})")
        script = zimage_adapter.resolve_script(CONFIG.pipeline_roots)
        if script is None:
            raise HTTPException(503, "zimage worker not found in any pipeline root "
                                     f"({[str(r) for r in CONFIG.pipeline_roots]})")

        base = req.model_dump(exclude={"pipeline", "mode", "dry_run", "count"})

        if req.dry_run:
            spec = JobSpec(pipeline=req.pipeline, mode=req.mode, params=base,
                           output_dir=CONFIG.dev_out_dir)
            argv = zimage_adapter.build_argv(spec, CONFIG.venv_python, script)
            return {"dry_run": True, "count": req.count, "argv": argv,
                    "cwd": str(script.parents[2]), "output_dir": str(CONFIG.dev_out_dir)}

        batch_id = "bat_" + uuid.uuid4().hex[:8]
        job_ids: list[str] = []
        for i in range(req.count):
            params = dict(base)
            if req.seed is not None:
                params["seed"] = req.seed + i      # distinct but reproducible
            jid = RUNNER.submit(pipeline=req.pipeline, mode=req.mode, params=params,
                                batch_id=batch_id, index=i, batch_size=req.count)
            job_ids.append(jid)
        return {"batch_id": batch_id, "count": req.count, "job_ids": job_ids}

    @app.get("/jobs")
    def list_jobs() -> dict:
        return {"jobs": RUNNER.snapshot(), "counts": RUNNER.counts()}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        job = RUNNER.get(job_id)
        if job is None:
            raise HTTPException(404, f"no such job {job_id!r}")
        return job

    @app.get("/outputs/{name}")
    def get_output(name: str) -> FileResponse:
        """Serve a generated PNG from the dev-out dir (M5: per-project out/)."""
        if "/" in name or "\\" in name or ".." in name:
            raise HTTPException(400, "invalid name")
        path = (CONFIG.dev_out_dir / name).resolve()
        if path.parent != CONFIG.dev_out_dir.resolve() or not path.is_file():
            raise HTTPException(404, f"no such output {name!r}")
        return FileResponse(path)

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
