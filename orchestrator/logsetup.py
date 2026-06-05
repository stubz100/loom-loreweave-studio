"""Central logging for the orchestrator (user request — `.env`-configurable).

One `loom` logger, configured once at startup, fanning out to **stderr** (so it shows
in the `npm run tauri dev` terminal next to uvicorn) and a **rotating file** under the
app-level state dir (`.loom_state/logs/orchestrator.log`, gitignored). The level comes
from `.env` via `LOOM_LOG_LEVEL`:

- **`brief`** (default) → INFO: lifecycle milestones (boot, launch gate, project
  open/create, job submit/start/done/fail, disk warn/hard, shutdown) + warnings/errors.
- **`verbose`** → DEBUG: the above plus per-tick detail (disk refreshes, dispatch waits,
  persistence) for diagnosing the "complicated deliveries".

Standard level names (`debug`/`info`/`warning`/`error`) are also accepted. Modules get
the shared logger via `get_logger()` and never configure handlers themselves.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "loom"
_ALIASES = {"brief": logging.INFO, "verbose": logging.DEBUG}
_configured = False


def resolve_level(level: str | None) -> int:
    """Map a config string (`brief`/`verbose` or a standard level name) → logging level."""
    if not level:
        return logging.INFO
    key = level.strip().lower()
    if key in _ALIASES:
        return _ALIASES[key]
    return getattr(logging, key.upper(), logging.INFO)


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def configure(level: str | None, log_dir: Path | None) -> logging.Logger:
    """Configure the `loom` logger once (idempotent). Returns it."""
    global _configured
    log = logging.getLogger(_LOGGER_NAME)
    lvl = resolve_level(level)
    log.setLevel(lvl)
    if _configured:
        for h in log.handlers:
            h.setLevel(lvl)
        log.debug("logging level set to %s", logging.getLevelName(lvl))
        return log

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [loom] %(message)s",
                            datefmt="%H:%M:%S")
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(fmt)
    log.addHandler(stderr)

    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            fileh = RotatingFileHandler(log_dir / "orchestrator.log", maxBytes=2_000_000,
                                        backupCount=3, encoding="utf-8")
            fileh.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-7s [loom] %(message)s"))
            log.addHandler(fileh)
        except OSError as e:
            log.warning("could not open log file in %s: %s", log_dir, e)

    log.propagate = False  # don't double-log through the root logger
    _configured = True
    log.info("logging configured (level=%s, dir=%s)", logging.getLevelName(lvl), log_dir)
    return log
