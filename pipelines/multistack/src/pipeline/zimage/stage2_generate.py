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


def _decode_latents_on_cpu(pipe, latents) -> list[Image.Image]:
    """Decode a latent batch on CPU without Accelerate moving the VAE back to CUDA.

    ``enable_model_cpu_offload`` installs forward hooks on the VAE. Calling
    ``vae.decode`` while those hooks are present silently moves it back to the
    execution device, defeating an explicit CPU decode. Remove the hooks for the
    decode, then restore the pipeline's previous offload/resident state so batch
    and warm workers remain reusable.
    """
    vae = pipe.vae
    had_offload_hooks = bool(getattr(pipe, "_all_hooks", None))
    try:
        original_device = next(vae.parameters()).device
    except (AttributeError, StopIteration):
        original_device = None

    was_tiling = bool(getattr(vae, "use_tiling", False))
    tile_sample_min_size = getattr(vae, "tile_sample_min_size", None)
    tile_latent_min_size = getattr(vae, "tile_latent_min_size", None)

    print("[zimage-vae] CPU decode enabled: moving latent + VAE to system RAM", flush=True)
    latents = latents.detach().to("cpu")
    if hasattr(pipe, "remove_all_hooks"):
        pipe.remove_all_hooks()
    vae.to("cpu")

    # GPU tiling is useful as a MIOpen workaround, but on CPU it turns one decode
    # into nine overlapping decodes. The full-frame CPU path is both safe and faster.
    if was_tiling and hasattr(vae, "disable_tiling"):
        vae.disable_tiling()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    try:
        latents = latents.to(dtype=vae.dtype)
        shift_factor = getattr(vae.config, "shift_factor", 0.0)
        if shift_factor is None:
            shift_factor = 0.0
        latents = (latents / vae.config.scaling_factor) + shift_factor
        decoded = vae.decode(latents, return_dict=False)[0]
        return pipe.image_processor.postprocess(decoded, output_type="pil")
    finally:
        if was_tiling:
            vae.enable_tiling()
            if tile_sample_min_size is not None:
                vae.tile_sample_min_size = tile_sample_min_size
            if tile_latent_min_size is not None:
                vae.tile_latent_min_size = tile_latent_min_size

        if had_offload_hooks:
            pipe.enable_model_cpu_offload(device=getattr(pipe, "_offload_device", None))
        elif original_device is not None and getattr(original_device, "type", str(original_device)) != "cpu":
            vae.to(original_device)


