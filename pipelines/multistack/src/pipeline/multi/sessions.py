"""Multi-image session manifest -- the single source of truth for run lineage.

Per kb-multi-image.md §Stage-Independent Execution Proposal -- Revision 2:

- Sessions live in `src/state/sessions/session_<id>.json`.
- Per-pipeline manifests stay clean of any multi-image fields.
- All multi-image lineage (parent_run_id, parent_stage, source, etc.) lives
  here.
- Every reference to a run_id or artifact_id also records the corresponding
  filesystem path alongside it (the path-alongside-id rule).
- Rehash-on-load: every load() walks the artifacts dict, recomputes file
  content hashes, and updates `state` + appends a `history` event when
  drift is detected. Anomalies are warned to stderr AND recorded in the
  session manifest's top-level `anomalies` list.
- Sequential CLI use only -- no locking.
"""

from __future__ import annotations

import json
import secrets
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .._artifact_id import compute_artifact_id, compute_content_hash


SESSIONS_DIR_DEFAULT = "src/state/sessions"


# --- Path helpers ----------------------------------------------------------


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mint_session_id() -> str:
    return f"session_{secrets.token_hex(4)}"


def session_path(session_id: str, sessions_dir: str | Path = SESSIONS_DIR_DEFAULT) -> Path:
    return Path(sessions_dir) / f"{session_id}.json"


def _resolve_sessions_dir(sessions_dir: str | Path) -> Path:
    """Anchor relative sessions_dir to the repo root.

    The user requested `src/state/sessions/`; we make sure that resolves
    consistently regardless of cwd.
    """
    p = Path(sessions_dir)
    if not p.is_absolute():
        # src/pipeline/multi/sessions.py -> repo root is parents[3]
        repo_root = Path(__file__).resolve().parents[3]
        p = repo_root / p
    return p


# --- Dataclasses -----------------------------------------------------------


@dataclass
class RunRecord:
    """One multi-image architecture invocation within a session."""
    run_id: str
    architecture: str                      # "compose-character" | "diversity-grid" | ...
    parent_run_id: str | None = None
    parent_stage: str | None = None
    parent_manifest_path: str | None = None  # path-alongside-id
    entry_stage: str = ""
    exit_stage: str = ""
    status: str = "running"                # "running" | "completed" | "failed"
    started_at: str = ""
    finished_at: str = ""
    manifest_path: str = ""                # path of THIS multi-image run's combined manifest
    session_manifest_stages: list[dict] = field(default_factory=list)
    final_output_path: str = ""
    final_artifact_id: str = ""


@dataclass
class ArtifactRecord:
    """Aggregated session-level record for a single artifact (image, etc.)."""
    artifact_id: str
    first_seen_in_run: str                 # per-pipeline run that originated it
    first_seen_in_arch_run: str            # multi-image run that orchestrated it
    path: str
    kind: str
    byte_size: int
    created_at: str
    stored_hash: str                       # initial content hash; equals artifact_id by construction
    current_hash: str                      # refreshed on each load
    state: str = "fresh"                   # "fresh" | "verified" | "stale" | "unavailable"
    last_verified: str = ""
    history: list[dict] = field(default_factory=list)


