"""Model catalog — every variant + every adjustable parameter for the three image
pipelines loom drives (flux2 · sd35 · zimage), P1/M3.

A prototyping tool needs the **full** surface, not just our defaults (user request): all
model variants (with repo + gated flag + per-model defaults/capabilities) and every tunable
generation parameter each worker CLI accepts — including ones earlier adapters hardcoded.
This module is the single source the UI's model picker + parameter controls read, and that
`/generate` validates a request's `params` against (M3 step 4b).

**Mirrors the pipeline source** (`FLUX2_MODEL_INFO`, `SD35_MODEL_INFO`, `ZIMAGE_MODEL_INFO`
+ each `run_pipeline.py` argparser). A drift-guard test extracts the variant ids from the
vendored source and asserts this catalog stays in lockstep — treat a mismatch as "update the
catalog". Param specs are curated (type/default/range/applies-to) for validation + UI hints.

`gated` repos need a one-time HF license acceptance + an HF_TOKEN; `sd3.5-medium`, both Z-Image
variants are **ungated** (good for prototyping). flux2's text encoder is Qwen3 (FP8 on CUDA,
non-FP8 on the Win-ROCm target — see [[components]]._needs_fp8_workaround); only flux.2-dev
uses the (gated) Mistral encoder.
"""

from __future__ import annotations

try:
    from . import flux2_prompt
except ImportError:  # pragma: no cover - direct-run convenience
    import flux2_prompt  # type: ignore

CATALOG_VERSION = 1

# --- parameter specs ------------------------------------------------------------
# A param spec: name (loom key) -> {flag, type, default, ...constraints, modes, note}.
# `default=None` means "the worker resolves the per-model preset" (don't send the flag).
# `modes` limits a param to certain generation modes; omitted = all modes.

# Shared across the t2i/img2img/inpaint pipelines (zimage + sd35). flux2 has its own param set
# (it's a casting member AND the Stage-B `ref` mode — multi-ref expansion, §11/R147).
_COMMON_PARAMS: list[dict] = [
    {"name": "width", "flag": "--width", "type": "int", "default": 1024,
     "min": 256, "max": 2048, "step": 16, "note": "divisible by 16"},
    {"name": "height", "flag": "--height", "type": "int", "default": 1024,
     "min": 256, "max": 2048, "step": 16, "note": "divisible by 16"},
    {"name": "seed", "flag": "--seed", "type": "int", "default": None, "note": "random if unset"},
    {"name": "num_steps", "flag": "--num-steps", "type": "int", "default": None,
     "min": 1, "max": 200, "note": "defaults to the model preset"},
    {"name": "negative_prompt", "flag": "--negative-prompt", "type": "str", "default": None,
     "note": "only models with supports_negative_prompt"},
    {"name": "init_image", "flag": "--init-image", "type": "image", "default": None,
     "modes": ["img2img", "inpaint"], "note": "out/-relative; required for img2img/inpaint"},
    {"name": "strength", "flag": "--strength", "type": "float", "default": None,
     "min": 0.0, "max": 1.0, "modes": ["img2img", "inpaint"], "note": "img2img sweep / inpaint repaint"},
]


