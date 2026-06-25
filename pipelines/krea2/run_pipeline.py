"""Krea 2 pipeline orchestrator - runs all stages and writes a JSON manifest."""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make src/pipeline/ importable for the shared _artifact_id helper.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _artifact_id  # noqa: E402

import stage1_load_pipeline, stage2_generate, stage3_save  # noqa: E402
from manifest import PipelineManifest  # noqa: E402
from stage1_load_pipeline import KREA2_MODEL_INFO  # noqa: E402


def run(
    prompt: str,
    model_name: str = "krea2-turbo",
    width: int | None = None,
    height: int | None = None,
    seed: int | None = None,
    num_steps: int | None = None,
    guidance_scale: float | None = None,
    negative_prompt: str | None = "",
    max_sequence_length: int = 512,
    output_dir: str = "src/assets/pics",
    device: str = "cuda",
    cpu_offload: bool = True,
    dtype: str = "bfloat16",
    device_map: str | None = None,
    lora_path: str | None = None,
    lora_weight: float = 1.0,
    quant_backend: str | None = None,
    quant_dtype: str = "float8",
    quant_skip_modules: list[str] | None = None,
) -> PipelineManifest:
    """Run the full Krea 2 text-to-image pipeline."""
    if "PYTORCH_ALLOC_CONF" not in os.environ:
        os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"

    if seed is None:
        seed = random.randrange(2**31)

    if model_name not in KREA2_MODEL_INFO:
        raise ValueError(f"unknown model_name {model_name!r}; expected one of {list(KREA2_MODEL_INFO)}")

    model_info = KREA2_MODEL_INFO[model_name]
    defaults = model_info["defaults"]
    if width is None:
        width = defaults["width"]
    if height is None:
        height = defaults["height"]
    if num_steps is None:
        num_steps = defaults["num_steps"]
    if guidance_scale is None:
        guidance_scale = defaults["guidance_scale"]

    if negative_prompt and (guidance_scale <= 0 or not model_info["supports_negative_prompt"]):
        print(f"[warn] {model_name} does not use negative prompts at guidance_scale={guidance_scale} -- ignoring")
        negative_prompt = None

    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        repo_root = Path(__file__).resolve().parents[3]
        out_dir = repo_root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = out_dir / f"krea2_{timestamp}_s{seed}.png"
    manifest_path = output_path.with_suffix(".json")

    manifest = PipelineManifest(
        model_name=model_name,
        prompt=prompt,
        seed=seed,
        width=width,
        height=height,
        created_at=datetime.now(timezone.utc).isoformat(),
        device=device,
        run_id=_artifact_id.mint_run_id(seed),
        hf_repo=model_info["repo_id"],
        dtype=dtype,
        cpu_offload=cpu_offload,
        device_map=device_map,
        quant_backend=quant_backend,
        quant_dtype=quant_dtype if quant_backend else None,
        quant_skip_modules=quant_skip_modules or [],
        num_inference_steps=num_steps,
        guidance_scale=guidance_scale,
        max_sequence_length=max_sequence_length,
        is_distilled=model_info["is_distilled"],
        vae_scale_factor=model_info["vae_scale_factor"],
        patch_size=model_info["patch_size"],
    )
    manifest.pipeline_start = time.time()

    rec = manifest.begin_stage(
        "load_pipeline",
        stage1_load_pipeline.get_manifest_inputs(
            model_name, device, cpu_offload, dtype, device_map, lora_path, lora_weight,
            quant_backend, quant_dtype, quant_skip_modules,
        ),
    )
    try:
        s1 = stage1_load_pipeline.run(
            model_name=model_name,
            device=device,
            cpu_offload=cpu_offload,
            dtype=dtype,
            device_map=device_map,
            lora_path=lora_path,
            lora_weight=lora_weight,
            quant_backend=quant_backend,
            quant_dtype=quant_dtype,
            quant_skip_modules=quant_skip_modules,
        )
        manifest.end_stage(
            rec,
            stage1_load_pipeline.get_manifest_outputs(s1),
            stage1_load_pipeline.get_manifest_debug(s1),
        )
        versions = s1["runtime_versions"]
        manifest.diffusers_version = versions["diffusers_version"]
        manifest.diffusers_commit = versions["diffusers_commit"]
        manifest.transformers_version = versions["transformers_version"]
        manifest.torch_version = versions["torch_version"]
        manifest.rocm_version = versions["rocm_version"]
        if s1["lora"] is not None:
            manifest.lora_paths = [s1["lora"]["path"]]
            manifest.lora_hashes = [s1["lora"]["sha256"]]
        print(f"[stage1] Pipeline loaded in {rec.duration_s}s" + (" (cpu_offload)" if cpu_offload else ""))
    except Exception as e:
        manifest.fail_stage(rec, str(e))
        _finalize_failed_manifest(manifest, manifest_path)
        raise

    rec = manifest.begin_stage(
        "generate",
        stage2_generate.get_manifest_inputs(
            prompt, width, height, seed, num_steps, guidance_scale,
            negative_prompt, max_sequence_length,
        ),
    )
    try:
        s2 = stage2_generate.run(
            pipe=s1["pipe"],
            prompt=prompt,
            width=width,
            height=height,
            seed=seed,
            num_inference_steps=num_steps,
            guidance_scale=guidance_scale,
            negative_prompt=negative_prompt,
            max_sequence_length=max_sequence_length,
        )
        manifest.end_stage(
            rec,
            stage2_generate.get_manifest_outputs(s2),
            stage2_generate.get_manifest_debug(s2),
        )
        manifest.peak_vram_mb = s2["peak_vram_mb"]
        print(f"[stage2] Generated in {rec.duration_s}s -- {s2['width']}x{s2['height']}")
    except Exception as e:
        manifest.fail_stage(rec, str(e))
        _finalize_failed_manifest(manifest, manifest_path)
        raise

    rec = manifest.begin_stage("save", stage3_save.get_manifest_inputs(str(output_path)))
    try:
        s3 = stage3_save.run(image=s2["image"], output_path=output_path)
        manifest.end_stage(rec, stage3_save.get_manifest_outputs(s3), stage3_save.get_manifest_debug(s3))
        manifest.artifacts.append(_artifact_id.make_artifact_record(
            output_path, kind="image/png", produced_by_stage="save",
        ))
        print(f"[stage3] Saved in {rec.duration_s}s -- {s3['output_path']}")
    except Exception as e:
        manifest.fail_stage(rec, str(e))
        _finalize_failed_manifest(manifest, manifest_path)
        raise

    manifest.pipeline_end = time.time()
    manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
    manifest.output_path = str(output_path)
    manifest.save(manifest_path)
    print(f"[done] Pipeline completed in {manifest.pipeline_duration_s}s")
    print(f"  Image: {output_path}")
    print(f"  Manifest: {manifest_path}")

    return manifest


