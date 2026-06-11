"""sd35 adapter — Stage-B expansion via **img2img + inpaint** (P1/M3, §5, R121).

SD 3.5 realizes two Stage-B dataset methods (§7.1): **img2img** (strength sweep from the
hero — pose/expression variation) and **inpaint** (stable subject, repaint the background —
the background-diversity axis). One prompt → one image (contrast `multi`'s pool).

CONTRACT (the 1-page check, kb-loom-p0 §15):

- **Invocation**: by **absolute file path** (like `zimage`), NOT `-m`. `run_pipeline.py`
  self-bootstraps its imports — `sys.path.insert(0, parents[1])` for the shared
  `_artifact_id`, and the script's own dir (sys.path[0], set by running a script by path)
  for its bare `import stage1_load_pipeline` etc. So the runner's default `cwd =
  script.parents[2]` + no PYTHONPATH wiring is sufficient (verified: vendored under
  `pipelines/multistack/src/pipeline/sd35/`, with `_artifact_id.py` alongside).
- **Modes**: wired = `img2img` (needs `--init-image`; `--strength` ~0.5–0.7 polish/variation)
  and `inpaint` (needs `--init-image` + `--mask-image`). The worker also supports `t2i` and
  the `cn-inpaint(-mc)` ControlNet modes — not wired here (casting is `multi`'s job; CN is
  postproc/M6+).
- **Output / truth**: same `PipelineManifest` convention as `zimage` — `sd35_<UTCstamp>_s<seed>.png`
  + a `.json` sidecar with `stages[].status` + `pipeline_duration_s` + `output_path`, and the
  worker prints `[stageN]` markers + `  Image:` / `  Manifest:` on success. parse_result is
  **manifest-status-as-truth** (every stage `completed` + the image on disk + exit 0).
- Cancel = subprocess kill (runner); progress = coarse stage markers (no per-step bar).
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

PIPELINE = "sd35"
# Full worker capability (informational); CN modes stay unwired (postproc/M6+).
SUPPORTED_MODES = ("t2i", "img2img", "inpaint", "cn-inpaint", "cn-inpaint-mc")
# t2i wired 2026-06-10 (review #3): the sandbox is the experimentation surface and sd35
# t2i is a worker-supported mode — no reason to fence it to `multi` casting only.
WIRED_MODES = ("t2i", "img2img", "inpaint")
# catalog flag → worker run() kwarg for the batch jobs file (see _batch)
_BATCH_INVERSIONS = {"no_cpu_offload": ("cpu_offload", False),
                     "no_skip_layer_guidance": ("skip_layer_guidance", False)}
WIRED_PARAMS = (
    "prompt", "mode", "width", "height", "seed", "model_name",
    "num_steps", "guidance_scale", "negative_prompt",
    "init_image", "mask_image", "strength",
)

_MANIFEST_RE = re.compile(r"^\s*Manifest:\s*(.+?)\s*$", re.MULTILINE)


def resolve_script(roots: list[Path]) -> Path | None:
    """First existing `sd35/run_pipeline.py` across the ordered pipeline roots (vendored-first
    — resolves under `pipelines/multistack/src/pipeline/`, parent monorepo as dev fallback)."""
    for r in roots:
        p = r / "sd35" / "run_pipeline.py"
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
        "modes": list(WIRED_MODES),             # honest: only what the API accepts
        "params": list(WIRED_PARAMS),
        "worker_modes": list(SUPPORTED_MODES),   # informational: full CLI capability
        "cancellable": True,
        "progress": "coarse",
        "vram_estimate_gb": None,
    }


def progress(line: str) -> float | None:
    """Coarse progress from the worker's stage prints (no per-step event stream)."""
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
    """Typed params → the real CLI (file-path invocation). Fixed args (prompt/mode/output-dir/
    device) + every catalog param present (`model_catalog.emit_argv` — the single flag-mapping
    source). img2img needs `init_image`; inpaint needs `init_image` + `mask_image` (init/mask are
    `modes`-gated in the catalog so they only emit for the right mode)."""
    p = spec.params
    mode = spec.mode if spec.mode in SUPPORTED_MODES else "img2img"
    if p.get("batch_items"):
        # ONE --jobs-file invocation: the worker loads the model once and loops the items.
        return _batch.build_batch_argv(spec, python, script, PIPELINE, _BATCH_INVERSIONS)
    argv: list[str] = [
        python,
        str(script),
        "--prompt", str(p["prompt"]),
        "--mode", mode,
        "--output-dir", str(spec.output_dir),
        "--device", str(p.get("device", "cuda")),
    ]
    argv += model_catalog.emit_argv(PIPELINE, p, mode)
    return argv


def make_progress(params: dict):
    """Batch jobs get a real per-item fraction; single runs keep the coarse markers."""
    items = params.get("batch_items")
    return _batch.make_batch_progress(len(items)) if items else progress


# Interim results: surface each announced image as it lands (per item in batch mode).
collect_output = _batch.collect_image_line


def _find_manifest(output_dir: Path, stdout: str) -> Path | None:
    """The job's manifest. With per-job output isolation the dir holds exactly one `*.json`,
    so it's unambiguous; fall back to the worker's `Manifest:` line."""
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
    """Manifest-status-as-truth (mirrors `zimage`): success = every stage `completed` + the
    image on disk + exit 0. Batch jobs route to the batch-summary parse."""
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
