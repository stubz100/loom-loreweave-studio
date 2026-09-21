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
* **The models view** (step e) — every use of a repo carries a label, a role (model · vae ·
  text encoder · ControlNet · LoRA · tool weights · base model) and where in loom it is used
  (Cast · Expand · Post: … · Train · …), and the inventory lists the roster **by model** too:
  one entry per catalog variant, casting preset, postproc tool and trainer preset with its
  repos and the worst of their health. A `whole` need (a diffusers or transformers-style
  repo opened with `from_pretrained`) is complete only when a revision holds weight files,
  not just the probe, and a fetch of it is a snapshot.
* **The token** (step e) — `hf_token()` / `token_source()`: a real env var, then `.env.local`,
  then the loom setting `settings.hf_token` (the location's precedence, D1); workers get it
  in their environment, the inventory reports only a masked form.

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
# Catalog pipelines whose repos are NOT diffusers layouts (no model_index.json): probe config.json.
NON_DIFFUSERS_PIPELINES = {"birefnet", "identity", "face_restore", "frame_harvest"}
# A `whole` need is complete only when a revision holds at least one of these (step e).
WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".onnx", ".gguf", ".msgpack", ".h5")
# A snapshot fetch of a diffusers repo skips the formats diffusers never loads when safetensors exist.
DIFFUSERS_IGNORE = ["*.msgpack", "*.h5", "*.ckpt", "*.onnx", "*.tflite", "*.ot"]
TOKEN_KEYS = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_HUB_TOKEN")

# --- where a repo is used in loom (step e) ---------------------------------------------------
PIPELINE_LABELS = {"flux2": "FLUX.2", "sd35": "SD3.5", "zimage": "Z-Image", "krea2": "Krea 2", "ltxv": "LTX-Video",
                   "birefnet": "BiRefNet", "identity": "Identity", "face_restore": "Face restore", "frame_harvest": "Frames"}
# The v2 stages a catalog pipeline serves (`test_cache_manager_d` keeps this in step with the composers).
STAGES = {"flux2": ["Cast", "Expand"], "sd35": ["Cast", "Expand"], "zimage": ["Cast", "Expand", "LoRA preview"],
          "krea2": ["Cast"], "ltxv": ["Video (not in v2 yet)"], "birefnet": ["Expand: matte"],
          "identity": ["Expand: identity lock"], "face_restore": [], "frame_harvest": ["Frames"]}
# The manifest's postproc blocks: label, role, where.
TOOLS = {
    "birefnet": ("Matte / cutout (BiRefNet)", "tool weights", ["Expand: matte"]),
    "identity": ("Identity lock (inswapper)", "tool weights", ["Expand: identity lock"]),
    "face_restore": ("Face restore (GFPGAN)", "tool weights", ["Post: Restore"]),
    "sd35_tile_cn": ("Scale (SD3.5 Tile ControlNet)", "ControlNet", ["Post: Scale"]),
    "flux2_turbo_lora": ("FLUX.2 dev Turbo LoRA", "LoRA", ["Cast, Expand: dev turbo sampling"]),
}
# The postproc presets by id, as the v2 Post tab names them.
PRESET_LABELS = {"clean": "Clean", "refine": "Refine", "stylelock": "StyleLock", "upscale": "Scale",
                 "restore": "Restore", "inpaint": "Inpaint", "resize": "Resize"}
ROLE_ORDER = {"model": 0, "base model": 0, "text encoder": 1, "vae": 2, "ControlNet": 3, "LoRA": 4, "tool weights": 5}
SEVERITY = ["missing", "partial", "ref_drift", "empty", "stale_extra", "ok", "unused"]


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
    whole: bool = False                    # opened with from_pretrained: a fetch is a snapshot, complete = weights present
    uses: list[dict] = field(default_factory=list)   # [{tag, label, role}] — one per used_by entry


@dataclass
class Resolved:
    path: Path
    revision: str
    via_ref: bool


# --- the cache on disk ---------------------------------------------------------------------

def location_source() -> str:
    """Where the cache location comes from, in precedence order: a real `LOOM_MODELS_DIR` env
    var (`env`), the same key in `.env` / `.env.local` (`dotenv`), the app setting
    `settings.models_dir` (`setting`), an inherited `HF_HOME` (`hf_home`), else the default."""
    if os.environ.get("LOOM_MODELS_DIR"):
        return "env"
    if getattr(config_mod, "_FILE_ENV", {}).get("LOOM_MODELS_DIR"):
        return "dotenv"
    if app_settings().get("models_dir"):
        return "setting"
    if os.environ.get("HF_HOME"):
        return "hf_home"
    return "default"


