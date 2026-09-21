"""M2.17 step a (kb-loom-cache.md, adopted 2026-09-21) — the model cache seen from loom.

Three things that used to live in four places or nowhere:

* **The roster** — every Hugging Face repo loom can need, with the exact files a consumer
  opens and who needs it (`used_by`): the phase manifest, the `multi` casting presets, the
  catalog variants (incl. their `probe_files` and VAE repos) and the postproc tools.
* **The resolver** — `resolve(repo, file)` reads the revision `refs/main` names first, then ANY
  cached revision that holds the file, never the network. The 2026-09-21 refusal was a
  ref that pointed at a one-file snapshot while the complete one sat unreferenced: the hub
  library follows the ref only, so 107 GB read as "not in cache". Workers get the chosen
  revision handed to them (`worker_env`) so the orchestrator's verdict and the worker's are
  the same bytes.
* **The inventory** — `GET /cache`: the location, every repo with its revisions and a health
  verdict (`ok · ref_drift · partial · missing · empty · unused · stale_extra`) and a sentence
  that says what to press; plus `repair_ref`, which rewrites `refs/main` to the newest
  revision that is complete for loom.

The layout stays the standard hub layout, so `hf` and the monorepo's other tools keep working.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import components, config as config_mod, model_catalog
from . import workspace as ws_mod
from .config import CONFIG

DIFFUSERS_PROBE = "model_index.json"


class CacheError(RuntimeError):
    def __init__(self, status: int, msg: str) -> None:
        super().__init__(msg)
        self.status = status


@dataclass
class Need:
    repo_id: str
    kind: str                              # diffusers | files
    files: list[str] = field(default_factory=list)   # the files a consumer opens ([] = any)
    used_by: list[str] = field(default_factory=list)
    gated: bool = False


@dataclass
class Resolved:
    path: Path
    revision: str
    via_ref: bool


# --- the cache on disk ---------------------------------------------------------------------

def hub_dir(cache_home: str | os.PathLike | None = None) -> Path:
    """The hub cache root (`<HF_HOME>/hub`). The orchestrator sets HF_HOME at startup from
    `CONFIG.hf_home`; an explicit `cache_home` wins (tests, and the step-c location setting)."""
    home = Path(cache_home) if cache_home else Path(os.environ.get("HF_HOME") or CONFIG.hf_home)
    return home / "hub"


def repo_dir(repo_id: str, hub: Path | None = None) -> Path:
    return (hub or hub_dir()) / f"models--{repo_id.replace('/', '--')}"


def _repo_id_of(rdir: Path) -> str:
    return rdir.name[len("models--"):].replace("--", "/")


def _ref_main(rdir: Path) -> str | None:
    p = rdir / "refs" / "main"
    try:
        return p.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _snapshots(rdir: Path) -> list[Path]:
    """Snapshot dirs, newest first."""
    d = rdir / "snapshots"
    if not d.is_dir():
        return []
    return sorted((s for s in d.iterdir() if s.is_dir()), key=lambda s: s.stat().st_mtime, reverse=True)


def _has(snapshot: Path, filename: str) -> bool:
    return (snapshot / filename).is_file()          # follows the symlink; a dangling one is False


def resolve(repo_id: str, filename: str, hub: Path | None = None) -> Resolved | None:
    """The cached file, from the ref'd revision first, else from any revision that holds it."""
    rdir = repo_dir(repo_id, hub)
    if not rdir.is_dir():
        return None
    ref = _ref_main(rdir)
    if ref and _has(rdir / "snapshots" / ref, filename):
        return Resolved(rdir / "snapshots" / ref / filename, ref, True)
    for s in _snapshots(rdir):
        if s.name != ref and _has(s, filename):
            return Resolved(s / filename, s.name, False)
    return None


# --- the roster ----------------------------------------------------------------------------

