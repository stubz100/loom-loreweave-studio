"""BiRefNet subject-matting CLI (postproc tool; loom P1/M3.5).

Produces the **Stage-B background-inpaint mask**: a subject matte of the hero image and
its inversion (background = white = REPAINT), with the subject **dilated** a few pixels so
inpainting never eats the subject's edges. Also emits an RGBA cutout (matte as alpha) for
layering / clean refs.

Single-image, deterministic (no seed), one model load per invocation. Heavy imports are
lazy so `-h` stays cheap (import-graph smoke tests rely on this).

CLI:
  python run_pipeline.py --input <image> --output-dir <dir>
      [--model-name birefnet|birefnet-hr] [--resolution 1024] [--threshold 0.5]
      [--dilate-px 12] [--feather-px 0] [--device cuda|cpu] [--dtype float32|float16]

Outputs in --output-dir (ts = YYYYmmdd_HHMMSS):
  birefnet_<ts>_matte.png    grayscale subject matte (white = subject, soft alpha)
  birefnet_<ts>_cutout.png   RGBA subject cutout (input with the matte as alpha)
  birefnet_<ts>_bgmask.png   background mask for inpainting (white = repaint;
                             subject protected by --dilate-px binary dilation)
  birefnet_<ts>.json         PostprocManifest (module="birefnet", manifest-as-truth)

Progress markers (parsed by the loom orchestrator adapter — same shapes as zimage):
  [stage1] Pipeline loaded in <s>s
  [stage2] Matted in <s>s -- <W>x<H>
  [done] Pipeline completed in <s>s
    Image: <path>            (one per emitted PNG)
    Manifest: <path>

Weights: Hugging Face `ZhengPeng7/BiRefNet` (MIT), a *transformers* repo loaded with
`AutoModelForImageSegmentation.from_pretrained(..., trust_remote_code=True)` — the model
code ships in the HF repo (well-known upstream), NOT vendored here. Presence probe =
`config.json` in the HF cache (it is not a diffusers repo: no model_index.json).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make the parent package importable when invoked as a script (postproc convention).
_PKG_ROOT = Path(__file__).resolve().parents[2]   # …/src/pipeline (or a vendored mirror)
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from postproc._common import PostprocManifest, append_artifact, mint_run_id  # noqa: E402

BIREFNET_MODEL_INFO = {
    # General-purpose 1024 model — the loom default (subject matting of a hero frame).
    "birefnet": {"repo_id": "ZhengPeng7/BiRefNet", "resolution": 1024},
    # High-resolution variant (2048) — slower, for promoted hi-res refs.
    "birefnet-hr": {"repo_id": "ZhengPeng7/BiRefNet_HR", "resolution": 2048},
}


def run(input_image: str, output_dir: str, *, model_name: str = "birefnet",
        resolution: int | None = None, threshold: float = 0.5, dilate_px: int = 12,
        feather_px: int = 0, device: str = "cuda", dtype: str = "float32") -> dict:
    """Matte one image. Returns {matte, cutout, bgmask, manifest} paths.

    Save-then-raise (zimage convention): on a stage failure the manifest is written with
    the failed stage before the exception propagates, so the orchestrator's
    manifest-as-truth parse sees an honest record.
    """
    if model_name not in BIREFNET_MODEL_INFO:
        raise ValueError(f"unknown model_name {model_name!r}; "
                         f"one of {list(BIREFNET_MODEL_INFO)}")
    info = BIREFNET_MODEL_INFO[model_name]
    res = int(resolution or info["resolution"])

    in_path = Path(input_image)
    if not in_path.is_file():
        raise FileNotFoundError(f"input image not found: {in_path}")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_path = out_dir / f"birefnet_{ts}.json"

    from PIL import Image

    img = Image.open(in_path).convert("RGB")
    width, height = img.size

    manifest = PostprocManifest(
        module="birefnet", input_image=str(in_path), seed=0,
        width=width, height=height,
        created_at=datetime.now(timezone.utc).isoformat(),
        pipeline_start=time.time(), device=device,
        run_id=mint_run_id(0),
    )

    # --- stage 1: load -----------------------------------------------------------
    rec = manifest.begin_stage("load", {"model_name": model_name,
                                        "repo_id": info["repo_id"], "device": device,
                                        "dtype": dtype})
    try:
        import torch
        from transformers import AutoModelForImageSegmentation
        model = AutoModelForImageSegmentation.from_pretrained(
            info["repo_id"], trust_remote_code=True)
        torch_dtype = torch.float16 if dtype == "float16" else torch.float32
        model.to(device=device, dtype=torch_dtype)
        model.eval()
    except Exception as e:  # noqa: BLE001 - save-then-raise
        manifest.fail_stage(rec, str(e))
        manifest.pipeline_end = time.time()
        manifest.save(manifest_path)
        raise
    manifest.end_stage(rec, {"loaded": True})
    print(f"[stage1] Pipeline loaded in {rec.duration_s}s")

    # --- stage 2: matte ----------------------------------------------------------
    rec = manifest.begin_stage("matte", {"resolution": res})
    try:
        from torchvision import transforms
        tf = transforms.Compose([
            transforms.Resize((res, res)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])
        with torch.no_grad():
            batch = tf(img).unsqueeze(0).to(device=device, dtype=torch_dtype)
            # BiRefNet returns multi-scale predictions; the last is the final matte.
            pred = model(batch)[-1].sigmoid().float().cpu()[0].squeeze(0)
        matte_img = Image.fromarray((pred.numpy() * 255).astype("uint8"), mode="L")
        matte_img = matte_img.resize((width, height), Image.LANCZOS)
    except Exception as e:  # noqa: BLE001 - save-then-raise
        manifest.fail_stage(rec, str(e))
        manifest.pipeline_end = time.time()
        manifest.save(manifest_path)
        raise
    manifest.end_stage(rec, {"width": width, "height": height})
    print(f"[stage2] Matted in {rec.duration_s}s -- {width}x{height}")

    # --- stage 3: artifacts (matte / cutout / bgmask) -----------------------------
    rec = manifest.begin_stage("artifacts", {"threshold": threshold,
                                             "dilate_px": dilate_px,
                                             "feather_px": feather_px})
    try:
        import numpy as np
        from scipy import ndimage

        matte_path = out_dir / f"birefnet_{ts}_matte.png"
        matte_img.save(matte_path)
        append_artifact(manifest, matte_path, kind="image/png", role="matte",
                        produced_by_stage="artifacts")

        cutout = img.convert("RGBA")
        cutout.putalpha(matte_img)
        cutout_path = out_dir / f"birefnet_{ts}_cutout.png"
        cutout.save(cutout_path)
        append_artifact(manifest, cutout_path, kind="image/png", role="cutout",
                        produced_by_stage="artifacts")

        # Background mask: white = REPAINT. Binarize the subject, dilate it so the
        # inpaint never eats subject edges, invert. Optional outward-only feather:
        # blur the bg side but keep the protected (dilated-subject) core hard zero.
        m = np.asarray(matte_img, dtype=np.float32) / 255.0
        subject = m >= float(threshold)
        if dilate_px > 0:
            subject = ndimage.binary_dilation(subject, iterations=int(dilate_px))
        bg = (~subject).astype(np.float32)
        if feather_px > 0:
            bg = ndimage.gaussian_filter(bg, sigma=float(feather_px) / 2.0)
            bg[subject] = 0.0                     # never soften INTO the subject
        bgmask_img = Image.fromarray((bg.clip(0.0, 1.0) * 255).astype("uint8"), mode="L")
        bgmask_path = out_dir / f"birefnet_{ts}_bgmask.png"
        bgmask_img.save(bgmask_path)
        append_artifact(manifest, bgmask_path, kind="image/png", role="bgmask",
                        produced_by_stage="artifacts")
    except Exception as e:  # noqa: BLE001 - save-then-raise
        manifest.fail_stage(rec, str(e))
        manifest.pipeline_end = time.time()
        manifest.save(manifest_path)
        raise
    manifest.end_stage(rec, {"matte": str(matte_path), "cutout": str(cutout_path),
                             "bgmask": str(bgmask_path)})

    manifest.output_path = str(bgmask_path)        # the Stage-B consumer artifact
    manifest.pipeline_end = time.time()
    manifest.pipeline_duration_s = round(manifest.pipeline_end - manifest.pipeline_start, 4)
    manifest.save(manifest_path)

    print(f"[done] Pipeline completed in {manifest.pipeline_duration_s}s")
    for p in (matte_path, cutout_path, bgmask_path):
        print(f"  Image: {p}")
    print(f"  Manifest: {manifest_path}")
    return {"matte": str(matte_path), "cutout": str(cutout_path),
            "bgmask": str(bgmask_path), "manifest": str(manifest_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="BiRefNet subject matting -> matte / cutout / background-inpaint mask")
    parser.add_argument("--input", required=True, help="image to matte (absolute path)")
    parser.add_argument("--output-dir", required=True, help="directory for the artifacts")
    parser.add_argument("--model-name", default="birefnet",
                        choices=sorted(BIREFNET_MODEL_INFO))
    parser.add_argument("--resolution", type=int, default=None,
                        help="inference square size (default: the model's native)")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="subject binarization threshold for the bg mask")
    parser.add_argument("--dilate-px", type=int, default=12,
                        help="grow the protected subject region by N px (edge safety)")
    parser.add_argument("--feather-px", type=int, default=0,
                        help="soften the bg mask outward by ~N px (0 = hard edge)")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--dtype", default="float32", choices=("float32", "float16"))
    args = parser.parse_args(argv)
    run(args.input, args.output_dir, model_name=args.model_name,
        resolution=args.resolution, threshold=args.threshold, dilate_px=args.dilate_px,
        feather_px=args.feather_px, device=args.device, dtype=args.dtype)
    return 0


if __name__ == "__main__":
    sys.exit(main())
