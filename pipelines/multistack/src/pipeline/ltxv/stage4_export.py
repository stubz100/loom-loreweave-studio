"""Stage 4 — Encode decoded frames to MP4.

CPU-only (ffmpeg via diffusers.utils.export_to_video). Accepts the numpy
[T, H, W, C] float-in-[0,1] frames tensor produced by stage 3.

This is stage 4 (not 5) in LTXV's numbering because LTXV has no SR cascade
stage; the export step follows directly after decode.
"""

from pathlib import Path

import numpy as np
from PIL import Image


def _to_pil_frames(video_np):
    """[T, H, W, C] float in [0, 1] → list[PIL.Image]."""
    pil_frames = []
    for frame in video_np:
        arr = (frame * 255).clip(0, 255).astype(np.uint8)
        pil_frames.append(Image.fromarray(arr))
    return pil_frames


def run(
    frames,  # numpy [T, H, W, C] in [0, 1]
    output_path: str | Path,
    fps: int = 24,
) -> dict:
    """Write frames out as an MP4 at the given fps.

    Returns dict with: output_path, fps, num_frames, height, width, file_size_bytes.
    """
    from diffusers.utils import export_to_video

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pil_frames = _to_pil_frames(frames)
    export_to_video(pil_frames, str(output_path), fps=fps)

    num_frames, height, width, _ = frames.shape
    return {
        "output_path": str(output_path),
        "fps": fps,
        "num_frames": int(num_frames),
        "height": int(height),
        "width": int(width),
        "file_size_bytes": output_path.stat().st_size,
    }


def get_manifest_inputs(output_path: str, fps: int, num_frames: int) -> dict:
    return {
        "output_path": output_path,
        "fps": fps,
        "num_frames": num_frames,
    }


def get_manifest_outputs(result: dict) -> dict:
    return {
        "output_path": result["output_path"],
        "fps": result["fps"],
        "num_frames": result["num_frames"],
        "height": result["height"],
        "width": result["width"],
        "file_size_bytes": result["file_size_bytes"],
    }


def get_manifest_debug(result: dict) -> dict:
    return {"container": "mp4"}
