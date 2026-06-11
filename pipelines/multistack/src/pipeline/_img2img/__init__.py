"""Shared img2img backend dispatch (kb-multi-image2.md §6, P2).

Single source of truth for "run an image through {zimage,sd35,flux2}
`--mode img2img` as a VRAM-isolated subprocess." Used by:

  - postproc handrefiner Stage 7 polish  (re-exported from
    `handrefiner.stage5_inpaint` -- behaviour byte-identical to pre-P2)
  - the v2 multi `batch` clean-all / polish-all stages (P3 / P4)

This package has **no dependency on `pipeline.postproc`** -- the dependency
direction is postproc -> _img2img, never the reverse.
"""

from .autodetect import detect_source_pipeline  # noqa: F401
from .backends import (  # noqa: F401
    BACKEND_MODULE,
    invoke_flux2_polish,
    invoke_sd35_polish,
    invoke_zimage_polish,
    run_img2img,
    run_polish,
)
