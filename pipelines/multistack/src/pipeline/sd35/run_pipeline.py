"""SD 3.5 pipeline orchestrator — runs all stages and writes a JSON manifest."""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import torch

# Make src/pipeline/ importable for the shared _artifact_id helper. The sd35
# scripts use bare imports (import stage1_load_pipeline, etc.) so the package
# dir is already on sys.path; we add its parent for src/pipeline/_artifact_id.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _artifact_id  # noqa: E402

import stage1_load_pipeline, stage2_generate, stage3_save  # noqa: E402
from manifest import PipelineManifest  # noqa: E402
from stage1_load_pipeline import SD35_MODEL_INFO  # noqa: E402


def run(
    prompt: str,
    model_name: str = "sd3.5-large-turbo",
    width: int = 1024,
    height: int = 1024,
    seed: int | None = None,
    num_steps: int | None = None,
    guidance_scale: float | None = None,
    negative_prompt: str | None = None,
    prompt_3: str | None = None,
    negative_prompt_3: str | None = None,
    max_sequence_length: int = 512,
    output_dir: str = "src/assets/pics",
    device: str = "cuda",
    cpu_offload: bool = False,
    drop_t5: bool = False,
    dtype: str = "bfloat16",
    skip_layer_guidance: bool = True,
    skip_layer_guidance_scale: float = 2.8,
    skip_layer_guidance_start: float = 0.01,
    skip_layer_guidance_stop:  float = 0.2,
    # --- inpaint / cn-inpaint ---
    mode: str = "t2i",
    init_image: str | None = None,
    mask_image: str | None = None,
    control_image: str | None = None,
    control_images: list[str] | None = None,
    controlnet: str | None = None,
    controlnets: list[str] | None = None,
    controlnet_conditioning_scale: float | list[float] = 1.0,
    strength: float = 1.0,
) -> PipelineManifest:
    """Run the full SD 3.5 image generation pipeline.

    Returns the completed PipelineManifest.
    """
    # Set expandable segments early to reduce VRAM fragmentation
    if "PYTORCH_ALLOC_CONF" not in os.environ:
        os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"

    if seed is None:
        seed = random.randrange(2**31)

    model_info = SD35_MODEL_INFO[model_name]
    defaults = model_info["defaults"]
    if num_steps is None:
        num_steps = defaults["num_steps"]
    if guidance_scale is None:
        guidance_scale = defaults["guidance_scale"]

    # Anchor relative output dirs to the repo root, not the current cwd. When
    # this script is invoked from inside its package directory (which the
    # multi-pipeline stage_runner does for VRAM isolation reasons), a relative
    # path like "src/assets/pics" would otherwise resolve to
    # `<repo>/src/pipeline/sd35/src/assets/pics`.
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        repo_root = Path(__file__).resolve().parents[3]
        out_dir = repo_root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = out_dir / f"sd35_{timestamp}_s{seed}.png"
    manifest_path = output_path.with_suffix(".json")

    manifest = PipelineManifest(
        model_name=model_name,
        prompt=prompt,
        seed=seed,
        width=width,
        height=height,
        created_at=datetime.now(timezone.utc).isoformat(),
        device=device,
        run_id=_artifact_id.mint_run_id(seed),
    )
    manifest.pipeline_start = time.time()

    # Skip Layer Guidance: per Stability AI defaults, ON for medium/large
    # (anatomy + composition fix), OFF for turbo (CFG disabled). User can
    # disable explicitly with --no-skip-layer-guidance.
    skip_guidance_layers = model_info.get("skip_guidance_layers") if skip_layer_guidance else None

    # --- Mode validation -- catch bad arg combos before model load ---
    if mode not in ("t2i", "img2img", "inpaint", "cn-inpaint", "cn-inpaint-mc"):
        raise ValueError(
            f"--mode must be one of t2i, img2img, inpaint, cn-inpaint, cn-inpaint-mc (got {mode!r})"
        )
    if mode == "img2img":
        if init_image is None:
            raise ValueError("mode=img2img requires --init-image")
    if mode == "inpaint":
        if init_image is None:
            raise ValueError("mode=inpaint requires --init-image")
        if mask_image is None:
            raise ValueError("mode=inpaint requires --mask-image")
    if mode == "cn-inpaint":
        # cn-inpaint is t2i+CN; the mask-composite step is the caller's
        # responsibility (HandRefiner stage 6). --init-image / --mask-image
        # are accepted but unused by this pipeline -- recorded in the manifest
        # so downstream consumers can still find them.
        if not controlnet:
            raise ValueError("mode=cn-inpaint requires --controlnet (e.g. 'depth')")
        if control_image is None:
            raise ValueError("mode=cn-inpaint requires --control-image")
    if mode == "cn-inpaint-mc":
        # Multi-CN inpaint -- mask-driven repaint with extra structural CNs
        # (e.g. depth). Inpaint CN must be first; its control_image is the
        # original scene image (alimama prep VAE-encodes it with mask region
        # zeroed). Other CN control_images go in subsequent positions.
        if init_image is None:
            raise ValueError("mode=cn-inpaint-mc requires --init-image")
        if mask_image is None:
            raise ValueError("mode=cn-inpaint-mc requires --mask-image")
        if not controlnets or len(controlnets) < 2:
            raise ValueError(
                "mode=cn-inpaint-mc requires --controlnets with >=2 entries "
                "(first must be an inpaint CN like 'inpaint')"
            )
        if not control_images or len(control_images) != len(controlnets):
            raise ValueError(
                "mode=cn-inpaint-mc requires --control-images with the same length as "
                f"--controlnets (got {len(control_images) if control_images else 0} vs "
                f"{len(controlnets)})"
            )
    if mode == "t2i" and (init_image or mask_image or control_image or control_images):
        print("[warn] init_image/mask_image/control_image(s) ignored in t2i mode")
        init_image = mask_image = control_image = None
        control_images = None

    # --- Stage 1: Load pipeline ---
    rec = manifest.begin_stage(
        "load_pipeline",
        stage1_load_pipeline.get_manifest_inputs(
            model_name, device, cpu_offload, drop_t5, mode, controlnet, controlnets,
        ),
    )
    try:
        s1 = stage1_load_pipeline.run(
            model_name=model_name,
            device=device,
            cpu_offload=cpu_offload,
            drop_t5=drop_t5,
            dtype=dtype,
            mode=mode,
            controlnet=controlnet,
            controlnets=controlnets,
        )
        manifest.end_stage(
            rec,
            stage1_load_pipeline.get_manifest_outputs(s1),
            stage1_load_pipeline.get_manifest_debug(s1),
        )
        print(f"[stage1] Pipeline loaded in {rec.duration_s}s" + (" (cpu_offload)" if cpu_offload else ""))
    except Exception as e:
        manifest.fail_stage(rec, str(e))
        manifest.pipeline_end = time.time()
        manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
        manifest.save(manifest_path)
        raise

    # --- Stage 2: Generate image ---
    rec = manifest.begin_stage(
        "generate",
        stage2_generate.get_manifest_inputs(
            prompt, width, height, seed, num_steps, guidance_scale, negative_prompt,
            max_sequence_length, prompt_3, negative_prompt_3, skip_guidance_layers,
            skip_layer_guidance_scale, skip_layer_guidance_start, skip_layer_guidance_stop,
            mode, init_image, mask_image, control_image, control_images,
            strength if mode != "t2i" else None,
            controlnet_conditioning_scale if mode in ("cn-inpaint", "cn-inpaint-mc") else None,
        ),
    )
    try:
        s2 = stage2_generate.run(
            pipe=s1["pipe"],
            prompt=prompt,
            width=width,
            height=height,
            seed=seed,
            num_inference_steps=num_steps,
            guidance_scale=guidance_scale,
            negative_prompt=negative_prompt,
            prompt_3=prompt_3,
            negative_prompt_3=negative_prompt_3,
            max_sequence_length=max_sequence_length,
            skip_guidance_layers=skip_guidance_layers,
            skip_layer_guidance_scale=skip_layer_guidance_scale,
            skip_layer_guidance_start=skip_layer_guidance_start,
            skip_layer_guidance_stop=skip_layer_guidance_stop,
            mode=mode,
            init_image=init_image,
            mask_image=mask_image,
            control_image=control_image,
            control_images=control_images,
            strength=strength,
            controlnet_conditioning_scale=controlnet_conditioning_scale,
        )
        manifest.end_stage(
            rec,
            stage2_generate.get_manifest_outputs(s2),
            stage2_generate.get_manifest_debug(s2),
        )
        print(f"[stage2] Generated in {rec.duration_s}s -- {s2['width']}x{s2['height']}")
    except Exception as e:
        manifest.fail_stage(rec, str(e))
        manifest.pipeline_end = time.time()
        manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
        manifest.save(manifest_path)
        raise

    # --- Stage 3: Save image ---
    rec = manifest.begin_stage("save", stage3_save.get_manifest_inputs(str(output_path)))
    try:
        s3 = stage3_save.run(image=s2["image"], output_path=output_path)
        manifest.end_stage(rec, stage3_save.get_manifest_outputs(s3), stage3_save.get_manifest_debug(s3))
        print(f"[stage3] Saved in {rec.duration_s}s -- {s3['output_path']}")
        # Record the saved PNG as an artifact for the multi-image session manifest.
        manifest.artifacts.append(_artifact_id.make_artifact_record(
            output_path, kind="image/png", produced_by_stage="save",
        ))
    except Exception as e:
        manifest.fail_stage(rec, str(e))
        manifest.pipeline_end = time.time()
        manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
        manifest.save(manifest_path)
        raise

    # --- Finalize ---
    manifest.pipeline_end = time.time()
    manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
    manifest.output_path = str(output_path)
    manifest.save(manifest_path)
    print(f"[done] Pipeline completed in {manifest.pipeline_duration_s}s")
    print(f"  Image: {output_path}")
    print(f"  Manifest: {manifest_path}")

    return manifest


