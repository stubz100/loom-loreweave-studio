"""Walk session manifests and print lineage trees.

Per kb-multi-image.md §Stage-Independent Execution Proposal -- Revision 2:

    python -m pipeline.multi.run_pipeline lineage <run_id-or-session_id>

Builds a parent->children tree from the `parent_run_id` chain inside each
session's `runs[]`. Output is human-readable text by default, or JSON via
`--format json` for scripting.
"""

from __future__ import annotations

import json
from pathlib import Path

from .sessions import SESSIONS_DIR_DEFAULT, _resolve_sessions_dir


def _load_session_data(sessions_dir: str | Path = SESSIONS_DIR_DEFAULT) -> dict[str, dict]:
    """Read every session_*.json under sessions_dir into a {session_id: dict} map."""
    d = _resolve_sessions_dir(sessions_dir)
    if not d.exists():
        return {}
    out: dict[str, dict] = {}
    for p in sorted(d.glob("session_*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            sid = data.get("session_id")
            if sid:
                out[sid] = data
        except Exception as e:
            print(f"WARNING: could not parse {p}: {e}")
    return out


def _resolve_target(target: str, all_sessions: dict[str, dict]) -> str | None:
    """Accept either a session_id or a run_id. Return the matching session_id."""
    if target in all_sessions:
        return target
    for sid, data in all_sessions.items():
        for r in data.get("runs", []):
            if r.get("run_id") == target:
                return sid
    return None


def _build_tree(runs: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Return (roots, children_by_parent). roots have parent_run_id=None."""
    roots = [r for r in runs if not r.get("parent_run_id")]
    children: dict[str, list[dict]] = {}
    for r in runs:
        parent = r.get("parent_run_id")
        if parent:
            children.setdefault(parent, []).append(r)
    return roots, children


def _format_run_summary(r: dict) -> str:
    arch = r.get("architecture", "?")
    es, xs = r.get("entry_stage", "?"), r.get("exit_stage", "?")
    status = r.get("status", "?")
    extra = []
    for stage in r.get("session_manifest_stages", []):
        if stage.get("ideation_mode"):
            extra.append(f"ideation_mode={stage['ideation_mode']}")
            break
    extras = (" " + ", ".join(extra)) if extra else ""
    return f"{arch} [{es} -> {xs}] status={status}{extras}"


def render_tree(session_id: str, sessions_dir: str | Path = SESSIONS_DIR_DEFAULT,
                show_failed: bool = True, depth_limit: int | None = None) -> str:
    all_sessions = _load_session_data(sessions_dir=sessions_dir)
    if session_id not in all_sessions:
        return f"session {session_id} not found under {sessions_dir}"
    data = all_sessions[session_id]

    runs = data.get("runs", [])
    if not show_failed:
        runs = [r for r in runs if r.get("status") != "failed"]

    roots, children = _build_tree(runs)
    lines: list[str] = []
    archs = data.get("architectures_used", [])
    lines.append(
        f"{session_id} (created {data.get('created_at', '?')}, "
        f"last_update {data.get('last_update', '?')}, "
        f"lineage_status {data.get('lineage_status', '?')}, "
        f"archs {archs})"
    )

    def _walk(r: dict, prefix: str, is_last: bool, depth: int) -> None:
        if depth_limit is not None and depth > depth_limit:
            return
        # ASCII tree characters -- avoids Windows cp1252 encoding errors when
        # output is captured by subprocess or piped to a non-UTF-8 stream.
        connector = "+-- "
        rid = r.get("run_id", "?")
        lines.append(f"{prefix}{connector}{rid}  {_format_run_summary(r)}")
        if r.get("final_output_path"):
            sub_prefix = prefix + ("    " if is_last else "|   ")
            lines.append(f"{sub_prefix}    final: {r['final_output_path']}")
            if r.get("final_artifact_id"):
                lines.append(f"{sub_prefix}    final_artifact_id: {r['final_artifact_id']}")
        kids = children.get(rid, [])
        new_prefix = prefix + ("    " if is_last else "|   ")
        for i, k in enumerate(kids):
            _walk(k, new_prefix, i == len(kids) - 1, depth + 1)

    for i, root in enumerate(roots):
        _walk(root, "", i == len(roots) - 1, 0)

    # Anomalies summary
    anomalies = data.get("anomalies", [])
    if anomalies:
        lines.append("")
        lines.append(f"Anomalies ({len(anomalies)}):")
        for a in anomalies[-10:]:        # last 10
            lines.append(f"  [{a.get('at')}] {a.get('kind')}: {a.get('message', '')}")

    return "\n".join(lines)


def render_json(session_id: str, sessions_dir: str | Path = SESSIONS_DIR_DEFAULT) -> str:
    all_sessions = _load_session_data(sessions_dir=sessions_dir)
    if session_id not in all_sessions:
        return json.dumps({"error": f"session {session_id} not found"})
    return json.dumps(all_sessions[session_id], indent=2, default=str)


def render_artifact_lookup(
    artifact_id: str,
    sessions_dir: str | Path = SESSIONS_DIR_DEFAULT,
    fmt: str = "tree",
) -> str:
    """Find an artifact across all sessions and report its location(s)."""
    all_sessions = _load_session_data(sessions_dir=sessions_dir)
    hits = []
    for sid, data in all_sessions.items():
        if artifact_id in (data.get("artifacts") or {}):
            ar = data["artifacts"][artifact_id]
            # Find which run(s) reference it
            referencing_runs = []
            for r in data.get("runs", []):
                for stage in r.get("session_manifest_stages", []):
                    if (artifact_id in stage.get("produced_artifact_ids", []) or
                        artifact_id in stage.get("consumed_artifact_ids", []) or
                        stage.get("chosen_artifact_id") == artifact_id):
                        referencing_runs.append(r.get("run_id"))
                        break
            hits.append({
                "session_id": sid,
                "artifact_id": artifact_id,
                "path": ar.get("path"),
                "state": ar.get("state"),
                "byte_size": ar.get("byte_size"),
                "first_seen_in_run": ar.get("first_seen_in_run"),
                "first_seen_in_arch_run": ar.get("first_seen_in_arch_run"),
                "referencing_runs": referencing_runs,
            })
    if fmt == "json":
        return json.dumps(hits, indent=2, default=str)
    if not hits:
        return f"artifact {artifact_id} not found in any session under {sessions_dir}"
    out = []
    for h in hits:
        out.append(f"{h['artifact_id']}  state={h['state']}  size={h['byte_size']}B")
        out.append(f"  session:        {h['session_id']}")
        out.append(f"  path:           {h['path']}")
        out.append(f"  first seen run: {h['first_seen_in_run']} (arch_run {h['first_seen_in_arch_run']})")
        if h["referencing_runs"]:
            out.append(f"  referenced by:  {', '.join(h['referencing_runs'])}")
        out.append("")
    return "\n".join(out)
