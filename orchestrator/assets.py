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

import shutil
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import workspace as ws_mod
    from . import logsetup
    from . import coverage
    from .workspace import Workspace, new_id, slugify
except ImportError:  # pragma: no cover - direct-run convenience
    import workspace as ws_mod  # type: ignore
    import logsetup  # type: ignore
    import coverage  # type: ignore
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


# --- M5: profile versioning (R49–R51, R58–R61, §3.4) -----------------------------

def _require_unlocked(version: dict) -> None:
    """Finalize = pure-intent lock (R60): a finalized version is immutable — every
    mutator calls this first; change means a NEW version (or profile — author's call,
    no hints, R61)."""
    if version.get("finalized"):
        raise ws_mod.WorkspaceError(
            f"version {version.get('name') or version.get('id')!r} is FINALIZED (locked, "
            "R60) — create a new version to change anything")


def create_version(ws: Workspace, asset_id: str, *, parent_version_id: str | None = None,
                   name: str | None = None) -> dict:
    """Copy-on-create (R50/R58/R59): a **full deep-duplicate** of ANY prior version
    (default: the active one) — own copies of casting/, refs/, faces/ (anchor incl. its
    durable verification stamp: the file is byte-identical, the proof carries), the
    records as-is, `derived_from` = the parent, fresh id, **unlocked** (R51: copy freezes
    a baseline → edit only what differs → finalize). The new version becomes active."""
    found = _find_profile(ws, asset_id)
    if found is None:
        raise ws_mod.WorkspaceError(f"unknown asset {asset_id!r}")
    adir, profile = found
    parent_id = parent_version_id or profile["active_version"]
    if parent_id not in profile["versions"]:
        raise ws_mod.WorkspaceError(f"version {parent_id!r} not in asset {asset_id!r}")
    pfound = _find_version(adir, parent_id)
    if pfound is None:
        raise ws_mod.WorkspaceError(f"parent version {parent_id!r} is unloadable")
    pdir, parent = pfound

    n = len(profile["versions"]) + 1
    vname = (name or "").strip() or f"v{n}"
    looks_versioned = vname.startswith("v") and vname[1:2].isdigit()
    dirname = slugify(vname) if looks_versioned else f"v{n}_{slugify(vname)}"
    vdir = adir / "versions" / dirname
    if vdir.exists():
        raise ws_mod.WorkspaceError(f"version dir {dirname!r} already exists — pick "
                                    "another name")

    shutil.copytree(pdir, vdir)
    ver_id = new_id("ver")
    version = dict(parent)
    version.update({"id": ver_id, "name": vname, "derived_from": parent_id,
                    "finalized": False, "saved_at": _now()})
    ws_mod.validate(version, "version.schema.json")
    ws_mod.atomic_write_json(vdir / "version.json", version)

    profile["versions"] = [*profile["versions"], ver_id]
    profile["active_version"] = ver_id
    ws_mod.validate(profile, "profile.schema.json")
    ws_mod.atomic_write_json(adir / "profile.json", profile)
    return {"profile": profile, "version": version}


def finalize_version(ws: Workspace, asset_id: str,
                     version_id: str | None = None) -> dict:
    """Finalize = pure-intent lock (R60): declare the version done → immutable.
    Idempotent (re-finalizing a locked version is a no-op, not an error)."""
    vdir, version = _resolve_version_dir(ws, asset_id, version_id)
    if version.get("finalized"):
        return version
    version["finalized"] = True
    version["saved_at"] = _now()
    return _write_version(vdir, version)


def set_active_version(ws: Workspace, asset_id: str, version_id: str) -> dict:
    """Switch the profile's active version (the version selector; everything downstream
    — grids, casting, curation, Stage-B — scopes to it). Returns the updated profile."""
    found = _find_profile(ws, asset_id)
    if found is None:
        raise ws_mod.WorkspaceError(f"unknown asset {asset_id!r}")
    adir, profile = found
    if version_id not in profile["versions"]:
        raise ws_mod.WorkspaceError(f"version {version_id!r} not in asset {asset_id!r}")
    if _find_version(adir, version_id) is None:
        raise ws_mod.WorkspaceError(f"version {version_id!r} is unloadable")
    profile["active_version"] = version_id
    ws_mod.validate(profile, "profile.schema.json")
    ws_mod.atomic_write_json(adir / "profile.json", profile)
    return profile


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


