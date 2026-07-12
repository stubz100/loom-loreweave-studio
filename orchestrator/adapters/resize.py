"""resize adapter — model-free Lanczos resample (P2, post-M2.11 postproc-class adapter).

The `resize` postproc step: pixel-faithful scaling with NO model in the loop. Every
diffusion-based "resize" re-renders its source (the tile-CN downscale that motivated
this smeared lines and shapes; user 2026-07-12); Lanczos is the correct tool when the
pixels should survive the size change — especially downscaling. Pure PIL, CPU,
milliseconds per image, zero VRAM, zero weights.

Batch-shaped (`inputs.json` → `resize_batch_<ts>.json` in the shared jobs_batch
shape) so streaming/⏹/partial-honesty/meta-echo apply unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import _batch
from .base import CompletionRecord, JobSpec

PIPELINE = "resize"
SUPPORTED_MODES = ("resize",)
WIRED_MODES = ("resize",)
WIRED_PARAMS = ("batch_items", "width", "height")


def resolve_script(roots: list[Path]) -> Path | None:
    for r in roots:
        p = r / "postproc" / "resize" / "run_pipeline.py"
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
        "vram_estimate_gb": None,      # PIL CPU
    }


def build_argv(spec: JobSpec, python: str, script: Path) -> list[str]:
    """Write `<out>/inputs.json` (items + target size) and return the argv."""
    p = spec.params
    payload = {
        "width": p["width"],
        "height": p["height"],
        "items": p["batch_items"],
    }
    inputs_path = spec.output_dir / "inputs.json"
    inputs_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return [python, str(script),
            "--inputs-file", str(inputs_path),
            "--output-dir", str(spec.output_dir)]


def make_progress(params: dict):
    return _batch.make_batch_progress(len(params.get("batch_items") or []) or 1)


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
        error="no resize batch manifest produced"
              + (f" (worker exited {returncode})" if returncode else ""),
        stderr_tail=(stdout or stderr or "")[-1500:],
    )
