"""Stage 1 — Load SD 3.5 pipeline (transformer, VAE, text encoders).

Four modes:
  * "t2i"           -- StableDiffusion3Pipeline (default; existing behaviour).
  * "inpaint"       -- StableDiffusion3InpaintPipeline (img2img + mask, no ControlNet).
  * "cn-inpaint"    -- StableDiffusion3ControlNetPipeline (t2i + ControlNet).
                       The "inpaint" suffix is semantic: the caller composites
                       the generated image back onto the base image at the mask
                       in stage 6. This mirrors the original HandRefiner design
                       (SD 1.5 t2i + depth CN + mask composite). No scene
                       context -- the inpainter sees only the depth conditioning,
                       so the masked region is freehanded and can clash with
                       the surrounding scene (visible bbox-edge "acrylic-on-canvas"
                       artefacts).
  * "cn-inpaint-mc" -- True inpaint with multi-ControlNet. Loads the alimama
                       SD3-Controlnet-Inpainting CN (which gives scene context
                       + region-locked repaint via mask) AND a non-inpaint CN
                       (e.g. InstantX depth, for hand anatomy guidance).
                       Uses our `SD3MultiControlNetInpaintPipeline` subclass
                       which dispatches per-CN preparation correctly --
                       the alimama upstream class would otherwise feed the
                       depth-CN a 17-channel mask-packed latent that it does
                       not understand. See `_multi_cn_inpaint_pipeline.py`.

ControlNet: InstantX's SD3 ControlNets (Depth / Canny / Pose / Tile) were
trained against SD 3 medium but community reports confirm they work with
SD 3.5 medium / large via the same `SD3ControlNetModel` class. This is
empirically validated downstream in HandRefiner; if quality drops on a
specific SD 3.5 variant, fall back to `sd3.5-medium` which has the closest
training data overlap with SD 3.
"""

import os
import time

import torch
from diffusers import (
    SD3ControlNetModel,
    SD3MultiControlNetModel,
    StableDiffusion3ControlNetPipeline,
    StableDiffusion3Img2ImgPipeline,
    StableDiffusion3InpaintPipeline,
    StableDiffusion3Pipeline,
)

from _multi_cn_inpaint_pipeline import SD3MultiControlNetInpaintPipeline


# Model registry with default parameters.
#
# Skip Layer Guidance (SLG) defaults per Stability AI / community research:
#   - Medium: layers [7, 8, 9], scale 2.8, start 0.01, stop 0.2 (official model
#     card recommendation; reduces anatomy + composition failures).
#   - Large : SLG also helps anatomy on Large (sandner.art research). Default
#     ON with the same layer indices [7, 8, 9]. Set --no-skip-layer-guidance to
#     disable for tasks where SLG over-saturates output (e.g. very stylized art).
#   - Turbo : distilled, guidance_scale=0 -> SLG cannot be applied (CFG path
#     disabled in the pipeline when guidance_scale<=1).
#
# Default num_steps / guidance_scale match the official HF model cards. The
# scheduler `shift=3.0` is already baked into the checkpoint's
# scheduler_config.json -- no override needed at load time.
SD35_MODEL_INFO = {
    "sd3.5-medium": {
        "repo_id": "stabilityai/stable-diffusion-3.5-medium",
        "defaults": {"num_steps": 40, "guidance_scale": 4.5},
        "supports_negative_prompt": True,
        "skip_guidance_layers": [7, 8, 9],
    },
    "sd3.5-large": {
        "repo_id": "stabilityai/stable-diffusion-3.5-large",
        "defaults": {"num_steps": 28, "guidance_scale": 3.5},
        "supports_negative_prompt": True,
        # SLG also benefits Large per third-party research; default ON.
        # "skip_guidance_layers": [7, 8, 9],
        "skip_guidance_layers": None,
    },
    "sd3.5-large-turbo": {
        "repo_id": "stabilityai/stable-diffusion-3.5-large-turbo",
        "defaults": {"num_steps": 4, "guidance_scale": 0.0},
        "supports_negative_prompt": False,
        # Turbo runs at guidance_scale=0 -> CFG path is disabled, SLG inert.
        "skip_guidance_layers": None,
    },
}


