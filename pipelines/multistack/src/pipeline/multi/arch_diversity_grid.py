"""Architecture A.0 — Diversity grid: same prompt × Flux2 + SD 3.5 + Z-Image.

Phase A validation architecture. Uses only the existing T2I modes of each
pipeline (no per-pipeline mode changes required), runs them sequentially with
process-level VRAM isolation, and stitches the three outputs into a single
horizontal grid PNG.

Failure of any single sub-pipeline does not abort the whole run — remaining
pipelines still execute and the manifest records the failed stage with its
error.
"""

from __future__ import annotations

import gc
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from .. import _artifact_id
from .intermediates import IntermediateStore
from .manifest import MultiPipelineManifest
from .sessions import (
    RunRecord, SessionManifest, SESSIONS_DIR_DEFAULT,
    ingest_pipeline_manifest, open_or_create,
)
from .stage_runner import invoke_flux2, invoke_sd35, invoke_zimage


PIPELINE_SPECS = [
    # (stage_name, invoker, default-kwargs-for-T2I)
    #
    # Each pipeline runs at its model-card defaults (resolved per-pipeline by
    # passing num_steps/guidance_scale=None down to run_pipeline.run()):
    #   - flux.2-klein-9b: 4 distilled steps, guidance 1.0
    #   - sd3.5-medium:    40 steps, CFG 4.5, SLG [7,8,9] (anatomy fix ON)
    #   - zimage-base:     50 steps, guidance 4.0, supports negative prompts
    ("flux2",  invoke_flux2,  {"model_name": "flux.2-klein-9b"}),
    ("sd35",   invoke_sd35,   {"model_name": "sd3.5-medium"}),
    ("zimage", invoke_zimage, {"model_name": "zimage-base"}),
]


def _hstack(images: list[Image.Image], gap: int = 8, bg=(0, 0, 0)) -> Image.Image:
    """Horizontal stack with a gap between panels. Panels are top-aligned."""
    if not images:
        return Image.new("RGB", (1, 1), bg)
    h = max(im.height for im in images)
    w = sum(im.width for im in images) + gap * (len(images) - 1)
    out = Image.new("RGB", (w, h), bg)
    x = 0
    for im in images:
        out.paste(im.convert("RGB"), (x, 0))
        x += im.width + gap
    return out


