"""Architecture 1 — "Compose Character".

Per kb-multi-image.md, this architecture composes a complex character through
a 4-stage flow:

  1. ideate  -- 3xN candidates via Flux2 + SD 3.5 + Z-Image-Base @ same seed
                (per user request, variety across pipelines is more valuable
                than per-pipeline N at this stage; user can still bump N).
  2. select  -- pick 1 candidate (auto = first successful, or user-supplied).
  3. clean   -- Z-Image-Base img2img with strength=0.35, negative prompt, and
                cfg_normalization=True for realism cleanup.
  4. compose -- Flux2-dev multi-ref editing of [character_clean, outfit_ref,
                background_ref] with a JSON prompt.

**Status (2026-04-25):** Stages 1-3 are fully implemented. Stage 4 requires
Phase B.3 (Flux2 multi-ref + JSON prompt support in
``src/pipeline/flux2/run_pipeline.py``), which has not landed yet. Until then,
stage 4 is a deferred no-op that copies the cleaned character to ``final.png``
and records a "deferred_due_to_phase_b3" status in the manifest. The
architecture is otherwise end-to-end runnable.
"""

from __future__ import annotations

import gc
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from .. import _artifact_id
from .candidates import Candidate, generate_candidates, successful
from .intermediates import IntermediateStore
from .manifest import MultiPipelineManifest
from .sessions import (
    RunRecord, SESSIONS_DIR_DEFAULT,
    ingest_pipeline_manifest, open_or_create,
)
from .stage_runner import invoke_flux2, invoke_sd35, invoke_zimage


# Stage 3 (clean) defaults -- per kb-multi-image §Architecture 1
CLEAN_DEFAULTS = {
    "model_name":       "zimage-base",
    "strength":         0.35,
    "negative_prompt":  "blurry, deformed hands, extra fingers, low contrast, low quality",
    "cfg_normalization": True,
}


# Stage 1 (ideate) ideation-mode presets. Each preset names three pipelines;
# every candidate runs at its model-card defaults (no per-mode override of
# steps / guidance -- the per-pipeline registry resolves those automatically).
#
#   - "fast":    Flux2-Klein-4B + SD 3.5 Medium + Z-Image-Turbo
#                Smaller / distilled variants where available; ideal when
#                you want quick iteration and stylistic variety still
#                across three pipeline families.
#   - "refined": Flux2-Klein-9B + SD 3.5 Large-Turbo + Z-Image-Base
#                Larger / higher-fidelity variants. Z-Image-Base in
#                particular gives the non-distilled 50-step path with
#                support for negative prompts and cfg_normalization.
#                This is the default.
IDEATION_PRESETS = {
    "fast": [
        ("flux2",  invoke_flux2,  {"model_name": "flux.2-klein-4b"}),
        ("sd35",   invoke_sd35,   {"model_name": "sd3.5-large-turbo"}),
        ("zimage", invoke_zimage, {"model_name": "zimage-turbo"}),
    ],
    "refined": [
        ("flux2",  invoke_flux2,  {"model_name": "flux.2-klein-9b"}),
        ("sd35",   invoke_sd35,   {"model_name": "sd3.5-large"}),
        ("zimage", invoke_zimage, {"model_name": "zimage-base"}),
    ],
}
DEFAULT_IDEATION_MODE = "refined"


def _setup_session(
    *, seed: int, architecture: str, entry_stage: str, exit_stage: str,
    sessions_dir, session_id, continue_from_run, manifest_path,
) -> tuple:
    """Mint or attach a session and build a fresh RunRecord. Returns
    (session, run_record, was_created, parent_manifest_path)."""
    from .sessions import open_or_create  # local import to keep shape stable
    session, was_created = open_or_create(
        session_id, sessions_dir=sessions_dir,
        continue_from_run=continue_from_run,
    )
    parent_manifest_path = None
    parent_stage = None
    if continue_from_run:
        prr = session.get_run(continue_from_run)
        if prr is not None:
            parent_manifest_path = prr.manifest_path
            parent_stage = prr.exit_stage
    arch_run_id = _artifact_id.mint_run_id(seed)
    run_record = RunRecord(
        run_id=arch_run_id,
        architecture=architecture,
        parent_run_id=continue_from_run,
        parent_stage=parent_stage,
        parent_manifest_path=parent_manifest_path,
        entry_stage=entry_stage, exit_stage=exit_stage,
        status="running",
        started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        manifest_path=str(manifest_path),
    )
    return session, run_record, was_created, arch_run_id


