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
import re
from pathlib import Path

from . import _batch
from .base import CompletionRecord, JobSpec

PIPELINE = "identity"
SUPPORTED_MODES = ("lock", "score")
WIRED_MODES = ("lock", "score")
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
    """Write `<out>/inputs.json` (mode + anchor + items + tunables) and return the argv.
    `anchor_image` is required for lock; OPTIONAL for score (R120 centroid fallback)."""
    p = spec.params
    anchor = p.get("anchor_image") if spec.mode == "score" else p["anchor_image"]
    payload = {
        "mode": spec.mode,
        "anchor": str(anchor) if anchor else None,
        "min_det_score": p.get("min_det_score", 0.5),
        "model_name": p.get("model_name") or "inswapper-128",
        "items": p["batch_items"],
    }
    inputs_path = spec.output_dir / "inputs.json"
    inputs_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return [python, str(script),
            "--inputs-file", str(inputs_path),
            "--output-dir", str(spec.output_dir)]


_SCORE_ITEM = re.compile(r"^\[item (\d+)/(\d+)\]")


def make_progress(params: dict):
    """lock: per-item fraction off the `  Image:` lines (batch machinery). score emits NO
    image lines by design — its fraction reads the `[item i/n]` progress prints (the
    runner calls `make_progress(params)`, so the submitter mirrors the mode into params)."""
    if params.get("mode") != "score":
        return _batch.make_batch_progress(len(params.get("batch_items") or []) or 1)

    def _progress(line: str) -> float | None:
        s = line.strip()
        m = _SCORE_ITEM.match(s)
        if m:
            done, total = int(m.group(1)), max(1, int(m.group(2)))
            return min(0.10 + 0.88 * done / total, 0.98)
        if "[stage1] Pipeline loaded" in s:
            return 0.10
        if "[batch-done]" in s:
            return 0.99
        return None

    return _progress


collect_output = _batch.collect_image_line


def _parse_score_result(returncode: int, stdout: str, stderr: str,
                        manifest_path: Path) -> CompletionRecord:
    """Score runs produce NO images by design — ok = exit 0 + a completed/stopped manifest
    with ≥1 scored item; the per-ref scores live in the manifest (the harvest endpoint
    reads it via `manifest_path`), and `outputs_meta` mirrors them for the job record."""
    status: str | None = None
    counts: dict | None = None
    meta: list[dict] = []
    error: str | None = None
    duration_s: float | None = None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        status = data.get("status")
        duration_s = data.get("total_duration_s")
        counts = {"count": data.get("count", 0), "ok": data.get("ok", 0),
                  "failed": data.get("failed", 0), "skipped": data.get("skipped", 0),
                  "status": status}
        for it in data.get("items") or []:
            if it.get("status") == "ok":
                m = dict(it.get("meta") or {})
                m.setdefault("index", it.get("index"))
                meta.append(m)
        if status == "failed":
            error = f"score failed: {data.get('failed', 0)} failed / {data.get('count', 0)} items"
    except (json.JSONDecodeError, OSError) as e:
        error = f"batch manifest unreadable: {e}"
    ok = returncode == 0 and status in ("completed", "stopped") and bool(meta)
    if not ok and error is None:
        error = f"worker exited {returncode}" if returncode else "score run scored no items"
    return CompletionRecord(
        ok=ok, returncode=returncode, outputs=[],
        manifest_path=str(manifest_path), duration_s=duration_s,
        manifest_status=status, error=error,
        stderr_tail=(stdout or stderr or "")[-1500:],
        outputs_meta=meta, batch=counts,
    )


def parse_result(
    returncode: int,
    stdout: str,
    stderr: str,
    output_dir: Path,
) -> CompletionRecord:
    """Batch-manifest-as-truth via the shared parser (`identity_batch_<ts>.json`);
    score-mode manifests route to the no-outputs score parser."""
    bm = _batch.find_batch_manifest(output_dir, PIPELINE)
    if bm is not None:
        try:
            if json.loads(bm.read_text(encoding="utf-8")).get("mode") == "score":
                return _parse_score_result(returncode, stdout, stderr, bm)
        except (json.JSONDecodeError, OSError):
            pass   # unreadable → the shared parser reports it uniformly
        return _batch.parse_batch_result(returncode, stdout, stderr, bm)
    return CompletionRecord(
        ok=False, returncode=returncode, outputs=[],
        manifest_path=None, duration_s=None, manifest_status=None,
        error="no identity batch manifest produced"
              + (f" (worker exited {returncode})" if returncode else ""),
        stderr_tail=(stdout or stderr or "")[-1500:],
    )