def _finalize_failed_manifest(manifest: PipelineManifest, manifest_path: Path) -> None:
    manifest.pipeline_end = time.time()
    manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
    manifest.save(manifest_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Krea 2 image generation pipeline")
    parser.add_argument("--prompt", required=True, help="Text prompt")
    parser.add_argument("--negative-prompt", default="", help="Used only when guidance_scale > 0")
    parser.add_argument("--model-name", default="krea2-turbo", choices=list(KREA2_MODEL_INFO.keys()))
    parser.add_argument("--width", type=int, default=None, help="Must be divisible by 16; defaults to model preset")
    parser.add_argument("--height", type=int, default=None, help="Must be divisible by 16; defaults to model preset")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--num-steps", type=int, default=None, help="Defaults to model preset")
    parser.add_argument("--guidance-scale", type=float, default=None, help="Defaults to model preset")
    parser.add_argument("--max-sequence-length", type=int, default=512)
    parser.add_argument("--output-dir", default="src/assets/pics")
    parser.add_argument("--device", default="cuda", help="Torch device (use 'cuda' for ROCm/HIP too)")
    parser.add_argument("--no-cpu-offload", action="store_true", help="Disable CPU offload")
    parser.add_argument("--device-map", default=None, help="Advanced accelerate placement, e.g. 'auto'")
    parser.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float16", "float32"])
    parser.add_argument("--lora-path", default=None, help="Optional Diffusers-compatible LoRA file or directory")
    parser.add_argument("--lora-weight", type=float, default=1.0, help="Runtime scale for --lora-path")
    parser.add_argument(
        "--quant-backend",
        default=None,
        choices=["quanto"],
        help="Experimental transformer quantization backend. First local target: quanto.",
    )
    parser.add_argument(
        "--quant-dtype",
        default="float8",
        choices=["float8", "int8", "int4", "int2"],
        help="Quanto transformer weight dtype when --quant-backend is set.",
    )
    parser.add_argument(
        "--quant-skip-modules",
        default=None,
        help=(
            "Comma-separated local module names to keep unquantized. "
            "Useful Quanto values for Krea2 include: linear,img_in,time_mod_proj,linear_1,linear_2,projector."
        ),
    )
    args = parser.parse_args()
    quant_skip_modules = (
        [m.strip() for m in args.quant_skip_modules.split(",") if m.strip()]
        if args.quant_skip_modules else None
    )

    run(
        prompt=args.prompt,
        model_name=args.model_name,
        width=args.width,
        height=args.height,
        seed=args.seed,
        num_steps=args.num_steps,
        guidance_scale=args.guidance_scale,
        negative_prompt=args.negative_prompt,
        max_sequence_length=args.max_sequence_length,
        output_dir=args.output_dir,
        device=args.device,
        cpu_offload=not args.no_cpu_offload,
        dtype=args.dtype,
        device_map=args.device_map,
        lora_path=args.lora_path,
        lora_weight=args.lora_weight,
        quant_backend=args.quant_backend,
        quant_dtype=args.quant_dtype,
        quant_skip_modules=quant_skip_modules,
    )


if __name__ == "__main__":
    main()