def _catalog() -> dict:
    return {
        # ── flux2 ──────────────────────────────────────────────────────────────
        "flux2": {
            "loom_access": "Stage-B identity-preserving expansion via the `ref` mode (multi-ref, "
                           "§11/R147 — the hero rides as an in-context reference); also a `multi` "
                           "casting member (preset-driven).",
            "variants": [
                {"id": "flux.2-klein-4b", "repo_id": "black-forest-labs/FLUX.2-klein-4B",
                 "ae_repo_id": "black-forest-labs/FLUX.2-dev", "text_encoder": "Qwen/Qwen3-4B",
                 "gated": True, "distilled": True, "guidance_fixed": True,
                 "defaults": {"num_steps": 4, "guidance": 1.0},
                 "note": "fast preset flow model; guidance+steps distilled (fixed)"},
                {"id": "flux.2-klein-9b", "repo_id": "black-forest-labs/FLUX.2-klein-9B",
                 "ae_repo_id": "black-forest-labs/FLUX.2-dev", "text_encoder": "Qwen/Qwen3-8B",
                 "gated": True, "distilled": True, "guidance_fixed": True,
                 "defaults": {"num_steps": 4, "guidance": 1.0},
                 "note": "refined preset flow model"},
                {"id": "flux.2-klein-9b-kv", "repo_id": "black-forest-labs/FLUX.2-klein-9B-kv",
                 "ae_repo_id": "black-forest-labs/FLUX.2-dev", "text_encoder": "Qwen/Qwen3-8B",
                 "gated": True, "distilled": True, "guidance_fixed": True,
                 "defaults": {"num_steps": 4, "guidance": 1.0},
                 "note": "9B with kv-cache"},
                {"id": "flux.2-klein-base-4b", "repo_id": "black-forest-labs/FLUX.2-klein-base-4B",
                 "ae_repo_id": "black-forest-labs/FLUX.2-dev", "text_encoder": "Qwen/Qwen3-4B",
                 "gated": True, "distilled": False, "guidance_fixed": False,
                 "defaults": {"num_steps": 50, "guidance": 4.0},
                 "note": "non-distilled base; guidance+steps adjustable"},
                {"id": "flux.2-klein-base-9b", "repo_id": "black-forest-labs/FLUX.2-klein-base-9B",
                 "ae_repo_id": "black-forest-labs/FLUX.2-dev", "text_encoder": "Qwen/Qwen3-8B",
                 "gated": True, "distilled": False, "guidance_fixed": False,
                 "defaults": {"num_steps": 50, "guidance": 4.0},
                 "note": "non-distilled base 9B"},
                # flux.2-dev IS guidance-distilled (uses a guidance EMBEDDING, no CFG negative) but
                # guidance is NOT fixed — default 4.0 is a real adjustable knob (worker fixed_params
                # is empty). So `guidance_fixed=False`: the Sampling guard must NOT warn on dev.
                # M0e Part A — dev's `defaults` carry a per-variant **size** (512²): dev is the
                # heaviest flux2 variant on 16 GB ROCm and its cost scales with output tokens²
                # (resolution), so the efficient workflow is author-small-then-upscale. The size
                # default only moves the UNSET default (explicit dims still win); model_size_default()
                # reads it and the /generate resolution + UI placeholder consult it.
                {"id": "flux.2-dev", "repo_id": "black-forest-labs/FLUX.2-dev",
                 "ae_repo_id": "black-forest-labs/FLUX.2-dev", "text_encoder": "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
                 "gated": True, "distilled": True, "guidance_fixed": False,
                 "defaults": {"num_steps": 50, "guidance": 4.0, "width": 512, "height": 512},
                 "note": "full dev model; Mistral-24B VLM encoder (gated); parses structured JSON "
                         "prompts; defaults to 512² (far faster at low res — upscale after)"},
            ],
            "params": [
                {"name": "model_name", "flag": "--model-name", "type": "enum", "default": "flux.2-klein-4b"},
                {"name": "width", "flag": "--width", "type": "int", "default": 1360, "min": 256, "max": 2048, "step": 16},
                {"name": "height", "flag": "--height", "type": "int", "default": 768, "min": 256, "max": 2048, "step": 16},
                {"name": "seed", "flag": "--seed", "type": "int", "default": None},
                {"name": "num_steps", "flag": "--num-steps", "type": "int", "default": None,
                 "note": "fixed for distilled variants (klein/dev); adjustable on -base"},
                {"name": "guidance", "flag": "--guidance", "type": "float", "default": None, "min": 0.0, "max": 30.0,
                 "note": "flux2 uses --guidance (not --guidance-scale); fixed for distilled variants"},
                {"name": "init_image", "flag": "--init-image", "type": "image", "default": None, "modes": ["img2img"]},
                {"name": "strength", "flag": "--strength", "type": "float", "default": 0.25, "min": 0.0, "max": 1.0,
                 "modes": ["img2img"], "note": "0.20–0.25 polish, higher to re-roll"},
                {"name": "cpu_offload", "flag": "--cpu-offload", "type": "flag", "default": False},
            ],
            # `ref` (§11): t2i conditioned on the hero as a reference image (identity-preserving
            # Stage-B expansion). `ref_images` rides the batch jobs-file shared block (the worker
            # encodes it once), not a per-cell catalog flag — so it's not in `params`.
            "modes": ["t2i", "img2img", "ref"],
        },
        # ── sd35 ───────────────────────────────────────────────────────────────
        "sd35": {
            "loom_access": "standalone adapter (Stage-B img2img/inpaint) + a `multi` casting member.",
            "variants": [
                {"id": "sd3.5-medium", "repo_id": "stabilityai/stable-diffusion-3.5-medium",
                 "gated": False, "supports_negative_prompt": True, "skip_guidance_layers": [7, 8, 9],
                 "defaults": {"num_steps": 40, "guidance_scale": 4.5},
                 "note": "UNGATED — good for prototyping; SLG on; closest SD3-CN overlap"},
                {"id": "sd3.5-large", "repo_id": "stabilityai/stable-diffusion-3.5-large",
                 "gated": True, "supports_negative_prompt": True, "skip_guidance_layers": None,
                 "defaults": {"num_steps": 28, "guidance_scale": 3.5}, "note": "refined preset member"},
                {"id": "sd3.5-large-turbo", "repo_id": "stabilityai/stable-diffusion-3.5-large-turbo",
                 "gated": True, "supports_negative_prompt": False, "skip_guidance_layers": None,
                 "defaults": {"num_steps": 4, "guidance_scale": 0.0},
                 "note": "fast preset member; guidance 0 (CFG/SLG inert)"},
            ],
            "params": [
                {"name": "model_name", "flag": "--model-name", "type": "enum", "default": "sd3.5-medium"},
                *_COMMON_PARAMS,
                {"name": "guidance_scale", "flag": "--guidance-scale", "type": "float", "default": None,
                 "min": 0.0, "max": 30.0, "note": "defaults to the model preset"},
                {"name": "mask_image", "flag": "--mask-image", "type": "image", "default": None,
                 "modes": ["inpaint"], "note": "white=repaint, black=preserve; required for inpaint"},
                {"name": "prompt_3", "flag": "--prompt-3", "type": "str", "default": None,
                 "note": "optional longer T5-only prompt (CLIP gets --prompt)"},
                {"name": "negative_prompt_3", "flag": "--negative-prompt-3", "type": "str", "default": None},
                {"name": "max_sequence_length", "flag": "--max-sequence-length", "type": "int", "default": 512,
                 "min": 64, "max": 512, "note": "T5 context; 512 for prose prompts"},
                {"name": "no_skip_layer_guidance", "flag": "--no-skip-layer-guidance", "type": "flag", "default": False,
                 "note": "SLG is ON by default for medium/large (anatomy/composition)"},
                {"name": "skip_layer_guidance_scale", "flag": "--skip-layer-guidance-scale", "type": "float", "default": 2.8},
                {"name": "skip_layer_guidance_start", "flag": "--skip-layer-guidance-start", "type": "float", "default": 0.01},
                {"name": "skip_layer_guidance_stop", "flag": "--skip-layer-guidance-stop", "type": "float", "default": 0.2},
                {"name": "drop_t5", "flag": "--drop-t5", "type": "flag", "default": False,
                 "note": "drop T5-XXL to save ~5 GB VRAM (hurts long prompts)"},
                {"name": "dtype", "flag": "--dtype", "type": "enum", "default": "bfloat16",
                 "choices": ["bfloat16", "float16"]},
                {"name": "no_cpu_offload", "flag": "--no-cpu-offload", "type": "flag", "default": False},
                # ControlNet modes (cn-inpaint / cn-inpaint-mc) are advanced — wired with postproc (M6+).
                {"name": "controlnet", "flag": "--controlnet", "type": "str", "default": None,
                 "modes": ["cn-inpaint"], "advanced": True},
                {"name": "control_image", "flag": "--control-image", "type": "image", "default": None,
                 "modes": ["cn-inpaint"], "advanced": True},
                {"name": "controlnets", "flag": "--controlnets", "type": "str", "default": None,
                 "modes": ["cn-inpaint-mc"], "advanced": True},
                {"name": "control_images", "flag": "--control-images", "type": "str", "default": None,
                 "modes": ["cn-inpaint-mc"], "advanced": True},
                {"name": "cn_scale", "flag": "--cn-scale", "type": "str", "default": "1.0",
                 "modes": ["cn-inpaint", "cn-inpaint-mc"], "advanced": True},
            ],
            "modes": ["t2i", "img2img", "inpaint", "cn-inpaint", "cn-inpaint-mc"],
        },
        # ── zimage ───────────────────────────────────────────────────────────────
        "zimage": {
            "loom_access": "standalone adapter (P0 t2i + M3 img2img/inpaint) + a `multi` casting member.",
            "variants": [
                {"id": "zimage-turbo", "repo_id": "Tongyi-MAI/Z-Image-Turbo",
                 "gated": False, "supports_negative_prompt": False, "supports_cfg_normalization": False,
                 "defaults": {"num_steps": 9, "guidance_scale": 0.0},
                 "note": "UNGATED (Apache); 8 NFE distilled, no CFG"},
                {"id": "zimage-base", "repo_id": "Tongyi-MAI/Z-Image",
                 "gated": False, "supports_negative_prompt": True, "supports_cfg_normalization": True,
                 "defaults": {"num_steps": 50, "guidance_scale": 4.0},
                 "note": "UNGATED (Apache); 28–50 steps, supports negatives + cfg-norm"},
            ],
            "params": [
                {"name": "model_name", "flag": "--model-name", "type": "enum", "default": "zimage-turbo"},
                *_COMMON_PARAMS,
                {"name": "guidance_scale", "flag": "--guidance-scale", "type": "float", "default": None,
                 "min": 0.0, "max": 30.0, "note": "defaults to the model preset"},
                {"name": "mask_image", "flag": "--mask-image", "type": "image", "default": None,
                 "modes": ["inpaint"], "note": "white=repaint, black=preserve; required for inpaint"},
                {"name": "cfg_normalization", "flag": "--cfg-normalization", "type": "flag", "default": False,
                 "note": "zimage-base only; prefer for realism, off for stylism"},
                {"name": "cfg_truncation", "flag": "--cfg-truncation", "type": "float", "default": None,
                 "min": 0.0, "max": 1.0, "note": "zimage-base only; <1.0 runs final steps unconditional"},
                {"name": "dtype", "flag": "--dtype", "type": "enum", "default": "bfloat16",
                 "choices": ["bfloat16", "float16"]},
                {"name": "attention_backend", "flag": "--attention-backend", "type": "enum", "default": None,
                 "choices": ["native_flash", "math", "flash", "_flash_3"],
                 "note": "on ROCm use native_flash or leave unset; avoid flash/_flash_3"},
                {"name": "no_cpu_offload", "flag": "--no-cpu-offload", "type": "flag", "default": False},
            ],
            "modes": ["t2i", "img2img", "inpaint"],
        },
        # ── ltxv (video sketch — P1/M7: i2v from the hero → chained frame harvest) ──
        "ltxv": {
            "loom_access": "Stage-B video-sketch harvest (M7): cheap low-res i2v motion "
                           "from the hero ★, then a chained frame_harvest pass extracts "
                           "stills — multi-angle/pose coverage without 3D (R11).",
            "variants": [
                {"id": "2b_0.9.7_distilled", "repo_id": "Lightricks/LTX-Video-0.9.7-distilled",
                 "gated": False, "defaults": {"num_steps": 8, "guidance_scale": 1.0},
                 "note": "DEFAULT — 2B distilled (CFG off, 4–10 steps); 704×480 native; "
                         "⚠ needs offload=model on 16 GB (T5-XXL text encoder)"},
                {"id": "2b_0.9.7_dev", "repo_id": "Lightricks/LTX-Video-0.9.7-dev",
                 "gated": False, "defaults": {"num_steps": 30, "guidance_scale": 5.0},
                 "note": "2B non-distilled — quality reference, full CFG"},
                {"id": "2b_0.9.5", "repo_id": "Lightricks/LTX-Video-0.9.5",
                 "gated": False, "defaults": {"num_steps": 30, "guidance_scale": 5.0},
                 "note": "older stable fallback"},
                {"id": "13b_0.9.8_distilled", "repo_id": "Lightricks/LTX-Video-0.9.8-13B-distilled",
                 "gated": False, "defaults": {"num_steps": 8, "guidance_scale": 1.0},
                 "note": "13B distilled — headline quality, heavy"},
            ],
            "params": [
                {"name": "model_name", "flag": "--variant", "type": "enum",
                 "default": "2b_0.9.7_distilled",
                 "note": "the worker calls this --variant (not --model-name)"},
                {"name": "width", "flag": "--width", "type": "int", "default": 704,
                 "min": 256, "max": 1280, "step": 32, "note": "low-res sketch — 704×480 native"},
                {"name": "height", "flag": "--height", "type": "int", "default": 480,
                 "min": 256, "max": 1280, "step": 32},
                {"name": "seed", "flag": "--seed", "type": "int", "default": None},
                {"name": "num_frames", "flag": "--num-frames", "type": "int", "default": 121,
                 "min": 9, "max": 257, "note": "~5 s at 24 fps"},
                {"name": "fps", "flag": "--fps", "type": "int", "default": 24, "min": 8, "max": 30},
                {"name": "num_steps", "flag": "--steps", "type": "int", "default": None,
                 "note": "distilled 4–10; dev/0.9.5 ~30 (variant default when unset)"},
                {"name": "guidance_scale", "flag": "--guidance-scale", "type": "float",
                 "default": None, "min": 0.0, "max": 15.0,
                 "note": "distilled variants need 1.0 (CFG off)"},
                {"name": "negative_prompt", "flag": "--negative-prompt", "type": "str",
                 "default": None},
                {"name": "offload", "flag": "--offload", "type": "enum", "default": None,
                 "choices": ["model", "sequential", "none"],
                 "note": "leave unset → variant default ('model' — required on 16 GB)"},
                {"name": "init_image", "flag": "--init-image", "type": "image",
                 "default": None, "modes": ["i2v"]},
            ],
            "modes": ["i2v"],
        },
        # ── birefnet (postproc: subject matting — P1/M3.5, first postproc-class) ──
        "birefnet": {
            "loom_access": "standalone postproc adapter — hero subject matte → the Stage-B "
                           "background-inpaint mask (realize=\"mixed\").",
            "variants": [
                {"id": "birefnet", "repo_id": "ZhengPeng7/BiRefNet", "gated": False,
                 "defaults": {"resolution": 1024},
                 "note": "MIT; general 1024 matting — the loom default. Transformers repo "
                         "(trust_remote_code; probe config.json, NOT model_index.json)"},
                {"id": "birefnet-hr", "repo_id": "ZhengPeng7/BiRefNet_HR", "gated": False,
                 "defaults": {"resolution": 2048},
                 "note": "MIT; high-res 2048 variant (slower; promoted hi-res refs)"},
            ],
            "params": [
                {"name": "model_name", "flag": "--model-name", "type": "enum", "default": "birefnet"},
                {"name": "resolution", "flag": "--resolution", "type": "int", "default": None,
                 "min": 256, "max": 2048, "note": "inference square; defaults to the variant's native"},
                {"name": "threshold", "flag": "--threshold", "type": "float", "default": 0.5,
                 "min": 0.0, "max": 1.0, "note": "subject binarization for the bg mask"},
                {"name": "dilate_px", "flag": "--dilate-px", "type": "int", "default": 12,
                 "min": 0, "max": 64, "note": "grow the protected subject region (edge safety)"},
                {"name": "feather_px", "flag": "--feather-px", "type": "int", "default": 0,
                 "min": 0, "max": 64, "note": "soften the bg mask outward (0 = hard edge)"},
                {"name": "dtype", "flag": "--dtype", "type": "enum", "default": "float32",
                 "choices": ["float32", "float16"]},
            ],
            "modes": ["matte"],
        },
    }