def _find_version(asset_dir: Path, version_id: str) -> tuple[Path, dict] | None:
    """`(version_dir, record)` for the on-disk version whose `id == version_id`, with the
    record **validated**. Returns None if no such file exists; **raises** `WorkspaceError`
    if the matched record is unreadable/invalid (a corrupt version must fail loudly, not be
    hidden — review High)."""
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
        return vdir, v
    return None


def _load_version_strict(asset_dir: Path, version_id: str) -> dict | None:
    found = _find_version(asset_dir, version_id)
    return found[1] if found is not None else None


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


# --- Stage-A casting: persist candidates + hero-star into version.json (M2) -----

def _resolve_version_dir(ws: Workspace, asset_id: str, version_id: str | None):
    """`(version_dir, record)` for the target version (active by default). Raises on an
    unknown asset / version (shared by the casting ops below)."""
    found = _find_profile(ws, asset_id)
    if found is None:
        raise ws_mod.WorkspaceError(f"unknown asset {asset_id!r}")
    adir, profile = found
    vid = version_id or profile["active_version"]
    if vid not in profile["versions"]:
        raise ws_mod.WorkspaceError(f"version {vid!r} not in asset {asset_id!r}")
    fv = _find_version(adir, vid)
    if fv is None:
        raise ws_mod.WorkspaceError(
            f"version {vid!r} of asset {asset_id!r} is missing or unreadable on disk")
    return fv


def _write_version(vdir: Path, version: dict) -> dict:
    ws_mod.validate(version, "version.schema.json")
    ws_mod.atomic_write_json(vdir / "version.json", version)
    return version


def star_candidate(ws: Workspace, asset_id: str, *, job_id: str, source_output: str,
                   version_id: str | None = None, pipeline: str | None = None,
                   seed: int | None = None, starred: bool = True) -> dict:
    """Promote a completed Stage-A job output into the version's `casting[]` and (when
    `starred`) make it the **sole hero ★** (R44). Idempotent on `job_id` — a candidate is
    recorded once; re-calling just toggles the hero.

    The candidate image (+ sidecar manifest if present) is **copied into the version's
    `casting/` dir** so a Saved version is self-contained and survives deleting the source
    job / pruning `out/` (the casting set is the saved provenance, not a live pointer).
    `source_output` is the candidate's output path relative to `out/` — for `zimage` that's
    `<job>/<file>`; for a `multi` cast (one job → N candidates) it's the specific candidate
    file, so the candidate (not the job) is the identity (dedup key)."""
    vdir, version = _resolve_version_dir(ws, asset_id, version_id)
    _require_unlocked(version)                      # finalized = immutable (R60, M5)
    casting = version.setdefault("casting", [])
    # Identity is the specific output, not the job (a multi job yields N candidates).
    entry = next((c for c in casting if c.get("source_output") == source_output), None)

    if entry is None:
        if ".." in source_output or "\\" in source_output:
            raise ws_mod.WorkspaceError(f"invalid output {source_output!r}")
        src = (ws.out_dir / source_output).resolve()
        if not src.is_relative_to(ws.out_dir.resolve()) or not src.is_file():
            raise ws_mod.WorkspaceError(f"output {source_output!r} not found in out/")
        cand_id = new_id("cand")
        cdir = vdir / "casting"
        cdir.mkdir(parents=True, exist_ok=True)
        dst = cdir / f"{cand_id}{src.suffix}"
        shutil.copy2(src, dst)
        man = src.with_suffix(".json")
        if man.is_file():
            shutil.copy2(man, cdir / f"{cand_id}.json")
        entry = {"id": cand_id, "job_id": job_id, "file": dst.name,
                 "source_output": source_output, "pipeline": pipeline, "seed": seed,
                 "starred": False, "added_at": _now()}
        casting.append(entry)

    if starred:
        for c in casting:
            c["starred"] = (c["id"] == entry["id"])   # exactly one hero
    else:
        entry["starred"] = False                       # un-star this one (toggle off)
    return _write_version(vdir, version)


