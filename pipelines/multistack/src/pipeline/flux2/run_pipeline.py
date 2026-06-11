"""Flux2 pipeline orchestrator — runs all 4 stages and writes a JSON manifest."""

import argparse
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import torch

from flux2.util import FLUX2_MODEL_INFO

from .. import _artifact_id
from . import stage1_load_models, stage2_text_encode, stage3_denoise, stage4_decode
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
) -> PipelineManifest:
    """Run the full Flux2 image generation pipeline.

    When cpu_offload=True, models are swapped between CPU and GPU between
    stages to fit large models (e.g. dev 32B) in limited VRAM.

    Returns the completed PipelineManifest.
    """
    if seed is None:
        seed = random.randrange(2**31)
    if mode not in ("t2i", "img2img"):
        raise ValueError(f"--mode must be one of t2i, img2img (got {mode!r})")
    if mode == "img2img" and not init_image:
        raise ValueError("mode=img2img requires --init-image")

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
        s1 = stage1_load_models.run(model_name=model_name, device=device, cpu_offload=cpu_offload)
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


def main():
    parser = argparse.ArgumentParser(description="Flux2 image generation pipeline")
    parser.add_argument("--prompt", required=True, help="Text prompt for generation")
    parser.add_argument("--model-name", default="flux.2-klein-4b", choices=list(FLUX2_MODEL_INFO.keys()))
    parser.add_argument("--width", type=int, default=1360)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--num-steps", type=int, default=None, help="Defaults to model preset")
    parser.add_argument("--guidance", type=float, default=None, help="Defaults to model preset")
    parser.add_argument("--output-dir", default="src/assets/pics")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--cpu-offload", action="store_true", help="Swap models between CPU/GPU to save VRAM")
    parser.add_argument("--mode", default="t2i", choices=["t2i", "img2img"],
                        help="t2i (default), or img2img (--init-image + --strength; "
                             "low-strength polish / global re-roll using flow-matching init mix).")
    parser.add_argument("--init-image", default=None,
                        help="Path to init image for img2img mode; centre-cropped to multiple of 16.")
    parser.add_argument("--strength", type=float, default=0.25,
                        help="img2img strength (0,1]; 0.20-0.25 typical for polish, higher for re-roll.")
    args = parser.parse_args()

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
    )


if __name__ == "__main__":
    main()
