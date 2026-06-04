"""Runtime configuration & path resolution (R101 transport, R103 interpreter/root).

Everything the orchestrator needs to know about *where it runs* lives here so it is
recorded, not assumed (P0-16). All values are overridable by environment variable
so the Tauri sidecar (later) can pin them at spawn time.

Pipeline code resolution (R162 + per-phase vendoring): a pipeline worker is looked
up across an ordered list of **pipeline roots** — the **in-repo vendored** copy
(`<app repo>/pipelines/`) is preferred, with the **parent monorepo**
(`<monorepo>/src/pipeline/`) as a dev fallback. Each "root" is the directory that
*directly* contains the pipeline packages (e.g. `zimage/`) and the shared
`_artifact_id.py`. This lets a clone run on its own for whatever pipelines have been
vendored, while non-vendored ones still resolve against the parent during dev.
Model **weights** are separate (never vendored, R160) and resolved under `src_root`.
"""

from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path


# This file: <monorepo>/loom/loom-loreweave-studio/orchestrator/config.py
#   parents[1] = loom-loreweave-studio/   (the app repo root)
#   parents[3] = <monorepo> root          (holds src/, .venv/)
_THIS = Path(__file__).resolve()
APP_REPO_ROOT = _THIS.parents[1]
_MONOREPO_ROOT_GUESS = _THIS.parents[3]


def _resolve_src_root() -> Path:
    """The parent monorepo `src/` (holds village_ai/models — weights live here, R160)."""
    env = os.environ.get("LOOM_SRC_ROOT")
    return Path(env).resolve() if env else (_MONOREPO_ROOT_GUESS / "src")


def _resolve_pipeline_roots() -> list[Path]:
    """Ordered pipeline-code roots: vendored in-repo first, parent monorepo fallback.

    Each entry directly contains the pipeline packages + `_artifact_id.py`.
    Prepend an explicit root via LOOM_PIPELINES_DIR.
    """
    roots: list[Path] = []
    env = os.environ.get("LOOM_PIPELINES_DIR")
    if env:
        roots.append(Path(env).resolve())
    roots.append(APP_REPO_ROOT / "pipelines")            # vendored (preferred)
    roots.append(_resolve_src_root() / "pipeline")       # parent monorepo (dev fallback)
    return roots


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
    pipeline_roots: list[Path] = field(default_factory=_resolve_pipeline_roots)
    src_root: Path = field(default_factory=_resolve_src_root)
    venv_python: str = field(default_factory=_resolve_venv_python)
    monorepo_root: Path = field(default_factory=lambda: _MONOREPO_ROOT_GUESS)
    app_repo_root: Path = field(default_factory=lambda: APP_REPO_ROOT)

    @property
    def models_dir(self) -> Path:
        """Bulk model weights live OUTSIDE the app repo (R160), in the monorepo."""
        return self.src_root / "village_ai" / "models"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def dev_out_dir(self) -> Path:
        """M1/M2 output dir for smoke generations. Replaced by the per-project
        workspace `out/` at M5 (R72); gitignored. Override via LOOM_DEV_OUT."""
        env = os.environ.get("LOOM_DEV_OUT")
        return Path(env).resolve() if env else (self.app_repo_root / ".dev_out")


# Singleton config for the process.
CONFIG = Config()