def _select_candidate(
    candidates: list[Candidate],
    selected_path: str | None,
) -> Candidate | None:
    """Pick a single candidate. Explicit path wins; otherwise first ok."""
    ok = successful(candidates)
    if not ok:
        return None
    if selected_path:
        sel = Path(selected_path).resolve()
        for c in candidates:
            if c.output_path and Path(c.output_path).resolve() == sel:
                return c
        # Path didn't match any candidate
        raise FileNotFoundError(
            f"--selected-panel {selected_path!r} did not match any successful "
            f"candidate. Available paths:\n  " +
            "\n  ".join(c.output_path for c in ok)
        )
    return ok[0]


def _candidate_summary(c: Candidate) -> dict:
    return {
        "pipeline": c.pipeline,
        "seed": c.seed,
        "candidate_index": c.candidate_index,
        "status": c.status,
        "output_path": c.output_path,
        "sub_manifest_path": c.manifest_path,
        "duration_s": c.duration_s,
        "error": c.error,
    }


def run_compose_character(
    *,
    prompt: str,
    seed: int,
    num_candidates: int = 1,
    ideation_mode: str = DEFAULT_IDEATION_MODE,
    width: int = 1024,
    height: int = 1024,
    selected_panel: str | None = None,
    clean_strength: float = CLEAN_DEFAULTS["strength"],
    clean_negative_prompt: str = CLEAN_DEFAULTS["negative_prompt"],
    clean_cfg_normalization: bool = CLEAN_DEFAULTS["cfg_normalization"],
    outfit_image: str | None = None,       # reserved for stage 4 (deferred)
    background_image: str | None = None,   # reserved for stage 4 (deferred)
    output_dir: Path | str = "src/assets/pics",
    intermediate_root: Path | str = "src/assets/pics/intermediate",
    sessions_dir: Path | str = SESSIONS_DIR_DEFAULT,
    session_id: str | None = None,
    continue_from_run: str | None = None,
    keep_intermediates: bool = False,
) -> MultiPipelineManifest:
    """Run Architecture 1: Compose Character.

    Args:
        prompt: text prompt sent to all three ideate pipelines verbatim.
        seed: base seed. With num_candidates > 1, additional candidates use
            seed+1, seed+2, ... so reruns at the same base seed are
            reproducible.
        num_candidates: how many seeds to fan out across all 3 pipelines.
            num_candidates=1 -> 3 candidates (one per pipeline).
            num_candidates=N -> 3*N candidates.
        ideation_mode: one of IDEATION_PRESETS keys ("fast" or "refined").
            Selects which trio of pipeline checkpoints generates the candidate
            pool at stage 1. See IDEATION_PRESETS for the model lineup of each.
        selected_panel: optional path to one of the candidate PNGs. If set,
            that candidate is used as the input to the clean stage. If
            omitted, the first successful candidate is auto-selected.
        clean_strength: img2img strength for the clean stage. 0.35 default
            preserves silhouette while letting neg-prompt + cfg_normalization
            fix small artifacts.
        clean_negative_prompt: negative prompt fed to Z-Image-Base in stage 3.
        clean_cfg_normalization: set True for realism, False for stylism.
        outfit_image / background_image: reserved for the deferred stage 4
            (Flux2-dev multi-ref compose). Recorded in the manifest now so
            the stage 4 input is wired up the moment Phase B.3 lands.
        output_dir: where the final PNG + combined manifest are written.
        intermediate_root: parent directory for the per-run intermediate
            store (candidates land under here).
        keep_intermediates: preserve the intermediate dir after success.
    """
    if ideation_mode not in IDEATION_PRESETS:
        raise ValueError(
            f"unknown ideation_mode {ideation_mode!r}; "
            f"must be one of {list(IDEATION_PRESETS)}"
        )
    ideation_specs = IDEATION_PRESETS[ideation_mode]

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    arch_run_id = _artifact_id.mint_run_id(seed)
    run_id = f"compose_character_{timestamp}_s{seed}"
    final_path = output_dir / f"multi_{run_id}.png"
    manifest_path = final_path.with_suffix(".json")

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
        architecture="compose-character",
        parent_run_id=continue_from_run,
        parent_stage=None,           # not yet inferred for compose-character; stage subcommands set this
        parent_manifest_path=parent_manifest_path,
        entry_stage="ideate", exit_stage="compose",
        status="running",
        started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        manifest_path=str(manifest_path),
    )

    store = IntermediateStore(intermediate_root, run_id=run_id)
    seeds = [seed + i for i in range(max(1, num_candidates))]

    manifest = MultiPipelineManifest(
        architecture="compose-character",
        prompt=prompt,
        seed=seed,
        created_at=datetime.now(timezone.utc).isoformat(),
        intermediate_dir=str(store),
        keep_intermediates=keep_intermediates,
    )
    manifest.pipeline_start = time.time()

    # =========================================================================
    # Stage 1: ideate  -- 3 x num_candidates panels via diversity-grid pipelines
    # =========================================================================
    rec = manifest.begin_stage(
        "ideate",
        inputs={
            "prompt": prompt,
            "seeds": seeds,
            "num_candidates": num_candidates,
            "ideation_mode": ideation_mode,
            "pipelines": [name for name, _, _ in ideation_specs],
            "model_names": {name: kw["model_name"] for name, _, kw in ideation_specs},
            "width": width,
            "height": height,
        },
    )
    candidates = generate_candidates(
        prompt=prompt,
        seeds=seeds,
        width=width,
        height=height,
        output_root=Path(str(store)) / "ideate",
        pipeline_specs=ideation_specs,
    )
    n_ok = sum(1 for c in candidates if c.status == "ok")
    n_fail = len(candidates) - n_ok
    print(f"[arch] ideate produced {n_ok} ok / {n_fail} failed candidate(s)")

    # Ingest each successful candidate's per-pipeline manifest into the session.
    candidate_aids: list[str] = []
    candidate_run_manifests: list[str] = []
    for c in candidates:
        if c.status == "ok" and c.manifest_path:
            aids = ingest_pipeline_manifest(
                session, arch_run_id=arch_run_id,
                pipeline_manifest_path=c.manifest_path,
            )
            candidate_aids.extend(aids)
            candidate_run_manifests.append(c.manifest_path)

    manifest.end_stage(
        rec,
        outputs={
            "candidate_count": len(candidates),
            "succeeded": n_ok,
            "failed": n_fail,
            "candidates": [_candidate_summary(c) for c in candidates],
            "produced_artifact_ids": candidate_aids,
        },
    )
    run_record.session_manifest_stages.append({
        "stage": "ideate",
        "source": "this_run",
        "ideation_mode": ideation_mode,
        "produced_run_manifest_paths": candidate_run_manifests,
        "produced_artifact_ids": candidate_aids,
    })

    # =========================================================================
    # Stage 2: select  -- pick 1 panel (auto-first or --selected-panel)
    # =========================================================================
    rec = manifest.begin_stage(
        "select",
        inputs={
            "selected_panel": selected_panel,
            "auto_select_strategy": "first_successful" if not selected_panel else "explicit",
        },
    )
    try:
        chosen = _select_candidate(candidates, selected_panel)
        if chosen is None:
            raise RuntimeError("ideate stage produced 0 successful candidates")
        # Find the chosen candidate's artifact_id by matching path through
        # the session's artifacts dict (stable lookup that survives renames).
        chosen_aid = None
        for aid, ar in session.artifacts.items():
            if Path(ar.path).resolve() == Path(chosen.output_path).resolve():
                chosen_aid = aid
                break
        manifest.end_stage(
            rec,
            outputs={
                "chosen_pipeline": chosen.pipeline,
                "chosen_seed": chosen.seed,
                "chosen_path": chosen.output_path,
                "chosen_candidate_index": chosen.candidate_index,
                "chosen_artifact_id": chosen_aid,
            },
        )
        run_record.session_manifest_stages.append({
            "stage": "select",
            "source": "this_run",
            "consumed_artifact_ids": [chosen_aid] if chosen_aid else [],
            "chosen_run_id": None,    # not exposed by Candidate dataclass; OK
            "chosen_artifact_id": chosen_aid,
            "chosen_path": chosen.output_path,
        })
        print(f"[arch] select chose: {chosen.pipeline} seed={chosen.seed} -> {chosen.output_path}")
    except Exception as e:  # noqa: BLE001
        manifest.fail_stage(rec, str(e))
        print(f"[arch] select FAILED -- {e}")
        manifest.pipeline_end = time.time()
        manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
        manifest.save(manifest_path)
        run_record.status = "failed"
        run_record.finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        run_record.exit_stage = "select"
        session.append_run(run_record)
        session.save(sessions_dir=sessions_dir)
        if not keep_intermediates:
            store.cleanup()
        return manifest

    # =========================================================================
    # Stage 3: clean   -- Z-Image-Base img2img with neg prompt + cfg_normalization
    # =========================================================================
    clean_dir = Path(str(store)) / "clean"
    rec = manifest.begin_stage(
        "clean",
        inputs={
            "model_name": CLEAN_DEFAULTS["model_name"],
            "init_image": chosen.output_path,
            "strength": clean_strength,
            "negative_prompt": clean_negative_prompt,
            "cfg_normalization": clean_cfg_normalization,
            "prompt": prompt,
        },
    )
    try:
        result = invoke_zimage(
            prompt=prompt,
            output_dir=clean_dir,
            seed=chosen.seed,
            width=width,
            height=height,
            model_name=CLEAN_DEFAULTS["model_name"],
            negative_prompt=clean_negative_prompt,
            cfg_normalization=clean_cfg_normalization,
            extra_args=[
                "--mode", "img2img",
                "--init-image", chosen.output_path,
                "--strength", str(clean_strength),
            ],
        )
        if result["returncode"] != 0 or not result["output_path"]:
            err_tail = (result.get("stderr") or "")[-400:]
            raise RuntimeError(
                f"zimage img2img clean stage failed: rc={result['returncode']} "
                f"stderr tail: ...{err_tail}"
            )
        # Ingest the clean stage's per-pipeline manifest into the session.
        clean_aids = ingest_pipeline_manifest(
            session, arch_run_id=arch_run_id,
            pipeline_manifest_path=result["manifest_path"],
        )
        manifest.end_stage(
            rec,
            outputs={
                "output_path": result["output_path"],
                "sub_manifest_path": result["manifest_path"],
                "subprocess_duration_s": result["duration_s"],
                "produced_artifact_ids": clean_aids,
            },
        )
        run_record.session_manifest_stages.append({
            "stage": "clean",
            "source": "this_run",
            "consumed_artifact_ids": [chosen_aid] if chosen_aid else [],
            "produced_run_manifest_paths": [result["manifest_path"]],
            "produced_artifact_ids": clean_aids,
            "produced_path": result["output_path"],
        })
        cleaned_path = result["output_path"]
        cleaned_aid = clean_aids[0] if clean_aids else None
        print(f"[arch] clean OK -- {cleaned_path}")
    except Exception as e:  # noqa: BLE001
        manifest.fail_stage(rec, str(e))
        print(f"[arch] clean FAILED -- {e}")
        manifest.pipeline_end = time.time()
        manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
        manifest.save(manifest_path)
        run_record.status = "failed"
        run_record.finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        run_record.exit_stage = "clean"
        session.append_run(run_record)
        session.save(sessions_dir=sessions_dir)
        if not keep_intermediates:
            store.cleanup()
        return manifest

    # =========================================================================
    # Stage 4: compose -- Flux2-dev multi-ref editing  [DEFERRED until Phase B.3]
    # =========================================================================
    # Flux2-dev multi-reference editing requires `--ref-image` + `--prompt-json`
    # support in src/pipeline/flux2/run_pipeline.py, which is Phase B.3 in the
    # multi-pipeline implementation plan and has not landed yet. Until then,
    # the cleaned character from stage 3 is the architecture's final output.
    # The outfit / background reference paths are recorded so they can be
    # wired straight into Flux2 once B.3 ships.
    rec = manifest.begin_stage(
        "compose",
        inputs={
            "model_name": "flux.2-dev",
            "ref_images": [cleaned_path, outfit_image, background_image],
            "outfit_image": outfit_image,
            "background_image": background_image,
            "status_note": "deferred_due_to_phase_b3",
        },
    )
    try:
        # Pass-through: copy cleaned character to final output path so the
        # architecture remains end-to-end runnable.
        shutil.copyfile(cleaned_path, final_path)
        # Record the final image as a session-level artifact (it's a fresh
        # copy of cleaned_path so it gets its own artifact_id).
        final_record = _artifact_id.make_artifact_record(
            final_path, kind="image/png", produced_by_stage="compose",
        )
        final_ar = session.upsert_artifact(
            rec_dict=final_record,
            originated_in_run=arch_run_id,
            arch_run_id=arch_run_id,
        )
        manifest.end_stage(
            rec,
            outputs={
                "output_path": str(final_path),
                "compose_status": "deferred_passthrough",
                "final_artifact_id": final_ar.artifact_id,
                "note": (
                    "Phase B.3 (Flux2 multi-ref + JSON prompts) not implemented; "
                    "stage 3 cleaned character copied to final output unchanged."
                ),
            },
        )
        run_record.session_manifest_stages.append({
            "stage": "compose",
            "source": "this_run",
            "consumed_artifact_ids": [cleaned_aid] if cleaned_aid else [],
            "produced_artifact_ids": [final_ar.artifact_id],
            "produced_path": str(final_path),
            "compose_status": "deferred_passthrough",
            "outfit_image": outfit_image,
            "background_image": background_image,
        })
        run_record.final_output_path = str(final_path)
        run_record.final_artifact_id = final_ar.artifact_id
        manifest.output_path = str(final_path)
        print(f"[arch] compose DEFERRED (Phase B.3 not landed) -- "
              f"copied cleaned character to {final_path}")
    except Exception as e:  # noqa: BLE001
        manifest.fail_stage(rec, str(e))
        print(f"[arch] compose FAILED -- {e}")

    manifest.pipeline_end = time.time()
    manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
    manifest.save(manifest_path)

    # --- Finalize the session run record + persist the session manifest ---
    final_succeeded_local = manifest.stages and manifest.stages[-1].name == "compose" \
        and manifest.stages[-1].status == "completed"
    run_record.status = "completed" if final_succeeded_local else "failed"
    run_record.finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    session.append_run(run_record)
    session_path = session.save(sessions_dir=sessions_dir)
    print(f"[arch] session saved -> {session_path}")

    # --- Cleanup ---
    final_succeeded = manifest.stages and manifest.stages[-1].name == "compose" \
        and manifest.stages[-1].status == "completed"
    if final_succeeded and not keep_intermediates:
        gc.collect()
        if store.cleanup():
            print(f"[arch] cleaned up {store}")
        else:
            print(f"[arch] cleanup partial -- intermediates remain at {store}")
    else:
        print(f"[arch] intermediates kept at {store}")

    print(f"[done] compose-character completed in {manifest.pipeline_duration_s}s")
    print(f"  Final image: {final_path}")
    print(f"  Manifest:    {manifest_path}")
    return manifest