def roster() -> list[Need]:
    """Every repo loom can need, merged by repo (files and users are unions)."""
    needs: dict[str, Need] = {}

    def add(repo: str, kind: str, files: list[str], user: str, gated: bool = False) -> None:
        if not repo:
            return
        n = needs.get(repo)
        if n is None:
            n = needs[repo] = Need(repo_id=repo, kind=kind)
        for f in files:
            if f and f not in n.files:
                n.files.append(f)
        if user not in n.used_by:
            n.used_by.append(user)
        n.gated = n.gated or bool(gated)

    try:
        manifest = components._load_models_manifest()
    except components.ManifestError:
        manifest = {}
    for e in manifest.get("models") or []:
        if e.get("type") == "hf_diffusers":                 # `file` weights live outside the hub cache
            add(e.get("repo_id", ""), "diffusers", [DIFFUSERS_PROBE], f"phase:{e.get('phase', '?')}", e.get("gated", False))
    for preset, entries in (manifest.get("multi_presets") or {}).items():
        for e in entries or []:
            if not isinstance(e, dict) or e.get("insightface_pack"):
                continue
            add(components._entry_resolve_repo(e), "files", [e.get("probe") or "config.json"], f"multi:{preset}", e.get("gated", False))
    for tool, entries in (manifest.get("postproc") or {}).items():
        for e in entries or []:
            if not isinstance(e, dict) or e.get("insightface_pack"):
                continue
            add(components._entry_resolve_repo(e), "files", [e.get("probe") or "config.json"], f"postproc:{tool}", e.get("gated", False))
    for p in model_catalog.pipelines():
        for v in model_catalog.variants(p):
            files = list(v.get("probe_files") or [DIFFUSERS_PROBE])
            add(v.get("repo_id", ""), "files" if v.get("probe_files") else "diffusers", files, f"catalog:{p}/{v['id']}", v.get("gated", False))
            ae = v.get("ae_repo_id")
            if ae and ae != v.get("repo_id"):
                add(ae, "files", [], f"catalog:{p}/{v['id']} (vae)", False)
    return sorted(needs.values(), key=lambda n: n.repo_id.lower())


def roster_map() -> dict[str, Need]:
    return {n.repo_id: n for n in roster()}


# --- inventory + health --------------------------------------------------------------------

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _dir_size(d: Path) -> int:
    total = 0
    for p in d.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def _revisions(rdir: Path, need: Need | None) -> list[dict]:
    ref = _ref_main(rdir)
    out = []
    for s in _snapshots(rdir):
        files = [str(p.relative_to(s)).replace("\\", "/") for p in s.rglob("*") if p.is_file()]
        needed = need.files if need else []
        present = [f for f in needed if _has(s, f)]
        complete = (len(present) == len(needed)) if needed else bool(files)
        out.append({
            "commit": s.name, "ref": "main" if s.name == ref else None,
            "files": len(files), "size_gb": round(_dir_size(s) / 1e9, 2),
            "needed_present": len(present), "needed_total": len(needed),
            "complete_for_loom": complete, "last_modified": _iso(s.stat().st_mtime),
        })
    return out


def _health(need: Need | None, ref: str | None, revs: list[dict]) -> tuple[str, str]:
    if not revs or all(r["files"] == 0 for r in revs):
        if need:
            return "missing", "no revision holds any file; fetch it"
        return "empty", "a repo folder with no files; safe to delete"
    if need is None:
        return "unused", "not used by loom; it may belong to another tool in this cache"
    ref_rev = next((r for r in revs if r["ref"] == "main"), None)
    complete = [r for r in revs if r["complete_for_loom"]]
    if ref_rev and ref_rev["complete_for_loom"]:
        if len(revs) > 1:
            return "stale_extra", f"complete; {len(revs) - 1} extra revision(s) nothing references"
        return "ok", "complete"
    if complete:
        best = complete[0]
        if ref_rev:
            return "ref_drift", (f"main points at {ref_rev['needed_present']} of {ref_rev['needed_total']} needed files; "
                                 f"a complete revision {best['commit'][:7]} exists (repair)")
        return "ref_drift", f"no main ref; a complete revision {best['commit'][:7]} exists (repair)"
    if any(r["needed_present"] for r in revs):
        return "partial", "no revision holds every needed file; fetch the missing ones"
    return "missing", "no revision holds the needed files; fetch it"


def _location(hub: Path) -> dict:
    home = hub.parent
    if os.environ.get("LOOM_MODELS_DIR"):
        source = "env"
    elif getattr(config_mod, "_FILE_ENV", {}).get("LOOM_MODELS_DIR"):
        source = "dotenv"
    else:
        source = "default"
    free_gb = total_gb = None
    try:
        probe = home if home.exists() else home.anchor or home
        du = shutil.disk_usage(probe)
        free_gb, total_gb = round(du.free / 1e9, 1), round(du.total / 1e9, 1)
    except OSError:
        pass
    return {"path": str(home), "exists": home.is_dir(), "free_gb": free_gb, "total_gb": total_gb, "source": source}


