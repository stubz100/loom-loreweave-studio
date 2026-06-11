"""Stage 2 — Run SD 3.5 denoising (text encoding + denoising + VAE decode in one call).

Quality-relevant defaults (validated against Stability AI model card +
community research as of 2026-04-25):

- num_inference_steps: medium=40, large=28, turbo=4 (model card)
- guidance_scale:      medium=4.5, large=3.5, turbo=0.0 (model card)
- max_sequence_length: 512 by default here (was 256 -- silently truncated
  long descriptive prompts that SD 3.5 specifically benefits from via T5)
- skip_guidance_layers: [7, 8, 9] for medium AND large with scale=2.8,
  start=0.01, stop=0.2 -- the official Stability AI anatomy / composition fix
- prompt_3:            optional, sends a separate (longer) prompt to T5 while
  CLIP-L / CLIP-G keep a short style anchor. Recommended pattern for
  prompts > ~50 tokens.
- negative_prompt:     None by default. SD 3.5 was not trained on negatives
  the way SD 1.5 / SDXL were; per Replicate + HF guidance, "less is better"
  -- prefer "" or short comma-separated lists over heavy SDXL-style ones.
"""

import torch
from diffusers import StableDiffusion3Pipeline
from PIL import Image


# Stability AI defaults for Skip Layer Guidance, surfaced here so the run
# manifest records the exact values the model was sampled with.
SLG_DEFAULTS = {
    "scale": 2.8,
    "start": 0.01,
    "stop":  0.2,
}


def _load_image(p: str | None, target_size: tuple[int, int] | None = None):
    """Load an image as PIL RGB, optionally resizing to (W, H) with LANCZOS."""
    if p is None:
        return None
    img = Image.open(p).convert("RGB")
    if target_size is not None and img.size != target_size:
        img = img.resize(target_size, Image.LANCZOS)
    return img


def _load_mask(p: str | None, target_size: tuple[int, int] | None = None):
    """Load a single-channel mask (white=repaint, black=preserve)."""
    if p is None:
        return None
    img = Image.open(p).convert("L")
    if target_size is not None and img.size != target_size:
        img = img.resize(target_size, Image.NEAREST)
    return img


