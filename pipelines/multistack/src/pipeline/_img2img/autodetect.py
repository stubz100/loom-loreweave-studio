"""Sidecar-based source-pipeline auto-detect (kb-multi-image2.md §6, P4).

Moved verbatim from ``handrefiner/__init__._detect_source_pipeline``;
``handrefiner`` re-exports it (``_detect_source_pipeline``) so postproc
behaviour is byte-identical. The multi ``batch`` polish stage uses it to
resolve the polish backend/seed/prompt from a candidate's `<image>.json`
sidecar (written/augmented by the ideate + clean stages).

No dependency on ``pipeline.postproc`` -- direction is postproc -> _img2img.
"""

from __future__ import annotations

import json
from pathlib import Path


def detect_source_pipeline(image_path: Path) -> dict:
    """Look for a sidecar manifest next to `image_path` and infer the
    pipeline + seed that produced it. Returns:
        {"backend": "sd35-img2img"|"zimage-img2img"|"flux2-img2img"|None,
         "seed":    int|None,
         "model":   str|None,
         "prompt":  str|None,
         "manifest_path": Path|None}

    The "backend" field is the POLISH backend (img2img variant) — i.e. if
    the source was made by sd35, we return "sd35-img2img" so the caller
    can pass it straight to `run_polish(...)`.

    Detection rules:
      * Sidecar JSON candidates: `<stem>.json` next to the image.
      * Look for `module` field == "sd35" / "zimage" / "flux2"; or for
        a file basename starting with `sd35_` / `zimage_` / `flux2_`.
      * If neither yields a hit, returns all None.
    """
    image_path = Path(image_path)
    sidecar = image_path.with_suffix(".json")

    backend = None
    seed = None
    model = None
    prompt = None
    manifest_path: Path | None = None

    if sidecar.exists():
        try:
            with open(sidecar, encoding="utf-8") as f:
                data = json.load(f)
            module = (data.get("module") or "").lower()
            if module == "sd35":
                backend = "sd35-img2img"
            elif module == "zimage":
                backend = "zimage-img2img"
            elif module == "flux2":
                backend = "flux2-img2img"
            seed   = data.get("seed")
            model  = data.get("model_name")
            prompt = data.get("prompt")
            manifest_path = sidecar
        except (json.JSONDecodeError, OSError):
            pass

    if backend is None:
        stem = image_path.stem.lower()
        if stem.startswith("sd35_"):
            backend = "sd35-img2img"
        elif stem.startswith("zimage_"):
            backend = "zimage-img2img"
        elif stem.startswith("flux2_"):
            backend = "flux2-img2img"

    return {"backend": backend, "seed": seed, "model": model,
            "prompt": prompt, "manifest_path": manifest_path}
