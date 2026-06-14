"""flux2 adapter — Stage-B **identity-preserving** expansion via multi-reference (§11, R147).

The §11 spike (2026-06-13, GO) proved FLUX.2's native in-context reference conditioning carries
the hero's identity into a NEW scene/pose — the variation `img2img` structurally can't produce
(img2img is anchored to the init's composition; a front portrait stays a front portrait). loom
wires it as the **`ref` mode**: the version's hero ★ rides as the reference image and each
coverage cell's prompt drives the pose/angle/expression, identity held by the reference tokens.

CONTRACT (the 1-page check, kb-loom-p0 §15):

- **Invocation**: `python -m pipeline.flux2.run_pipeline …` (MODULE, like `multi` — the flux2
  worker uses package-relative imports, so it can't be run by bare path). The runner sets
  `cwd = script.parents[2]` (= `…/src`); the worker self-bootstraps the BFL `flux2` lib path.
  Stage-B fires ONE `--jobs-file` batch job per sweep: the worker encodes ALL cell prompts, frees the
  text encoder, loads the flow model + AE, encodes the SHARED hero reference ONCE, then loops the
  cells (so the 8 GB Qwen3 encoder and 8 GB klein flow model never co-reside on 16 GB VRAM).
- **Modes**: wired = `ref` (needs `ref_images`). The worker also does `t2i`/`img2img` (casting is
  `multi`'s job; img2img is the zimage/sd35 ladder) — not wired into loom's flux2 role.
- **Output / truth**: batch summary `flux2_batch_<ts>.json` (jobs_batch manifest) — parse is
  `_batch.parse_batch_result` (status completed/stopped + ≥1 ok item + exit 0); each ok cell also
  gets a PNG + .json sidecar. Per-cell `meta` (coverage_cell) echoes back via `outputs_meta`.
- Cancel = subprocess kill; STOP file = graceful batch stop; progress = per-item.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import _batch
from .base import CompletionRecord, JobSpec

try:
    from .. import model_catalog
except ImportError:  # pragma: no cover - direct-run convenience
    import model_catalog  # type: ignore

PIPELINE = "flux2"
# Module-invoked (package-relative imports), like `multi` — NOT run by bare path.
MODULE = "pipeline.flux2.run_pipeline"
SUPPORTED_MODES = ("t2i", "img2img", "ref")
# loom wires `ref` (identity-preserving Stage-B expansion, §11/R147) + `t2i` (standalone
# casting/sandbox — flux2 as a first-class generator, not only inside `multi`).
WIRED_MODES = ("ref", "t2i")
WIRED_PARAMS = (
    "prompt", "mode", "width", "height", "seed", "model_name",
    "num_steps", "guidance", "ref_images",
)
# Reference caps (kb-flux2.md §6): klein ≤4, dev ≤6.
MAX_REFS = {"flux.2-dev": 6}
DEFAULT_MAX_REFS = 4
# Shared (load-bound + generation) keys copied into the batch jobs file's `shared` block.
_SHARED_KEYS = ("mode", "model_name", "width", "height", "num_steps", "guidance",
                "cpu_offload", "ref_images")

_MANIFEST_RE = re.compile(r"^\s*Manifest:\s*(.+?)\s*$", re.MULTILINE)


def resolve_script(roots: list[Path]) -> Path | None:
    """First existing `flux2/run_pipeline.py` across the ordered pipeline roots (vendored-first)."""
    for r in roots:
        p = r / "flux2" / "run_pipeline.py"
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
        "vram_estimate_gb": None,
        # §11 multi-ref: the hero rides as an in-context reference (identity-preserving).
        "multi_ref": {"max_refs": DEFAULT_MAX_REFS, "via": "encode_image_refs"},
    }


def progress(line: str) -> float | None:
    """Coarse stage markers for a single run (batch runs use the per-item progress)."""
    s = line.strip()
    if "[done]" in s:
        return 1.0
    if "[stage4]" in s:
        return 0.95
    if "[stage3]" in s:
        return 0.85
    if "[stage1]" in s:
        return 0.25
    return None


def _build_batch_argv(spec: JobSpec, python: str, script: Path) -> list[str]:
    """Write `<out>/jobs.json` (shared incl. ref_images + per-cell items) and the --jobs-file argv.
    flux2-specific (not the generic `_batch.build_batch_argv`) because `ref_images` rides the
    SHARED block — encoded once for the whole sweep — and isn't a per-cell catalog flag."""
    p = spec.params
    shared: dict = {"mode": spec.mode}
    for k in _SHARED_KEYS:
        if k == "mode":
            continue
        v = p.get(k)
        if v is not None:
            shared[k] = v
    payload = {"shared": shared, "items": p["batch_items"]}
    jobs_path = spec.output_dir / "jobs.json"
    jobs_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return [python, "-m", MODULE,
            "--jobs-file", str(jobs_path),
            "--output-dir", str(spec.output_dir),
            "--device", str(p.get("device", "cuda"))]


