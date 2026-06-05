"""Disk guard (M6, P0-10 / §9, R96) — continuously-polled space safety.

Two measures × two thresholds (§9):

    | Measure              | Source                               | Warn   | Hard stop |
    | -------------------- | ------------------------------------ | ------ | --------- |
    | Project-cap headroom | project folder size vs `size_cap_gb` | <5%    | <2%       |
    | Disk free space      | work-disk free vs total              | <5%    | <2%       |

R96 **reverses the earlier validate-only stance**: instead of only checking free ≥ cap
at `loom init`, a background thread polls **continuously during work** and caches the
latest status. A **hard stop blocks admitting new space-consuming jobs** (the worker
also won't *start* a queued job under hard-stop) — but **running jobs finish**. Resolve
by raising the cap or freeing space (manual; no project manager in v1, R80).

The guard owns no other state: it reads the active workspace via an injected getter and
wakes the runner (so held jobs resume the moment space frees) via an injected callback —
keeping `runner.py` free of a disk-guard import.
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import Callable

try:
    from .config import CONFIG
    from .workspace import Workspace
    from .logsetup import get_logger
except ImportError:  # pragma: no cover - direct-run convenience
    from config import CONFIG  # type: ignore
    from workspace import Workspace  # type: ignore
    from logsetup import get_logger  # type: ignore

_GB = 1024 ** 3

WARN_PCT = 5.0   # <5% headroom/free → warn
HARD_PCT = 2.0   # <2% headroom/free → hard stop (block admission + dispatch)
DEFAULT_POLL_S = 5.0
LOG = get_logger()


def _warn(msg: str) -> None:
    LOG.warning(msg)


def _dir_size_bytes(root: Path) -> int:
    """Sum of file sizes under `root` (best-effort; tolerates files vanishing mid-walk).

    P0 walks the tree each poll — fine for small projects; an incremental accountant is a
    later refinement once PNG-sequence masters make projects large (noted in the journal)."""
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            try:
                total += os.stat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass  # raced deletion / permission — skip
    return total


class DiskGuard:
    def __init__(self, get_workspace: Callable[[], Workspace | None],
                 on_change: Callable[[], None] | None = None,
                 poll_s: float = DEFAULT_POLL_S) -> None:
        self._get_ws = get_workspace
        self._on_change = on_change
        self._poll_s = poll_s
        self._lock = threading.Lock()
        self._status: dict = self._idle_status()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle --------------------------------------------------------
    def start(self) -> None:
        """Seed the status and (re)start the poll thread. Restartable in-process: if a
        previous `stop()` ended the thread, this clears the stop event and spins up a
        fresh one (review: a second lifespan must not leave the poller dead)."""
        self.refresh()  # seed before serving the first request
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="loom-disk-guard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the poll thread to exit and join it, so a later `start()` begins clean."""
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=self._poll_s + 1.0)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self._poll_s):
            prev = self.is_hard_blocked()
            self.refresh()
            # Wake the runner when the hard-stop CLEARS, so jobs held at dispatch resume
            # the instant space frees (or the cap is raised).
            if prev and not self.is_hard_blocked() and self._on_change is not None:
                try:
                    self._on_change()
                except Exception as e:  # noqa: BLE001
                    _warn(f"disk-guard wake callback failed: {e}")

    # --- measurement ------------------------------------------------------
    def _idle_status(self) -> dict:
        return {"state": "ok", "blocked": False, "reason": None, "project": None,
                "disk": None, "thresholds": {"warn_pct": WARN_PCT, "hard_pct": HARD_PCT}}

    def refresh(self) -> dict:
        """Recompute the status for the active workspace + work disk and cache it."""
        ws = self._get_ws()
        status = self._idle_status()
        worst = "ok"
        reasons: list[str] = []

        # Disk free vs total (cheap) — measured on the active project's disk, else the
        # default work-disk root (probing the nearest existing parent).
        probe = ws.path if ws is not None else CONFIG.work_disk_root
        p = probe
        while not p.exists() and p.parent != p:
            p = p.parent
        try:
            du = shutil.disk_usage(p)
            free_pct = (du.free / du.total * 100.0) if du.total else 100.0
            status["disk"] = {"free_gb": round(du.free / _GB, 1),
                              "total_gb": round(du.total / _GB, 1),
                              "free_pct": round(free_pct, 2)}
            if free_pct < HARD_PCT:
                worst = "hard"; reasons.append(f"disk {free_pct:.1f}% free (<{HARD_PCT}%)")
            elif free_pct < WARN_PCT:
                worst = _max_state(worst, "warn"); reasons.append(f"disk {free_pct:.1f}% free")
        except OSError as e:
            _warn(f"disk-guard could not stat {p}: {e}")

        # Project-cap headroom (walks the project tree).
        if ws is not None:
            try:
                project = ws.load_project()
                cap_gb = float(project.get("size_cap_gb") or 0)
                used_gb = _dir_size_bytes(ws.path) / _GB
                headroom_pct = ((cap_gb - used_gb) / cap_gb * 100.0) if cap_gb else 0.0
                status["project"] = {"used_gb": round(used_gb, 2), "cap_gb": cap_gb,
                                     "headroom_pct": round(headroom_pct, 2)}
                if headroom_pct < HARD_PCT:
                    worst = "hard"
                    reasons.append(f"project {headroom_pct:.1f}% under cap (<{HARD_PCT}%)")
                elif headroom_pct < WARN_PCT:
                    worst = _max_state(worst, "warn")
                    reasons.append(f"project {headroom_pct:.1f}% under cap")
            except Exception as e:  # noqa: BLE001 - never let a bad project crash the guard
                _warn(f"disk-guard could not size project {ws.path}: {e}")

        status["state"] = worst
        status["blocked"] = worst == "hard"
        status["reason"] = "; ".join(reasons) if reasons else None
        with self._lock:
            prev = self._status.get("state")
            self._status = status
        if worst != prev:   # log only transitions at INFO (avoid per-poll spam)
            if worst == "hard":
                LOG.warning("disk HARD-STOP — %s", status["reason"])
            elif worst == "warn":
                LOG.info("disk warning — %s", status["reason"])
            else:
                LOG.info("disk OK (recovered from %s)", prev)
        else:
            LOG.debug("disk %s (free=%s%%, proj_headroom=%s%%)", worst,
                      (status["disk"] or {}).get("free_pct"),
                      (status["project"] or {}).get("headroom_pct"))
        return status

    # --- read views -------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def is_hard_blocked(self) -> bool:
        with self._lock:
            return bool(self._status.get("blocked"))

    def block_reason(self) -> str | None:
        with self._lock:
            return self._status.get("reason")


def _max_state(a: str, b: str) -> str:
    order = {"ok": 0, "warn": 1, "hard": 2}
    return a if order[a] >= order[b] else b
