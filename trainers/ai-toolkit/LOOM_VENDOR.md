# Loom ai-toolkit vendor record

This directory is a source snapshot of [ostris/ai-toolkit](https://github.com/ostris/ai-toolkit)
at commit `548a286992261fbef40c380e82495d21fd3bca86` (2026-06-19, MIT). It is the
P2/M1 training-spike runtime, not a general promise that every upstream model or extension works
on Windows ROCm.

## Loom compatibility patches

Six small source patches were required by the proven Z-Image path on the RX 9070 XT:

- `run.py` supplies the missing `torch.distributed.is_initialized` cleanup predicate.
- `toolkit/config_modules.py`, `toolkit/util/quantize.py`, and `toolkit/models/lokr.py` keep
  TorchAO optional. Z-Image `qfloat8` remains backed by `optimum-quanto`.
- `toolkit/extension.py` honors `AI_TOOLKIT_MINIMAL_ZIMAGE=1` and avoids eagerly importing
  unrelated extensions/native dependencies; normal upstream discovery remains the default.
- `extensions_built_in/diffusion_models/__init__.py` registers only `ZImageModel` in that minimal
  mode; normal upstream model registration remains the default.

The isolated dependency overlay used for M1 also omits `torchao` and `bitsandbytes`, pins
`numpy==1.26.4` for the upstream `scipy==1.12.0` ABI, and uses the Diffusers commit pinned in
`requirements_base.txt`. On Windows ROCm that Diffusers snapshot needs its top-level
`torch.distributed.fsdp` imports in `diffusers/training_utils.py` wrapped in
`try/except (ImportError, ModuleNotFoundError)` with the four imported FSDP symbols set to `None`.
This is a single-GPU compatibility shim; FSDP training is unsupported on this runtime.

Do not install this snapshot's requirements into Loom's shared ROCm environment. M2 owns the
repeatable isolated dependency layer and queued-job wrapper. Until then, run it with the tested
overlay first on `PYTHONPATH` and set `AI_TOOLKIT_MINIMAL_ZIMAGE=1`.

## M1 evidence and default

The fixed `config/loom_zimage_rocm.example.yaml` shape was exercised with 17 finalized `char01`
references at 512 px, rank/alpha 16/16, `qfloat8`, bf16, and plain AdamW. The 100-step can-run
checkpoint loaded successfully but did not reproduce the identity. The same run auto-resumed twice,
including optimizer state, and reached the **500-step default** in 8,097.5 aggregate training
seconds. The final 85,094,896-byte adapter has SHA-256
`B84DA64D6E642D18F62950BB522405AC560B101ADA6B4C2A89E46A3CAEB1EA1C`.

Diffusers loaded that adapter through Loom's Z-Image worker and generated the fixed-seed M1 image at
512 px / 30 steps / guidance 4 / LoRA weight 1.0. Without appearance words beyond the deterministic
caption (`char01_lw, front view, full body, neutral expression`), it reproduced the older
silver-haired subject, olive trench coat, stern expression, fluorescent room, and vintage treatment.
ArcFace centroid similarity rose from -0.016 for the base-model control to 0.263 for the final image;
this proves a material identity signal but remains below the curated set's own cross-view band, so
the LoRA does not replace Loom's separate face-lock pass.

The shared inference environment lacks PEFT and therefore rejects `load_lora_weights`; M1 deliberately
used the isolated dependency overlay (`peft==0.18.1` plus the pinned Diffusers snapshot) instead of
mutating that known-good environment. M2 must make this overlay a declared trainer/inference runtime
dependency before queued LoRA jobs are exposed.

The artifact, generated acceptance image, and training data intentionally remain outside Git.