CATALOG = _catalog()

# --- clean/polish post-passes (orchestrator-chained, ANY pipeline) ----------------
# User request 2026-06-11: clean/polish are POST-PROCESS passes, available on every
# generation run (zimage/sd35 singles, multi casts, Stage-B datasets) — not a multi-only
# in-worker feature. They are **not worker CLI flags**: `/generate` extracts them from
# the params channel (`post: True` marks them; emit_argv skips them) and, when the
# parent job finishes, the runner CHAINS one batch img2img job per pass over the
# parent's outputs (model loads once; tiles stream per item like any other run —
# this also fixes the old in-worker passes never streaming, they were piped).
# Backends are the wired standalone adapters (zimage · sd35); a flux2-img2img backend
# needs the §11 standalone-flux2 spike first.
_POST_BACKENDS = ["zimage", "sd35"]
POST_PARAMS: list[dict] = [
    {"name": "clean", "flag": None, "type": "flag", "default": False, "post": True,
     "note": "chained img2img clean pass over every output of this run"},
    {"name": "clean_backend", "flag": None, "type": "enum", "default": "zimage",
     "choices": _POST_BACKENDS, "post": True, "note": "requires clean"},
    {"name": "clean_model", "flag": None, "type": "enum", "default": None, "choices": [],
     "post": True,   # choices auto-filled below (zimage + sd35 variants)
     "note": "model variant for the clean backend (default = the backend's default); "
             "must belong to the chosen clean_backend family; requires clean"},
    {"name": "clean_strength", "flag": None, "type": "float", "default": 0.5,
     "min": 0.0, "max": 1.0, "post": True, "note": "requires clean"},
    {"name": "clean_prompt", "flag": None, "type": "str", "default": None, "post": True,
     "note": "defaults to each image's own prompt; requires clean"},
    {"name": "clean_negative_prompt", "flag": None, "type": "str", "default": None,
     "post": True, "note": "requires clean"},
    {"name": "polish", "flag": None, "type": "flag", "default": False, "post": True,
     "note": "chained low-strength img2img polish pass (runs after clean when both on)"},
    {"name": "polish_backend", "flag": None, "type": "enum", "default": "sd35",
     "choices": _POST_BACKENDS, "post": True, "note": "requires polish"},
    {"name": "polish_model", "flag": None, "type": "enum", "default": None, "choices": [],
     "post": True,
     "note": "model variant for the polish backend (default = the backend's default); "
             "must belong to the chosen polish_backend family; requires polish"},
    {"name": "polish_strength", "flag": None, "type": "float", "default": 0.22,
     "min": 0.0, "max": 1.0, "post": True,
     "note": "0.20–0.25 typical, >0.30 degrades; requires polish"},
    {"name": "polish_prompt", "flag": None, "type": "str", "default": None, "post": True,
     "note": "defaults to each image's own prompt; requires polish"},
    {"name": "polish_negative_prompt", "flag": None, "type": "str", "default": None,
     "post": True, "note": "requires polish"},
    {"name": "polish_seed", "flag": None, "type": "int", "default": None, "post": True,
     "note": "default: each image's own seed; requires polish"},
    # M6 — GFPGAN face-restore pass (CPU onnx, no diffusion backbone): fixes soft faces,
    # esp. the 128px softness the identity swap leaves on close-ups → it always runs
    # AFTER identity in the chain. No-face images pass through unchanged.
    {"name": "restore", "flag": None, "type": "flag", "default": False, "post": True,
     "note": "chained face-restore pass (GFPGAN onnx, CPU) over every output of this run"},
    {"name": "restore_blend", "flag": None, "type": "float", "default": 0.8,
     "min": 0.0, "max": 1.0, "post": True,
     "note": "restored↔original blend per face (1 = fully restored); requires restore"},
]

