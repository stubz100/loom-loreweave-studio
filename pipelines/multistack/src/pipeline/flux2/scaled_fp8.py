"""Scaled-FP8 helpers for Comfy-Org/flux2-dev split files (the quantized `flux.2-dev` path).

Comfy's Flux2 and Mistral files store selected Linear weights as torch.float8_e4m3fn plus sibling
scalar tensors:

  <module>.weight
  <module>.weight_scale
  <module>.input_scale

The wrapper below keeps the FP8 weight in memory and can either preserve the per-call
dequantization path or use torch._scaled_mm as a native scaled-FP8 matmul backend on supported
GPU builds.

M2.5: this replaces Loom's full BFL `flux.2-dev` runtime. The Mistral **config + tokenizer** are
loaded from the VENDORED `assets/mistral_te/` dir (config/tokenizer only, no weights) so the dev
path needs no gated `black-forest-labs/FLUX.2-dev` repo. Weights come from `Comfy-Org/flux2-dev`.
Ported from the proven spike `src/pipeline/flux2_q8/scaled_fp8.py` (2026-06-26).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
import torch
import torch.nn.functional as F
from huggingface_hub import hf_hub_download
from safetensors import safe_open
from torch import nn


COMFY_FLUX2_REPO = "Comfy-Org/flux2-dev"
TRANSFORMER_FILE = "split_files/diffusion_models/flux2_dev_fp8mixed.safetensors"
TEXT_ENCODER_FILES = {
    "fp8": "split_files/text_encoders/mistral_3_small_flux2_fp8.safetensors",
    "bf16": "split_files/text_encoders/mistral_3_small_flux2_bf16.safetensors",
    "fp4": "split_files/text_encoders/mistral_3_small_flux2_fp4_mixed.safetensors",
}
TEXT_ENCODER_VARIANT_ALIASES = {"fp16": "bf16"}
TEXT_ENCODER_VARIANTS = tuple(TEXT_ENCODER_FILES)
TEXT_ENCODER_CLI_CHOICES = TEXT_ENCODER_VARIANTS + tuple(TEXT_ENCODER_VARIANT_ALIASES)
VAE_FILE = "split_files/vae/flux2-vae.safetensors"
FP8_MIN = float(torch.finfo(torch.float8_e4m3fn).min)
FP8_MAX = float(torch.finfo(torch.float8_e4m3fn).max)
FP8_MATMUL_MODES = ("auto", "native", "dequant")

# Vendored Mistral config + tokenizer (config/tokenizer ONLY, no weights) — see
# assets/mistral_te/PROVENANCE.md. Lets the quantized dev text encoder build with no gated repo.
VENDORED_MISTRAL_TE = Path(__file__).resolve().parent / "assets" / "mistral_te"


def resolve_hf_file(repo_id: str, filename: str, local_files_only: bool = True) -> str:
    """Resolve a single HF file, defaulting to the local cache to avoid surprise downloads."""
    return hf_hub_download(repo_id=repo_id, filename=filename, local_files_only=local_files_only)


def resolve_dtype(dtype: str) -> torch.dtype:
    if dtype == "bfloat16":
        return torch.bfloat16
    if dtype == "float16":
        return torch.float16
    if dtype == "float32":
        return torch.float32
    raise ValueError(f"unsupported dtype {dtype!r}; expected bfloat16, float16, or float32")


def normalize_text_encoder_variant(variant: str) -> str:
    key = variant.lower()
    key = TEXT_ENCODER_VARIANT_ALIASES.get(key, key)
    if key not in TEXT_ENCODER_FILES:
        choices = ", ".join(TEXT_ENCODER_CLI_CHOICES)
        raise ValueError(f"unsupported text encoder variant {variant!r}; expected one of {choices}")
    return key


class ScaledFP8Linear(nn.Module):
    """Inference-only Linear that stores Comfy scaled-FP8 weights compactly."""

    def __init__(
        self,
        in_features: int,
        out_features: int,
        bias: bool,
        device: str | torch.device = "meta",
        matmul_mode: str = "auto",
    ):
        super().__init__()
        if matmul_mode not in FP8_MATMUL_MODES:
            raise ValueError(f"matmul_mode must be one of {FP8_MATMUL_MODES}, got {matmul_mode!r}")
        self.in_features = int(in_features)
        self.out_features = int(out_features)
        self.matmul_mode = matmul_mode
        self._native_disabled = False
        self.register_buffer(
            "weight",
            torch.empty((out_features, in_features), dtype=torch.float8_e4m3fn, device=device),
        )
        self.register_buffer("weight_scale", torch.empty((), dtype=torch.float32, device=device))
        self.register_buffer("input_scale", torch.empty((), dtype=torch.float32, device=device))
        if bias:
            self.bias = nn.Parameter(torch.empty(out_features, device=device), requires_grad=False)
        else:
            self.register_parameter("bias", None)

    def forward(self, input: torch.Tensor) -> torch.Tensor:
        if self.matmul_mode != "dequant" and not self._native_disabled and _can_use_native_scaled_mm(input, self.weight):
            try:
                return self._forward_native(input)
            except RuntimeError as exc:
                if self.matmul_mode == "native" or "out of memory" in str(exc).lower():
                    raise
                self._native_disabled = True
        if self.matmul_mode == "native":
            raise RuntimeError("native FP8 matmul requested, but torch._scaled_mm is unavailable for this input")
        return self._forward_dequant(input)

    def _forward_native(self, input: torch.Tensor) -> torch.Tensor:
        orig_shape = input.shape[:-1]
        input_2d = input.reshape(-1, input.shape[-1])
        input_scale = self.input_scale.to(device=input.device, dtype=input.dtype)
        weight_scale = self.weight_scale.to(device=input.device, dtype=torch.float32)
        input_fp8 = (input_2d / input_scale).clamp(FP8_MIN, FP8_MAX).to(torch.float8_e4m3fn)
        output = torch._scaled_mm(
            input_fp8,
            self.weight.t(),
            scale_a=input_scale.to(torch.float32),
            scale_b=weight_scale,
            out_dtype=input.dtype,
        )
        if self.bias is not None:
            output = output + self.bias.to(device=input.device, dtype=input.dtype)
        return output.reshape(*orig_shape, self.out_features)

    def _forward_dequant(self, input: torch.Tensor) -> torch.Tensor:
        weight = self.weight.to(dtype=input.dtype) * self.weight_scale.to(device=input.device, dtype=input.dtype)
        bias = self.bias.to(dtype=input.dtype) if self.bias is not None else None
        return F.linear(input, weight, bias)


def install_scaled_fp8_linears(
    model: nn.Module,
    safetensors_path: str | Path,
    key_map=None,
    matmul_mode: str = "auto",
) -> int:
    """Replace Linear modules whose checkpoint has a .weight_scale sibling."""
    if matmul_mode not in FP8_MATMUL_MODES:
        raise ValueError(f"matmul_mode must be one of {FP8_MATMUL_MODES}, got {matmul_mode!r}")
    count = 0
    with safe_open(str(safetensors_path), framework="pt", device="cpu") as sf:
        keys = set(sf.keys())
        bases = sorted(k[:-13] for k in keys if k.endswith(".weight_scale") and f"{k[:-13]}.weight" in keys)

    for base in bases:
        mapped_base = key_map(base) if key_map else base
        if isinstance(mapped_base, tuple):
            mapped_base = mapped_base[0]
        if mapped_base is None:
            continue
        module = _get_module(model, mapped_base)
        if not isinstance(module, nn.Linear):
            continue
        replacement = ScaledFP8Linear(
            module.in_features,
            module.out_features,
            module.bias is not None,
            device="meta",
            matmul_mode=matmul_mode,
        )
        _set_module(model, mapped_base, replacement)
        count += 1
    return count


def load_safetensors_into_model(
    model: nn.Module,
    safetensors_path: str | Path,
    device: str | torch.device,
    dtype: torch.dtype,
    *,
    key_map=None,
    strict: bool = True,
) -> dict:
    """Stream a safetensors checkpoint into an already-created model.

    key_map may return None to skip a key, a string new key, or a tuple
    (new_key, transform_fn) where transform_fn receives the tensor.
    """
    expected = set(model.state_dict().keys())
    loaded: set[str] = set()
    unexpected: list[str] = []
    device = torch.device(device)

    with safe_open(str(safetensors_path), framework="pt", device="cpu") as sf:
        for key in sf.keys():
            mapped = key_map(key) if key_map else key
            transform = None
            if mapped is None:
                continue
            if isinstance(mapped, tuple):
                mapped, transform = mapped

            if mapped not in expected:
                unexpected.append(mapped)
                continue

            tensor = sf.get_tensor(key)
            if transform is not None:
                tensor = transform(tensor)

            tensor = _cast_tensor_for_key(tensor, mapped, dtype)
            _assign_tensor(model, mapped, tensor.to(device))
            loaded.add(mapped)

    missing = sorted(expected - loaded)
    if strict and (missing or unexpected):
        sample_missing = ", ".join(missing[:8])
        sample_unexpected = ", ".join(unexpected[:8])
        raise RuntimeError(
            f"checkpoint/model mismatch: missing={len(missing)} [{sample_missing}] "
            f"unexpected={len(unexpected)} [{sample_unexpected}]"
        )

    return {"loaded": len(loaded), "missing": missing, "unexpected": unexpected}


def load_comfy_flux2_transformer(
    safetensors_path: str | Path,
    device: str | torch.device,
    dtype: torch.dtype,
    *,
    fp8_matmul: str = "auto",
) -> tuple[nn.Module, dict]:
    ensure_flux2_src_on_path()
    from flux2.model import Flux2, Flux2Params

    with torch.device("meta"):
        model = Flux2(Flux2Params())
    fp8_modules = install_scaled_fp8_linears(model, safetensors_path, matmul_mode=fp8_matmul)
    stats = load_safetensors_into_model(model, safetensors_path, device, dtype, strict=True)
    model.eval().requires_grad_(False)
    stats["scaled_fp8_linears"] = fp8_modules
    stats["fp8_matmul"] = fp8_matmul
    return model, stats


def load_comfy_mistral_text_encoder(
    safetensors_path: str | Path,
    device: str | torch.device,
    dtype: torch.dtype,
    *,
    config_repo: str = str(VENDORED_MISTRAL_TE),
    processor_repo: str = str(VENDORED_MISTRAL_TE),
    local_files_only: bool = True,
    fp8_matmul: str = "auto",
) -> tuple[nn.Module, object, dict]:
    from transformers import AutoConfig, AutoProcessor, Mistral3ForConditionalGeneration

    config = AutoConfig.from_pretrained(config_repo, subfolder="text_encoder", local_files_only=local_files_only)
    with torch.device("meta"):
        model = Mistral3ForConditionalGeneration(config)
    _trim_mistral_for_flux2(model, num_layers=30)
    fp8_modules = install_scaled_fp8_linears(
        model,
        safetensors_path,
        key_map=map_comfy_mistral_key,
        matmul_mode=fp8_matmul,
    )
    stats = load_safetensors_into_model(
        model,
        safetensors_path,
        device,
        dtype,
        key_map=map_comfy_mistral_key,
        strict=False,
    )
    if stats["missing"]:
        stats["missing_ignored"] = []
    if stats["missing"] or stats["unexpected"]:
        raise RuntimeError(
            f"text encoder checkpoint/model mismatch: missing={len(stats['missing'])}, "
            f"unexpected={len(stats['unexpected'])}"
        )

    processor = AutoProcessor.from_pretrained(processor_repo, subfolder="tokenizer", local_files_only=local_files_only)
    _materialize_mistral_runtime_buffers(model, device=torch.device(device))
    _raise_if_meta_tensors(model.model.language_model, "text encoder language tower")
    model.eval().requires_grad_(False)
    stats["scaled_fp8_linears"] = fp8_modules
    stats["fp8_matmul"] = fp8_matmul
    return model, processor, stats


def load_comfy_vae(
    safetensors_path: str | Path,
    device: str | torch.device,
    dtype: torch.dtype,
) -> tuple[nn.Module, dict]:
    ensure_flux2_src_on_path()
    from flux2.autoencoder import AutoEncoder, AutoEncoderParams

    with torch.device("meta"):
        ae = AutoEncoder(AutoEncoderParams())
    stats = load_safetensors_into_model(ae, safetensors_path, device, dtype, key_map=map_comfy_vae_key, strict=True)
    ae.eval().requires_grad_(False)
    return ae, stats


def map_comfy_vae_key(key: str):
    """Map Diffusers/Comfy Flux2 VAE keys to BFL AutoEncoder keys."""
    key = key.replace("conv_norm_out.", "norm_out.")
    key = key.replace("conv_shortcut.", "nin_shortcut.")
    key = re.sub(r"^encoder\.down_blocks\.(\d+)\.resnets\.(\d+)\.", r"encoder.down.\1.block.\2.", key)
    key = re.sub(r"^encoder\.down_blocks\.(\d+)\.downsamplers\.0\.", r"encoder.down.\1.downsample.", key)

    match = re.match(r"^decoder\.up_blocks\.(\d+)\.(.*)$", key)
    if match:
        idx = str(3 - int(match.group(1)))
        rest = re.sub(r"^resnets\.(\d+)\.", r"block.\1.", match.group(2))
        rest = re.sub(r"^upsamplers\.0\.", "upsample.", rest)
        key = f"decoder.up.{idx}.{rest}"

    key = re.sub(r"^(encoder|decoder)\.mid_block\.resnets\.0\.", r"\1.mid.block_1.", key)
    key = re.sub(r"^(encoder|decoder)\.mid_block\.resnets\.1\.", r"\1.mid.block_2.", key)
    key = re.sub(r"^(encoder|decoder)\.mid_block\.attentions\.0\.group_norm\.", r"\1.mid.attn_1.norm.", key)
    key = re.sub(r"^(encoder|decoder)\.mid_block\.attentions\.0\.to_q\.", r"\1.mid.attn_1.q.", key)
    key = re.sub(r"^(encoder|decoder)\.mid_block\.attentions\.0\.to_k\.", r"\1.mid.attn_1.k.", key)
    key = re.sub(r"^(encoder|decoder)\.mid_block\.attentions\.0\.to_v\.", r"\1.mid.attn_1.v.", key)
    key = re.sub(r"^(encoder|decoder)\.mid_block\.attentions\.0\.to_out\.0\.", r"\1.mid.attn_1.proj_out.", key)

    if key.startswith("quant_conv."):
        key = f"encoder.{key}"
    elif key.startswith("post_quant_conv."):
        key = f"decoder.{key}"

    def maybe_unsqueeze(tensor: torch.Tensor) -> torch.Tensor:
        return tensor[:, :, None, None] if tensor.ndim == 2 else tensor

    return key, maybe_unsqueeze


def map_comfy_mistral_key(key: str):
    """Map Comfy text-only Mistral keys into Mistral3ForConditionalGeneration."""
    if key in {"tekken_model", "scaled_fp8"}:
        return None
    if key.startswith("vision_tower.") or key.startswith("multi_modal_projector."):
        return None
    if key.startswith("model.norm.") or key.startswith("lm_head."):
        return None
    if key.startswith("model.layers."):
        layer_idx = int(key.split(".", 3)[2])
        if layer_idx >= 30:
            return None
        return "model.language_model.layers." + key[len("model.layers."):]
    if key.startswith("model.embed_tokens."):
        return "model.language_model.embed_tokens." + key[len("model.embed_tokens."):]
    return key


def _trim_mistral_for_flux2(model: nn.Module, num_layers: int) -> None:
    """Keep only the language layers Flux2 uses for hidden-state taps.

    The Comfy-Org text encoder file contains layers 0..29. Flux2 reads hidden
    states 10, 20, and 30, so keeping 30 decoder layers is sufficient and avoids
    meta tensors for the missing tail, final norm, and lm_head.
    """
    language_model = model.model.language_model
    language_model.layers = nn.ModuleList(list(language_model.layers[:num_layers]))
    language_model.norm = nn.Identity()
    model.model.vision_tower = nn.Identity()
    model.model.multi_modal_projector = nn.Identity()
    model.lm_head = nn.Identity()
    if hasattr(language_model, "config"):
        language_model.config.num_hidden_layers = num_layers
    if hasattr(model.config, "text_config"):
        model.config.text_config.num_hidden_layers = num_layers


def _materialize_mistral_runtime_buffers(model: nn.Module, device: torch.device) -> None:
    """Recreate non-persistent runtime buffers left meta by empty initialization."""
    rotary = model.model.language_model.rotary_emb
    model.model.language_model.rotary_emb = rotary.__class__(rotary.config, device=device)


def _raise_if_meta_tensors(module: nn.Module, label: str) -> None:
    meta = []
    for name, tensor in list(module.named_parameters()) + list(module.named_buffers()):
        if getattr(tensor, "is_meta", False):
            meta.append(name)
    if meta:
        sample = ", ".join(meta[:8])
        raise RuntimeError(f"{label} still has {len(meta)} meta tensor(s): {sample}")


def _cast_tensor_for_key(tensor: torch.Tensor, key: str, dtype: torch.dtype) -> torch.Tensor:
    if key.endswith(".weight") and tensor.dtype == torch.float8_e4m3fn:
        return tensor
    if key.endswith(".weight_scale") or key.endswith(".input_scale"):
        return tensor.to(torch.float32)
    if tensor.is_floating_point():
        return tensor.to(dtype)
    return tensor


def _can_use_native_scaled_mm(input: torch.Tensor, weight: torch.Tensor) -> bool:
    return (
        hasattr(torch, "_scaled_mm")
        and input.is_cuda
        and weight.is_cuda
        and input.dtype in (torch.bfloat16, torch.float16)
        and weight.dtype == torch.float8_e4m3fn
        and input.shape[-1] == weight.shape[-1]
    )


def _get_module(model: nn.Module, path: str) -> nn.Module:
    module = model
    for part in path.split("."):
        module = module[int(part)] if part.isdigit() else getattr(module, part)
    return module


def _set_module(model: nn.Module, path: str, value: nn.Module) -> None:
    parts = path.split(".")
    parent = _get_module(model, ".".join(parts[:-1])) if len(parts) > 1 else model
    name = parts[-1]
    if name.isdigit():
        parent[int(name)] = value
    else:
        setattr(parent, name, value)


def _assign_tensor(model: nn.Module, key: str, tensor: torch.Tensor) -> None:
    parts = key.split(".")
    module = _get_module(model, ".".join(parts[:-1])) if len(parts) > 1 else model
    name = parts[-1]
    if name in module._parameters:
        module._parameters[name] = nn.Parameter(tensor, requires_grad=False)
    elif name in module._buffers:
        module._buffers[name] = tensor
    else:
        raise KeyError(f"{key!r} is neither a parameter nor a buffer")


def ensure_flux2_src_on_path() -> None:
    root = Path(__file__).resolve().parents[3]
    flux2_src = root / "flux2" / "src"
    if flux2_src.is_dir() and str(flux2_src) not in sys.path:
        sys.path.insert(0, str(flux2_src))
