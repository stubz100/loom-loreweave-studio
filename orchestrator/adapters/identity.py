"""identity adapter — ReActor-class face lock to the version's anchor (P1/M4).

Spike-validated (journal M4, 2026-06-11): inswapper_128 swap on real candidates lifts the
ArcFace cosine to the anchor from ~0.1 to ~0.87, ~0.2 s/image on **CPU** (onnxruntime) —
post-hoc and model-agnostic, so it locks zimage/sd35/flux2/multi outputs alike.

Always batch-shaped: the job's `batch_items` (`{"input": <abs image>, "seed", "meta"}`)
are written to `<out>/inputs.json` together with the **anchor** path; the worker loads the
face stack once, loops the items, and emits the same `*_batch_<ts>.json` summary shape as
the zimage/sd35 batch workers — so `_batch.parse_batch_result` (streaming, ⏹ STOP,
partial-honesty, per-item `meta` echo incl. coverage_cell + the measured `anchor_cos`)
applies unchanged.

No-face items (back views) pass through unchanged (meta.identity="no_face_passthrough").
⚠ inswapper weights are research/non-commercial (HF mirror) — tool-scoped gate in
models.json `postproc.identity`; the buffalo_l detector pack auto-downloads on first use.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import _batch
from .base import CompletionRecord, JobSpec

PIPELINE = "identity"
SUPPORTED_MODES = ("lock",)
WIRED_MODES = ("lock",)
WIRED_PARAMS = ("anchor_image", "batch_items", "min_det_score", "model_name")


def resolve_script(roots: list[Path]) -> Path | None:
    """First existing `postproc/identity/run_pipeline.py` across the pipeline roots."""
    for r in roots:
        p = r / "postproc" / "identity" / "run_pipeline.py"
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
        "vram_estimate_gb": None,      # onnxruntime CPU — no GPU residency
    }


def build_argv(spec: JobSpec, python: str, script: Path) -> list[str]:
    """Write `<out>/inputs.json` (anchor + items + tunables) and return the argv."""
    p = spec.params
    payload = {
        "anchor": str(p["anchor_image"]),
        "min_det_score": p.get("min_det_score", 0.5),
        "model_name": p.get("model_name") or "inswapper-128",
        "items": p["batch_items"],
    }
    inputs_path = spec.output_dir / "inputs.json"
    inputs_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return [python, str(script),
            "--inputs-file", str(inputs_path),
            "--output-dir", str(spec.output_dir)]


def make_progress(params: dict):
    """Real per-item fraction off the announced `  Image:` lines (batch machinery)."""
    return _batch.make_batch_progress(len(params.get("batch_items") or []) or 1)


collect_output = _batch.collect_image_line


def parse_result(
    returncode: int,
    stdout: str,
    stderr: str,
    output_dir: Path,
) -> CompletionRecord:
    """Batch-manifest-as-truth via the shared parser (`identity_batch_<ts>.json`)."""
    bm = _batch.find_batch_manifest(output_dir, PIPELINE)
    if bm is not None:
        return _batch.parse_batch_result(returncode, stdout, stderr, bm)
    return CompletionRecord(
        ok=False, returncode=returncode, outputs=[],
        manifest_path=None, duration_s=None, manifest_status=None,
        error="no identity batch manifest produced"
              + (f" (worker exited {returncode})" if returncode else ""),
        stderr_tail=(stdout or stderr or "")[-1500:],
    )