# The post-passes apply to every generation surface: zimage/sd35 singles + Stage-B
# datasets (their catalogs) and multi casts (MULTI_PARAMS below).
for _p in ("zimage", "sd35"):
    CATALOG[_p]["params"].extend(POST_PARAMS)

# Post-pass model DROPDOWNS (user request 2026-06-11 — was freetext): the valid set is
# the union of the two backend families; family consistency vs the chosen *_backend is
# enforced at /generate (a zimage model with an sd35 backend → 422). The POST_PARAMS
# dicts are shared by reference across zimage/sd35/multi, so one fill covers all.
_POST_MODEL_IDS = [v["id"] for _b in _POST_BACKENDS for v in CATALOG[_b]["variants"]]
for _spec_p in POST_PARAMS:
    if _spec_p["name"] in ("clean_model", "polish_model"):
        _spec_p["choices"] = _POST_MODEL_IDS

# --- flux2 sampling presets (M0d Part B) ----------------------------------------
# One-click (model_name + num_steps + guidance) combos fronting the flux2 generation
# knobs, so an author fixes loose pose adherence without hand-tuning each field.
# Researched values (kb-loom-p2 §12 "M0d"): the default **distilled** klein follows the
# prompt loosely and IGNORES guidance (CFG pinned ≈1); the non-distilled `-base` variants
# honour guidance + more steps → the actual pose fix. Each preset's `model_name` MUST be a
# real flux2 variant (asserted in tests). The UI also offers an implicit **Custom** (no
# preset row) = whatever the hand-set guidance/num_steps fields hold. `default` marks the
# one selected on load (speed); `recommended` marks the one-click pose fix.
FLUX2_SAMPLING_PRESETS: list[dict] = [
    {"id": "fast", "label": "Fast (draft)", "model_name": "flux.2-klein-4b",
     "num_steps": 4, "guidance": 1.0, "default": True,
     "note": "quick exploration; distilled — loose adherence, guidance inert"},
    {"id": "balanced", "label": "Balanced", "model_name": "flux.2-klein-base-4b",
     "num_steps": 24, "guidance": 4.0, "recommended": True,
     "note": "non-distilled base — good pose/prompt adherence; the one-click pose fix"},
    {"id": "quality", "label": "Quality", "model_name": "flux.2-klein-base-9b",
     "num_steps": 40, "guidance": 4.5,
     "note": "strongest adherence; slower + needs cpu-offload on 16 GB"},
    {"id": "dev", "label": "Dev / JSON", "model_name": "flux.2-dev",
     "num_steps": 50, "guidance": 4.5,
     "note": "Mistral-VLM; best true-JSON prompting (gated weights, heaviest)"},
]
# Attach to the flux2 catalog entry so GET /models serves it alongside variants/params.
CATALOG["flux2"]["sampling_presets"] = FLUX2_SAMPLING_PRESETS