def set_hero(ws: Workspace, asset_id: str, *, candidate_id: str | None,
             version_id: str | None = None) -> dict:
    """Set (or clear, with `candidate_id=None`) the hero ★ among already-recorded casting
    candidates. Raises if `candidate_id` isn't in the version's casting set."""
    vdir, version = _resolve_version_dir(ws, asset_id, version_id)
    _require_unlocked(version)                      # finalized = immutable (R60, M5)
    casting = version.get("casting", [])
    if candidate_id is not None and not any(c["id"] == candidate_id for c in casting):
        raise ws_mod.WorkspaceError(f"candidate {candidate_id!r} not in casting set")
    for c in casting:
        c["starred"] = (c["id"] == candidate_id)
    return _write_version(vdir, version)


def resolve_hero(ws: Workspace, asset_id: str, version_id: str | None = None):
    """`(version, hero_entry, hero_abs_path)` for the version's starred hero ★ (the Stage-A
    pick that seeds Stage-B img2img). Raises `WorkspaceError` if the asset/version is unknown
    or **no hero is starred yet** (Stage B can't start without one) / its image is missing."""
    _vdir, version = _resolve_version_dir(ws, asset_id, version_id)
    hero = next((c for c in version.get("casting", []) if c.get("starred")), None)
    if hero is None:
        raise ws_mod.WorkspaceError(
            f"asset {asset_id!r} has no starred hero — cast + star one in Stage A first")
    path = (_vdir / "casting" / hero["file"]).resolve()
    if not path.is_file():
        raise ws_mod.WorkspaceError(f"hero image {hero['file']!r} missing on disk")
    return version, hero, path


def casting_file_path(ws: Workspace, asset_id: str, file: str,
                      version_id: str | None = None) -> Path:
    """Resolve a casting image path for serving (traversal-guarded). Raises if absent."""
    if ".." in file or "/" in file or "\\" in file:
        raise ws_mod.WorkspaceError(f"invalid casting file {file!r}")
    vdir, _version = _resolve_version_dir(ws, asset_id, version_id)
    base = (vdir / "casting").resolve()
    path = (base / file).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise ws_mod.WorkspaceError(f"no such casting file {file!r}")
    return path


# --- M4: face anchor (R94) — the chosen face image the identity pass locks to -----

def set_anchor(ws: Workspace, asset_id: str, *, job_id: str, source_output: str,
               version_id: str | None = None) -> dict:
    """Pick `source_output` (an out/-relative image from an owned job — ownership is the
    caller's scope guard, like refs/keep) as the version's **face anchor** (R94): copied
    into the version's `faces/anchor.png` so a Saved version is self-contained. Re-picking
    overwrites (per-version, re-pickable — scar/tattoo)."""
    vdir, version = _resolve_version_dir(ws, asset_id, version_id)
    _require_unlocked(version)                      # finalized = immutable (R60, M5)
    if ".." in source_output or "\\" in source_output:
        raise ws_mod.WorkspaceError(f"invalid output {source_output!r}")
    src = (ws.out_dir / source_output).resolve()
    if not src.is_relative_to(ws.out_dir.resolve()) or not src.is_file():
        raise ws_mod.WorkspaceError(f"output {source_output!r} not found in out/")
    fdir = vdir / "faces"
    fdir.mkdir(parents=True, exist_ok=True)
    dst = fdir / f"anchor{src.suffix}"
    shutil.copy2(src, dst)
    version["anchor"] = {"file": dst.name, "source_output": source_output,
                         "job_id": job_id, "set_at": _now()}
    return _write_version(vdir, version)