# ============================================================================
# Per-stage standalone functions (Step 4 of the implementation plan)
#
# Each runs ONE stage of compose-character, with explicit inputs and writes
# its own session run record (entry_stage == exit_stage). Useful for branching
# off the full pipeline at any point.
# ============================================================================


def run_ideate_only(
    *,
    prompt: str,
    seed: int,
    num_candidates: int = 1,
    ideation_mode: str = DEFAULT_IDEATION_MODE,
    width: int = 1024,
    height: int = 1024,
    output_dir: Path | str = "src/assets/pics",
    intermediate_root: Path | str = "src/assets/pics/intermediate",
    sessions_dir: Path | str = SESSIONS_DIR_DEFAULT,
    session_id: str | None = None,
    continue_from_run: str | None = None,
    keep_intermediates: bool = True,         # default true: downstream stages need them
) -> MultiPipelineManifest:
    """Stage 1 only -- generate the candidate pool and stop. The user can
    then inspect the candidates and pass one to run_clean_only or
    run-from clean later."""
    if ideation_mode not in IDEATION_PRESETS:
        raise ValueError(f"unknown ideation_mode {ideation_mode!r}; "
                         f"must be one of {list(IDEATION_PRESETS)}")
    ideation_specs = IDEATION_PRESETS[ideation_mode]

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id_str = f"compose_character_ideate_{timestamp}_s{seed}"
    final_path = output_dir / f"multi_{run_id_str}.png"   # placeholder; not produced
    manifest_path = final_path.with_suffix(".json")

    session, run_record, was_created, arch_run_id = _setup_session(
        seed=seed, architecture="compose-character",
        entry_stage="ideate", exit_stage="ideate",
        sessions_dir=sessions_dir, session_id=session_id,
        continue_from_run=continue_from_run, manifest_path=manifest_path,
    )
    print(f"[arch] {'minted' if was_created else 'attached to'} session {session.session_id}")

    store = IntermediateStore(intermediate_root, run_id=run_id_str)
    seeds = [seed + i for i in range(max(1, num_candidates))]

    manifest = MultiPipelineManifest(
        architecture="compose-character",
        prompt=prompt, seed=seed,
        created_at=datetime.now(timezone.utc).isoformat(),
        intermediate_dir=str(store), keep_intermediates=keep_intermediates,
    )
    manifest.pipeline_start = time.time()

    rec = manifest.begin_stage("ideate", inputs={
        "prompt": prompt, "seeds": seeds, "num_candidates": num_candidates,
        "ideation_mode": ideation_mode,
        "pipelines": [n for n, _, _ in ideation_specs],
        "model_names": {n: kw["model_name"] for n, _, kw in ideation_specs},
        "width": width, "height": height, "stage_only": True,
    })
    candidates = generate_candidates(
        prompt=prompt, seeds=seeds, width=width, height=height,
        output_root=Path(str(store)) / "ideate",
        pipeline_specs=ideation_specs,
    )
    n_ok = sum(1 for c in candidates if c.status == "ok")
    print(f"[arch] ideate produced {n_ok} ok / {len(candidates) - n_ok} failed candidate(s)")

    candidate_aids: list[str] = []
    candidate_run_manifests: list[str] = []
    for c in candidates:
        if c.status == "ok" and c.manifest_path:
            aids = ingest_pipeline_manifest(
                session, arch_run_id=arch_run_id,
                pipeline_manifest_path=c.manifest_path,
            )
            candidate_aids.extend(aids)
            candidate_run_manifests.append(c.manifest_path)

    manifest.end_stage(rec, outputs={
        "candidate_count": len(candidates),
        "succeeded": n_ok, "failed": len(candidates) - n_ok,
        "candidates": [_candidate_summary(c) for c in candidates],
        "produced_artifact_ids": candidate_aids,
    })
    run_record.session_manifest_stages.append({
        "stage": "ideate",
        "source": "this_run",
        "ideation_mode": ideation_mode,
        "produced_run_manifest_paths": candidate_run_manifests,
        "produced_artifact_ids": candidate_aids,
    })

    manifest.pipeline_end = time.time()
    manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
    manifest.output_path = ""        # ideate-only: no single final image
    manifest.save(manifest_path)

    run_record.status = "completed" if n_ok > 0 else "failed"
    run_record.finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    session.append_run(run_record)
    sp = session.save(sessions_dir=sessions_dir)
    print(f"[arch] session saved -> {sp}")
    print(f"[done] ideate-only completed -- {n_ok} candidates, manifest: {manifest_path}")
    return manifest