@dataclass
class SessionManifest:
    session_id: str
    created_at: str = ""
    last_update: str = ""
    lineage_status: str = "clean"           # "clean" | "broken_at <run_id>:<stage>" | "partial_at <run_id>"
    architectures_used: list[str] = field(default_factory=list)
    runs: list[RunRecord] = field(default_factory=list)
    artifacts: dict[str, ArtifactRecord] = field(default_factory=dict)
    anomalies: list[dict] = field(default_factory=list)

    # ---- factory ----

    @classmethod
    def new(cls) -> "SessionManifest":
        sid = mint_session_id()
        ts = _utc_iso()
        return cls(session_id=sid, created_at=ts, last_update=ts)

    # ---- serialization ----

    def to_dict(self) -> dict:
        d = asdict(self)
        # artifacts is a dict of dataclasses -- asdict on the wrapper dict has
        # already flattened them, but explicit conversion guards against
        # future changes:
        d["artifacts"] = {k: asdict(v) if not isinstance(v, dict) else v
                          for k, v in self.artifacts.items()}
        return d

    def save(self, sessions_dir: str | Path = SESSIONS_DIR_DEFAULT) -> Path:
        self.last_update = _utc_iso()
        d = _resolve_sessions_dir(sessions_dir)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{self.session_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
        return path

    @classmethod
    def load(
        cls,
        session_id: str,
        sessions_dir: str | Path = SESSIONS_DIR_DEFAULT,
        rehash: bool = True,
    ) -> "SessionManifest":
        d = _resolve_sessions_dir(sessions_dir)
        path = d / f"{session_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"session manifest not found: {path}")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        runs = [RunRecord(**r) for r in data.pop("runs", [])]
        artifacts_raw = data.pop("artifacts", {})
        artifacts = {k: ArtifactRecord(**v) for k, v in artifacts_raw.items()}
        sm = cls(runs=runs, artifacts=artifacts, **data)
        if rehash:
            sm._rehash_all()
        return sm

    # ---- run management ----

    def append_run(self, rec: RunRecord) -> None:
        if rec.architecture not in self.architectures_used:
            self.architectures_used.append(rec.architecture)
        self.runs.append(rec)

    def get_run(self, run_id: str) -> RunRecord | None:
        return next((r for r in self.runs if r.run_id == run_id), None)

    # ---- artifact management ----

    def upsert_artifact(self, *, rec_dict: dict, originated_in_run: str,
                        arch_run_id: str) -> ArtifactRecord:
        """Add or update an artifact record. Idempotent on artifact_id.

        rec_dict is the per-pipeline manifest's artifact entry (as produced
        by _artifact_id.make_artifact_record).
        """
        aid = rec_dict["artifact_id"]
        if aid in self.artifacts:
            return self.artifacts[aid]   # already known across runs
        # First time we see this artifact in the session.
        ts = _utc_iso()
        ar = ArtifactRecord(
            artifact_id=aid,
            first_seen_in_run=originated_in_run,
            first_seen_in_arch_run=arch_run_id,
            path=rec_dict["path"],
            kind=rec_dict["kind"],
            byte_size=rec_dict["byte_size"],
            created_at=rec_dict["created_at"],
            stored_hash=aid,                        # stored_hash == artifact_id by construction
            current_hash=aid,
            state="fresh",
            last_verified=ts,
            history=[{
                "at": ts, "event": "created", "by_run": originated_in_run,
                "fields": {
                    "state":        {"before": None, "after": "fresh"},
                    "path":         {"before": None, "after": rec_dict["path"]},
                    "current_hash": {"before": None, "after": aid},
                    "byte_size":    {"before": None, "after": rec_dict["byte_size"]},
                },
            }],
        )
        self.artifacts[aid] = ar
        return ar

    # ---- rehash / state machine ----

    def _record_history(self, ar: ArtifactRecord, event: str,
                        by_run: str | None, field_diffs: dict) -> None:
        ar.history.append({
            "at": _utc_iso(), "event": event, "by_run": by_run,
            "fields": field_diffs,
        })

    def _emit_anomaly(self, kind: str, *, artifact_id: str | None = None,
                      run_id: str | None = None, path: str | None = None,
                      message: str = "") -> None:
        ev = {
            "at": _utc_iso(), "kind": kind,
            "artifact_id": artifact_id, "run_id": run_id, "path": path,
            "message": message,
        }
        self.anomalies.append(ev)
        print(f"WARNING [session {self.session_id}]: {message}", file=sys.stderr)

    def _rehash_one(self, ar: ArtifactRecord) -> None:
        """Refresh state + current_hash for a single artifact. Emits warnings
        and history events on every detected drift / missing file."""
        path = Path(ar.path)
        prev_state = ar.state
        if not path.exists():
            if ar.state != "unavailable":
                self._record_history(ar, "marked_unavailable", by_run=None,
                                     field_diffs={"state": {"before": prev_state, "after": "unavailable"}})
                ar.state = "unavailable"
                self._emit_anomaly(
                    "missing_file", artifact_id=ar.artifact_id, path=ar.path,
                    message=(f"artifact {ar.artifact_id} at {ar.path} is "
                             f"unavailable (was {prev_state!r} as of {ar.last_verified})."),
                )
            return

        try:
            # Use the SAME formula as the original artifact_id (sha256(bytes + created_at)[:8])
            # so the current hash is directly comparable to stored_hash. compute_content_hash
            # is exposed as a separate helper for callers that want byte-only hashing.
            current = compute_artifact_id(path, ar.created_at)
        except Exception as e:
            self._emit_anomaly(
                "rehash_error", artifact_id=ar.artifact_id, path=ar.path,
                message=f"could not rehash artifact {ar.artifact_id} at {ar.path}: {e}",
            )
            return

        prev_hash = ar.current_hash
        prev_size = ar.byte_size
        new_size = path.stat().st_size

        if current == ar.stored_hash:
            # Verified -- still byte-identical to the originally-recorded content.
            ar.last_verified = _utc_iso()
            if prev_state == "fresh":
                # First post-creation verification -- transition to "verified" silently.
                self._record_history(ar, "verified", by_run=None,
                                     field_diffs={"state": {"before": prev_state, "after": "verified"}})
                ar.state = "verified"
            elif prev_state in ("stale", "unavailable"):
                # File was edited then restored to original bytes.
                self._record_history(ar, "verified", by_run=None,
                                     field_diffs={
                                         "state": {"before": prev_state, "after": "verified"},
                                         "current_hash": {"before": prev_hash, "after": current},
                                         "byte_size": {"before": prev_size, "after": new_size},
                                     })
                ar.state = "verified"
                ar.current_hash = current
                ar.byte_size = new_size
            else:
                # Was already "verified" -- no change, just refresh last_verified.
                pass
        else:
            # Drift detected.
            if ar.state != "stale" or current != ar.current_hash:
                self._record_history(ar, "rehash_drift", by_run=None,
                                     field_diffs={
                                         "state": {"before": prev_state, "after": "stale"},
                                         "current_hash": {"before": prev_hash, "after": current},
                                         "byte_size": {"before": prev_size, "after": new_size},
                                     })
                ar.state = "stale"
                ar.current_hash = current
                ar.byte_size = new_size
                self._emit_anomaly(
                    "hash_drift", artifact_id=ar.artifact_id, path=ar.path,
                    message=(f"artifact {ar.artifact_id} at {ar.path} drifted "
                             f"(stored={ar.stored_hash}, current={current}); marked stale."),
                )

    def _rehash_all(self) -> None:
        for ar in self.artifacts.values():
            self._rehash_one(ar)

        # Cross-check: do any reachable runs reference unavailable artifacts?
        for run in self.runs:
            for stage in run.session_manifest_stages:
                for aid in (stage.get("consumed_artifact_ids", []) +
                            stage.get("produced_artifact_ids", [])):
                    ar = self.artifacts.get(aid)
                    if ar and ar.state == "unavailable":
                        # Already warned per-artifact; surface at run-level too
                        self.lineage_status = (
                            f"partial_at {run.run_id}"
                            if self.lineage_status == "clean" else self.lineage_status
                        )


# --- Session lifecycle helpers ---------------------------------------------


def open_or_create(
    session_id: str | None,
    *,
    sessions_dir: str | Path = SESSIONS_DIR_DEFAULT,
    continue_from_run: str | None = None,
) -> tuple[SessionManifest, bool]:
    """Resolve a session for a CLI invocation.

    Returns (session, was_created). Decision logic per kb-multi-image
    §Session lifecycle:

    - explicit `session_id` -> open it (must exist)
    - `continue_from_run` set -> find the session containing that run and use it
    - otherwise -> mint a new session
    """
    if session_id:
        return SessionManifest.load(session_id, sessions_dir=sessions_dir), False
    if continue_from_run:
        owner = find_session_for_run(continue_from_run, sessions_dir=sessions_dir)
        if owner:
            return SessionManifest.load(owner, sessions_dir=sessions_dir), False
        # Run referenced doesn't belong to any session -- error out, the caller
        # should pass --session-id explicitly or omit --continue-from.
        raise FileNotFoundError(
            f"no session contains run_id={continue_from_run!r}; pass --session-id "
            f"explicitly or omit --continue-from to start fresh."
        )
    return SessionManifest.new(), True


def ingest_pipeline_manifest(
    session: SessionManifest,
    *,
    arch_run_id: str,
    pipeline_manifest_path: str | Path,
) -> list[str]:
    """Read a per-pipeline manifest (the JSON written by flux2/sd35/zimage's
    run_pipeline.py) and ingest its artifacts into the session.

    Returns the list of artifact_ids that were upserted.
    """
    p = Path(pipeline_manifest_path)
    if not p.exists():
        # Don't crash -- just log an anomaly and skip.
        session._emit_anomaly(
            "pipeline_manifest_missing",
            run_id=arch_run_id, path=str(p),
            message=f"per-pipeline manifest at {p} not found while ingesting "
                    f"into session {session.session_id}",
        )
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        session._emit_anomaly(
            "pipeline_manifest_unreadable",
            run_id=arch_run_id, path=str(p),
            message=f"could not parse {p}: {e}",
        )
        return []

    pipeline_run_id = data.get("run_id", "")
    aids: list[str] = []
    for rec in data.get("artifacts", []):
        session.upsert_artifact(
            rec_dict=rec,
            originated_in_run=pipeline_run_id or "<unknown_pipeline_run>",
            arch_run_id=arch_run_id,
        )
        aids.append(rec["artifact_id"])
    return aids


def find_session_for_run(
    run_id: str,
    sessions_dir: str | Path = SESSIONS_DIR_DEFAULT,
) -> str | None:
    """Walk session JSONs to find the one containing run_id. Returns
    session_id or None."""
    d = _resolve_sessions_dir(sessions_dir)
    if not d.exists():
        return None
    for p in d.glob("session_*.json"):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for r in data.get("runs", []):
            if r.get("run_id") == run_id:
                return data.get("session_id")
    return None
