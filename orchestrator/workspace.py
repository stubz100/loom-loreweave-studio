"""Durable bundle I/O (M5, P0-8/P0-9) — the persistence core every record reuses.

This is the spine of §6 (Records, IDs, schema, atomic writes) and §5 (the project
workspace on the work disk). It provides:

- **Stable internal IDs** (`prj_…`, `job_…`, …) generated on creation (refs use IDs).
- **Atomic, fsync'd writes** (temp → fsync → `os.replace`) so a crash never leaves a
  half-written record; readers **refuse partial JSON** (a parse error raises, never a
  silent empty record).
- **Schema validation** against the authored JSON Schemas in `schemas/` — dependency
  free (no `jsonschema`, per R97/R103 "keep orchestrator deps minimal"): a small
  draft-07 subset checker (required + types + `const`/`enum`/`pattern`).
- The **`Workspace`** object: the `<project>/` tree (`project.json`, `jobs/`,
  `jobs/logs/`, `lineage/`, `_temp/`, `out/`) with `create()` (empty-folder +
  free-space validation, R80) and `open()` (schema-validated load).
- A **footprint estimator** (R161/R164): episode length × resolution × fps → projected
  PNG-sequence master size → a suggested size cap.

The queue (`runner.py`) and the lineage writer (`lineage.py`) both build on this; the
M0–M4 interim `.loom_state/` + `.dev_out/` are retired in favour of the per-project
`<project>/jobs/queue.json` + `<project>/out/`.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE_SCHEMA_VERSION = 1
_GB = 1024 ** 3

# Project tree (§5). Most subtrees are P0-minimal; later phases populate them lazily.
_SUBDIRS = ("jobs", "jobs/logs", "lineage", "_temp", "out")

# Default project format — Wan2.2 native 1280×720 @ 24fps (R56), WAV 48k/16/stereo.
DEFAULT_FORMAT: dict[str, Any] = {
    "aspect": [16, 9],
    "resolution": [1280, 720],
    "fps": 24,
    "audio_master": {"container": "wav", "rate_hz": 48000, "bits": 16, "channels": 2},
}
DEFAULT_SIZE_CAP_GB = 250   # R164 (raised from 100 to fit R161 PNG-seq masters)
MIN_SIZE_CAP_GB = 50        # R79 floor; no max

# PNG-master footprint heuristic (R161/R164): bytes-per-pixel for a compressed PNG of
# rendered/animated content. Conservative; refined once real masters exist. Exposed so
# the estimator's assumption is inspectable, not buried in a magic number.
PNG_BYTES_PER_PIXEL = 1.5


class WorkspaceError(Exception):
    """A project-workspace validation/IO failure surfaced to the user (→ HTTP 4xx)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str, n: int = 6) -> str:
    """Stable internal id, e.g. `prj_3f9a2c` (§6). References use these, never paths."""
    return f"{prefix}_{uuid.uuid4().hex[:n]}"


# --- atomic JSON I/O ------------------------------------------------------------

def atomic_write_json(path: Path, data: Any) -> None:
    """Write temp → fsync → atomic `os.replace` (§6). A crash mid-write leaves either
    the old file or the new one, never a truncated one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    """Load JSON, **refusing partial/corrupt files** (§6) — a parse error raises a
    WorkspaceError rather than degrading to an empty record."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise WorkspaceError(f"missing record: {path}") from e
    except (json.JSONDecodeError, OSError) as e:
        raise WorkspaceError(f"corrupt/partial record (refused): {path} — {e}") from e


# --- minimal JSON-Schema validation (draft-07 subset, dependency-free) ----------

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
_SCHEMA_CACHE: dict[str, dict] = {}

_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def load_schema(name: str) -> dict:
    """Load (and cache) a JSON Schema file from `orchestrator/schemas/`."""
    if name not in _SCHEMA_CACHE:
        _SCHEMA_CACHE[name] = json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))
    return _SCHEMA_CACHE[name]


def _check_type(value: Any, spec: Any, path: str, errors: list[str]) -> None:
    types = spec if isinstance(spec, list) else [spec]
    # bool is a subclass of int — guard so a boolean doesn't pass as integer/number.
    ok = False
    for t in types:
        py = _TYPE_MAP.get(t, ())
        if t in ("integer", "number") and isinstance(value, bool):
            continue
        if isinstance(value, py):
            ok = True
            break
    if not ok:
        errors.append(f"{path or '<root>'}: expected type {types}, got {type(value).__name__}")


