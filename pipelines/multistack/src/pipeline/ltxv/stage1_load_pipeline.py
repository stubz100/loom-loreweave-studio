"""Stage 1 — Load LTX-Video 0.9.x pipeline (transformer, VAE, text encoder, scheduler).

ROCm-safe defaults:
  - PYTORCH_ALLOC_CONF=expandable_segments:True (set here if missing)
  - BF16 throughout
  - Standard SDPA attention (LTXV uses full self-attention, no variable-length
    masks — no AOTriton compile cost like HunyuanVideo-1.5)
  - 2B variants: NO offload (4 GB weights fit 16 GB VRAM comfortably; offload
    overhead exceeds benefit at this size)
  - 13B variants: model offload by default; sequential as safety fallback
  - FP8 / NVFP4 variants are SKIPPED entirely on AMD (NVIDIA-leaning kernels)

Per kb-ltx09.md Part 3 "Stage 1 — Load pipeline".
"""

import os
import time

# CRITICAL for ROCm: prevent HIP memory allocator fragmentation.
# Must be set BEFORE torch is imported via any diffusers path.
os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")

import torch


# Variant registry. Mirrors hunyuan's HUNYUAN_VARIANTS shape so the orchestrator
# can dispatch uniformly. `mode` here is the *primary* mode the variant
# represents in the registry — but the same checkpoint can be loaded via
# different pipeline classes (T2V/I2V/Condition) at runtime.
#
# Lesson learned (2026-05-26, verified against Diffusers docs):
# Lightricks publishes diffusers-ready repos for a SUBSET of LTXV variants —
# not every checkpoint that exists in the unified `Lightricks/LTX-Video` repo
# has a standalone diffusers fork. Confirmed-existing Diffusers repos:
#   - Lightricks/LTX-Video                       (unified — single-file path)
#   - Lightricks/LTX-Video-0.9.5
#   - Lightricks/LTX-Video-0.9.7-dev
#   - Lightricks/LTX-Video-0.9.7-distilled       ← 2B distilled (DEFAULT)
#   - Lightricks/LTX-Video-0.9.8-13B-distilled   ← only 0.9.8 diffusers repo
#   - Lightricks/ltxv-spatial-upscaler-0.9.7
# There is NO `Lightricks/LTX-Video-0.9.8-distilled` (2B) as a separate repo
# despite the inference-script CLI accepting "ltxv-2b-0.9.8-distilled" as a
# checkpoint key. For 2B-distilled-via-diffusers, 0.9.7-distilled is the
# practical choice. The 0.9.8 2B weights are reachable via from_single_file
# on the unified Lightricks/LTX-Video repo (future variant if needed).
LTXV_VARIANTS = {
    # ---- 2B 0.9.7 distilled — Phase 1+2 first-build target (DEFAULT) ----
    # The diffusers-ready 2B distilled checkpoint. One minor version behind
    # 0.9.8 but the only 2B distilled with a per-variant diffusers repo.
    #
    # Settings per diffusers LTX-Video docs (verified 2026-05-26):
    # "LTX-Video 0.9.7 distilled model is guidance and timestep-distilled to
    # speedup generation. It requires guidance_scale to be set to 1.0 and
    # num_inference_steps should be set between 4 and 10."
    #
    # IMPORTANT (lesson learned 2026-05-26 after a degraded smoke test):
    # 2B variants need `offload="model"` on 16 GB VRAM, NOT `"none"`. The
    # original kb assumption "2B fits in VRAM" counted only the 4 GB
    # transformer and forgot T5-XXL (~11 GB BF16 text encoder) + VAE (~1 GB).
    # Total pipeline weights are ~16 GB; with activations the resident
    # footprint at offload=none silently pages through system RAM and the
    # output quality degrades visibly. Same lesson kb-hunyuan.md learned for
    # 8.3B Hunyuan at ~16 GB VRAM.
    "2b_0.9.7_distilled": {
        "model_id": "Lightricks/LTX-Video-0.9.7-distilled",
        "primary_mode": "i2v",
        "offload": "model",
        "default_height": 480, "default_width": 704, "default_fps": 24,
        "default_num_frames": 121,
        "default_steps": 8,
        "default_guidance_scale": 1.0,   # distilled — CFG disabled
        "default_image_cond_noise_scale": 0.0,
        "default_decode_noise_scale": 0.0,
        "dtype": "bfloat16",
    },
    # ---- 2B 0.9.7 dev — quality reference at 2B scale ----
    # Non-distilled: needs CFG (5.0) and more steps (30).
    "2b_0.9.7_dev": {
        "model_id": "Lightricks/LTX-Video-0.9.7-dev",
        "primary_mode": "i2v",
        "offload": "model",
        "default_height": 480, "default_width": 704, "default_fps": 24,
        "default_num_frames": 121,
        "default_steps": 30,
        "default_guidance_scale": 5.0,   # base/dev — full CFG
        "default_image_cond_noise_scale": 0.0,
        "default_decode_noise_scale": 0.0,
        "dtype": "bfloat16",
    },
    # ---- 2B 0.9.5 — older stable fallback (non-distilled) ----
    "2b_0.9.5": {
        "model_id": "Lightricks/LTX-Video-0.9.5",
        "primary_mode": "i2v",
        "offload": "model",
        "default_height": 480, "default_width": 704, "default_fps": 24,
        "default_num_frames": 121,
        "default_steps": 30,
        "default_guidance_scale": 5.0,
        "default_image_cond_noise_scale": 0.0,
        "default_decode_noise_scale": 0.0,
        "dtype": "bfloat16",
    },
    # ---- 13B 0.9.8 distilled — Phase 4 stretch; needs offload ----
    # The ONLY 0.9.8 line with a diffusers-ready repo. Headline quality.
    # Same distilled-family settings as 0.9.7 distilled: guidance_scale=1.0,
    # 8 steps (4-10 range), decode_noise_scale=0.025, image_cond_noise_scale=0.0.
    "13b_0.9.8_distilled": {
        "model_id": "Lightricks/LTX-Video-0.9.8-13B-distilled",
        "primary_mode": "i2v",
        "offload": "model",
        "default_height": 480, "default_width": 704, "default_fps": 24,
        "default_num_frames": 121,
        "default_steps": 8,
        "default_guidance_scale": 1.0,   # distilled — CFG disabled
        "default_image_cond_noise_scale": 0.0,
        "default_decode_noise_scale": 0.0,
        "dtype": "bfloat16",
    },
}