def run(
    pipe: StableDiffusion3Pipeline,
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    seed: int = 42,
    num_inference_steps: int = 40,
    guidance_scale: float = 4.5,
    negative_prompt: str | None = None,
    prompt_3: str | None = None,
    negative_prompt_3: str | None = None,
    max_sequence_length: int = 512,
    skip_guidance_layers: list[int] | None = None,
    skip_layer_guidance_scale: float = SLG_DEFAULTS["scale"],
    skip_layer_guidance_start: float = SLG_DEFAULTS["start"],
    skip_layer_guidance_stop:  float = SLG_DEFAULTS["stop"],
    # --- inpaint / cn-inpaint additions ---
    mode: str = "t2i",
    init_image: str | None = None,
    mask_image: str | None = None,
    control_image: str | None = None,
    control_images: list[str] | None = None,
    strength: float = 1.0,
    controlnet_conditioning_scale: float | list[float] = 1.0,
) -> dict:
    """Run the full SD 3.5 generation (encode + denoise + decode).

    The diffusers StableDiffusion3Pipeline handles text encoding, denoising,
    and VAE decode internally in a single __call__.

    Returns dict with keys: image, seed, width, height, num_inference_steps,
        guidance_scale, prompt, negative_prompt, prompt_3, negative_prompt_3,
        max_sequence_length, skip_guidance_layers, skip_layer_guidance_scale,
        skip_layer_guidance_start, skip_layer_guidance_stop.
    """
    # NOTE: torch.Generator(device="cpu") works on all backends and matches
    # the device-agnostic example in the diffusers docs. Using the GPU device
    # would shift the RNG stream slightly between CUDA / ROCm runs.
    generator = torch.Generator(device="cpu").manual_seed(seed)

    call_kwargs: dict = {
        "prompt": prompt,
        "width": width,
        "height": height,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "generator": generator,
        "max_sequence_length": max_sequence_length,
    }

    if negative_prompt:
        call_kwargs["negative_prompt"] = negative_prompt

    # prompt_3 routes a separate (typically longer, prose-style) prompt to
    # the T5-XXL encoder, leaving CLIP-L/G with a short style anchor. Useful
    # for prompts > ~50 tokens that would otherwise get truncated by CLIP.
    if prompt_3:
        call_kwargs["prompt_3"] = prompt_3
    if negative_prompt_3:
        call_kwargs["negative_prompt_3"] = negative_prompt_3

    # Skip Layer Guidance is only on the base StableDiffusion3Pipeline call sig.
    # The ControlNet-inpainting variant inherits from a different base and
    # rejects these kwargs with TypeError. Probe the actual __call__ signature
    # before adding them.
    if skip_guidance_layers is not None:
        import inspect
        try:
            pipe_call_params = inspect.signature(pipe.__call__).parameters
        except (TypeError, ValueError):
            pipe_call_params = {}
        if "skip_guidance_layers" in pipe_call_params:
            call_kwargs["skip_guidance_layers"] = skip_guidance_layers
            call_kwargs["skip_layer_guidance_scale"] = skip_layer_guidance_scale
            call_kwargs["skip_layer_guidance_start"] = skip_layer_guidance_start
            call_kwargs["skip_layer_guidance_stop"]  = skip_layer_guidance_stop

    # --- img2img / inpaint / cn-inpaint extras: load + resize images, add to call_kwargs ---
    if mode == "img2img":
        if init_image is None:
            raise ValueError(f"mode={mode} requires --init-image")
        target = (width, height)
        call_kwargs["image"]    = _load_image(init_image, target)
        call_kwargs["strength"] = strength
    elif mode == "inpaint":
        if init_image is None:
            raise ValueError(f"mode={mode} requires --init-image")
        if mask_image is None:
            raise ValueError(f"mode={mode} requires --mask-image")
        target = (width, height)
        call_kwargs["image"]      = _load_image(init_image, target)
        call_kwargs["mask_image"] = _load_mask(mask_image, target)
        call_kwargs["strength"]   = strength
    elif mode == "cn-inpaint":
        # StableDiffusion3ControlNetPipeline is t2i+CN -- it has no `image` /
        # `mask_image` / `strength`. The caller composites the output onto the
        # base image by the mask in postproc stage 6.
        if control_image is None:
            raise ValueError("mode=cn-inpaint requires --control-image (e.g. depth.png)")
        target = (width, height)
        call_kwargs["control_image"] = _load_image(control_image, target)
        call_kwargs["controlnet_conditioning_scale"] = controlnet_conditioning_scale
    elif mode == "cn-inpaint-mc":
        # Multi-ControlNet inpaint. The pipeline class is our
        # SD3MultiControlNetInpaintPipeline -- expects:
        #   * control_image: LIST of N images (N = number of CNs)
        #   * control_mask:  single mask shared across CNs
        #   * controlnet_conditioning_scale: list[float] of length N
        # The first CN MUST be an inpaint CN (alimama). It gets the original
        # init_image as its control_image (the alimama prep VAE-encodes it
        # with the masked region zeroed and adds the mask channel). Other
        # CNs (depth, etc.) get their own conditioning images.
        if init_image is None:
            raise ValueError("mode=cn-inpaint-mc requires --init-image (the original scene)")
        if mask_image is None:
            raise ValueError("mode=cn-inpaint-mc requires --mask-image")
        if not control_images:
            raise ValueError(
                "mode=cn-inpaint-mc requires --control-images (comma-separated paths). "
                "Convention: position 0 = init_image (alimama inpaint-CN sees image+mask), "
                "position 1+ = conditioning images for the extra CNs (e.g. depth.png)."
            )
        target = (width, height)
        call_kwargs["control_image"] = [_load_image(p, target) for p in control_images]
        call_kwargs["control_mask"] = _load_mask(mask_image, target)
        # Scale must be a list matching the number of CNs.
        if isinstance(controlnet_conditioning_scale, (int, float)):
            scales = [float(controlnet_conditioning_scale)] * len(control_images)
        else:
            scales = list(controlnet_conditioning_scale)
            if len(scales) != len(control_images):
                raise ValueError(
                    f"controlnet_conditioning_scale list length ({len(scales)}) must match "
                    f"control_images length ({len(control_images)})"
                )
        call_kwargs["controlnet_conditioning_scale"] = scales

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
        "prompt_3": prompt_3,
        "negative_prompt_3": negative_prompt_3,
        "max_sequence_length": max_sequence_length,
        "skip_guidance_layers": skip_guidance_layers,
        "skip_layer_guidance_scale": skip_layer_guidance_scale if skip_guidance_layers else None,
        "skip_layer_guidance_start": skip_layer_guidance_start if skip_guidance_layers else None,
        "skip_layer_guidance_stop":  skip_layer_guidance_stop  if skip_guidance_layers else None,
        # mode-specific echoes for the manifest
        "mode": mode,
        "init_image": init_image,
        "mask_image": mask_image,
        "control_image": control_image,
        "control_images": control_images,
        "strength": strength if mode != "t2i" else None,
        "controlnet_conditioning_scale": (
            controlnet_conditioning_scale if mode in ("cn-inpaint", "cn-inpaint-mc") else None
        ),
    }


