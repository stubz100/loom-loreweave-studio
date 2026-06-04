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
import sys
import time

from fastapi import FastAPI

# Support both `python -m orchestrator.main` (package) and a direct run.
try:
    from .config import CONFIG
    from . import __version__, SCHEMA_VERSION
except ImportError:  # pragma: no cover - direct-run convenience
    from config import CONFIG  # type: ignore
    __version__ = "0.0.1"
    SCHEMA_VERSION = 1


_STARTED_AT = time.time()


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