# Convenience: short tag used in output filenames per variant.
def variant_tag(variant: str) -> str:
    """Map a variant key to a short tag suitable for output filenames."""
    return {
        "2b_0.9.7_distilled":  "2bd097",
        "2b_0.9.7_dev":        "2bv097",
        "2b_0.9.5":            "2b095",
        "13b_0.9.8_distilled": "13bd098",
    }.get(variant, variant)


# Pipeline class candidates per mode. LTX-Video ships THREE separate pipeline
# classes:
#   - LTXPipeline                  — T2V
#   - LTXImageToVideoPipeline      — I2V
#   - LTXConditionPipeline         — keyframes / extend / control (Phase 3+)
# Using the wrong class for T2V vs I2V silently drops the image kwarg, the same
# failure mode documented for HunyuanVideo-1.5.
_PIPELINE_CLASS_CANDIDATES_BY_MODE = {
    "t2v": ["LTXPipeline"],
    "i2v": ["LTXImageToVideoPipeline"],
    # Phase 3+: keyframes / extend / control all use LTXConditionPipeline
    "keyframes": ["LTXConditionPipeline"],
    "extend":    ["LTXConditionPipeline"],
    "control":   ["LTXConditionPipeline"],
}


def _resolve_diffusers_class(name_candidates: list[str]):
    """Resolve the first available diffusers class by name."""
    import diffusers
    tried = []
    for name in name_candidates:
        cls = getattr(diffusers, name, None)
        if cls is not None:
            return cls, name
        tried.append(name)
        # Some versions namespace these deeper.
        for submod_name in (
            "pipelines.ltx", "pipelines.ltx_video", "models.autoencoders",
        ):
            try:
                submod = __import__(f"diffusers.{submod_name}", fromlist=[name])
                cls = getattr(submod, name, None)
                if cls is not None:
                    return cls, name
            except ImportError:
                continue
    raise ImportError(
        f"None of these diffusers classes were found: {tried}. "
        f"Installed diffusers version: {diffusers.__version__}. "
        f"LTX-Video 0.9.x needs diffusers with LTXPipeline / "
        f"LTXImageToVideoPipeline / LTXConditionPipeline support."
    )