# Known SD 3 ControlNet repos. Adding a new one here lets `--controlnet KEY`
# resolve to the full HF id; arbitrary HF repo paths also work via the same flag.
CONTROLNET_REGISTRY = {
    "depth":   "InstantX/SD3-Controlnet-Depth",
    "canny":   "InstantX/SD3-Controlnet-Canny",
    "pose":    "InstantX/SD3-Controlnet-Pose",
    "tile":    "InstantX/SD3-Controlnet-Tile",
    "inpaint": "alimama-creative/SD3-Controlnet-Inpainting",
}


def _resolve_controlnet_repo(arg: str) -> str:
    """Map short keys ('depth') to full HF repo ids; pass through full repo ids."""
    return CONTROLNET_REGISTRY.get(arg, arg)


def run(
    model_name: str = "sd3.5-medium",
    device: str = "cuda",
    cpu_offload: bool = True,
    drop_t5: bool = False,
    dtype: str = "bfloat16",
    mode: str = "t2i",
    controlnet: str | None = None,
    controlnets: list[str] | None = None,
) -> dict:
    """Load the SD 3.5 pipeline (and optional ControlNet) and return it.

    Args:
        model_name: Key into SD35_MODEL_INFO.
        device: Target device ("cuda" or "cpu").
        cpu_offload: Use enable_model_cpu_offload() to save VRAM.
        drop_t5: Drop T5-XXL text encoder to save ~5 GB VRAM.
        dtype: "bfloat16" or "float16".
        mode: "t2i" | "inpaint" | "cn-inpaint" | "cn-inpaint-mc".
        controlnet: Single ControlNet repo (id or registry key) for cn-inpaint.
        controlnets: List of ControlNet repos for cn-inpaint-mc; first MUST be
            an inpaint CN (extra_conditioning_channels>0, e.g. alimama),
            second+ can be any non-inpaint CN (e.g. depth).

    Returns dict with keys: pipe, model_info, model_name, device, mode,
        controlnet_repo (str or list[str] or None), timings.
    """
    if mode not in ("t2i", "img2img", "inpaint", "cn-inpaint", "cn-inpaint-mc"):
        raise ValueError(
            f"unsupported mode {mode!r}; expected t2i, img2img, inpaint, cn-inpaint, cn-inpaint-mc"
        )
    if mode == "cn-inpaint" and not controlnet:
        raise ValueError("mode='cn-inpaint' requires --controlnet (e.g. 'depth')")
    if mode == "cn-inpaint-mc":
        if not controlnets or len(controlnets) < 2:
            raise ValueError(
                "mode='cn-inpaint-mc' requires --controlnets with at least 2 entries "
                "(an inpaint CN like 'inpaint' plus one or more conditioning CNs like 'depth')"
            )

    model_info = SD35_MODEL_INFO[model_name]
    repo_id = model_info["repo_id"]
    torch_dtype = torch.bfloat16 if dtype == "bfloat16" else torch.float16
    timings: dict[str, float] = {}

    load_kwargs: dict = {"torch_dtype": torch_dtype}
    if drop_t5:
        load_kwargs["text_encoder_3"] = None
        load_kwargs["tokenizer_3"] = None

    cn_repo: str | list[str] | None = None
    cn_model = None

    t0 = time.time()
    if mode == "t2i":
        pipe = StableDiffusion3Pipeline.from_pretrained(repo_id, **load_kwargs)
    elif mode == "img2img":
        # Plain img2img -- low-strength polish / global re-roll. No mask.
        pipe = StableDiffusion3Img2ImgPipeline.from_pretrained(repo_id, **load_kwargs)
    elif mode == "inpaint":
        pipe = StableDiffusion3InpaintPipeline.from_pretrained(repo_id, **load_kwargs)
    elif mode == "cn-inpaint":
        cn_repo = _resolve_controlnet_repo(controlnet)
        cn_t0 = time.time()
        cn_model = SD3ControlNetModel.from_pretrained(cn_repo, torch_dtype=torch_dtype)
        timings["controlnet_load_s"] = round(time.time() - cn_t0, 4)
        pipe = StableDiffusion3ControlNetPipeline.from_pretrained(
            repo_id, controlnet=cn_model, **load_kwargs,
        )
    else:  # cn-inpaint-mc -- multi-CN inpaint (alimama-style + extras)
        cn_repos = [_resolve_controlnet_repo(c) for c in controlnets]
        cn_t0 = time.time()
        # alimama uses extra_conditioning_channels=1 baked into its config;
        # InstantX CNs default to 0. The from_pretrained call respects each
        # repo's config so we don't pass `extra_conditioning_channels` here.
        cn_models = [
            SD3ControlNetModel.from_pretrained(r, torch_dtype=torch_dtype)
            for r in cn_repos
        ]
        # Sanity check: at least one CN must be an inpaint CN
        # (extra_conditioning_channels>0) so the mask channel has a home.
        if not any(int(getattr(c.config, "extra_conditioning_channels", 0) or 0) > 0
                   for c in cn_models):
            raise ValueError(
                "mode='cn-inpaint-mc' needs at least one inpaint CN with "
                "extra_conditioning_channels>0 (e.g. alimama-creative/SD3-Controlnet-Inpainting). "
                f"Got CNs: {cn_repos}"
            )
        timings["controlnet_load_s"] = round(time.time() - cn_t0, 4)
        controlnet_arg = SD3MultiControlNetModel(cn_models)
        pipe = SD3MultiControlNetInpaintPipeline.from_pretrained(
            repo_id, controlnet=controlnet_arg, **load_kwargs,
        )
        cn_repo = cn_repos
        cn_model = cn_models

        # Dim-compatibility check: every CN's caption_projection_dim must match
        # the transformer's, otherwise the multi-CN block-residual sum +
        # transformer hidden-state add fails with a tensor-shape error mid-
        # generation (we burn ~40s of denoising before crashing). The classic
        # case: alimama + InstantX-depth (both SD3-medium-trained, hidden=1536)
        # paired with sd3.5-large (hidden=2432). Catch it at load time with a
        # clear message instead.
        transformer_dim = int(pipe.transformer.config.caption_projection_dim)
        bad_cns = [
            (r, int(c.config.caption_projection_dim))
            for r, c in zip(cn_repos, cn_models)
            if int(c.config.caption_projection_dim) != transformer_dim
        ]
        if bad_cns:
            details = ", ".join(f"{r} (hidden={d})" for r, d in bad_cns)
            raise ValueError(
                f"mode='cn-inpaint-mc' dim mismatch: transformer "
                f"{model_name} has hidden={transformer_dim}, but ControlNet(s) "
                f"{details} were trained at a different hidden dim. "
                f"Pair {model_name} with CNs trained at hidden={transformer_dim}, "
                f"or switch --sd35-model to a variant matching the CNs' hidden dim. "
                f"(Most public SD3 ControlNets are SD3-medium / SD3.5-medium "
                f"-- hidden=1536; use --sd35-model sd3.5-medium with them.)"
            )

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
        "cpu_offload": cpu_offload,
        "drop_t5": drop_t5,
        "dtype": dtype,
        "mode": mode,
        "controlnet_repo": cn_repo,
        "timings": timings,
    }


