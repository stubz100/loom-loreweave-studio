"""Stage 2 — Run Z-Image generation (t2i / img2img / inpaint).

Phase B.1 extends the original T2I-only stage to dispatch to the right diffusers
call signature based on `mode`:

- t2i:     plain text-to-image (no image input)
- img2img: text + reference image; `strength` controls how much the reference
           is preserved (0.2-0.4 = strong lock, 0.6 default = balanced).
- inpaint: text + reference image + mask; `strength` defaults to 1.0 = free
           repaint of masked region.

All three modes share the same per-call kwargs (prompt, negative_prompt,
cfg_normalization, cfg_truncation, generator, num_inference_steps,
guidance_scale). The mode-specific kwargs (`image`, `mask_image`, `strength`)
are added only when the mode requires them.
"""

from pathlib import Path

import torch
from diffusers import ZImageImg2ImgPipeline, ZImageInpaintPipeline, ZImagePipeline
from diffusers.utils import load_image
from PIL import Image


# Mode-specific defaults that match diffusers' own defaults but are surfaced
# here so the manifest records what the run actually used.
MODE_DEFAULTS = {
    "t2i":     {"strength": None},
    "img2img": {"strength": 0.6},
    "inpaint": {"strength": 1.0},
}


def _load_pil(path_or_image: str | Path | Image.Image) -> Image.Image:
    """Accept a path-string, Path, or PIL Image and return a PIL Image."""
    if isinstance(path_or_image, Image.Image):
        return path_or_image
    return load_image(str(path_or_image))


def run(
    pipe: ZImagePipeline | ZImageImg2ImgPipeline | ZImageInpaintPipeline,
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    seed: int = 42,
    num_inference_steps: int = 9,
    guidance_scale: float = 0.0,
    negative_prompt: str | None = None,
    cfg_normalization: bool | None = None,
    cfg_truncation: float | None = None,
    mode: str = "t2i",
    init_image: str | Path | Image.Image | None = None,
    mask_image: str | Path | Image.Image | None = None,
    strength: float | None = None,
) -> dict:
    """Run the full Z-Image generation pass.

    For img2img and inpaint, the input image is resized to (width, height) so
    width/height effectively control output resolution. The mask, if provided,
    is converted to single-channel `L` mode (white = inpaint, black = preserve).

    Returns dict with keys: image, seed, width, height, num_inference_steps,
        guidance_scale, prompt, negative_prompt, cfg_normalization,
        cfg_truncation, mode, strength, init_image_path, mask_image_path.
    """
    if width % 16 != 0 or height % 16 != 0:
        raise ValueError(f"width ({width}) and height ({height}) must be divisible by 16")

    if mode not in MODE_DEFAULTS:
        raise ValueError(f"unknown mode {mode!r}; must be one of {list(MODE_DEFAULTS)}")

    # Pipeline-class / mode consistency check -- catches the common mistake of
    # loading a t2i pipeline and then passing mode="img2img" through stage 2.
    if mode == "t2i" and not isinstance(pipe, ZImagePipeline):
        raise TypeError(f"mode=t2i requires ZImagePipeline; got {type(pipe).__name__}")
    if mode == "img2img" and not isinstance(pipe, ZImageImg2ImgPipeline):
        raise TypeError(f"mode=img2img requires ZImageImg2ImgPipeline; got {type(pipe).__name__}")
    if mode == "inpaint" and not isinstance(pipe, ZImageInpaintPipeline):
        raise TypeError(f"mode=inpaint requires ZImageInpaintPipeline; got {type(pipe).__name__}")

    # Resolve mode-required inputs
    init_image_path = str(init_image) if isinstance(init_image, (str, Path)) else None
    mask_image_path = str(mask_image) if isinstance(mask_image, (str, Path)) else None
    init_pil: Image.Image | None = None
    mask_pil: Image.Image | None = None

    if mode in ("img2img", "inpaint"):
        if init_image is None:
            raise ValueError(f"mode={mode} requires init_image")
        init_pil = _load_pil(init_image).convert("RGB").resize((width, height))

    if mode == "inpaint":
        if mask_image is None:
            raise ValueError("mode=inpaint requires mask_image")
        mask_pil = _load_pil(mask_image).convert("L").resize((width, height))

    # Strength: fall back to per-mode default when caller didn't override.
    if strength is None:
        strength = MODE_DEFAULTS[mode]["strength"]

    generator = torch.Generator(device="cpu").manual_seed(seed)

    call_kwargs: dict = {
        "prompt": prompt,
        "width": width,
        "height": height,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "generator": generator,
    }

    if negative_prompt:
        call_kwargs["negative_prompt"] = negative_prompt

    if cfg_normalization is not None:
        call_kwargs["cfg_normalization"] = cfg_normalization

    if cfg_truncation is not None:
        call_kwargs["cfg_truncation"] = cfg_truncation

    if mode in ("img2img", "inpaint"):
        call_kwargs["image"] = init_pil
        if strength is not None:
            call_kwargs["strength"] = strength

    if mode == "inpaint":
        call_kwargs["mask_image"] = mask_pil

    with torch.inference_mode():
        result = pipe(**call_kwargs)

    image: Image.Image = result.images[0]

    return {
        "image": image,
        "seed": seed,
        "width": image.width,
        "height": image.height,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "cfg_normalization": cfg_normalization,
        "cfg_truncation": cfg_truncation,
        "mode": mode,
        "strength": strength,
        "init_image_path": init_image_path,
        "mask_image_path": mask_image_path,
    }


def get_manifest_inputs(
    prompt: str,
    width: int,
    height: int,
    seed: int,
    num_inference_steps: int,
    guidance_scale: float,
    negative_prompt: str | None,
    cfg_normalization: bool | None,
    cfg_truncation: float | None = None,
    mode: str = "t2i",
    init_image: str | Path | None = None,
    mask_image: str | Path | None = None,
    strength: float | None = None,
) -> dict:
    return {
        "prompt": prompt,
        "width": width,
        "height": height,
        "seed": seed,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "negative_prompt": negative_prompt,
        "cfg_normalization": cfg_normalization,
        "cfg_truncation": cfg_truncation,
        "mode": mode,
        "init_image": str(init_image) if init_image is not None else None,
        "mask_image": str(mask_image) if mask_image is not None else None,
        "strength": strength,
    }


def get_manifest_outputs(result: dict) -> dict:
    return {
        "width": result["width"],
        "height": result["height"],
        "seed": result["seed"],
        "num_inference_steps": result["num_inference_steps"],
        "guidance_scale": result["guidance_scale"],
        "cfg_normalization": result["cfg_normalization"],
        "cfg_truncation": result["cfg_truncation"],
        "mode": result["mode"],
        "strength": result["strength"],
        "init_image_path": result["init_image_path"],
        "mask_image_path": result["mask_image_path"],
    }


def get_manifest_debug(result: dict) -> dict:
    img = result["image"]
    return {
        "image_mode": img.mode,
        "image_size": list(img.size),
    }
