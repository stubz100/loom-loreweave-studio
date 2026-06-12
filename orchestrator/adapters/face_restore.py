"""face_restore adapter — GFPGAN-onnx face restore (P1/M6, second postproc-class adapter).

The chained `restore` pass: fixes soft/degraded faces — most importantly the 128px
softness the M4 identity swap leaves on close-ups, which is why the pass orders AFTER
identity. Per item every detected face is aligned → restored → feathered back, blended by
`blend` (1.0 = fully restored); no-face images pass through. CPU onnx (~0.3 s/face,
M6 spike) — no diffusion backbone, works on any pipeline's outputs.

Batch-shaped (`inputs.json` → `face_restore_batch_<ts>.json` in the shared jobs_batch
shape) so streaming/⏹/partial-honesty/meta-echo apply unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import _batch
from .base import CompletionRecord, JobSpec

PIPELINE = "face_restore"
SUPPORTED_MODES = ("restore",)
WIRED_MODES = ("restore",)
WIRED_PARAMS = ("batch_items", "blend", "min_det_score", "model_name")


def resolve_script(roots: list[Path]) -> Path | None:
    for r in roots:
        p = r / "postproc" / "face_restore" / "run_pipeline.py"
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
        "vram_estimate_gb": None,      # onnxruntime CPU
    }


def build_argv(spec: JobSpec, python: str, script: Path) -> list[str]:
    """Write `<out>/inputs.json` (items + tunables) and return the argv."""
    p = spec.params
    payload = {
        "blend": p.get("blend", 0.8),
        "min_det_score": p.get("min_det_score", 0.5),
        "model_name": p.get("model_name") or "gfpgan-1.4",
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
        error="no face_restore batch manifest produced"
              + (f" (worker exited {returncode})" if returncode else ""),
        stderr_tail=(stdout or stderr or "")[-1500:],
    )