def get_manifest_inputs(
    model_name: str, device: str, cpu_offload: bool, drop_t5: bool,
    mode: str = "t2i", controlnet: str | None = None,
    controlnets: list[str] | None = None,
) -> dict:
    return {
        "model_name": model_name, "device": device, "cpu_offload": cpu_offload,
        "drop_t5": drop_t5, "mode": mode,
        "controlnet": _resolve_controlnet_repo(controlnet) if controlnet else None,
        "controlnets": [_resolve_controlnet_repo(c) for c in controlnets] if controlnets else None,
    }


def get_manifest_outputs(result: dict) -> dict:
    info = result["model_info"]
    return {
        "model_name": result["model_name"],
        "repo_id": info["repo_id"],
        "defaults": info["defaults"],
        "supports_negative_prompt": info["supports_negative_prompt"],
        "cpu_offload": result["cpu_offload"],
        "drop_t5": result["drop_t5"],
        "dtype": result["dtype"],
        "mode": result["mode"],
        "controlnet_repo": result.get("controlnet_repo"),
        "timings": result["timings"],
    }


def get_manifest_debug(result: dict) -> dict:
    pipe = result["pipe"]
    components = []
    if pipe.text_encoder is not None:
        components.append("clip_l")
    if pipe.text_encoder_2 is not None:
        components.append("clip_g")
    if pipe.text_encoder_3 is not None:
        components.append("t5_xxl")
    return {
        "loaded_text_encoders": components,
        "transformer_dtype": str(pipe.transformer.dtype),
        "pipeline_class": type(pipe).__name__,
    }