# --- Batch mode (--jobs-file): load the pipeline ONCE, generate N images -----------
#
# Same contract as zimage's batch mode (see src/pipeline/zimage/run_pipeline.py):
# `{"shared": {...run() kwargs...}, "items": [{"prompt", "seed", "meta", ...overrides}]}`.
# Load-bound keys are shared-only; `meta` is echoed into the batch summary manifest.
# Restricted to the plain modes (t2i/img2img/inpaint) -- the ControlNet modes need
# per-item conditioning images and stay single-run.

_BATCH_SHARED_ONLY = ("mode", "model_name", "dtype", "cpu_offload", "drop_t5", "device")
_BATCH_MODES = ("t2i", "img2img", "inpaint")


def _generate_item(s1: dict, *, model_name: str, defaults: dict, mode: str, device: str,
                   skip_guidance_layers, merged: dict, out_dir: Path, idx: int = 0) -> dict:
    """Generate + save ONE image from `merged` params into `out_dir`, returning the per-item
    result record (status/output_path/manifest_path/seed/duration_s/error/meta). SHARED by the
    `--jobs-file` batch loop and the `--serve` warm loop (M2.7 Phase 2a) so the two can never
    drift on the (subtle) img2img/inpaint generation path. Per-item failure is captured in the
    record (status='failed' + error), never raised — the caller decides how to report it."""
    seed = merged.get("seed")
    if seed is None:
        seed = random.randrange(2**31)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = out_dir / f"sd35_{ts}_i{idx:03d}_s{seed}.png"
    manifest_path = output_path.with_suffix(".json")
    it0 = time.time()
    rec_out = {"index": idx, "status": "failed", "seed": seed, "prompt": merged["prompt"],
               "output_path": "", "manifest_path": "", "duration_s": 0.0,
               "error": None, "meta": merged.get("meta")}
    try:
        init_image = merged.get("init_image")
        mask_image = merged.get("mask_image")
        if mode in ("img2img", "inpaint") and init_image is None:
            raise ValueError(f"mode={mode} requires init_image")
        if mode == "inpaint" and mask_image is None:
            raise ValueError("mode=inpaint requires mask_image")
        if mode == "img2img":
            mask_image = None
        num_steps = merged.get("num_steps")
        if num_steps is None:
            num_steps = defaults["num_steps"]
        guidance_scale = merged.get("guidance_scale")
        if guidance_scale is None:
            guidance_scale = defaults["guidance_scale"]
        width = merged.get("width", 1024)
        height = merged.get("height", 1024)
        strength = merged.get("strength", 1.0)
        negative_prompt = merged.get("negative_prompt")
        prompt_3 = merged.get("prompt_3")
        negative_prompt_3 = merged.get("negative_prompt_3")
        max_sequence_length = merged.get("max_sequence_length", 512)
        slg_scale = merged.get("skip_layer_guidance_scale", 2.8)
        slg_start = merged.get("skip_layer_guidance_start", 0.01)
        slg_stop = merged.get("skip_layer_guidance_stop", 0.2)

        manifest = PipelineManifest(
            model_name=model_name, prompt=merged["prompt"], seed=seed,
            width=width, height=height,
            created_at=datetime.now(timezone.utc).isoformat(),
            device=device, run_id=_artifact_id.mint_run_id(seed),
        )
        manifest.pipeline_start = time.time()
        rec = manifest.begin_stage("generate", stage2_generate.get_manifest_inputs(
            merged["prompt"], width, height, seed, num_steps, guidance_scale,
            negative_prompt, max_sequence_length, prompt_3, negative_prompt_3,
            skip_guidance_layers, slg_scale, slg_start, slg_stop,
            mode, init_image, mask_image, None, None,
            strength if mode != "t2i" else None,
            None,
        ))
        s2 = stage2_generate.run(
            pipe=s1["pipe"], prompt=merged["prompt"], width=width, height=height,
            seed=seed, num_inference_steps=num_steps, guidance_scale=guidance_scale,
            negative_prompt=negative_prompt, prompt_3=prompt_3,
            negative_prompt_3=negative_prompt_3,
            max_sequence_length=max_sequence_length,
            skip_guidance_layers=skip_guidance_layers,
            skip_layer_guidance_scale=slg_scale,
            skip_layer_guidance_start=slg_start,
            skip_layer_guidance_stop=slg_stop,
            mode=mode, init_image=init_image, mask_image=mask_image,
            control_image=None, control_images=None,
            strength=strength, controlnet_conditioning_scale=1.0,
        )
        manifest.end_stage(rec, stage2_generate.get_manifest_outputs(s2),
                           stage2_generate.get_manifest_debug(s2))
        rec = manifest.begin_stage("save", stage3_save.get_manifest_inputs(str(output_path)))
        s3 = stage3_save.run(image=s2["image"], output_path=output_path)
        manifest.end_stage(rec, stage3_save.get_manifest_outputs(s3),
                           stage3_save.get_manifest_debug(s3))
        manifest.artifacts.append(_artifact_id.make_artifact_record(
            output_path, kind="image/png", produced_by_stage="save"))
        manifest.pipeline_end = time.time()
        manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
        manifest.output_path = str(output_path)
        manifest.save(manifest_path)
        dt = round(time.time() - it0, 2)
        rec_out.update(status="ok", output_path=str(output_path),
                       manifest_path=str(manifest_path), duration_s=dt)
        print(f"  Image: {output_path}")
    except Exception as e:  # captured, not raised — one bad item must not kill the batch/sweep
        rec_out.update(error=str(e), duration_s=round(time.time() - it0, 2))
    return rec_out


