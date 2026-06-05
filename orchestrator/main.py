"""Orchestrator app factory + handshake (M0) + generate/queue + grid (M1/M2).

Run (dev):
    python -m orchestrator.main        # prints its bound URL + token, then serves

M2: POST /generate enqueues an N-image batch onto the single-worker runner; the UI
polls /jobs and streams results into a grid; /outputs/<name> serves the PNGs.

M5: generation is **project-scoped**. `POST /project` (loom init) creates a workspace
on the work disk (empty-folder + free-space validated); the durable queue + outputs +
per-job logs live inside `<project>/`. `/generate` 409s until a project is open; the
last project re-opens on launch (queue resume-paused). `/project/estimate` projects the
PNG-master footprint to suggest a size cap.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Support both `python -m orchestrator.main` (package) and a direct run.
try:
    from .config import CONFIG
    from . import __version__, SCHEMA_VERSION
    from .adapters import JobSpec
    from .adapters import zimage as zimage_adapter
    from .runner import RUNNER, WORKER_REAP, estimate_vram
    from . import projects
    from . import workspace as ws_mod
    from . import components
    from .diskguard import DiskGuard
except ImportError:  # pragma: no cover - direct-run convenience
    from config import CONFIG  # type: ignore
    from adapters import JobSpec  # type: ignore
    from adapters import zimage as zimage_adapter  # type: ignore
    from runner import RUNNER, WORKER_REAP, estimate_vram  # type: ignore
    import projects  # type: ignore
    import workspace as ws_mod  # type: ignore
    import components  # type: ignore
    from diskguard import DiskGuard  # type: ignore
    __version__ = "0.0.1"
    SCHEMA_VERSION = 1


# Cached launch report (set by the gate at startup, refreshed after a fetch) so /health
# can report launch_ok without re-running the presence checks every probe.
_LAUNCH: dict = {"launch_ok": True}


_STARTED_AT = time.time()
MAX_BATCH = 8  # batch cap for the smoke grid (≤ R38's cap)

# Disk guard (M6, §9): reads the active workspace, wakes the runner when a hard-stop
# clears so dispatch-held jobs resume.
GUARD = DiskGuard(get_workspace=lambda: RUNNER.workspace, on_change=RUNNER.wake,
                  poll_s=CONFIG.disk_poll_s)


class GenerateRequest(BaseModel):
    """M2 generate payload — an N-image batch (count) fired into the grid.

    `extra="forbid"` so unknown/unsupported params 422 instead of being silently
    dropped (review #1). Only **t2i** is wired at P0; img2img/inpaint (with
    init_image/mask_image/strength + mode-specific validation) arrive in **P1**.
    """

    model_config = ConfigDict(extra="forbid")

    pipeline: str = "zimage"
    mode: Literal["t2i"] = "t2i"
    prompt: str
    count: int = Field(default=3, ge=1, le=MAX_BATCH)
    seed: int | None = None         # if set, image i uses seed+i; else random per image
    # Validated at the API boundary (review #2) so bad dims fail BEFORE a model load:
    # zimage requires width/height divisible by 16.
    width: int = Field(default=1280, ge=256, le=2048, multiple_of=16)
    height: int = Field(default=720, ge=256, le=2048, multiple_of=16)
    model_name: str | None = None
    num_steps: int | None = Field(default=None, ge=1, le=200)
    guidance_scale: float | None = Field(default=None, ge=0.0, le=30.0)
    negative_prompt: str | None = None
    dry_run: bool = False           # return the argv without running the GPU job (testing)

    @field_validator("prompt")
    @classmethod
    def _prompt_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("prompt must not be empty")
        return v


class CreateProjectRequest(BaseModel):
    """`loom init` payload (M5/P0-9). `dest` is the (empty) project folder; `format`
    defaults to Wan 1280×720 (R56) and `size_cap_gb` to 250 (R164) when omitted."""

    model_config = ConfigDict(extra="forbid")
    dest: str
    name: str
    format: dict | None = None
    size_cap_gb: float = Field(default=ws_mod.DEFAULT_SIZE_CAP_GB, ge=ws_mod.MIN_SIZE_CAP_GB)


class OpenProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class EstimateRequest(BaseModel):
    """Footprint estimator inputs (R161/R164): episode length × resolution × fps."""

    model_config = ConfigDict(extra="forbid")
    length_s: float = Field(ge=0)
    width: int = Field(default=1280, ge=16)
    height: int = Field(default=720, ge=16)
    fps: int = Field(default=24, ge=1)
    size_cap_gb: float | None = None


def require_token(x_loom_token: str | None = Header(default=None)) -> None:
    """Auth gate for mutating/expensive endpoints (review #1, R101 transport).

    The loopback bind already blocks off-machine callers; the token blocks *local*
    cross-site requests from spending GPU (the no-surprise-GPU posture, R141–143).
    """
    if x_loom_token != CONFIG.token:
        raise HTTPException(status_code=401, detail="missing or invalid X-Loom-Token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the worker, then re-open the last project (M5: the queue is per-project, so
    # the worker idles until a project is bound — resolve_startup binds the last one,
    # resume-paused). Then emit READY — lifespan startup runs AFTER uvicorn binds the
    # socket (review #3), so a port conflict fails before any false READY line.
    # Launch gate (M7, §11): refuse to start on a missing P0-essential CODE component
    # (clear error, no degraded mode). A missing P0-essential WEIGHT does not abort —
    # it's reported so the UI can offer an explicit HF fetch (R163).
    global _LAUNCH
    try:
        _LAUNCH = components.gate()
    except components.LaunchError as e:
        print(f"LOOM_ORCH_LAUNCH_REFUSED {e}", file=sys.stderr, flush=True)
        raise
    if not _LAUNCH.get("weights_ok", True):
        print(f"[loom] launch: P0 weights missing {_LAUNCH['weights_missing']} — "
              "fetch via POST /components/fetch before generating", file=sys.stderr, flush=True)

    RUNNER.start()
    projects.resolve_startup()
    # Disk guard (M6): gate the worker's dispatch on the hard-stop, then start polling.
    RUNNER.set_disk_gate(GUARD.is_hard_blocked)
    GUARD.start()
    print(f"LOOM_ORCH_READY url={CONFIG.base_url} token={CONFIG.token}", flush=True)
    yield
    # Graceful shutdown: stop the guard, then re-queue any running job + mark a clean stop
    # so a reload re-queues (not fails) it (R159 graceful branch). Runs on a clean uvicorn
    # stop; a hard kill skips this -> reload treats running jobs as a crash (-> failed).
    GUARD.stop()
    RUNNER.graceful_shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="Loreweave Studio orchestrator", version=__version__, lifespan=lifespan)

    # Restrict to known dev/Tauri origins (review #1) — was `*`. Defense-in-depth;
    # the token on /generate is the real gate.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CONFIG.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        """Liveness + identity. Unauthenticated so the sidecar can probe boot."""
        return {
            "status": "ok",
            "app_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "pid": os.getpid(),
            "uptime_s": round(time.time() - _STARTED_AT, 3),
            "launch_ok": _LAUNCH.get("launch_ok", True),
            "weights_ok": _LAUNCH.get("weights_ok", True),
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
            "cors_origins": CONFIG.cors_origins,
            "token_required": ["POST /generate", "POST /jobs/{id}/cancel",
                               "POST /queue/pause", "POST /queue/unpause",
                               "POST /project", "POST /project/open",
                               "POST /components/fetch", "POST /shutdown"],
            "worker_reap": WORKER_REAP,
            "work_disk_root": str(CONFIG.work_disk_root),
            "active_project": (str(RUNNER.workspace.path) if RUNNER.workspace else None),
        }

    @app.post("/generate")
    def generate(req: GenerateRequest, _auth: None = Depends(require_token)) -> dict:
        """Enqueue an N-image batch onto the single-worker runner (M2). Token-gated.
        Requires an **open project** (M5): outputs + queue are per-project."""
        if req.pipeline != "zimage":
            raise HTTPException(400, f"only the 'zimage' adapter is wired (got {req.pipeline!r})")
        script = zimage_adapter.resolve_script(CONFIG.pipeline_roots)
        if script is None:
            raise HTTPException(503, "zimage worker not found in any pipeline root "
                                     f"({[str(r) for r in CONFIG.pipeline_roots]})")

        ws = RUNNER.workspace
        if ws is None:
            raise HTTPException(409, "no project open — create or open a project first "
                                     "(POST /project or /project/open)")
        project = ws.load_project()

        # Launch-gate precondition (M7/§11.1): a P0-essential weight must be present —
        # offer the explicit fetch rather than failing the GPU run mid-flight.
        ok, missing = components.weights_ok()
        if not ok:
            raise HTTPException(412, f"required model weight(s) missing: {missing} — "
                                     "fetch via POST /components/fetch first")

        base = req.model_dump(exclude={"pipeline", "mode", "dry_run", "count"})

        if req.dry_run:
            spec = JobSpec(pipeline=req.pipeline, mode=req.mode, params=base,
                           output_dir=ws.out_dir)
            argv = zimage_adapter.build_argv(spec, CONFIG.venv_python, script)
            return {"dry_run": True, "count": req.count, "argv": argv,
                    "cwd": str(script.parents[2]), "output_dir": str(ws.out_dir)}

        # VRAM admission (§7) — enforce, don't just record (review #2): refuse a job
        # whose estimate exceeds the budget rather than queueing a guaranteed OOM.
        est = estimate_vram(req.pipeline)
        if est > CONFIG.vram_budget_gb:
            raise HTTPException(
                422, f"{req.pipeline} needs ~{est} GB VRAM > budget {CONFIG.vram_budget_gb} GB — "
                     f"reduce size/steps or raise LOOM_VRAM_BUDGET_GB")

        # Disk-guard admission (§9/R96): refuse to admit a space-consuming job under a
        # hard stop (running jobs finish). Resolve by raising the cap or freeing space.
        if GUARD.is_hard_blocked():
            raise HTTPException(507, f"disk hard-stop — {GUARD.block_reason()}; "
                                     "free space or raise the project size cap")

        batch_id = "bat_" + uuid.uuid4().hex[:8]
        job_ids: list[str] = []
        for i in range(req.count):
            params = dict(base)
            if req.seed is not None:
                params["seed"] = req.seed + i      # distinct but reproducible
            jid = RUNNER.submit(pipeline=req.pipeline, mode=req.mode, params=params,
                                batch_id=batch_id, index=i, batch_size=req.count,
                                requester_id=project["id"])   # lineage edge → project (R98)
            job_ids.append(jid)
        return {"batch_id": batch_id, "count": req.count, "job_ids": job_ids}

    @app.get("/project")
    def get_project() -> dict:
        """Active project info (or {open:false}). Unauthenticated read."""
        return projects.active_info()

    @app.post("/project")
    def create_project(req: CreateProjectRequest, _auth: None = Depends(require_token)) -> dict:
        """`loom init` — create a project workspace (empty-folder + free-space validated,
        R80) and open it. Token-gated."""
        try:
            info = projects.create_project(Path(req.dest), name=req.name, fmt=req.format,
                                           size_cap_gb=req.size_cap_gb)
            GUARD.refresh()   # re-measure now that a new project is active (don't wait for the poll)
            return info
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    @app.post("/project/open")
    def open_project(req: OpenProjectRequest, _auth: None = Depends(require_token)) -> dict:
        """Open an existing project; its queue resumes **paused** (R88). Token-gated."""
        try:
            info = projects.open_project(Path(req.path))
            GUARD.refresh()   # re-measure now that a different project is active
            return info
        except ws_mod.WorkspaceError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    @app.post("/project/estimate")
    def estimate_project(req: EstimateRequest) -> dict:
        """Footprint estimator (R161/R164): projected PNG-master size + suggested cap.
        Pure calculation — unauthenticated."""
        return ws_mod.footprint_report(length_s=req.length_s, width=req.width,
                                       height=req.height, fps=req.fps,
                                       size_cap_gb=req.size_cap_gb)

    @app.get("/jobs")
    def list_jobs() -> dict:
        st = RUNNER.state()
        return {"jobs": RUNNER.snapshot(), "counts": st["counts"],
                "paused": st["paused"], "vram_budget_gb": st["vram_budget_gb"],
                "disk": GUARD.status()}     # live usage + warn/hard for the dock (M6)

    @app.get("/disk")
    def disk() -> dict:
        """Live disk-guard status (two measures × two thresholds, §9). Unauthenticated read."""
        return GUARD.status()

    @app.get("/components")
    def get_components() -> dict:
        """Live launch report — phase-scoped 3-state component manifest (§11). The
        orchestrator only started if `code_ok`; `weights_ok=false` means the UI should
        offer a fetch. Unauthenticated read."""
        return components.launch_report()

    @app.post("/components/fetch")
    def fetch_components(_auth: None = Depends(require_token)) -> dict:
        """Explicit, on-demand fetch of missing active-phase weights from the manifest
        (R163, §11.1). Token-gated; never an auto-download. Refreshes the cached report."""
        global _LAUNCH
        res = components.fetch_missing_weights()
        _LAUNCH = res["report"]
        return res

    @app.post("/shutdown")
    def shutdown(_auth: None = Depends(require_token)) -> dict:
        """Graceful-shutdown handshake (P0-15): re-queue the in-flight job + mark a clean
        stop so a relaunch resumes it **paused/queued** (not failed). The Tauri shell calls
        this **before** hard-killing the sidecar on app exit, so the desktop 'quit mid-job'
        takes the R159 graceful branch. The process stays up (Tauri kills it next) — the
        durable state is already clean. Idempotent + token-gated."""
        GUARD.stop()
        RUNNER.graceful_shutdown()
        return {"stopped": True, "clean_shutdown": True}

    @app.post("/queue/pause")
    def queue_pause(_auth: None = Depends(require_token)) -> dict:
        RUNNER.pause()
        return RUNNER.state()

    @app.post("/queue/unpause")
    def queue_unpause(_auth: None = Depends(require_token)) -> dict:
        """Resume the GPU worker (the [unpause] control after a resume-paused load, R88)."""
        RUNNER.unpause()
        return RUNNER.state()

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        job = RUNNER.get(job_id)
        if job is None:
            raise HTTPException(404, f"no such job {job_id!r}")
        return job

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, _auth: None = Depends(require_token)) -> dict:
        """Cancel a queued/running job (cancel = subprocess kill, §8/§15). Token-gated."""
        if not RUNNER.cancel(job_id):
            raise HTTPException(409, f"job {job_id!r} is unknown or already finished")
        return {"job_id": job_id, "canceling": True}

    @app.get("/capabilities")
    def capabilities() -> dict:
        """Declared adapter contract — modes/params/presence (§8). Drives the UI."""
        return {"pipelines": {"zimage": zimage_adapter.capabilities(CONFIG.pipeline_roots)}}

    @app.get("/outputs/{name:path}")
    def get_output(name: str) -> FileResponse:
        """Serve a generated PNG from the **active project's** out/ dir, incl. per-job
        subdirs (M5). Traversal-guarded."""
        if ".." in name or "\\" in name:
            raise HTTPException(400, "invalid name")
        ws = RUNNER.workspace
        if ws is None:
            raise HTTPException(409, "no project open")
        base = ws.out_dir.resolve()
        path = (base / name).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise HTTPException(404, f"no such output {name!r}")
        return FileResponse(path)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    # READY is emitted from the lifespan startup (after the socket binds) so it is
    # the sidecar handshake contract only once the service is actually listening.
    uvicorn.run(app, host=CONFIG.host, port=CONFIG.port, log_level="info")


if __name__ == "__main__":
    main()
