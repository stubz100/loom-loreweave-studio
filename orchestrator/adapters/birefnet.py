"""birefnet adapter — BiRefNet subject matting (P1/M3.5, the first postproc-class adapter).

One image in → three artifacts out (matte / cutout / **bgmask**), one model load per
invocation, no seed (deterministic). The bgmask is the Stage-B consumer: white = repaint,
subject protected by dilation — it feeds the inpaint-realized recipe cells
(`realize="mixed"`), restoring the §7.1 background-diversity axis.

Worker: `postproc/birefnet/run_pipeline.py` (vendored under `pipelines/multistack/src/
pipeline/`, monorepo `src/pipeline/` as dev fallback). Invoked by absolute file path; the
script self-inserts its package root so the `postproc._common` import resolves from any
vendored mirror. Prints the standard markers (`[stage1] Pipeline loaded…`, `  Image: …`,
`  Manifest: …`, `[done] …`) so progress/interim-collection reuse the family conventions.
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

PIPELINE = "birefnet"
SUPPORTED_MODES = ("matte",)
WIRED_MODES = ("matte",)
WIRED_PARAMS = ("input_image", "model_name", "resolution", "threshold",
                "dilate_px", "feather_px", "dtype")

_MANIFEST_RE = re.compile(r"^\s*Manifest:\s*(.+?)\s*$", re.MULTILINE)


def resolve_script(roots: list[Path]) -> Path | None:
    """First existing `postproc/birefnet/run_pipeline.py` across the ordered pipeline
    roots (vendored-first — the multistack mirror carries `postproc/`)."""
    for r in roots:
        p = r / "postproc" / "birefnet" / "run_pipeline.py"
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
        "progress": "coarse",
        "vram_estimate_gb": None,
    }


def progress(line: str) -> float | None:
    """Coarse stage markers — load dominates the wall time, the forward pass is quick."""
    s = line.strip()
    if "[done]" in s:
        return 1.0
    if "[stage2]" in s:   # matted
        return 0.9
    if "[stage1]" in s:   # model loaded
        return 0.6
    return None


def build_argv(spec: JobSpec, python: str, script: Path) -> list[str]:
    """`--input` + `--output-dir` fixed; the tunables (model variant, resolution,
    threshold, dilate/feather, dtype) flow through the catalog flag mapping."""
    p = spec.params
    argv: list[str] = [
        python,
        str(script),
        "--input", str(p["input_image"]),
        "--output-dir", str(spec.output_dir),
        "--device", str(p.get("device", "cuda")),
    ]
    argv += model_catalog.emit_argv(PIPELINE, p, spec.mode)
    return argv


# Single-image runs still stream their artifacts as they're announced.
collect_output = _batch.collect_image_line


def _find_manifest(output_dir: Path, stdout: str) -> Path | None:
    if output_dir.is_dir():
        jsons = sorted(output_dir.glob("birefnet_*.json"), key=lambda f: f.stat().st_mtime)
        if jsons:
            return jsons[-1]
    m = _MANIFEST_RE.search(stdout or "")
    if m:
        p = Path(m.group(1))
        if p.is_file():
            return p
    return None


def parse_result(
    returncode: int,
    stdout: str,
    stderr: str,
    output_dir: Path,
) -> CompletionRecord:
    """Manifest-as-truth: every stage `completed` + the artifacts exist + rc==0.
    Outputs carry their **role** (`matte`/`cutout`/`bgmask`) in `outputs_meta` so the
    UI/Stage-B can pick the bgmask out of the trio without filename heuristics."""
    manifest_path = _find_manifest(output_dir, stdout)
    manifest_status: str | None = None
    error: str | None = None
    duration_s: float | None = None
    outputs: list[str] = []
    meta: list[dict] = []

    if manifest_path and manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            duration_s = data.get("pipeline_duration_s")
            stages = data.get("stages") or []
            if stages and all(s.get("status") == "completed" for s in stages):
                manifest_status = "completed"
            else:
                manifest_status = "failed"
                for s in stages:
                    if s.get("status") == "failed":
                        error = f"{s.get('name')}: {s.get('error')}"
                        break
            for a in data.get("artifacts") or []:
                path = a.get("path")
                if path and Path(path).is_file():
                    outputs.append(path)
                    meta.append({"role": a.get("role", "")})
        except (json.JSONDecodeError, OSError) as e:
            error = f"manifest unreadable: {e}"

    ok = manifest_status == "completed" and bool(outputs) and returncode == 0
    if not ok and error is None:
        if returncode != 0:
            error = f"worker exited {returncode}"
        elif manifest_status is None:
            error = "no manifest produced"
        elif not outputs:
            error = "manifest completed but no artifact file exists"

    return CompletionRecord(
        ok=ok,
        returncode=returncode,
        outputs=outputs,
        outputs_meta=meta if outputs else None,
        manifest_path=str(manifest_path) if manifest_path else None,
        duration_s=duration_s,
        manifest_status=manifest_status,
        error=error,
        stderr_tail=(stdout or stderr or "")[-1500:],
    )