def effective_home() -> Path:
    """The cache home in force right now (step c): env > dotenv > setting > HF_HOME > default.
    Read on every call, so a setting change applies to the next scan and the next job with
    no restart; the env sources still win, and the UI says so."""
    src = location_source()
    if src in ("env", "dotenv"):
        return Path(CONFIG.hf_home)
    if src == "setting":
        return Path(app_settings()["models_dir"]).resolve()
    if src == "hf_home":
        return Path(os.environ["HF_HOME"])
    return Path(CONFIG.hf_home)


def hub_dir(cache_home: str | os.PathLike | None = None) -> Path:
    """The hub cache root (`<home>/hub`); an explicit `cache_home` wins, else `effective_home()`."""
    home = Path(cache_home) if cache_home else effective_home()
    return home / "hub"


def repo_dir(repo_id: str, hub: Path | None = None) -> Path:
    """The repo's cache folder. The hub is case-insensitive about repo ids but the cache folder
    is not: a repo downloaded as `…klein-9b-kv` lives in a lower-case folder while the catalog
    says `…9B-kv`. Match the folder ignoring case; fall back to the exact-case path."""
    hub = hub or hub_dir()
    exact = hub / f"models--{repo_id.replace('/', '--')}"
    if not hub.is_dir():
        return exact
    want = exact.name.lower()
    # one listing, so the on-disk spelling comes back even where the filesystem itself ignores case
    for d in hub.iterdir():
        if d.is_dir() and d.name.lower() == want:
            return d
    return exact


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
    pinned = pinned_commit(repo_id)
    if pinned and _has(rdir / "snapshots" / pinned, filename):
        return Resolved(rdir / "snapshots" / pinned / filename, pinned, pinned == _ref_main(rdir))
    ref = _ref_main(rdir)
    if ref and _has(rdir / "snapshots" / ref, filename):
        return Resolved(rdir / "snapshots" / ref / filename, ref, True)
    for s in _snapshots(rdir):
        if s.name != ref and _has(s, filename):
            return Resolved(s / filename, s.name, False)
    return None


# --- the roster ----------------------------------------------------------------------------

def roster() -> list[Need]:
    """Every repo loom can need, merged by repo (files, users and the `whole` flag are unions).
    Every use names the model it belongs to (`label`) and what the repo is to it (`role`), so the
    Models page can say which cached folder serves which model in which stage (step e)."""
    needs: dict[str, Need] = {}

    def add(repo: str, kind: str, files: list[str], user: str, gated: bool = False, *,
            label: str = "", role: str = "model", whole: bool = False) -> None:
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
            n.uses.append({"tag": user, "label": label or user, "role": role})
        n.gated = n.gated or bool(gated)
        n.whole = n.whole or bool(whole)

    def whole_probe(e: dict) -> bool:
        # a probe that is the repo's config = the consumer opens the whole repo; a `filename` = one file
        return not e.get("filename") and (e.get("probe") or "config.json") in ("config.json", DIFFUSERS_PROBE)

    try:
        manifest = components._load_models_manifest()
    except components.ManifestError:
        manifest = {}
    for e in manifest.get("models") or []:
        if e.get("type") == "hf_diffusers":                 # `file` weights live outside the hub cache
            phase = e.get("phase", "?")
            add(e.get("repo_id", ""), "diffusers", [DIFFUSERS_PROBE], f"phase:{phase}", e.get("gated", False),
                label=f"{e.get('id', '?')} (phase {phase} manifest)", whole=True)
    for preset, entries in (manifest.get("multi_presets") or {}).items():
        for e in entries or []:
            if not isinstance(e, dict) or e.get("insightface_pack"):
                continue
            eid = str(e.get("id", ""))
            role = "vae" if eid.endswith("-ae") else "text encoder" if "text-encoder" in eid else "model"
            add(components._entry_resolve_repo(e), "files", [e.get("probe") or "config.json"], f"multi:{preset}",
                e.get("gated", False), label=f"Cast preset '{preset}'", role=role, whole=whole_probe(e))
    for tool, entries in (manifest.get("postproc") or {}).items():
        label, role, _ = TOOLS.get(tool, (f"postproc tool '{tool}'", "tool weights", []))
        for e in entries or []:
            if not isinstance(e, dict) or e.get("insightface_pack"):
                continue
            add(components._entry_resolve_repo(e), "files", [e.get("probe") or "config.json"], f"postproc:{tool}",
                e.get("gated", False), label=label, role=role, whole=whole_probe(e))
    for p in model_catalog.pipelines():
        for v in model_catalog.variants(p):
            repo = v.get("repo_id", "")
            user = f"catalog:{p}/{v['id']}"
            label = f"{PIPELINE_LABELS.get(p, p)} · {v['id']}"
            if v.get("probe_files"):
                add(repo, "files", list(v["probe_files"]), user, v.get("gated", False), label=label)
            elif repo in needs and needs[repo].files:
                # a manifest entry already names the file this repo is probed by (BiRefNet is a
                # transformers-style repo: it has config.json and never model_index.json)
                add(repo, needs[repo].kind, [], user, v.get("gated", False), label=label)
            elif p in NON_DIFFUSERS_PIPELINES:
                add(repo, "files", ["config.json"], user, v.get("gated", False), label=label, whole=True)
            else:
                add(repo, "diffusers", [DIFFUSERS_PROBE], user, v.get("gated", False), label=label, whole=True)
            ae = v.get("ae_repo_id")
            if ae and ae != v.get("repo_id"):
                add(ae, "files", [], user, False, label=label, role="vae")
            te = str(v.get("text_encoder") or "")
            if "/" in te and " " not in te:
                # the Klein text encoder: the non-FP8 Qwen3 repo on Windows ROCm, its FP8 twin elsewhere
                # (flux2 `stage1_load_models._load_text_encoder_safe` / `text_encoder.load_text_encoder`)
                te_repo = components._entry_resolve_repo({"repo_id": te, "fp8_repo_id": te + "-FP8"})
                add(te_repo, "files", ["config.json"], user, False, label=label, role="text encoder", whole=True)
    try:
        from . import training
        presets = training.TRAINER_PRESETS
    except Exception:  # noqa: BLE001 - the trainer module is optional to the roster
        presets = {}
    for fam, p in presets.items():
        settings = p.get("settings") or {}
        gate = p.get("gate_env")
        add(str(settings.get("base_model") or ""), "diffusers", [DIFFUSERS_PROBE], f"train:{fam}", False,
            label=f"Train (LoRA on {settings.get('model_name', fam)}{', behind ' + gate if gate else ''})",
            role="base model", whole=True)
    return sorted(needs.values(), key=lambda n: n.repo_id.lower())


