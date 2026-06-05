"""Lineage-edge writer (M5, P0-8 / R98) — the embryo of provenance tracking.

Every generated output gets a **lineage edge**:
`{requester_id} → job_id → output_file → manifest`. At P0 that's the subset we have
(project + job + output + manifest); `asset@version` + LoRA version slots are present
but `null` until assets/LoRAs exist (R98). The edges live in a small **rebuildable**
`lineage/index.json` so provenance reads never have to scan every manifest at startup.

Writes go through `workspace.atomic_write_json` (atomic, orchestrator-owned, §6); the
index is validated against `lineage.schema.json`.
"""

from __future__ import annotations

from datetime import datetime, timezone

try:
    from . import workspace as ws_mod
    from .workspace import Workspace
except ImportError:  # pragma: no cover - direct-run convenience
    import workspace as ws_mod  # type: ignore
    from workspace import Workspace  # type: ignore

LINEAGE_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_index() -> dict:
    return {"schema_version": LINEAGE_SCHEMA_VERSION, "edges": []}


def load_index(ws: Workspace) -> dict:
    """Load the lineage index (validated); an absent index is an empty one."""
    if not ws.lineage_index.is_file():
        return _empty_index()
    try:
        data = ws_mod.read_json(ws.lineage_index)
        ws_mod.validate(data, "lineage.schema.json")
        return data
    except ws_mod.WorkspaceError:
        # A corrupt index is rebuildable — start fresh rather than blocking generation.
        return _empty_index()


def make_edge(job: dict, *, asset_version: str | None = None,
              lora_version: str | None = None) -> dict:
    """Build the lineage edge for a finished job's output (R98 P0 subset)."""
    result = job.get("result") or {}
    return {
        "job_id": job["id"],
        "requester_id": job.get("requester_id", ""),
        "output_file": result.get("output_name") or "",
        "manifest": result.get("manifest_path"),
        "asset_version": asset_version,
        "lora_version": lora_version,
        "created_at": _now(),
    }


def record_output(ws: Workspace, job: dict, *, asset_version: str | None = None,
                  lora_version: str | None = None) -> dict:
    """Append (or replace, on retry) the lineage edge for `job` and persist the index
    atomically. Idempotent per job_id. Returns the edge."""
    edge = make_edge(job, asset_version=asset_version, lora_version=lora_version)
    index = load_index(ws)
    index["edges"] = [e for e in index["edges"] if e.get("job_id") != job["id"]]
    index["edges"].append(edge)
    ws_mod.validate(index, "lineage.schema.json")
    ws_mod.atomic_write_json(ws.lineage_index, index)
    return edge
