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
import re
from pathlib import Path

from .base import CompletionRecord, JobSpec

try:
    from .. import model_catalog
except ImportError:  # pragma: no cover - direct-run convenience
    import model_catalog  # type: ignore

PIPELINE = "multi"
# The API mode stays `ideate`; build_argv picks the `batch` subcommand when the
# clean/polish toggles are on (they're params, not a separate mode — one cast, one job).
WIRED_MODES = ("ideate",)
SUPPORTED_MODES = ("ideate", "batch")
WIRED_PARAMS = ("prompt", "num_candidates", "ideation_mode",
                *(p["name"] for p in model_catalog.MULTI_PARAMS))
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
    """Coarse progress from the batch's stage prints (no per-candidate event stream).
    Kept for the capabilities contract / fallback; the runner prefers `make_progress`."""
    s = line.strip()
    if "[batch] ideate produced" in s or "[ideate] produced" in s:
        return 0.9
    # NB the real print is "minted NEW session" (arch_*: open_or_create) — the old
    # "minted session" substring never matched; "[batch]" caught it in practice.
    if "minted new session" in s or "attached to session" in s or "[batch]" in s:
        return 0.2
    return None


# Both ideation presets (fast | refined) run the full 3-pipeline lineup (flux2+sd35+zimage).
_PRESET_PIPELINES = 3

# Per-image completion lines in the merged stdout. Ideate children stream live (their
# stdout passes through) and print `  Image: <path>`; clean/polish sub-runs are PIPED by
# the batch driver, so their images surface via its `[batch] clean OK … -> <path>` lines.
_IMAGE_LINE = re.compile(r"^\s*Image:\s*(.+?\.png)\s*$")
_BATCH_PASS_LINE = re.compile(r"^\[batch\] (clean|polish) (OK|FAIL)\b(?:.*?->\s*(.+?\.png)\s*$)?")


def _pass_units(params: dict) -> tuple[int, int]:
    """(per-pass candidate count, number of passes) for the progress denominator."""
    n = max(1, int(params.get("num_candidates", 1))) * _PRESET_PIPELINES
    passes = 1 + (1 if params.get("clean") else 0) + (1 if params.get("polish") else 0)
    return n, passes


def make_progress(params: dict):
    """Stateful per-candidate progress (review 2026-06-10): a cast = `num_candidates ×
    3 pipelines` sequential one-shot subprocesses (× extra clean/polish passes when
    toggled), and the coarse markers left the bar at 20% for the whole run. Count the
    per-image completions against the known total → a real fraction. Failures still
    advance (the next spawn banner / the FAIL line), so nothing freezes the bar."""
    n, passes = _pass_units(params)
    total = n * passes
    state = {"done": 0, "started": 0}

    def _frac() -> float:
        return min(0.05 + 0.90 * state["done"] / total, 0.95)

    def _progress(line: str) -> float | None:
        s = line.strip()
        if "[done] Pipeline completed" in s:
            # an ideate child finished (clean/polish children are piped, never seen here)
            state["done"] = min(state["done"] + 1, total)
            return _frac()
        if _BATCH_PASS_LINE.match(line):
            state["done"] = min(state["done"] + 1, total)   # one clean/polish image done
            return _frac()
        if "[stage_runner] $" in s:
            state["started"] += 1
            return min(0.05 + 0.90 * (state["started"] - 1) / total, 0.95)
        if "[batch] ideate produced" in s or "[ideate] produced" in s:
            # ideate pass complete — snap the counter to the pass boundary (failed
            # candidates never print [done], so don't let them stall the fraction)
            state["done"] = max(state["done"], n)
            return _frac() if passes > 1 else 0.95
        if "minted new session" in s or "attached to session" in s:
            return 0.02
        return None

    return _progress


def collect_output(line: str) -> str | None:
    """Interim-result hook (user request 2026-06-10): return the absolute image path when
    `line` announces a finished image, so the runner can surface the pool **while the cast
    is still running** instead of only at the end. Matches the ideate children's live
    `  Image: <path>` prints and the batch driver's `[batch] clean|polish OK … -> <path>`
    lines. The runner guards the path (must resolve under the project out/)."""
    m = _IMAGE_LINE.match(line)
    if m:
        return m.group(1)
    m = _BATCH_PASS_LINE.match(line)
    if m and m.group(2) == "OK" and m.group(3):
        return m.group(3)
    return None


def build_argv(spec: JobSpec, python: str, script: Path) -> list[str]:
    """Typed params → `python -m pipeline.multi.run_pipeline ideate …`.

    Loom always invokes the **`ideate`** subcommand (2026-06-11): clean/polish are
    orchestrator-chained post-passes now (separate batch img2img jobs over the pool —
    they stream per item, unlike the worker's piped in-worker passes), so the `batch`
    subcommand's toggles are never used here. width/height/seed flow through
    `model_catalog.emit_argv("multi", …)` (post-marked params are skipped there).

    `script` is only used by the runner to set cwd (= `script.parents[2]` = `…/src`); the
    module path is fixed. Sessions land under the project (`<project>/_temp/multi_sessions`)
    so casts don't pollute the monorepo's default `src/state/sessions`."""
    p = spec.params
    mode = spec.mode if spec.mode in SUPPORTED_MODES else "ideate"
    out_dir = spec.output_dir                       # <project>/out/<job>
    project_dir = out_dir.parents[1]                # <project>
    argv: list[str] = [
        python, "-m", MODULE, "ideate",
        "--prompt", str(p["prompt"]),
        "--num-candidates", str(int(p.get("num_candidates", 1))),
        "--ideation-mode", str(p.get("ideation_mode", DEFAULT_IDEATION_MODE)),
        "--output-dir", str(out_dir),
        "--intermediate-root", str(out_dir / "_inter"),
        "--sessions-dir", str(project_dir / "_temp" / "multi_sessions"),
    ]
    argv += model_catalog.emit_argv("multi", p, mode)
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
    `ok` candidate's `output_path` from the `ideate` stage, plus — when the batch ran with
    the clean/polish toggles — every ok `cleaned[]`/`polished[]` output (the whole batch is
    the deliverable; each pass yields its own starrable tile). ok = ideate completed + ≥1
    candidate succeeded + exit 0 (per-image clean/polish failures are non-fatal, mirroring
    the worker's own exit logic)."""
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
            # clean/polish passes (batch subcommand): `cleaned[]` / `polished[]` records.
            for stage_name, key in (("clean", "cleaned"), ("polish", "polished")):
                st = next((s for s in stages if s.get("name") == stage_name), None)
                if st is None:
                    continue
                for r in (st.get("outputs") or {}).get(key) or []:
                    op = r.get("output_path")
                    if r.get("status") == "ok" and op and Path(op).is_file():
                        outputs.append(op)
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
