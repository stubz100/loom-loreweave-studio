"""Stage 1 — Load Flux2 flow model, autoencoder, and text encoder."""

import argparse
import os
import sys
import time

import torch

from flux2.util import FLUX2_MODEL_INFO, load_ae, load_flow_model, load_text_encoder


def _needs_fp8_workaround() -> bool:
    """FP8 quantized models require torch.distributed.tensor.DTensor which is
    unavailable on Windows ROCm.  Return True when we must fall back to the
    non-FP8 variant of Qwen3."""
    if os.name != "nt":
        return False
    hip = getattr(torch.version, "hip", None)
    return hip is not None


def _qwen3_variant_for_model(model_name: str) -> str | None:
    """Return the Qwen3 variant size used by a Klein model, or None for non-Klein."""
    if "klein" not in model_name:
        return None
    return "8B" if "9b" in model_name else "4B"


def _load_text_encoder_safe(model_name: str, device: torch.device) -> torch.nn.Module:
    """Load the text encoder, falling back to non-FP8 Qwen3 on Windows ROCm."""
    variant = _qwen3_variant_for_model(model_name)
    if variant is not None and _needs_fp8_workaround():
        from flux2.text_encoder import Qwen3Embedder
        model_spec = f"Qwen/Qwen3-{variant}"
        print(f"[stage1] Windows ROCm detected — loading non-FP8 text encoder: {model_spec}")
        return Qwen3Embedder(model_spec=model_spec, device=device)
    return load_text_encoder(model_name, device=device)


# ── flux.2-dev quantized path (M2.5) ──────────────────────────────────────────────────
# `flux.2-dev` routes to the Comfy-Org quantized split files (scaled-FP8 transformer + Mistral TE
# + Flux2 VAE), proven in the spike. Single-run only (t2i/img2img) — the batch `ref` sweep stays
# Klein-only (run_pipeline.run_jobs guards dev out). Klein variants are untouched above.
QUANTIZED_DEV_MODEL = "flux.2-dev"


class ComfyMistralEmbedder(torch.nn.Module):
    """Adapter matching flux2.text_encoder.Mistral3SmallEmbedder output (3 hidden-state taps)."""

    output_layers = (10, 20, 30)
    max_length = 512
    system_message = (
        "You are an AI that reasons about image descriptions. You give structured responses focusing "
        "on object relationships, object attribution and actions without speculation."
    )

    def __init__(self, model: torch.nn.Module, processor) -> None:
        super().__init__()
        self.model = model
        self.processor = processor

    def forward(self, txt: list[str]) -> torch.Tensor:
        from einops import rearrange

        messages = [
            [
                {"role": "system", "content": [{"type": "text", "text": self.system_message}]},
                {"role": "user", "content": [{"type": "text", "text": prompt.replace("[IMG]", "")}]},
            ]
            for prompt in txt
        ]
        inputs = self.processor.apply_chat_template(
            messages,
            add_generation_prompt=False,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            padding="max_length",
            truncation=True,
            max_length=self.max_length,
        )
        device = self.model.model.language_model.embed_tokens.weight.device
        input_ids = inputs["input_ids"].to(device)
        attention_mask = inputs["attention_mask"].to(device)
        output = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            output_hidden_states=True,
            use_cache=False,
        )
        out = torch.stack([output.hidden_states[i] for i in self.output_layers], dim=1)
        return rearrange(out, "b c l d -> b l (c d)")


def _load_dev_quantized(
    device: str,
    cpu_offload: bool,
    dtype: str,
    local_files_only: bool,
    fp8_matmul: str,
    text_encoder_variant: str | None,
) -> dict:
    """Load `flux.2-dev` from the Comfy-Org quantized split files. Returns the same contract as
    run() (model/ae/text_encoder/model_info/...) plus quantized lineage for the manifest."""
    from . import scaled_fp8 as q

    torch_device = torch.device(device)
    torch_dtype = q.resolve_dtype(dtype)
    variant = q.normalize_text_encoder_variant(text_encoder_variant or "fp8")
    if variant == "fp4":
        raise NotImplementedError(
            "text_encoder_variant='fp4' uses Comfy's packed NVFP4 layout (comfy_quant/weight_scale_2); "
            "the quantized dev path supports bf16/fp8 — add an NVFP4 Linear adapter before fp4."
        )
    te_file = q.TEXT_ENCODER_FILES[variant]
    timings: dict[str, float] = {}

    t0 = time.time()
    transformer_path = q.resolve_hf_file(q.COMFY_FLUX2_REPO, q.TRANSFORMER_FILE, local_files_only=local_files_only)
    te_path = q.resolve_hf_file(q.COMFY_FLUX2_REPO, te_file, local_files_only=local_files_only)
    vae_path = q.resolve_hf_file(q.COMFY_FLUX2_REPO, q.VAE_FILE, local_files_only=local_files_only)
    timings["resolve_files_s"] = round(time.time() - t0, 4)

    t0 = time.time()
    text_encoder, processor, te_stats = q.load_comfy_mistral_text_encoder(
        te_path, device=torch_device, dtype=torch_dtype,
        local_files_only=local_files_only, fp8_matmul=fp8_matmul,
    )
    timings["text_encoder_load_s"] = round(time.time() - t0, 4)

    t0 = time.time()
    flow_device = "cpu" if cpu_offload else torch_device
    model, tr_stats = q.load_comfy_flux2_transformer(
        transformer_path, device=flow_device, dtype=torch_dtype, fp8_matmul=fp8_matmul,
    )
    timings["flow_model_load_s"] = round(time.time() - t0, 4)

    t0 = time.time()
    ae, vae_stats = q.load_comfy_vae(vae_path, device=torch_device, dtype=torch_dtype)
    timings["autoencoder_load_s"] = round(time.time() - t0, 4)

    return {
        "model": model,
        "ae": ae,
        "text_encoder": ComfyMistralEmbedder(text_encoder, processor),
        "model_info": FLUX2_MODEL_INFO[QUANTIZED_DEV_MODEL],
        "model_name": QUANTIZED_DEV_MODEL,
        "device": device,
        "timings": timings,
        # quantized lineage (surfaced into the manifest by run_pipeline) — distinguishes
        # "Comfy quantized dev" from a hypothetical full-dev output even though the id is the same.
        "quantized": {
            "backend_variant": "comfy-q8",
            "hf_repo": q.COMFY_FLUX2_REPO,
            "transformer_file": q.TRANSFORMER_FILE,
            "text_encoder_file": te_file,
            "text_encoder_variant": variant,
            "vae_file": q.VAE_FILE,
            "fp8_matmul": fp8_matmul,
            "dtype": dtype,
            "cpu_offload": cpu_offload,
        },
        "quant_stats": {"transformer": tr_stats, "text_encoder": te_stats, "vae": vae_stats},
    }


