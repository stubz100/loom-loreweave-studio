"""Shared batch-job helpers for the one-shot image adapters (zimage · sd35).

The batch-mode worker (P1 review 2026-06-10, the "load once, generate N" fix for the
31× model reload in Stage B): a job whose params carry **`batch_items`** is executed as
ONE worker invocation — the adapter writes a `jobs.json` into the job's out dir and
invokes `run_pipeline.py --jobs-file`, the worker loads the pipeline once and loops the
items, and a `<pipeline>_batch_<ts>.json` summary manifest records every item (each ok
item also gets the normal per-image PNG + sidecar pair).

Item shape: `{"prompt": …, "seed": …, "meta": {…}}` (+ any non-load-bound override).
`meta` is opaque to the worker and comes back per-item in the batch manifest — the
orchestrator uses it to carry each Stage-B image's `coverage_cell` + seed, surfaced as
`CompletionRecord.outputs_meta` (parallel to `outputs`).

The jobs-file vocabulary is the worker `run()` kwargs, so the catalog's inverted flags
(`no_cpu_offload`, `no_skip_layer_guidance`) are translated here via each adapter's
inversion map.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .base import CompletionRecord, JobSpec

try:
    from .. import model_catalog
except ImportError:  # pragma: no cover - direct-run convenience
    import model_catalog  # type: ignore

# A finished image announced on stdout (`  Image: <path>` — printed per item in batch
# mode, once at the end for single runs). Drives interim tiles + batch progress.
IMAGE_LINE = re.compile(r"^\s*Image:\s*(.+?\.png)\s*$")


def collect_image_line(line: str) -> str | None:
    """`collect_output` hook for the one-shot adapters: the announced image path."""
    m = IMAGE_LINE.match(line)
    return m.group(1) if m else None


def build_batch_shared(params: dict, pipeline: str, mode: str,
                       inversions: dict[str, tuple[str, object]]) -> dict:
    """The jobs-file `shared` block: every non-advanced catalog tunable present in the
    job params (worker-kwarg vocabulary), seed excluded (per-item). `inversions` maps a
    catalog flag to its run() kwarg, e.g. no_cpu_offload → cpu_offload=False."""
    shared: dict = {"mode": mode}
    for s in model_catalog.params(pipeline):
        name = s["name"]
        if name == "seed" or s.get("advanced") or s.get("post"):
            continue   # post-passes chain as their own jobs, never into the jobs file
        v = params.get(name)
        if v is not None:
            shared[name] = v
    for src, (dst, val) in inversions.items():
        if shared.pop(src, None):
            shared[dst] = val
    return shared


def build_batch_argv(spec: JobSpec, python: str, script: Path, pipeline: str,
                     inversions: dict[str, tuple[str, object]]) -> list[str]:
    """Write `<out>/jobs.json` (shared + items) and return the --jobs-file argv."""
    p = spec.params
    payload = {"shared": build_batch_shared(p, pipeline, spec.mode, inversions),
               "items": p["batch_items"]}
    jobs_path = spec.output_dir / "jobs.json"
    jobs_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return [python, str(script),
            "--jobs-file", str(jobs_path),
            "--output-dir", str(spec.output_dir),
            "--device", str(p.get("device", "cuda"))]


def find_batch_manifest(output_dir: Path, pipeline: str) -> Path | None:
    """The worker's batch summary (`<pipeline>_batch_<ts>.json`) — its presence is what
    routes parse_result down the batch path (single runs never produce one)."""
    if output_dir.is_dir():
        ms = sorted(output_dir.glob(f"{pipeline}_batch_*.json"),
                    key=lambda f: f.stat().st_mtime)
        if ms:
            return ms[-1]
    return None


def parse_batch_result(returncode: int, stdout: str, stderr: str,
                       manifest_path: Path) -> CompletionRecord:
    """Batch-manifest-as-truth: ok = exit 0 + status completed/stopped + ≥1 ok item
    on disk. Per-item failures don't fail the batch (mirrors the worker's exit logic);
    a graceful STOP keeps the completed items. `outputs_meta` carries each item's
    `meta` (+seed/index/manifest_path) parallel to `outputs`.

    **Honesty (review finding 2026-06-10):** the real `status` (incl. "stopped") and the
    item counts ride along in `batch` — a 1-ok/77-failed run must surface as a *partial*
    dataset, never a silent green done."""
    outputs: list[str] = []
    meta: list[dict] = []
    counts: dict | None = None
    status: str | None = None
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
            op = it.get("output_path")
            if it.get("status") == "ok" and op and Path(op).is_file():
                outputs.append(op)
                m = dict(it.get("meta") or {})
                m.setdefault("seed", it.get("seed"))
                m.setdefault("index", it.get("index"))
                m.setdefault("manifest_path", it.get("manifest_path"))
                m.setdefault("prompt", it.get("prompt"))   # chained passes reuse it
                m.setdefault("duration_s", it.get("duration_s"))  # per-image gen time (inspector)
                meta.append(m)
        if status == "failed":
            error = data.get("error") or (
                f"batch failed: {data.get('failed', 0)} failed / {data.get('count', 0)} items")
    except (json.JSONDecodeError, OSError) as e:
        error = f"batch manifest unreadable: {e}"

    ok = bool(outputs) and status in ("completed", "stopped") and returncode == 0
    if not ok and error is None:
        if returncode != 0:
            error = f"worker exited {returncode}"
        elif not outputs:
            error = "batch produced no images"

    return CompletionRecord(
        ok=ok,
        returncode=returncode,
        outputs=outputs,
        manifest_path=str(manifest_path),
        duration_s=duration_s,
        manifest_status=status,            # the REAL status — "stopped" stays visible
        error=error,
        stderr_tail=(stdout or stderr or "")[-1500:],
        outputs_meta=meta,
        batch=counts,
    )


def partial_note(batch: dict | None) -> str | None:
    """The job-note line for a partial/stopped batch (shown in the queue panel +
    Inspector + the Stage B/C banner): None when the batch fully completed."""
    if not batch:
        return None
    failed = batch.get("failed") or 0
    skipped = batch.get("skipped") or 0
    if not failed and not skipped:
        return None
    stopped = " — stopped early" if batch.get("status") == "stopped" else ""
    return (f"partial dataset: {batch.get('ok', 0)}/{batch.get('count', 0)} cells "
            f"({failed} failed, {skipped} skipped){stopped}")


def make_batch_progress(total: int):
    """Per-item progress for a batch run: the shared load lands at 10%, each announced
    image advances the fraction, [batch-done] caps at 99% (finalize = 1.0)."""
    total = max(1, total)
    state = {"done": 0}

    def _progress(line: str) -> float | None:
        s = line.strip()
        if IMAGE_LINE.match(line):
            state["done"] = min(state["done"] + 1, total)
            return min(0.10 + 0.88 * state["done"] / total, 0.98)
        if "[stage1] Pipeline loaded" in s:
            return 0.10
        if "[batch-done]" in s:
            return 0.99
        return None

    return _progress
