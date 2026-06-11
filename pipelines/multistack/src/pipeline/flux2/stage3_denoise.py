"""Stage 3 — Create noise, compute schedule, and denoise latent.

Two entry points:
  * `run(...)`           -- t2i: pure noise → denoise from t=1.0 to t=0.0.
  * `run_img2img(...)`   -- img2img: AE-encode an init image, mix with noise
                            via flow-matching linear interpolation
                            `x = (1 - t_start) * z0 + t_start * noise` where
                            `t_start = strength`, then denoise the schedule
                            tail from t_start down. Used by HandRefiner's
                            polish pass.
"""

import argparse

import torch
from PIL import Image

from flux2.model import Flux2
from flux2.sampling import batched_prc_img, default_prep, denoise, denoise_cfg, get_schedule


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
) -> dict:
    """Create noise, build timestep schedule, and run denoising loop.

    Returns dict with keys: x, x_ids, seed, timesteps, noise_shape.
    """
    if seed is None:
        seed = torch.randint(0, 2**31, (1,)).item()

    noise_shape = (1, 128, height // 16, width // 16)

    with torch.no_grad():
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
            )

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
    via flow-matching linear interpolation, slices the timestep schedule to
    start at t=strength, and runs the denoise loop from there.

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
        img_tensor = img_tensor[None].to(device="cuda", dtype=torch.bfloat16)
        prep_h, prep_w = img_tensor.shape[-2:]

        # 2. AE-encode -> latent at /16 spatial, 128 channels (matches noise).
        z0 = ae.encode(img_tensor)
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

        # 5. Build the full schedule, then take the tail starting at t=strength.
        full_timesteps = get_schedule(num_steps, x.shape[1])
        # The schedule descends from 1.0 to 0.0; find the first index where
        # the timestep <= strength, and slice from one step earlier so the
        # first interval starts at strength (we then overwrite the head).
        start_idx = 0
        for i, t in enumerate(full_timesteps):
            if t <= strength:
                start_idx = max(0, i - 1)
                break
        timesteps = [strength] + full_timesteps[start_idx + 1:]
        # Floor to at least 2 entries (one denoise interval).
        if len(timesteps) < 2:
            timesteps = [strength, 0.0]

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
    return {
        "x_dtype": str(x.dtype),
        "x_device": str(x.device),
        "x_min": float(x.float().min()),
        "x_max": float(x.float().max()),
        "x_mean": float(x.float().mean()),
        "timesteps": result["timesteps"],
    }


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
