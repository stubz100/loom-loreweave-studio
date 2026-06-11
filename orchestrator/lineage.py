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


def make_edges(job: dict, *, asset_version: str | None = None,
               lora_version: str | None = None) -> list[dict]:
    """Build the lineage edges for a finished job — **one edge per output** (R98: every
    generated image is traceable; review 2026-06-10 — a batch/multi job yields N outputs,
    and recording only `output_name` lost provenance for the other N−1). Keyed by
    `job_id` + `output_file`; the per-output manifest comes from `output_meta` when the
    batch recorded one, else the job-level manifest."""
    result = job.get("result") or {}
    names = result.get("output_names") or ([result["output_name"]]
                                           if result.get("output_name") else [])
    meta = result.get("output_meta") or {}
    base = {
        "job_id": job["id"],
        "requester_id": job.get("requester_id", ""),
        "manifest": result.get("manifest_path"),
        "asset_version": asset_version,
        "lora_version": lora_version,
        # P1: which AssetProfile version + bootstrap stage (A casting / B expansion / C curation)
        # produced this image (§4 lineage), read off the job record when set.
        "profile_version_id": job.get("profile_version_id"),
        "stage": job.get("stage"),
        "created_at": _now(),
    }
    edges = []
    for n in names:
        e = {**base, "output_file": n}
        m = meta.get(n) or {}
        if m.get("manifest_path"):
            e["manifest"] = m["manifest_path"]
        edges.append(e)
    return edges


def remove_edge(ws: Workspace, job_id: str) -> bool:
    """Drop a job's lineage edge(s) from the index (atomic) — all of them for a
    multi-output job. Used when a generation is deleted so the index never references
    a removed output. Returns True if any was removed. Idempotent (absent → False,
    no write)."""
    index = load_index(ws)
    kept = [e for e in index["edges"] if e.get("job_id") != job_id]
    if len(kept) == len(index["edges"]):
        return False
    index["edges"] = kept
    ws_mod.validate(index, "lineage.schema.json")
    ws_mod.atomic_write_json(ws.lineage_index, index)
    return True


def record_output(ws: Workspace, job: dict, *, asset_version: str | None = None,
                  lora_version: str | None = None) -> list[dict]:
    """Append (or replace, on retry) the lineage edges for `job` — one per output —
    and persist the index atomically. Idempotent per job_id. Returns the edges."""
    edges = make_edges(job, asset_version=asset_version, lora_version=lora_version)
    index = load_index(ws)
    index["edges"] = [e for e in index["edges"] if e.get("job_id") != job["id"]]
    index["edges"].extend(edges)
    ws_mod.validate(index, "lineage.schema.json")
    ws_mod.atomic_write_json(ws.lineage_index, index)
    return edges