def run_select_only(
    *,
    candidates_dir: Path | str | None = None,
    selected_panel: str | None = None,
    auto_select: str | None = None,           # "first" or None
    output_dir: Path | str = "src/assets/pics",
    sessions_dir: Path | str = SESSIONS_DIR_DEFAULT,
    session_id: str | None = None,
    continue_from_run: str | None = None,
    seed: int = 0,                            # used only for the run_id
) -> MultiPipelineManifest:
    """Stage 2 only -- pin a chosen panel into the session as a select-stage
    record. No generation happens. The chosen artifact is recorded so a
    downstream `clean` or `compose` run can consume it via --continue-from.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id_str = f"compose_character_select_{timestamp}_s{seed}"
    manifest_path = output_dir / f"multi_{run_id_str}.json"

    session, run_record, was_created, arch_run_id = _setup_session(
        seed=seed, architecture="compose-character",
        entry_stage="select", exit_stage="select",
        sessions_dir=sessions_dir, session_id=session_id,
        continue_from_run=continue_from_run, manifest_path=manifest_path,
    )
    print(f"[arch] {'minted' if was_created else 'attached to'} session {session.session_id}")

    manifest = MultiPipelineManifest(
        architecture="compose-character",
        prompt="", seed=seed,
        created_at=datetime.now(timezone.utc).isoformat(),
        intermediate_dir="", keep_intermediates=True,
    )
    manifest.pipeline_start = time.time()
    rec = manifest.begin_stage("select", inputs={
        "candidates_dir": str(candidates_dir) if candidates_dir else None,
        "selected_panel": selected_panel,
        "auto_select": auto_select,
        "stage_only": True,
    })

    try:
        # Resolve the chosen path
        if selected_panel:
            chosen_path = Path(selected_panel).resolve()
        elif auto_select == "first" and candidates_dir:
            cdir = Path(candidates_dir)
            cands = sorted(cdir.rglob("*.png"))
            if not cands:
                raise FileNotFoundError(f"no candidates found under {cdir}")
            chosen_path = cands[0].resolve()
        else:
            raise ValueError("select-only requires either --selected-panel or --auto-select=first with --candidates-dir")
        if not chosen_path.exists():
            raise FileNotFoundError(f"selected_panel does not exist: {chosen_path}")

        # Try to find the artifact_id for this path in the session.
        chosen_aid = None
        for aid, ar in session.artifacts.items():
            if Path(ar.path).resolve() == chosen_path:
                chosen_aid = aid
                break
        if chosen_aid is None:
            # External input -- record it as a fresh artifact in the session.
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            extern = _artifact_id.make_artifact_record(
                chosen_path, kind="image/png",
                produced_by_stage="external_input", created_at=ts,
            )
            ar = session.upsert_artifact(
                rec_dict=extern, originated_in_run="<external>",
                arch_run_id=arch_run_id,
            )
            chosen_aid = ar.artifact_id

        manifest.end_stage(rec, outputs={
            "chosen_path": str(chosen_path),
            "chosen_artifact_id": chosen_aid,
        })
        run_record.session_manifest_stages.append({
            "stage": "select",
            "source": "this_run" if continue_from_run else "external_input",
            "consumed_artifact_ids": [chosen_aid],
            "chosen_artifact_id": chosen_aid,
            "chosen_path": str(chosen_path),
        })
        run_record.final_artifact_id = chosen_aid
        run_record.final_output_path = str(chosen_path)
        manifest.output_path = str(chosen_path)
        print(f"[arch] select pinned: {chosen_path} -> {chosen_aid}")
    except Exception as e:  # noqa: BLE001
        manifest.fail_stage(rec, str(e))
        print(f"[arch] select FAILED -- {e}")

    manifest.pipeline_end = time.time()
    manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
    manifest.save(manifest_path)
    run_record.status = "completed" if manifest.stages[-1].status == "completed" else "failed"
    run_record.finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    session.append_run(run_record)
    session.save(sessions_dir=sessions_dir)
    return manifest


def run_clean_only(
    *,
    input_image: str,
    prompt: str,
    seed: int | None = None,
    strength: float = CLEAN_DEFAULTS["strength"],
    negative_prompt: str = CLEAN_DEFAULTS["negative_prompt"],
    cfg_normalization: bool = CLEAN_DEFAULTS["cfg_normalization"],
    width: int = 1024,
    height: int = 1024,
    output_dir: Path | str = "src/assets/pics",
    intermediate_root: Path | str = "src/assets/pics/intermediate",
    sessions_dir: Path | str = SESSIONS_DIR_DEFAULT,
    session_id: str | None = None,
    continue_from_run: str | None = None,
    keep_intermediates: bool = True,
) -> MultiPipelineManifest:
    """Stage 3 only -- Z-Image-Base img2img with the supplied input image.

    The input can come from any source -- a candidate from a prior ideate
    run, a hand-painted PNG, or a previous architecture's output. If
    --continue-from is set, the input image's artifact_id is looked up in
    the session manifest; if not, the image is recorded as `external_input`.
    """
    if seed is None:
        import random
        seed = random.randrange(2**31)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id_str = f"compose_character_clean_{timestamp}_s{seed}"
    final_path = output_dir / f"multi_{run_id_str}.png"
    manifest_path = final_path.with_suffix(".json")

    session, run_record, was_created, arch_run_id = _setup_session(
        seed=seed, architecture="compose-character",
        entry_stage="clean", exit_stage="clean",
        sessions_dir=sessions_dir, session_id=session_id,
        continue_from_run=continue_from_run, manifest_path=manifest_path,
    )
    print(f"[arch] {'minted' if was_created else 'attached to'} session {session.session_id}")

    store = IntermediateStore(intermediate_root, run_id=run_id_str)
    clean_dir = Path(str(store)) / "clean"

    manifest = MultiPipelineManifest(
        architecture="compose-character",
        prompt=prompt, seed=seed,
        created_at=datetime.now(timezone.utc).isoformat(),
        intermediate_dir=str(store), keep_intermediates=keep_intermediates,
    )
    manifest.pipeline_start = time.time()
    rec = manifest.begin_stage("clean", inputs={
        "model_name": CLEAN_DEFAULTS["model_name"],
        "input_image": input_image, "strength": strength,
        "negative_prompt": negative_prompt,
        "cfg_normalization": cfg_normalization,
        "prompt": prompt, "stage_only": True,
    })

    # Look up or record the input artifact
    input_path = Path(input_image).resolve()
    input_aid = None
    for aid, ar in session.artifacts.items():
        if Path(ar.path).resolve() == input_path:
            input_aid = aid
            break
    input_source = "this_run"
    if input_aid is None:
        # External input
        if input_path.exists():
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            extern = _artifact_id.make_artifact_record(
                input_path, kind="image/png",
                produced_by_stage="external_input", created_at=ts,
            )
            ar = session.upsert_artifact(
                rec_dict=extern, originated_in_run="<external>",
                arch_run_id=arch_run_id,
            )
            input_aid = ar.artifact_id
            input_source = "external_input"

    try:
        result = invoke_zimage(
            prompt=prompt, output_dir=clean_dir, seed=seed,
            width=width, height=height,
            model_name=CLEAN_DEFAULTS["model_name"],
            negative_prompt=negative_prompt,
            cfg_normalization=cfg_normalization,
            extra_args=[
                "--mode", "img2img",
                "--init-image", str(input_path),
                "--strength", str(strength),
            ],
        )
        if result["returncode"] != 0 or not result["output_path"]:
            err_tail = (result.get("stderr") or "")[-400:]
            raise RuntimeError(
                f"zimage img2img clean stage failed: rc={result['returncode']} "
                f"stderr tail: ...{err_tail}"
            )
        produced_aids = ingest_pipeline_manifest(
            session, arch_run_id=arch_run_id,
            pipeline_manifest_path=result["manifest_path"],
        )
        # Also copy the cleaned image to the architecture's final_path so
        # the manifest.output_path is meaningful even for stage-only runs.
        shutil.copyfile(result["output_path"], final_path)
        # Final-path version gets its own artifact_id (different created_at)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        final_record = _artifact_id.make_artifact_record(
            final_path, kind="image/png", produced_by_stage="clean",
            created_at=ts,
        )
        final_ar = session.upsert_artifact(
            rec_dict=final_record, originated_in_run=arch_run_id,
            arch_run_id=arch_run_id,
        )
        manifest.end_stage(rec, outputs={
            "output_path": str(final_path),
            "sub_manifest_path": result["manifest_path"],
            "produced_artifact_ids": produced_aids + [final_ar.artifact_id],
            "subprocess_duration_s": result["duration_s"],
        })
        run_record.session_manifest_stages.append({
            "stage": "clean",
            "source": input_source,
            "consumed_artifact_ids": [input_aid] if input_aid else [],
            "produced_run_manifest_paths": [result["manifest_path"]],
            "produced_artifact_ids": produced_aids + [final_ar.artifact_id],
            "produced_path": str(final_path),
        })
        run_record.final_output_path = str(final_path)
        run_record.final_artifact_id = final_ar.artifact_id
        manifest.output_path = str(final_path)
        print(f"[arch] clean OK -- {final_path}")
    except Exception as e:  # noqa: BLE001
        manifest.fail_stage(rec, str(e))
        print(f"[arch] clean FAILED -- {e}")

    manifest.pipeline_end = time.time()
    manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
    manifest.save(manifest_path)

    run_record.status = "completed" if manifest.stages[-1].status == "completed" else "failed"
    run_record.finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    session.append_run(run_record)
    session.save(sessions_dir=sessions_dir)
    return manifest
