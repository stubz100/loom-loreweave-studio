"""M2.17 step a — the model cache seen from loom: the resolver that survives ref drift, the
roster, the inventory with health, the ref repair, and the pins handed to workers.

A fake hub cache in tmp reproduces the 2026-09-21 refusal exactly: `refs/main` names a
snapshot holding only the VAE while the complete snapshot sits unreferenced. Regular files
stand in for the hub's symlinks (the resolver follows either). No network, no GPU."""

from __future__ import annotations

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
JUNE = "03d6521e6f6a47396b3f951cbea50f7e6c2f482e"
AUGUST = "06029c966dd5b73929c909f046cbd29303b98879"


def _repo(hub: Path, repo_id: str, revisions: dict[str, list[str]], main: str | None, order: list[str] | None = None) -> Path:
    """A fake cached repo: {commit: [files]}; `main` = what refs/main names (None = no ref).
    `order` sets snapshot mtimes oldest→newest so 'newest first' is deterministic."""
    rdir = hub / f"models--{repo_id.replace('/', '--')}"
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
    monkeypatch.delenv("LOOM_MODELS_DIR", raising=False)
    from orchestrator import config as config_mod
    monkeypatch.delitem(config_mod._FILE_ENV, "LOOM_MODELS_DIR", raising=False)   # the repo .env must not win here
    monkeypatch.delenv("LOOM_WORKERS_ONLINE", raising=False)
    return hub


def _drifted(hub: Path) -> Path:
    """The real situation: June complete, August VAE-only, main → August."""
    files = [TRANSFORMER, TE, VAE, "split_files/loras/Flux2TurboComfyv2.safetensors"]
    return _repo(hub, DEV, {JUNE: files, AUGUST: [VAE]}, main=AUGUST, order=[JUNE, AUGUST])


def test_roster_merges_the_four_sources_by_repo():
    needs = weights.roster_map()
    dev = needs[DEV]
    assert TRANSFORMER in dev.files and TE in dev.files and VAE in dev.files      # the catalog's probe files
    assert "split_files/loras/Flux2TurboComfyv2.safetensors" in dev.files          # the postproc Turbo LoRA
    assert any(u.startswith("catalog:flux2/flux.2-dev") for u in dev.used_by)
    assert any(u.startswith("postproc:flux2_turbo_lora") for u in dev.used_by)
    assert any(u.startswith("multi:") for u in dev.used_by)
    zt = needs["Tongyi-MAI/Z-Image-Turbo"]
    assert zt.kind == "diffusers" and zt.files == ["model_index.json"] and "phase:P0" in zt.used_by
    assert all("/" in n.repo_id for n in weights.roster())                         # no file-target weights in the hub roster


def test_resolver_reads_any_cached_revision_never_the_ref_alone(hub):
    _drifted(hub)
    r = weights.resolve(DEV, TRANSFORMER)
    assert r is not None and r.revision == JUNE and r.via_ref is False and r.path.is_file()
    v = weights.resolve(DEV, VAE)
    assert v is not None and v.revision == AUGUST and v.via_ref is True              # the ref wins when it has the file
    assert weights.resolve(DEV, "split_files/nope.safetensors") is None
    assert weights.resolve("nobody/nothing", TRANSFORMER) is None


def test_components_probe_and_variant_gate_pass_under_drift(hub):
    from orchestrator import components, model_catalog
    _drifted(hub)
    assert components._hf_cache_probe(DEV, TRANSFORMER) is True
    assert components.variant_weights_present(model_catalog.find_variant("flux2", "flux.2-dev")) is True
    assert components._hf_cache_probe(DEV, "split_files/nope.safetensors") is False