def build_argv(spec: JobSpec, python: str, script: Path) -> list[str]:
    """Stage-B fires a `--jobs-file` batch (load once, loop cells). A single run (dry-run preview /
    manual test) emits `--ref-image` per reference + the catalog flags."""
    p = spec.params
    mode = spec.mode if spec.mode in SUPPORTED_MODES else "ref"
    if p.get("batch_items"):
        return _build_batch_argv(spec, python, script)
    argv: list[str] = [
        python, "-m", MODULE,
        "--prompt", str(p.get("prompt", "")),
        "--mode", mode,
        "--output-dir", str(spec.output_dir),
        "--device", str(p.get("device", "cuda")),
    ]
    for ref in (p.get("ref_images") or []):
        argv += ["--ref-image", str(ref)]
    argv += model_catalog.emit_argv(PIPELINE, p, mode)
    # Single-run flux2 (t2i casting / ref preview) loads the 8 GB klein flow model AND the
    # 8 GB Qwen3 text encoder — force the CPU<->GPU swap so they don't co-reside on 16 GB.
    # (The batch `ref` sweep does its own two-phase offload in run_jobs, never this path.)
    if "--cpu-offload" not in argv:
        argv.append("--cpu-offload")
    return argv


def make_progress(params: dict):
    items = params.get("batch_items")
    return _batch.make_batch_progress(len(items)) if items else progress


collect_output = _batch.collect_image_line


def _find_manifest(output_dir: Path, stdout: str) -> Path | None:
    if output_dir.is_dir():
        jsons = sorted(output_dir.glob("flux2_*.json"), key=lambda f: f.stat().st_mtime)
        # exclude the batch summary (handled separately)
        jsons = [j for j in jsons if "_batch_" not in j.name]
        if jsons:
            return jsons[-1]
    m = _MANIFEST_RE.search(stdout or "")
    if m:
        p = Path(m.group(1))
        if p.is_file():
            return p
    return None


def parse_result(returncode: int, stdout: str, stderr: str, output_dir: Path) -> CompletionRecord:
    """Batch-summary-as-truth (the Stage-B path). A single run falls back to the lightweight
    sidecar / newest PNG."""
    bm = _batch.find_batch_manifest(output_dir, PIPELINE)
    if bm is not None:
        return _batch.parse_batch_result(returncode, stdout, stderr, bm)

    image_path: str | None = None
    manifest_path = _find_manifest(output_dir, stdout)
    if manifest_path and manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            image_path = data.get("output_path") or None
        except (json.JSONDecodeError, OSError):
            pass
    if not image_path and output_dir.is_dir():
        pngs = sorted(output_dir.glob("flux2_*.png"), key=lambda f: f.stat().st_mtime)
        if pngs:
            image_path = str(pngs[-1])
    image_exists = bool(image_path and Path(image_path).is_file())

    ok = image_exists and returncode == 0
    error = None if ok else (f"worker exited {returncode}" if returncode != 0
                             else "no output image produced")
    return CompletionRecord(
        ok=ok,
        returncode=returncode,
        outputs=[image_path] if image_exists else [],
        manifest_path=str(manifest_path) if manifest_path else None,
        manifest_status="completed" if ok else "failed",
        error=error,
        stderr_tail=(stdout or stderr or "")[-1500:],
    )