def _validate(data: Any, schema: dict, path: str, errors: list[str]) -> None:
    if "const" in schema and data != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {data!r}")
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: {data!r} not in enum {schema['enum']}")
    if "type" in schema:
        _check_type(data, schema["type"], path, errors)
    if isinstance(data, str):
        if "pattern" in schema and not re.search(schema["pattern"], data):
            errors.append(f"{path}: {data!r} does not match /{schema['pattern']}/")
        if "minLength" in schema and len(data) < schema["minLength"]:
            errors.append(f"{path}: length {len(data)} < minLength {schema['minLength']}")
        if "maxLength" in schema and len(data) > schema["maxLength"]:
            errors.append(f"{path}: length {len(data)} > maxLength {schema['maxLength']}")
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if "minimum" in schema and data < schema["minimum"]:
            errors.append(f"{path}: {data} < minimum {schema['minimum']}")
        if "maximum" in schema and data > schema["maximum"]:
            errors.append(f"{path}: {data} > maximum {schema['maximum']}")
    if isinstance(data, dict):
        for req in schema.get("required", []):
            if req not in data:
                errors.append(f"{path or '<root>'}: missing required '{req}'")
        for key, subspec in schema.get("properties", {}).items():
            if key in data:
                _validate(data[key], subspec, f"{path}.{key}" if path else key, errors)
    if isinstance(data, list):
        if "minItems" in schema and len(data) < schema["minItems"]:
            errors.append(f"{path}: {len(data)} items < minItems {schema['minItems']}")
        if "maxItems" in schema and len(data) > schema["maxItems"]:
            errors.append(f"{path}: {len(data)} items > maxItems {schema['maxItems']}")
        if "items" in schema:
            for i, item in enumerate(data):
                _validate(item, schema["items"], f"{path}[{i}]", errors)


def validate(data: Any, schema_name: str) -> None:
    """Validate `data` against a named schema; raise WorkspaceError on any violation."""
    errors: list[str] = []
    _validate(data, load_schema(schema_name), "", errors)
    if errors:
        raise WorkspaceError(f"{schema_name} validation failed: " + "; ".join(errors))


def validate_project(project: dict) -> None:
    """Schema-validate a project record **and** enforce the cross-field invariant the
    schema can't express: aspect and resolution are **locked together** (§5/P0), i.e.
    `aspect_w · H == aspect_h · W`. Catches e.g. aspect [1,1] with a 1280×720 frame
    before it poisons downstream geometry assumptions (review: Med)."""
    validate(project, "project.schema.json")
    fmt = project.get("format") or {}
    aspect, res = fmt.get("aspect"), fmt.get("resolution")
    if (isinstance(aspect, list) and len(aspect) == 2
            and isinstance(res, list) and len(res) == 2):
        aw, ah = aspect
        rw, rh = res
        if aw * rh != ah * rw:
            raise WorkspaceError(
                f"format geometry inconsistent: aspect {aw}:{ah} does not match "
                f"resolution {rw}×{rh} (require aspect_w·H == aspect_h·W)")


# --- footprint estimator (R161/R164) -------------------------------------------

def estimate_footprint_gb(*, length_s: float, width: int, height: int, fps: int,
                          bytes_per_pixel: float = PNG_BYTES_PER_PIXEL) -> float:
    """Projected PNG-sequence **master** size for `length_s` seconds at `width×height`,
    `fps` (R161 PNG-seq masters). Heuristic; the per-pixel factor is conservative."""
    frames = max(0.0, length_s) * max(1, fps)
    total_bytes = frames * width * height * bytes_per_pixel
    return total_bytes / _GB


def suggest_cap_gb(footprint_gb: float, *, headroom: float = 1.3) -> int:
    """Suggest a size cap from a footprint: footprint × headroom, rounded up to 10 GB,
    floored at the R79 minimum."""
    raw = footprint_gb * headroom
    rounded = int(math.ceil(raw / 10.0) * 10)
    return max(MIN_SIZE_CAP_GB, rounded)


def footprint_report(*, length_s: float, width: int, height: int, fps: int,
                     size_cap_gb: float | None = None) -> dict:
    """Estimator payload for `loom init` (R164): projection + suggested cap + a warning
    if the chosen cap can't hold the projected master."""
    fp = estimate_footprint_gb(length_s=length_s, width=width, height=height, fps=fps)
    suggested = suggest_cap_gb(fp)
    report = {
        "length_s": length_s,
        "resolution": [width, height],
        "fps": fps,
        "bytes_per_pixel": PNG_BYTES_PER_PIXEL,
        "frames": int(length_s * fps),
        "projected_master_gb": round(fp, 2),
        "suggested_cap_gb": suggested,
    }
    if size_cap_gb is not None:
        report["chosen_cap_gb"] = size_cap_gb
        report["cap_sufficient"] = size_cap_gb >= fp
        if size_cap_gb < fp:
            report["warning"] = (f"chosen cap {size_cap_gb} GB < projected master "
                                 f"{round(fp, 1)} GB — raise to ~{suggested} GB")
    return report


