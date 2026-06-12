"""Stage 2 — Run denoising and produce latents.

Two-phase split (matches kb-wan2.md / kb-hunyuan.md): denoise here with
`output_type="latent"`, then stage 3 reloads the VAE standalone for an
isolated, ROCm-safe decode.

Latents are saved to disk as `<run>.latents.pt` so the user can iterate on
decode without re-running denoise (`--resume-latents`). LTXV's 8-step
distilled denoise is fast enough that this matters less than for Hunyuan
(15+ min) or Wan2.2 (60+ min), but the diagnostic value of "save latents
and inspect" carries over.

Pipeline-signature filtering: LTX pipeline classes have different __call__
signatures (T2V doesn't take `image`; I2V does; Condition takes a list of
conditions). We introspect the pipeline and only pass kwargs it accepts.

Phase 1+2 scope: t2v and i2v modes. Phase 3+ adds keyframes/extend/control.
"""

import inspect
import time
from pathlib import Path

import torch
from diffusers.utils import load_image


def _filter_kwargs_to_signature(pipe, kwargs: dict) -> tuple[dict, list[str]]:
    """Keep only kwargs the pipeline's __call__ actually accepts.

    Returns (filtered_kwargs, dropped_keys).
    """
    try:
        sig = inspect.signature(pipe.__call__)
    except (ValueError, TypeError):
        return kwargs, []
    accepted = set(sig.parameters.keys())
    has_var_kwargs = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if has_var_kwargs:
        return kwargs, []
    filtered = {k: v for k, v in kwargs.items() if k in accepted}
    dropped = [k for k in kwargs if k not in accepted]
    return filtered, dropped


