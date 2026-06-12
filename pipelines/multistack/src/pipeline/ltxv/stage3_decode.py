"""Stage 3 — Isolated VAE decode (ROCm-safe).

LTXV's Video-VAE has 1:192 compression (32×32 spatial × 8 temporal) so the
latent tensor is small and the OOM risk profile is much lower than Wan2.2 /
Hunyuan / Mochi. We still use the two-phase tear-down pattern from
kb-wan2.md / kb-hunyuan.md for code-symmetry and to inherit the OOM-recovery
ladder for free if a 13B variant ever needs it.

  1. Caller has torn down the denoising pipeline before calling this stage.
  2. Reload the VAE standalone in BF16 (NOT FP32).
  3. Enable tiling + slicing (cheap insurance; rarely load-bearing for LTXV).
  4. Use torch.inference_mode() (prevents autograd graph allocation).
  5. PYTORCH_ALLOC_CONF=expandable_segments:True must be set (done in stage1).
"""

import gc
import time
from pathlib import Path

import torch


# Candidate VAE class names, tried in order. LTX-Video uses
# AutoencoderKLLTXVideo in current diffusers.
_VAE_CLASS_CANDIDATES = [
    "AutoencoderKLLTXVideo",
    "AutoencoderKLLTX",            # older naming
    "AutoencoderKL",               # last-resort generic
]


def _resolve_vae_class():
    """Resolve the first available VAE class by name."""
    import diffusers
    tried = []
    for name in _VAE_CLASS_CANDIDATES:
        cls = getattr(diffusers, name, None)
        if cls is not None:
            return cls, name
        tried.append(name)
        for submod_name in (
            "models.autoencoders", "pipelines.ltx", "pipelines.ltx_video",
        ):
            try:
                submod = __import__(f"diffusers.{submod_name}", fromlist=[name])
                cls = getattr(submod, name, None)
                if cls is not None:
                    return cls, name
            except ImportError:
                continue
    raise ImportError(
        f"None of these VAE classes were found: {tried}. "
        f"Installed diffusers version: {diffusers.__version__}. "
        f"LTX-Video 0.9.x needs diffusers with AutoencoderKLLTXVideo support."
    )


def _load_vae(model_id: str, vae_subfolder: str, torch_dtype, device: str,
              enable_spatial_tiling: bool = True):
    """Load the LTX-Video VAE standalone."""
    vae_cls, vae_class_name = _resolve_vae_class()
    vae = vae_cls.from_pretrained(
        model_id, subfolder=vae_subfolder, torch_dtype=torch_dtype,
    )
    if enable_spatial_tiling:
        vae.enable_tiling()
    else:
        if hasattr(vae, "disable_tiling"):
            vae.disable_tiling()
        else:
            vae.use_tiling = False
    try:
        vae.enable_slicing()
    except Exception:
        pass
    vae.to(device)
    vae.eval()
    vae._loaded_class_name = vae_class_name  # type: ignore[attr-defined]
    vae._spatial_tiling_enabled = enable_spatial_tiling  # type: ignore[attr-defined]
    return vae


def _flush_gpu():
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()


def _is_oom_error(exc: BaseException) -> bool:
    if isinstance(exc, torch.OutOfMemoryError):
        return True
    msg = str(exc).lower()
    return (
        "out of memory" in msg
        or "memory allocation" in msg
        or "hip error: out of memory" in msg
        or "cuda error: out of memory" in msg
    )