def run(
    model_name: str = "flux.2-klein-4b",
    device: str = "cuda",
    cpu_offload: bool = False,
    *,
    dtype: str = "bfloat16",
    local_files_only: bool = True,
    fp8_matmul: str = "auto",
    text_encoder_variant: str | None = None,
) -> dict:
    """Load all three model components and return them with metadata.

    When cpu_offload=True, the flow model is loaded on CPU to save VRAM.
    The orchestrator is responsible for moving it to GPU before denoising.

    `flux.2-dev` routes to the Comfy-Org quantized split files (M2.5); the dev-only kwargs
    (dtype/fp8_matmul/text_encoder_variant/local_files_only) are ignored for Klein.

    Returns dict with keys: model, ae, text_encoder, model_info, timings (+ quantized for dev).
    """
    if model_name == QUANTIZED_DEV_MODEL:
        return _load_dev_quantized(
            device, cpu_offload, dtype, local_files_only, fp8_matmul, text_encoder_variant,
        )

    torch_device = torch.device(device)
    model_info = FLUX2_MODEL_INFO[model_name]
    timings: dict[str, float] = {}

    t0 = time.time()
    text_encoder = _load_text_encoder_safe(model_name, torch_device)
    text_encoder.eval()
    timings["text_encoder_load_s"] = round(time.time() - t0, 4)

    t0 = time.time()
    flow_device = "cpu" if cpu_offload else torch_device
    model = load_flow_model(model_name, device=flow_device)
    model.eval()
    timings["flow_model_load_s"] = round(time.time() - t0, 4)

    t0 = time.time()
    ae = load_ae(model_name)
    ae.eval()
    timings["autoencoder_load_s"] = round(time.time() - t0, 4)

    return {
        "model": model,
        "ae": ae,
        "text_encoder": text_encoder,
        "model_info": model_info,
        "model_name": model_name,
        "device": device,
        "timings": timings,
    }


def get_manifest_inputs(model_name: str, device: str, cpu_offload: bool = False) -> dict:
    return {"model_name": model_name, "device": device, "cpu_offload": cpu_offload}


def get_manifest_outputs(result: dict) -> dict:
    info = result["model_info"]
    return {
        "model_name": result["model_name"],
        "guidance_distilled": info.get("guidance_distilled", False),
        "use_kv_cache": info.get("use_kv_cache", False),
        "defaults": info.get("defaults", {}),
        "fixed_params": list(info.get("fixed_params", set())),
        "timings": result["timings"],
    }


def get_manifest_debug(result: dict) -> dict:
    return {
        "torch_version": torch.__version__,
        "device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "device_count": torch.cuda.device_count(),
        "hip_available": hasattr(torch.version, "hip") and torch.version.hip is not None,
        "fp8_workaround": _needs_fp8_workaround(),
    }


def main():
    parser = argparse.ArgumentParser(description="Stage 1: Load Flux2 models")
    parser.add_argument("--model-name", default="flux.2-klein-4b", choices=list(FLUX2_MODEL_INFO.keys()))
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()

    print(f"Loading models: {args.model_name} on {args.device}")
    result = run(model_name=args.model_name, device=args.device)

    print("Timings:")
    for k, v in result["timings"].items():
        print(f"  {k}: {v}s")
    print("Model info:", {k: v for k, v in result["model_info"].items() if k != "params_cls"})
    print("Stage 1 complete.")


if __name__ == "__main__":
    main()
