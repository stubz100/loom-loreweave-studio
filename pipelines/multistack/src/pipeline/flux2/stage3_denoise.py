"""Stage 3 — Create noise, compute schedule, and denoise latent.

Two entry points:
  * `run(...)`           -- t2i: pure noise → denoise from t=1.0 to t=0.0.
  * `run_img2img(...)`   -- img2img: AE-encode an init image, mix with noise
                            via flow-matching linear interpolation
                            `x = (1 - t_start) * z0 + t_start * noise` where
                            `t_start = strength`, then denoise across
                            `[t_start, 0]` on a schedule built natively for
                            that sub-range (`img2img_schedule`). Used by the
                            postprocess stack and HandRefiner's polish pass.
"""

import argparse
import math

import torch
from PIL import Image

from flux2.model import Flux2
from flux2.sampling import (
    batched_prc_img, compute_empirical_mu, default_prep, denoise, denoise_cfg,
    encode_image_refs, generalized_time_snr_shift, get_schedule,
)


def img2img_schedule(num_steps: int, image_seq_len: int, strength: float) -> list[float]:
    """The timestep schedule for an img2img run: `num_steps` intervals across
    `[strength, 0]`, spaced the way the model would traverse THAT PART of the trajectory.

    `get_schedule` maps a LINEAR ramp through `generalized_time_snr_shift`, whose
    resolution-dependent `mu` bunches timesteps up near t=1. Two earlier attempts both
    failed on that:

    * *slice the full 1→0 schedule below `strength`* — at 1024² (4096 tokens) almost every
      timestep sits above 0.6, so the tail held ONE interval no matter how many steps were
      asked for (`num_timesteps: 2`).
    * *scale the full schedule by `strength`* — keeps num_steps intervals, but also keeps the
      bunching, so the last interval swallowed most of the range: at strength 0.85/4 steps the
      schedule was `[0.85, 0.822, 0.772, 0.652, 0.0]` — three ~0.03-0.12 steps, then a single
      0.652 leap. That leap is why a "clean" pass returned its input: one Euler step to t=0 is
      `x_t - t·v`, and with the model's velocity ≈ the true `noise - z0` that evaluates to
      **exactly z0** — the source image. Re-interpretation lives in the accumulated curvature
      of several moderate steps; one big step short-circuits straight back to the source.
      (Author 2026-08-09: `job_4064f0f9` at 0.5 and `job_b5f67d87` at 0.85 both came back
      near-identical to their input even with a deliberately re-styled prompt.)

    The shift is invertible at sigma=1 — `s = e^mu / (e^mu + 1/t - 1)` gives
    `t = s / (s + e^mu·(1 - s))` — so map `strength` back to LINEAR time, ramp linearly from
    there to 0, and shift each point. `num_steps` means num_steps, the head is exactly
    `strength` (where the interpolation put the latent), the tail is exactly 0.0, and the
    spacing is the model's own. At strength 0.5/4 steps the largest:smallest interval ratio
    drops from 23.5 to 2.4.
    """
    if num_steps < 1:
        return [strength, 0.0]
    mu = compute_empirical_mu(image_seq_len, num_steps)
    u_start = strength / (strength + math.exp(mu) * (1.0 - strength))
    u = torch.linspace(u_start, 0.0, num_steps + 1)
    timesteps = generalized_time_snr_shift(u, mu, 1.0).tolist()
    # Pin the endpoints against float drift: the head must equal `strength` exactly (the
    # latent is mixed at that t) and the tail must reach a clean 0.0.
    timesteps[0], timesteps[-1] = float(strength), 0.0
    return timesteps


def _latent_stats(x: torch.Tensor) -> dict:
    x_float = x.detach().float()
    finite = torch.isfinite(x_float)
    total = x_float.numel()
    finite_count = int(finite.sum().item())
    stats = {
        "x_dtype": str(x.dtype),
        "x_device": str(x.device),
        "x_finite": finite_count == total,
        "x_finite_count": finite_count,
        "x_total_count": total,
        "x_finite_ratio": round(finite_count / total, 8) if total else 1.0,
    }
    if finite_count:
        finite_x = x_float[finite]
        stats.update({
            "x_min": float(finite_x.min()),
            "x_max": float(finite_x.max()),
            "x_mean": float(finite_x.mean()),
        })
    else:
        stats.update({"x_min": None, "x_max": None, "x_mean": None})
    return stats


