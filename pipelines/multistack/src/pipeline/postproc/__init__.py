"""Image post-processing modules — runs after a per-pipeline `run_pipeline.py`.

See [.github/copilot/kb-postproc-img.md](../../../.github/copilot/kb-postproc-img.md)
for the design rationale, license matrix, and per-pipeline integration plan.

Submodules:
  * handrefiner -- hand anatomy fix via Mesh Graphormer + Z-Image inpaint
  * face_restore (later) -- CodeFormer / GFPGAN
  * upscale (later) -- Real-ESRGAN
"""