def inventory(hub: Path | None = None) -> dict:
    hub = hub or hub_dir()
    needs = roster_map()
    repos: list[dict] = []
    seen: set[str] = set()
    if hub.is_dir():
        for rdir in sorted(hub.glob("models--*")):
            if not rdir.is_dir():
                continue
            repo_id = _repo_id_of(rdir)
            need = needs.get(repo_id)
            ref = _ref_main(rdir)
            revs = _revisions(rdir, need)
            health, detail = _health(need, ref, revs)
            seen.add(repo_id)
            repos.append({
                "repo_id": repo_id, "size_gb": round(_dir_size(rdir / "blobs") / 1e9, 2) if (rdir / "blobs").is_dir() else round(_dir_size(rdir) / 1e9, 2),
                "used_by": list(need.used_by) if need else [], "needed": need is not None,
                "gated": bool(need and need.gated), "ref": ref,
                "revisions": revs, "health": health, "detail": detail,
            })
    for n in needs.values():
        if n.repo_id not in seen:
            repos.append({"repo_id": n.repo_id, "size_gb": 0.0, "used_by": list(n.used_by), "needed": True,
                          "gated": n.gated, "ref": None, "revisions": [], "health": "missing",
                          "detail": "not in the cache; fetch it"})
    repos.sort(key=lambda r: (-r["size_gb"], r["repo_id"].lower()))
    return {
        "location": _location(hub),
        "scanned_at": _iso(datetime.now(tz=timezone.utc).timestamp()),
        "size_gb": round(sum(r["size_gb"] for r in repos), 1),
        "repos": repos,
        "needs_missing": [asdict(needs[r["repo_id"]]) for r in repos if r["needed"] and r["health"] in ("missing", "partial")],
    }


# --- repair + the worker's pins ------------------------------------------------------------

def pin_for(repo_id: str, files: list[str], hub: Path | None = None) -> str | None:
    """The revision a worker should read: the ref'd one when it holds every needed file, else
    the newest revision that does. None when nothing complete is cached."""
    rdir = repo_dir(repo_id, hub)
    if not rdir.is_dir():
        return None
    ref = _ref_main(rdir)
    ok = lambda s: all(_has(s, f) for f in files) if files else any(p.is_file() for p in s.rglob("*"))  # noqa: E731
    if ref and (rdir / "snapshots" / ref).is_dir() and ok(rdir / "snapshots" / ref):
        return ref
    for s in _snapshots(rdir):
        if s.name != ref and ok(s):
            return s.name
    return None


def repair_ref(repo_id: str, hub: Path | None = None) -> dict:
    """Rewrite `refs/main` to the newest revision that is complete for loom. Idempotent."""
    hub = hub or hub_dir()
    rdir = repo_dir(repo_id, hub)
    if not rdir.is_dir():
        raise CacheError(404, f"{repo_id!r} is not in the cache")
    need = roster_map().get(repo_id)
    files = need.files if need else []
    target = pin_for(repo_id, files, hub)
    if target is None:
        raise CacheError(409, f"no cached revision of {repo_id!r} holds every file loom needs; fetch it instead")
    previous = _ref_main(rdir)
    changed = previous != target
    if changed:
        ws_mod.atomic_write_text(rdir / "refs" / "main", target)
    return {"repo_id": repo_id, "previous": previous, "now": target, "changed": changed,
            "files": files, "used_by": list(need.used_by) if need else []}


def pins(hub: Path | None = None) -> dict[str, str]:
    """repo → revision for every roster repo that has a complete cached revision."""
    out: dict[str, str] = {}
    for n in roster():
        rev = pin_for(n.repo_id, n.files, hub)
        if rev:
            out[n.repo_id] = rev
    return out


def worker_env(hub: Path | None = None) -> dict[str, str]:
    """What a worker's environment gains: the pinned revisions, and the hub forced offline
    (R163: fetches are explicit loom actions; a stale ref must never start a download mid-job).
    `LOOM_WORKERS_ONLINE=1` restores the old online behaviour."""
    env = {"LOOM_HF_REVISIONS": json.dumps(pins(hub), separators=(",", ":"))}
    if not CONFIG.workers_online:
        env["HF_HUB_OFFLINE"] = "1"
    return env
