"""Pipeline adapters — one module per pipeline (kb-loom-p0.md §8).

An adapter is the shock-absorber between the orchestrator and an existing
`run_pipeline.py` worker: it turns a typed JobSpec into a CLI invocation and
normalizes the worker's result into a common CompletionRecord envelope.

M1 ships exactly one (`zimage`) at minimal contract level (build_argv +
read-the-saved-manifest). The full contract (capabilities/presence, progress,
cancel, manifest-as-truth) is M3.
"""

from .base import JobSpec, CompletionRecord  # noqa: F401
