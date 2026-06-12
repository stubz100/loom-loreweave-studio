"""ltxv adapter — LTX-Video i2v for the Stage-B video-sketch harvest (P1/M7, R11).

One job = one low-res motion sketch from the hero ★ (i2v). The video itself is an
intermediate: a chained `frame_harvest` pass extracts stills that stream into the grid
carrying the sketch's TARGET coverage_cell — multi-angle/pose coverage without 3D.

Worker: `ltxv/run_pipeline.py` (vendored multistack mirror; monorepo fallback), invoked
by file path with the `i2v` subcommand. PipelineManifest family (stage records,
save-then-raise); prints `[stage1..4]`, `[done]`, `  Video:` + `  Manifest:`. Output =
`ltxv_i2v_<tag>_s<seed>_<ts>.mp4` + `.json` (+ a transient `.latents.pt` it cleans up).
⚠ 2B variants NEED offload=model on the 16 GB target (T5-XXL text encoder ~11 GB) — the
variant defaults handle it; the catalog exposes the override.
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

PIPELINE = "ltxv"
SUPPORTED_MODES = ("t2v", "i2v")
WIRED_MODES = ("i2v",)       # the M7 sketch path; t2v has no loom surface yet
WIRED_PARAMS = ("prompt", "init_image", "model_name", "width", "height", "seed",
                "num_frames", "fps", "num_steps", "guidance_scale",
                "negative_prompt", "offload")

_MANIFEST_RE = re.compile(r"^\s*Manifest:\s*(.+?)\s*$", re.MULTILINE)
_VIDEO_RE = re.compile(r"^\s*Video:\s*(.+?\.mp4)\s*$", re.MULTILINE)


def resolve_script(roots: list[Path]) -> Path | None:
    for r in roots:
        p = r / "ltxv" / "run_pipeline.py"
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
    """Coarse stage markers: load → denoise → decode → export."""
    s = line.strip()
    if "[done]" in s:
        return 1.0
    if "[stage4]" in s:   # exported
        return 0.97
    if "[stage3]" in s:   # decoded
        return 0.9
    if "[stage2]" in s:   # denoised (the long part)
        return 0.8
    if "[stage1]" in s:   # pipeline loaded
        return 0.25
    return None


def build_argv(spec: JobSpec, python: str, script: Path) -> list[str]:
    """`i2v` subcommand + fixed args + the catalog flag mapping (model_name → --variant)."""
    p = spec.params
    argv: list[str] = [
        python,
        str(script),
        spec.mode,
        "--prompt", str(p["prompt"]),
        "--output-dir", str(spec.output_dir),
        "--device", str(p.get("device", "cuda")),
    ]
    argv += model_catalog.emit_argv(PIPELINE, p, spec.mode)
    return argv


def collect_output(line: str) -> str | None:
    """Stream the finished video the moment the worker announces it."""
    m = _VIDEO_RE.match(line)
    return m.group(1) if m else None


def _find_manifest(output_dir: Path, stdout: str) -> Path | None:
    if output_dir.is_dir():
        jsons = sorted(output_dir.glob("ltxv_*.json"), key=lambda f: f.stat().st_mtime)
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
    """Manifest-as-truth (the PipelineManifest family): every stage completed + the
    exported mp4 exists + rc == 0."""
    manifest_path = _find_manifest(output_dir, stdout)
    manifest_status: str | None = None
    error: str | None = None
    duration_s: float | None = None
    video_path: str | None = None

    if manifest_path and manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            duration_s = data.get("pipeline_duration_s")
            video_path = data.get("output_path") or None
            stages = data.get("stages") or []
            if stages and all(s.get("status") == "completed" for s in stages):
                manifest_status = "completed"
            else:
                manifest_status = "failed"
                for s in stages:
                    if s.get("status") == "failed":
                        error = f"{s.get('name')}: {s.get('error')}"
                        break
        except (json.JSONDecodeError, OSError) as e:
            error = f"manifest unreadable: {e}"

    video_exists = bool(video_path and Path(video_path).is_file())
    ok = manifest_status == "completed" and video_exists and returncode == 0
    if not ok and error is None:
        if returncode != 0:
            error = f"worker exited {returncode}"
        elif manifest_status is None:
            error = "no manifest produced"
        elif not video_exists:
            error = "manifest completed but the exported video is missing"

    return CompletionRecord(
        ok=ok,
        returncode=returncode,
        outputs=[video_path] if video_exists else [],
        manifest_path=str(manifest_path) if manifest_path else None,
        duration_s=duration_s,
        manifest_status=manifest_status,
        error=error,
        stderr_tail=(stdout or stderr or "")[-1500:],
    )
