"""Reusable candidate-generation helper.

Runs the same prompt through multiple pipelines (and optionally multiple seeds)
to produce a pool of candidate images. Used by:

  - arch_diversity_grid: 1 seed × 3 pipelines = 3 panels
  - arch_compose_character: N seeds × 3 pipelines = 3N candidates

Each candidate is returned as a structured dict so callers can build manifest
StageRecords without re-parsing subprocess output.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .stage_runner import invoke_flux2, invoke_sd35, invoke_zimage


# Default pipeline lineup -- each pipeline uses its model-card defaults
# (num_steps and guidance_scale fall back to per-pipeline registry defaults
# when not overridden by the caller).
DEFAULT_PIPELINE_SPECS = [
    ("flux2",  invoke_flux2,  {"model_name": "flux.2-klein-9b"}),
    ("sd35",   invoke_sd35,   {"model_name": "sd3.5-medium"}),
    ("zimage", invoke_zimage, {"model_name": "zimage-base"}),
]


@dataclass
class Candidate:
    """One generated image. Status='ok' on success, 'failed' otherwise."""
    pipeline: str       # "flux2" | "sd35" | "zimage"
    seed: int
    candidate_index: int  # 0-based index within the per-architecture run
    status: str           # "ok" | "failed"
    output_path: str      # "" if failed
    manifest_path: str    # "" if failed
    duration_s: float
    error: str = ""


def generate_candidates(
    *,
    prompt: str,
    seeds: list[int],
    width: int,
    height: int,
    output_root: Path,
    pipeline_specs: list[tuple[str, callable, dict]] | None = None,
    pipelines_filter: list[str] | None = None,
    extra_invoker_kwargs: dict[str, dict] | None = None,
) -> list[Candidate]:
    """Generate len(seeds) * len(pipelines) candidate images.

    Args:
        prompt: text prompt sent to every pipeline verbatim.
        seeds: one or more seeds. Each seed × pipeline produces one candidate.
        width / height: per-candidate resolution (uniform across pipelines).
        output_root: parent directory; each candidate lands in
            ``<output_root>/<pipeline>/seed_<seed>/<pipeline-output>.png``.
        pipeline_specs: tuple of (name, invoker, default_kwargs) entries.
            Defaults to DEFAULT_PIPELINE_SPECS.
        pipelines_filter: subset of pipeline names to actually run. None = all.
        extra_invoker_kwargs: per-pipeline-name dict of additional kwargs to
            pass through to the invoker (e.g.,
            ``{"zimage": {"cfg_normalization": True}}``).

    Returns: list of Candidate. Failures don't abort the loop -- subsequent
        pipelines and seeds still run, and the failure is recorded in the list.
    """
    pipeline_specs = pipeline_specs or DEFAULT_PIPELINE_SPECS
    extra_invoker_kwargs = extra_invoker_kwargs or {}

    if pipelines_filter is not None:
        pipeline_specs = [s for s in pipeline_specs if s[0] in pipelines_filter]

    results: list[Candidate] = []
    candidate_index = 0
    for seed in seeds:
        for stage_name, invoker, default_kwargs in pipeline_specs:
            sub_dir = Path(output_root) / stage_name / f"seed_{seed}"
            kwargs = {**default_kwargs, **extra_invoker_kwargs.get(stage_name, {})}
            try:
                result = invoker(
                    prompt=prompt,
                    output_dir=sub_dir,
                    seed=seed,
                    width=width,
                    height=height,
                    **kwargs,
                )
                if result["returncode"] != 0 or not result["output_path"]:
                    err_tail = (result.get("stderr") or "")[-400:]
                    results.append(Candidate(
                        pipeline=stage_name, seed=seed,
                        candidate_index=candidate_index, status="failed",
                        output_path="", manifest_path="",
                        duration_s=result.get("duration_s", 0.0),
                        error=f"rc={result['returncode']}; stderr tail: ...{err_tail}",
                    ))
                else:
                    results.append(Candidate(
                        pipeline=stage_name, seed=seed,
                        candidate_index=candidate_index, status="ok",
                        output_path=result["output_path"],
                        manifest_path=result["manifest_path"],
                        duration_s=result["duration_s"],
                    ))
            except Exception as e:  # noqa: BLE001 -- record any failure
                results.append(Candidate(
                    pipeline=stage_name, seed=seed,
                    candidate_index=candidate_index, status="failed",
                    output_path="", manifest_path="",
                    duration_s=0.0, error=str(e),
                ))
            candidate_index += 1
    return results


def successful(candidates: list[Candidate]) -> list[Candidate]:
    """Filter to successful candidates only (status == 'ok')."""
    return [c for c in candidates if c.status == "ok"]
