"""img2img backend dispatch -- canonical home (moved from
``handrefiner/stage5_inpaint.py`` in P2, kb-multi-image2.md §6).

The logic below is the verbatim polish dispatch that previously lived in
``stage5_inpaint.py``; ``stage5_inpaint`` now re-exports these names so every
existing postproc caller (``run_pipeline.cmd_polish``, ``handrefiner.__init__``
Stage 7, ``_prior_polish``) is byte-identical to pre-P2.

The small subprocess plumbing (`_build_env`, `_find_latest_output`, the
per-pipeline run-script path constants) is intentionally self-contained here:
``stage5_inpaint`` keeps its own copies for the *inpaint* path, which P2 must
not destabilise. Only the meaningful backend behaviour (command construction,
defaults, dispatch) is single-sourced -- the ~25 lines of generic env/glob
plumbing are infra, not "implementation".
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# Anchor to the repo root so we don't depend on cwd.
#   <repo>/src/pipeline/_img2img/backends.py
#   parents[0]=_img2img  [1]=pipeline  [2]=src  [3]=<repo>
_THIS_FILE = Path(__file__).resolve()
_SRC_DIR = _THIS_FILE.parents[2]
_ZIMAGE_PKG = _SRC_DIR / "pipeline" / "zimage"
_ZIMAGE_RUN = _ZIMAGE_PKG / "run_pipeline.py"
_SD35_PKG = _SRC_DIR / "pipeline" / "sd35"
_SD35_RUN = _SD35_PKG / "run_pipeline.py"
_FLUX2_PKG = _SRC_DIR / "pipeline" / "flux2"
_FLUX2_RUN = _FLUX2_PKG / "run_pipeline.py"


def _build_env(extra_pkg_dir: Path | None = None) -> dict:
    """Return an env dict with PYTHONPATH = [pkg_dir, src/] prepended.

    `extra_pkg_dir` is the per-pipeline package dir whose bare imports we
    need to resolve (zimage, sd35, etc.). Defaults to zimage for backwards
    compatibility with the only previous caller.
    """
    env = os.environ.copy()
    pkg = extra_pkg_dir or _ZIMAGE_PKG
    extra = [str(pkg), str(_SRC_DIR)]
    existing = env.get("PYTHONPATH", "")
    parts = extra + ([existing] if existing else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _find_latest_output(output_dir: Path, seed: int, prefix: str) -> tuple[Path | None, Path | None]:
    """Glob `<output_dir>/<prefix>_*_s<seed>.png` and return (png, manifest) by mtime."""
    pngs = sorted(
        Path(output_dir).glob(f"{prefix}_*_s{seed}.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not pngs:
        return None, None
    png = pngs[0]
    manifest = png.with_suffix(".json")
    return png, (manifest if manifest.exists() else None)


def _find_latest_zimage_output(output_dir: Path, seed: int) -> tuple[Path | None, Path | None]:
    return _find_latest_output(output_dir, seed, "zimage")


def _find_latest_sd35_output(output_dir: Path, seed: int) -> tuple[Path | None, Path | None]:
    return _find_latest_output(output_dir, seed, "sd35")


def _find_latest_flux2_output(output_dir: Path, seed: int) -> tuple[Path | None, Path | None]:
    return _find_latest_output(output_dir, seed, "flux2")


# ---------------------------------------------------------------------------
# Polish / img2img backends (global img2img on the full image).
# These wrap the per-pipeline `run_pipeline.py --mode img2img` surfaces.
# Used both for postproc Stage 7 polish and (P3/P4) multi clean/polish.
# ---------------------------------------------------------------------------

def invoke_zimage_polish(
    image_path: str | Path,
    *,
    prompt: str,
    negative_prompt: str = "",
    strength: float = 0.22,
    seed: int = 0,
    width: int | None = None,
    height: int | None = None,
    output_dir: str | Path,
    model_name: str = "zimage-base",
    cfg_normalization: bool = True,
) -> dict:
    """Run Z-Image img2img as a low-strength polish pass.

    Z-Image's img2img has no `--mask-image`; the polish is global. The dilated
    MANO mask is irrelevant here -- the goal is a gentle, scene-wide
    re-roll that smooths discontinuities at the inpaint seam.
    """
    image_path = Path(image_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if width is None or height is None:
        from PIL import Image
        with Image.open(image_path) as im:
            w, h = im.size
        width  = width  or w
        height = height or h

    cmd: list[str] = [
        sys.executable, str(_ZIMAGE_RUN),
        "--prompt",        prompt,
        "--model-name",    model_name,
        "--mode",          "img2img",
        "--init-image",    str(image_path),
        "--strength",      str(strength),
        "--width",         str(width),
        "--height",        str(height),
        "--seed",          str(seed),
        "--output-dir",    str(output_dir),
    ]
    if negative_prompt:
        cmd += ["--negative-prompt", negative_prompt]
    if cfg_normalization:
        cmd.append("--cfg-normalization")

    t0 = time.time()
    proc = subprocess.run(
        cmd, cwd=str(_ZIMAGE_PKG), env=_build_env(_ZIMAGE_PKG),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    duration = round(time.time() - t0, 4)

    png, manifest = _find_latest_zimage_output(output_dir, seed)
    return {
        "output_path":           str(png) if png else "",
        "sub_manifest_path":     str(manifest) if manifest else "",
        "subprocess_duration_s": duration,
        "returncode":            proc.returncode,
        "stderr":                proc.stderr[-2000:] if proc.stderr else "",
        "stdout_tail":           proc.stdout[-1000:] if proc.stdout else "",
        "cmd":                   cmd,
        "init_image_used":       str(image_path),
        "strength":              strength,
        "backend":               "zimage-img2img",
    }


def invoke_sd35_polish(
    image_path: str | Path,
    *,
    prompt: str,
    negative_prompt: str = "",
    strength: float = 0.22,
    seed: int = 0,
    width: int | None = None,
    height: int | None = None,
    output_dir: str | Path,
    model_name: str = "sd3.5-medium",
) -> dict:
    """Run SD 3.5 img2img (StableDiffusion3Img2ImgPipeline) as a low-strength
    polish pass. Best style match when the original image was generated by
    SD 3.5. Negative prompt is fully respected (SD models support negatives)."""
    image_path = Path(image_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if width is None or height is None:
        from PIL import Image
        with Image.open(image_path) as im:
            w, h = im.size
        width  = width  or w
        height = height or h

    cmd: list[str] = [
        sys.executable, str(_SD35_RUN),
        "--prompt",          prompt,
        "--model-name",      model_name,
        "--mode",            "img2img",
        "--init-image",      str(image_path),
        "--strength",        str(strength),
        "--width",           str(width),
        "--height",          str(height),
        "--seed",            str(seed),
        "--output-dir",      str(output_dir),
    ]
    if negative_prompt:
        cmd += ["--negative-prompt", negative_prompt]

    t0 = time.time()
    proc = subprocess.run(
        cmd, cwd=str(_SD35_PKG), env=_build_env(_SD35_PKG),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    duration = round(time.time() - t0, 4)

    png, manifest = _find_latest_sd35_output(output_dir, seed)
    return {
        "output_path":           str(png) if png else "",
        "sub_manifest_path":     str(manifest) if manifest else "",
        "subprocess_duration_s": duration,
        "returncode":            proc.returncode,
        "stderr":                proc.stderr[-2000:] if proc.stderr else "",
        "stdout_tail":           proc.stdout[-1000:] if proc.stdout else "",
        "cmd":                   cmd,
        "init_image_used":       str(image_path),
        "strength":              strength,
        "backend":               "sd35-img2img",
    }


def invoke_flux2_polish(
    image_path: str | Path,
    *,
    prompt: str,
    negative_prompt: str = "",
    strength: float = 0.22,
    seed: int = 0,
    width: int | None = None,
    height: int | None = None,
    output_dir: str | Path,
    model_name: str = "flux.2-klein-4b",
) -> dict:
    """Run Flux 2 img2img (flow-matching init-mix) as a low-strength polish
    pass. Best style match when the original image was generated by Flux 2.
    Flux 2 does NOT use negative prompts -- the kwarg is accepted but ignored
    in the subprocess (logged in the manifest for traceability)."""
    image_path = Path(image_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if width is None or height is None:
        from PIL import Image
        with Image.open(image_path) as im:
            w, h = im.size
        width  = width  or w
        height = height or h

    cmd: list[str] = [
        sys.executable, str(_FLUX2_RUN),
        "--prompt",        prompt,
        "--model-name",    model_name,
        "--mode",          "img2img",
        "--init-image",    str(image_path),
        "--strength",      str(strength),
        "--width",         str(width),
        "--height",        str(height),
        "--seed",          str(seed),
        "--output-dir",    str(output_dir),
    ]

    t0 = time.time()
    proc = subprocess.run(
        cmd, cwd=str(_FLUX2_PKG), env=_build_env(_FLUX2_PKG),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    duration = round(time.time() - t0, 4)

    png, manifest = _find_latest_flux2_output(output_dir, seed)
    return {
        "output_path":           str(png) if png else "",
        "sub_manifest_path":     str(manifest) if manifest else "",
        "subprocess_duration_s": duration,
        "returncode":            proc.returncode,
        "stderr":                proc.stderr[-2000:] if proc.stderr else "",
        "stdout_tail":           proc.stdout[-1000:] if proc.stdout else "",
        "cmd":                   cmd,
        "init_image_used":       str(image_path),
        "strength":              strength,
        "negative_prompt_ignored": bool(negative_prompt),
        "backend":               "flux2-img2img",
    }


def run_polish(
    image_path: str | Path,
    *,
    backend: str,
    prompt: str,
    negative_prompt: str = "",
    strength: float = 0.22,
    seed: int = 0,
    output_dir: str | Path,
    model_name: str | None = None,
) -> dict:
    """Dispatcher for the polish (img2img) pass.

    `backend` ∈ {"zimage-img2img", "sd35-img2img", "flux2-img2img"}.
    `model_name` defaults: zimage→zimage-base, sd35→sd3.5-medium,
    flux2→flux.2-klein-4b. Pass-through to the per-backend invoke fn.
    """
    if backend == "zimage-img2img":
        result = invoke_zimage_polish(
            image_path, prompt=prompt, negative_prompt=negative_prompt,
            strength=strength, seed=seed, output_dir=output_dir,
            model_name=model_name or "zimage-base",
        )
    elif backend == "sd35-img2img":
        result = invoke_sd35_polish(
            image_path, prompt=prompt, negative_prompt=negative_prompt,
            strength=strength, seed=seed, output_dir=output_dir,
            model_name=model_name or "sd3.5-medium",
        )
    elif backend == "flux2-img2img":
        result = invoke_flux2_polish(
            image_path, prompt=prompt, negative_prompt=negative_prompt,
            strength=strength, seed=seed, output_dir=output_dir,
            model_name=model_name or "flux.2-klein-4b",
        )
    else:
        raise ValueError(f"unknown polish backend {backend!r}; expected one of "
                         f"zimage-img2img, sd35-img2img, flux2-img2img")
    if result["returncode"] != 0 or not result["output_path"]:
        raise RuntimeError(
            f"{backend} polish failed (rc={result['returncode']}): "
            f"{result.get('stderr', '<no stderr>')}"
        )
    return result


# Backend name -> the `module` token used by per-pipeline manifests and by
# `handrefiner._detect_source_pipeline` for sidecar auto-detect.
BACKEND_MODULE = {
    "zimage-img2img": "zimage",
    "sd35-img2img":   "sd35",
    "flux2-img2img":  "flux2",
}


def run_img2img(
    image_path: str | Path,
    *,
    backend: str,
    prompt: str,
    negative_prompt: str = "",
    strength: float = 0.22,
    seed: int = 0,
    output_dir: str | Path,
    model_name: str | None = None,
    cfg_normalization: bool = True,
    width: int | None = None,
    height: int | None = None,
) -> dict:
    """General img2img dispatcher -- the single entry point both the multi
    `batch` clean/polish stages and (indirectly) postproc polish share.

    Unlike `run_polish` (kept verbatim for postproc byte-compat, raises on
    failure), this returns the raw backend result dict WITHOUT raising, so
    callers can do per-image non-fatal handling over a batch.

    `cfg_normalization` is honoured only by the zimage backend (sd35/flux2
    ignore it); `negative_prompt` is honoured by zimage*/sd35, ignored by
    flux2. *(\\* zimage img2img accepts the kwarg.)* This mirrors the §5
    backend-capability matrix in kb-multi-image2.md.
    """
    if backend == "zimage-img2img":
        return invoke_zimage_polish(
            image_path, prompt=prompt, negative_prompt=negative_prompt,
            strength=strength, seed=seed, width=width, height=height,
            output_dir=output_dir, model_name=model_name or "zimage-base",
            cfg_normalization=cfg_normalization,
        )
    if backend == "sd35-img2img":
        return invoke_sd35_polish(
            image_path, prompt=prompt, negative_prompt=negative_prompt,
            strength=strength, seed=seed, width=width, height=height,
            output_dir=output_dir, model_name=model_name or "sd3.5-medium",
        )
    if backend == "flux2-img2img":
        return invoke_flux2_polish(
            image_path, prompt=prompt, negative_prompt=negative_prompt,
            strength=strength, seed=seed, width=width, height=height,
            output_dir=output_dir, model_name=model_name or "flux.2-klein-4b",
        )
    raise ValueError(f"unknown img2img backend {backend!r}; expected one of "
                     f"{sorted(BACKEND_MODULE)}")