# M0d Part C — the angle→camera/pose directive vocabulary, served so the `flux.2-dev` JSON
# prompt tree's pose-preset dropdown reuses Part A's table (single source, no FE drift).
CATALOG["flux2"]["angle_directives"] = dict(flux2_prompt.ANGLE_DIRECTIVES)


def flux2_sampling_presets() -> list[dict]:
    """The flux2 Sampling pull-down rows (M0d Part B) — model_name+steps+guidance combos."""
    return FLUX2_SAMPLING_PRESETS


# --- multi (casting) tunables ---------------------------------------------------
# `multi` is not an image-model entry (no variants of its own — the ideation preset fixes
# the member models), but its tunables ARE catalog-served so the UI's param drawer + the
# /generate params channel treat it uniformly. Loom invokes the `ideate` subcommand only;
# clean/polish happen as the chained post-passes above (2026-06-11 — the in-worker batch
# passes were piped, so their tiles never streamed).
# NOT exposable without a worker CLI change (vendored, R162): per-member ideate model/
# steps/guidance overrides — the preset (fast|refined) fixes those.
MULTI_PARAMS: list[dict] = [
    {"name": "width", "flag": "--width", "type": "int", "default": 1024,
     "min": 256, "max": 2048, "step": 16, "note": "divisible by 16"},
    {"name": "height", "flag": "--height", "type": "int", "default": 1024,
     "min": 256, "max": 2048, "step": 16, "note": "divisible by 16"},
    {"name": "seed", "flag": "--seed", "type": "int", "default": None,
     "note": "random if unset; candidate i uses seed+i"},
    *POST_PARAMS,
]