def run(
    latents: torch.Tensor | str | Path,
    model_id: str,
    vae_subfolder: str = "vae",
    vae_dtype: str = "bfloat16",
    device: str = "cuda",
    enable_spatial_tiling: bool = True,
    auto_retry_oom: bool = True,
    decode_timestep: float = 0.05,
    decode_noise_scale: float = 0.0,
) -> dict:
    """Decode latents to a frames tensor using an isolated VAE.

    Args:
        latents: Either an in-memory tensor or a path to a saved .latents.pt.
        model_id: HuggingFace model id (same as stage 1).
        vae_subfolder: Subfolder for the VAE within model_id.
        vae_dtype: "bfloat16" recommended.
        device: Target device.
        enable_spatial_tiling: Spatial tiling on the VAE. Default True.
        auto_retry_oom: If True, retry with spatial tiling forced on after OOM.
        decode_timestep: Timestep value passed as `temb` to the LTX VAE. Only
            used when `vae.config.timestep_conditioning` is True. Default 0.05
            matches the diffusers LTX pipeline default.
        decode_noise_scale: Fraction of unit-Gaussian noise mixed into latents
            before decode. **Default 0.0 (skip mixing)** — appropriate for
            distilled variants (e.g. `*_distilled`) which weren't trained to
            expect input noise at decode time. Set to 0.025 for base/dev
            variants where the noise mixing is part of the trained decode
            distribution. Symptom of "too much noise_scale": visible grain on
            every frame even though latents denoised cleanly.
    """
    import numpy as np

    torch_dtype = getattr(torch, vae_dtype)

    # 1. Ensure nothing else is on the GPU before VAE load.
    _flush_gpu()

    # 2. Load latents (from disk if path was given).
    if isinstance(latents, (str, Path)):
        latents = torch.load(latents, map_location="cpu", weights_only=True)

    # 3. Reload VAE standalone.
    vae = _load_vae(model_id, vae_subfolder, torch_dtype, device,
                    enable_spatial_tiling=enable_spatial_tiling)

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
        vram_after_vae_load = torch.cuda.memory_allocated() / 1024**3
    else:
        vram_after_vae_load = 0.0

    # 4. Apply VAE denormalization. LTX-Video uses a hybrid formula:
    #
    #     latents_denorm = latents * latents_std / scaling_factor + latents_mean
    #
    # CRITICAL (verified 2026-05-26 after a degraded decode):
    #   (a) Use the VAE's REGISTERED BUFFERS (`vae.latents_mean`, `vae.latents_std`)
    #       NOT the config dict values. The buffers are dtype-matched to the loaded
    #       VAE (BF16 in our case); the config dict is Python floats.
    #   (b) Do the denormalization on GPU, NOT on CPU. CPU BF16 arithmetic on the
    #       precise (small std) channels was producing subtly wrong latents → VAE
    #       output looked like "VHS tapes with lost color range."
    #
    # The two together — buffer-on-GPU vs config-to-tensor-on-CPU — were the
    # root cause of the bad decode. Mirroring diffusers' LTXImageToVideoPipeline.
    vae_config = dict(vae.config) if hasattr(vae, "config") else {}

    # Move latents to device + match VAE dtype BEFORE denormalization.
    latents = latents.to(device=device, dtype=torch_dtype)

    has_buffer_mean = hasattr(vae, "latents_mean") and vae.latents_mean is not None
    has_buffer_std = hasattr(vae, "latents_std") and vae.latents_std is not None
    has_scaling = "scaling_factor" in vae_config

    if has_buffer_mean and has_buffer_std and has_scaling:
        # LTX standard path — buffers + scaling_factor on GPU.
        scaling_factor = float(vae_config["scaling_factor"])
        latents_mean = vae.latents_mean.view(1, -1, 1, 1, 1).to(
            device=latents.device, dtype=latents.dtype
        )
        latents_std = vae.latents_std.view(1, -1, 1, 1, 1).to(
            device=latents.device, dtype=latents.dtype
        )
        latents = latents * latents_std / scaling_factor + latents_mean
    elif "latents_mean" in vae_config and "latents_std" in vae_config:
        # Fallback path: VAE didn't expose buffers (older diffusers?). Use config
        # values but still do the math on GPU.
        latent_channels = vae_config.get(
            "latent_channels", vae_config.get("z_dim", latents.shape[1])
        )
        latents_mean = (
            torch.tensor(vae_config["latents_mean"])
            .view(1, latent_channels, 1, 1, 1)
            .to(device=latents.device, dtype=latents.dtype)
        )
        latents_std = (
            torch.tensor(vae_config["latents_std"])
            .view(1, latent_channels, 1, 1, 1)
            .to(device=latents.device, dtype=latents.dtype)
        )
        if has_scaling:
            scaling_factor = float(vae_config["scaling_factor"])
            latents = latents * latents_std / scaling_factor + latents_mean
        else:
            # Wan-style: mean/std only, no scaling_factor.
            latents = latents / (1.0 / latents_std) + latents_mean
    elif has_scaling:
        # Mochi-style fallback: scaling_factor only, no per-channel stats.
        latents = latents / vae_config["scaling_factor"]

    # 5. LTX-specific: the VAE is timestep-conditioned. Without a temb tensor,
    # the decoder hits `None * timestep_scale_multiplier` and TypeErrors. If
    # the VAE config has timestep_conditioning=True (true for LTX 0.9.x), we
    # build a temb from `decode_timestep` and OPTIONALLY mix decode-time noise
    # into the latents proportional to `decode_noise_scale`.
    #
    # IMPORTANT (lesson learned 2026-05-26): the diffusers LTX pipeline default
    # decode_noise_scale=0.025 is for BASE/DEV models. Applying that to a
    # DISTILLED variant injects ~2.5% unit-Gaussian noise that the distilled
    # VAE wasn't trained to handle — produces visible grain on every frame
    # even though denoise was clean. We now default to decode_noise_scale=0.0
    # (skip noise mixing entirely), which is correct for distilled variants.
    # For base/dev variants, pass --vae-decode-noise-scale 0.025.
    timestep_conditioning = bool(vae_config.get("timestep_conditioning", False))
    decode_timestep_val = float(decode_timestep)
    decode_noise_scale_val = float(decode_noise_scale)
    apply_noise_mixing = timestep_conditioning and decode_noise_scale_val > 0.0
    if timestep_conditioning:
        if apply_noise_mixing:
            print(
                f"[ltxv/stage3] VAE timestep-conditioned: decode_timestep="
                f"{decode_timestep_val}, decode_noise_scale="
                f"{decode_noise_scale_val} (noise mixing ENABLED — for base/dev)"
            )
        else:
            print(
                f"[ltxv/stage3] VAE timestep-conditioned: decode_timestep="
                f"{decode_timestep_val}, noise mixing SKIPPED "
                f"(distilled-safe default; pass decode_noise_scale>0 for base/dev)"
            )

    print(
        f"[ltxv/stage3] Decoding latents shape={tuple(latents.shape)} | "
        f"spatial_tiling={'ON' if enable_spatial_tiling else 'OFF'}, dtype={vae_dtype}"
    )

    retry_attempts: list[dict] = []
    current_spatial_tiling = bool(enable_spatial_tiling)

    t0 = time.time()
    while True:
        try:
            # latents are already on GPU + dtype-cast from the denormalization
            # block above. Build decode-time temb (+ optional noise mixing) per
            # the LTX pipeline.
            decode_kwargs: dict = {"return_dict": False}
            if timestep_conditioning:
                # ALWAYS pass temb when VAE is timestep-conditioned (required to
                # avoid TypeError on None * timestep_scale_multiplier).
                if apply_noise_mixing:
                    noise = torch.randn(
                        latents.shape,
                        generator=torch.Generator(device=latents.device).manual_seed(
                            int(time.time() * 1000) & 0xFFFFFFFF
                        ),
                        device=latents.device, dtype=latents.dtype,
                    )
                    scale = torch.tensor(
                        [decode_noise_scale_val],
                        device=latents.device, dtype=latents.dtype,
                    ).view(latents.shape[0], 1, 1, 1, 1)
                    latents = (1.0 - scale) * latents + scale * noise
                timestep = torch.tensor(
                    [decode_timestep_val] * latents.shape[0],
                    device=latents.device, dtype=latents.dtype,
                )
                decode_kwargs["temb"] = timestep
            with torch.inference_mode():
                decoded = vae.decode(latents, **decode_kwargs)[0]
            # decoded: [B, C, T, H, W] in [-1, 1] typically
            decoded = decoded.clamp(-1, 1)
            decoded = (decoded + 1) / 2
            # [B, C, T, H, W] -> [T, H, W, C] on CPU
            video = decoded.squeeze(0).permute(1, 2, 3, 0).cpu().float().numpy()
            break
        except Exception as e:
            if not (auto_retry_oom and _is_oom_error(e)):
                raise

            failed = {
                "spatial_tiling": current_spatial_tiling,
                "error": str(e).splitlines()[0],
            }
            retry_attempts.append(failed)
            print(
                f"[ltxv/stage3] VAE decode OOM with spatial_tiling="
                f"{current_spatial_tiling}; retrying with safer settings",
                flush=True,
            )

            del vae
            _flush_gpu()

            if not current_spatial_tiling:
                current_spatial_tiling = True
            else:
                raise RuntimeError(
                    "LTXV VAE decode OOMs even with spatial tiling enabled. "
                    "Latent tensor is unexpectedly large — verify latents shape "
                    "and consider running with a smaller --num-frames."
                ) from e

            vae = _load_vae(
                model_id, vae_subfolder, torch_dtype, device,
                enable_spatial_tiling=current_spatial_tiling,
            )
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()

            print(
                f"[ltxv/stage3] Retry settings: spatial_tiling={current_spatial_tiling}",
                flush=True,
            )

    t1 = time.time()
    decode_s = round(t1 - t0, 2)

    vae_peak_vram_gb = (
        torch.cuda.max_memory_allocated() / 1024**3
        if torch.cuda.is_available() else 0.0
    )

    num_frames, height, width, _ = video.shape

    del latents, vae
    try:
        del decoded  # type: ignore[name-defined]
    except NameError:
        pass
    _flush_gpu()

    return {
        "frames": video,  # numpy [T, H, W, C] in [0, 1]
        "decode_s": decode_s,
        "vae_peak_vram_gb": round(vae_peak_vram_gb, 2),
        "vram_after_vae_load_gb": round(vram_after_vae_load, 2),
        "num_frames": int(num_frames),
        "height": int(height),
        "width": int(width),
        "vae_dtype": vae_dtype,
        "spatial_tiling_enabled": current_spatial_tiling,
        "vae_oom_retry_attempts": retry_attempts,
        "decode_timestep_used": decode_timestep_val if timestep_conditioning else None,
        "decode_noise_scale_used": decode_noise_scale_val if apply_noise_mixing else 0.0,
        "noise_mixing_applied": apply_noise_mixing,
    }