def run_jobs(jobs_file: str, output_dir: str = "src/assets/pics", device: str = "cuda") -> int:
    """Execute a jobs file: one shared pipeline load, then generate+save per item.

    Returns the process exit code: 0 if at least one item succeeded, else 2.
    """
    if "PYTORCH_ALLOC_CONF" not in os.environ:
        os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"

    spec = json.loads(Path(jobs_file).read_text(encoding="utf-8"))
    shared = dict(spec.get("shared") or {})
    items = list(spec.get("items") or [])
    if not items:
        print("[batch-error] jobs file has no items")
        return 2
    for i, item in enumerate(items):
        if not (item.get("prompt") or "").strip():
            print(f"[batch-error] item {i} has no prompt")
            return 2
        bad = sorted(k for k in _BATCH_SHARED_ONLY if k in item)
        if bad:
            print(f"[batch-error] item {i} overrides load-bound key(s) {bad} -- shared-only")
            return 2

    mode = shared.get("mode", "t2i")
    if mode not in _BATCH_MODES:
        print(f"[batch-error] shared.mode must be one of {_BATCH_MODES} (got {mode!r})")
        return 2
    model_name = shared.get("model_name", "sd3.5-medium")
    if model_name not in SD35_MODEL_INFO:
        print(f"[batch-error] unknown model_name {model_name!r}")
        return 2
    model_info = SD35_MODEL_INFO[model_name]
    defaults = model_info["defaults"]
    device = shared.get("device", device)
    skip_layer_guidance = shared.get("skip_layer_guidance", True)
    skip_guidance_layers = model_info.get("skip_guidance_layers") if skip_layer_guidance else None

    out_dir = Path(shared.get("output_dir") or output_dir)
    if not out_dir.is_absolute():
        out_dir = Path(__file__).resolve().parents[3] / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    batch_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    batch_manifest_path = out_dir / f"sd35_batch_{batch_ts}.json"

    t0 = time.time()
    print(f"[batch] {len(items)} item(s) | mode={mode} model={model_name}")
    results: list[dict] = []

    def _finish(status: str, load_s: float, error: str | None = None) -> int:
        n_ok = sum(1 for r in results if r["status"] == "ok")
        n_fail = sum(1 for r in results if r["status"] == "failed")
        n_skip = sum(1 for r in results if r["status"] == "skipped")
        if n_ok == 0 and status == "completed":
            status = "failed"
        summary = {
            "kind": "jobs_batch", "schema_version": 1, "pipeline": "sd35",
            "model_name": model_name, "mode": mode, "status": status, "error": error,
            "count": len(items), "ok": n_ok, "failed": n_fail, "skipped": n_skip,
            "load_duration_s": load_s,
            "total_duration_s": round(time.time() - t0, 4),
            "items": results,
        }
        batch_manifest_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
        print(f"[batch-done] {n_ok} ok / {n_fail} failed / {n_skip} skipped "
              f"in {summary['total_duration_s']}s ({status})")
        print(f"  BatchManifest: {batch_manifest_path}")
        return 0 if n_ok > 0 else 2

    def _skip_rest(start: int, reason: str) -> None:
        for j in range(start, len(items)):
            results.append({"index": j, "status": "skipped", "seed": items[j].get("seed"),
                            "prompt": items[j]["prompt"], "output_path": "",
                            "manifest_path": "", "duration_s": 0.0, "error": reason,
                            "meta": items[j].get("meta")})

    # --- Shared pipeline load (the whole point of batch mode) ---
    try:
        s1 = stage1_load_pipeline.run(
            model_name=model_name,
            device=device,
            cpu_offload=shared.get("cpu_offload", True),
            drop_t5=shared.get("drop_t5", False),
            dtype=shared.get("dtype", "bfloat16"),
            mode=mode,
            controlnet=None,
            controlnets=None,
        )
    except Exception as e:
        _skip_rest(0, "pipeline load failed")
        return _finish("failed", round(time.time() - t0, 2), error=str(e))
    load_s = round(time.time() - t0, 2)
    print(f"[stage1] Pipeline loaded in {load_s}s (shared across {len(items)} items)")

    status = "completed"
    stop_file = out_dir / "STOP"
    for idx, item in enumerate(items):
        if stop_file.exists():
            print(f"[batch] STOP file found -- stopping before item {idx + 1}/{len(items)}")
            _skip_rest(idx, "stopped")
            status = "stopped"
            break
        merged = {**shared, **item}
        if merged.get("seed") is None:
            merged["seed"] = random.randrange(2**31)
        print(f"[item {idx + 1}/{len(items)}] seed={merged['seed']}")
        rec_out = _generate_item(
            s1, model_name=model_name, defaults=defaults, mode=mode, device=device,
            skip_guidance_layers=skip_guidance_layers, merged=merged, out_dir=out_dir, idx=idx,
        )
        if rec_out["status"] == "ok":
            print(f"[item {idx + 1}/{len(items)}] done in {rec_out['duration_s']}s")
        else:
            print(f"[item {idx + 1}/{len(items)}] FAILED: {rec_out['error']}")
        results.append(rec_out)

    return _finish(status, load_s)


