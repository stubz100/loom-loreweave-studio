"""Stage 1 - Load Krea 2 diffusers pipeline."""

from __future__ import annotations

import hashlib
import importlib.metadata
import time
from pathlib import Path
from typing import Any


KREA2_MODEL_INFO = {
    "krea2-turbo": {
        "repo_id": "krea/Krea-2-Turbo",
        "defaults": {"num_steps": 8, "guidance_scale": 0.0, "width": 768, "height": 768},
        "supports_negative_prompt": False,
        "is_distilled": True,
        "vae_scale_factor": 8,
        "patch_size": 2,
    },
}


def run(
    model_name: str = "krea2-turbo",
    device: str = "cuda",
    cpu_offload: bool = True,
    dtype: str = "bfloat16",
    device_map: str | None = None,
    lora_path: str | None = None,
    lora_weight: float = 1.0,
    quant_backend: str | None = None,
    quant_dtype: str = "float8",
    quant_skip_modules: list[str] | None = None,
) -> dict:
    """Load Krea2Pipeline and return it with runtime metadata."""
    if model_name not in KREA2_MODEL_INFO:
        raise ValueError(f"unknown model_name {model_name!r}; expected one of {list(KREA2_MODEL_INFO)}")
    if device_map and cpu_offload:
        raise ValueError("--device-map and CPU offload are mutually exclusive; pass --no-cpu-offload with --device-map")

    try:
        from diffusers import Krea2Pipeline
    except ImportError as exc:
        raise ImportError(
            "Krea2Pipeline is not available in the installed diffusers package. "
            "Install diffusers from main in an isolated runtime, e.g. "
            "`pip install git+https://github.com/huggingface/diffusers.git`."
        ) from exc

    model_info = KREA2_MODEL_INFO[model_name]
    repo_id = model_info["repo_id"]
    torch_dtype = _resolve_dtype(dtype)
    timings: dict[str, float] = {}

    load_kwargs: dict[str, Any] = {"torch_dtype": torch_dtype}
    if device_map:
        load_kwargs["device_map"] = device_map
    if quant_backend:
        if quant_backend != "quanto":
            raise ValueError(f"unsupported quant_backend {quant_backend!r}; expected 'quanto'")
        if quant_dtype not in ("float8", "int8", "int4", "int2"):
            raise ValueError("quanto quant_dtype must be one of float8, int8, int4, int2")
        try:
            from diffusers import Krea2Transformer2DModel, QuantoConfig
        except ImportError as exc:
            raise ImportError(
                "Krea 2 Quanto quantization requires diffusers main with "
                "Krea2Transformer2DModel and QuantoConfig available."
            ) from exc
        q_t0 = time.time()
        quant_config = QuantoConfig(
            weights_dtype=quant_dtype,
            modules_to_not_convert=quant_skip_modules,
        )
        load_kwargs["transformer"] = Krea2Transformer2DModel.from_pretrained(
            repo_id,
            subfolder="transformer",
            quantization_config=quant_config,
            torch_dtype=torch_dtype,
        )
        timings["transformer_quantized_load_s"] = round(time.time() - q_t0, 4)

    lora = _resolve_lora(lora_path, lora_weight)

    t0 = time.time()
    pipe = Krea2Pipeline.from_pretrained(repo_id, **load_kwargs)

    if lora is not None:
        path = Path(lora["path"])
        load_root = path.parent if path.is_file() else path
        load_lora_kwargs: dict[str, Any] = {"adapter_name": lora["name"]}
        if path.is_file():
            load_lora_kwargs["weight_name"] = path.name
        pipe.load_lora_weights(str(load_root), **load_lora_kwargs)
        if hasattr(pipe, "set_adapters"):
            pipe.set_adapters(lora["name"], adapter_weights=float(lora_weight))

    if cpu_offload:
        pipe.enable_model_cpu_offload()
    elif not device_map:
        pipe.to(device)

    timings["pipeline_load_s"] = round(time.time() - t0, 4)

    versions = get_runtime_versions()
    return {
        "pipe": pipe,
        "model_info": model_info,
        "model_name": model_name,
        "device": device,
        "cpu_offload": cpu_offload,
        "dtype": dtype,
        "device_map": device_map,
        "quant_backend": quant_backend,
        "quant_dtype": quant_dtype if quant_backend else None,
        "quant_skip_modules": quant_skip_modules or [],
        "lora": lora,
        "runtime_versions": versions,
        "timings": timings,
    }