def test_inventory_health_and_detail(hub):
    _drifted(hub)
    _repo(hub, "Tongyi-MAI/Z-Image-Turbo", {"f332072aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": ["model_index.json", "vae/config.json"]}, main="f332072aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    _repo(hub, "other/tool-model", {"aaaa": ["config.json"]}, main="aaaa")
    _repo(hub, "other/empty", {}, main=None)
    _repo(hub, "stabilityai/stable-diffusion-3.5-medium", {"bbbb": ["vae/config.json"]}, main="bbbb")   # needed, but no model_index
    inv = weights.inventory()
    by = {r["repo_id"]: r for r in inv["repos"]}
    dev = by[DEV]
    assert dev["health"] == "ref_drift" and JUNE[:7] in dev["detail"] and "repair" in dev["detail"]
    assert dev["ref"] == AUGUST and dev["needed"] is True
    ref_rev = next(r for r in dev["revisions"] if r["ref"] == "main")
    assert ref_rev["complete_for_loom"] is False and ref_rev["needed_present"] == 1
    assert by["Tongyi-MAI/Z-Image-Turbo"]["health"] == "ok"
    assert by["other/tool-model"]["health"] == "unused" and by["other/tool-model"]["needed"] is False
    assert by["other/empty"]["health"] == "empty"
    assert by["stabilityai/stable-diffusion-3.5-medium"]["health"] == "missing"
    klein = by["black-forest-labs/FLUX.2-klein-4B"]                                # a roster repo absent from the cache
    assert klein["health"] == "missing" and klein["revisions"] == [] and klein["needed"] is True
    assert any(n["repo_id"] == "stabilityai/stable-diffusion-3.5-medium" for n in inv["needs_missing"])
    assert inv["location"]["path"] == str(hub.parent) and inv["location"]["source"] == "hf_home"   # the fixture sets HF_HOME, nothing else
    assert inv["size_gb"] >= 0 and inv["scanned_at"]


def test_repair_rewrites_the_ref_to_the_complete_revision(hub):
    rdir = _drifted(hub)
    r = weights.repair_ref(DEV)
    assert r == {"repo_id": DEV, "previous": AUGUST, "now": JUNE, "changed": True, "files": r["files"], "used_by": r["used_by"]}
    assert (rdir / "refs" / "main").read_text(encoding="utf-8") == JUNE
    assert not list((rdir / "refs").glob("*.tmp"))
    again = weights.repair_ref(DEV)
    assert again["changed"] is False and again["now"] == JUNE
    dev = next(r for r in weights.inventory()["repos"] if r["repo_id"] == DEV)
    assert dev["health"] == "stale_extra"                                       # complete now, one extra revision
    with pytest.raises(weights.CacheError) as e:
        weights.repair_ref("nobody/nothing")
    assert e.value.status == 404
    _repo(hub, "black-forest-labs/FLUX.2-klein-4B", {"cccc": ["tokenizer.json"]}, main="cccc")   # the needed model_index.json is nowhere
    with pytest.raises(weights.CacheError) as e:
        weights.repair_ref("black-forest-labs/FLUX.2-klein-4B")
    assert e.value.status == 409


def test_worker_env_pins_the_complete_revision_and_forces_offline(hub, monkeypatch):
    _drifted(hub)
    env = weights.worker_env()
    assert json.loads(env["LOOM_HF_REVISIONS"])[DEV] == JUNE
    assert env["HF_HUB_OFFLINE"] == "1"
    monkeypatch.setenv("LOOM_WORKERS_ONLINE", "1")
    assert "HF_HUB_OFFLINE" not in weights.worker_env()
    weights.repair_ref(DEV)
    assert json.loads(weights.worker_env()["LOOM_HF_REVISIONS"])[DEV] == JUNE       # ref'd and complete: still June


def test_worker_reads_the_pin_without_torch(monkeypatch):
    src = Path(__file__).resolve().parents[2] / "pipelines" / "multistack" / "src" / "pipeline" / "flux2" / "hf_pins.py"
    spec = importlib.util.spec_from_file_location("hf_pins", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.delenv("LOOM_HF_REVISIONS", raising=False)
    assert mod.pinned_revision(DEV) is None
    monkeypatch.setenv("LOOM_HF_REVISIONS", json.dumps({DEV: JUNE}))
    assert mod.pinned_revision(DEV) == JUNE and mod.pinned_revision("x/y") is None
    monkeypatch.setenv("LOOM_HF_REVISIONS", "not json")
    assert mod.pinned_revision(DEV) is None
    # and the worker's resolver passes the pin to the hub library
    q = (src.parent / "scaled_fp8.py").read_text(encoding="utf-8")
    assert "revision = pinned_revision(repo_id)" in q and "revision=revision" in q


def test_runner_spawns_carry_the_pins():
    src = (Path(__file__).resolve().parents[1] / "runner.py").read_text(encoding="utf-8")
    assert src.count("env.update(weights.worker_env())") == 2                    # the cold spawn and the warm worker


@pytest.fixture()
def client(monkeypatch, tmp_path, hub):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def test_cache_endpoints(client, hub):
    _drifted(hub)
    r = client.get("/cache")
    assert r.status_code == 200
    dev = next(x for x in r.json()["repos"] if x["repo_id"] == DEV)
    assert dev["health"] == "ref_drift"
    assert client.post(f"/cache/{DEV}/repair", headers={"X-Loom-Token": "wrong"}).status_code == 401
    assert client.post("/cache/nobody/nothing/repair").status_code == 404
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "x"}, batch_id="b", index=0, batch_size=1, requester_id="sandbox")
    RUNNER.jobs[jid]["status"] = "running"
    try:
        assert client.post(f"/cache/{DEV}/repair").status_code == 409             # never while a job runs
    finally:
        RUNNER.jobs[jid]["status"] = "canceled"
    r = client.post(f"/cache/{DEV}/repair")
    assert r.status_code == 200 and r.json()["now"] == JUNE and r.json()["changed"] is True
    assert next(x for x in client.get("/cache").json()["repos"] if x["repo_id"] == DEV)["health"] == "stale_extra"
