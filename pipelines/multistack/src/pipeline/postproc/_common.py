"""Shared post-processing types and IO helpers.

`PostprocManifest` mirrors the `PipelineManifest` shape used by `sd35` /
`flux2` / `zimage` so `multi/`'s `SessionManifest` ingests post-processing
runs identically to generation runs (same `run_id` + `artifacts[]` contract;
see `pipeline/_artifact_id.py`).

Each post-processing module (handrefiner, face_restore, ...) extends this
class with module-specific fields when needed; otherwise the base class is
used as-is.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Reuse the per-pipeline _artifact_id helper so run_id / artifact_id formats
# match across the project.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _artifact_id  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class StageRecord:
    name: str
    status: str = "pending"  # pending | running | completed | failed
    start_time: float = 0.0
    end_time: float = 0.0
    duration_s: float = 0.0
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    debug: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class PostprocManifest:
    """Manifest for a post-processing run.

    Mirrors `pipeline.{flux2,sd35,zimage}.manifest.PipelineManifest` so the
    multi-image session manifest can ingest postproc runs without any special
    case. The `module` field distinguishes "handrefiner" from "face_restore"
    etc. for readers; the `parent_artifact_id` field links the postproc run
    back to the upstream image it was applied to (so session-manifest lineage
    walks resolve correctly).
    """

    module: str                          # "handrefiner", "face_restore", ...
    input_image: str                     # path to the image being post-processed
    seed: int = 0
    width: int = 0                       # filled in once the image is loaded
    height: int = 0
    created_at: str = ""
    pipeline_start: float = 0.0
    pipeline_end: float = 0.0
    pipeline_duration_s: float = 0.0
    output_path: str = ""                # final artifact this run produced
    device: str = "cuda"
    stages: list[StageRecord] = field(default_factory=list)
    # --- multi-image-aware fields, identical to per-pipeline manifests ---
    run_id: str = ""
    artifacts: list[dict] = field(default_factory=list)
    # --- postproc-specific lineage ---
    parent_artifact_id: str = ""         # the artifact_id of the upstream image, if known
    # --- soft signals + run bookkeeping (Commit B, 2026-05-02) ---
    # warnings: rows shaped {stage, severity, message, ts_utc}. Captures
    # non-fatal issues (VLM unreachable, schema parse failure that fell
    # through to a heuristic, ...) that the orchestrator decided to recover
    # from rather than block on. Empty when no issues.
    warnings: list[dict] = field(default_factory=list)
    # *_prompt_source: stamped at run_pipeline-time so a batch post-mortem
    # can distinguish auto-prompted runs from operator-prompted runs without
    # grepping logs.
    # inpaint_prompt_source ∈ {"explicit", "auto", "default_fallback", ""}
    # polish_prompt_source  ∈ {"explicit", "auto", "sidecar", "default_fallback", ""}
    inpaint_prompt_source: str = ""
    polish_prompt_source: str = ""

    def begin_stage(self, name: str, inputs: dict) -> StageRecord:
        rec = StageRecord(name=name, status="running", start_time=time.time(), inputs=inputs)
        self.stages.append(rec)
        return rec

    def end_stage(self, rec: StageRecord, outputs: dict, debug: dict | None = None) -> None:
        rec.end_time = time.time()
        rec.duration_s = round(rec.end_time - rec.start_time, 4)
        rec.status = "completed"
        rec.outputs = outputs
        if debug:
            rec.debug = debug

    def fail_stage(self, rec: StageRecord, error: str) -> None:
        rec.end_time = time.time()
        rec.duration_s = round(rec.end_time - rec.start_time, 4)
        rec.status = "failed"
        rec.error = error

    def add_warning(self, *, stage: str, severity: str, message: str) -> dict:
        """Record a non-fatal issue that the orchestrator recovered from.

        Use for things like "VLM subprocess crashed; fell through to
        templated baseline prompt" — the run continued, but downstream
        readers should know which path was taken. Returns the appended row
        for caller-side inspection.
        """
        from datetime import datetime, timezone
        row = {
            "stage": stage,
            "severity": severity,
            "message": message,
            "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.warnings.append(row)
        return row

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, default=str)

    @staticmethod
    def load(path: Path) -> "PostprocManifest":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        stages = [StageRecord(**s) for s in data.pop("stages", [])]
        # Forward-compat: drop unknown fields and default-fill the new ones.
        # Keeps old manifests loadable after we add fields.
        known = {f.name for f in PostprocManifest.__dataclass_fields__.values()}
        clean = {k: v for k, v in data.items() if k in known}
        return PostprocManifest(**clean, stages=stages)


def load_image_rgb(image_path: str | Path):
    """Load an image as an HxWx3 uint8 RGB ndarray."""
    import numpy as np
    from PIL import Image
    img = Image.open(str(image_path)).convert("RGB")
    return np.asarray(img)


def resolve_repo_path(p: str | Path) -> Path:
    """Anchor a relative path to the repo root, mirroring per-pipeline behavior.

    Same rationale as `pipeline.zimage.run_pipeline.run`: when called as a
    subprocess from inside a sub-package directory, relative paths would
    otherwise resolve under that sub-package.
    """
    path = Path(p)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def append_artifact(
    manifest: PostprocManifest,
    file_path: str | Path,
    *,
    kind: str,
    role: str,
    produced_by_stage: str,
) -> dict:
    """Append an artifact record and return it (caller may inspect)."""
    record = _artifact_id.make_artifact_record(
        file_path, kind=kind, produced_by_stage=produced_by_stage,
    )
    record["role"] = role
    if manifest.parent_artifact_id:
        record["parent_artifact_id"] = manifest.parent_artifact_id
    manifest.artifacts.append(record)
    return record


def mint_run_id(seed: int) -> str:
    return _artifact_id.mint_run_id(seed)
