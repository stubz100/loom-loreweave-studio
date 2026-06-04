"""Z-Image pipeline orchestrator — runs all stages and writes a JSON manifest."""

import argparse
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make src/pipeline/ importable for the shared _artifact_id helper.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _artifact_id  # noqa: E402

import stage1_load_pipeline, stage2_generate, stage3_save  # noqa: E402
from manifest import PipelineManifest  # noqa: E402
from stage1_load_pipeline import ZIMAGE_MODEL_INFO  # noqa: E402


def run(
    prompt: str,
    model_name: str = "zimage-turbo",
    width: int = 1024,
    height: int = 1024,
    seed: int | None = None,
    num_steps: int | None = None,
    guidance_scale: float | None = None,
    negative_prompt: str | None = None,
    cfg_normalization: bool | None = None,
    cfg_truncation: float | None = None,
    output_dir: str = "src/assets/pics",
    device: str = "cuda",
    cpu_offload: bool = True,
    dtype: str = "bfloat16",
    attention_backend: str | None = None,
    mode: str = "t2i",
    init_image: str | None = None,
    mask_image: str | None = None,
    strength: float | None = None,
) -> PipelineManifest:
    """Run the full Z-Image generation pipeline.

    Args:
        mode: One of "t2i" (default), "img2img", "inpaint". Selects the
            diffusers pipeline class loaded in stage 1.
        init_image: Path to a reference image (required for img2img and inpaint).
        mask_image: Path to a mask image (required for inpaint; white = repaint,
            black = preserve).
        strength: Override the per-mode default strength. img2img defaults to
            0.6 (balanced), inpaint defaults to 1.0 (free repaint of masked
            region).
        cfg_truncation: zimage-base only; fraction of the schedule (0-1) over
            which CFG is applied. <1.0 lets the final steps run unconditional.

    Returns the completed PipelineManifest.
    """
    if "PYTORCH_ALLOC_CONF" not in os.environ:
        os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"

    if seed is None:
        seed = random.randrange(2**31)

    model_info = ZIMAGE_MODEL_INFO[model_name]
    defaults = model_info["defaults"]
    if num_steps is None:
        num_steps = defaults["num_steps"]
    if guidance_scale is None:
        guidance_scale = defaults["guidance_scale"]

    if negative_prompt and not model_info["supports_negative_prompt"]:
        print(f"[warn] {model_name} does not support negative prompts -- ignoring")
        negative_prompt = None

    if cfg_normalization is not None and not model_info["supports_cfg_normalization"]:
        print(f"[warn] {model_name} does not support cfg_normalization -- ignoring")
        cfg_normalization = None

    if cfg_truncation is not None and not model_info["supports_cfg_normalization"]:
        # cfg_truncation is on the same Base-only CFG path as cfg_normalization
        print(f"[warn] {model_name} does not support cfg_truncation -- ignoring")
        cfg_truncation = None

    # --- Mode validation -- catch missing/extra image inputs early before model load ---
    if mode not in ("t2i", "img2img", "inpaint"):
        raise ValueError(f"--mode must be one of t2i, img2img, inpaint (got {mode!r})")
    if mode in ("img2img", "inpaint") and init_image is None:
        raise ValueError(f"mode={mode} requires --init-image")
    if mode == "inpaint" and mask_image is None:
        raise ValueError("mode=inpaint requires --mask-image")
    if mode == "t2i" and (init_image or mask_image):
        print("[warn] init_image/mask_image ignored in t2i mode")
        init_image = None
        mask_image = None
    if mode == "img2img" and mask_image is not None:
        print("[warn] mask_image ignored in img2img mode")
        mask_image = None

    # Anchor relative output dirs to the repo root, not the current cwd. When
    # this script is invoked from inside its package directory (which the
    # multi-pipeline stage_runner does for VRAM isolation reasons), a relative
    # path like "src/assets/pics" would otherwise resolve to
    # `<repo>/src/pipeline/zimage/src/assets/pics`.
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        repo_root = Path(__file__).resolve().parents[3]
        out_dir = repo_root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = out_dir / f"zimage_{timestamp}_s{seed}.png"
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

    # --- Stage 1: Load pipeline ---
    rec = manifest.begin_stage(
        "load_pipeline",
        stage1_load_pipeline.get_manifest_inputs(model_name, device, cpu_offload, attention_backend, mode),
    )
    try:
        s1 = stage1_load_pipeline.run(
            model_name=model_name,
            device=device,
            cpu_offload=cpu_offload,
            dtype=dtype,
            attention_backend=attention_backend,
            mode=mode,
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
            cfg_normalization, cfg_truncation, mode, init_image, mask_image, strength,
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
            cfg_normalization=cfg_normalization,
            cfg_truncation=cfg_truncation,
            mode=mode,
            init_image=init_image,
            mask_image=mask_image,
            strength=strength,
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


def main():
    parser = argparse.ArgumentParser(description="Z-Image image generation pipeline (t2i / img2img / inpaint)")
    parser.add_argument("--prompt", required=True, help="Text prompt (EN or ZH supported)")
    parser.add_argument("--model-name", default="zimage-turbo", choices=list(ZIMAGE_MODEL_INFO.keys()))
    parser.add_argument("--width", type=int, default=1024, help="Must be divisible by 16")
    parser.add_argument("--height", type=int, default=1024, help="Must be divisible by 16")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--num-steps", type=int, default=None, help="Defaults to model preset")
    parser.add_argument("--guidance-scale", type=float, default=None, help="Defaults to model preset")
    parser.add_argument("--negative-prompt", default=None, help="Negative prompt (zimage-base only)")
    parser.add_argument(
        "--cfg-normalization",
        action="store_true",
        help="Enable CFG normalization (zimage-base only; prefer for realism, off for stylism)",
    )
    parser.add_argument(
        "--cfg-truncation",
        type=float,
        default=None,
        help="zimage-base only: fraction (0-1) of schedule to apply CFG; <1.0 lets final steps run unconditional",
    )
    parser.add_argument("--output-dir", default="src/assets/pics")
    parser.add_argument("--device", default="cuda", help="Torch device (use 'cuda' for ROCm/HIP too)")
    parser.add_argument("--no-cpu-offload", action="store_true", help="Disable CPU offload")
    parser.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float16"])
    parser.add_argument(
        "--attention-backend",
        default=None,
        choices=["native_flash", "math", "flash", "_flash_3"],
        help="Attention backend. On ROCm use 'native_flash' or leave unset; avoid 'flash'/'_flash_3'",
    )
    # --- B.1 mode + image inputs ---
    parser.add_argument(
        "--mode",
        default="t2i",
        choices=["t2i", "img2img", "inpaint"],
        help="t2i (default), img2img (needs --init-image), or inpaint (needs --init-image and --mask-image)",
    )
    parser.add_argument(
        "--init-image",
        default=None,
        help="Path to reference image. Required for img2img and inpaint; resized to (width, height).",
    )
    parser.add_argument(
        "--mask-image",
        default=None,
        help="Path to mask image (single-channel L, white=repaint, black=preserve). Required for inpaint.",
    )
    parser.add_argument(
        "--strength",
        type=float,
        default=None,
        help="Reference strength for img2img/inpaint (0-1). img2img defaults to 0.6, inpaint to 1.0.",
    )
    args = parser.parse_args()

    run(
        prompt=args.prompt,
        model_name=args.model_name,
        width=args.width,
        height=args.height,
        seed=args.seed,
        num_steps=args.num_steps,
        guidance_scale=args.guidance_scale,
        negative_prompt=args.negative_prompt,
        cfg_normalization=args.cfg_normalization if args.cfg_normalization else None,
        cfg_truncation=args.cfg_truncation,
        output_dir=args.output_dir,
        device=args.device,
        cpu_offload=not args.no_cpu_offload,
        dtype=args.dtype,
        attention_backend=args.attention_backend,
        mode=args.mode,
        init_image=args.init_image,
        mask_image=args.mask_image,
        strength=args.strength,
    )


if __name__ == "__main__":
    main()
