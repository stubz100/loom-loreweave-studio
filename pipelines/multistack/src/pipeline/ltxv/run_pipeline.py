"""LTX-Video 0.9.x pipeline orchestrator — t2v / i2v subcommands.

Stages:
  1. load_pipeline   — stage1_load_pipeline.run()
  2. generate        — stage2_generate.run() (denoise → latents)
     [tear down pipeline between stage 2 and stage 3 — ROCm-safe pattern]
  3. decode          — stage3_decode.run()  (isolated VAE decode)
  4. export          — stage4_export.run()  (frames → MP4)

Phase 1+2 implementation per kb-ltx09.md. Phase 3+ will add keyframes / extend
/ control subcommands using LTXConditionPipeline.

Usage:
    # I2V from a Flux2 still — fastest path
    python -m run_pipeline i2v --prompt "..." \
        --init-image src/assets/pics/flux2_hero.png

    # T2V from prompt only
    python -m run_pipeline t2v --prompt "..."

    # Resume from saved latents (skip stages 1+2)
    python -m run_pipeline i2v --prompt "..." \
        --init-image src/assets/pics/flux2_hero.png \
        --resume-latents path/to/run.latents.pt
"""

import argparse
import gc
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent.parent.parent

# Support both invocation styles:
#   1. `python -m src.pipeline.ltxv.run_pipeline ...` (from repo root)
#   2. `cd src/pipeline/ltxv && python -m run_pipeline ...` (wan2/zimage style)
sys.path.insert(0, str(_SCRIPT_DIR))           # ltxv/ — stages + manifest
sys.path.insert(0, str(_SCRIPT_DIR.parent))    # src/pipeline/ — _artifact_id

import _artifact_id  # noqa: E402

import stage1_load_pipeline, stage2_generate, stage3_decode, stage4_export  # noqa: E402
from manifest import PipelineManifest  # noqa: E402
from stage1_load_pipeline import LTXV_VARIANTS, variant_tag  # noqa: E402

_DEFAULT_OUTPUT_DIR = str(_REPO_ROOT / "src" / "assets" / "animation")


def _flush_gpu():
    import torch
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()


