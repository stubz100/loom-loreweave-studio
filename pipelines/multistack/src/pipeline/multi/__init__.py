"""Multi-pipeline orchestrator — composes Flux2, SD 3.5, and Z-Image in chained workflows.

Phase A delivers the orchestration layer plus a single architecture (`diversity-grid`)
that exercises all three pipelines in T2I mode. Later phases extend per-pipeline modes
(B) and add preprocessing tooling (C). See .github/copilot/kb-multi-image.md.
"""
