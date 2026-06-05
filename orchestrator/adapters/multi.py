"""multi adapter — Stage-A casting (P1/M2, §5, R38/R105/R121).

`multi` is the casting pipeline: one run = **one batch → a pool of N candidates**, run
across a lineup of image pipelines (the `refined`/`fast` ideation presets:
flux2 + sd35 + zimage). Contrast `zimage`, which is one prompt → one image.

CONTRACT (the 1-page check, kb-loom-p0 §15):

- **Invocation**: a Python **module**, not a file path — `python -m
  pipeline.multi.run_pipeline ideate …` — because `multi` uses package-relative imports
  (`from .arch_batch import …`). The runner already runs workers with `cwd =
  script.parents[2]`; for `…/src/pipeline/multi/run_pipeline.py` that is `…/src`, exactly
  the cwd that makes `-m pipeline.multi.run_pipeline` resolve. `multi`'s own `stage_runner`
  is **self-locating** (derives flux2/sd35/zimage package paths + PYTHONPATH from its file
  location) and runs each sub-pipeline as an isolated subprocess (VRAM isolation), so we do
  not replicate any of that env wiring here.
- **Modes**: `ideate` (candidate pool only — the Stage-A casting default) | `batch`
  (ideate + optional clean/polish; clean/polish stay off in M2). Pool only for casting.
- **Output**: `multi_<run_id>.json` is written into `--output-dir` (the job's `out/<job>/`);
  candidate PNGs land under `--intermediate-root`. parse_result reads the manifest's
  **`ideate` stage** → `outputs.candidates[].output_path` for every `status == "ok"`
  candidate, so one job surfaces **N outputs** (the runner expands them into grid tiles).
- **Weights**: the preset pulls flux2 (FLUX.2-klein) + sd35 + zimage; flux2/sd3.5-large(-turbo)
  are HF-**gated** (need a token + license acceptance). The launch gate / `models.json` track
  them; a cast fails fast if a weight is missing (no silent half-run).
- Cancel = subprocess kill (handled by the runner); progress is coarse (`[batch]`/`[ideate]`
  stage prints), no per-candidate bar.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import CompletionRecord, JobSpec

PIPELINE = "multi"
# Casting fires the candidate pool only (ideate); clean/polish (batch toggles) are later.
WIRED_MODES = ("ideate",)
SUPPORTED_MODES = ("ideate", "batch")
WIRED_PARAMS = ("prompt", "num_candidates", "ideation_mode", "width", "height", "seed")
# Default ideation preset: `fast` (klein-4b / sd3.5-large-turbo / zimage-turbo) — lighter
# weights + quicker pools, the better fit for 16 GB + Stage-A free experimentation. The
# `refined` preset (klein-9b / sd3.5-large / zimage-base) is opt-in per cast.
DEFAULT_IDEATION_MODE = "fast"
MODULE = "pipeline.multi.run_pipeline"


def resolve_script(roots: list[Path]) -> Path | None:
    """First existing `multi/run_pipeline.py` across the ordered pipeline roots. The file
    is only located to (a) prove presence and (b) let the runner derive cwd = parents[2];
    the actual invocation is by **module** (see build_argv)."""
    for r in roots:
        p = r / "multi" / "run_pipeline.py"
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
        "multi_output": True,            # one job → a pool of candidates (runner expands)
    }


def progress(line: str) -> float | None:
    """Coarse progress from the batch's stage prints (no per-candidate event stream)."""
    s = line.strip()
    if "[batch] ideate produced" in s or "[ideate] produced" in s:
        return 0.9
    if "minted session" in s or "attached to session" in s or "[batch]" in s:
        return 0.2
    return None


def build_argv(spec: JobSpec, python: str, script: Path) -> list[str]:
    """Typed params → `python -m pipeline.multi.run_pipeline ideate …`.

    `script` is only used by the runner to set cwd (= `script.parents[2]` = `…/src`); the
    module path is fixed. Sessions land under the project (`<project>/_temp/multi_sessions`)
    so casts don't pollute the monorepo's default `src/state/sessions`."""
    p = spec.params
    mode = spec.mode if spec.mode in SUPPORTED_MODES else "ideate"
    out_dir = spec.output_dir                       # <project>/out/<job>
    project_dir = out_dir.parents[1]                # <project>
    argv: list[str] = [
        python, "-m", MODULE, mode,
        "--prompt", str(p["prompt"]),
        "--num-candidates", str(int(p.get("num_candidates", 1))),
        "--ideation-mode", str(p.get("ideation_mode", DEFAULT_IDEATION_MODE)),
        "--width", str(p.get("width", 1024)),
        "--height", str(p.get("height", 1024)),
        "--output-dir", str(out_dir),
        "--intermediate-root", str(out_dir / "_inter"),
        "--sessions-dir", str(project_dir / "_temp" / "multi_sessions"),
    ]
    if p.get("seed") is not None:
        argv += ["--seed", str(p["seed"])]
    return argv


def _find_manifest(output_dir: Path) -> Path | None:
    """The batch writes exactly one `multi_<run_id>.json` into the (per-job) output dir."""
    if output_dir.is_dir():
        ms = sorted(output_dir.glob("multi_*.json"), key=lambda f: f.stat().st_mtime)
        if ms:
            return ms[-1]
    return None


def parse_result(returncode: int, stdout: str, stderr: str, output_dir: Path) -> CompletionRecord:
    """Manifest-as-truth (like zimage), but a multi run yields a **pool**: collect every
    `ok` candidate's `output_path` from the `ideate` stage. ok = ideate completed + ≥1
    candidate succeeded + exit 0."""
    manifest_path = _find_manifest(output_dir)
    outputs: list[str] = []
    manifest_status: str | None = None
    error: str | None = None
    duration_s: float | None = None

    if manifest_path and manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            duration_s = data.get("pipeline_duration_s")
            stages = data.get("stages") or []
            ideate = next((s for s in stages if s.get("name") == "ideate"), None)
            if ideate is not None:
                manifest_status = ideate.get("status")
                cands = (ideate.get("outputs") or {}).get("candidates") or []
                for c in cands:
                    if c.get("status") == "ok" and c.get("output_path"):
                        if Path(c["output_path"]).is_file():
                            outputs.append(c["output_path"])
                if not outputs and ideate.get("status") == "completed":
                    error = "ideate completed but no candidate images on disk"
                elif ideate.get("status") == "failed":
                    error = ideate.get("error") or "ideate stage failed"
            else:
                error = "no ideate stage in multi manifest"
        except (json.JSONDecodeError, OSError) as e:
            error = f"multi manifest unreadable: {e}"
    else:
        error = "no multi manifest produced"

    ok = bool(outputs) and manifest_status == "completed" and returncode == 0
    if not ok and error is None:
        if returncode != 0:
            error = f"worker exited {returncode}"
        elif manifest_status is None:
            error = "no manifest produced"

    return CompletionRecord(
        ok=ok,
        returncode=returncode,
        outputs=outputs,
        manifest_path=str(manifest_path) if manifest_path else None,
        duration_s=duration_s,
        manifest_status=manifest_status,
        error=error,
        stderr_tail=(stdout or stderr or "")[-1500:],
    )
