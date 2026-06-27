"""Flux2 pipeline orchestrator — runs all 4 stages and writes a JSON manifest."""

import argparse
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import torch

# The BFL `flux2` library lives at <repo>/flux2/src (multi's stage_runner puts it on
# PYTHONPATH when IT spawns this worker). For a STANDALONE invocation (loom Stage-B fires
# `-m pipeline.flux2.run_pipeline` directly), self-bootstrap the same path so `import
# flux2.util` resolves — idempotent in the multi context (already on PYTHONPATH there).
_FLUX2_LIB_SRC = Path(__file__).resolve().parents[3] / "flux2" / "src"
if _FLUX2_LIB_SRC.is_dir() and str(_FLUX2_LIB_SRC) not in sys.path:
    sys.path.insert(0, str(_FLUX2_LIB_SRC))

from flux2.util import FLUX2_MODEL_INFO

from .. import _artifact_id
from . import scaled_fp8, stage1_load_models, stage2_text_encode, stage3_denoise, stage4_decode
from .manifest import PipelineManifest


def run(
    prompt: str,
    model_name: str = "flux.2-klein-4b",
    width: int = 1360,
    height: int = 768,
    seed: int | None = None,
    num_steps: int | None = None,
    guidance: float | None = None,
    output_dir: str = "src/assets/pics",
    device: str = "cuda",
    cpu_offload: bool = False,
    mode: str = "t2i",
    init_image: str | None = None,
    strength: float = 0.25,
    ref_images: list[str] | None = None,
    fp8_matmul: str = "auto",
    text_encoder_variant: str | None = None,
    dtype: str = "bfloat16",
    local_files_only: bool = True,
    turbo: bool = False,
    turbo_strength: float = 1.0,
) -> PipelineManifest:
    """Run the full Flux2 image generation pipeline.

    When cpu_offload=True, models are swapped between CPU and GPU between
    stages to fit large models (e.g. dev 32B) in limited VRAM.

    `mode="ref"` (loom multi-ref, §11): t2i generation CONDITIONED on `ref_images`
    (reference tokens carried in-context) — the "insert this character into a new scene"
    path. For a coverage-dataset SWEEP use the batch worker (`run_jobs`), which encodes the
    shared reference ONCE; this single-run path re-encodes per call.

    Returns the completed PipelineManifest.
    """
    if seed is None:
        seed = random.randrange(2**31)
    if mode not in ("t2i", "img2img", "ref"):
        raise ValueError(f"--mode must be one of t2i, img2img, ref (got {mode!r})")
    if mode == "img2img" and not init_image:
        raise ValueError("mode=img2img requires --init-image")
    if mode == "ref" and not ref_images:
        raise ValueError("mode=ref requires --ref-image (one or more)")

    model_info = FLUX2_MODEL_INFO[model_name]
    defaults = model_info.get("defaults", {})
    if num_steps is None:
        num_steps = defaults.get("num_steps", 50)
    if guidance is None:
        guidance = defaults.get("guidance", 4.0)

    # Anchor relative output dirs to the repo root, not the current cwd. The
    # multi-pipeline stage_runner invokes per-pipeline scripts with cwd set to
    # different directories for VRAM/import-path reasons; without this guard a
    # relative path like "src/assets/pics" would land in the wrong place.
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        repo_root = Path(__file__).resolve().parents[3]
        out_dir = repo_root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = out_dir / f"flux2_{timestamp}_s{seed}.png"
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

    guidance_distilled = model_info.get("guidance_distilled", True)
    torch_device = torch.device(device)

    # --- Stage 1: Load models ---
    rec = manifest.begin_stage("load_models", stage1_load_models.get_manifest_inputs(model_name, device, cpu_offload))
    try:
        s1 = stage1_load_models.run(
            model_name=model_name, device=device, cpu_offload=cpu_offload,
            dtype=dtype, local_files_only=local_files_only,
            fp8_matmul=fp8_matmul, text_encoder_variant=text_encoder_variant,
            turbo=turbo, turbo_strength=turbo_strength,
        )
        manifest.quantized = s1.get("quantized", {})  # M2.5: comfy-q8 lineage for dev, {} for Klein
        manifest.end_stage(rec, stage1_load_models.get_manifest_outputs(s1), stage1_load_models.get_manifest_debug(s1))
        print(f"[stage1] Models loaded in {rec.duration_s}s" + (" (cpu_offload)" if cpu_offload else ""))
    except Exception as e:
        manifest.fail_stage(rec, str(e))
        manifest.pipeline_end = time.time()
        manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
        manifest.save(manifest_path)
        raise

    # --- Stage 2: Text encoding ---
    rec = manifest.begin_stage("text_encode", stage2_text_encode.get_manifest_inputs(prompt, guidance_distilled))
    try:
        s2 = stage2_text_encode.run(
            prompt=prompt,
            text_encoder=s1["text_encoder"],
            guidance_distilled=guidance_distilled,
        )
        manifest.end_stage(rec, stage2_text_encode.get_manifest_outputs(s2), stage2_text_encode.get_manifest_debug(s2))
        print(f"[stage2] Text encoded in {rec.duration_s}s — ctx {s2['ctx'].shape}")
    except Exception as e:
        manifest.fail_stage(rec, str(e))
        manifest.pipeline_end = time.time()
        manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
        manifest.save(manifest_path)
        raise

    # --- CPU offload: swap text encoder out, flow model in ---
    if cpu_offload:
        print("[offload] Moving text encoder → CPU, flow model → GPU ...")
        s1["text_encoder"].cpu()
        del s1["text_encoder"]
        torch.cuda.empty_cache()
        s1["model"].to(torch_device)

    # --- Stage 3: Denoise ---
    rec = manifest.begin_stage(
        "denoise",
        stage3_denoise.get_manifest_inputs(width, height, seed, num_steps, guidance, guidance_distilled),
    )
    try:
        if mode == "img2img":
            s3 = stage3_denoise.run_img2img(
                model=s1["model"],
                ae=s1["ae"],
                ctx=s2["ctx"],
                ctx_ids=s2["ctx_ids"],
                init_image_path=init_image,
                width=width,
                height=height,
                seed=seed,
                num_steps=num_steps,
                guidance=guidance,
                guidance_distilled=guidance_distilled,
                strength=strength,
            )
        else:
            # t2i, or `ref` (t2i conditioned on reference images — needs the AE).
            s3 = stage3_denoise.run(
                model=s1["model"],
                ctx=s2["ctx"],
                ctx_ids=s2["ctx_ids"],
                width=width,
                height=height,
                seed=seed,
                num_steps=num_steps,
                guidance=guidance,
                guidance_distilled=guidance_distilled,
                ae=s1["ae"] if mode == "ref" else None,
                ref_images=ref_images if mode == "ref" else None,
            )
        manifest.end_stage(rec, stage3_denoise.get_manifest_outputs(s3), stage3_denoise.get_manifest_debug(s3))
        print(f"[stage3] Denoised in {rec.duration_s}s — x {s3['x'].shape} (mode={mode})")
    except Exception as e:
        manifest.fail_stage(rec, str(e))
        manifest.pipeline_end = time.time()
        manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
        manifest.save(manifest_path)
        raise

    # --- CPU offload: free flow model before decode ---
    if cpu_offload:
        print("[offload] Moving flow model → CPU ...")
        s1["model"].cpu()
        del s1["model"]
        torch.cuda.empty_cache()

    # --- Stage 4: Decode to image ---
    rec = manifest.begin_stage(
        "decode",
        stage4_decode.get_manifest_inputs(list(s3["x"].shape), list(s3["x_ids"].shape), str(output_path)),
    )
    try:
        s4 = stage4_decode.run(ae=s1["ae"], x=s3["x"], x_ids=s3["x_ids"], output_path=output_path)
        manifest.end_stage(rec, stage4_decode.get_manifest_outputs(s4), stage4_decode.get_manifest_debug(s4))
        print(f"[stage4] Decoded in {rec.duration_s}s -- saved {s4['output_path']}")
        # Record the saved PNG as an artifact so the multi-image session
        # manifest can reference it by artifact_id.
        manifest.artifacts.append(_artifact_id.make_artifact_record(
            output_path, kind="image/png", produced_by_stage="decode",
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


# --- Batch mode (--jobs-file): load the pipeline ONCE, generate N images ------------
#
# A coverage-dataset SWEEP (loom Stage-B) fires ONE invocation that loops the cells, so the
# (slow) model load is paid once. The jobs file is JSON:
#
#   {"shared": {"mode": "ref", "model_name": "flux.2-klein-4b", "width": 1024, ...,
#               "ref_images": ["/abs/hero.png"]},
#    "items":  [{"prompt": "...", "seed": 101, "meta": {...}}, ...]}
#
# `mode="ref"` (loom multi-ref, §11): the SHARED `ref_images` (the hero ★) are encoded ONCE
# into reference tokens that condition every cell's denoise — the identity-preserving
# expansion img2img can't do. Memory: encode ALL prompts first (text encoder), free it, then
# load the flow model + AE (so the 8 GB encoder and 8 GB flow model never co-reside on 16 GB).
# A `STOP` file finishes the current item then stops gracefully; a `flux2_batch_<ts>.json`
# summary records every item (each ok item also gets a PNG + .json sidecar).
def run_jobs(jobs_file: str, output_dir: str = "src/assets/pics", device: str = "cuda") -> int:
    import json as _json

    from PIL import Image

    from flux2.util import load_ae, load_flow_model
    from flux2.sampling import (
        batched_prc_img, batched_prc_txt, denoise, denoise_cfg, encode_image_refs, get_schedule,
    )

    spec = _json.loads(Path(jobs_file).read_text(encoding="utf-8"))
    shared = dict(spec.get("shared") or {})
    items = list(spec.get("items") or [])
    if not items:
        print("[batch-error] jobs file has no items")
        return 2
    for i, it in enumerate(items):
        if not (it.get("prompt") or "").strip():
            print(f"[batch-error] item {i} has no prompt")
            return 2

    model_name = shared.get("model_name", "flux.2-klein-4b")
    if model_name not in FLUX2_MODEL_INFO:
        print(f"[batch-error] unknown model_name {model_name!r}")
        return 2
    # M2.5: `flux.2-dev` routes the batch loaders to the quantized Comfy split files (the same
    # components the single-run path uses). The encode-all → free-TE → load-flow structure below
    # already keeps the ~17 GB Mistral TE and the ~34 GB fp8 transformer from co-residing on 16 GB,
    # so a coverage sweep pays the (slow) model load ONCE then loops the cells — far better than N
    # single-run dev casts. (Enabled for the expansion/curation screen's advanced prompting.)
    is_dev = model_name == "flux.2-dev"
    info = FLUX2_MODEL_INFO[model_name]
    distilled = info.get("guidance_distilled", True)
    defaults = info.get("defaults", {})
    mode = shared.get("mode", "ref")
    num_steps = shared.get("num_steps") or defaults.get("num_steps", 4)
    guidance = shared.get("guidance")
    if guidance is None:
        guidance = defaults.get("guidance", 1.0)
    width, height = int(shared.get("width", 1360)), int(shared.get("height", 768))
    device = shared.get("device", device)
    ref_paths = list(shared.get("ref_images") or [])
    torch_device = torch.device(device)
    # dev-only quantized knobs (ignored for Klein) — ride the shared block (adapter _SHARED_KEYS).
    fp8_matmul = shared.get("fp8_matmul", "auto")
    text_encoder_variant = shared.get("text_encoder")
    dtype = shared.get("dtype", "bfloat16")
    turbo = bool(shared.get("turbo"))           # M2.6: Turbo LoRA for viable low-step dev sweeps
    turbo_strength = float(shared.get("turbo_strength", 1.0))

    out_dir = Path(shared.get("output_dir") or output_dir)
    if not out_dir.is_absolute():
        out_dir = Path(__file__).resolve().parents[3] / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    batch_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    summary_path = out_dir / f"flux2_batch_{batch_ts}.json"
    t0 = time.time()
    print(f"[batch] {len(items)} item(s) | mode={mode} model={model_name} refs={len(ref_paths)}")
    results: list[dict] = []

    def _skip_rest(start: int, reason: str) -> None:
        for j in range(start, len(items)):
            results.append({"index": j, "status": "skipped", "seed": items[j].get("seed"),
                            "prompt": items[j]["prompt"], "output_path": "", "manifest_path": "",
                            "duration_s": 0.0, "error": reason, "meta": items[j].get("meta")})

    def _finish(status: str, load_s: float, error: str | None = None) -> int:
        n_ok = sum(1 for r in results if r["status"] == "ok")
        n_fail = sum(1 for r in results if r["status"] == "failed")
        n_skip = sum(1 for r in results if r["status"] == "skipped")
        if n_ok == 0 and status == "completed":
            status = "failed"
        summary = {"kind": "jobs_batch", "schema_version": 1, "pipeline": "flux2",
                   "model_name": model_name, "mode": mode, "status": status, "error": error,
                   "backend_variant": "comfy-q8" if is_dev else None,  # M2.5 quantized-dev lineage
                   "count": len(items), "ok": n_ok, "failed": n_fail, "skipped": n_skip,
                   "load_duration_s": load_s, "total_duration_s": round(time.time() - t0, 4),
                   "items": results}
        summary_path.write_text(_json.dumps(summary, indent=1), encoding="utf-8")
        print(f"[batch-done] {n_ok} ok / {n_fail} failed / {n_skip} skipped in "
              f"{summary['total_duration_s']}s ({status})")
        print(f"  BatchManifest: {summary_path}")
        return 0 if n_ok > 0 else 2

    def _fail_preload(reason: str, exc: Exception, load_s: float) -> int:
        # A pre-loop bail (encode/model-load/ref-encode) skips EVERY item — surface the real
        # cause to stderr + the manifest error, so it isn't a silent "[batch-done] N skipped"
        # with no WHY (the batch manifest can be pruned with the job). User 2026-06-22.
        import traceback
        print(f"[batch-error] {reason}: {exc}", flush=True)
        traceback.print_exc()
        _skip_rest(0, reason)
        return _finish("failed", load_s, error=f"{reason}: {exc}")

    # --- Phase 1: encode ALL prompts, then free the text encoder ---
    try:
        if is_dev:
            te_variant = scaled_fp8.normalize_text_encoder_variant(text_encoder_variant or "fp8")
            te_path = scaled_fp8.resolve_hf_file(
                scaled_fp8.COMFY_FLUX2_REPO, scaled_fp8.TEXT_ENCODER_FILES[te_variant])
            te_model, processor, _ = scaled_fp8.load_comfy_mistral_text_encoder(
                te_path, device=torch_device, dtype=scaled_fp8.resolve_dtype(dtype), fp8_matmul=fp8_matmul)
            enc = stage1_load_models.ComfyMistralEmbedder(te_model, processor)
        else:
            enc = stage1_load_models._load_text_encoder_safe(model_name, torch_device)
        enc.eval()
        ctxs: list[tuple] = []
        with torch.no_grad():
            for it in items:
                if distilled:
                    c = enc([it["prompt"]]).to(torch.bfloat16)
                else:
                    c = torch.cat([enc([""]), enc([it["prompt"]])], dim=0).to(torch.bfloat16)
                c, cid = batched_prc_txt(c)
                ctxs.append((c.cpu(), cid.cpu()))
        del enc
        torch.cuda.empty_cache()
    except Exception as e:  # noqa: BLE001
        return _fail_preload("text encode failed", e, round(time.time() - t0, 2))
    load_s = round(time.time() - t0, 2)
    print(f"[stage1] text encoded for {len(items)} item(s); encoder freed ({load_s}s)")

    # --- Phase 2: flow model + AE; encode the shared reference(s) ONCE ---
    try:
        if is_dev:
            tr_path = scaled_fp8.resolve_hf_file(scaled_fp8.COMFY_FLUX2_REPO, scaled_fp8.TRANSFORMER_FILE)
            model, _ = scaled_fp8.load_comfy_flux2_transformer(
                tr_path, device=torch_device, dtype=scaled_fp8.resolve_dtype(dtype), fp8_matmul=fp8_matmul)
            vae_path = scaled_fp8.resolve_hf_file(scaled_fp8.COMFY_FLUX2_REPO, scaled_fp8.VAE_FILE)
            # VAE in float32 (matches Klein) — required for ref/i2i encode of a float32 image; the
            # bf16 latent is promoted in the VAE's inv_normalize, so decode is fine too.
            ae, _ = scaled_fp8.load_comfy_vae(vae_path, device=torch_device, dtype=torch.float32)
            if turbo:  # M2.6: attach the Turbo LoRA so the whole sweep runs few-step (loaded once)
                scaled_fp8.apply_turbo_lora(model, device=torch_device, strength=turbo_strength)
        else:
            model = load_flow_model(model_name, device=torch_device)
            ae = load_ae(model_name, device=torch_device)
        model.eval()
        ae.eval()
    except Exception as e:  # noqa: BLE001
        return _fail_preload("model load failed", e, load_s)

    ref_tokens = ref_ids = None
    if ref_paths:
        try:
            with torch.no_grad():
                refs = [Image.open(p).convert("RGB") for p in ref_paths]
                ref_tokens, ref_ids = encode_image_refs(ae, refs)
            if ref_tokens is not None:
                ref_tokens, ref_ids = ref_tokens.to(torch_device), ref_ids.to(torch_device)
            n_tok = int(ref_tokens.shape[1]) if ref_tokens is not None else 0
            print(f"[stage1] encoded {len(refs)} reference image(s) -> {n_tok} ref tokens")
        except Exception as e:  # noqa: BLE001
            return _fail_preload("reference encode failed", e, load_s)

    status = "completed"
    stop_file = out_dir / "STOP"
    for idx, it in enumerate(items):
        if stop_file.exists():
            print(f"[batch] STOP file found -- stopping before item {idx + 1}/{len(items)}")
            _skip_rest(idx, "stopped")
            status = "stopped"
            break
        seed = it.get("seed")
        if seed is None:
            seed = random.randrange(2**31)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = out_dir / f"flux2_{ts}_i{idx:03d}_s{seed}.png"
        manifest_path = output_path.with_suffix(".json")
        rec = {"index": idx, "status": "failed", "seed": seed, "prompt": it["prompt"],
               "output_path": "", "manifest_path": "", "duration_s": 0.0,
               "error": None, "meta": it.get("meta")}
        it0 = time.time()
        try:
            ctx, ctx_ids = ctxs[idx]
            ctx, ctx_ids = ctx.to(torch_device), ctx_ids.to(torch_device)
            noise_shape = (1, 128, height // 16, width // 16)
            g = torch.Generator(device="cuda").manual_seed(int(seed))
            noise = torch.randn(noise_shape, generator=g, dtype=torch.bfloat16, device="cuda")
            x, x_ids = batched_prc_img(noise)
            timesteps = get_schedule(num_steps, x.shape[1])
            with torch.no_grad():
                if distilled:
                    x = denoise(model, x, x_ids, ctx, ctx_ids, timesteps=timesteps,
                                guidance=guidance, img_cond_seq=ref_tokens,
                                img_cond_seq_ids=ref_ids)
                else:
                    x = denoise_cfg(model, x, x_ids, ctx, ctx_ids, timesteps=timesteps,
                                    guidance=guidance, img_cond_seq=ref_tokens,
                                    img_cond_seq_ids=ref_ids)
                s4 = stage4_decode.run(ae=ae, x=x, x_ids=x_ids, output_path=output_path)
            manifest_path.write_text(_json.dumps({
                "kind": "flux2_item", "model_name": model_name, "mode": mode,
                "prompt": it["prompt"], "seed": seed, "ref_images": ref_paths,
                "width": s4["width"], "height": s4["height"], "output_path": str(output_path),
            }, indent=1), encoding="utf-8")
            rec.update(status="ok", output_path=str(output_path),
                       manifest_path=str(manifest_path), duration_s=round(time.time() - it0, 3))
            print(f"[item {idx + 1}/{len(items)}] seed={seed} ok")
            print(f"  Image: {output_path}")
        except Exception as e:  # noqa: BLE001 — per-item failure doesn't fail the batch
            rec["error"] = str(e)
            print(f"[item {idx + 1}/{len(items)}] FAILED: {e}")
        results.append(rec)

    return _finish(status, load_s)


# --- Serve mode (--serve): a PERSISTENT warm worker (M2.7 Phase 1) -------------------
#
# The runner feeds same-`warm_group` cell-jobs (one image each) to ONE long-lived process so the
# model loads ONCE for the whole sweep, while each image is its own queue entry that persists the
# moment it's done. Protocol:
#   stdin  (runner -> worker): one JSON job per line — {job_id, model_name, prompt, mode, width,
#           height, seed, ref_images, num_steps, guidance, turbo, text_encoder, fp8_matmul, meta}.
#           A `{"cmd":"shutdown"}` line (or EOF) exits cleanly.
#   stdout (worker -> runner): free-text progress (the runner streams it to the job log) + ONE
#           result line per job, framed `SERVE_RESULT_PREFIX + <json>`:
#           {job_id, status:"ok"|"failed", output_path, seed, width, height, duration_s, meta, error}.
# The model (flow + AE, encoded ref) stays resident across jobs; the text encoder is (re)loaded per
# job by stage1 — the dev/klein TE can't co-reside with the flow model on 16 GB (the batch worker's
# encode-all-then-free-TE avoids that; an encode-ahead buffer to recover it is a later phase).
SERVE_RESULT_PREFIX = "[serve-result] "


class _ServeGenerator:
    """Lazy-loads the flow model + AE (+ ref) from the FIRST job and keeps them resident; each
    `generate(job)` produces one image. The text encoder rides stage1's load. GPU code — exercised
    on-rig; the stdin/stdout protocol (`run_serve`) is what the no-GPU tests cover."""

    def __init__(self, output_dir: str, device: str) -> None:
        self.output_dir = output_dir
        self.device = device
        self.state: dict | None = None

    def _load(self, job: dict) -> None:
        from flux2.sampling import encode_image_refs
        from PIL import Image

        model_name = job.get("model_name", "flux.2-klein-4b")
        torch_device = torch.device(self.device)
        s1 = stage1_load_models.run(
            model_name=model_name, device=self.device, cpu_offload=False,
            fp8_matmul=job.get("fp8_matmul", "auto"),
            text_encoder_variant=job.get("text_encoder"),
            turbo=bool(job.get("turbo")),
        )
        info = FLUX2_MODEL_INFO[model_name]
        ref_tokens = ref_ids = None
        ref_paths = list(job.get("ref_images") or [])
        if job.get("mode") == "ref" and ref_paths:
            with torch.no_grad():
                refs = [Image.open(p).convert("RGB") for p in ref_paths]
                ref_tokens, ref_ids = encode_image_refs(s1["ae"], refs)
                if ref_tokens is not None:
                    ref_tokens, ref_ids = ref_tokens.to(torch_device), ref_ids.to(torch_device)
        out_dir = Path(self.output_dir)
        if not out_dir.is_absolute():
            out_dir = Path(__file__).resolve().parents[3] / out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        self.state = {
            "model": s1["model"], "ae": s1["ae"], "enc": s1["text_encoder"],
            "distilled": info.get("guidance_distilled", True), "defaults": info.get("defaults", {}),
            "ref_tokens": ref_tokens, "ref_ids": ref_ids, "out_dir": out_dir,
            "model_name": model_name,
        }

    def generate(self, job: dict) -> dict:
        from flux2.sampling import batched_prc_img, batched_prc_txt, denoise, denoise_cfg, get_schedule

        if self.state is None:
            self._load(job)
        s = self.state
        seed = job.get("seed")
        if seed is None:
            seed = random.randrange(2**31)
        width, height = int(job.get("width", 1360)), int(job.get("height", 768))
        num_steps = int(job.get("num_steps") or s["defaults"].get("num_steps", 4))
        guidance = job.get("guidance")
        if guidance is None:
            guidance = s["defaults"].get("guidance", 1.0)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = s["out_dir"] / f"flux2_{ts}_s{seed}.png"
        t0 = time.time()
        with torch.no_grad():
            c = s["enc"]([job["prompt"]]).to(torch.bfloat16)
            if not s["distilled"]:
                c = torch.cat([s["enc"]([""]).to(torch.bfloat16), c], dim=0)
            ctx, ctx_ids = batched_prc_txt(c)
            g = torch.Generator(device="cuda").manual_seed(int(seed))
            noise = torch.randn((1, 128, height // 16, width // 16), generator=g,
                                dtype=torch.bfloat16, device="cuda")
            x, x_ids = batched_prc_img(noise)
            timesteps = get_schedule(num_steps, x.shape[1])
            dn = denoise if s["distilled"] else denoise_cfg
            x = dn(s["model"], x, x_ids, ctx, ctx_ids, timesteps=timesteps, guidance=guidance,
                   img_cond_seq=s["ref_tokens"], img_cond_seq_ids=s["ref_ids"])
            s4 = stage4_decode.run(ae=s["ae"], x=x, x_ids=x_ids, output_path=out_path)
        return {"job_id": job.get("job_id"), "status": "ok", "output_path": str(out_path),
                "seed": seed, "width": s4["width"], "height": s4["height"],
                "duration_s": round(time.time() - t0, 3), "meta": job.get("meta"), "error": None}

    def close(self) -> None:
        self.state = None
        try:
            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass


def run_serve(output_dir: str = "src/assets/pics", device: str = "cuda", *,
              in_stream=None, emit=None, generator=None) -> int:
    """Persistent warm-worker loop (M2.7): one image per stdin job line, model loaded once.
    `in_stream`/`emit`/`generator` are injectable so the protocol is unit-testable without a GPU."""
    import json as _json
    import sys as _sys

    in_stream = in_stream if in_stream is not None else _sys.stdin
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
                job = _json.loads(line)
            except _json.JSONDecodeError as exc:
                emit(SERVE_RESULT_PREFIX + _json.dumps(
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
            emit(SERVE_RESULT_PREFIX + _json.dumps(result))
    finally:
        gen.close()
    print(f"[serve] done ({n_ok} ok / {n_fail} failed)", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Flux2 image generation pipeline")
    parser.add_argument("--prompt", default=None, help="Text prompt (required unless --jobs-file)")
    parser.add_argument("--model-name", default="flux.2-klein-4b", choices=list(FLUX2_MODEL_INFO.keys()))
    parser.add_argument("--width", type=int, default=1360)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--num-steps", type=int, default=None, help="Defaults to model preset")
    parser.add_argument("--guidance", type=float, default=None, help="Defaults to model preset")
    parser.add_argument("--output-dir", default="src/assets/pics")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--cpu-offload", action="store_true", help="Swap models between CPU/GPU to save VRAM")
    parser.add_argument("--mode", default="t2i", choices=["t2i", "img2img", "ref"],
                        help="t2i (default); img2img (--init-image + --strength); or ref "
                             "(--ref-image: t2i conditioned on reference images, the multi-ref §11 path).")
    parser.add_argument("--init-image", default=None,
                        help="Path to init image for img2img mode; centre-cropped to multiple of 16.")
    parser.add_argument("--strength", type=float, default=0.25,
                        help="img2img strength (0,1]; 0.20-0.25 typical for polish, higher for re-roll.")
    parser.add_argument("--ref-image", action="append", default=None, dest="ref_image",
                        help="Reference image path (repeatable, <=4 Klein/<=6 dev) for --mode ref.")
    parser.add_argument("--jobs-file", default=None,
                        help="Batch mode: a JSON {shared, items} file — one load, N images.")
    parser.add_argument("--serve", action="store_true",
                        help="Warm-worker mode (M2.7): read one JSON job per stdin line, load the "
                             "model once, emit one image + a [serve-result] line per job.")
    # M2.5 dev-only knobs (ignored for Klein): the quantized `flux.2-dev` text-encoder precision
    # and the scaled-FP8 matmul backend.
    parser.add_argument("--text-encoder", dest="text_encoder", default=None,
                        choices=list(scaled_fp8.TEXT_ENCODER_CLI_CHOICES),
                        help="flux.2-dev only: Mistral TE precision (fp8 default / bf16; fp16 aliases bf16).")
    parser.add_argument("--fp8-matmul", dest="fp8_matmul", default="auto",
                        choices=list(scaled_fp8.FP8_MATMUL_MODES),
                        help="flux.2-dev only: scaled-FP8 Linear backend (auto/native use torch._scaled_mm).")
    parser.add_argument("--turbo", action="store_true",
                        help="flux.2-dev only (M2.6): attach the Flux2-Turbo LoRA for viable low-step "
                             "(~4-6) generation — JSON prompting unaffected (TE untouched).")
    args = parser.parse_args()

    if args.serve:
        raise SystemExit(run_serve(output_dir=args.output_dir, device=args.device))
    if args.jobs_file:
        raise SystemExit(run_jobs(args.jobs_file, output_dir=args.output_dir, device=args.device))
    if not args.prompt:
        parser.error("--prompt is required (unless --jobs-file)")

    run(
        prompt=args.prompt,
        model_name=args.model_name,
        width=args.width,
        height=args.height,
        seed=args.seed,
        num_steps=args.num_steps,
        guidance=args.guidance,
        output_dir=args.output_dir,
        device=args.device,
        cpu_offload=args.cpu_offload,
        mode=args.mode,
        init_image=args.init_image,
        strength=args.strength,
        ref_images=args.ref_image,
        fp8_matmul=args.fp8_matmul,
        text_encoder_variant=args.text_encoder,
        turbo=args.turbo,
    )


if __name__ == "__main__":
    main()