def roster_map() -> dict[str, Need]:
    return {n.repo_id: n for n in roster()}


def where_used(tag: str, presets: dict[str, str] | None = None) -> list[str]:
    """The places in loom a use tag stands for: the v2 stages of a catalog pipeline (plus the
    postproc presets whose backend is that pipeline, on its default variant), a casting preset,
    a postproc tool, the trainer, the launch gate. `presets` = {preset id: backend}."""
    presets = presets or {}
    if tag.startswith("catalog:"):
        p, _, vid = tag[len("catalog:"):].partition("/")
        out = list(STAGES.get(p, []))
        if p == "flux2" and vid == "flux.2-dev":
            out.append("Poses")
        if model_catalog.default_model(p) == vid:
            post = [PRESET_LABELS.get(k, k) for k, b in presets.items() if b == p]
            if post:
                out.append("Post: " + ", ".join(post))
        return out
    if tag.startswith("multi:"):
        return [f"Cast (multi, {tag[len('multi:'):]} preset)"]
    if tag.startswith("postproc:"):
        return list(TOOLS.get(tag[len("postproc:"):], ("", "", []))[2])
    if tag.startswith("train:"):
        return ["Train"]
    if tag.startswith("phase:"):
        return [f"Launch gate ({tag[len('phase:'):]})"]
    return []


def _twin_note(repo_id: str, needs: dict[str, Need]) -> str | None:
    """An unused repo that is the FP8 / non-FP8 twin of a text encoder loom loads on another
    platform: the roster names the one this box loads, but the twin is not junk."""
    low = repo_id.lower()
    for n in needs.values():
        if not any(u["role"] == "text encoder" for u in n.uses):
            continue
        nid = n.repo_id.lower()
        twin = nid[:-4] if nid.endswith("-fp8") else nid + "-fp8"
        if low == twin:
            which = "non-FP8" if nid.endswith("-fp8") else "FP8"
            return f"the {which} twin of {n.repo_id}, the text encoder loom loads on another platform, not on this box"
    return None


