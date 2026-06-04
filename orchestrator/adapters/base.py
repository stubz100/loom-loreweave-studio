"""Shared adapter types — the typed job in, the normalized envelope out (§8).

Kept deliberately small for M1. M3 promotes this to the full contract
(capabilities()/presence, coarse progress, cancel=kill, manifest-status-as-truth).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class JobSpec:
    """One unit of work the orchestrator hands an adapter."""

    pipeline: str                 # e.g. "zimage"
    mode: str                     # e.g. "t2i" | "img2img" | "inpaint"
    params: dict                  # pipeline-specific params (prompt, seed, w/h, …)
    output_dir: Path              # ABSOLUTE dir the worker writes PNG + manifest into


@dataclass
class CompletionRecord:
    """Normalized worker result — the common envelope (§8).

    `ok` is the source of truth for success; M1 derives it from the exit code,
    M3 will cross-check the saved manifest's stage status (belt-and-suspenders).
    """

    ok: bool
    returncode: int
    outputs: list[str] = field(default_factory=list)   # absolute output file paths (PNGs)
    manifest_path: str | None = None                   # sidecar <output>.json
    duration_s: float | None = None
    peak_vram_gb: float | None = None                  # M3+ (not emitted up-front yet)
    stderr_tail: str = ""                              # last lines of output for post-mortem
    # M3: success is decided by the manifest's stage status (manifest-as-truth),
    # cross-checked with the exit code. `error` carries the failing-stage message.
    manifest_status: str | None = None                 # "completed" | "failed" | None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "outputs": self.outputs,
            "manifest_path": self.manifest_path,
            "duration_s": self.duration_s,
            "peak_vram_gb": self.peak_vram_gb,
            "stderr_tail": self.stderr_tail,
            "manifest_status": self.manifest_status,
            "error": self.error,
        }
