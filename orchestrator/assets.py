"""L2 Asset Studio records (P1 §3.4) — **AssetProfile** + **ProfileVersion**.

M1 is the scaffold: create a profile with a single `v1_base` version (no copy-on-create /
finalize machinery — that's M5). Records inherit P0's IDs/schema/atomic-write rules
(`profile.schema.json`, `version.schema.json`). Folder layout (§3.1, §4):

    assets/<asset_class>/<slug>/
      ├── profile.json
      └── versions/v1_base/
          ├── version.json
          └── casting/ · refs/ · faces/   (created lazily by later stages)

The display name lives in the record; the folder is a slug; references use the stable id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

try:
    from . import workspace as ws_mod
    from . import logsetup
    from .workspace import Workspace, new_id, slugify
except ImportError:  # pragma: no cover - direct-run convenience
    import workspace as ws_mod  # type: ignore
    import logsetup  # type: ignore
    from workspace import Workspace, new_id, slugify  # type: ignore

LOG = logsetup.get_logger()

PROFILE_SCHEMA_VERSION = 1
ASSET_CLASSES = ("characters", "props", "scenes")
_VERSION_SUBDIRS = ("casting", "refs", "faces")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- paths ----------------------------------------------------------------------

def _profile_path(ws: Workspace, asset_class: str, slug: str) -> Path:
    return ws.asset_dir(asset_class, slug) / "profile.json"


def _version_dir(ws: Workspace, asset_class: str, slug: str, version_name: str) -> Path:
    return ws.asset_dir(asset_class, slug) / "versions" / version_name


# --- create ---------------------------------------------------------------------

def create_asset(ws: Workspace, *, name: str, asset_class: str = "characters") -> dict:
    """Create an AssetProfile + a single `v1_base` version (M1 scaffold). Raises
    WorkspaceError on a bad class / empty name / slug collision within the class."""
    if asset_class not in ASSET_CLASSES:
        raise ws_mod.WorkspaceError(f"unknown asset_class {asset_class!r} (one of {ASSET_CLASSES})")
    if not name or not name.strip():
        raise ws_mod.WorkspaceError("asset name must not be empty")
    slug = slugify(name)
    adir = ws.asset_dir(asset_class, slug)
    if adir.exists():
        raise ws_mod.WorkspaceError(f"an asset named {name!r} already exists in {asset_class}")

    ver_id = new_id("ver")
    vdir = _version_dir(ws, asset_class, slug, "v1_base")
    for sub in _VERSION_SUBDIRS:
        (vdir / sub).mkdir(parents=True, exist_ok=True)

    version = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "id": ver_id,
        "name": "v1_base",
        "derived_from": None,
        "finalized": False,        # M1 = Saved, not Finalized (R119)
        "saved_at": _now(),
        "prompt_template": "",
        "anchor_ref": None,
        "ref_set": [],
        "casting": [],
    }
    ws_mod.validate(version, "version.schema.json")
    ws_mod.atomic_write_json(vdir / "version.json", version)

    profile = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        # Neutral asset id (review): the same record backs characters/props/scenes, so the
        # id must not be character-shaped. `asset_class` carries the kind.
        "id": new_id("ast"),
        "name": name.strip(),
        "asset_class": asset_class,
        "slug": slug,
        "created_at": _now(),
        "active_version": ver_id,
        "versions": [ver_id],
    }
    ws_mod.validate(profile, "profile.schema.json")
    ws_mod.atomic_write_json(_profile_path(ws, asset_class, slug), profile)
    return {"profile": profile, "versions": [version]}


# --- read -----------------------------------------------------------------------

def _iter_profiles(ws: Workspace):
    if not ws.assets_dir.is_dir():
        return
    for cls_dir in sorted(ws.assets_dir.iterdir()):
        if not cls_dir.is_dir():
            continue
        for asset_dir in sorted(cls_dir.iterdir()):
            pj = asset_dir / "profile.json"
            if pj.is_file():
                try:
                    profile = ws_mod.read_json(pj)
                    ws_mod.validate(profile, "profile.schema.json")
                    yield asset_dir, profile
                except ws_mod.WorkspaceError as e:
                    # Skip a corrupt profile so it doesn't break the whole list — but say so
                    # loudly (review: corruption must surface, not silently disappear).
                    LOG.warning("skipping corrupt profile %s: %s", pj, e)
                    continue


def list_assets(ws: Workspace) -> dict:
    """Library summary for the L2 tree (id/name/class/active/version count)."""
    assets = []
    for _adir, p in _iter_profiles(ws):
        assets.append({"id": p["id"], "name": p["name"], "asset_class": p["asset_class"],
                       "slug": p.get("slug"), "active_version": p["active_version"],
                       "version_count": len(p["versions"])})
    return {"assets": assets}


def _load_versions(asset_dir: Path, profile: dict) -> list[dict]:
    out = []
    vroot = asset_dir / "versions"
    if vroot.is_dir():
        for vdir in sorted(vroot.iterdir()):
            vj = vdir / "version.json"
            if vj.is_file():
                try:
                    v = ws_mod.read_json(vj)
                    ws_mod.validate(v, "version.schema.json")
                    out.append(v)
                except ws_mod.WorkspaceError as e:
                    LOG.warning("skipping corrupt version %s: %s", vj, e)
    return out


def _find_profile(ws: Workspace, asset_id: str) -> tuple[Path, dict] | None:
    """`(asset_dir, profile)` for a stable id (profile.json loaded + validated), else None."""
    for adir, p in _iter_profiles(ws):
        if p["id"] == asset_id:
            return adir, p
    return None


def _load_version_strict(asset_dir: Path, version_id: str) -> dict | None:
    """Load + **validate** the on-disk version record whose `id == version_id`. Returns
    None if no such file exists; **raises** `WorkspaceError` if the matched record is
    unreadable/invalid (review: a corrupt version must fail loudly here, not be hidden)."""
    vroot = asset_dir / "versions"
    if not vroot.is_dir():
        return None
    for vdir in sorted(vroot.iterdir()):
        vj = vdir / "version.json"
        if not vj.is_file():
            continue
        try:
            v = ws_mod.read_json(vj)
        except ws_mod.WorkspaceError:
            continue   # a different (unreadable) version's file — keep scanning for the target
        if v.get("id") != version_id:
            continue
        ws_mod.validate(v, "version.schema.json")   # propagate loudly if the target is invalid
        return v
    return None


def get_asset(ws: Workspace, asset_id: str) -> dict | None:
    """Full profile + its versions, by stable id."""
    found = _find_profile(ws, asset_id)
    if found is None:
        return None
    adir, p = found
    return {"profile": p, "versions": _load_versions(adir, p)}


def resolve_version(ws: Workspace, asset_id: str, version_id: str | None = None) -> str:
    """Validate the asset exists, the target version is registered, **and the target
    version record actually loads + validates on disk**, then return its id (the profile's
    active version when `version_id` is omitted). Raises `WorkspaceError` on an unknown
    asset/version or a corrupt-but-registered version — so we never enqueue Stage A/B/C
    work against a ProfileVersion that can't be loaded or saved (review High)."""
    found = _find_profile(ws, asset_id)
    if found is None:
        raise ws_mod.WorkspaceError(f"unknown asset {asset_id!r}")
    adir, profile = found
    target = version_id or profile["active_version"]
    if target not in profile["versions"]:
        raise ws_mod.WorkspaceError(f"version {target!r} not in asset {asset_id!r}")
    record = _load_version_strict(adir, target)
    if record is None:
        raise ws_mod.WorkspaceError(
            f"version {target!r} of asset {asset_id!r} is missing or unreadable on disk")
    return target