MULTI_ENTRY = {
    "loom_access": "Stage-A casting (one cast → a pool of candidates). The ideation preset "
                   "(fast|refined) fixes the member models; clean/polish chain as "
                   "post-passes over the pool.",
    "variants": [],
    "params": MULTI_PARAMS,
    "modes": ["ideate"],
}


def catalog_for_api() -> dict:
    """What GET /models serves: the image-model catalog + the multi casting tunables."""
    return {**CATALOG, "multi": MULTI_ENTRY}

# Fill each `model_name` param's `choices` from its pipeline's variants, so an unknown model
# is rejected by validate_params (enum) too — and /models advertises the valid set. Auto-synced
# (no hand-maintained list to drift from the variants above).
for _spec in CATALOG.values():
    _ids = [v["id"] for v in _spec.get("variants", [])]
    for _p in _spec.get("params", []):
        if _p["name"] == "model_name":
            _p["choices"] = _ids


def pipelines() -> list[str]:
    return list(CATALOG)


def variants(pipeline: str) -> list[dict]:
    return CATALOG.get(pipeline, {}).get("variants", [])


def variant_ids(pipeline: str) -> list[str]:
    return [v["id"] for v in variants(pipeline)]


def params(pipeline: str) -> list[dict]:
    if pipeline == "multi":
        return MULTI_PARAMS
    return CATALOG.get(pipeline, {}).get("params", [])


