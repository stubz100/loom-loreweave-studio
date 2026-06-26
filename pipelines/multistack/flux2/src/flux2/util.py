import base64
import io
import os
import re
import sys

import huggingface_hub
import torch
from PIL import Image
from safetensors.torch import load_file as load_sft

from .autoencoder import AutoEncoder, AutoEncoderParams
from .model import Flux2, Flux2Params, Klein4BParams, Klein9BParams
from .text_encoder import load_qwen3_embedder


def _dev_text_encoder_via_quantized(device="cuda"):
    """M2.5: `flux.2-dev` loads its (quantized Comfy) Mistral text encoder via
    pipeline.flux2.stage1_load_models (scaled_fp8), not this registry fn. Fail loud if reached so
    nothing silently falls back to the gated full-precision Mistral repo."""
    raise RuntimeError(
        "flux.2-dev text encoder is the quantized Comfy Mistral (M2.5); load it via "
        "pipeline.flux2.stage1_load_models.run(), not flux2.util.load_text_encoder()."
    )

# M2.5: the Flux2 VAE is shared by every variant. Load it from the PUBLIC Comfy-Org mirror
# (identical weights to the gated black-forest-labs/FLUX.2-dev `ae.safetensors` — verified
# tensor-by-tensor, kb-loom-p2-imp.md M2.5) so no FLUX.2-dev repo is needed for any variant.
# The Comfy file uses Diffusers key names; map_comfy_vae_key() remaps them onto the BFL
# AutoEncoder (+ the q/k/v/proj 2D->4D unsqueeze) so load_state_dict(strict=True) succeeds.
COMFY_FLUX2_VAE_REPO = "Comfy-Org/flux2-dev"
COMFY_FLUX2_VAE_FILE = "split_files/vae/flux2-vae.safetensors"

FLUX2_MODEL_INFO = {
    "flux.2-klein-4b": {
        "repo_id": "black-forest-labs/FLUX.2-klein-4B",
        "ae_repo_id": COMFY_FLUX2_VAE_REPO,
        "filename": "flux-2-klein-4b.safetensors",
        "filename_ae": COMFY_FLUX2_VAE_FILE,
        "params": Klein4BParams(),
        "text_encoder_load_fn": lambda device="cuda": load_qwen3_embedder(variant="4B", device=device),
        "model_path": "KLEIN_4B_MODEL_PATH",
        "defaults": {"guidance": 1.0, "num_steps": 4},
        "fixed_params": {"guidance", "num_steps"},  # guidance and timestep distilled
        "guidance_distilled": True,
    },
    "flux.2-klein-9b": {
        "repo_id": "black-forest-labs/FLUX.2-klein-9B",
        "ae_repo_id": COMFY_FLUX2_VAE_REPO,
        "filename": "flux-2-klein-9b.safetensors",
        "filename_ae": COMFY_FLUX2_VAE_FILE,
        "params": Klein9BParams(),
        "text_encoder_load_fn": lambda device="cuda": load_qwen3_embedder(variant="8B", device=device),
        "model_path": "KLEIN_9B_MODEL_PATH",
        "defaults": {"guidance": 1.0, "num_steps": 4},
        "fixed_params": {"guidance", "num_steps"},  # guidance and timestep distilled
        "guidance_distilled": True,
    },
    "flux.2-klein-9b-kv": {
        "repo_id": "black-forest-labs/FLUX.2-klein-9B-kv",
        "ae_repo_id": COMFY_FLUX2_VAE_REPO,
        "filename": "flux-2-klein-9b-kv.safetensors",
        "filename_ae": COMFY_FLUX2_VAE_FILE,
        "params": Klein9BParams(),
        "text_encoder_load_fn": lambda device="cuda": load_qwen3_embedder(variant="8B", device=device),
        "model_path": "KLEIN_9B_KV_MODEL_PATH",
        "defaults": {"guidance": 1.0, "num_steps": 4},
        "fixed_params": {"guidance", "num_steps"},  # guidance and timestep distilled
        "guidance_distilled": True,
        "use_kv_cache": True,
    },
    "flux.2-klein-base-4b": {
        "repo_id": "black-forest-labs/FLUX.2-klein-base-4B",
        "ae_repo_id": COMFY_FLUX2_VAE_REPO,
        "filename": "flux-2-klein-base-4b.safetensors",
        "filename_ae": COMFY_FLUX2_VAE_FILE,
        "params": Klein4BParams(),
        "text_encoder_load_fn": lambda device="cuda": load_qwen3_embedder(variant="4B", device=device),
        "model_path": "KLEIN_4B_BASE_MODEL_PATH",
        "defaults": {"guidance": 4.0, "num_steps": 50},
        "fixed_params": {},
        "guidance_distilled": False,
    },
    "flux.2-klein-base-9b": {
        "repo_id": "black-forest-labs/FLUX.2-klein-base-9B",
        "ae_repo_id": COMFY_FLUX2_VAE_REPO,
        "filename": "flux-2-klein-base-9b.safetensors",
        "filename_ae": COMFY_FLUX2_VAE_FILE,
        "params": Klein9BParams(),
        "text_encoder_load_fn": lambda device="cuda": load_qwen3_embedder(variant="8B", device=device),
        "model_path": "KLEIN_9B_BASE_MODEL_PATH",
        "defaults": {"guidance": 4.0, "num_steps": 50},
        "fixed_params": {},
        "guidance_distilled": False,
    },
    "flux.2-dev": {
        # M2.5: dev weights come from the quantized Comfy split files; the flow model is loaded by
        # pipeline.flux2.stage1_load_models (scaled_fp8), NOT load_flow_model (guarded below). This
        # entry remains for guidance_distilled/defaults metadata. No gated FLUX.2-dev repo.
        "repo_id": COMFY_FLUX2_VAE_REPO,
        "filename": "split_files/diffusion_models/flux2_dev_fp8mixed.safetensors",
        "filename_ae": COMFY_FLUX2_VAE_FILE,
        "params": Flux2Params(),
        "text_encoder_load_fn": _dev_text_encoder_via_quantized,
        "model_path": "FLUX2_MODEL_PATH",
        "defaults": {"guidance": 4.0, "num_steps": 8},
        "fixed_params": {},
        "guidance_distilled": True,
    },
}