def clear_anchor(ws: Workspace, asset_id: str, version_id: str | None = None) -> dict:
    """Opt the version out of the face anchor (R93). The copied file is removed too."""
    vdir, version = _resolve_version_dir(ws, asset_id, version_id)
    _require_unlocked(version)                      # finalized = immutable (R60, M5)
    anchor = version.get("anchor")
    if anchor and anchor.get("file"):
        try:
            (vdir / "faces" / anchor["file"]).unlink(missing_ok=True)
        except OSError:
            pass                                     # record-of-truth is version.json
    version["anchor"] = None
    return _write_version(vdir, version)


def mark_anchor_verified(ws: Workspace, version_id: str, *, anchor_path: str,
                         job_id: str) -> bool:
    """Persist the anchor-verification fact on `version.anchor` (M4 review: the computed
    check read RUNNER job history, so deleting/pruning the verifying job silently
    un-verified a perfectly good anchor — Saved profiles must keep their identity-lock
    readiness, M5+). Looks the version up by id (the runner-side caller only knows the
    job's `profile_version_id`), confirms the verified file is STILL the current anchor
    (a re-pick mid-run must not inherit the old run's credit), stamps
    `verified_at`/`verified_by_job`. Idempotent; returns True when the fact is durable."""
    for adir, profile in _iter_profiles(ws):
        if version_id not in profile.get("versions", []):
            continue
        found = _find_version(adir, version_id)
        if found is None:
            return False
        vdir, version = found
        if version.get("finalized"):
            return False                  # locked (R60) — even metadata stays immutable
        anchor = version.get("anchor")
        if not anchor or not anchor.get("file"):
            return False
        current = (vdir / "faces" / anchor["file"]).resolve()
        try:
            if str(current) != str(Path(anchor_path).resolve()):
                return False
        except OSError:
            return False
        if anchor.get("verified_at"):
            return True                              # already durable
        anchor["verified_at"] = _now()
        anchor["verified_by_job"] = job_id
        _write_version(vdir, version)
        return True
    return False


def anchor_file_path(ws: Workspace, asset_id: str,
                     version_id: str | None = None) -> Path | None:
    """Absolute path of the version's anchor image, or None when unset/missing."""
    vdir, version = _resolve_version_dir(ws, asset_id, version_id)
    anchor = version.get("anchor")
    if not anchor or not anchor.get("file"):
        return None
    path = (vdir / "faces" / anchor["file"]).resolve()
    return path if path.is_file() else None


# --- Stage-C curation: keep/cull Stage-B outputs → curated ref_set (M3) ----------

def keep_ref(ws: Workspace, asset_id: str, *, job_id: str, source_output: str,
             coverage_cell: dict, version_id: str | None = None,
             pipeline: str | None = None, seed: int | None = None,
             method: str | None = None) -> dict:
    """Keep a Stage-B candidate into the version's curated `ref_set` (the future LoRA corpus,
    R107) — the MVP done-line's payload. Idempotent on `source_output`. Each kept image (+ its
    sidecar manifest) is **copied into the version's `refs/` dir** so a Saved version is
    self-contained (survives job deletion / out/ pruning), and the entry carries its frozen
    `coverage_cell` (the P1→P2 contract) so P2 can template-caption it. Raises on an invalid
    cell / unsafe output / missing source."""
    coverage.validate_cell(coverage_cell)                  # frozen-contract guard
    vdir, version = _resolve_version_dir(ws, asset_id, version_id)
    _require_unlocked(version)                      # finalized = immutable (R60, M5)
    ref_set = version.setdefault("ref_set", [])
    entry = next((r for r in ref_set if r.get("source_output") == source_output), None)
    if entry is None:
        if ".." in source_output or "\\" in source_output:
            raise ws_mod.WorkspaceError(f"invalid output {source_output!r}")
        src = (ws.out_dir / source_output).resolve()
        if not src.is_relative_to(ws.out_dir.resolve()) or not src.is_file():
            raise ws_mod.WorkspaceError(f"output {source_output!r} not found in out/")
        ref_id = new_id("ref")
        rdir = vdir / "refs"
        rdir.mkdir(parents=True, exist_ok=True)
        dst = rdir / f"{ref_id}{src.suffix}"
        shutil.copy2(src, dst)
        man = src.with_suffix(".json")
        if man.is_file():
            shutil.copy2(man, rdir / f"{ref_id}.json")
        entry = {"id": ref_id, "file": dst.name, "coverage_cell": coverage_cell,
                 "source_output": source_output, "job_id": job_id, "pipeline": pipeline,
                 "method": method, "seed": seed, "added_at": _now()}
        ref_set.append(entry)
    # Keeping wins over a stale reject mark (P1-12): un-reject on keep.
    rej = version.get("rejected") or []
    if source_output in rej:
        version["rejected"] = [r for r in rej if r != source_output]
    return _write_version(vdir, version)


