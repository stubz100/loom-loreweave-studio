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


def run(
    model_name: str = "flux.2-klein-4b",
    device: str = "cuda",
    cpu_offload: bool = False,
) -> dict:
    """Load all three model components and return them with metadata.

    When cpu_offload=True, the flow model is loaded on CPU to save VRAM.
    The orchestrator is responsible for moving it to GPU before denoising.

    Returns dict with keys: model, ae, text_encoder, model_info, timings.
    """
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
