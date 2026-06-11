"""Architecture: "batch" -- the v2 multi-image flow (see kb-multi-image2.md).

A run is a *batch*, not a hero-compose funnel:

    ideate (3 x N candidates)
      -> [clean-all]?   per-candidate img2img cleanup
      -> [polish-all]?  per-candidate img2img polish   (wired in P4)

`select` and `compose` are gone. Every successful ideate candidate flows
through clean (and later polish) independently; a failure on one candidate
never aborts the batch.

Phase status (kb-multi-image2.md §12):
  - P1 (this commit): `batch` orchestration + clean-all fanned over the
    whole pool using the existing Z-Image-Base img2img clean. `select` /
    `compose-character` removed from the CLI. Polish + configurable clean
    backend land in P3/P4 once the shared `_img2img` module exists (P2).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from .. import _artifact_id
from .arch_compose_character import (
    CLEAN_DEFAULTS, DEFAULT_IDEATION_MODE, IDEATION_PRESETS,
    _candidate_summary, _setup_session,
)
from .._img2img.autodetect import detect_source_pipeline
from .._img2img.backends import BACKEND_MODULE, run_img2img
from .candidates import generate_candidates, successful
from .intermediates import IntermediateStore
from .manifest import MultiPipelineManifest
from .sessions import SESSIONS_DIR_DEFAULT, ingest_pipeline_manifest

ARCHITECTURE = "batch"
DEFAULT_CLEAN_BACKEND = "zimage-img2img"   # preserves pre-P3 behaviour


def _augment_sidecar_module(manifest_path: str, backend: str) -> None:
    """Add a `module` key to the clean output's `<png>.json` sidecar so
    `handrefiner._detect_source_pipeline` (P4 polish auto-detect) and lineage
    can resolve the producing pipeline from the JSON rather than only the
    filename prefix. Best-effort + idempotent: never raises, never clobbers.
    """
    import json
    mod = BACKEND_MODULE.get(backend)
    if not mod or not manifest_path:
        return
    p = Path(manifest_path)
    try:
        if not p.exists():
            return
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("module") == mod:
            return
        data["module"] = mod
        p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except (OSError, ValueError) as e:  # ValueError covers json errors
        print(f"[batch] WARNING: could not augment sidecar {p}: {e}")


IMG2IMG_BATCHING = ("by-backend", "per-image")
DEFAULT_IMG2IMG_BATCHING = "by-backend"


def _execution_order(candidates, *, batching: str, backend_of):
    """Return `candidates` in the order their img2img subprocesses should run.

    * ``per-image``  -- submission (candidate) order; backends interleave.
    * ``by-backend`` -- stable-grouped so every same-backend subprocess runs
      consecutively.

    HONEST SCOPE NOTE (kb-multi-image2.md section 7, section 13-Q3): the
    per-pipeline CLIs (`zimage/sd35/flux2 run_pipeline.py`) accept a single
    ``--init-image`` and load the model once *per process* -- there is no
    multi-image mode. So ``by-backend`` does NOT make a backend's model load
    once for the whole group; it makes same-backend cold loads consecutive
    (warm OS page-cache for the multi-GB weights, a modest real saving) and,
    more importantly, is the single ordering seam where true batched
    execution will plug in IF per-pipeline multi-image support is ever added.
    Per-candidate records are always emitted in candidate order regardless of
    `batching`, so manifests/lineage stay deterministic.
    """
    if batching == "per-image":
        return list(candidates)
    if batching == "by-backend":
        return sorted(candidates, key=backend_of)  # Python sort is stable
    raise ValueError(f"unknown img2img_batching {batching!r}; "
                     f"expected one of {IMG2IMG_BATCHING}")


def _clean_one(
    *,
    candidate,
    backend: str,
    model_name: str | None,
    prompt: str,
    strength: float,
    negative_prompt: str,
    cfg_normalization: bool,
    width: int,
    height: int,
    clean_root: Path,
) -> dict:
    """img2img-clean one candidate via the shared `pipeline._img2img`
    dispatcher (same backend code postproc polish uses).

    Returns a per-candidate record (always; never raises) so the batch
    loop can keep going when a single image fails. On success, augments the
    output sidecar with a `module` key for P4 polish auto-detect.
    """
    base = {
        "source_pipeline": candidate.pipeline,
        "source_seed": candidate.seed,
        "source_candidate_index": candidate.candidate_index,
        "source_path": candidate.output_path,
        "backend": backend,
    }
    sub_dir = Path(clean_root) / candidate.pipeline / f"seed_{candidate.seed}"
    try:
        result = run_img2img(
            candidate.output_path,
            backend=backend,
            prompt=prompt,
            negative_prompt=negative_prompt,
            strength=strength,
            seed=candidate.seed,
            output_dir=sub_dir,
            model_name=model_name,
            cfg_normalization=cfg_normalization,
            width=width,
            height=height,
        )
        if result["returncode"] != 0 or not result["output_path"]:
            err_tail = (result.get("stderr") or "")[-400:]
            return {**base, "status": "failed", "output_path": "",
                    "sub_manifest_path": "",
                    "duration_s": result.get("subprocess_duration_s", 0.0),
                    "error": f"rc={result['returncode']}; stderr tail: ...{err_tail}"}
        _augment_sidecar_module(result["sub_manifest_path"], backend)
        return {**base, "status": "ok",
                "output_path": result["output_path"],
                "sub_manifest_path": result["sub_manifest_path"],
                "duration_s": result["subprocess_duration_s"], "error": ""}
    except Exception as e:  # noqa: BLE001 -- one bad image must not kill the batch
        return {**base, "status": "failed", "output_path": "",
                "sub_manifest_path": "", "duration_s": 0.0, "error": str(e)}


def _polish_one(
    *,
    candidate,
    input_image: str,
    polish_backend: str | None,
    polish_model: str | None,
    polish_prompt: str | None,
    polish_negative_prompt: str,
    polish_strength: float,
    polish_seed: int | None,
    batch_prompt: str,
    batch_seed: int,
    polish_root: Path,
) -> dict:
    """Polish one image via the shared dispatcher. Backend/seed/prompt are
    resolved from the input's sidecar exactly like postproc `cmd_polish`:

        backend = explicit  or sidecar  or 'sd35-img2img'
        seed    = explicit  or sidecar  or batch/candidate seed
        prompt  = explicit  or sidecar  or batch prompt
        model   = explicit only  (parity: postproc does not read model from sidecar)

    Never raises; returns a per-candidate record so the batch keeps going.
    """
    base = {
        "source_pipeline": candidate.pipeline,
        "source_seed": candidate.seed,
        "source_candidate_index": candidate.candidate_index,
        "input_image": input_image,
    }
    sub_dir = Path(polish_root) / candidate.pipeline / f"seed_{candidate.seed}"
    try:
        src = detect_source_pipeline(Path(input_image))
        backend = polish_backend or src["backend"] or "sd35-img2img"
        seed = (polish_seed if polish_seed is not None
                else (src["seed"] if src["seed"] is not None else batch_seed))
        prompt = polish_prompt or src["prompt"] or batch_prompt
        negative = polish_negative_prompt or ""
        resolved = {"backend": backend, "seed": seed,
                    "source_detected": src["backend"]}
        result = run_img2img(
            input_image, backend=backend, prompt=prompt,
            negative_prompt=negative, strength=polish_strength,
            seed=seed, output_dir=sub_dir, model_name=polish_model,
        )
        if result["returncode"] != 0 or not result["output_path"]:
            err_tail = (result.get("stderr") or "")[-400:]
            return {**base, **resolved, "status": "failed", "output_path": "",
                    "sub_manifest_path": "",
                    "duration_s": result.get("subprocess_duration_s", 0.0),
                    "error": f"rc={result['returncode']}; stderr tail: ...{err_tail}"}
        _augment_sidecar_module(result["sub_manifest_path"], backend)
        return {**base, **resolved, "status": "ok",
                "output_path": result["output_path"],
                "sub_manifest_path": result["sub_manifest_path"],
                "duration_s": result["subprocess_duration_s"], "error": ""}
    except Exception as e:  # noqa: BLE001 -- one bad image must not kill the batch
        return {**base, "status": "failed", "output_path": "",
                "sub_manifest_path": "", "duration_s": 0.0, "error": str(e)}


def run_batch(
    *,
    prompt: str,
    seed: int,
    num_candidates: int = 1,
    ideation_mode: str = DEFAULT_IDEATION_MODE,
    width: int = 1024,
    height: int = 1024,
    do_clean: bool = False,
    clean_backend: str = DEFAULT_CLEAN_BACKEND,
    clean_model: str | None = None,
    clean_strength: float = CLEAN_DEFAULTS["strength"],
    clean_prompt: str | None = None,
    clean_negative_prompt: str = CLEAN_DEFAULTS["negative_prompt"],
    clean_cfg_normalization: bool = CLEAN_DEFAULTS["cfg_normalization"],
    do_polish: bool = False,
    polish_backend: str | None = None,      # None -> auto-detect per candidate
    polish_model: str | None = None,
    polish_prompt: str | None = None,
    polish_negative_prompt: str = "",
    polish_strength: float = 0.22,
    polish_seed: int | None = None,
    img2img_batching: str = DEFAULT_IMG2IMG_BATCHING,
    output_dir: Path | str = "src/assets/pics",
    intermediate_root: Path | str = "src/assets/pics/intermediate",
    sessions_dir: Path | str = SESSIONS_DIR_DEFAULT,
    session_id: str | None = None,
    continue_from_run: str | None = None,
    keep_intermediates: bool = True,
) -> MultiPipelineManifest:
    """ideate -> [clean-all] -> [polish-all].

    With no toggles this is functionally identical to `ideate`. The batch
    (the whole candidate pool, plus per-candidate clean/polish outputs) is
    the deliverable -- there is no "chosen" image. Polish input per candidate
    is the clean output when clean ran (and succeeded for it), otherwise the
    raw ideate candidate; backend/seed/prompt auto-detect mirrors postproc.
    """
    if ideation_mode not in IDEATION_PRESETS:
        raise ValueError(f"unknown ideation_mode {ideation_mode!r}; "
                         f"must be one of {list(IDEATION_PRESETS)}")
    if img2img_batching not in IMG2IMG_BATCHING:
        raise ValueError(f"unknown img2img_batching {img2img_batching!r}; "
                         f"expected one of {IMG2IMG_BATCHING}")
    ideation_specs = IDEATION_PRESETS[ideation_mode]
    clean_prompt = clean_prompt if clean_prompt is not None else prompt

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id_str = f"{ARCHITECTURE}_{timestamp}_s{seed}"
    manifest_path = output_dir / f"multi_{run_id_str}.json"

    session, run_record, was_created, arch_run_id = _setup_session(
        seed=seed, architecture=ARCHITECTURE,
        entry_stage="ideate",
        exit_stage=("polish" if do_polish else "clean" if do_clean else "ideate"),
        sessions_dir=sessions_dir, session_id=session_id,
        continue_from_run=continue_from_run, manifest_path=manifest_path,
    )
    print(f"[batch] {'minted' if was_created else 'attached to'} session {session.session_id}")

    store = IntermediateStore(intermediate_root, run_id=run_id_str)
    seeds = [seed + i for i in range(max(1, num_candidates))]

    manifest = MultiPipelineManifest(
        architecture=ARCHITECTURE, prompt=prompt, seed=seed,
        created_at=datetime.now(timezone.utc).isoformat(),
        intermediate_dir=str(store), keep_intermediates=keep_intermediates,
    )
    manifest.pipeline_start = time.time()

    # ---- Stage: ideate ----------------------------------------------------
    rec = manifest.begin_stage("ideate", inputs={
        "prompt": prompt, "seeds": seeds, "num_candidates": num_candidates,
        "ideation_mode": ideation_mode,
        "pipelines": [n for n, _, _ in ideation_specs],
        "model_names": {n: kw["model_name"] for n, _, kw in ideation_specs},
        "width": width, "height": height,
    })
    candidates = generate_candidates(
        prompt=prompt, seeds=seeds, width=width, height=height,
        output_root=Path(str(store)) / "ideate",
        pipeline_specs=ideation_specs,
    )
    n_ok = sum(1 for c in candidates if c.status == "ok")
    n_fail = len(candidates) - n_ok
    print(f"[batch] ideate produced {n_ok} ok / {n_fail} failed candidate(s)")

    candidate_aids: list[str] = []
    candidate_run_manifests: list[str] = []
    for c in candidates:
        if c.status == "ok" and c.manifest_path:
            try:
                aids = ingest_pipeline_manifest(
                    session, arch_run_id=arch_run_id,
                    pipeline_manifest_path=c.manifest_path,
                )
                candidate_aids.extend(aids)
                candidate_run_manifests.append(c.manifest_path)
            except Exception as e:  # noqa: BLE001 -- lineage ingest is best-effort
                print(f"[batch] WARNING: ideate manifest ingest failed for "
                      f"{c.pipeline} s{c.seed}: {e}")

    manifest.end_stage(rec, outputs={
        "candidate_count": len(candidates),
        "succeeded": n_ok, "failed": n_fail,
        "candidates": [_candidate_summary(c) for c in candidates],
        "produced_artifact_ids": candidate_aids,
    })
    run_record.session_manifest_stages.append({
        "stage": "ideate", "source": "this_run",
        "ideation_mode": ideation_mode,
        "produced_run_manifest_paths": candidate_run_manifests,
        "produced_artifact_ids": candidate_aids,
    })

    cleaned: list[dict] = []   # filled if clean runs; consumed by polish-all

    # ---- Stage: clean-all (optional) -------------------------------------
    if do_clean:
        ok_candidates = successful(candidates)
        rec = manifest.begin_stage("clean", inputs={
            "backend": clean_backend,
            "model_name": clean_model,   # None -> backend default in run_img2img
            "strength": clean_strength,
            "negative_prompt": clean_negative_prompt,
            "cfg_normalization": clean_cfg_normalization,
            "prompt": clean_prompt,
            "batching": img2img_batching,
            "candidate_count": len(ok_candidates),
        })
        clean_root = Path(str(store)) / "clean"
        clean_aids: list[str] = []
        # Execute in batching order; record in candidate order (deterministic).
        _co = _execution_order(ok_candidates, batching=img2img_batching,
                               backend_of=lambda c: clean_backend)
        _cres: dict[int, dict] = {}
        for c in _co:
            _cres[c.candidate_index] = _clean_one(
                candidate=c, backend=clean_backend, model_name=clean_model,
                prompt=clean_prompt, strength=clean_strength,
                negative_prompt=clean_negative_prompt,
                cfg_normalization=clean_cfg_normalization,
                width=width, height=height, clean_root=clean_root,
            )
        for c in ok_candidates:
            r = _cres[c.candidate_index]
            if r["status"] == "ok" and r["sub_manifest_path"]:
                try:
                    aids = ingest_pipeline_manifest(
                        session, arch_run_id=arch_run_id,
                        pipeline_manifest_path=r["sub_manifest_path"],
                    )
                    r["produced_artifact_ids"] = aids
                    clean_aids.extend(aids)
                except Exception as e:  # noqa: BLE001
                    print(f"[batch] WARNING: clean manifest ingest failed: {e}")
            tag = "OK " if r["status"] == "ok" else "FAIL"
            print(f"[batch] clean {tag} {c.pipeline} s{c.seed} -> "
                  f"{r['output_path'] or r['error'][:120]}")
            cleaned.append(r)

        c_ok = sum(1 for r in cleaned if r["status"] == "ok")
        manifest.end_stage(rec, outputs={
            "backend": clean_backend,
            "succeeded": c_ok, "failed": len(cleaned) - c_ok,
            "cleaned": cleaned,
            "produced_artifact_ids": clean_aids,
        })
        run_record.session_manifest_stages.append({
            "stage": "clean", "source": "this_run",
            "produced_artifact_ids": clean_aids,
        })
        print(f"[batch] clean-all: {c_ok} ok / {len(cleaned) - c_ok} failed")

    # ---- Stage: polish-all (optional) ------------------------------------
    if do_polish:
        ok_candidates = successful(candidates)
        clean_ok_by_idx = {r["source_candidate_index"]: r["output_path"]
                           for r in cleaned if r.get("status") == "ok"}
        rec = manifest.begin_stage("polish", inputs={
            "backend": polish_backend,   # None -> auto-detect per candidate
            "model_name": polish_model,
            "strength": polish_strength,
            "negative_prompt": polish_negative_prompt,
            "prompt": polish_prompt,     # None -> sidecar/batch prompt
            "seed": polish_seed,
            "input_source": "clean" if do_clean else "ideate",
            "batching": img2img_batching,
            "candidate_count": len(ok_candidates),
        })
        polish_root = Path(str(store)) / "polish"
        polished: list[dict] = []
        polish_aids: list[str] = []

        def _pin(c):  # polish input: clean output if clean ran ok, else ideate
            return clean_ok_by_idx.get(c.candidate_index) or c.output_path

        def _pbk(c):  # resolved polish backend (sort key, mirrors _polish_one)
            return (polish_backend
                    or detect_source_pipeline(Path(_pin(c)))["backend"]
                    or "sd35-img2img")

        _po = _execution_order(ok_candidates, batching=img2img_batching,
                               backend_of=_pbk)
        _pres: dict[int, dict] = {}
        for c in _po:
            _pres[c.candidate_index] = _polish_one(
                candidate=c, input_image=_pin(c),
                polish_backend=polish_backend, polish_model=polish_model,
                polish_prompt=polish_prompt,
                polish_negative_prompt=polish_negative_prompt,
                polish_strength=polish_strength, polish_seed=polish_seed,
                batch_prompt=prompt, batch_seed=c.seed,
                polish_root=polish_root,
            )
        for c in ok_candidates:
            r = _pres[c.candidate_index]
            if r["status"] == "ok" and r["sub_manifest_path"]:
                try:
                    aids = ingest_pipeline_manifest(
                        session, arch_run_id=arch_run_id,
                        pipeline_manifest_path=r["sub_manifest_path"],
                    )
                    r["produced_artifact_ids"] = aids
                    polish_aids.extend(aids)
                except Exception as e:  # noqa: BLE001
                    print(f"[batch] WARNING: polish manifest ingest failed: {e}")
            tag = "OK " if r["status"] == "ok" else "FAIL"
            print(f"[batch] polish {tag} {c.pipeline} s{c.seed} "
                  f"[{r.get('backend', '?')}] -> "
                  f"{r['output_path'] or r['error'][:120]}")
            polished.append(r)

        p_ok = sum(1 for r in polished if r["status"] == "ok")
        manifest.end_stage(rec, outputs={
            "succeeded": p_ok, "failed": len(polished) - p_ok,
            "polished": polished,
            "produced_artifact_ids": polish_aids,
        })
        run_record.session_manifest_stages.append({
            "stage": "polish", "source": "this_run",
            "produced_artifact_ids": polish_aids,
        })
        print(f"[batch] polish-all: {p_ok} ok / {len(polished) - p_ok} failed")

    manifest.pipeline_end = time.time()
    manifest.pipeline_duration_s = round(
        manifest.pipeline_end - manifest.pipeline_start, 4)
    manifest.output_path = ""    # a batch has no single final image
    manifest.save(manifest_path)

    # Run is "completed" if ideate produced >=1 candidate (clean failures on
    # individual images are non-fatal -- the batch is still a deliverable).
    run_record.status = "completed" if n_ok > 0 else "failed"
    run_record.finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    session.append_run(run_record)
    sp = session.save(sessions_dir=sessions_dir)
    print(f"[batch] session saved -> {sp}")
    print(f"[done] batch completed -- {n_ok} candidates"
          f"{', clean-all' if do_clean else ''}"
          f"{', polish-all' if do_polish else ''}; manifest: {manifest_path}")
    return manifest