# --- validation helpers (R80) ---------------------------------------------------

def validate_empty_dest(dest: Path) -> None:
    """The destination must be empty (R80) — refuse a non-empty folder so init never
    clobbers existing data."""
    if dest.exists():
        if not dest.is_dir():
            raise WorkspaceError(f"destination is not a directory: {dest}")
        if any(dest.iterdir()):
            raise WorkspaceError(f"destination folder is not empty: {dest}")


def free_space_gb(dest: Path) -> float:
    """Free space (GB) on the disk that would hold `dest`, probing the nearest existing
    parent (the folder itself may not exist yet)."""
    probe = dest
    while not probe.exists():
        if probe.parent == probe:
            break
        probe = probe.parent
    return shutil.disk_usage(probe).free / _GB


def validate_free_space(dest: Path, size_cap_gb: float) -> None:
    """Free space on the chosen disk must be ≥ the size cap (R80)."""
    free = free_space_gb(dest)
    if free < size_cap_gb:
        raise WorkspaceError(
            f"insufficient free space: {round(free, 1)} GB available on "
            f"{dest.anchor or dest} < requested cap {size_cap_gb} GB")


# --- the project workspace ------------------------------------------------------

class Workspace:
    """A project folder on the work disk (§5). Holds the durable queue, outputs, logs,
    and lineage. All bundle writes go through here (atomic, orchestrator-owned)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()

    # tree (§5)
    @property
    def project_json(self) -> Path: return self.path / "project.json"
    @property
    def jobs_dir(self) -> Path: return self.path / "jobs"
    @property
    def logs_dir(self) -> Path: return self.path / "jobs" / "logs"
    @property
    def queue_path(self) -> Path: return self.path / "jobs" / "queue.json"
    @property
    def lineage_dir(self) -> Path: return self.path / "lineage"
    @property
    def lineage_index(self) -> Path: return self.path / "lineage" / "index.json"
    @property
    def temp_dir(self) -> Path: return self.path / "_temp"
    @property
    def out_dir(self) -> Path: return self.path / "out"

    def log_path(self, job_id: str) -> Path:
        return self.logs_dir / f"{job_id}.log"

    def _ensure_tree(self) -> None:
        for sub in _SUBDIRS:
            (self.path / sub).mkdir(parents=True, exist_ok=True)

    @classmethod
    def create(cls, dest: Path, *, name: str, fmt: dict | None = None,
               size_cap_gb: float = DEFAULT_SIZE_CAP_GB) -> "Workspace":
        """`loom init` (§5): validate empty dest (R80) + free space ≥ cap (R80), build
        the tree, write `project.json` atomically. Returns the open workspace."""
        dest = Path(dest).resolve()
        if not name or not name.strip():
            raise WorkspaceError("project name must not be empty")
        if size_cap_gb < MIN_SIZE_CAP_GB:
            raise WorkspaceError(f"size cap {size_cap_gb} GB below the {MIN_SIZE_CAP_GB} GB floor (R79)")
        validate_empty_dest(dest)
        validate_free_space(dest, size_cap_gb)

        project = {
            "schema_version": WORKSPACE_SCHEMA_VERSION,
            "id": new_id("prj"),
            "name": name.strip(),
            "created_at": _now(),
            "workspace_path": str(dest),
            "format": fmt or json.loads(json.dumps(DEFAULT_FORMAT)),
            "size_cap_gb": size_cap_gb,
        }
        # Validate (schema + geometry lock) BEFORE any side effect, so a bad format
        # never leaves a half-built tree on disk.
        validate_project(project)

        ws = cls(dest)
        ws._ensure_tree()
        atomic_write_json(ws.project_json, project)
        return ws

    @classmethod
    def open(cls, path: Path) -> "Workspace":
        """Open an existing project: load + schema-validate `project.json`, ensure the
        tree exists (idempotent — heals a missing subdir)."""
        ws = cls(Path(path))
        if not ws.project_json.is_file():
            raise WorkspaceError(f"not a loom project (no project.json): {ws.path}")
        project = read_json(ws.project_json)
        validate_project(project)
        ws._ensure_tree()
        return ws

    def load_project(self) -> dict:
        project = read_json(self.project_json)
        validate_project(project)
        return project

    def info(self) -> dict:
        """Active-project summary for the API/UI."""
        project = self.load_project()
        return {
            "open": True,
            "path": str(self.path),
            "id": project["id"],
            "name": project["name"],
            "format": project["format"],
            "size_cap_gb": project["size_cap_gb"],
            "free_space_gb": round(free_space_gb(self.path), 1),
        }
