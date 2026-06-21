"""Stage 1 — Load Z-Image pipeline (transformer, VAE, Qwen3 text encoder, scheduler)."""

import hashlib
import time
from pathlib import Path

import torch
from diffusers import ZImageImg2ImgPipeline, ZImageInpaintPipeline, ZImagePipeline


# Mode → diffusers pipeline-class mapping. Phase B.1 (img2img + inpaint) shares
# the same transformer/text-encoder/VAE checkpoints as plain T2I; only the
# pipeline class differs.
MODE_PIPELINE_CLASSES = {
    "t2i": ZImagePipeline,
    "img2img": ZImageImg2ImgPipeline,
    "inpaint": ZImageInpaintPipeline,
}


# Model registry with default parameters
#
# Z-Image ships two released variants (both Apache 2.0):
#   - zimage-turbo: 8 NFEs (num_inference_steps=9), guidance_scale=0.0, RL-trained, no CFG
#   - zimage-base:  28-50 steps, guidance 3.0-5.0, supports negative prompts + cfg_normalization
ZIMAGE_MODEL_INFO = {
    "zimage-turbo": {
        "repo_id": "Tongyi-MAI/Z-Image-Turbo",
        "defaults": {"num_steps": 9, "guidance_scale": 0.0},
        "supports_negative_prompt": False,
        "supports_cfg_normalization": False,
    },
    "zimage-base": {
        "repo_id": "Tongyi-MAI/Z-Image",
        "defaults": {"num_steps": 50, "guidance_scale": 4.0},
        "supports_negative_prompt": True,
        "supports_cfg_normalization": True,
    },
}


def run(
    model_name: str = "zimage-turbo",
    device: str = "cuda",
    cpu_offload: bool = True,
    dtype: str = "bfloat16",
    attention_backend: str | None = None,
    mode: str = "t2i",
    lora_path: str | None = None,
    lora_name: str = "loom_character",
    lora_weight: float = 1.0,
) -> dict:
    """Load the Z-Image pipeline and return it with metadata.

    Args:
        model_name: Key into ZIMAGE_MODEL_INFO.
        device: Target device ("cuda" — also used for ROCm/HIP).
        cpu_offload: Use enable_model_cpu_offload() to save VRAM.
        dtype: "bfloat16" or "float16" (bfloat16 recommended).
        attention_backend: One of "native_flash", "math", "flash", "_flash_3", or None.
            On ROCm Windows, "flash" and "_flash_3" are unavailable — use "native_flash"
            (SDPA, dispatches to CK on ROCm) or leave as None for the diffusers default.
        mode: One of "t2i" (default), "img2img", "inpaint". Selects the diffusers
            pipeline class. All three modes share the same checkpoint, so model
            download is identical.
        lora_path: Optional local LoRA file or directory accepted by Diffusers.
        lora_name: Adapter name registered in the pipeline.
        lora_weight: Runtime adapter scale.

    Returns dict with keys: pipe, model_info, model_name, device, mode, timings.
    """
    if mode not in MODE_PIPELINE_CLASSES:
        raise ValueError(
            f"unknown mode {mode!r}; must be one of {list(MODE_PIPELINE_CLASSES)}"
        )
    pipeline_cls = MODE_PIPELINE_CLASSES[mode]
    model_info = ZIMAGE_MODEL_INFO[model_name]
    repo_id = model_info["repo_id"]
    torch_dtype = torch.bfloat16 if dtype == "bfloat16" else torch.float16
    timings: dict[str, float] = {}
    lora: dict | None = None
    load_root: Path | None = None
    load_kwargs: dict = {}
    if lora_path:
        path = Path(lora_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"LoRA path does not exist: {path}")
        if not lora_name.strip():
            raise ValueError("lora_name must not be empty when lora_path is set")
        load_root = path.parent if path.is_file() else path
        load_kwargs = {"adapter_name": lora_name}
        if path.is_file():
            load_kwargs["weight_name"] = path.name
        lora = {
            "path": str(path),
            "name": lora_name,
            "weight": float(lora_weight),
            "sha256": _sha256(path) if path.is_file() else None,
        }

    t0 = time.time()
    # low_cpu_mem_usage=False is explicitly recommended by the Z-Image model card
    # and avoids meta-tensor init paths that can interact poorly with HIP device placement.
    pipe = pipeline_cls.from_pretrained(
        repo_id,
        torch_dtype=torch_dtype,
        low_cpu_mem_usage=False,
    )

    if load_root is not None:
        pipe.load_lora_weights(str(load_root), **load_kwargs)
        pipe.set_adapters(lora_name, adapter_weights=float(lora_weight))

    if attention_backend is not None:
        pipe.transformer.set_attention_backend(attention_backend)

    if cpu_offload:
        pipe.enable_model_cpu_offload()
    else:
        pipe.to(device)

    timings["pipeline_load_s"] = round(time.time() - t0, 4)

    return {
        "pipe": pipe,
        "model_info": model_info,
        "model_name": model_name,
        "device": device,
        "mode": mode,
        "pipeline_class": pipeline_cls.__name__,
        "cpu_offload": cpu_offload,
        "dtype": dtype,
        "attention_backend": attention_backend,
        "lora": lora,
        "timings": timings,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_manifest_inputs(
    model_name: str,
    device: str,
    cpu_offload: bool,
    attention_backend: str | None,
    mode: str = "t2i",
    lora_path: str | None = None,
    lora_name: str = "loom_character",
    lora_weight: float = 1.0,
) -> dict:
    return {
        "model_name": model_name,
        "device": device,
        "cpu_offload": cpu_offload,
        "attention_backend": attention_backend,
        "mode": mode,
        "lora_path": lora_path,
        "lora_name": lora_name if lora_path else None,
        "lora_weight": lora_weight if lora_path else None,
    }


def get_manifest_outputs(result: dict) -> dict:
    info = result["model_info"]
    return {
        "model_name": result["model_name"],
        "repo_id": info["repo_id"],
        "defaults": info["defaults"],
        "supports_negative_prompt": info["supports_negative_prompt"],
        "supports_cfg_normalization": info["supports_cfg_normalization"],
        "mode": result["mode"],
        "pipeline_class": result["pipeline_class"],
        "cpu_offload": result["cpu_offload"],
        "dtype": result["dtype"],
        "attention_backend": result["attention_backend"],
        "lora": result["lora"],
        "timings": result["timings"],
    }


def get_manifest_debug(result: dict) -> dict:
    pipe = result["pipe"]
    return {
        "transformer_class": type(pipe.transformer).__name__,
        "transformer_dtype": str(pipe.transformer.dtype),
        "text_encoder_class": type(pipe.text_encoder).__name__,
        "vae_class": type(pipe.vae).__name__,
        "scheduler_class": type(pipe.scheduler).__name__,
    }