def get_manifest_inputs(
    prompt: str,
    width: int,
    height: int,
    seed: int,
    num_inference_steps: int,
    guidance_scale: float,
    negative_prompt: str | None,
    max_sequence_length: int,
    prompt_3: str | None = None,
    negative_prompt_3: str | None = None,
    skip_guidance_layers: list[int] | None = None,
    skip_layer_guidance_scale: float | None = None,
    skip_layer_guidance_start: float | None = None,
    skip_layer_guidance_stop:  float | None = None,
    mode: str = "t2i",
    init_image: str | None = None,
    mask_image: str | None = None,
    control_image: str | None = None,
    control_images: list[str] | None = None,
    strength: float | None = None,
    controlnet_conditioning_scale: float | list[float] | None = None,
) -> dict:
    return {
        "prompt": prompt,
        "width": width,
        "height": height,
        "seed": seed,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "negative_prompt": negative_prompt,
        "prompt_3": prompt_3,
        "negative_prompt_3": negative_prompt_3,
        "max_sequence_length": max_sequence_length,
        "skip_guidance_layers": skip_guidance_layers,
        "skip_layer_guidance_scale": skip_layer_guidance_scale,
        "skip_layer_guidance_start": skip_layer_guidance_start,
        "skip_layer_guidance_stop":  skip_layer_guidance_stop,
        "mode": mode,
        "init_image": init_image,
        "mask_image": mask_image,
        "control_image": control_image,
        "control_images": control_images,
        "strength": strength,
        "controlnet_conditioning_scale": controlnet_conditioning_scale,
    }


def get_manifest_outputs(result: dict) -> dict:
    return {
        "width": result["width"],
        "height": result["height"],
        "seed": result["seed"],
        "num_inference_steps": result["num_inference_steps"],
        "guidance_scale": result["guidance_scale"],
        "max_sequence_length": result["max_sequence_length"],
        "skip_guidance_layers": result["skip_guidance_layers"],
        "skip_layer_guidance_scale": result["skip_layer_guidance_scale"],
        "skip_layer_guidance_start": result["skip_layer_guidance_start"],
        "skip_layer_guidance_stop":  result["skip_layer_guidance_stop"],
        "mode": result.get("mode", "t2i"),
        "strength": result.get("strength"),
        "controlnet_conditioning_scale": result.get("controlnet_conditioning_scale"),
    }


def get_manifest_debug(result: dict) -> dict:
    img = result["image"]
    return {
        "image_mode": img.mode,
        "image_size": list(img.size),
    }