def _ensure_finite_latents(x: torch.Tensor, context: str) -> None:
    stats = _latent_stats(x)
    if stats["x_finite"]:
        return
    raise FloatingPointError(
        f"{context} produced non-finite latents "
        f"({stats['x_finite_count']}/{stats['x_total_count']} finite, "
        f"ratio={stats['x_finite_ratio']}); refusing to decode a likely black image"
    )


def run(
    model: Flux2,
    ctx: torch.Tensor,
    ctx_ids: torch.Tensor,
    width: int = 1360,
    height: int = 768,
    seed: int | None = None,
    num_steps: int = 4,
    guidance: float = 1.0,
    guidance_distilled: bool = True,
    ae=None,
    ref_images: list[str] | None = None,
) -> dict:
    """Create noise, build timestep schedule, and run denoising loop.

    `ref_images` (loom multi-ref, §11): reference-image paths conditioned IN-CONTEXT —
    `encode_image_refs` AE-encodes them into reference tokens that ride alongside the noise
    tokens through the transformer (the native FLUX.2 pathway), carrying the subject/character
    into a NEW scene/pose. `ae` is then required. This is t2i CONDITIONED on the refs (not
    img2img — the refs are not the starting latent).

    Returns dict with keys: x, x_ids, seed, timesteps, noise_shape.
    """
    if seed is None:
        seed = torch.randint(0, 2**31, (1,)).item()

    noise_shape = (1, 128, height // 16, width // 16)

    with torch.no_grad():
        img_cond = img_cond_ids = None
        if ref_images:
            if ae is None:
                raise ValueError("ref_images requires the autoencoder (ae=)")
            refs = [Image.open(p).convert("RGB") for p in ref_images]
            img_cond, img_cond_ids = encode_image_refs(ae, refs)

        generator = torch.Generator(device="cuda").manual_seed(seed)
        noise = torch.randn(noise_shape, generator=generator, dtype=torch.bfloat16, device="cuda")

        x, x_ids = batched_prc_img(noise)
        timesteps = get_schedule(num_steps, x.shape[1])

        if guidance_distilled:
            x = denoise(
                model,
                x,
                x_ids,
                ctx,
                ctx_ids,
                timesteps=timesteps,
                guidance=guidance,
                img_cond_seq=img_cond,
                img_cond_seq_ids=img_cond_ids,
            )
        else:
            x = denoise_cfg(
                model,
                x,
                x_ids,
                ctx,
                ctx_ids,
                timesteps=timesteps,
                guidance=guidance,
                img_cond_seq=img_cond,
                img_cond_seq_ids=img_cond_ids,
            )

        _ensure_finite_latents(x, "denoise")

    return {
        "x": x,
        "x_ids": x_ids,
        "seed": seed,
        "timesteps": timesteps,
        "noise_shape": list(noise_shape),
        "width": width,
        "height": height,
        "num_steps": num_steps,
        "guidance": guidance,
        "ref_images": list(ref_images) if ref_images else [],
    }


def run_img2img(
    model: Flux2,
    ae,
    ctx: torch.Tensor,
    ctx_ids: torch.Tensor,
    init_image_path: str,
    width: int = 1360,
    height: int = 768,
    seed: int | None = None,
    num_steps: int = 4,
    guidance: float = 1.0,
    guidance_distilled: bool = True,
    strength: float = 0.25,
) -> dict:
    """Img2img variant of `run`. AE-encodes the init image, mixes with noise
    via flow-matching linear interpolation at t=strength, then runs the denoise
    loop across `[strength, 0]` on a schedule built for that sub-range.

    `strength` in (0, 1] controls how much of the original is preserved:
        0.20-0.25 = "polish" (small global re-roll, preserves composition)
        0.40-0.60 = noticeable re-interpretation
        0.80-1.0  = essentially t2i with init bias

    The init image is centre-cropped + resized to a multiple of 16 on both
    sides. The output dims are derived from the prepped image, not the
    `width`/`height` args, so the saved image keeps the source's aspect
    ratio. (`width`/`height` are kept in the signature for parity with
    `run` and recorded in the manifest.)

    Returns dict with the same keys as `run`, plus `init_image_path` and
    `strength`.
    """
    if seed is None:
        seed = torch.randint(0, 2**31, (1,)).item()
    if not (0.0 < strength <= 1.0):
        raise ValueError(f"strength must be in (0, 1], got {strength}")

    with torch.no_grad():
        # 1. Load + preprocess init image -> tensor in [-1, 1].
        img_pil = Image.open(init_image_path).convert("RGB")
        img_tensor = default_prep(img_pil, limit_pixels=None, ensure_multiple=16)
        # Encode in the AE's own dtype — the Comfy VAE is float32 and its conv stack
        # rejects a bf16 input (mirrors encode_image_refs, which feeds fp32 and only
        # casts the resulting tokens).
        img_tensor = img_tensor[None].to(device="cuda", dtype=next(ae.parameters()).dtype)
        prep_h, prep_w = img_tensor.shape[-2:]

        # 2. AE-encode -> latent at /16 spatial, 128 channels (matches noise); bf16 for
        #    the flow model (fp32 latents would trip its matmuls one call later).
        z0 = ae.encode(img_tensor).to(torch.bfloat16)
        if z0.dim() == 4 and z0.shape[0] == 1:
            pass
        elif z0.dim() == 3:
            z0 = z0[None]
        latent_shape = tuple(z0.shape)

        # 3. Build matching noise + flow-matching interpolation at t=strength.
        generator = torch.Generator(device="cuda").manual_seed(seed)
        noise = torch.randn(
            latent_shape, generator=generator, dtype=torch.bfloat16, device="cuda",
        )
        x_init = (1.0 - strength) * z0 + strength * noise

        # 4. Patch + ids.
        x, x_ids = batched_prc_img(x_init)

        # 5. Build the schedule natively across [strength, 0] — see `img2img_schedule`.
        timesteps = img2img_schedule(num_steps, x.shape[1], strength)

        if guidance_distilled:
            x = denoise(
                model, x, x_ids, ctx, ctx_ids,
                timesteps=timesteps, guidance=guidance,
            )
        else:
            x = denoise_cfg(
                model, x, x_ids, ctx, ctx_ids,
                timesteps=timesteps, guidance=guidance,
            )

        _ensure_finite_latents(x, "img2img denoise")

    return {
        "x": x,
        "x_ids": x_ids,
        "seed": seed,
        "timesteps": timesteps,
        "noise_shape": list(latent_shape),
        "width": prep_w,
        "height": prep_h,
        "num_steps": num_steps,
        "guidance": guidance,
        "init_image_path": str(init_image_path),
        "strength": strength,
    }


def get_manifest_inputs(width: int, height: int, seed: int, num_steps: int, guidance: float, guidance_distilled: bool) -> dict:
    return {
        "width": width,
        "height": height,
        "seed": seed,
        "num_steps": num_steps,
        "guidance": guidance,
        "guidance_distilled": guidance_distilled,
    }


def get_manifest_outputs(result: dict) -> dict:
    return {
        "x_shape": list(result["x"].shape),
        "x_ids_shape": list(result["x_ids"].shape),
        "noise_shape": result["noise_shape"],
        "seed": result["seed"],
        "num_timesteps": len(result["timesteps"]),
        "timesteps_first": result["timesteps"][0] if result["timesteps"] else None,
        "timesteps_last": result["timesteps"][-1] if result["timesteps"] else None,
    }


def get_manifest_debug(result: dict) -> dict:
    x = result["x"]
    debug = _latent_stats(x)
    debug["timesteps"] = result["timesteps"]
    return debug


def main():
    parser = argparse.ArgumentParser(description="Stage 3: Denoise")
    parser.add_argument("--model-name", default="flux.2-klein-4b")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--width", type=int, default=1360)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--num-steps", type=int, default=4)
    parser.add_argument("--guidance", type=float, default=1.0)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()

    from flux2.util import FLUX2_MODEL_INFO, load_ae, load_flow_model, load_text_encoder
    from flux2.sampling import batched_prc_txt

    model_info = FLUX2_MODEL_INFO[args.model_name]
    torch_device = torch.device(args.device)

    text_encoder = load_text_encoder(args.model_name, device=torch_device)
    text_encoder.eval()
    model = load_flow_model(args.model_name, device=torch_device)
    model.eval()

    guidance_distilled = model_info.get("guidance_distilled", True)
    with torch.no_grad():
        if guidance_distilled:
            ctx = text_encoder([args.prompt]).to(torch.bfloat16)
        else:
            ctx = torch.cat([text_encoder([""]), text_encoder([args.prompt])], dim=0).to(torch.bfloat16)
        ctx, ctx_ids = batched_prc_txt(ctx)

    result = run(
        model=model,
        ctx=ctx,
        ctx_ids=ctx_ids,
        width=args.width,
        height=args.height,
        seed=args.seed,
        num_steps=args.num_steps,
        guidance=args.guidance,
        guidance_distilled=guidance_distilled,
    )

    print(f"Denoised x shape: {result['x'].shape}")
    print(f"Seed: {result['seed']}")
    print(f"Timesteps: {len(result['timesteps'])} steps")
    print("Stage 3 complete.")


if __name__ == "__main__":
    main()