def _use_kind(tag: str) -> str:
    return {"catalog": "model", "multi": "preset", "postproc": "tool", "train": "train", "phase": "manifest"}.get(tag.split(":", 1)[0], "other")


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
        weights_present = any(f.lower().endswith(WEIGHT_SUFFIXES) for f in files)
        complete = (len(present) == len(needed)) if needed else bool(files)
        if need is not None and need.whole:
            complete = complete and weights_present      # the probe alone is not the model (step e)
        out.append({
            "commit": s.name, "ref": "main" if s.name == ref else None,
            "files": len(files), "size_gb": round(_dir_size(s) / 1e9, 2),
            "needed_present": len(present), "needed_total": len(needed), "weights": weights_present,
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
        if need.whole and any(r["needed_present"] == r["needed_total"] and not r["weights"] for r in revs):
            return "partial", "the metadata is cached but no weight files are; fetch the whole repo"
        return "partial", "no revision holds every needed file; fetch the missing ones"
    return "missing", "no revision holds the needed files; fetch it"


def _location(hub: Path) -> dict:
    home = hub.parent
    source = location_source()
    free_gb = total_gb = None
    try:
        probe = home if home.exists() else home.anchor or home
        du = shutil.disk_usage(probe)
        free_gb, total_gb = round(du.free / 1e9, 1), round(du.total / 1e9, 1)
    except OSError:
        pass
    prev = app_settings().get("previous_models_dir")
    previous = None
    if prev and Path(prev).resolve() != home.resolve():
        ph = Path(prev) / "hub"
        previous = {"path": prev, "exists": ph.is_dir(), "size_gb": round(_dir_size(ph) / 1e9, 1) if ph.is_dir() else 0.0}
    return {"path": str(home), "exists": home.is_dir(), "free_gb": free_gb, "total_gb": total_gb, "source": source,
            "managed": source in ("setting", "hf_home", "default"), "previous": previous}


def inventory(hub: Path | None = None, presets: dict[str, str] | None = None) -> dict:
    """The cache seen from loom: the location, the token (masked), every repo with its revisions,
    health, uses (label · role · where) and, by model, every use with its repos and the worst of
    their health. `presets` = the postproc presets {id: backend} for the *where* column."""
    hub = hub or hub_dir()
    needs = roster_map()
    needs_ci = {k.lower(): v for k, v in needs.items()}
    repos: list[dict] = []
    seen: set[str] = set()

    def uses_of(need: Need | None) -> list[dict]:
        return [{**u, "where": where_used(u["tag"], presets)} for u in need.uses] if need else []

    if hub.is_dir():
        for rdir in sorted(hub.glob("models--*")):
            if not rdir.is_dir():
                continue
            repo_id = _repo_id_of(rdir)
            need = needs_ci.get(repo_id.lower())
            if need:
                repo_id = need.repo_id            # the roster's spelling; the folder may differ in case
            ref = _ref_main(rdir)
            revs = _revisions(rdir, need)
            health, detail = _health(need, ref, revs)
            if need is None and health == "unused":
                detail = _twin_note(repo_id, needs) or detail
            seen.add(repo_id)
            repos.append({
                "repo_id": repo_id, "size_gb": round(_dir_size(rdir / "blobs") / 1e9, 2) if (rdir / "blobs").is_dir() else round(_dir_size(rdir) / 1e9, 2),
                "used_by": list(need.used_by) if need else [], "uses": uses_of(need), "needed": need is not None,
                "gated": bool(need and need.gated), "whole": bool(need and need.whole), "ref": ref,
                "revisions": revs, "health": health, "detail": detail,
            })
    for n in needs.values():
        if n.repo_id not in seen:
            repos.append({"repo_id": n.repo_id, "size_gb": 0.0, "used_by": list(n.used_by), "uses": uses_of(n), "needed": True,
                          "gated": n.gated, "whole": n.whole, "ref": None, "revisions": [], "health": "missing",
                          "detail": "not in the cache; fetch it"})
    repos.sort(key=lambda r: (-r["size_gb"], r["repo_id"].lower()))
    return {
        "location": _location(hub),
        "token": token_info(),
        "scanned_at": _iso(datetime.now(tz=timezone.utc).timestamp()),
        "size_gb": round(sum(r["size_gb"] for r in repos), 1),
        "repos": repos,
        "models": _models(repos, needs, presets),
        "needs_missing": [asdict(needs[r["repo_id"]]) for r in repos if r["needed"] and r["health"] in ("missing", "partial")],
    }


def _models(repos: list[dict], needs: dict[str, Need], presets: dict[str, str] | None) -> list[dict]:
    """The roster by model: one entry per use tag (a catalog variant, a casting preset, a postproc
    tool, a trainer preset, a manifest phase) with its repos, their roles and the worst health."""
    by_repo = {r["repo_id"]: r for r in repos}
    catalog_order: dict[str, int] = {}
    for p in model_catalog.pipelines():
        for v in model_catalog.variants(p):
            catalog_order[f"catalog:{p}/{v['id']}"] = len(catalog_order)
    groups: dict[str, dict] = {}
    for n in needs.values():
        r = by_repo.get(n.repo_id)
        for u in n.uses:
            g = groups.setdefault(u["tag"], {"tag": u["tag"], "label": u["label"], "kind": _use_kind(u["tag"]),
                                             "where": where_used(u["tag"], presets), "repos": [], "health": "ok"})
            g["repos"].append({"repo_id": n.repo_id, "role": u["role"], "health": r["health"] if r else "missing",
                               "size_gb": r["size_gb"] if r else 0.0, "gated": n.gated})
    kind_order = {"model": 0, "preset": 1, "tool": 2, "train": 3, "manifest": 4}
    for g in groups.values():
        g["repos"].sort(key=lambda x: (ROLE_ORDER.get(x["role"], 9), x["repo_id"].lower()))
        g["health"] = min((x["health"] for x in g["repos"]), key=lambda h: SEVERITY.index(h) if h in SEVERITY else 9)
    return sorted(groups.values(), key=lambda g: (kind_order.get(g["kind"], 9), catalog_order.get(g["tag"], 10_000), g["label"].lower()))


# --- repair + the worker's pins ------------------------------------------------------------

def _weights_in(snapshot: Path) -> bool:
    return any(p.is_file() and p.suffix.lower() in WEIGHT_SUFFIXES for p in snapshot.rglob("*"))


def pin_for(repo_id: str, files: list[str], hub: Path | None = None, whole: bool = False) -> str | None:
    """The revision a worker should read: the ref'd one when it holds every needed file, else
    the newest revision that does. None when nothing complete is cached. A `whole` need also
    wants weight files in the revision, not just the probe."""
    rdir = repo_dir(repo_id, hub)
    if not rdir.is_dir():
        return None
    ref = _ref_main(rdir)

    def ok(s: Path) -> bool:
        have = all(_has(s, f) for f in files) if files else any(p.is_file() for p in s.rglob("*"))
        return have and (not whole or _weights_in(s))
    pinned = pinned_commit(repo_id)
    if pinned and (rdir / "snapshots" / pinned).is_dir() and ok(rdir / "snapshots" / pinned):
        return pinned
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
    target = pin_for(repo_id, files, hub, whole=bool(need and need.whole))
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
        rev = pin_for(n.repo_id, n.files, hub, whole=n.whole)
        if rev:
            out[n.repo_id] = rev
    return out


def worker_env(hub: Path | None = None, online: bool = False) -> dict[str, str]:
    """What a worker's environment gains: the pinned revisions, the hub forced offline
    (R163: fetches are explicit loom actions; a stale ref must never start a download mid-job;
    `LOOM_WORKERS_ONLINE=1` restores the old online behaviour; `online` = the cache worker,
    the one that may fetch) and the Hugging Face token in force (step e)."""
    env = {"LOOM_HF_REVISIONS": json.dumps(pins(hub), separators=(",", ":")),
           "HF_HOME": str((hub or hub_dir()).parent)}       # step c: the location in force, per job
    if not online and not CONFIG.workers_online:
        env["HF_HUB_OFFLINE"] = "1"
    tok = hf_token()
    if tok:
        env["HF_TOKEN"] = tok
    return env


# --- the app-level settings block (step b: pins; step c: the cache location) ------------------

def app_settings() -> dict:
    """The `settings` block of `.loom_state/app.json` (machine-local, next to the recents)."""
    p = CONFIG.app_pointer_path
    try:
        data = ws_mod.read_json(p) if p.is_file() else {}
    except ws_mod.WorkspaceError:
        data = {}
    return dict(data.get("settings") or {})


def set_app_setting(key: str, value) -> dict:
    """Write one setting (None removes it) without touching the pointer's other fields."""
    p = CONFIG.app_pointer_path
    try:
        data = ws_mod.read_json(p) if p.is_file() else {}
    except ws_mod.WorkspaceError:
        data = {}
    data.setdefault("schema_version", 1)
    data.setdefault("active_project", None)
    data.setdefault("recent", [])
    settings = dict(data.get("settings") or {})
    if value is None:
        settings.pop(key, None)
    else:
        settings[key] = value
    data["settings"] = settings
    ws_mod.atomic_write_json(p, data)
    return settings


def pinned_commit(repo_id: str) -> str | None:
    """A revision the author pinned for a repo (`settings.cache_pins`), matched ignoring case."""
    pins_ = app_settings().get("cache_pins") or {}
    want = repo_id.lower()
    for k, v in pins_.items():
        if k.lower() == want and v:
            return str(v)
    return None


def set_pin(repo_id: str, commit: str | None, hub: Path | None = None) -> dict:
    rdir = repo_dir(repo_id, hub)
    if commit:
        if not (rdir / "snapshots" / commit).is_dir():
            raise CacheError(404, f"{repo_id!r} has no cached revision {commit!r}")
    pins_ = {k: v for k, v in (app_settings().get("cache_pins") or {}).items() if k.lower() != repo_id.lower()}
    if commit:
        pins_[repo_id] = commit
    set_app_setting("cache_pins", pins_ or None)
    return {"repo_id": repo_id, "pinned": commit, "pins": pins_}


# --- the Hugging Face token (step e) -----------------------------------------------------------

def _token_values() -> tuple[str | None, str | None, str | None]:
    env_val = next((os.environ.get(k) for k in TOKEN_KEYS if os.environ.get(k)), None)
    file_env = getattr(config_mod, "_FILE_ENV", {})
    file_val = next((file_env.get(k) for k in TOKEN_KEYS if file_env.get(k)), None)
    setting = app_settings().get("hf_token")
    return env_val, file_val, (str(setting) if setting else None)


def token_source() -> str | None:
    """Where the token comes from, the location's precedence (D1): a real env var (`env`; the
    startup export of `.env.local` into the environment still reads as `dotenv`), `.env` /
    `.env.local` (`dotenv`), the loom setting (`setting`), else None."""
    env_val, file_val, setting = _token_values()
    if env_val and env_val != file_val:
        return "env"
    if file_val:
        return "dotenv"
    if setting:
        return "setting"
    return None


def hf_token() -> str | None:
    """The token in force (never logged, never returned by an endpoint in full)."""
    env_val, file_val, setting = _token_values()
    return {"env": env_val, "dotenv": file_val, "setting": setting}.get(token_source() or "", None)


def _mask(token: str | None) -> str | None:
    if not token:
        return None
    return f"{token[:3]}…{token[-4:]}" if len(token) >= 10 else "…"


def token_info() -> dict:
    src = token_source()
    return {"set": src is not None, "source": src, "masked": _mask(hf_token()), "managed": src in (None, "setting")}


def set_token(token: str | None) -> dict:
    """Store the token as `settings.hf_token` (None or blank clears it). Refused while the
    environment or `.env.local` supplies one, with the reason, because the setting would not
    take effect (D1)."""
    src = token_source()
    if src in ("env", "dotenv"):
        where = "the environment" if src == "env" else ".env.local"
        raise CacheError(409, f"the token comes from {where}; remove HF_TOKEN there to manage it here")
    t = (token or "").strip()
    if not t:
        set_app_setting("hf_token", None)
        return token_info()
    if any(c.isspace() for c in t) or len(t) < 12:
        raise CacheError(400, "that does not look like a Hugging Face token (hf_… from huggingface.co/settings/tokens)")
    set_app_setting("hf_token", t)
    return token_info()


def check_token(token: str | None = None) -> dict:
    """Ask the hub who the token belongs to: the given candidate, else the one in force. The
    orchestrator is the one process allowed online for this; the result never echoes the token."""
    t = (token or "").strip() or hf_token()
    if not t:
        return {"ok": False, "error": "no token to check"}
    try:
        from huggingface_hub import HfApi
        me = HfApi(token=t).whoami()
    except Exception as e:  # noqa: BLE001 - the reason is the answer
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    return {"ok": True, "user": me.get("name"), "type": me.get("type"),
            "orgs": [o.get("name") for o in (me.get("orgs") or []) if isinstance(o, dict)]}


# --- fetch plan, delete, prune (step b) ------------------------------------------------------

def plan_fetch(repo_id: str, files: list[str] | None = None, force: bool = False, hub: Path | None = None) -> dict:
    """What a fetch job should pull: the given files; else, for a `whole` need, a snapshot of the
    repo unless a complete revision (probe + weights) is cached (`force` fetches regardless — the
    library skips what is there); else the roster files that do not resolve (all of them with
    `force`). A repo outside the roster needs explicit files. `nothing` = no job to queue."""
    need = None
    for k, n in roster_map().items():
        if k.lower() == repo_id.lower():
            need = n
            break
    snapshot = False
    if files:
        wanted = list(files)
    elif need is None:
        raise CacheError(404, f"{repo_id!r} is not in loom's roster; name the files to fetch")
    elif need.whole:
        wanted = []
        snapshot = force or pin_for(need.repo_id, need.files, hub, whole=True) is None
    elif force:
        wanted = list(need.files)
    else:
        wanted = [f for f in need.files if resolve(need.repo_id, f, hub) is None]
    return {"repo_id": need.repo_id if need else repo_id, "files": wanted, "snapshot": snapshot,
            "nothing": not wanted and not snapshot,
            "ignore_patterns": list(DIFFUSERS_IGNORE) if (snapshot and need and need.kind == "diffusers") else None,
            "gated": bool(need and need.gated), "needed": need is not None, "whole": bool(need and need.whole),
            "used_by": list(need.used_by) if need else []}


def _scan(hub: Path):
    from huggingface_hub import scan_cache_dir
    return scan_cache_dir(hub)


def delete_revision(repo_id: str, commit: str, hub: Path | None = None) -> dict:
    hub = hub or hub_dir()
    rdir = repo_dir(repo_id, hub)
    if not (rdir / "snapshots" / commit).is_dir():
        raise CacheError(404, f"{repo_id!r} has no cached revision {commit!r}")
    info = _scan(hub)
    strategy = info.delete_revisions(commit)
    freed = int(strategy.expected_freed_size)
    strategy.execute()
    if not (rdir / "snapshots").is_dir() or not any((rdir / "snapshots").iterdir()):
        shutil.rmtree(rdir, ignore_errors=True)           # the last revision took the folder with it
    return {"repo_id": repo_id, "deleted": [commit], "freed_gb": round(freed / 1e9, 2)}


def delete_repo(repo_id: str, hub: Path | None = None) -> dict:
    hub = hub or hub_dir()
    rdir = repo_dir(repo_id, hub)
    if not rdir.is_dir():
        raise CacheError(404, f"{repo_id!r} is not in the cache")
    commits = [s.name for s in _snapshots(rdir)]
    freed = 0
    if commits:
        strategy = _scan(hub).delete_revisions(*commits)
        freed = int(strategy.expected_freed_size)
        strategy.execute()
    freed += _dir_size(rdir) if rdir.is_dir() else 0
    shutil.rmtree(rdir, ignore_errors=True)
    return {"repo_id": repo_id, "deleted": commits, "freed_gb": round(freed / 1e9, 2)}


def plan_prune(hub: Path | None = None) -> dict:
    """What a prune would remove, and nothing else: unreferenced revisions of ROSTER repos that
    are NOT complete for loom, empty repo folders, and blobs no snapshot links to (only judged
    where the repo uses symlinks). Never a complete revision, never another tool's repo."""
    hub = hub or hub_dir()
    needs_ci = {k.lower(): v for k, v in roster_map().items()}
    revisions: list[dict] = []
    empty: list[dict] = []
    blobs: list[dict] = []
    if hub.is_dir():
        for rdir in sorted(hub.glob("models--*")):
            if not rdir.is_dir():
                continue
            repo_id = _repo_id_of(rdir)
            need = needs_ci.get(repo_id.lower())
            snaps = _snapshots(rdir)
            if not snaps or all(not any(p.is_file() for p in s.rglob("*")) for s in snaps):
                empty.append({"repo_id": repo_id, "size_gb": round(_dir_size(rdir) / 1e9, 3)})
                continue
            if need is None:
                continue                                    # another tool's repo: untouched
            ref = _ref_main(rdir)
            for s in snaps:
                if s.name == ref:
                    continue
                complete = all(_has(s, f) for f in need.files) if need.files else any(p.is_file() for p in s.rglob("*"))
                if not complete:
                    revisions.append({"repo_id": need.repo_id, "commit": s.name, "size_gb": round(_dir_size(s) / 1e9, 3),
                                      "files": sum(1 for p in s.rglob("*") if p.is_file())})
            # orphan blobs: only where snapshots link to blobs (a copied cache links nothing)
            links = [p for s in snaps for p in s.rglob("*") if p.is_symlink()]
            if links and (rdir / "blobs").is_dir():
                referenced = set()
                for p in links:
                    try:
                        referenced.add(p.resolve())
                    except OSError:
                        pass
                for b in (rdir / "blobs").iterdir():
                    if b.is_file() and b.resolve() not in referenced:
                        blobs.append({"repo_id": need.repo_id, "blob": b.name, "size_gb": round(b.stat().st_size / 1e9, 3)})
    total = sum(x["size_gb"] for x in revisions + empty + blobs)
    return {"revisions": revisions, "empty_repos": empty, "orphan_blobs": blobs, "total_gb": round(total, 2)}


def prune(hub: Path | None = None) -> dict:
    hub = hub or hub_dir()
    plan = plan_prune(hub)
    done = {"revisions": [], "empty_repos": [], "orphan_blobs": [], "freed_gb": 0.0}
    commits = [r["commit"] for r in plan["revisions"]]
    if commits:
        strategy = _scan(hub).delete_revisions(*commits)
        done["freed_gb"] += strategy.expected_freed_size / 1e9
        strategy.execute()
        done["revisions"] = plan["revisions"]
    for e in plan["empty_repos"]:
        shutil.rmtree(repo_dir(e["repo_id"], hub), ignore_errors=True)
        done["empty_repos"].append(e)
    for b in plan["orphan_blobs"]:
        p = repo_dir(b["repo_id"], hub) / "blobs" / b["blob"]
        try:
            size = p.stat().st_size
            p.unlink()
            done["freed_gb"] += size / 1e9
            done["orphan_blobs"].append(b)
        except OSError:
            pass
    done["freed_gb"] = round(done["freed_gb"], 2)
    return done


# --- the location as a setting, and the move (step c) ----------------------------------------

def _check_target(path: str) -> Path:
    if not path or not path.strip():
        raise CacheError(400, "give an absolute folder path")
    p = Path(path.strip())
    if not p.is_absolute():
        raise CacheError(400, f"{path!r} is not an absolute path")
    if p.exists() and not p.is_dir():
        raise CacheError(400, f"{path!r} exists and is not a folder")
    return p


def set_location(path: str) -> dict:
    """Make `path` the cache home (`settings.models_dir`). Refused while an env source is in
    force — that line must go first, and the response names it. The folder is created; the
    hub tree is NOT moved (that is `plan_move`)."""
    src = location_source()
    if src in ("env", "dotenv"):
        raise CacheError(409, f"the cache location is set by {'the LOOM_MODELS_DIR environment variable' if src == 'env' else 'LOOM_MODELS_DIR in .env or .env.local'}; "
                              "remove it there to manage the location from loom")
    p = _check_target(path)
    try:
        (p / "hub").mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise CacheError(400, f"cannot create {p}: {e}")
    current = effective_home()
    set_app_setting("models_dir", str(p))
    if current.resolve() != p.resolve() and (current / "hub").is_dir():
        set_app_setting("previous_models_dir", str(current))
    return {"path": str(p), "source": location_source(), "applies_to": "the next scan and the next job"}


def plan_move(to: str) -> dict:
    """Validate a move of the hub tree to `to`: not the current home or inside it, enough free
    space for the tree. Returns what the job will carry."""
    src = location_source()
    if src in ("env", "dotenv"):
        raise CacheError(409, "the cache location is set by LOOM_MODELS_DIR (env or .env); remove it there before moving from loom")
    dst = _check_target(to)
    cur = effective_home()
    hub = cur / "hub"
    if not hub.is_dir():
        raise CacheError(404, f"no hub cache at {hub}")
    if dst.resolve() == cur.resolve() or cur.resolve() in dst.resolve().parents:
        raise CacheError(409, "the destination must not be the current location or inside it")
    size = _dir_size(hub)
    try:
        probe = dst if dst.exists() else (dst.parent if dst.parent.exists() else Path(dst.anchor))
        free = shutil.disk_usage(probe).free
    except OSError:
        free = None
    if free is not None and free < size:
        raise CacheError(409, f"not enough free space at {dst}: {free / 1e9:.1f} GB free, the cache is {size / 1e9:.1f} GB")
    return {"from": str(cur), "to": str(dst), "size_gb": round(size / 1e9, 1), "free_gb": round(free / 1e9, 1) if free is not None else None}


def finish_move(job: dict) -> bool:
    """Completion observer: a done `hf_cache` move job switches the setting to its destination
    and remembers the old home as `previous_models_dir` (kept until deleted)."""
    if job.get("pipeline") != "hf_cache" or job.get("mode") != "move":
        return False
    if not (job.get("result") or {}).get("ok"):
        return False
    params = job.get("params") or {}
    to, frm = params.get("to"), params.get("cache_home")
    if not to:
        return False
    if location_source() in ("env", "dotenv"):
        return False                                   # an env source still wins; nothing to switch
    set_app_setting("models_dir", str(Path(to)))
    if frm and Path(frm).resolve() != Path(to).resolve():
        set_app_setting("previous_models_dir", str(frm))
    return True


def delete_previous() -> dict:
    prev = app_settings().get("previous_models_dir")
    if not prev:
        raise CacheError(404, "no previous cache location is recorded")
    if Path(prev).resolve() == effective_home().resolve():
        set_app_setting("previous_models_dir", None)
        raise CacheError(409, "the previous location is the current one; nothing to delete")
    hub = Path(prev) / "hub"
    size = _dir_size(hub) if hub.is_dir() else 0
    shutil.rmtree(hub, ignore_errors=True)
    set_app_setting("previous_models_dir", None)
    return {"deleted": str(hub), "freed_gb": round(size / 1e9, 1)}
