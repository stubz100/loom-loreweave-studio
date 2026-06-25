"""Krea 2 Turbo adapter - standalone text-to-image generator.

Krea 2 is wired conservatively: the installed Diffusers runtime exposes
`Krea2Pipeline` only, so Loom advertises T2I and does not claim img2img/inpaint.
The vendored worker is file-path invoked like zimage/sd35.
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

PIPELINE = "krea2"
SUPPORTED_MODES = ("t2i",)
WIRED_MODES = ("t2i",)
WIRED_PARAMS = (
    "prompt", "mode", "width", "height", "seed", "model_name",
    "num_steps", "guidance_scale", "negative_prompt", "max_sequence_length",
    "dtype", "no_cpu_offload", "lora_path", "lora_weight",
    "quant_backend", "quant_dtype", "quant_skip_modules",
)

_MANIFEST_RE = re.compile(r"^\s*Manifest:\s*(.+?)\s*$", re.MULTILINE)


def resolve_script(roots: list[Path]) -> Path | None:
    """First existing `krea2/run_pipeline.py` across ordered pipeline roots."""
    for r in roots:
        p = r / "krea2" / "run_pipeline.py"
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
    s = line.strip()
    if "[done]" in s:
        return 1.0
    if "[stage3]" in s:
        return 0.95
    if "[stage2]" in s:
        return 0.8
    if "[stage1]" in s:
        return 0.25
    return None


def build_argv(spec: JobSpec, python: str, script: Path) -> list[str]:
    """Typed params -> the Krea 2 CLI. T2I only; no --mode flag exists."""
    p = spec.params
    argv: list[str] = [
        python,
        str(script),
        "--prompt", str(p["prompt"]),
        "--output-dir", str(spec.output_dir),
        "--device", str(p.get("device", "cuda")),
    ]
    argv += model_catalog.emit_argv(PIPELINE, p, "t2i")
    return argv


def _find_manifest(output_dir: Path, stdout: str) -> Path | None:
    if output_dir.is_dir():
        jsons = sorted(output_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
        if jsons:
            return jsons[-1]
    m = _MANIFEST_RE.search(stdout or "")
    if m:
        p = Path(m.group(1))
        if p.is_file():
            return p
    return None


def parse_result(returncode: int, stdout: str, stderr: str, output_dir: Path) -> CompletionRecord:
    manifest_path = _find_manifest(output_dir, stdout)
    manifest_status: str | None = None
    error: str | None = None
    duration_s: float | None = None
    image_path: str | None = None

    if manifest_path and manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            duration_s = data.get("pipeline_duration_s")
            image_path = data.get("output_path") or None
            stages = data.get("stages") or []
            statuses = [s.get("status") for s in stages]
            if stages and all(st == "completed" for st in statuses):
                manifest_status = "completed"
            else:
                manifest_status = "failed"
                for s in stages:
                    if s.get("status") == "failed":
                        error = f"{s.get('name')}: {s.get('error')}"
                        break
        except (json.JSONDecodeError, OSError) as e:
            error = f"manifest unreadable: {e}"

    if not image_path and output_dir.is_dir():
        pngs = sorted(output_dir.glob("*.png"), key=lambda f: f.stat().st_mtime)
        if pngs:
            image_path = str(pngs[-1])
    image_exists = bool(image_path and Path(image_path).is_file())

    ok = manifest_status == "completed" and image_exists and returncode == 0
    if not ok and error is None:
        if returncode != 0:
            error = f"worker exited {returncode}"
        elif manifest_status is None:
            error = "no manifest produced"
        elif not image_exists:
            error = "manifest completed but output image is missing"

    return CompletionRecord(
        ok=ok,
        returncode=returncode,
        outputs=[image_path] if image_exists else [],
        manifest_path=str(manifest_path) if manifest_path else None,
        duration_s=duration_s,
        manifest_status=manifest_status,
        error=error,
        stderr_tail=(stdout or stderr or "")[-1500:],
    )
