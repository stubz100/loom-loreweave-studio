"""Shared run_id and artifact_id helpers for all per-pipeline manifests.

These are owned by the *individual* pipelines (flux2, sd35, zimage), not the
multi-image layer. The multi-image session manifest only consumes them.

Per kb-multi-image.md §Stage-Independent Execution Proposal -- Revision 2:

  - run_id format: "run_<UTC-timestamp>_s<seed>"
    Example: "run_20260425T123456Z_s42"
    Sortable by lexicographic order, informative, stable across re-imports.

  - artifact_id format: "art_" + sha256(file_bytes + iso_created_at)[:8]
    Stable per (file_bytes, creation timestamp). Survives file moves/renames.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path


def mint_run_id(seed: int, when: datetime | None = None) -> str:
    """Return a run_id of the form 'run_<UTC-timestamp>_s<seed>'.

    The timestamp uses ISO-8601 basic format ('compact', no separators) so
    file paths and lexicographic sort align: ``20260425T123456Z``.
    """
    when = when or datetime.now(timezone.utc)
    ts = when.strftime("%Y%m%dT%H%M%SZ")
    return f"run_{ts}_s{seed}"


def compute_artifact_id(file_path: str | Path, created_at_iso: str) -> str:
    """Compute the stable artifact_id for a file.

    Hashes the concatenation of file bytes + the ISO-8601 creation timestamp,
    then takes the first 8 hex chars and prefixes with 'art_'. Two saves of
    the same content at different times produce different artifact_ids; this
    is intentional -- the id captures the lineage position, not just bytes.
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"cannot compute artifact_id for missing file {p}")
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    h.update(created_at_iso.encode("utf-8"))
    return f"art_{h.hexdigest()[:8]}"


def compute_content_hash(file_path: str | Path) -> str:
    """Compute the hash of file bytes only (no timestamp).

    Used by the multi-image session manifest's rehash-on-load drift check:
    we recompute this and compare to the artifact's `current_hash` to detect
    external edits. Note this is NOT the same as artifact_id (which mixes
    in the creation timestamp). The session manifest stores both.
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"cannot hash missing file {p}")
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return f"art_{h.hexdigest()[:8]}"


def make_artifact_record(
    file_path: str | Path,
    *,
    kind: str,
    produced_by_stage: str,
    created_at: str | None = None,
) -> dict:
    """Build the per-artifact record appended to PipelineManifest.artifacts."""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"cannot record missing artifact {p}")
    if created_at is None:
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    artifact_id = compute_artifact_id(p, created_at)
    return {
        "artifact_id":       artifact_id,
        "path":              str(p),
        "kind":              kind,
        "byte_size":         p.stat().st_size,
        "created_at":        created_at,
        "produced_by_stage": produced_by_stage,
    }
