"""LTX-Video 0.9.x (LTXV) pipeline — fast iteration sketchpad + multi-condition toolkit.

See .github/copilot/kb-ltx09.md for the full plan and capability notes.

Phase 1+2 scope (this implementation):
  - 2B distilled I2V (`2b_0.9.8_distilled`) — the fast daily driver
  - T2V from prompt only
  - Standalone CLI (run_pipeline.py + generate.py)
  - --resume-latents support

Phase 3+ scope (deferred):
  - keyframes / extend / control subcommands (LTXConditionPipeline modes)
  - 13B variants with offload
  - ControlNet adapters (pose / depth / canny)
"""
