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

# AutoencoderKL only switches to tiled decode when the latent is strictly larger than
# `tile_latent_min_size`. Z-Image declares sample_size=1024, so enable_tiling() alone is a no-op
# for our exact 1024x1024 target (128x128 latent). Force 512px / 64-latent tiles so this path is
# genuinely exercised and keeps each MIOpen convolution below the pathological full-frame shape.
VAE_TILE_SAMPLE_MIN_SIZE = 512


def _enable_vae_tiling(vae) -> dict:
    vae_scale_factor = 2 ** (len(vae.config.block_out_channels) - 1)
    tile_latent_min_size = VAE_TILE_SAMPLE_MIN_SIZE // vae_scale_factor
    vae.enable_tiling()
    vae.tile_sample_min_size = VAE_TILE_SAMPLE_MIN_SIZE
    vae.tile_latent_min_size = tile_latent_min_size
    print(
        f"[zimage-vae] GPU tiling enabled: sample_tile={VAE_TILE_SAMPLE_MIN_SIZE} "
        f"latent_tile={tile_latent_min_size}",
        flush=True,
    )
    return {
        "enabled": True,
        "tile_sample_min_size": VAE_TILE_SAMPLE_MIN_SIZE,
        "tile_latent_min_size": tile_latent_min_size,
    }


def _compile_transformer(transformer) -> dict:
    """Opt-in torch.compile of the DiT (the caller ROCm-gates it). Prefers diffusers'
    compile_repeated_blocks (fast compile, offload-friendly) over whole-module compile.
    ZImage's 3D RoPE uses complex ops TorchInductor can't codegen, so fullgraph=False and the
    realistic gain is ~10% (see kb-zimage denoise-floor). Best-effort: any failure (e.g. no
    triton-windows/inductor) degrades to eager, never raises."""
    import os

    # Persistent inductor cache so the one-time ~60s compile amortises across worker processes
    # (each spawn derives the same stable path). Only touched when actually compiling.
    if not os.environ.get("TORCHINDUCTOR_CACHE_DIR"):
        hf_home = os.environ.get("HF_HOME")
        base = Path(hf_home).parent if hf_home else Path.home()
        os.environ["TORCHINDUCTOR_CACHE_DIR"] = str(base / "torchinductor_cache")
    try:
        import torch._dynamo
        torch._dynamo.config.cache_size_limit = 64
    except Exception:  # noqa: BLE001
        pass

    block_compile = getattr(transformer, "compile_repeated_blocks", None)
    try:
        if callable(block_compile):
            block_compile(fullgraph=False)
            method = "compile_repeated_blocks"
        else:
            transformer.compile()  # nn.Module.compile -> torch.compile in place
            method = "compile"
    except Exception as e:  # noqa: BLE001 — inductor/triton may be unavailable; stay eager
        print(f"[zimage-compile] torch.compile unavailable, running eager: {e}", flush=True)
        return {"enabled": False, "error": str(e)[:200]}

    cache_dir = os.environ.get("TORCHINDUCTOR_CACHE_DIR")
    print(f"[zimage-compile] {method}(fullgraph=False); inductor cache={cache_dir} "
          f"(first run per shape compiles ~60s, then cached)", flush=True)
    return {"enabled": True, "method": method, "cache_dir": cache_dir}


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
    compile_transformer: bool = False,
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
        compile_transformer: Opt-in torch.compile of the DiT (ROCm-gated here). ~10%
            faster/step at the cost of a one-time ~60s compile per output shape; best for
            fixed-size batches. Best-effort — falls back to eager if inductor is unavailable.

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

    vae_tiling = _enable_vae_tiling(pipe.vae)

    if cpu_offload:
        pipe.enable_model_cpu_offload()
    else:
        pipe.to(device)

    # torch.compile AFTER offload setup (production uses offload; the two coexist). ROCm/CUDA-gated
    # — on CPU there's nothing to gain. compile_repeated_blocks marks the blocks lazily, so the
    # ~60s compile lands on the first stage2 forward, not here.
    compile_info: dict = {"enabled": False}
    if compile_transformer:
        if torch.cuda.is_available():
            compile_info = _compile_transformer(pipe.transformer)
        else:
            print("[zimage-compile] skipped (no CUDA/ROCm device)", flush=True)

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
        "vae_tiling": vae_tiling,
        "compile": compile_info,
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
    compile_transformer: bool = False,
) -> dict:
    return {
        "model_name": model_name,
        "device": device,
        "cpu_offload": cpu_offload,
        "attention_backend": attention_backend,
        "compile": compile_transformer,
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
        "vae_tiling": result["vae_tiling"],
        "compile": result.get("compile"),
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
