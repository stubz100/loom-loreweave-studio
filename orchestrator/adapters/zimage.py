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

from . import _batch
from .base import CompletionRecord, JobSpec

try:
    from .. import model_catalog
except ImportError:  # pragma: no cover - direct-run convenience
    import model_catalog  # type: ignore

PIPELINE = "zimage"
# catalog flag → worker run() kwarg for the batch jobs file (see _batch)
_BATCH_INVERSIONS = {"no_cpu_offload": ("cpu_offload", False)}
# What the worker CLI can do (build_argv supports all three — ready for P1).
SUPPORTED_MODES = ("t2i", "img2img", "inpaint")
# What the orchestrator API actually accepts TODAY: capabilities must advertise only this,
# so it never claims a mode/param GenerateRequest will reject. img2img/inpaint (+ init_image/
# mask_image/strength) wired in P1/M3 for Stage-B expansion (build_argv already handled them).
WIRED_MODES = ("t2i", "img2img", "inpaint")
WIRED_PARAMS = (
    "prompt", "mode", "width", "height", "seed", "model_name",
    "num_steps", "guidance_scale", "negative_prompt",
    "lora_path", "lora_name", "lora_weight",
)

_MANIFEST_RE = re.compile(r"^\s*Manifest:\s*(.+?)\s*$", re.MULTILINE)

# M2.7 Phase 2a — warm-worker serve mode: the runner feeds same-`warm_group` Stage-B cell-jobs to
# ONE persistent `--serve` process (pipeline loaded once for the sweep). The result-line prefix the
# worker frames each per-job result with; the runner matches on it (see runner._feed_warm).
SERVE_RESULT_PREFIX = "[serve-result] "


def serve_argv(python: str, script: Path, device: str, out_dir: str) -> list[str]:
    """argv to spawn the persistent warm worker (one pipeline load, N stdin jobs). zimage is invoked
    by absolute file path (its bare imports need the script dir on sys.path[0]); the per-cell output
    dir rides each fed job spec, so `--output-dir` here is just the default fallback."""
    return [python, str(script), "--serve", "--device", str(device), "--output-dir", str(out_dir)]


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
    """Typed params → the real CLI. Fixed args (prompt/mode/output-dir/device) + every catalog
    param present (`model_catalog.emit_argv` — the single flag-mapping source, so any variant +
    any tunable in the request's params channel flows through). width/height divisible by 16.

    A job with **`batch_items`** becomes ONE `--jobs-file` invocation instead (the worker
    loads the model once and loops the items — the Stage-B dataset path, see `_batch`).

    `script` is the resolved absolute worker path — zimage is invoked by file path, not `-m`."""
    p = spec.params
    # Loom runs zimage RESIDENT on the 16 GB ROCm rig. With diffusers
    # enable_model_cpu_offload() the zimage-base **VAE decode** costs ~15 min/image — probe
    # job_b4ae9136: `encode+setup=11.5s denoise=1.0s decode+post=894.1s` — because the
    # transformer already denoises fast on-GPU during offload, so only the final decode pays
    # the offload tax (the small VAE ends up on the slow path). Resident keeps the VAE on the
    # GPU where decode is seconds. Default `no_cpu_offload=True` for the single + batch paths
    # so all three dispatch routes agree (the warm `--serve` worker is already resident by
    # default, run_pipeline `_ServeGenerator._load`). emit_argv/build_batch only act on params
    # that are *present* (catalog defaults aren't injected), so this is where the loom default
    # lives; an explicit `no_cpu_offload=False` in the request still forces offload.
    p.setdefault("no_cpu_offload", True)
    if p.get("batch_items"):
        return _batch.build_batch_argv(spec, python, script, PIPELINE, _BATCH_INVERSIONS)
    argv: list[str] = [
        python,
        str(script),
        "--prompt", str(p["prompt"]),
        "--mode", spec.mode,
        "--output-dir", str(spec.output_dir),
        "--device", str(p.get("device", "cuda")),
    ]
    argv += model_catalog.emit_argv(PIPELINE, p, spec.mode)
    return argv


def make_progress(params: dict):
    """Batch jobs get a real per-item fraction; single runs keep the coarse markers."""
    items = params.get("batch_items")
    return _batch.make_batch_progress(len(items)) if items else progress


# Interim results: surface each announced image as it lands (per item in batch mode).
collect_output = _batch.collect_image_line


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

    Batch jobs route to the batch-summary parse (their `<pipeline>_batch_*.json` is
    authoritative; per-item sidecars stay as per-image provenance)."""
    bm = _batch.find_batch_manifest(output_dir, PIPELINE)
    if bm is not None:
        return _batch.parse_batch_result(returncode, stdout, stderr, bm)
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
