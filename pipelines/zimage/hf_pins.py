"""The revisions the orchestrator pinned for this job (M2.17, kb-loom-cache.md).

loom resolves every cached file at admission and hands the worker `LOOM_HF_REVISIONS`, a JSON
map of repo id → commit. A loader that passes the pin as `revision=` reads the same bytes the
orchestrator judged present, immune to a `refs/main` that drifted. No torch here.
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
        pins = json.loads(raw) or {}
    except (ValueError, TypeError):
        return None
    if not isinstance(pins, dict):
        return None
    want = str(repo_id).lower()
    for k, v in pins.items():
        if str(k).lower() == want and v:
            return str(v)
    return None