def get_manifest_inputs(
    latents_source: str,
    model_id: str,
    vae_subfolder: str,
    vae_dtype: str,
    device: str,
    decode_timestep: float = 0.05,
    decode_noise_scale: float = 0.0,
) -> dict:
    return {
        "latents_source": latents_source,  # "in_memory" or path
        "model_id": model_id,
        "vae_subfolder": vae_subfolder,
        "vae_dtype": vae_dtype,
        "device": device,
        "decode_timestep": decode_timestep,
        "decode_noise_scale": decode_noise_scale,
    }


def get_manifest_outputs(result: dict) -> dict:
    return {
        "decode_s": result["decode_s"],
        "num_frames": result["num_frames"],
        "height": result["height"],
        "width": result["width"],
        "vae_dtype": result["vae_dtype"],
        "spatial_tiling_enabled": result["spatial_tiling_enabled"],
        "vae_oom_retries": len(result["vae_oom_retry_attempts"]),
        "decode_timestep_used": result["decode_timestep_used"],
        "decode_noise_scale_used": result["decode_noise_scale_used"],
        "noise_mixing_applied": result["noise_mixing_applied"],
    }


def get_manifest_debug(result: dict) -> dict:
    return {
        "vae_peak_vram_gb": result["vae_peak_vram_gb"],
        "vram_after_vae_load_gb": result["vram_after_vae_load_gb"],
        "vae_oom_retry_attempts": result["vae_oom_retry_attempts"],
    }
