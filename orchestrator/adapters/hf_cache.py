"""hf_cache adapter — the model cache's io jobs (M2.17 step b, kb-loom-cache.md).

Three tasks, one torch-free worker (`pipelines/hf_cache/run_pipeline.py`), all through the
queue so the dock shows progress and a day-long download can be paused or cancelled:

* **fetch**  — `hf_hub_download` per needed file into the hub cache (resume is the library's
  default; a cancel stops after the current file, the next fetch resumes).
* **verify** — sha256 of each needed blob against the name the hub gave it (LFS blobs are
  named by their sha256; small git blobs by their git sha1) — an offline integrity check.
* **move**   — copy the hub tree to a new location preserving the relative symlinks the
  layout depends on, skipping files already there with the same size (resumable), then
  verify counts and sizes. The orchestrator switches the location when the job completes.

No image is produced: `outputs` stays empty and the canvas never draws a tile for it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .base import CompletionRecord, JobSpec

PIPELINE = "hf_cache"
SUPPORTED_MODES = ("fetch", "verify", "move")
WIRED_MODES = SUPPORTED_MODES
WIRED_PARAMS = ("repo_id", "files", "revision", "cache_home", "to")

_PROGRESS = re.compile(r"^\[cache\] progress (\d+(?:\.\d+)?)")
_NOTE = re.compile(r"^\[cache\] note (.+)$")


def resolve_script(roots: list[Path]) -> Path | None:
    for r in roots:
        p = r / "hf_cache" / "run_pipeline.py"
        if p.is_file():
            return p
    return None


def present(roots: list[Path]) -> bool:
    return resolve_script(roots) is not None


def capabilities(roots: list[Path]) -> dict:
    return {
        "pipeline": PIPELINE, "present": present(roots), "worker": str(resolve_script(roots) or ""),
        "modes": list(WIRED_MODES), "params": list(WIRED_PARAMS), "worker_modes": list(SUPPORTED_MODES),
        "cancellable": True, "progress": "per-file", "vram_estimate_gb": None,
    }


def build_argv(spec: JobSpec, python: str, script: Path) -> list[str]:
    """Write `<out>/spec.json` (the task and its arguments) and return the argv."""
    payload = {"task": spec.mode, **{k: spec.params.get(k) for k in WIRED_PARAMS if spec.params.get(k) is not None}}
    spec_path = spec.output_dir / "spec.json"
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return [python, str(script), "--spec", str(spec_path), "--output-dir", str(spec.output_dir)]


def progress(line: str) -> float | None:
    m = _PROGRESS.match(line)
    return max(0.0, min(1.0, float(m.group(1)))) if m else None


def collect_note(line: str) -> str | None:
    m = _NOTE.match(line)
    return m.group(1).strip() if m else None


def parse_result(returncode: int, stdout: str, stderr: str, output_dir: Path) -> CompletionRecord:
    """`<out>/cache_result.json` is the truth: `{ok, task, error?, ...details}`."""
    path = output_dir / "cache_result.json"
    tail = (stdout or stderr or "")[-1500:]
    if not path.is_file():
        return CompletionRecord(ok=False, returncode=returncode, outputs=[], manifest_path=None, duration_s=None,
                                manifest_status=None, stderr_tail=tail,
                                error="no cache result produced" + (f" (worker exited {returncode})" if returncode else ""))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return CompletionRecord(ok=False, returncode=returncode, outputs=[], manifest_path=str(path), duration_s=None,
                                manifest_status="failed", stderr_tail=tail, error=f"unreadable cache result: {e}")
    ok = bool(data.get("ok")) and returncode == 0
    return CompletionRecord(ok=ok, returncode=returncode, outputs=[], manifest_path=str(path),
                            duration_s=data.get("duration_s"), manifest_status="completed" if ok else "failed",
                            stderr_tail=tail, error=None if ok else (data.get("error") or f"worker exited {returncode}"))