def map_comfy_vae_key(key: str):
    """Map Diffusers/Comfy Flux2 VAE keys onto the BFL AutoEncoder, returning (new_key, transform).

    The transform unsqueezes the mid-block q/k/v/proj weights stored 2D in Comfy to the 4D conv
    layout BFL uses. Idempotent on already-BFL keys (they don't match the Comfy patterns), so it is
    safe to apply unconditionally. Canonical copy — pipeline.flux2.scaled_fp8 imports this one.
    """
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

    def maybe_unsqueeze(tensor):
        return tensor[:, :, None, None] if tensor.ndim == 2 else tensor

    return key, maybe_unsqueeze


def _is_comfy_vae_layout(sd: dict) -> bool:
    return any(".down_blocks." in k or ".up_blocks." in k or "conv_norm_out" in k for k in sd)


def _remap_comfy_vae_state_dict(sd: dict) -> dict:
    out = {}
    for k, v in sd.items():
        new_key, transform = map_comfy_vae_key(k)
        out[new_key] = transform(v)
    return out


def load_flow_model(model_name: str, debug_mode: bool = False, device: str | torch.device = "cuda") -> Flux2:
    config = FLUX2_MODEL_INFO[model_name.lower()]

    # M2.5: `flux.2-dev` is the quantized Comfy split-file path — its fp8mixed transformer can't be
    # loaded by a plain load_state_dict here. It's loaded by pipeline.flux2.stage1_load_models
    # (scaled_fp8); fail loud if anything reaches this full-precision loader for dev.
    if model_name.lower() == "flux.2-dev":
        raise RuntimeError(
            "flux.2-dev uses the quantized Comfy loader (M2.5); load it via "
            "pipeline.flux2.stage1_load_models.run(), not flux2.util.load_flow_model()."
        )

    if debug_mode:
        config["params"].depth = 1
        config["params"].depth_single_blocks = 1
    else:
        if config["model_path"] in os.environ:
            weight_path = os.environ[config["model_path"]]
            assert os.path.exists(weight_path), f"Provided weight path {weight_path} does not exist"
        else:
            # download from huggingface
            try:
                weight_path = huggingface_hub.hf_hub_download(
                    repo_id=config["repo_id"],
                    filename=config["filename"],
                    repo_type="model",
                )
            except huggingface_hub.errors.RepositoryNotFoundError:
                print(
                    f"Failed to access the model repository. Please check your internet "
                    f"connection and make sure you've access to {config['repo_id']}."
                    "Stopping."
                )
                sys.exit(1)

    if not debug_mode:
        with torch.device("meta"):
            model = Flux2(FLUX2_MODEL_INFO[model_name.lower()]["params"]).to(torch.bfloat16)
        print(f"Loading {weight_path} for the FLUX.2 weights")
        sd = load_sft(weight_path, device=str(device))
        model.load_state_dict(sd, strict=True, assign=True)
        return model.to(device)
    else:
        with torch.device(device):
            return Flux2(FLUX2_MODEL_INFO[model_name.lower()]["params"]).to(torch.bfloat16)


def load_text_encoder(model_name: str, device: str | torch.device = "cuda"):
    config = FLUX2_MODEL_INFO[model_name.lower()]
    return config["text_encoder_load_fn"](device=device)


def load_ae(model_name: str, device: str | torch.device = "cuda") -> AutoEncoder:
    config = FLUX2_MODEL_INFO[model_name.lower()]

    if "AE_MODEL_PATH" in os.environ:
        weight_path = os.environ["AE_MODEL_PATH"]
        assert os.path.exists(weight_path), f"Provided weight path {weight_path} does not exist"
    else:
        # download from huggingface
        try:
            ae_repo = config.get("ae_repo_id", config["repo_id"])
            weight_path = huggingface_hub.hf_hub_download(
                repo_id=ae_repo,
                filename=config["filename_ae"],
                repo_type="model",
            )
        except huggingface_hub.errors.RepositoryNotFoundError:
            print(
                f"Failed to access the model repository. Please check your internet "
                f"connection and make sure you've access to {config['repo_id']}."
                "Stopping."
            )
            sys.exit(1)

    if isinstance(device, str):
        device = torch.device(device)
    with torch.device("meta"):
        ae = AutoEncoder(AutoEncoderParams())

    print(f"Loading {weight_path} for the AutoEncoder weights")
    sd = load_sft(weight_path, device=str(device))
    if _is_comfy_vae_layout(sd):  # M2.5: Comfy VAE uses Diffusers key names — remap onto BFL AE
        sd = _remap_comfy_vae_state_dict(sd)
    ae.load_state_dict(sd, strict=True, assign=True)

    return ae.to(device)


def image_to_base64(image: Image.Image) -> str:
    """Convert PIL Image to base64 string."""
    buffered = io.BytesIO()
    image.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return img_str
