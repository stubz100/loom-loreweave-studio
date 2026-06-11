"""Shared test fixtures.

`tmp_path` is overridden to land under **`<monorepo>/loom/testing/<test-name>/`**
instead of the pytest default in AppData temp (user request 2026-06-10): test-created
projects/queues/outputs stay inspectable next to the user's real project
(`loom/test/`). The folder is wiped at the START of each session, so the latest
run's artifacts persist until the next run.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

# tests/ -> orchestrator -> loom-loreweave-studio -> loom -> loom/testing
TESTING_ROOT = Path(__file__).resolve().parents[3] / "testing"


@pytest.fixture(scope="session", autouse=True)
def _fresh_testing_root():
    """Wipe last session's artifacts, keep this session's for inspection."""
    shutil.rmtree(TESTING_ROOT, ignore_errors=True)
    TESTING_ROOT.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture()
def tmp_path(request, _fresh_testing_root) -> Path:  # overrides pytest's builtin
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)[:60]
    p = TESTING_ROOT / name
    n = 1
    while p.exists():
        p = TESTING_ROOT / f"{name}_{n}"
        n += 1
    p.mkdir(parents=True)
    return p
