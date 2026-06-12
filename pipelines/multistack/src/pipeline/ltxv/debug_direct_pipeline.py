"""Diagnostic: bypass our two-phase pipeline and call diffusers directly.

If THIS produces clean output, the bug is in our isolated VAE decode path
(stage 2 unpack OR stage 3 denorm/noise-mix/decode).

If THIS ALSO produces "VHS-like" degraded output, the bug is somewhere else —
the model+input combination, the VAE weights, or something further upstream.

Usage:
    python -m src.pipeline.ltxv.debug_direct_pipeline `
        --variant 2b_0.9.7_distilled `
        --init-image src/assets/pics/.../zimage_20260524_200624_s491651418.png `
        --prompt "A girl slowly pulls her hand away from the window"
"""

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")

import torch
from diffusers import LTXImageToVideoPipeline
from diffusers.utils import load_image, export_to_video


_VARIANTS = {
    "2b_0.9.7_distilled": "Lightricks/LTX-Video-0.9.7-distilled",
    "2b_0.9.7_dev":       "Lightricks/LTX-Video-0.9.7-dev",
    "13b_0.9.8_distilled": "Lightricks/LTX-Video-0.9.8-13B-distilled",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", default="2b_0.9.7_distilled",
                        choices=list(_VARIANTS))
    parser.add_argument("--init-image", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--num-frames", type=int, default=33)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--width", type=int, default=704)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--guidance-scale", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--decode-timestep", type=float, default=0.0,
                        help="Installed diffusers default: 0.0")
    parser.add_argument("--decode-noise-scale", type=float, default=0.0,
                        help="Installed diffusers default: None (→ 0.0). "
                             "Newer diffusers/docs recommend 0.025 but the "
                             "installed pipeline doesn't apply this by default.")
    parser.add_argument("--output", default="ltxv_direct_pipeline_test.mp4")
    args = parser.parse_args()

    model_id = _VARIANTS[args.variant]
    print(f"Loading {model_id}...")
    pipe = LTXImageToVideoPipeline.from_pretrained(
        model_id, torch_dtype=torch.bfloat16,
    )
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_tiling()
    try:
        pipe.vae.enable_slicing()
    except Exception:
        pass

    image = load_image(str(args.init_image))
    print(f"Loaded init image: {image.size}")

    # Generator must be on cuda so the decode-time torch.randn (which uses it)
    # produces a cuda tensor that matches the device of latents.
    generator = torch.Generator(device="cuda").manual_seed(args.seed)

    print(f"Generating with: num_frames={args.num_frames}, "
          f"{args.width}x{args.height}, steps={args.steps}, "
          f"guidance_scale={args.guidance_scale}, "
          f"decode_timestep={args.decode_timestep}, "
          f"decode_noise_scale={args.decode_noise_scale}")

    output = pipe(
        image=image,
        prompt=args.prompt,
        num_frames=args.num_frames,
        height=args.height,
        width=args.width,
        num_inference_steps=args.steps,
        guidance_scale=args.guidance_scale,
        decode_timestep=args.decode_timestep,
        decode_noise_scale=args.decode_noise_scale,
        generator=generator,
    ).frames[0]

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = Path("src/assets/animation") / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    export_to_video(output, str(out_path), fps=24)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
