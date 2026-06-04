"""zimage adapter — the P0 smoke target (§8, §12).

Full contract (M3): build_argv + capabilities()/presence + coarse progress() +
manifest-status-as-truth parse_result. Cancel = subprocess kill, handled by the
runner (no in-worker signal handling — §15).

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
# What the worker CLI can do (build_argv supports all three — ready for P1).
SUPPORTED_MODES = ("t2i", "img2img", "inpaint")
# What the orchestrator API actually accepts TODAY (review #1): capabilities must
# advertise only this, so it never claims a mode/param GenerateRequest will reject.
# img2img/inpaint (+ init_image/mask_image/strength) get wired in P1 (asset studio).
WIRED_MODES = ("t2i",)
WIRED_PARAMS = (
    "prompt", "mode", "width", "height", "seed", "model_name",
    "num_steps", "guidance_scale", "negative_prompt",
)

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


def capabilities(roots: list[Path]) -> dict:
    """Declared contract for the UI/launch gate (§8): modes, params, presence.

    Honest about the gaps audited in §15: cancel is subprocess-kill (no in-worker
    signal handling), progress is coarse stage markers (no per-step bar), and no
    up-front VRAM estimate.
    """
    return {
        "pipeline": PIPELINE,
        "present": present(roots),
        "worker": str(resolve_script(roots) or ""),
        "modes": list(WIRED_MODES),            # honest: only what the API accepts (review #1)
        "params": list(WIRED_PARAMS),
        "worker_modes": list(SUPPORTED_MODES),  # informational: full CLI capability (P1)
        "cancellable": True,
        "progress": "coarse",
        "vram_estimate_gb": None,
    }


def progress(line: str) -> float | None:
    """Best-effort COARSE progress from the worker's stage prints (§15).

    Deliberately not fine-grained — the worker emits per-stage markers, not
    per-diffusion-step events, so we map stages to a few checkpoints rather than
    faking a smooth bar.
    """
    s = line.strip()
    if "[done]" in s:
        return 1.0
    if "[stage3]" in s:   # saved
        return 0.95
    if "[stage2]" in s:   # generated
        return 0.8
    if "[stage1]" in s:   # pipeline loaded
        return 0.25
    return None


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


def _find_manifest(output_dir: Path, stdout: str) -> Path | None:
    """The job's manifest. With per-job output isolation (M3) the dir holds exactly
    one `*.json`, so it's unambiguous; fall back to the worker's `Manifest:` line."""
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


def parse_result(
    returncode: int,
    stdout: str,
    stderr: str,
    output_dir: Path,
) -> CompletionRecord:
    """Normalize into the common envelope using **manifest-status-as-truth** (§8/§15,
    review #4): success is decided by the saved manifest's per-stage status, not by
    scanning for the newest PNG, cross-checked with the exit code.
    """
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

    # Only if the manifest gave no output_path, fall back to a PNG in the (isolated) dir.
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
