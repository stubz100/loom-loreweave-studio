"""frame_harvest adapter — extract stills from a video sketch (P1/M7, the chained
second half of the ltxv video-sketch ladder).

One input video → MANY frame outputs: the worker writes one batch-manifest item per
saved frame (the manifest is the truth `_batch.parse_batch_result` reads), each echoing
the sketch's target coverage_cell meta + its frame number — so harvested stills stream
into the Stage-B grid and curate exactly like recipe cells. Pure OpenCV, CPU.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import _batch
from .base import CompletionRecord, JobSpec

PIPELINE = "frame_harvest"
SUPPORTED_MODES = ("harvest",)
WIRED_MODES = ("harvest",)
WIRED_PARAMS = ("batch_items", "every", "max_frames")


def resolve_script(roots: list[Path]) -> Path | None:
    for r in roots:
        p = r / "postproc" / "frame_harvest" / "run_pipeline.py"
        if p.is_file():
            return p
    return None


def present(roots: list[Path]) -> bool:
    return resolve_script(roots) is not None


def capabilities(roots: list[Path]) -> dict:
    return {
        "pipeline": PIPELINE,
        "present": present(roots),
        "worker": str(resolve_script(roots) or ""),
        "modes": list(WIRED_MODES),
        "params": list(WIRED_PARAMS),
        "worker_modes": list(SUPPORTED_MODES),
        "cancellable": True,
        "progress": "per-item",
        "vram_estimate_gb": None,      # OpenCV CPU — no models at all
    }


def build_argv(spec: JobSpec, python: str, script: Path) -> list[str]:
    p = spec.params
    payload = {
        "every": p.get("every", 6),
        "max_frames": p.get("max_frames", 24),
        "items": p["batch_items"],
    }
    inputs_path = spec.output_dir / "inputs.json"
    inputs_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return [python, str(script),
            "--inputs-file", str(inputs_path),
            "--output-dir", str(spec.output_dir)]


def make_progress(params: dict):
    total = (len(params.get("batch_items") or []) or 1) * int(params.get("max_frames", 24))
    return _batch.make_batch_progress(total)


collect_output = _batch.collect_image_line


def parse_result(
    returncode: int,
    stdout: str,
    stderr: str,
    output_dir: Path,
) -> CompletionRecord:
    bm = _batch.find_batch_manifest(output_dir, PIPELINE)
    if bm is not None:
        return _batch.parse_batch_result(returncode, stdout, stderr, bm)
    return CompletionRecord(
        ok=False, returncode=returncode, outputs=[],
        manifest_path=None, duration_s=None, manifest_status=None,
        error="no frame_harvest batch manifest produced"
              + (f" (worker exited {returncode})" if returncode else ""),
        stderr_tail=(stdout or stderr or "")[-1500:],
    )
