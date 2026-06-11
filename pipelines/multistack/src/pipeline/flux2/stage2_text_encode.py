"""Stage 2 — Encode text prompt into context tensors for the denoiser."""

import argparse
import sys

import torch

from flux2.sampling import batched_prc_txt


def run(
    prompt: str,
    text_encoder: torch.nn.Module,
    guidance_distilled: bool = True,
) -> dict:
    """Encode prompt and return processed context tensors.

    For guidance-distilled models (Klein): encode prompt only.
    For non-distilled models (Dev): encode empty + prompt, concatenate for CFG.

    Returns dict with keys: ctx, ctx_ids, prompt.
    """
    with torch.no_grad():
        if guidance_distilled:
            ctx = text_encoder([prompt]).to(torch.bfloat16)
        else:
            ctx_empty = text_encoder([""]).to(torch.bfloat16)
            ctx_prompt = text_encoder([prompt]).to(torch.bfloat16)
            ctx = torch.cat([ctx_empty, ctx_prompt], dim=0)

        ctx, ctx_ids = batched_prc_txt(ctx)

    return {
        "ctx": ctx,
        "ctx_ids": ctx_ids,
        "prompt": prompt,
        "guidance_distilled": guidance_distilled,
    }


def get_manifest_inputs(prompt: str, guidance_distilled: bool) -> dict:
    return {"prompt": prompt, "guidance_distilled": guidance_distilled}


def get_manifest_outputs(result: dict) -> dict:
    return {
        "ctx_shape": list(result["ctx"].shape),
        "ctx_ids_shape": list(result["ctx_ids"].shape),
        "ctx_dtype": str(result["ctx"].dtype),
    }


def get_manifest_debug(result: dict) -> dict:
    return {
        "ctx_device": str(result["ctx"].device),
        "ctx_min": float(result["ctx"].float().min()),
        "ctx_max": float(result["ctx"].float().max()),
    }


def main():
    parser = argparse.ArgumentParser(description="Stage 2: Encode text prompt")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model-name", default="flux.2-klein-4b")
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()

    from flux2.util import FLUX2_MODEL_INFO, load_text_encoder

    model_info = FLUX2_MODEL_INFO[args.model_name]
    text_encoder = load_text_encoder(args.model_name, device=torch.device(args.device))
    text_encoder.eval()

    result = run(
        prompt=args.prompt,
        text_encoder=text_encoder,
        guidance_distilled=model_info.get("guidance_distilled", True),
    )

    print(f"ctx shape: {result['ctx'].shape}")
    print(f"ctx_ids shape: {result['ctx_ids'].shape}")
    print("Stage 2 complete.")


if __name__ == "__main__":
    main()
