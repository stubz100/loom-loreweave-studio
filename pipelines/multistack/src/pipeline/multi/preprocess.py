"""Phase C — Auxiliary preprocessing tooling for multi-pipeline architectures.

Six helpers, each a pure function PIL.Image -> PIL.Image (or similar):

- extract_canny(img, low, high)        -- OpenCV Canny edge detection
- extract_depth(img)                   -- Marigold depth map (lazy-loaded model)
- extract_pose(img)                    -- DWPose / OpenPose skeleton (lazy-loaded)
- make_mask_from_alpha(rgba, threshold)-- alpha channel -> binary mask
- make_mask_from_bbox(bbox, size)      -- white rect on black canvas
- composite_alpha(fg_rgba, bg_rgb)     -- alpha-composite foreground over background

Heavy model dependencies (Marigold, DWPose) are lazy-imported inside the
relevant function; the module imports cleanly on a machine without them, and
the lighter helpers (canny, masks, composite) work without those dependencies.

CLI dispatcher at the bottom: `python -m pipeline.multi.preprocess --tool <name>
--in <path> [--out <path>]` for ad-hoc use and the C-T1 through C-T6 smoke
tests.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image


# --- Lightweight helpers (pure PIL/numpy, no model load) --------------------


def extract_canny(
    image: Image.Image,
    low: int = 100,
    high: int = 200,
) -> Image.Image:
    """Run OpenCV Canny edge detection on a PIL image.

    Returns a 3-channel RGB image where edges are white and background is
    black -- the format ControlNet-Canny expects as input.
    """
    try:
        import cv2
    except ImportError as e:
        raise ImportError(
            "extract_canny requires opencv-python; pip install opencv-python"
        ) from e

    arr = np.array(image.convert("L"))
    edges = cv2.Canny(arr, low, high)
    # ControlNet-Canny expects 3-channel input
    edges_rgb = np.stack([edges] * 3, axis=-1)
    return Image.fromarray(edges_rgb, mode="RGB")


def make_mask_from_alpha(
    image: Image.Image,
    threshold: int = 128,
) -> Image.Image:
    """Convert the alpha channel of an RGBA image to a binary mask.

    White (255) where alpha >= threshold, black (0) otherwise. Output mode is
    'L' (single-channel) -- matches the inpaint-mask convention used by all
    three pipelines (white = repaint, black = preserve).
    """
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    alpha = np.array(image)[..., 3]
    mask = np.where(alpha >= threshold, 255, 0).astype(np.uint8)
    return Image.fromarray(mask, mode="L")


def make_mask_from_bbox(
    bbox: tuple[int, int, int, int],
    size: tuple[int, int],
) -> Image.Image:
    """Draw a white rectangle on a black canvas of given size.

    Args:
        bbox: (x1, y1, x2, y2) in pixel coords. Coordinates are clamped to
            the canvas; if x2 <= x1 or y2 <= y1 the result is all-black.
        size: (width, height) of the output canvas.

    Returns a single-channel ('L' mode) PIL image.
    """
    w, h = size
    x1, y1, x2, y2 = bbox
    x1 = max(0, min(w, x1))
    y1 = max(0, min(h, y1))
    x2 = max(0, min(w, x2))
    y2 = max(0, min(h, y2))
    arr = np.zeros((h, w), dtype=np.uint8)
    if x2 > x1 and y2 > y1:
        arr[y1:y2, x1:x2] = 255
    return Image.fromarray(arr, mode="L")


def composite_alpha(
    foreground: Image.Image,
    background: Image.Image,
) -> Image.Image:
    """Alpha-composite an RGBA foreground over an RGB(A) background.

    The foreground is resized to match the background's dimensions if needed.
    Result is RGB (alpha discarded after compositing).
    """
    if foreground.mode != "RGBA":
        foreground = foreground.convert("RGBA")
    if background.mode not in ("RGB", "RGBA"):
        background = background.convert("RGB")
    if foreground.size != background.size:
        foreground = foreground.resize(background.size)
    bg_rgba = background.convert("RGBA")
    out = Image.alpha_composite(bg_rgba, foreground)
    return out.convert("RGB")


# --- Heavy model helpers (lazy-loaded singletons) ---------------------------

# Module-level singletons: heavy models are loaded once on first use, kept for
# the orchestrator's lifetime, freed when the process exits. The orchestrator
# currently runs as a single short-lived subprocess per generation, so no
# explicit unload is needed.
_DEPTH_PIPE = None
_POSE_DETECTOR = None


def _get_depth_pipe(device: str = "cuda", dtype: str = "bfloat16"):
    global _DEPTH_PIPE
    if _DEPTH_PIPE is not None:
        return _DEPTH_PIPE
    try:
        import torch
        from diffusers import MarigoldDepthPipeline
    except ImportError as e:
        raise ImportError(
            "extract_depth requires diffusers + torch; install via the project venv"
        ) from e
    torch_dtype = torch.bfloat16 if dtype == "bfloat16" else torch.float16
    _DEPTH_PIPE = MarigoldDepthPipeline.from_pretrained(
        "prs-eth/marigold-depth-v1-1",
        torch_dtype=torch_dtype,
    ).to(device)
    return _DEPTH_PIPE


def extract_depth(
    image: Image.Image,
    device: str = "cuda",
    dtype: str = "bfloat16",
) -> Image.Image:
    """Compute a depth map via Marigold and return an 8-bit grayscale PIL.

    Closer = brighter. Output mode is 'RGB' (3-channel grayscale) so it can
    be passed directly to a ControlNet-Depth pipeline.
    """
    pipe = _get_depth_pipe(device=device, dtype=dtype)
    pipe_out = pipe(image)
    # Marigold returns a Tensor in [0, 1]; convert to 8-bit grayscale + 3-channel
    depth = pipe_out.prediction[0]   # (1, H, W) in [0, 1]
    if hasattr(depth, "cpu"):
        depth = depth.cpu().numpy()
    depth = depth.squeeze()
    depth_u8 = (depth * 255.0).clip(0, 255).astype(np.uint8)
    depth_rgb = np.stack([depth_u8] * 3, axis=-1)
    return Image.fromarray(depth_rgb, mode="RGB")


def _get_pose_detector():
    global _POSE_DETECTOR
    if _POSE_DETECTOR is not None:
        return _POSE_DETECTOR
    try:
        from controlnet_aux import OpenposeDetector
    except ImportError as e:
        raise ImportError(
            "extract_pose requires controlnet-aux; pip install controlnet-aux"
        ) from e
    _POSE_DETECTOR = OpenposeDetector.from_pretrained("lllyasviel/Annotators")
    return _POSE_DETECTOR


def extract_pose(image: Image.Image) -> Image.Image:
    """Extract a pose skeleton overlay via OpenPose.

    Returns a 3-channel RGB PIL image with the skeleton drawn on a black
    background -- the format ControlNet-Pose expects.
    """
    detector = _get_pose_detector()
    return detector(image)


# --- CLI dispatcher ----------------------------------------------------------


_TOOLS = {
    "canny":     "Run Canny edge detection (OpenCV)",
    "depth":     "Run Marigold depth estimation (loads ~2 GB model on first use)",
    "pose":      "Run OpenPose skeleton extraction (requires controlnet-aux)",
    "alpha-mask": "Convert RGBA alpha channel to binary mask",
    "bbox-mask": "Draw a white rect on a black canvas (--bbox X1,Y1,X2,Y2 --size W,H)",
    "composite": "Alpha-composite --fg over --bg",
}


def _parse_int_tuple(s: str, n: int) -> tuple[int, ...]:
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != n:
        raise argparse.ArgumentTypeError(f"expected {n} comma-separated ints, got {s!r}")
    return tuple(int(p) for p in parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-pipeline preprocessing helpers")
    parser.add_argument(
        "--tool", required=True, choices=list(_TOOLS),
        help="Which helper to run (" + " | ".join(_TOOLS) + ")",
    )
    parser.add_argument("--in", dest="in_path", default=None, help="Input image path")
    parser.add_argument("--out", default=None, help="Output image path (default: <tool>.png)")
    # canny tunables
    parser.add_argument("--canny-low", type=int, default=100)
    parser.add_argument("--canny-high", type=int, default=200)
    # alpha-mask tunable
    parser.add_argument("--alpha-threshold", type=int, default=128)
    # bbox-mask args
    parser.add_argument("--bbox", default=None, help="X1,Y1,X2,Y2 (bbox-mask only)")
    parser.add_argument("--size", default=None, help="W,H (bbox-mask only)")
    # composite args
    parser.add_argument("--fg", default=None, help="Foreground RGBA path (composite only)")
    parser.add_argument("--bg", default=None, help="Background RGB path (composite only)")
    # depth args
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float16"])
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else Path(f"{args.tool}.png")

    if args.tool == "canny":
        if not args.in_path:
            parser.error("--in is required for tool=canny")
        result = extract_canny(Image.open(args.in_path), args.canny_low, args.canny_high)

    elif args.tool == "depth":
        if not args.in_path:
            parser.error("--in is required for tool=depth")
        result = extract_depth(Image.open(args.in_path), device=args.device, dtype=args.dtype)

    elif args.tool == "pose":
        if not args.in_path:
            parser.error("--in is required for tool=pose")
        result = extract_pose(Image.open(args.in_path))

    elif args.tool == "alpha-mask":
        if not args.in_path:
            parser.error("--in is required for tool=alpha-mask")
        result = make_mask_from_alpha(Image.open(args.in_path), args.alpha_threshold)

    elif args.tool == "bbox-mask":
        if not args.bbox or not args.size:
            parser.error("tool=bbox-mask requires --bbox and --size")
        bbox = _parse_int_tuple(args.bbox, 4)
        size = _parse_int_tuple(args.size, 2)
        result = make_mask_from_bbox(bbox, size)

    elif args.tool == "composite":
        if not args.fg or not args.bg:
            parser.error("tool=composite requires --fg and --bg")
        result = composite_alpha(Image.open(args.fg), Image.open(args.bg))

    else:
        parser.error(f"unknown tool {args.tool!r}")
        return 2  # unreachable -- parser.error exits

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.save(out_path)
    print(f"[preprocess] {args.tool} -> {out_path} (mode={result.mode}, size={result.size})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