# LTXV's Video-VAE has 8× temporal compression. Frame-count rule is 8N+1 for
# clean latent boundaries (vs 4N+1 for Wan2.2 / Hunyuan / Mochi). Verify on
# first build — if the pipeline auto-rounds internally, this is a no-op.
def _round_to_8n_plus_1(n: int) -> int:
    if (n - 1) % 8 == 0:
        return n
    return ((n - 1) // 8) * 8 + 1


def run(
    pipe,
    mode: str,
    prompt: str,
    init_image: str | None = None,
    height: int = 480,
    width: int = 704,
    num_frames: int = 121,
    num_inference_steps: int = 8,
    guidance_scale: float = 1.0,
    seed: int = 42,
    negative_prompt: str | None = None,
    image_cond_noise_scale: float = 0.0,
    latents_save_path: str | Path | None = None,
) -> dict:
    """Run the LTX-Video denoising pass and return latents.

    Args:
        pipe: Loaded pipeline from stage 1.
        mode: "t2v" or "i2v" (Phase 1+2).
        prompt: Text prompt describing the desired motion / scene.
        init_image: First-frame image path. Required when mode == "i2v".
        height, width: Output resolution. LTXV native 480×704; 768×512+ for 13B.
        num_frames: Number of frames; auto-rounded to 8N+1.
        num_inference_steps: 8 for distilled, 25-40 for dev.
        guidance_scale: 3.0 typical for distilled; 3.5-5.0 for dev.
        seed: Reproducibility seed.
        negative_prompt: Optional negative prompt. Helpful for distilled too
            (unlike Hunyuan distilled which fully bakes CFG into weights).
        latents_save_path: If set, save latents to this path after denoising.

    Returns dict with: latents (cpu tensor), latents_shape, denoise_s,
        steps_per_s, latents_path, per_step_times_s, vram_peak_gb, mode,
        prompt, init_image, height, width, num_frames, num_inference_steps,
        guidance_scale, seed, negative_prompt.
    """
    if mode not in ("t2v", "i2v"):
        raise ValueError(
            f"Phase 1+2 supports mode 't2v' or 'i2v' only (got {mode!r}). "
            f"keyframes/extend/control are Phase 3+ — see kb-ltx09.md."
        )
    if mode == "i2v" and not init_image:
        raise ValueError("mode='i2v' requires init_image")
    if mode == "t2v" and init_image:
        print("[ltxv/stage2] WARN: init_image ignored in t2v mode")
        init_image = None

    num_frames = _round_to_8n_plus_1(num_frames)

    generator = torch.Generator(device="cpu").manual_seed(seed)

    requested_kwargs: dict = {
        "prompt": prompt,
        "height": height,
        "width": width,
        "num_frames": num_frames,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "generator": generator,
        "output_type": "latent",
        # LTX-specific I2V kwarg — controls noise added to image conditioning.
        # Distilled examples in the diffusers docs use image_cond_noise_scale=0.0.
        # Non-zero values blur the init image's influence on the first frame.
        "image_cond_noise_scale": image_cond_noise_scale,
    }
    if negative_prompt is not None:
        requested_kwargs["negative_prompt"] = negative_prompt
    if mode == "i2v":
        # LTXImageToVideoPipeline takes `image` as a PIL Image.
        requested_kwargs["image"] = load_image(str(init_image))

    # Filter to what THIS variant actually accepts.
    call_kwargs, dropped = _filter_kwargs_to_signature(pipe, requested_kwargs)
    if dropped:
        print(
            f"[ltxv/stage2] Pipeline doesn't accept: {dropped} — dropping. "
            f"(Verify this is expected for {type(pipe).__name__}.)"
        )

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    # Per-step timing — wraps the pipeline's progress_bar so each tqdm tick
    # prints a timestamped line. Less load-bearing than for Hunyuan (no
    # AOTriton compile waits expected on LTXV), but the diagnostic carries
    # over essentially for free.
    per_step_times: list[float] = []
    last_tick_time = [time.time()]

    original_progress_bar = pipe.progress_bar

    def _timed_progress_bar(*pb_args, **pb_kwargs):
        bar = original_progress_bar(*pb_args, **pb_kwargs)
        original_update = bar.update

        def _update(n=1):
            now = time.time()
            delta = now - last_tick_time[0]
            last_tick_time[0] = now
            per_step_times.append(round(delta, 2))
            done = (bar.n or 0) + n
            total = bar.total or num_inference_steps
            print(
                f"[ltxv/stage2] step {done}/{total} done in {delta:.1f}s",
                flush=True,
            )
            return original_update(n)
        bar.update = _update
        return bar

    pipe.progress_bar = _timed_progress_bar

    t0 = time.time()
    try:
        output = pipe(**call_kwargs)
    finally:
        pipe.progress_bar = original_progress_bar
    t1 = time.time()
    denoise_s = round(t1 - t0, 2)

    # Diffusers video pipelines return latents under .frames when output_type="latent".
    latents = output.frames
    latents_shape_packed = tuple(latents.shape)

    # LTXV-specific: the transformer operates on PATCHIFIED latents in a flat
    # token sequence (B, num_tokens, channels). To decode, we have to unpack
    # them back into a 5D video latent (B, C, T_lat, H_lat, W_lat) using the
    # pipeline's own static helper. Wan2.2 / Hunyuan / Mochi don't need this
    # step because they return 5D directly when output_type="latent".
    if latents.dim() == 3:
        spatial_compression = getattr(pipe, "vae_spatial_compression_ratio", 32)
        temporal_compression = getattr(pipe, "vae_temporal_compression_ratio", 8)
        patch_size = getattr(pipe, "transformer_spatial_patch_size", 1)
        patch_size_t = getattr(pipe, "transformer_temporal_patch_size", 1)
        latent_num_frames = (num_frames - 1) // temporal_compression + 1
        latent_height = height // spatial_compression
        latent_width = width // spatial_compression
        print(
            f"[ltxv/stage2] Unpacking patchified latents {latents_shape_packed} → "
            f"5D (B=1, C={latents.shape[-1]}, T={latent_num_frames}, "
            f"H={latent_height}, W={latent_width})"
        )
        latents = pipe._unpack_latents(
            latents,
            latent_num_frames, latent_height, latent_width,
            patch_size, patch_size_t,
        )
    latents_shape = tuple(latents.shape)

    vram_peak_gb = 0.0
    if torch.cuda.is_available():
        vram_peak_gb = round(torch.cuda.max_memory_allocated() / 1024**3, 2)

    saved_path: str = ""
    if latents_save_path is not None:
        p = Path(latents_save_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        torch.save(latents.cpu(), p)
        saved_path = str(p)

    return {
        "latents": latents.cpu(),
        "latents_shape": latents_shape,
        "latents_path": saved_path,
        "denoise_s": denoise_s,
        "steps_per_s": round(num_inference_steps / max(denoise_s, 1e-6), 3),
        "per_step_times_s": per_step_times,
        "vram_peak_gb": vram_peak_gb,
        "mode": mode,
        "prompt": prompt,
        "init_image": str(init_image) if init_image else "",
        "height": height,
        "width": width,
        "num_frames": num_frames,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "seed": seed,
        "negative_prompt": negative_prompt or "",
        "image_cond_noise_scale": image_cond_noise_scale,
    }


def get_manifest_inputs(
    mode: str,
    prompt: str,
    init_image: str | None,
    height: int,
    width: int,
    num_frames: int,
    num_inference_steps: int,
    guidance_scale: float,
    seed: int,
    negative_prompt: str | None,
    image_cond_noise_scale: float = 0.0,
) -> dict:
    return {
        "mode": mode,
        "prompt": prompt,
        "init_image": init_image or "",
        "height": height,
        "width": width,
        "num_frames": num_frames,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "seed": seed,
        "negative_prompt": negative_prompt or "",
        "image_cond_noise_scale": image_cond_noise_scale,
    }


def get_manifest_outputs(result: dict) -> dict:
    return {
        "latents_shape": list(result["latents_shape"]),
        "latents_path": result["latents_path"],
        "denoise_s": result["denoise_s"],
        "steps_per_s": result["steps_per_s"],
        "num_frames_actual": result["num_frames"],
    }


def get_manifest_debug(result: dict) -> dict:
    return {
        "vram_peak_gb": result["vram_peak_gb"],
        "per_step_times_s": result["per_step_times_s"],
    }