def run(
    variant: str = "2b_0.9.8_distilled",
    mode: str = "i2v",
    device: str = "cuda",
    offload_override: str | None = None,
    dtype_override: str | None = None,
) -> dict:
    """Load the LTX-Video pipeline and return it with metadata.

    Args:
        variant: Key into LTXV_VARIANTS.
        mode: "t2v" | "i2v" (Phase 1+2) | "keyframes" | "extend" | "control"
            (Phase 3+). Selects the pipeline class — the same checkpoint can
            be loaded into different pipeline classes.
        device: Target device ("cuda" — masquerades as HIP on ROCm).
        offload_override: Force offload strategy. None → use variant default.
            2B variants default to "none" (offload overhead > benefit at 4 GB).
            13B variants default to "model" or "sequential".
        dtype_override: Force dtype. None → use variant default (bfloat16).

    Returns dict with: pipe, variant, model_id, pipeline_class_name,
        vae_class_name, mode, offload_strategy, dtype, vae_dtype, device,
        defaults, timings, vram_after_load_gb.
    """
    if variant not in LTXV_VARIANTS:
        raise ValueError(
            f"unknown variant {variant!r}; must be one of {list(LTXV_VARIANTS)}"
        )
    if mode not in _PIPELINE_CLASS_CANDIDATES_BY_MODE:
        raise ValueError(
            f"unknown mode {mode!r}; must be one of "
            f"{list(_PIPELINE_CLASS_CANDIDATES_BY_MODE)}"
        )

    cfg = LTXV_VARIANTS[variant]
    model_id = cfg["model_id"]
    offload = offload_override or cfg["offload"]
    dtype_str = dtype_override or cfg["dtype"]
    torch_dtype = getattr(torch, dtype_str)
    timings: dict[str, float] = {}

    # Resolve pipeline class — different class per mode. T2V vs I2V vs Condition
    # are NOT the same class.
    pipeline_cls, pipeline_class_name = _resolve_diffusers_class(
        _PIPELINE_CLASS_CANDIDATES_BY_MODE[mode]
    )

    # Load the full pipeline. Lightricks' HF repos are flat — no subfolder, VAE
    # / transformer / text_encoder / scheduler all live at the root.
    t0 = time.time()
    pipe = pipeline_cls.from_pretrained(model_id, torch_dtype=torch_dtype)
    timings["pipeline_load_s"] = round(time.time() - t0, 4)

    vae_class_name = type(pipe.vae).__name__

    # VAE tiling + slicing. LTXV's Video-VAE has 1:192 compression so latent
    # tensors are small — VAE OOM risk is much lower than Wan2.2 / Hunyuan /
    # Mochi. But tiling+slicing is defensive default; cost is negligible.
    pipe.vae.enable_tiling()
    try:
        pipe.vae.enable_slicing()
    except Exception:
        # Some VAE variants may not expose enable_slicing; not fatal.
        pass

    # Apply offloading strategy.
    t2 = time.time()
    if offload == "sequential":
        pipe.enable_sequential_cpu_offload()
    elif offload == "model":
        pipe.enable_model_cpu_offload()
    elif offload == "none":
        pipe.to(device)
    else:
        raise ValueError(f"unknown offload strategy {offload!r}")
    timings["offload_setup_s"] = round(time.time() - t2, 4)

    vram_after_load_gb = 0.0
    if torch.cuda.is_available():
        vram_after_load_gb = round(torch.cuda.memory_allocated() / 1024**3, 2)

    print(
        f"[ltxv/stage1] Loaded {model_id} ({pipeline_class_name}) "
        f"in {timings['pipeline_load_s']}s, mode={mode}, offload={offload}, "
        f"VRAM after load: {vram_after_load_gb} GB"
    )

    return {
        "pipe": pipe,
        "variant": variant,
        "model_id": model_id,
        "pipeline_class_name": pipeline_class_name,
        "vae_class_name": vae_class_name,
        "mode": mode,
        "offload_strategy": offload,
        "dtype": dtype_str,
        "vae_dtype": dtype_str,
        "device": device,
        "default_steps": cfg["default_steps"],
        "default_guidance_scale": cfg["default_guidance_scale"],
        "default_image_cond_noise_scale": cfg.get("default_image_cond_noise_scale", 0.0),
        "default_decode_noise_scale": cfg.get("default_decode_noise_scale", 0.025),
        "default_height": cfg["default_height"],
        "default_width": cfg["default_width"],
        "default_num_frames": cfg["default_num_frames"],
        "default_fps": cfg["default_fps"],
        "vram_after_load_gb": vram_after_load_gb,
        "timings": timings,
    }


def get_manifest_inputs(
    variant: str,
    mode: str,
    device: str,
    offload_override: str | None,
) -> dict:
    return {
        "variant": variant,
        "mode": mode,
        "device": device,
        "offload_override": offload_override,
    }


def get_manifest_outputs(result: dict) -> dict:
    return {
        "variant": result["variant"],
        "model_id": result["model_id"],
        "pipeline_class": result["pipeline_class_name"],
        "vae_class": result["vae_class_name"],
        "mode": result["mode"],
        "offload_strategy": result["offload_strategy"],
        "dtype": result["dtype"],
        "vae_dtype": result["vae_dtype"],
        "device": result["device"],
        "vram_after_load_gb": result["vram_after_load_gb"],
        "timings": result["timings"],
    }


def get_manifest_debug(result: dict) -> dict:
    pipe = result["pipe"]
    return {
        "transformer_class": type(pipe.transformer).__name__,
        "transformer_dtype": str(pipe.transformer.dtype),
        "text_encoder_class": type(getattr(pipe, "text_encoder", None)).__name__,
        "vae_class": type(pipe.vae).__name__,
        "scheduler_class": type(pipe.scheduler).__name__,
    }