def reject_output(ws: Workspace, asset_id: str, *, source_output: str,
                  version_id: str | None = None, rejected: bool = True) -> dict:
    """Mark (or unmark, `rejected=False`) a Stage-B candidate output as **rejected**
    (P1-12 curation throughput): a persistent, lightweight cull-from-view list — no image
    copy, just the out/-relative name in `version.rejected[]`, so the ~100→~30 reject
    sweep survives reloads. A KEPT output can't be rejected (cull it first); keeping a
    rejected output un-rejects it (keep wins). Idempotent both ways."""
    if ".." in source_output or "\\" in source_output:
        raise ws_mod.WorkspaceError(f"invalid output {source_output!r}")
    vdir, version = _resolve_version_dir(ws, asset_id, version_id)
    _require_unlocked(version)                      # finalized = immutable (R60, M5)
    if rejected and any(r.get("source_output") == source_output
                        for r in version.get("ref_set", [])):
        raise ws_mod.WorkspaceError(
            f"output {source_output!r} is KEPT in the ref_set — cull it before rejecting")
    rej = [r for r in (version.get("rejected") or []) if r != source_output]
    if rejected:
        rej.append(source_output)
    version["rejected"] = rej
    return _write_version(vdir, version)


def remove_ref(ws: Workspace, asset_id: str, *, ref_id: str,
               version_id: str | None = None) -> dict:
    """Cull a kept ref (un-keep): drop it from `ref_set` + delete its copied `refs/` image +
    sidecar. Raises if `ref_id` isn't in the set."""
    vdir, version = _resolve_version_dir(ws, asset_id, version_id)
    _require_unlocked(version)                      # finalized = immutable (R60, M5)
    ref_set = version.get("ref_set", [])
    entry = next((r for r in ref_set if r.get("id") == ref_id), None)
    if entry is None:
        raise ws_mod.WorkspaceError(f"ref {ref_id!r} not in ref_set")
    rdir = vdir / "refs"
    stem = Path(entry.get("file", "")).stem
    if stem:
        for f in (rdir / entry["file"], rdir / f"{stem}.json"):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
    version["ref_set"] = [r for r in ref_set if r.get("id") != ref_id]
    return _write_version(vdir, version)


def save_profile(ws: Workspace, asset_id: str, *, prompt_template: str | None = None,
                 version_id: str | None = None) -> dict:
    """**Save AssetProfile** (R119): persist the version's editable identity clause
    (`prompt_template`) + re-stamp `saved_at`. The version is **Saved, not Finalized** — still
    editable (finalize/lock is M5). Refuses if already finalized (locked → make a new version)."""
    vdir, version = _resolve_version_dir(ws, asset_id, version_id)
    if version.get("finalized"):
        raise ws_mod.WorkspaceError(
            "version is finalized (locked) — create a new version to edit (M5)")
    if prompt_template is not None:
        version["prompt_template"] = prompt_template
    version["saved_at"] = _now()
    return _write_version(vdir, version)


def ref_file_path(ws: Workspace, asset_id: str, file: str,
                  version_id: str | None = None) -> Path:
    """Resolve a curated ref image path for serving (traversal-guarded). Raises if absent."""
    if ".." in file or "/" in file or "\\" in file:
        raise ws_mod.WorkspaceError(f"invalid ref file {file!r}")
    vdir, _version = _resolve_version_dir(ws, asset_id, version_id)
    base = (vdir / "refs").resolve()
    path = (base / file).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise ws_mod.WorkspaceError(f"no such ref file {file!r}")
    return path
