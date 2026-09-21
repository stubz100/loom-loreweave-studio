"""The revisions the orchestrator pinned for this job (M2.17 step a, kb-loom-cache.md).

loom resolves every cached file at admission and hands the worker `LOOM_HF_REVISIONS`, a JSON
map of repo id → commit. A worker that passes the pin to `hf_hub_download(revision=...)` reads
the same bytes the orchestrator judged present — immune to a `refs/main` that drifted to a
snapshot holding only one file. No torch here: the orchestrator's tests import this file.
"""

from __future__ import annotations

import json
import os

ENV = "LOOM_HF_REVISIONS"


def pinned_revision(repo_id: str) -> str | None:
    raw = os.environ.get(ENV)
    if not raw:
        return None
    try:
        rev = (json.loads(raw) or {}).get(repo_id)
    except (ValueError, TypeError, AttributeError):
        return None
    return rev or None
