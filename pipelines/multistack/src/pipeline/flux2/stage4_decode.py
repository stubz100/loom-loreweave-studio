"""Stage 4 — Decode denoised latent to image and save to disk."""

import argparse
from pathlib import Path

import torch
from einops import rearrange
from PIL import ExifTags, Image

from flux2.autoencoder import AutoEncoder
from flux2.sampling import scatter_ids


def run(
    ae: AutoEncoder,
    x: torch.Tensor,
    x_ids: torch.Tensor,
    output_path: str | Path,
) -> dict:
    """Decode latent, convert to PIL Image, save as PNG.

    Returns dict with keys: image, output_path, width, height, file_size_bytes.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with torch.no_grad():
        x_scattered = torch.cat(scatter_ids(x, x_ids)).squeeze(2)
        print("[stage4] VAE decoding latent — this may take a few minutes ...")
        decoded = ae.decode(x_scattered).float()
        print("[stage4] VAE decode complete.")

    decoded = decoded.clamp(-1, 1)
    pixels = rearrange(decoded[0], "c h w -> h w c")
    img_array = (127.5 * (pixels + 1.0)).cpu().byte().numpy()
    image = Image.fromarray(img_array)

    exif_data = Image.Exif()
    exif_data[ExifTags.Base.Software] = "AI generated;flux2"
    exif_data[ExifTags.Base.Make] = "Black Forest Labs"
    image.save(output_path, exif=exif_data, quality=95, subsampling=0)

    return {
        "image": image,
        "output_path": str(output_path),
        "width": image.width,
        "height": image.height,
        "file_size_bytes": output_path.stat().st_size,
    }


def get_manifest_inputs(x_shape: list, x_ids_shape: list, output_path: str) -> dict:
    return {
        "x_shape": x_shape,
        "x_ids_shape": x_ids_shape,
        "output_path": output_path,
    }


def get_manifest_outputs(result: dict) -> dict:
    return {
        "output_path": result["output_path"],
        "width": result["width"],
        "height": result["height"],
        "file_size_bytes": result["file_size_bytes"],
    }


def get_manifest_debug(result: dict) -> dict:
    return {
        "image_mode": result["image"].mode,
        "image_format": "PNG",
    }


def main():
    parser = argparse.ArgumentParser(description="Stage 4: Decode latent to image")
    parser.add_argument("--model-name", default="flux.2-klein-4b")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--width", type=int, default=1360)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-steps", type=int, default=4)
    parser.add_argument("--guidance", type=float, default=1.0)
    parser.add_argument("--output", default="src/assets/pics/output.png")
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()

    from flux2.util import FLUX2_MODEL_INFO, load_ae, load_flow_model, load_text_encoder
    from flux2.sampling import batched_prc_img, batched_prc_txt, denoise, get_schedule

    model_info = FLUX2_MODEL_INFO[args.model_name]
    torch_device = torch.device(args.device)

    text_encoder = load_text_encoder(args.model_name, device=torch_device)
    text_encoder.eval()
    model = load_flow_model(args.model_name, device=torch_device)
    model.eval()
    ae = load_ae(args.model_name)
    ae.eval()

    guidance_distilled = model_info.get("guidance_distilled", True)
    with torch.no_grad():
        if guidance_distilled:
            ctx = text_encoder([args.prompt]).to(torch.bfloat16)
        else:
            ctx = torch.cat([text_encoder([""]), text_encoder([args.prompt])], dim=0).to(torch.bfloat16)
        ctx, ctx_ids = batched_prc_txt(ctx)

        noise_shape = (1, 128, args.height // 16, args.width // 16)
        generator = torch.Generator(device="cuda").manual_seed(args.seed)
        noise = torch.randn(noise_shape, generator=generator, dtype=torch.bfloat16, device="cuda")
        x, x_ids = batched_prc_img(noise)
        timesteps = get_schedule(args.num_steps, x.shape[1])
        x = denoise(model, x, x_ids, ctx, ctx_ids, timesteps=timesteps, guidance=args.guidance)

    result = run(ae=ae, x=x, x_ids=x_ids, output_path=args.output)
    print(f"Saved {result['output_path']} ({result['width']}x{result['height']}, {result['file_size_bytes']} bytes)")
    print("Stage 4 complete.")


if __name__ == "__main__":
    main()
