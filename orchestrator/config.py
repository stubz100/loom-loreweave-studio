"""Runtime configuration & path resolution (R101 transport, R103 interpreter/root).

Everything the orchestrator needs to know about *where it runs* lives here so it is
recorded, not assumed (P0-16). All values are overridable by environment variable
so the Tauri sidecar (later) can pin them at spawn time.

Resolution order for the pipelines/models root (§4): explicit env →
parent monorepo `src/` discovered by walking up from this file → cwd fallback.
During dev the app repo sits at `<monorepo>/loom/loom-loreweave-studio/`, so the
monorepo root is three parents up from this file.
"""

from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path


# This file: <monorepo>/loom/loom-loreweave-studio/orchestrator/config.py
#   parents[0] = orchestrator/
#   parents[1] = loom-loreweave-studio/   (the app repo root)
#   parents[2] = loom/
#   parents[3] = <monorepo> root          (holds src/, .venv/)
_THIS = Path(__file__).resolve()
APP_REPO_ROOT = _THIS.parents[1]
_MONOREPO_ROOT_GUESS = _THIS.parents[3]


def _resolve_pipelines_root() -> Path:
    """The monorepo `src/` that holds pipeline CLIs + village_ai/models (§4, R103).

    Configurable via LOOM_PIPELINES_ROOT; defaults to the parent monorepo's `src/`.
    """
    env = os.environ.get("LOOM_PIPELINES_ROOT")
    if env:
        return Path(env).resolve()
    guess = _MONOREPO_ROOT_GUESS / "src"
    if guess.is_dir():
        return guess
    return (Path.cwd() / "src").resolve()


def _resolve_venv_python() -> str:
    """The interpreter used to shell out to pipeline CLIs (R103: reuse parent .venv).

    Configurable via LOOM_VENV_PYTHON; defaults to the interpreter currently
    running this orchestrator (so dev "just works" inside the shared .venv).
    """
    env = os.environ.get("LOOM_VENV_PYTHON")
    if env:
        return env
    return sys.executable


@dataclass(frozen=True)
class Config:
    host: str = field(default_factory=lambda: os.environ.get("LOOM_ORCH_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("LOOM_ORCH_PORT", "8765")))
    # Loopback handshake token (R101). The Tauri shell generates this and passes it
    # to both the sidecar (env) and the UI; mutating endpoints will require it (M1+).
    token: str = field(default_factory=lambda: os.environ.get("LOOM_ORCH_TOKEN") or secrets.token_urlsafe(24))
    pipelines_root: Path = field(default_factory=_resolve_pipelines_root)
    venv_python: str = field(default_factory=_resolve_venv_python)
    monorepo_root: Path = field(default_factory=lambda: _MONOREPO_ROOT_GUESS)
    app_repo_root: Path = field(default_factory=lambda: APP_REPO_ROOT)

    @property
    def models_dir(self) -> Path:
        """Bulk model weights live OUTSIDE the app repo (R160), in the monorepo."""
        return self.pipelines_root / "village_ai" / "models"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def dev_out_dir(self) -> Path:
        """M1-only output dir for smoke generations. Replaced by the per-project
        workspace `out/` at M5 (R72); gitignored. Override via LOOM_DEV_OUT."""
        env = os.environ.get("LOOM_DEV_OUT")
        return Path(env).resolve() if env else (self.app_repo_root / ".dev_out")


# Singleton config for the process.
CONFIG = Config()