def run_diversity_grid(
    *,
    prompt: str,
    seed: int,
    width: int = 512,
    height: int = 512,
    output_dir: Path | str = "src/assets/pics",
    intermediate_root: Path | str = "src/assets/pics/intermediate",
    sessions_dir: Path | str = SESSIONS_DIR_DEFAULT,
    session_id: str | None = None,
    continue_from_run: str | None = None,
    keep_intermediates: bool = False,
    pipelines: list[str] | None = None,
) -> MultiPipelineManifest:
    """Run the same prompt through each pipeline and produce a stitched grid.

    Args:
        prompt: text prompt sent to all three pipelines verbatim.
        seed: shared seed for reproducibility (each sub-pipeline uses it).
        width / height: per-panel resolution (low default keeps smoke tests fast).
        output_dir: where the final grid PNG + combined manifest are written.
        intermediate_root: parent directory for the per-run intermediate store.
        sessions_dir: where session manifests live (default
            src/state/sessions/).
        session_id: attach to an existing session by id; if None and no
            continue_from_run, mint a new session.
        continue_from_run: open the session that contains this multi-image
            run_id (parent_run_id is set to it). diversity-grid does not
            inherit upstream stages, so this only affects session grouping.
        keep_intermediates: if False, the per-run intermediate dir is removed
            after a successful grid is written.
        pipelines: subset of ["flux2","sd35","zimage"] to run. None = all.

    Returns the completed MultiPipelineManifest.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    arch_run_id = _artifact_id.mint_run_id(seed)
    run_id = f"diversity_grid_{timestamp}_s{seed}"
    grid_path = output_dir / f"multi_{run_id}.png"
    manifest_path = grid_path.with_suffix(".json")

    # --- Session manifest setup ---
    session, was_created = open_or_create(
        session_id, sessions_dir=sessions_dir,
        continue_from_run=continue_from_run,
    )
    if was_created:
        print(f"[arch] minted new session {session.session_id}")
    else:
        print(f"[arch] attached to session {session.session_id}")

    parent_manifest_path = None
    if continue_from_run:
        prr = session.get_run(continue_from_run)
        if prr is not None:
            parent_manifest_path = prr.manifest_path

    run_record = RunRecord(
        run_id=arch_run_id,
        architecture="diversity-grid",
        parent_run_id=continue_from_run,
        parent_stage=None,           # diversity-grid has no inherited upstream stage
        parent_manifest_path=parent_manifest_path,
        entry_stage="generate", exit_stage="stitch_grid",
        status="running",
        started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        manifest_path=str(manifest_path),
    )

    store = IntermediateStore(intermediate_root, run_id=run_id)

    selected = pipelines or [name for name, _, _ in PIPELINE_SPECS]
    specs = [(n, inv, kw) for (n, inv, kw) in PIPELINE_SPECS if n in selected]

    manifest = MultiPipelineManifest(
        architecture="diversity-grid",
        prompt=prompt,
        seed=seed,
        created_at=datetime.now(timezone.utc).isoformat(),
        intermediate_dir=str(store),
        keep_intermediates=keep_intermediates,
    )
    manifest.pipeline_start = time.time()

    panel_paths: list[str] = []
    panel_owners: list[str] = []  # parallel to panel_paths

    for stage_name, invoker, default_kwargs in specs:
        sub_dir = Path(str(store)) / stage_name
        rec = manifest.begin_stage(
            stage_name,
            inputs={
                "prompt": prompt,
                "seed": seed,
                "width": width,
                "height": height,
                "output_dir": str(sub_dir),
                **default_kwargs,
            },
        )
        try:
            result = invoker(
                prompt=prompt,
                output_dir=sub_dir,
                seed=seed,
                width=width,
                height=height,
                **default_kwargs,
            )
            if result["returncode"] != 0 or not result["output_path"]:
                err = (
                    f"subprocess returncode={result['returncode']}; "
                    f"stderr tail: ...{(result['stderr'] or '')[-400:]}"
                )
                manifest.fail_stage(rec, err)
                print(f"[arch] {stage_name} FAILED -- {err}")
                continue

            # Ingest the per-pipeline manifest's artifacts into the session.
            produced_aids = ingest_pipeline_manifest(
                session, arch_run_id=arch_run_id,
                pipeline_manifest_path=result["manifest_path"],
            )
            manifest.end_stage(
                rec,
                outputs={
                    "output_path": result["output_path"],
                    "sub_manifest_path": result["manifest_path"],
                    "subprocess_duration_s": result["duration_s"],
                    "produced_artifact_ids": produced_aids,
                },
            )
            run_record.session_manifest_stages.append({
                "stage": stage_name,
                "source": "this_run",
                "produced_run_manifest_paths": [result["manifest_path"]],
                "produced_artifact_ids": produced_aids,
                "produced_path": result["output_path"],
            })
            panel_paths.append(result["output_path"])
            panel_owners.append(stage_name)
            print(f"[arch] {stage_name} OK -- {result['output_path']}")
        except Exception as e:  # noqa: BLE001 -- we want to record any failure
            manifest.fail_stage(rec, str(e))
            print(f"[arch] {stage_name} EXCEPTION -- {e}")

    # --- Stitch the grid ---
    rec = manifest.begin_stage(
        "stitch_grid",
        inputs={"panel_paths": panel_paths, "panel_owners": panel_owners},
    )
    try:
        if not panel_paths:
            raise RuntimeError("no panels produced -- every sub-pipeline failed")
        # Load each panel via a context manager + eager convert so the source
        # file handle is released before we attempt to clean up intermediates
        # below. Windows holds locks on PIL's lazy-loaded file pointers.
        panels = []
        for p in panel_paths:
            with Image.open(p) as src:
                panels.append(src.convert("RGB"))
        grid = _hstack(panels)
        grid.save(grid_path)
        # Record the final grid as a session-level artifact.
        final_record = _artifact_id.make_artifact_record(
            grid_path, kind="image/png", produced_by_stage="stitch_grid",
        )
        ar = session.upsert_artifact(
            rec_dict=final_record,
            originated_in_run=arch_run_id,
            arch_run_id=arch_run_id,
        )
        run_record.final_output_path = str(grid_path)
        run_record.final_artifact_id = ar.artifact_id
        run_record.session_manifest_stages.append({
            "stage": "stitch_grid",
            "source": "this_run",
            "consumed_artifact_ids": [
                a for s in run_record.session_manifest_stages
                for a in s.get("produced_artifact_ids", [])
            ],
            "produced_artifact_ids": [ar.artifact_id],
            "produced_path": str(grid_path),
            "panel_count": len(panels),
        })
        manifest.end_stage(
            rec,
            outputs={"output_path": str(grid_path), "panel_count": len(panels),
                     "final_artifact_id": ar.artifact_id},
        )
        manifest.output_path = str(grid_path)
        print(f"[arch] stitched grid -> {grid_path}")
    except Exception as e:  # noqa: BLE001
        manifest.fail_stage(rec, str(e))
        print(f"[arch] stitch FAILED -- {e}")

    manifest.pipeline_end = time.time()
    manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
    manifest.save(manifest_path)

    # --- Finalize the session run record + persist the session manifest ---
    grid_succeeded_local = manifest.stages and manifest.stages[-1].name == "stitch_grid" \
        and manifest.stages[-1].status == "completed"
    run_record.status = "completed" if grid_succeeded_local else "failed"
    run_record.finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    session.append_run(run_record)
    session_path = session.save(sessions_dir=sessions_dir)
    print(f"[arch] session saved -> {session_path}")

    # --- Cleanup ---
    grid_succeeded = grid_succeeded_local
    if grid_succeeded and not keep_intermediates:
        # Drop any in-memory PIL refs so Windows releases file locks before rmtree
        for _name in ("panels", "grid"):
            if _name in locals():
                del locals()[_name]
        gc.collect()
        if store.cleanup():
            print(f"[arch] cleaned up {store}")
        else:
            print(f"[arch] cleanup partial -- intermediates remain at {store}")
    else:
        print(f"[arch] intermediates kept at {store}")

    print(f"[done] diversity-grid completed in {manifest.pipeline_duration_s}s")
    print(f"  Grid: {grid_path}")
    print(f"  Manifest: {manifest_path}")
    return manifest
