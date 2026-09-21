"""M2.17 step b — fetch as a job, verify, delete, prune, pin; the roster's non-diffusers probe;
case-insensitive cache folders; the worker's own logic (verify hashes, move, fetch) by file
path without torch or the network."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator import weights
from orchestrator.config import CONFIG

DEV = "Comfy-Org/flux2-dev"
TRANSFORMER = "split_files/diffusion_models/flux2_dev_fp8mixed.safetensors"
TE = "split_files/text_encoders/mistral_3_small_flux2_fp8.safetensors"
VAE = "split_files/vae/flux2-vae.safetensors"
LORA = "split_files/loras/Flux2TurboComfyv2.safetensors"
JUNE = "03d6521e6f6a47396b3f951cbea50f7e6c2f482e"
AUGUST = "06029c966dd5b73929c909f046cbd29303b98879"
WORKER = Path(__file__).resolve().parents[2] / "pipelines" / "hf_cache" / "run_pipeline.py"


def _repo(hub: Path, repo_id: str, revisions: dict[str, list[str]], main: str | None, order=None, folder: str | None = None) -> Path:
    rdir = hub / (folder or f"models--{repo_id.replace('/', '--')}")
    (rdir / "blobs").mkdir(parents=True)
    for i, commit in enumerate(order or list(revisions)):
        snap = rdir / "snapshots" / commit
        snap.mkdir(parents=True)
        for f in revisions[commit]:
            p = snap / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * (10 + i))
        ts = time.time() - 1000 + i * 100
        os.utime(snap, (ts, ts))
    if main:
        (rdir / "refs").mkdir(parents=True)
        (rdir / "refs" / "main").write_text(main, encoding="utf-8")
    return rdir


@pytest.fixture()
def hub(tmp_path, monkeypatch):
    home = tmp_path / "hf_home"
    hub = home / "hub"
    hub.mkdir(parents=True)
    monkeypatch.setenv("HF_HOME", str(home))
    monkeypatch.setenv("LOOM_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("LOOM_MODELS_DIR", raising=False)
    from orchestrator import config as config_mod
    monkeypatch.delitem(config_mod._FILE_ENV, "LOOM_MODELS_DIR", raising=False)   # the repo .env must not win here
    monkeypatch.delenv("LOOM_WORKERS_ONLINE", raising=False)
    return hub


def _drifted(hub: Path) -> Path:
    return _repo(hub, DEV, {JUNE: [TRANSFORMER, TE, VAE, LORA], AUGUST: [VAE]}, main=AUGUST, order=[JUNE, AUGUST])


def _worker():
    spec = importlib.util.spec_from_file_location("hf_cache_worker", WORKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- the roster + folders ----------------------------------------------------------------------

def test_roster_probes_transformers_style_repos_by_config_json():
    needs = weights.roster_map()
    bir = needs["ZhengPeng7/BiRefNet"]
    assert bir.files == ["config.json"] and "model_index.json" not in bir.files
    assert any(u.startswith("catalog:birefnet/") for u in bir.used_by) and "postproc:birefnet" in bir.used_by


def test_cache_folders_match_ignoring_case(hub):
    """The catalog says FLUX.2-klein-9B-kv; the download made a lower-case folder. One repo."""
    rid = "black-forest-labs/FLUX.2-klein-9B-kv"
    _repo(hub, rid, {"c1": ["model_index.json"]}, main="c1", folder="models--black-forest-labs--FLUX.2-klein-9b-kv")
    assert weights.repo_dir(rid).name == "models--black-forest-labs--FLUX.2-klein-9b-kv"
    assert weights.resolve(rid, "model_index.json") is not None
    inv = weights.inventory()
    rows = [r for r in inv["repos"] if r["repo_id"].lower() == rid.lower()]
    assert len(rows) == 1 and rows[0]["repo_id"] == rid and rows[0]["health"] == "ok"


# --- settings + pins ---------------------------------------------------------------------------

def test_settings_survive_the_pointer_and_pins_win_when_complete(hub, monkeypatch):
    weights.set_app_setting("cache_pins", {DEV: JUNE})
    from orchestrator import projects
    projects.write_pointer(hub.parent)                       # the recents writer must keep settings
    assert weights.app_settings()["cache_pins"] == {DEV: JUNE}
    rdir = _drifted(hub)
    assert weights.pin_for(DEV, weights.roster_map()[DEV].files) == JUNE
    weights.repair_ref(DEV)                                  # ref → June anyway; pin still June
    (rdir / "refs" / "main").write_text(AUGUST, encoding="utf-8")
    assert weights.resolve(DEV, VAE).revision == JUNE        # the pin beats the ref
    weights.set_app_setting("cache_pins", {DEV: AUGUST})     # an incomplete pin is ignored for pinning
    assert weights.pin_for(DEV, weights.roster_map()[DEV].files) == JUNE
    r = weights.set_pin(DEV, None)
    assert r["pinned"] is None and "cache_pins" not in weights.app_settings()
    with pytest.raises(weights.CacheError):
        weights.set_pin(DEV, "nope")


# --- fetch plan, delete, prune ----------------------------------------------------------------

def test_plan_fetch_names_only_what_is_missing(hub):
    _repo(hub, DEV, {JUNE: [TRANSFORMER, VAE]}, main=JUNE)
    plan = weights.plan_fetch(DEV)
    assert sorted(plan["files"]) == sorted([TE, LORA]) and plan["needed"] is True
    assert weights.plan_fetch(DEV, force=True)["files"] == weights.roster_map()[DEV].files
    assert weights.plan_fetch(DEV, files=[VAE])["files"] == [VAE]
    with pytest.raises(weights.CacheError) as e:
        weights.plan_fetch("nobody/nothing")
    assert e.value.status == 404
    assert weights.plan_fetch("nobody/nothing", files=["a.bin"])["needed"] is False


def test_prune_plans_only_the_junk(hub):
    rdir = _drifted(hub)                                                     # June complete + unreferenced
    _repo(hub, "other/tool", {"t1": ["config.json"], "t2": ["config.json"]}, main="t1")   # another tool: untouched
    _repo(hub, "other/empty", {}, main=None)
    plan = weights.plan_prune()
    assert plan["revisions"] == []                                           # June is complete: never
    assert [e["repo_id"] for e in plan["empty_repos"]] == ["other/empty"]
    weights.repair_ref(DEV)                                                  # main → June; August is now junk
    plan = weights.plan_prune()
    assert [(r["repo_id"], r["commit"]) for r in plan["revisions"]] == [(DEV, AUGUST)]
    assert plan["orphan_blobs"] == []                                        # regular files, no symlinks: blobs untouched
    done = weights.prune()
    assert not (rdir / "snapshots" / AUGUST).exists() and (rdir / "snapshots" / JUNE).is_dir()
    assert not (hub / "models--other--empty").exists() and (hub / "models--other--tool" / "snapshots" / "t2").is_dir()
    assert done["revisions"] and done["empty_repos"]


def test_delete_revision_and_repo(hub):
    rdir = _drifted(hub)
    r = weights.delete_revision(DEV, AUGUST)
    assert r["deleted"] == [AUGUST] and not (rdir / "snapshots" / AUGUST).exists() and (rdir / "snapshots" / JUNE).is_dir()
    with pytest.raises(weights.CacheError) as e:
        weights.delete_revision(DEV, AUGUST)
    assert e.value.status == 404
    r = weights.delete_repo(DEV)
    assert r["deleted"] == [JUNE] and not rdir.exists()
    with pytest.raises(weights.CacheError):
        weights.delete_repo(DEV)


# --- the worker, by file path ------------------------------------------------------------------

def test_worker_verifies_blobs_against_their_names(tmp_path):
    w = _worker()
    data = b"weights" * 1000
    good = tmp_path / hashlib.sha256(data).hexdigest()
    good.write_bytes(data)
    assert w.verify_file(good)["ok"] is True and w.verify_file(good)["method"] == "sha256"
    bad = tmp_path / ("0" * 64)
    bad.write_bytes(data)
    assert w.verify_file(bad)["ok"] is False
    small = b"{}"
    git = hashlib.sha1(f"blob {len(small)}\0".encode() + small).hexdigest()
    g = tmp_path / git
    g.write_bytes(small)
    assert w.verify_file(g)["ok"] is True and w.verify_file(g)["method"] == "git-sha1"
    plain = tmp_path / "config.json"
    plain.write_bytes(small)
    assert w.verify_file(plain)["method"] == "size" and w.verify_file(plain)["ok"] is True
    # the verify task over a fake repo with regular files: size-only, all ok
    hub = tmp_path / "home" / "hub"
    hub.mkdir(parents=True)
    _repo(hub, DEV, {JUNE: [VAE, TE]}, main=JUNE)
    r = w.task_verify({"repo_id": DEV, "files": [VAE, TE], "cache_home": str(tmp_path / "home")})
    assert r["ok"] is True and r["checked"] == 2


def test_worker_moves_the_hub_tree_and_resumes(tmp_path):
    w = _worker()
    src_home = tmp_path / "a"
    hub = src_home / "hub"
    hub.mkdir(parents=True)
    rdir = _repo(hub, DEV, {JUNE: [VAE]}, main=JUNE)
    blob = rdir / "blobs" / ("a" * 64)
    blob.write_bytes(b"blob" * 100)
    linked = rdir / "snapshots" / JUNE / "linked.bin"
    try:
        os.symlink(os.path.relpath(blob, linked.parent), linked)
        has_link = True
    except OSError:
        has_link = False
    r = w.task_move({"cache_home": str(src_home), "to": str(tmp_path / "b")})
    assert r["ok"] is True, r
    dst = tmp_path / "b" / "hub" / rdir.name
    assert (dst / "refs" / "main").read_text(encoding="utf-8") == JUNE
    assert (dst / "snapshots" / JUNE / VAE).is_file()
    if has_link:
        assert (dst / "snapshots" / JUNE / "linked.bin").is_symlink() or (dst / "snapshots" / JUNE / "linked.bin").is_file()
    again = w.task_move({"cache_home": str(src_home), "to": str(tmp_path / "b")})
    assert again["ok"] is True and again["copied"] == 0 and again["skipped"] == r["seen"]   # resumable: nothing re-copied
    bad = w.task_move({"cache_home": str(src_home), "to": str(src_home)})
    assert bad["ok"] is False


def test_worker_fetch_uses_the_hub_library_per_file(tmp_path, monkeypatch, capsys):
    w = _worker()
    calls = []

    def fake_download(repo_id, filename, revision=None, cache_dir=None, token=None):
        calls.append((repo_id, filename, revision, cache_dir))
        p = tmp_path / "dl" / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"d" * 64)
        return str(p)
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    r = w.task_fetch({"repo_id": DEV, "files": [TE, VAE], "revision": JUNE, "cache_home": str(tmp_path / "home")})
    assert r["ok"] is True and r["bytes"] == 128 and [c[1] for c in calls] == [TE, VAE]
    assert calls[0][2] == JUNE and calls[0][3].endswith("hub")
    out = capsys.readouterr().out
    assert "[cache] progress 1" in out and "[cache] note Comfy-Org/flux2-dev" in out


# --- the endpoints -----------------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path, hub):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def test_fetch_verify_pin_endpoints_queue_jobs(client, hub):
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    _repo(hub, DEV, {JUNE: [TRANSFORMER, VAE]}, main=JUNE)
    r = client.post("/cache/fetch", json={"repo_id": DEV})
    assert r.status_code == 200, r.text
    job = RUNNER.jobs[r.json()["job_id"]]
    assert job["pipeline"] == "hf_cache" and job["mode"] == "fetch" and job["status"] == "queued"
    assert sorted(job["params"]["files"]) == sorted([TE, LORA]) and job["params"]["cache_home"] == str(hub.parent)
    assert client.post("/cache/fetch", json={"repo_id": "nobody/nothing"}).status_code == 404
    r = client.post("/cache/fetch", json={"repo_id": "Tongyi-MAI/Z-Image-Turbo", "files": ["model_index.json"]})
    assert r.status_code == 200 and RUNNER.jobs[r.json()["job_id"]]["params"]["files"] == ["model_index.json"]
    v = client.post(f"/cache/{DEV}/verify")
    assert v.status_code == 200 and RUNNER.jobs[v.json()["job_id"]]["mode"] == "verify"
    assert client.post("/cache/nobody/nothing/verify").status_code == 404
    p = client.put(f"/cache/{DEV}/pin", json={"commit": JUNE})
    assert p.status_code == 200 and p.json()["pinned"] == JUNE
    assert client.put(f"/cache/{DEV}/pin", json={"commit": "nope"}).status_code == 404
    assert client.put(f"/cache/{DEV}/pin", json={"commit": None}).json()["pinned"] is None
    # the queued cache job is not a canvas tile
    assert 'NO_TILE = new Set(["hf_cache"])' in (Path(__file__).resolve().parents[2] / "frontends" / "v2" / "src" / "canvas" / "tiles.ts").read_text(encoding="utf-8")


def test_delete_and_prune_endpoints_guard_the_queue(client, hub):
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    rdir = _drifted(hub)
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "x"}, batch_id="b", index=0, batch_size=1, requester_id="sandbox")
    RUNNER.jobs[jid]["status"] = "running"
    try:                                                          # a failure here must not leave the runner "running"
        assert client.delete(f"/cache/{DEV}/revisions/{AUGUST}").status_code == 409
        assert client.delete(f"/cache/{DEV}").status_code == 409
        assert client.post("/cache/prune", json={"dry_run": False}).status_code == 409
        assert client.post("/cache/prune", json={}).status_code == 200            # a dry run is always allowed
    finally:
        RUNNER.jobs[jid]["status"] = "canceled"
    r = client.delete(f"/cache/{DEV}/revisions/{AUGUST}")
    assert r.status_code == 200 and r.json()["deleted"] == [AUGUST] and not (rdir / "snapshots" / AUGUST).exists()
    assert client.delete(f"/cache/{DEV}/revisions/{AUGUST}").status_code == 404
    plan = client.post("/cache/prune", json={"dry_run": True}).json()
    assert plan["dry_run"] is True and plan["revisions"] == []
    r = client.delete(f"/cache/{DEV}")
    assert r.status_code == 200 and r.json()["needed"] is True and not rdir.exists()
    assert client.delete(f"/cache/{DEV}", headers={"X-Loom-Token": "wrong"}).status_code == 401


def test_runner_registers_the_cache_adapter_and_loaders_read_the_pin():
    root = Path(__file__).resolve().parents[2]
    runner = (root / "orchestrator" / "runner.py").read_text(encoding="utf-8")
    assert '"hf_cache": hf_cache_adapter' in runner and '"hf_cache": 0.0' in runner
    for rel in ("pipelines/multistack/src/pipeline/sd35/stage1_load_pipeline.py",
                "pipelines/multistack/src/pipeline/zimage/stage1_load_pipeline.py",
                "pipelines/zimage/stage1_load_pipeline.py",
                "pipelines/krea2/stage1_load_pipeline.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "pinned_revision(repo_id)" in src, rel
    for rel in ("pipelines/multistack/src/pipeline/hf_pins.py", "pipelines/zimage/hf_pins.py", "pipelines/krea2/hf_pins.py"):
        assert (root / rel).is_file(), rel
    from orchestrator.adapters import hf_cache
    assert hf_cache.progress("[cache] progress 0.5") == 0.5 and hf_cache.progress("x") is None
    assert hf_cache.collect_note("[cache] note hashing a (1 of 2)") == "hashing a (1 of 2)"
