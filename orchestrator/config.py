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


def _load_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser (KEY=VALUE; # comments; optional quotes/`export`).

    Dependency-free so the orchestrator needs no python-dotenv.
    """
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


# Central config (the user-chosen master config): committed `.env` for non-secret
# settings + gitignored `.env.local` for the dev token. Precedence: real env var >
# .env.local > .env > default. The Tauri shell still injects the token at runtime
# for the packaged app (this file path is for the orchestrator + dev).
_FILE_ENV: dict[str, str] = {}
for _name in (".env", ".env.local"):
    _FILE_ENV.update(_load_env_file(APP_REPO_ROOT / _name))


def _get(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, _FILE_ENV.get(key, default))


def _resolve_src_root() -> Path:
    """The parent monorepo `src/` (holds village_ai/models — weights live here, R160)."""
    env = _get("LOOM_SRC_ROOT")
    return Path(env).resolve() if env else (_MONOREPO_ROOT_GUESS / "src")


def _resolve_pipeline_roots() -> list[Path]:
    """Ordered pipeline-code roots: vendored in-repo first, parent monorepo fallback.

    Each entry directly contains the pipeline packages + `_artifact_id.py`.
    Prepend an explicit root via LOOM_PIPELINES_DIR.
    """
    roots: list[Path] = []
    env = _get("LOOM_PIPELINES_DIR")
    if env:
        roots.append(Path(env).resolve())
    roots.append(APP_REPO_ROOT / "pipelines")            # vendored flat (zimage; file-path invoked)
    # Vendored `multi` casting stack (P1/M2). `multi` is module-invoked
    # (`-m pipeline.multi.run_pipeline`) and its stage_runner self-locates flux2/sd35/
    # zimage + the flux2 lib by paths relative to its own file, so the vendored copy
    # mirrors the monorepo's `src/pipeline/` + sibling `flux2/src/` layout EXACTLY —
    # the registered root is the inner `…/src/pipeline` so parents[2] == `…/src` (the
    # cwd that makes the module import + the self-location resolve, unedited). This is
    # what makes a clone runnable for `multi` without the parent monorepo (R162).
    roots.append(APP_REPO_ROOT / "pipelines" / "multistack" / "src" / "pipeline")
    roots.append(_resolve_src_root() / "pipeline")       # parent monorepo (dev fallback)
    return roots


def _resolve_venv_python() -> str:
    """The interpreter used to shell out to pipeline CLIs (R103: reuse parent .venv).

    Configurable via LOOM_VENV_PYTHON; defaults to the interpreter currently
    running this orchestrator (so dev "just works" inside the shared .venv).
    """
    env = _get("LOOM_VENV_PYTHON")
    if env:
        return env
    return sys.executable


def _resolve_cors_origins() -> list[str]:
    """Allowed browser origins for the loopback API (was `*` — review finding #1).

    Defaults to the dev UI + the Tauri webview origins; override comma-separated
    via LOOM_CORS_ORIGINS. The token is the real auth gate on /generate; this is
    defense-in-depth so a random web page can't read /jobs etc.
    """
    env = _get("LOOM_CORS_ORIGINS")
    if env:
        return [o.strip() for o in env.split(",") if o.strip()]
    return [
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ]


@dataclass(frozen=True)
class Config:
    host: str = field(default_factory=lambda: _get("LOOM_ORCH_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_get("LOOM_ORCH_PORT", "8765")))
    # Loopback handshake token (R101). Enforced on mutating endpoints (/generate).
    # In the packaged app the Tauri shell reads it from the READY line and injects
    # it into the webview; in dev it comes from `.env.local` (LOOM_ORCH_TOKEN +
    # VITE_LOOM_ORCH_TOKEN). Falls back to a per-process random if unset.
    token: str = field(default_factory=lambda: _get("LOOM_ORCH_TOKEN") or secrets.token_urlsafe(24))
    cors_origins: list[str] = field(default_factory=_resolve_cors_origins)
    pipeline_roots: list[Path] = field(default_factory=_resolve_pipeline_roots)
    src_root: Path = field(default_factory=_resolve_src_root)
    venv_python: str = field(default_factory=_resolve_venv_python)
    monorepo_root: Path = field(default_factory=lambda: _MONOREPO_ROOT_GUESS)
    app_repo_root: Path = field(default_factory=lambda: APP_REPO_ROOT)
    # VRAM budget for admission (16 GB target rig — RX 9070 XT). Override LOOM_VRAM_BUDGET_GB.
    vram_budget_gb: float = field(default_factory=lambda: float(_get("LOOM_VRAM_BUDGET_GB", "16")))
    # Disk-guard poll cadence (M6, §9). Override LOOM_DISK_POLL_S.
    disk_poll_s: float = field(default_factory=lambda: float(_get("LOOM_DISK_POLL_S", "5")))

    @property
    def models_dir(self) -> Path:
        """Bulk model weights live OUTSIDE the app repo (R160), in the monorepo."""
        return self.src_root / "village_ai" / "models"

    @property
    def state_dir(self) -> Path:
        """**App-level** (cross-project) state — gitignored. Since M5 the durable queue
        + outputs live in the per-project workspace (`<project>/jobs/`, `<project>/out/`,
        R72); this dir now holds only app-wide state (the last-opened-project pointer,
        `app.json`). Override LOOM_STATE_DIR."""
        env = _get("LOOM_STATE_DIR")
        return Path(env).resolve() if env else (self.app_repo_root / ".loom_state")

    @property
    def log_level(self) -> str:
        """Logging verbosity (`brief`|`verbose`, or a standard level name). Override
        LOOM_LOG_LEVEL. `brief` = INFO lifecycle; `verbose` = DEBUG detail."""
        return _get("LOOM_LOG_LEVEL", "brief") or "brief"

    @property
    def log_dir(self) -> Path:
        """App-level log dir (gitignored): `.loom_state/logs/`."""
        return self.state_dir / "logs"

    @property
    def app_pointer_path(self) -> Path:
        """Cross-project pointer to the last-opened project, so a relaunch re-opens it
        (resume-paused). Lives outside any project (it records *which* project)."""
        return self.state_dir / "app.json"

    @property
    def work_disk_root(self) -> Path:
        """Default parent for new project workspaces — the **work disk** (R72). Projects
        are `<work_disk_root>/<name>/`. Override LOOM_WORK_DISK. `loom init` may target
        any folder; this is only the default the UI proposes."""
        env = _get("LOOM_WORK_DISK")
        if env:
            return Path(env).resolve()
        if sys.platform == "win32":
            return Path(r"F:\_tmp")
        return Path.home() / "LoreweaveProjects"

    @property
    def hf_home(self) -> Path:
        """Where Hugging Face weights are cached (the `hub/` cache lives here). Defaults to
        a **shared `loom-models/` dir on the work disk** (next to projects, off the system
        drive) rather than the buried `~/.cache/huggingface`. Override `LOOM_MODELS_DIR`.
        Weights are **shared across all loom projects** (R160 — never per-project, never in
        git): one ~330 GB casting set, not a copy per project. Set as `HF_HOME` for the
        orchestrator + every pipeline subprocess at startup."""
        env = _get("LOOM_MODELS_DIR")
        if env:
            return Path(env).resolve()
        wd = self.work_disk_root
        base = Path(wd.anchor) if wd.anchor else wd     # e.g. F:\  →  F:\loom-models
        return (base / "loom-models").resolve()

    @property
    def hf_token(self) -> str | None:
        """A Hugging Face token for gated-weight downloads (multi's flux2/sd3.5-large),
        read via the central loader (`.env.local` → `HF_TOKEN`, or a real env var). Kept
        out of git like the orchestrator token. `None` ⇒ only open/cached weights work."""
        return (_get("HF_TOKEN") or _get("HUGGING_FACE_HUB_TOKEN")
                or _get("HUGGINGFACE_HUB_TOKEN"))

    @property
    def active_phases_raw(self) -> str | None:
        """Raw `LOOM_ACTIVE_PHASES` (comma-separated phases the launch gate hard-requires),
        read through the **central loader** (real env > `.env.local` > `.env`) like every
        other setting — so editing the committed `.env` actually takes effect (review: this
        var previously bypassed the loader and only honored the process env). `None` ⇒ the
        launch gate falls back to its built-in default (`{P0, P1}`)."""
        return _get("LOOM_ACTIVE_PHASES")

    @property
    def project_dir_override(self) -> Path | None:
        """Optional forced project path (tests/CI/GPU verify): open (or create with
        defaults) this project at startup. Set LOOM_PROJECT_DIR."""
        env = _get("LOOM_PROJECT_DIR")
        return Path(env).resolve() if env else None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def dev_out_dir(self) -> Path:
        """LEGACY scratch dir (pre-M5). No longer the runtime output root — generations
        land in the per-project `<project>/out/`. Kept only as a fallback for `dry_run`
        argv display when no project is open. Override via LOOM_DEV_OUT."""
        env = _get("LOOM_DEV_OUT")
        return Path(env).resolve() if env else (self.app_repo_root / ".dev_out")


# Singleton config for the process.
CONFIG = Config()
