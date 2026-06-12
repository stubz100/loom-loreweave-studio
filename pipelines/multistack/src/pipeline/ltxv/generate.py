"""Standalone LTX-Video 0.9.x quick entry point.

Thin wrapper over run_pipeline.run() with the smallest sensible CLI for
smoke-testing and one-shot generation. For full control + manifest + resume
support, use run_pipeline.py instead.

Usage:
    python -m generate --prompt "..." --init-image path.png        # i2v
    python -m generate --prompt "..." --t2v                        # t2v
"""

import argparse
import os
import sys
from pathlib import Path

# Set ROCm allocator config before any torch import inside stages.
os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")

# Make stage modules importable when running as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_pipeline import run, _DEFAULT_OUTPUT_DIR  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description="LTX-Video 0.9.x quick generate (smoke-test entry point)",
    )
    parser.add_argument("--prompt", required=True, help="Text prompt")
    parser.add_argument(
        "--init-image",
        default=None,
        help="First-frame image (presence selects i2v; absence selects t2v unless --t2v is set)",
    )
    parser.add_argument(
        "--t2v", action="store_true",
        help="Force text-to-video mode (default i2v if --init-image is given, t2v otherwise)",
    )
    parser.add_argument(
        "--variant", default="2b_0.9.7_distilled",
        help="LTXV variant key (default: 2b_0.9.7_distilled)",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--steps", type=int, default=None)
    parser.add_argument("--guidance-scale", type=float, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--num-frames", type=int, default=None)
    parser.add_argument("--fps", type=int, default=None)
    parser.add_argument("--negative-prompt", default=None)
    parser.add_argument(
        "--offload",
        choices=["model", "sequential", "none"],
        default=None,
    )
    parser.add_argument("--output-dir", default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()

    # Mode resolution: explicit --t2v wins; else infer from --init-image presence.
    if args.t2v:
        mode = "t2v"
        if args.init_image:
            print("[generate] --t2v set; ignoring --init-image")
            args.init_image = None
    elif args.init_image:
        mode = "i2v"
    else:
        mode = "t2v"

    run(
        mode=mode,
        prompt=args.prompt,
        variant=args.variant,
        init_image=args.init_image,
        seed=args.seed,
        height=args.height,
        width=args.width,
        num_frames=args.num_frames,
        num_inference_steps=args.steps,
        guidance_scale=args.guidance_scale,
        fps=args.fps,
        negative_prompt=args.negative_prompt,
        offload=args.offload,
        output_dir=args.output_dir,
        device=args.device,
    )


if __name__ == "__main__":
    main()
