"""zimage adapter (M1) — the P0 smoke target (§8, §12).

Minimal contract: build_argv + read-the-saved-manifest. The full hardening
(capabilities/presence, progress, cancel, manifest-as-truth) is M3.

CONTRACT FINDING (M1): `run_pipeline.py` uses bare imports
(`import stage1_load_pipeline`), so it MUST be invoked **by absolute file path**
(which puts the `zimage/` dir on sys.path[0]) — NOT `python -m src.pipeline...`
(cwd would be sys.path[0], breaking those imports). Recorded in kb-loom-p0-imp.md.
The CLI also has **no `--count`/`--out`**: it auto-names
`zimage_<UTCstamp>_s<seed>.png` + a `<same>.json` sidecar under `--output-dir`,
and prints `  Image: <path>` / `  Manifest: <path>` on success.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .base import CompletionRecord, JobSpec

PIPELINE = "zimage"
SUPPORTED_MODES = ("t2i", "img2img", "inpaint")

_IMAGE_RE = re.compile(r"^\s*Image:\s*(.+?)\s*$", re.MULTILINE)
_MANIFEST_RE = re.compile(r"^\s*Manifest:\s*(.+?)\s*$", re.MULTILINE)


def resolve_script(roots: list[Path]) -> Path | None:
    """First existing worker across the ordered pipeline roots (vendored-first).

    Each root directly contains `zimage/run_pipeline.py`; see config.pipeline_roots.
    """
    for r in roots:
        p = r / "zimage" / "run_pipeline.py"
        if p.is_file():
            return p
    return None


def present(roots: list[Path]) -> bool:
    """Presence check (drives the launch gate, §11 — full version in M7)."""
    return resolve_script(roots) is not None


def build_argv(spec: JobSpec, python: str, script: Path) -> list[str]:
    """Typed params → the real CLI. width/height must be divisible by 16.

    `script` is the resolved absolute worker path (see resolve_script) — zimage
    must be invoked by file path, not `-m` (module docstring)."""
    p = spec.params
    argv: list[str] = [
        python,
        str(script),
        "--prompt", str(p["prompt"]),
        "--mode", spec.mode,
        "--width", str(p.get("width", 1280)),
        "--height", str(p.get("height", 720)),
        "--output-dir", str(spec.output_dir),
        "--device", str(p.get("device", "cuda")),
    ]
    if p.get("model_name"):
        argv += ["--model-name", str(p["model_name"])]
    if p.get("seed") is not None:
        argv += ["--seed", str(p["seed"])]
    if p.get("num_steps") is not None:
        argv += ["--num-steps", str(p["num_steps"])]
    if p.get("guidance_scale") is not None:
        argv += ["--guidance-scale", str(p["guidance_scale"])]
    if p.get("negative_prompt"):
        argv += ["--negative-prompt", str(p["negative_prompt"])]
    if spec.mode in ("img2img", "inpaint") and p.get("init_image"):
        argv += ["--init-image", str(p["init_image"])]
    if spec.mode == "inpaint" and p.get("mask_image"):
        argv += ["--mask-image", str(p["mask_image"])]
    if p.get("strength") is not None:
        argv += ["--strength", str(p["strength"])]
    return argv


def parse_result(
    returncode: int,
    stdout: str,
    stderr: str,
    output_dir: Path,
) -> CompletionRecord:
    """Normalize the worker's output into the common envelope (§8)."""
    img_match = _IMAGE_RE.search(stdout or "")
    man_match = _MANIFEST_RE.search(stdout or "")
    image_path = img_match.group(1) if img_match else None
    manifest_path = man_match.group(1) if man_match else None

    # Fallback: newest PNG in the output dir (if stdout parsing missed it).
    if image_path is None and output_dir.is_dir():
        pngs = sorted(output_dir.glob("zimage_*.png"), key=lambda f: f.stat().st_mtime)
        if pngs:
            image_path = str(pngs[-1])
            manifest_path = manifest_path or str(pngs[-1].with_suffix(".json"))

    duration_s = None
    if manifest_path and Path(manifest_path).is_file():
        try:
            data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            duration_s = data.get("pipeline_duration_s")
        except (json.JSONDecodeError, OSError):
            pass  # manifest unreadable — fall back to exit code only

    image_exists = bool(image_path and Path(image_path).is_file())
    ok = returncode == 0 and image_exists

    return CompletionRecord(
        ok=ok,
        returncode=returncode,
        outputs=[image_path] if image_exists else [],
        manifest_path=manifest_path if (manifest_path and Path(manifest_path).is_file()) else None,
        duration_s=duration_s,
        stderr_tail=(stderr or "")[-1500:],
    )