def param_default(pipeline: str, name: str):
    """The catalog default for one param — what the UI drawer advertises, and therefore
    what an UNSET request must resolve to (M6 review #2: display must equal reality)."""
    for prm in params(pipeline):
        if prm["name"] == name:
            return prm.get("default")
    return None


def model_size_default(pipeline: str, model_name: str | None) -> tuple[int | None, int | None]:
    """The per-VARIANT (width, height) default override for a model, or (None, None) when the
    variant has none (the caller then falls back to the pipeline `param_default`). M0e Part A:
    `flux.2-dev` carries `defaults.width/height = 512` so an unset dev cast resolves to 512²
    (it runs far faster at low res on 16 GB ROCm). Display==reality — both /generate's unset-size
    resolution and the UI drawer placeholder read this, so an unset dev cast GETS what the drawer
    advertises. Any variant may opt in by adding width/height to its `defaults`."""
    v = find_variant(pipeline, model_name) if model_name else None
    d = (v or {}).get("defaults") or {}
    w, h = d.get("width"), d.get("height")
    return (w if isinstance(w, int) else None, h if isinstance(h, int) else None)


def find_variant(pipeline: str, model_name: str) -> dict | None:
    return next((v for v in variants(pipeline) if v["id"] == model_name), None)


def default_model(pipeline: str) -> str | None:
    """The worker's default variant for a pipeline (the `model_name` param default)."""
    for prm in params(pipeline):
        if prm["name"] == "model_name":
            return prm.get("default")
    return None