def run(
    mode: str,
    prompt: str,
    variant: str,
    init_image: str | None = None,
    seed: int = 42,
    height: int | None = None,
    width: int | None = None,
    num_frames: int | None = None,
    num_inference_steps: int | None = None,
    guidance_scale: float | None = None,
    fps: int | None = None,
    negative_prompt: str | None = None,
    offload: str | None = None,
    vae_dtype: str = "bfloat16",
    vae_spatial_tiling: bool = True,
    vae_auto_retry: bool = True,
    vae_decode_timestep: float = 0.0,               # matches installed diffusers default
    vae_decode_noise_scale: float | None = None,    # None → use variant default (0.0)
    image_cond_noise_scale: float | None = None,    # None → use variant default (0.0)
    resume_latents: str | None = None,
    output_dir: str = _DEFAULT_OUTPUT_DIR,
    device: str = "cuda",
) -> PipelineManifest:
    """Run the full LTX-Video generation pipeline.

    Args:
        mode: "t2v" or "i2v".
        prompt: Text prompt.
        variant: Key into LTXV_VARIANTS.
        init_image: First-frame image path (required for i2v).
        seed: Reproducibility seed.
        height/width/num_frames/num_inference_steps/guidance_scale/fps:
            Override variant defaults. None → use variant default.
        negative_prompt: Optional negative prompt.
        offload: "model" | "sequential" | "none". None → variant default.
            2B variants default to "none" (offload overhead > benefit at 4 GB).
        vae_dtype: "bfloat16" (recommended) or "float32".
        vae_spatial_tiling: Enable spatial tiling on VAE decode.
        vae_auto_retry: Auto-retry decode after OOM with tiling forced on.
        resume_latents: Path to a saved .latents.pt — skips stages 1 and 2.
        output_dir: Where to write the MP4 + manifest JSON.
        device: Torch device.

    Returns the completed PipelineManifest.
    """
    if variant not in LTXV_VARIANTS:
        raise ValueError(
            f"unknown variant {variant!r}; must be one of {list(LTXV_VARIANTS)}"
        )
    if mode not in ("t2v", "i2v"):
        raise ValueError(
            f"Phase 1+2 supports mode 't2v' or 'i2v' only (got {mode!r}). "
            f"keyframes/extend/control are Phase 3+ — see kb-ltx09.md."
        )
    if mode == "i2v" and not init_image and not resume_latents:
        raise ValueError("mode='i2v' requires --init-image (unless --resume-latents)")

    cfg = LTXV_VARIANTS[variant]

    # Apply variant defaults where caller didn't override.
    height = height or cfg["default_height"]
    width = width or cfg["default_width"]
    num_frames = num_frames or cfg["default_num_frames"]
    num_inference_steps = num_inference_steps or cfg["default_steps"]
    guidance_scale = guidance_scale if guidance_scale is not None else cfg["default_guidance_scale"]
    fps = fps or cfg["default_fps"]
    if vae_decode_noise_scale is None:
        vae_decode_noise_scale = cfg.get("default_decode_noise_scale", 0.025)
    if image_cond_noise_scale is None:
        image_cond_noise_scale = cfg.get("default_image_cond_noise_scale", 0.0)

    # Anchor relative output dirs to the repo root.
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = _REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build output filename.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tag = variant_tag(variant)
    if mode == "i2v" and init_image:
        stem = Path(init_image).stem
        out_name = f"{stem}_ltxv_{mode}_{tag}_s{seed}_{timestamp}.mp4"
    else:
        out_name = f"ltxv_{mode}_{tag}_s{seed}_{timestamp}.mp4"
    output_path = out_dir / out_name
    manifest_path = output_path.with_suffix(".json")
    latents_path = output_path.with_suffix(".latents.pt")

    # Build manifest skeleton.
    manifest = PipelineManifest(
        prompt=prompt,
        negative_prompt=negative_prompt or "",
        mode=mode,
        model_variant=variant,
        seed=seed,
        width=width,
        height=height,
        num_frames=num_frames,
        fps=fps,
        num_inference_steps=num_inference_steps,
        guidance_scale=guidance_scale,
        init_image=init_image or "",
        created_at=datetime.now(timezone.utc).isoformat(),
        device=device,
        output_path=str(output_path),
        working_dir=str(out_dir),
        run_id=_artifact_id.mint_run_id(seed),
        offload_strategy=offload or cfg["offload"],
        vae_dtype=vae_dtype,
    )
    manifest.pipeline_start = time.time()

    # ===== Stages 1 + 2: load pipeline + denoise (skipped on resume) =====
    if resume_latents:
        print(f"[ltxv] Resume mode — loading latents from {resume_latents}")
        if not Path(resume_latents).exists():
            raise FileNotFoundError(f"resume-latents path does not exist: {resume_latents}")
        latents_source = resume_latents
    else:
        # --- Stage 1: load pipeline ---
        rec = manifest.begin_stage(
            "load_pipeline",
            stage1_load_pipeline.get_manifest_inputs(
                variant=variant, mode=mode, device=device,
                offload_override=offload,
            ),
        )
        try:
            s1 = stage1_load_pipeline.run(
                variant=variant, mode=mode, device=device,
                offload_override=offload,
            )
            manifest.end_stage(
                rec,
                stage1_load_pipeline.get_manifest_outputs(s1),
                stage1_load_pipeline.get_manifest_debug(s1),
            )
            manifest.offload_strategy = s1["offload_strategy"]
            print(
                f"[stage1] Loaded {s1['model_id']}"
                f" ({s1['pipeline_class_name']}) in {rec.duration_s}s,"
                f" peak VRAM after load: {s1['vram_after_load_gb']} GB"
            )
        except Exception as e:
            manifest.fail_stage(rec, str(e))
            manifest.pipeline_end = time.time()
            manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
            manifest.save(manifest_path)
            raise

        # --- Stage 2: denoise → latents ---
        rec = manifest.begin_stage(
            "generate",
            stage2_generate.get_manifest_inputs(
                mode=mode, prompt=prompt, init_image=init_image,
                height=height, width=width, num_frames=num_frames,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale, seed=seed,
                negative_prompt=negative_prompt,
                image_cond_noise_scale=image_cond_noise_scale,
            ),
        )
        try:
            s2 = stage2_generate.run(
                pipe=s1["pipe"],
                mode=mode, prompt=prompt, init_image=init_image,
                height=height, width=width, num_frames=num_frames,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale, seed=seed,
                negative_prompt=negative_prompt,
                image_cond_noise_scale=image_cond_noise_scale,
                latents_save_path=latents_path,
            )
            manifest.end_stage(
                rec,
                stage2_generate.get_manifest_outputs(s2),
                stage2_generate.get_manifest_debug(s2),
            )
            print(
                f"[stage2] Denoise complete in {rec.duration_s}s"
                f" ({s2['steps_per_s']} steps/s, peak VRAM {s2['vram_peak_gb']} GB)"
            )
            print(f"[stage2] Latents saved to {s2['latents_path']}")
        except Exception as e:
            manifest.fail_stage(rec, str(e))
            manifest.pipeline_end = time.time()
            manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
            manifest.save(manifest_path)
            raise

        # --- Tear down pipeline before VAE decode (CRITICAL for ROCm) ---
        latents_source = s2["latents"]
        del s1
        _flush_gpu()
        print("[ltxv] Pipeline torn down; GPU flushed for isolated VAE decode")

    # ===== Stage 3: isolated VAE decode =====
    rec = manifest.begin_stage(
        "decode",
        stage3_decode.get_manifest_inputs(
            latents_source=resume_latents if resume_latents else "in_memory",
            model_id=cfg["model_id"],
            vae_subfolder="vae",
            vae_dtype=vae_dtype,
            device=device,
            decode_timestep=vae_decode_timestep,
            decode_noise_scale=vae_decode_noise_scale,
        ),
    )
    try:
        s3 = stage3_decode.run(
            latents=latents_source,
            model_id=cfg["model_id"],
            vae_subfolder="vae",
            vae_dtype=vae_dtype,
            device=device,
            enable_spatial_tiling=vae_spatial_tiling,
            auto_retry_oom=vae_auto_retry,
            decode_timestep=vae_decode_timestep,
            decode_noise_scale=vae_decode_noise_scale,
        )
        manifest.end_stage(
            rec,
            stage3_decode.get_manifest_outputs(s3),
            stage3_decode.get_manifest_debug(s3),
        )
        print(
            f"[stage3] Decode complete in {rec.duration_s}s"
            f" ({s3['num_frames']} frames, peak VAE VRAM {s3['vae_peak_vram_gb']} GB)"
        )
    except Exception as e:
        manifest.fail_stage(rec, str(e))
        manifest.pipeline_end = time.time()
        manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
        manifest.save(manifest_path)
        raise

    # ===== Stage 4: export MP4 =====
    rec = manifest.begin_stage(
        "export",
        stage4_export.get_manifest_inputs(
            output_path=str(output_path), fps=fps, num_frames=s3["num_frames"],
        ),
    )
    try:
        s4 = stage4_export.run(
            frames=s3["frames"], output_path=output_path, fps=fps,
        )
        manifest.end_stage(
            rec,
            stage4_export.get_manifest_outputs(s4),
            stage4_export.get_manifest_debug(s4),
        )
        print(f"[stage4] Saved {s4['output_path']} ({s4['file_size_bytes']:,} bytes)")
        manifest.artifacts.append(_artifact_id.make_artifact_record(
            output_path, kind="video/mp4", produced_by_stage="export",
        ))
    except Exception as e:
        manifest.fail_stage(rec, str(e))
        manifest.pipeline_end = time.time()
        manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
        manifest.save(manifest_path)
        raise

    # ===== Finalize =====
    manifest.pipeline_end = time.time()
    manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
    manifest.save(manifest_path)

    print(f"[done] Pipeline completed in {manifest.pipeline_duration_s}s")
    print(f"  Video:    {output_path}")
    print(f"  Manifest: {manifest_path}")
    return manifest