# --- Serve mode (--serve): a PERSISTENT warm worker (M2.7 Phase 2a) -------------------
#
# Mirrors the flux2 worker's `--serve`: the runner feeds same-`warm_group` cell-jobs (one image
# each) to ONE long-lived process so the pipeline loads ONCE for the whole Expansion sweep, while
# each image is its own queue entry that persists the moment it's done (pause keeps the finished
# tiles). Protocol — stdin: one JSON job per line (`{job_id, model_name, mode, prompt, seed, width,
# height, init_image, mask_image, strength, num_steps, guidance_scale, negative_prompt, output_dir,
# meta, ...}`); a `{"cmd":"shutdown"}` line (or EOF) exits. stdout: free-text stage prints (streamed
# to the job log) + ONE result line per job, framed `SERVE_RESULT_PREFIX + <json>`.
SERVE_RESULT_PREFIX = "[serve-result] "


class _ServeGenerator:
    """Loads the SD 3.5 pipeline from the FIRST job and keeps it resident (the model is the load-
    bound part of the warm_group, so every cell in a sweep shares it); each `generate(job)` produces
    one image via the shared `_generate_item`. GPU code — exercised on-rig; the stdin/stdout protocol
    (`run_serve`) is what the no-GPU tests cover."""

    def __init__(self, output_dir: str, device: str) -> None:
        self.output_dir = output_dir
        self.device = device
        self.state: dict | None = None

    def _load(self, job: dict) -> None:
        model_name = job.get("model_name", "sd3.5-medium")
        if model_name not in SD35_MODEL_INFO:
            raise ValueError(f"unknown model_name {model_name!r}")
        mode = job.get("mode", "img2img")
        if mode not in _BATCH_MODES:
            raise ValueError(f"serve mode must be one of {_BATCH_MODES} (got {mode!r})")
        model_info = SD35_MODEL_INFO[model_name]
        # Honor the catalog's INVERTED flags (no_cpu_offload / no_skip_layer_guidance) directly —
        # the warm job spec carries them as-is (it doesn't pass through build_batch_argv's inversion).
        slg_on = job.get("skip_layer_guidance", True) and not job.get("no_skip_layer_guidance", False)
        skip_guidance_layers = model_info.get("skip_guidance_layers") if slg_on else None
        cpu_offload = job.get("cpu_offload", True) and not job.get("no_cpu_offload", False)
        s1 = stage1_load_pipeline.run(
            model_name=model_name, device=self.device,
            cpu_offload=cpu_offload, drop_t5=job.get("drop_t5", False),
            dtype=job.get("dtype", "bfloat16"), mode=mode, controlnet=None, controlnets=None,
        )
        self.state = {"s1": s1, "model_name": model_name, "defaults": model_info["defaults"],
                      "mode": mode, "skip_guidance_layers": skip_guidance_layers}

    def _resolve_out_dir(self, job: dict) -> Path:
        out_dir = Path(job.get("output_dir") or self.output_dir)
        if not out_dir.is_absolute():
            out_dir = Path(__file__).resolve().parents[3] / out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    def generate(self, job: dict) -> dict:
        if self.state is None:
            self._load(job)
        s = self.state
        merged = dict(job)                       # the spec already carries the per-cell params
        if merged.get("seed") is None:
            merged["seed"] = random.randrange(2**31)
        rec = _generate_item(
            s["s1"], model_name=s["model_name"], defaults=s["defaults"], mode=s["mode"],
            device=self.device, skip_guidance_layers=s["skip_guidance_layers"],
            merged=merged, out_dir=self._resolve_out_dir(job), idx=0,
        )
        ok = rec["status"] == "ok"
        return {"job_id": job.get("job_id"), "status": "ok" if ok else "failed",
                "output_path": rec["output_path"], "seed": rec["seed"],
                "width": merged.get("width", 1024), "height": merged.get("height", 1024),
                "duration_s": rec["duration_s"], "meta": job.get("meta"), "error": rec.get("error")}

    def close(self) -> None:
        self.state = None
        try:
            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass


def run_serve(output_dir: str = "src/assets/pics", device: str = "cuda", *,
              in_stream=None, emit=None, generator=None) -> int:
    """Persistent warm-worker loop (M2.7 Phase 2a): one image per stdin job line, pipeline loaded
    once. `in_stream`/`emit`/`generator` are injectable so the protocol is unit-testable without a
    GPU (mirrors the flux2 worker)."""
    if "PYTORCH_ALLOC_CONF" not in os.environ:
        os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"
    in_stream = in_stream if in_stream is not None else sys.stdin
    emit = emit if emit is not None else (lambda line: print(line, flush=True))
    gen = generator if generator is not None else _ServeGenerator(output_dir, device)
    n_ok = n_fail = 0
    print(f"[serve] ready (device={device})", flush=True)
    try:
        for raw in in_stream:
            line = raw.strip()
            if not line:
                continue
            try:
                job = json.loads(line)
            except json.JSONDecodeError as exc:
                emit(SERVE_RESULT_PREFIX + json.dumps(
                    {"job_id": None, "status": "failed", "error": f"bad job json: {exc}"}))
                continue
            if job.get("cmd") == "shutdown":
                break
            try:
                result = gen.generate(job)
            except Exception as exc:  # noqa: BLE001 — one image's failure never kills the worker
                import traceback
                traceback.print_exc()
                result = {"job_id": job.get("job_id"), "status": "failed",
                          "output_path": "", "error": str(exc), "meta": job.get("meta")}
            if result.get("status") == "ok":
                n_ok += 1
            else:
                n_fail += 1
            emit(SERVE_RESULT_PREFIX + json.dumps(result))
    finally:
        gen.close()
    print(f"[serve] done ({n_ok} ok / {n_fail} failed)", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description="SD 3.5 image generation pipeline")
    parser.add_argument("--prompt", required=False, default=None,
                        help="Text prompt for generation; required unless --jobs-file is given")
    parser.add_argument("--jobs-file", default=None,
                        help="Batch mode: JSON jobs file ({shared:{...}, items:[{prompt,seed,meta},...]}); "
                             "loads the pipeline once and generates every item (t2i/img2img/inpaint)")
    parser.add_argument("--serve", action="store_true",
                        help="Warm-worker mode (M2.7): read one JSON job per stdin line, load the "
                             "pipeline once, emit one image + a [serve-result] line per job.")
    parser.add_argument("--model-name", default="sd3.5-medium", choices=list(SD35_MODEL_INFO.keys()))
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--num-steps", type=int, default=None, help="Defaults to model preset")
    parser.add_argument("--guidance-scale", type=float, default=None, help="Defaults to model preset")
    parser.add_argument("--negative-prompt", default=None,
                        help="Negative prompt (not for turbo). SD 3.5 prefers SHORT or empty negatives.")
    parser.add_argument("--prompt-3", default=None,
                        help="Optional separate (longer) prompt sent only to T5. CLIP gets --prompt as anchor.")
    parser.add_argument("--negative-prompt-3", default=None,
                        help="Optional separate negative prompt for T5.")
    parser.add_argument("--max-sequence-length", type=int, default=512,
                        help="T5 context length (256 default, up to 512). 512 recommended for prose prompts.")
    parser.add_argument("--output-dir", default="src/assets/pics")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--no-cpu-offload", action="store_true", help="Disable CPU offload")
    parser.add_argument("--drop-t5", action="store_true",
                        help="Drop T5-XXL to save ~5GB VRAM. Hurts long-prompt comprehension.")
    parser.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float16"])
    # --- Skip Layer Guidance (anatomy + composition fix) ---
    parser.add_argument("--no-skip-layer-guidance", action="store_true",
                        help="Disable Skip Layer Guidance (default ON for medium+large; "
                             "improves anatomy / hands at small saturation cost)")
    parser.add_argument("--skip-layer-guidance-scale", type=float, default=2.8,
                        help="SLG scale (Stability AI default 2.8)")
    parser.add_argument("--skip-layer-guidance-start", type=float, default=0.01,
                        help="SLG start fraction (Stability AI default 0.01)")
    parser.add_argument("--skip-layer-guidance-stop",  type=float, default=0.2,
                        help="SLG stop fraction (Stability AI default 0.2)")
    # --- inpaint / cn-inpaint ---
    parser.add_argument(
        "--mode",
        default="t2i",
        choices=["t2i", "img2img", "inpaint", "cn-inpaint", "cn-inpaint-mc"],
        help="t2i (default), img2img (--init-image + --strength; no mask -- low-strength polish / "
             "global re-roll), inpaint (--init-image+--mask-image), cn-inpaint "
             "(adds --controlnet + --control-image), or cn-inpaint-mc "
             "(multi-CN inpaint: --init-image + --mask-image + --controlnets + --control-images)",
    )
    parser.add_argument("--init-image", default=None,
                        help="Path to base image for inpaint / cn-inpaint(-mc); resized to (width, height).")
    parser.add_argument("--mask-image", default=None,
                        help="Single-channel mask (white=repaint, black=preserve). Required for inpaint modes.")
    parser.add_argument("--control-image", default=None,
                        help="ControlNet conditioning image (e.g. depth.png). Required for cn-inpaint.")
    parser.add_argument("--control-images", default=None,
                        help="Comma-separated ControlNet conditioning images for cn-inpaint-mc. "
                             "Order MUST match --controlnets. Position 0 should be the original "
                             "scene image (for the inpaint CN); position 1+ the structural "
                             "conditioning images (e.g. depth.png).")
    parser.add_argument("--controlnet", default=None,
                        help="ControlNet repo: short key (depth/canny/pose/tile/inpaint) or full HF id.")
    parser.add_argument("--controlnets", default=None,
                        help="Comma-separated ControlNet repos for cn-inpaint-mc. First MUST be "
                             "an inpaint CN (e.g. 'inpaint' alias for alimama-creative/SD3-Controlnet-Inpainting); "
                             "rest are structural CNs (e.g. 'depth').")
    parser.add_argument("--cn-scale", default="1.0",
                        help="ControlNet conditioning scale. Single float for cn-inpaint, or "
                             "comma-separated floats matching --controlnets length for cn-inpaint-mc "
                             "(e.g. '0.95,0.5' for inpaint=0.95, depth=0.5). Default 1.0.")
    parser.add_argument("--strength", type=float, default=1.0,
                        help="Inpaint strength 0..1 (default 1.0 = full repaint).")
    args = parser.parse_args()

    if args.serve:
        sys.exit(run_serve(output_dir=args.output_dir, device=args.device))
    if args.jobs_file:
        sys.exit(run_jobs(args.jobs_file, output_dir=args.output_dir, device=args.device))
    if not args.prompt:
        parser.error("--prompt is required (or use --jobs-file for batch mode)")

    # Parse comma-list flags.
    parsed_controlnets = (
        [c.strip() for c in args.controlnets.split(",")] if args.controlnets else None
    )
    parsed_control_images = (
        [c.strip() for c in args.control_images.split(",")] if args.control_images else None
    )
    if "," in args.cn_scale:
        parsed_cn_scale: float | list[float] = [float(s.strip()) for s in args.cn_scale.split(",")]
    else:
        parsed_cn_scale = float(args.cn_scale)

    run(
        prompt=args.prompt,
        model_name=args.model_name,
        width=args.width,
        height=args.height,
        seed=args.seed,
        num_steps=args.num_steps,
        guidance_scale=args.guidance_scale,
        negative_prompt=args.negative_prompt,
        prompt_3=args.prompt_3,
        negative_prompt_3=args.negative_prompt_3,
        max_sequence_length=args.max_sequence_length,
        output_dir=args.output_dir,
        device=args.device,
        cpu_offload=not args.no_cpu_offload,
        drop_t5=args.drop_t5,
        dtype=args.dtype,
        skip_layer_guidance=not args.no_skip_layer_guidance,
        skip_layer_guidance_scale=args.skip_layer_guidance_scale,
        skip_layer_guidance_start=args.skip_layer_guidance_start,
        skip_layer_guidance_stop=args.skip_layer_guidance_stop,
        mode=args.mode,
        init_image=args.init_image,
        mask_image=args.mask_image,
        control_image=args.control_image,
        control_images=parsed_control_images,
        controlnet=args.controlnet,
        controlnets=parsed_controlnets,
        controlnet_conditioning_scale=parsed_cn_scale,
        strength=args.strength,
    )


if __name__ == "__main__":
    main()