def _resolve_dtype(dtype: str) -> Any:
    try:
        import torch
    except ImportError as exc:
        raise ImportError("Krea 2 generation requires PyTorch to be installed in the active runtime.") from exc

    if dtype == "bfloat16":
        return torch.bfloat16
    if dtype == "float16":
        return torch.float16
    if dtype == "float32":
        return torch.float32
    raise ValueError(f"unsupported dtype {dtype!r}; expected bfloat16, float16, or float32")


def _resolve_lora(lora_path: str | None, lora_weight: float) -> dict | None:
    if not lora_path:
        return None
    path = Path(lora_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"LoRA path does not exist: {path}")
    return {
        "path": str(path),
        "name": "krea2_lora",
        "weight": float(lora_weight),
        "sha256": _sha256(path) if path.is_file() else None,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def get_runtime_versions() -> dict:
    """Collect dependency versions for the Krea 2 spike manifest."""
    diffusers_commit = None
    torch_version = None
    rocm_version = None
    try:
        import diffusers

        diffusers_commit = getattr(diffusers, "__git_version__", None)
        version_module = getattr(diffusers, "version", None)
        if diffusers_commit is None and version_module is not None:
            diffusers_commit = getattr(version_module, "git_version", None)
    except Exception:
        pass
    try:
        import torch

        torch_version = torch.__version__
        rocm_version = getattr(torch.version, "hip", None)
    except Exception:
        pass

    return {
        "diffusers_version": _metadata_version("diffusers"),
        "diffusers_commit": diffusers_commit,
        "transformers_version": _metadata_version("transformers"),
        "torch_version": torch_version,
        "rocm_version": rocm_version,
    }


def get_manifest_inputs(
    model_name: str,
    device: str,
    cpu_offload: bool,
    dtype: str,
    device_map: str | None = None,
    lora_path: str | None = None,
    lora_weight: float = 1.0,
    quant_backend: str | None = None,
    quant_dtype: str = "float8",
    quant_skip_modules: list[str] | None = None,
) -> dict:
    return {
        "model_name": model_name,
        "device": device,
        "cpu_offload": cpu_offload,
        "dtype": dtype,
        "device_map": device_map,
        "quant_backend": quant_backend,
        "quant_dtype": quant_dtype if quant_backend else None,
        "quant_skip_modules": quant_skip_modules or [],
        "lora_path": lora_path,
        "lora_weight": lora_weight if lora_path else None,
    }


def get_manifest_outputs(result: dict) -> dict:
    info = result["model_info"]
    return {
        "model_name": result["model_name"],
        "hf_repo": info["repo_id"],
        "defaults": info["defaults"],
        "supports_negative_prompt": info["supports_negative_prompt"],
        "is_distilled": info["is_distilled"],
        "vae_scale_factor": info["vae_scale_factor"],
        "patch_size": info["patch_size"],
        "cpu_offload": result["cpu_offload"],
        "dtype": result["dtype"],
        "device_map": result["device_map"],
        "quant_backend": result["quant_backend"],
        "quant_dtype": result["quant_dtype"],
        "quant_skip_modules": result["quant_skip_modules"],
        "lora": result["lora"],
        **result["runtime_versions"],
        "timings": result["timings"],
    }


def get_manifest_debug(result: dict) -> dict:
    pipe = result["pipe"]
    transformer = getattr(pipe, "transformer", None)
    text_encoder = getattr(pipe, "text_encoder", None)
    vae = getattr(pipe, "vae", None)
    scheduler = getattr(pipe, "scheduler", None)
    return {
        "pipeline_class": type(pipe).__name__,
        "transformer_class": type(transformer).__name__ if transformer is not None else None,
        "transformer_dtype": str(getattr(transformer, "dtype", "")) if transformer is not None else None,
        "text_encoder_class": type(text_encoder).__name__ if text_encoder is not None else None,
        "vae_class": type(vae).__name__ if vae is not None else None,
        "scheduler_class": type(scheduler).__name__ if scheduler is not None else None,
    }