def _build_common_args(parser: argparse.ArgumentParser, mode: str):
    """Flags shared by both t2v and i2v subcommands."""
    parser.add_argument("--prompt", required=True, help="Text prompt describing the motion / scene")
    parser.add_argument(
        "--variant",
        default="2b_0.9.7_distilled",
        choices=list(LTXV_VARIANTS.keys()),
        help="LTX-Video variant to use (default: 2b_0.9.7_distilled — the "
             "diffusers-ready 2B distilled. 0.9.8 2B has no per-variant "
             "diffusers repo; use 13b_0.9.8_distilled for the 0.9.8 line.)",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--height", type=int, default=None, help="Override variant default")
    parser.add_argument("--width", type=int, default=None, help="Override variant default")
    parser.add_argument(
        "--num-frames", type=int, default=None,
        help="Number of frames (auto-rounded to 8N+1). Override variant default.",
    )
    parser.add_argument(
        "--steps", type=int, default=None,
        help="Denoising steps. Override variant default (distilled=8, dev=25-40).",
    )
    parser.add_argument(
        "--guidance-scale", type=float, default=None,
        help="CFG scale. Override variant default (distilled=3.0, dev=3.5-5.0).",
    )
    parser.add_argument("--fps", type=int, default=None, help="Output FPS (override)")
    parser.add_argument(
        "--negative-prompt", default=None,
        help="Negative prompt (helpful for LTXV — unlike Hunyuan distilled, "
             "LTXV distilled still honors negative prompts)",
    )
    parser.add_argument(
        "--offload",
        choices=["model", "sequential", "none"],
        default=None,
        help="Override variant default offload strategy. 2B defaults to 'none' "
             "(offload overhead > benefit); 13B defaults to 'model'.",
    )
    parser.add_argument(
        "--vae-dtype",
        choices=["bfloat16", "float32"],
        default="bfloat16",
        help="VAE decode dtype. float32 may OOM.",
    )
    parser.add_argument(
        "--vae-no-spatial-tile", action="store_true",
        help="Disable spatial tiling in the VAE decode (full latent frame per "
             "pass). LTXV's Video-VAE has 1:192 compression so the latent is "
             "small — tiling is rarely needed. Disabling can give 2-5× speedup.",
    )
    parser.add_argument(
        "--vae-no-auto-retry", action="store_true",
        help="Disable automatic VAE OOM fallback (retry with spatial tiling on).",
    )
    parser.add_argument(
        "--vae-decode-timestep", type=float, default=0.0,
        help="Timestep value passed as temb to LTX VAE (timestep-conditioned). "
             "Default 0.0 matches the installed diffusers pipeline signature "
             "default. Newer diffusers docs recommend 0.05 but the installed "
             "pipeline does not apply that by default.",
    )
    parser.add_argument(
        "--vae-decode-noise-scale", type=float, default=None,
        help="Fraction of unit-Gaussian noise mixed into latents before decode. "
             "Default: variant-specific (0.0 — matches installed diffusers "
             "default behavior where decode_noise_scale=None → 0.0, no noise "
             "added). Pass > 0 for experiments with noise injection.",
    )
    parser.add_argument(
        "--image-cond-noise-scale", type=float, default=None,
        help="LTX I2V-specific: fraction of noise added to the image "
             "conditioning during denoise. Default: variant-specific (0.0 for "
             "all 0.9.x variants per diffusers docs). Non-zero values blur the "
             "init image's influence on the first frame.",
    )
    parser.add_argument(
        "--resume-latents",
        default=None,
        help="Path to a saved .latents.pt — skips stages 1 and 2",
    )
    parser.add_argument("--output-dir", default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument("--device", default="cuda")
    parser.add_argument(
        "--keep-latents", action="store_true",
        help="Keep the .latents.pt sidecar after a successful run (default: delete)",
    )


def main():
    parser = argparse.ArgumentParser(
        description="LTX-Video 0.9.x generation pipeline (t2v / i2v)",
    )
    sub = parser.add_subparsers(dest="pipeline", required=True)

    p_t2v = sub.add_parser("t2v", help="Text-to-video (no first frame)")
    _build_common_args(p_t2v, mode="t2v")

    p_i2v = sub.add_parser("i2v", help="Image-to-video (first frame + text)")
    _build_common_args(p_i2v, mode="i2v")
    p_i2v.add_argument(
        "--init-image", required=False, default=None,
        help="Path to first-frame PNG (required unless --resume-latents)",
    )

    args = parser.parse_args()

    manifest = run(
        mode=args.pipeline,
        prompt=args.prompt,
        variant=args.variant,
        init_image=getattr(args, "init_image", None),
        seed=args.seed,
        height=args.height,
        width=args.width,
        num_frames=args.num_frames,
        num_inference_steps=args.steps,
        guidance_scale=args.guidance_scale,
        fps=args.fps,
        negative_prompt=args.negative_prompt,
        offload=args.offload,
        vae_dtype=args.vae_dtype,
        vae_spatial_tiling=not args.vae_no_spatial_tile,
        vae_auto_retry=not args.vae_no_auto_retry,
        vae_decode_timestep=args.vae_decode_timestep,
        vae_decode_noise_scale=args.vae_decode_noise_scale,
        image_cond_noise_scale=args.image_cond_noise_scale,
        resume_latents=args.resume_latents,
        output_dir=args.output_dir,
        device=args.device,
    )

    # Latent sidecar cleanup. Always-kept on resume runs (we didn't create it).
    if not args.resume_latents and not args.keep_latents:
        latents_path = Path(manifest.output_path).with_suffix(".latents.pt")
        if latents_path.exists():
            latents_path.unlink()
            print(f"[cleanup] Removed {latents_path}")


if __name__ == "__main__":
    # Set ROCm allocator config before any torch import inside stages.
    os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")
    main()