def _decode_latents_on_gpu_freed(pipe, latents, device) -> list[Image.Image]:
    """Decode on the GPU after returning the caching-allocator reserve to the driver.

    Root cause of the zimage-base "15-minute decode" on Windows ROCm: the Z-Image (FLUX) VAE
    decoder is convolution-heavy, and MIOpen allocates its conv workspace with a *separate*
    ``hipMalloc``. Under ``enable_model_cpu_offload`` the transformer/text-encoder are CPU-side
    after denoise, but their ~12 GB stays in PyTorch's caching reserve, so the driver has only
    ~1 GB free at decode time → ``hipMalloc`` fails → MIOpen falls back to ``ConvDirectNaiveConvFwd``
    → ~900 s (measured: free_vram_at_decode≈1.2 GB ↔ ~900 s; ~15 GB free ↔ ~2 s). ``MIOPEN_FIND_MODE=2``
    can't help with no workspace to allocate. So: drop the offload hooks (else the hook bounces the
    VAE), put the small VAE on the GPU, ``empty_cache()`` to hand the transformer's reserve back to
    the driver (~14 GB free), then decode — MIOpen now gets its workspace and the fast solver.
    Pipeline state is restored afterward so batch + warm workers stay reusable.

    Note: this frees the reserve, which only helps when the heavy modules are already off-GPU
    (the offload default). In explicit resident mode they stay on the card, so the reserve can't be
    freed — resident + this path is not the fast combination; offload + this path is.
    """
    vae = pipe.vae
    had_offload_hooks = bool(getattr(pipe, "_all_hooks", None))
    try:
        original_device = next(vae.parameters()).device
    except (AttributeError, StopIteration):
        original_device = None

    if hasattr(pipe, "remove_all_hooks"):
        pipe.remove_all_hooks()
    vae.to(device)
    _free = -1.0
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            _free = torch.cuda.mem_get_info()[0] / (1024 ** 3)
    except Exception:
        pass
    print(f"[zimage-vae] GPU decode after freeing reserve: free_vram={_free:.2f}GB", flush=True)

    try:
        latents = latents.to(device=device, dtype=vae.dtype)
        shift_factor = getattr(vae.config, "shift_factor", 0.0)
        if shift_factor is None:
            shift_factor = 0.0
        latents = (latents / vae.config.scaling_factor) + shift_factor
        decoded = vae.decode(latents, return_dict=False)[0]
        return pipe.image_processor.postprocess(decoded, output_type="pil")
    finally:
        if had_offload_hooks:
            pipe.enable_model_cpu_offload(device=getattr(pipe, "_offload_device", None))
        elif original_device is not None and getattr(original_device, "type", str(original_device)) != "cpu":
            vae.to(original_device)


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
    cpu_vae: bool = False,
) -> dict:
    """Run the full Z-Image generation pass.

    For img2img and inpaint, the input image is resized to (width, height) so
    width/height effectively control output resolution. The mask, if provided,
    is converted to single-channel `L` mode (white = inpaint, black = preserve).

    Returns dict with keys: image, seed, width, height, num_inference_steps,
        guidance_scale, prompt, negative_prompt, cfg_normalization,
        cfg_truncation, mode, strength, cpu_vae, init_image_path, mask_image_path.
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

    # Always stop Diffusers after denoising so WE control the VAE decode's memory state.
    # The decode is the convolution-heavy phase that MIOpen handles badly on Windows ROCm when
    # the card is nearly full (probe: free_vram_at_decode≈1.2GB → ~900s). Under
    # enable_model_cpu_offload the transformer/text-encoder are CPU-side after denoise but their
    # VRAM stays in the caching-allocator reserve; decoding inside the pipeline never frees it.
    # Stopping here lets us empty that reserve before vae.decode (see _decode_latents_on_gpu_freed
    # / _decode_latents_on_cpu). The pipeline's own non-latent path would just decode immediately
    # into that starved state.
    call_kwargs["output_type"] = "latent"

    # --- Diagnostic probe (zimage-base 17-min investigation): split the pipe call into
    # text-encode+setup (before the first denoise step), the denoise loop, and VAE decode+post
    # (after the last step) via a step-end callback. `[zimage-probe]` prints to the worker log so a
    # single run reveals which phase dominates. Defensive: if the pipeline rejects the callback kwarg
    # we retry without it (timing then only covers the whole call).
    import time as _ptime
    _pb = {"start": _ptime.perf_counter(), "first": None, "last": None, "lat": None, "free": None}

    def _probe_cb(_pipe, _step, _ts, _cbk):
        _t = _ptime.perf_counter()
        if _pb["first"] is None:
            _pb["first"] = _t
        _pb["last"] = _t
        # Capture the LAST denoise latent + the free VRAM at that moment (just before decode).
        # This isolates why the in-app decode is slow when the standalone VAE-only decode is fast:
        #   - free_vram tiny  -> MIOpen's conv workspace is starved (offload/memory pressure)
        #   - lat absmax huge / nonfinite>0 -> the real base latent hits a slow/overflow conv path
        #     (would explain turbo's same-VAE decode being ~14x faster than base's).
        if isinstance(_cbk, dict) and _cbk.get("latents") is not None:
            _pb["lat"] = _cbk["latents"]
        try:
            if torch.cuda.is_available():
                _pb["free"] = torch.cuda.mem_get_info()[0]
        except Exception:
            pass
        return _cbk

    call_kwargs.setdefault("callback_on_step_end", _probe_cb)
    with torch.inference_mode():
        try:
            result = pipe(**call_kwargs)
        except TypeError as _e:
            if "callback_on_step_end" not in str(_e):
                raise
            call_kwargs.pop("callback_on_step_end", None)
            result = pipe(**call_kwargs)

        latents = result.images
        result = None
        if cpu_vae:
            # Explicit CPU fallback (opt-in). Slower, but sidesteps MIOpen entirely.
            images = _decode_latents_on_cpu(pipe, latents)
        else:
            # Default: free the GPU reserve, then decode on the now-empty card so MIOpen
            # gets its conv workspace (the actual fix for the ~15-min decode).
            _dev = "cuda" if torch.cuda.is_available() else "cpu"
            images = _decode_latents_on_gpu_freed(pipe, latents, _dev)
        image: Image.Image = images[0]
    _end = _ptime.perf_counter()
    # Echo the two things that decide VAE-decode speed on Windows ROCm so a single completed
    # job is self-diagnosing: MIOPEN_FIND_MODE (must be "2" for the fast conv solver — see
    # kb-zimage.md) and the VAE device (must be cuda for the resident GPU path). If decode+post
    # is still ~minutes with find_mode=2 + vae on cuda, the env var isn't reaching the worker.
    import os as _os
    _vae = getattr(pipe, "vae", None)
    _extra = ""
    _lat = _pb.get("lat")
    if _lat is not None:
        try:
            _lf = _lat.detach().float()
            _nf = int((~torch.isfinite(_lf)).sum().item())
            _extra += (f" lat[absmax={_lf.abs().max().item():.2e} mean={_lf.mean().item():.2e} "
                       f"std={_lf.std().item():.2e} nonfinite={_nf}]")
        except Exception:
            pass
    if _pb.get("free") is not None:
        _extra += f" free_vram_at_decode={_pb['free'] / (1024 ** 3):.2f}GB"
    _diag = (f"MIOPEN_FIND_MODE={_os.environ.get('MIOPEN_FIND_MODE', '(unset)')} "
             f"vae_device={getattr(_vae, 'device', '?')}{_extra}")
    if _pb["first"] is not None:
        print(f"[zimage-probe] encode+setup={_pb['first'] - _pb['start']:.1f}s "
              f"denoise={_pb['last'] - _pb['first']:.1f}s decode+post={_end - _pb['last']:.1f}s "
              f"total={_end - _pb['start']:.1f}s | steps={num_inference_steps} {width}x{height} "
              f"mode={mode} | {_diag}", flush=True)
    else:
        print(f"[zimage-probe] total={_end - _pb['start']:.1f}s (no step callback) | "
              f"steps={num_inference_steps} {width}x{height} mode={mode} | {_diag}", flush=True)

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
        "cpu_vae": cpu_vae,
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
    cpu_vae: bool = False,
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
        "cpu_vae": cpu_vae,
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
        "cpu_vae": result["cpu_vae"],
        "init_image_path": result["init_image_path"],
        "mask_image_path": result["mask_image_path"],
    }


def get_manifest_debug(result: dict) -> dict:
    img = result["image"]
    return {
        "image_mode": img.mode,
        "image_size": list(img.size),
    }