def validate_model(pipeline: str, model_name: str | None):
    """Return the catalog variant for an explicit `model_name`, or None when unset (the caller
    uses the worker default). **Raises `CatalogError` if model_name is set but not a real
    variant** — so an unknown model fails fast (422) instead of dying in the worker (whose
    argparse `--model-name choices=…` would reject it after a subprocess spawn)."""
    if not model_name:
        return None
    v = find_variant(pipeline, model_name)
    if v is None:
        raise CatalogError(f"unknown {pipeline} model {model_name!r} (see GET /models)")
    return v


def _param_specs(pipeline: str) -> dict[str, dict]:
    return {p["name"]: p for p in params(pipeline)}


class CatalogError(ValueError):
    """A requested param is unknown / wrong-typed / out-of-range / not valid for the mode."""


def validate_params(pipeline: str, mode: str, raw: dict) -> dict:
    """Validate a request's catalog-`params` channel for a pipeline+mode (M3 step 4b): every
    key must be a known catalog param, of the right type, in range, and applicable to `mode`.
    Returns the validated dict (None values dropped). Raises CatalogError with a clear message.

    Structural validation only — per-MODEL applicability (e.g. cfg_normalization is zimage-base
    only, negatives need supports_negative_prompt) is left to the worker, which warns + ignores
    gracefully; we don't hard-reject on it so prototyping stays frictionless."""
    if not raw:
        return {}
    if pipeline not in CATALOG and pipeline != "multi":
        raise CatalogError(f"pipeline {pipeline!r} has no tunable catalog (params channel n/a)")
    specs = _param_specs(pipeline)
    out: dict = {}
    for key, val in raw.items():
        s = specs.get(key)
        if s is None:
            raise CatalogError(f"unknown param {key!r} for {pipeline} (see GET /models)")
        modes = s.get("modes")
        if modes and mode not in modes:
            raise CatalogError(f"param {key!r} is only valid in modes {modes} (got {mode!r})")
        if val is None:
            continue
        t = s["type"]
        if t == "flag":
            if not isinstance(val, bool):
                raise CatalogError(f"param {key!r} must be a boolean flag")
        elif t == "int":
            if not isinstance(val, int) or isinstance(val, bool):
                raise CatalogError(f"param {key!r} must be an integer")
            _range_check(key, val, s)
        elif t == "float":
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise CatalogError(f"param {key!r} must be a number")
            _range_check(key, val, s)
        elif t == "enum":
            choices = s.get("choices")
            if choices and val not in choices:
                raise CatalogError(f"param {key!r} must be one of {choices}")
        # str / image: any string passes structural validation
        out[key] = val
    return out


def _range_check(key: str, val, spec: dict) -> None:
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None and val < lo:
        raise CatalogError(f"param {key!r}={val} below min {lo}")
    if hi is not None and val > hi:
        raise CatalogError(f"param {key!r}={val} above max {hi}")


def emit_argv(pipeline: str, params_dict: dict, mode: str) -> list[str]:
    """Emit CLI flags for every catalog param present in `params_dict` (the single source of
    flag mapping, so adding a param = a catalog entry, not adapter edits). Respects each param's
    `modes` (skip if not applicable) and `type` (flag→bare, else `--flag value`); None is skipped
    (the worker resolves its preset). Catalog order — argparse is order-insensitive."""
    argv: list[str] = []
    for s in params(pipeline):
        if s.get("post"):
            continue   # post-passes are orchestrator-chained, never worker CLI flags
        name, flag, t = s["name"], s["flag"], s["type"]
        modes = s.get("modes")
        if modes and mode not in modes:
            continue
        if name not in params_dict:
            continue
        val = params_dict[name]
        if val is None:
            continue
        if t == "flag":
            if val:
                argv.append(flag)
        else:
            argv += [flag, str(val)]
    return argv
