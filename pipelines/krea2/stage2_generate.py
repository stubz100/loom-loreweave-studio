"""Stage 2 - Run Krea 2 text-to-image generation."""

from __future__ import annotations

import time
from typing import Any


def run(
    pipe: Any,
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    seed: int = 42,
    num_inference_steps: int = 8,
    guidance_scale: float = 0.0,
    negative_prompt: str | None = None,
    max_sequence_length: int = 512,
) -> dict:
    """Generate one image through Krea2Pipeline."""
    if width % 16 != 0 or height % 16 != 0:
        raise ValueError(f"width ({width}) and height ({height}) must be divisible by 16")
    if max_sequence_length <= 0:
        raise ValueError("max_sequence_length must be positive")

    try:
        import torch
    except ImportError as exc:
        raise ImportError("Krea 2 generation requires PyTorch to be installed in the active runtime.") from exc

    generator = torch.Generator(device="cpu").manual_seed(seed)
    call_kwargs = {
        "prompt": prompt,
        "width": width,
        "height": height,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "generator": generator,
        "max_sequence_length": max_sequence_length,
    }
    if negative_prompt and guidance_scale > 0:
        call_kwargs["negative_prompt"] = negative_prompt

    _reset_peak_memory(torch)
    t0 = time.time()
    with torch.inference_mode():
        result = pipe(**call_kwargs)
    wall_time_seconds = round(time.time() - t0, 4)
    peak_vram_mb = _peak_vram_mb(torch)

    image = result.images[0]
    return {
        "image": image,
        "seed": seed,
        "width": image.width,
        "height": image.height,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "prompt": prompt,
        "negative_prompt": negative_prompt if guidance_scale > 0 else None,
        "max_sequence_length": max_sequence_length,
        "peak_vram_mb": peak_vram_mb,
        "wall_time_seconds": wall_time_seconds,
    }


def _reset_peak_memory(torch_module: Any) -> None:
    try:
        if torch_module.cuda.is_available():
            torch_module.cuda.reset_peak_memory_stats()
    except Exception:
        pass


def _peak_vram_mb(torch_module: Any) -> int | None:
    try:
        if torch_module.cuda.is_available():
            return int(torch_module.cuda.max_memory_allocated() / (1024 * 1024))
    except Exception:
        pass
    return None


def get_manifest_inputs(
    prompt: str,
    width: int,
    height: int,
    seed: int,
    num_inference_steps: int,
    guidance_scale: float,
    negative_prompt: str | None,
    max_sequence_length: int,
) -> dict:
    return {
        "prompt": prompt,
        "width": width,
        "height": height,
        "seed": seed,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "negative_prompt": negative_prompt,
        "max_sequence_length": max_sequence_length,
    }


def get_manifest_outputs(result: dict) -> dict:
    return {
        "width": result["width"],
        "height": result["height"],
        "seed": result["seed"],
        "num_inference_steps": result["num_inference_steps"],
        "guidance_scale": result["guidance_scale"],
        "negative_prompt": result["negative_prompt"],
        "max_sequence_length": result["max_sequence_length"],
        "peak_vram_mb": result["peak_vram_mb"],
        "wall_time_seconds": result["wall_time_seconds"],
    }


def get_manifest_debug(result: dict) -> dict:
    img = result["image"]
    return {
        "image_mode": img.mode,
        "image_size": list(img.size),
    }
