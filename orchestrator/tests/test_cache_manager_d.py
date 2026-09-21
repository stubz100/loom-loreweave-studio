"""M2.17 step e — the roster by model (every use with a label, a role and where in loom it is
used), whole-repo needs (a probe-only snapshot is not the model; a fetch of it is a snapshot),
the Klein text encoders in the roster on both platforms, the manifest's 8B entry, the token as
a loom setting behind the environment and .env.local, the cache worker online, and the fetch
worker lifting the offline flag before the hub library reads it."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator import components, weights
from orchestrator.config import CONFIG

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "pipelines" / "hf_cache" / "run_pipeline.py"
SD35M = "stabilityai/stable-diffusion-3.5-medium"
KLEIN4 = "black-forest-labs/FLUX.2-klein-4B"
DEV = "Comfy-Org/flux2-dev"
TOK = "hf_abcdefghijklmnopqrstuvwxyz"


def _repo(hub: Path, repo_id: str, revisions: dict[str, list[str]], main: str | None, order=None) -> Path:
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
    monkeypatch.setenv("LOOM_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("LOOM_MODELS_DIR", raising=False)
    monkeypatch.delenv("LOOM_WORKERS_ONLINE", raising=False)
    from orchestrator import config as config_mod
    monkeypatch.delitem(config_mod._FILE_ENV, "LOOM_MODELS_DIR", raising=False)   # the repo .env must not win here
    for k in weights.TOKEN_KEYS:                                                   # nor the developer's token
        monkeypatch.delenv(k, raising=False)
        monkeypatch.delitem(config_mod._FILE_ENV, k, raising=False)
    return hub


def _worker():
    spec = importlib.util.spec_from_file_location("hf_cache_worker_e", WORKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- the roster: labels, roles, the text encoders, whole-repo needs ---------------------------

def test_roster_uses_carry_label_role_and_the_klein_text_encoder(monkeypatch):
    monkeypatch.setattr(components, "_needs_fp8_workaround", lambda: True)     # the rig: Windows ROCm
    needs = weights.roster_map()
    k4 = needs[KLEIN4]
    u = next(x for x in k4.uses if x["tag"] == "catalog:flux2/flux.2-klein-4b")
    assert u["role"] == "model" and u["label"] == "FLUX.2 · flux.2-klein-4b"
    assert k4.whole is False and k4.files == ["flux-2-klein-4b.safetensors"]      # the one file the loader downloads
    te = needs["Qwen/Qwen3-4B"]
    assert te.whole is True and te.files == ["config.json"]
    assert {x["role"] for x in te.uses} == {"text encoder"}
    assert any(x["tag"] == "catalog:flux2/flux.2-klein-4b" for x in te.uses) and "multi:fast" in te.used_by
    te9 = needs["Qwen/Qwen3-8B"]
    assert {x["tag"] for x in te9.uses} >= {"catalog:flux2/flux.2-klein-9b", "catalog:flux2/flux.2-klein-9b-kv",
                                            "catalog:flux2/flux.2-klein-base-9b", "multi:refined"}
    assert "Qwen/Qwen3-8B-FP8" not in needs and "Qwen/Qwen3-4B-FP8" not in needs
    dev = needs[DEV]
    assert next(x for x in dev.uses if x["tag"] == "catalog:flux2/flux.2-klein-4b")["role"] == "vae"
    lora = next(x for x in dev.uses if x["tag"] == "postproc:flux2_turbo_lora")
    assert lora["role"] == "LoRA" and lora["label"] == "FLUX.2 dev Turbo LoRA"
    assert dev.whole is False                                                    # split files: never a snapshot
    assert needs["InstantX/SD3-Controlnet-Tile"].whole and needs["ZhengPeng7/BiRefNet"].whole
    assert needs["facefusion/models-3.0.0"].whole is False and needs["ezioruan/inswapper_128.onnx"].whole is False
    assert any(x["tag"] == "train:zimage" and x["role"] == "base model" for x in needs["Tongyi-MAI/Z-Image"].uses)
    assert needs[SD35M].whole and needs[SD35M].kind == "diffusers"
    assert all(len(n.uses) == len(n.used_by) for n in needs.values())


def test_roster_follows_the_platform_for_the_fp8_text_encoder(monkeypatch):
    monkeypatch.setattr(components, "_needs_fp8_workaround", lambda: False)    # CUDA / Linux
    needs = weights.roster_map()
    assert "Qwen/Qwen3-4B-FP8" in needs and "Qwen/Qwen3-8B-FP8" in needs
    assert "Qwen/Qwen3-4B" not in needs and "Qwen/Qwen3-8B" not in needs


def test_manifest_8b_text_encoder_entry_mirrors_the_4b_one():
    m = json.loads((ROOT / "models.json").read_text(encoding="utf-8"))
    by = {e["id"]: e for p in m["multi_presets"].values() for e in p}
    assert by["qwen3-4b-text-encoder"]["repo_id"] == "Qwen/Qwen3-4B" and by["qwen3-4b-text-encoder"]["fp8_repo_id"] == "Qwen/Qwen3-4B-FP8"
    assert by["qwen3-8b-text-encoder"]["repo_id"] == "Qwen/Qwen3-8B" and by["qwen3-8b-text-encoder"]["fp8_repo_id"] == "Qwen/Qwen3-8B-FP8"
    # the flux2 loader's own rule, which components._entry_resolve_repo mirrors
    src = (ROOT / "pipelines/multistack/src/pipeline/flux2/stage1_load_models.py").read_text(encoding="utf-8")
    assert 'model_spec = f"Qwen/Qwen3-{variant}"' in src and 'return "8B" if "9b" in model_name else "4B"' in src


def test_where_used_names_the_stages_and_the_presets():
    presets = {"clean": "zimage", "upscale": "sd35", "stylelock": "sd35", "inpaint": "sd35", "restore": "face_restore", "resize": "resize"}
    assert weights.where_used("catalog:flux2/flux.2-dev", presets) == ["Cast", "Expand", "Poses"]
    assert weights.where_used("catalog:sd35/sd3.5-medium", presets) == ["Cast", "Expand", "Post: Scale, StyleLock, Inpaint"]
    assert weights.where_used("catalog:sd35/sd3.5-large", presets) == ["Cast", "Expand"]        # presets ride the default variant
    assert weights.where_used("catalog:zimage/zimage-turbo", presets) == ["Cast", "Expand", "LoRA preview", "Post: Clean"]
    assert weights.where_used("multi:fast") == ["Cast (multi, fast preset)"]
    assert weights.where_used("postproc:sd35_tile_cn") == ["Post: Scale"] and weights.where_used("train:zimage") == ["Train"]
    # the stage table matches the v2 composers and the Post tab's preset names
    cs = (ROOT / "frontends/v2/src/compose/composeStore.ts").read_text(encoding="utf-8")
    cast = set(re.findall(r'\{ id: "(\w+)", label:', cs)) - {"multi"}
    expand = set(re.search(r"export type ExpandPipeline = ([^;]+);", cs).group(1).replace('"', "").split(" | "))
    assert {p for p, s in weights.STAGES.items() if "Cast" in s} == cast
    assert {p for p, s in weights.STAGES.items() if "Expand" in s} == expand
    post = (ROOT / "frontends/v2/src/inspect/PostTab.tsx").read_text(encoding="utf-8")
    for pid, label in weights.PRESET_LABELS.items():
        assert f'id: "{pid}", label: "{label}' in post, pid


# --- whole-repo needs ----------------------------------------------------------------------------

def test_a_probe_only_snapshot_is_not_a_whole_model(hub):
    r = _repo(hub, SD35M, {"c1": ["model_index.json", "vae/config.json"]}, main="c1")
    inv = weights.inventory()
    row = next(x for x in inv["repos"] if x["repo_id"] == SD35M)
    assert row["health"] == "partial" and "no weight files" in row["detail"] and row["whole"] is True
    assert row["revisions"][0]["weights"] is False and row["revisions"][0]["complete_for_loom"] is False
    plan = weights.plan_fetch(SD35M)
    assert plan["snapshot"] is True and plan["files"] == [] and plan["nothing"] is False
    assert plan["ignore_patterns"] == weights.DIFFUSERS_IGNORE
    assert weights.pin_for(SD35M, ["model_index.json"], whole=True) is None
    with pytest.raises(weights.CacheError):
        weights.repair_ref(SD35M)                                               # nothing complete to point at
    (r / "snapshots" / "c1" / "transformer").mkdir()
    (r / "snapshots" / "c1" / "transformer" / "diffusion_pytorch_model.safetensors").write_bytes(b"w" * 32)
    inv = weights.inventory()
    assert next(x for x in inv["repos"] if x["repo_id"] == SD35M)["health"] == "ok"
    plan = weights.plan_fetch(SD35M)
    assert plan["nothing"] is True and plan["snapshot"] is False
    assert weights.plan_fetch(SD35M, force=True)["snapshot"] is True
    assert weights.pins()[SD35M] == "c1"
    assert weights.plan_fetch(DEV, files=["a.bin"])["snapshot"] is False       # named files are never a snapshot


# --- the inventory by model ----------------------------------------------------------------------

def test_inventory_by_model_groups_repos_with_roles_and_worst_health(hub, monkeypatch):
    monkeypatch.setattr(components, "_needs_fp8_workaround", lambda: True)
    _repo(hub, KLEIN4, {"k": ["flux-2-klein-4b.safetensors"]}, main="k")
    _repo(hub, DEV, {"d": ["split_files/vae/flux2-vae.safetensors"]}, main="d")   # Qwen/Qwen3-4B absent
    _repo(hub, "Qwen/Qwen3-8B-FP8", {"q": ["config.json", "model.safetensors"]}, main="q")   # the other platform's twin
    inv = weights.inventory(presets={"clean": "zimage", "upscale": "sd35"})
    m = next(x for x in inv["models"] if x["tag"] == "catalog:flux2/flux.2-klein-4b")
    assert m["kind"] == "model" and m["label"] == "FLUX.2 · flux.2-klein-4b" and m["where"] == ["Cast", "Expand"]
    roles = [(r["role"], r["repo_id"], r["health"]) for r in m["repos"]]
    assert roles[0] == ("model", KLEIN4, "ok")
    assert ("text encoder", "Qwen/Qwen3-4B", "missing") in roles and ("vae", DEV, "partial") in roles
    assert m["health"] == "missing"                                              # the worst of its repos
    dev = next(x for x in inv["repos"] if x["repo_id"] == DEV)
    vae_use = next(u for u in dev["uses"] if u["tag"] == "catalog:flux2/flux.2-klein-4b")
    assert vae_use == {"tag": "catalog:flux2/flux.2-klein-4b", "label": "FLUX.2 · flux.2-klein-4b", "role": "vae", "where": ["Cast", "Expand"]}
    kinds = [x["kind"] for x in inv["models"]]
    assert kinds == sorted(kinds, key=["model", "preset", "tool", "train", "manifest"].index)   # catalog models first
    fast = next(x for x in inv["models"] if x["tag"] == "multi:fast")
    assert fast["kind"] == "preset" and {r["repo_id"] for r in fast["repos"]} >= {KLEIN4, "Qwen/Qwen3-4B", DEV}
    twin = next(x for x in inv["repos"] if x["repo_id"] == "Qwen/Qwen3-8B-FP8")
    assert twin["health"] == "unused" and "twin of Qwen/Qwen3-8B" in twin["detail"]
    assert inv["token"] == {"set": False, "source": None, "masked": None, "managed": True}


# --- the token -----------------------------------------------------------------------------------

def test_token_precedence_setting_env_and_dotenv(hub, monkeypatch):
    from orchestrator import config as config_mod
    assert weights.token_source() is None and weights.hf_token() is None
    assert "HF_TOKEN" not in weights.worker_env()
    info = weights.set_token(TOK)
    assert info == {"set": True, "source": "setting", "masked": "hf_…wxyz", "managed": True}
    assert weights.hf_token() == TOK and weights.worker_env()["HF_TOKEN"] == TOK
    assert weights.app_settings()["hf_token"] == TOK
    for bad in ("hf_ short", "short"):
        with pytest.raises(weights.CacheError) as e:
            weights.set_token(bad)
        assert e.value.status == 400
    assert weights.set_token(None)["set"] is False and "hf_token" not in weights.app_settings()
    monkeypatch.setitem(config_mod._FILE_ENV, "HF_TOKEN", "hf_fromdotenvfile0000")
    assert weights.token_source() == "dotenv" and weights.token_info()["managed"] is False
    with pytest.raises(weights.CacheError) as e:
        weights.set_token(TOK)
    assert e.value.status == 409 and ".env.local" in str(e.value)
    monkeypatch.setenv("HF_TOKEN", "hf_fromdotenvfile0000")                   # the startup export of .env.local
    assert weights.token_source() == "dotenv"
    monkeypatch.setenv("HF_TOKEN", "hf_fromtherealenv000")
    assert weights.token_source() == "env" and weights.hf_token() == "hf_fromtherealenv000"
    assert weights.worker_env()["HF_TOKEN"] == "hf_fromtherealenv000"
    with pytest.raises(weights.CacheError):
        weights.set_token(None)                                                 # clearing would not take effect either


def test_check_token_never_echoes_it(monkeypatch):
    class Api:
        def __init__(self, token):
            self.token = token

        def whoami(self):
            assert self.token == TOK
            return {"name": "stubz", "type": "user", "orgs": [{"name": "loom"}]}

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "HfApi", Api)
    r = weights.check_token(TOK)
    assert r == {"ok": True, "user": "stubz", "type": "user", "orgs": ["loom"]} and TOK not in json.dumps(r)
    assert weights.check_token("   ") == {"ok": False, "error": "no token to check"} or weights.check_token("   ")["ok"] is False


# --- the workers ---------------------------------------------------------------------------------

def test_worker_env_is_online_only_for_the_cache_worker(hub):
    assert weights.worker_env()["HF_HUB_OFFLINE"] == "1"
    assert "HF_HUB_OFFLINE" not in weights.worker_env(online=True)
    src = (ROOT / "orchestrator" / "runner.py").read_text(encoding="utf-8")
    assert 'weights.worker_env(online=(pipeline == "hf_cache"))' in src


def test_fetch_worker_lifts_offline_before_the_hub_library_reads_it(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    for name in [k for k in sys.modules if k == "huggingface_hub" or k.startswith("huggingface_hub.")]:
        monkeypatch.delitem(sys.modules, name)
    src = WORKER.read_text(encoding="utf-8")
    body = src[src.index("def task_fetch"):]
    assert body.index("go_online()") < body.index("from huggingface_hub import hf_hub_download")
    mod = _worker()
    mod.go_online()
    import huggingface_hub.constants as c                                       # a fresh import reads the lifted flag
    assert c.HF_HUB_OFFLINE is False and os.environ["HF_HUB_OFFLINE"] == "0"


def test_worker_snapshot_fetch_passes_the_ignore_patterns(monkeypatch, tmp_path, capsys):
    calls: dict = {}

    class Stub:
        @staticmethod
        def snapshot_download(**kw):
            calls.update(kw)
            return str(tmp_path / "snap")

        @staticmethod
        def hf_hub_download(**kw):
            raise AssertionError("a snapshot fetch never downloads per file")

    for k in weights.TOKEN_KEYS:
        monkeypatch.delenv(k, raising=False)
    mod = _worker()
    monkeypatch.setattr(mod, "go_online", lambda: None)                        # keep the stub in sys.modules
    monkeypatch.setattr(mod, "_plan_bytes", lambda *a, **k: (None, None))
    monkeypatch.setattr(mod, "_meter_class", lambda: "meter")
    monkeypatch.setitem(sys.modules, "huggingface_hub", Stub)
    out = mod.task_fetch({"repo_id": "a/b", "files": [], "ignore_patterns": ["*.h5"], "cache_home": str(tmp_path)})
    assert out["ok"] is True and out["snapshot"] == str(tmp_path / "snap") and out["ignore_patterns"] == ["*.h5"]
    assert calls == {"repo_id": "a/b", "revision": None, "cache_dir": str(tmp_path / "hub"), "token": None, "ignore_patterns": ["*.h5"], "tqdm_class": "meter"}
    assert "[cache] note snapshot of a/b" in capsys.readouterr().out


# --- the endpoints -------------------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path, hub):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def test_token_endpoints_and_the_inventory_never_leak_the_token(client, monkeypatch):
    r = client.put("/cache/token", json={"token": TOK})
    assert r.status_code == 200 and r.json() == {"set": True, "source": "setting", "masked": "hf_…wxyz", "managed": True}
    inv = client.get("/cache")
    assert TOK not in inv.text and inv.json()["token"]["masked"] == "hf_…wxyz"
    assert client.put("/cache/token", json={"token": "nope"}).status_code == 400
    monkeypatch.setattr(weights, "check_token", lambda token=None: {"ok": True, "user": "stubz", "checked": token})
    r = client.post("/cache/token/check", json={})
    assert r.status_code == 200 and r.json()["user"] == "stubz" and r.json()["checked"] is None
    assert client.post("/cache/token/check", json={"token": "hf_candidate000000000"}).json()["checked"] == "hf_candidate000000000"
    assert client.put("/cache/token", json={"token": None}).json()["set"] is False
    assert client.put("/cache/token", json={"token": TOK}, headers={"X-Loom-Token": "wrong"}).status_code == 401


def test_fetch_endpoint_snapshots_a_whole_repo_and_gates_on_the_token(client, hub):
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    _repo(hub, SD35M, {"c1": ["model_index.json"]}, main="c1")
    r = client.post("/cache/fetch", json={"repo_id": SD35M})
    assert r.status_code == 200, r.text
    job = RUNNER.jobs[r.json()["job_id"]]
    assert r.json()["snapshot"] is True and job["params"]["files"] == [] and job["params"]["ignore_patterns"] == weights.DIFFUSERS_IGNORE
    r = client.post("/cache/fetch", json={"repo_id": KLEIN4})                    # gated, no token anywhere
    assert r.status_code == 412 and "Models page" in r.json()["detail"]["hint"]
    assert client.put("/cache/token", json={"token": TOK}).status_code == 200
    r = client.post("/cache/fetch", json={"repo_id": KLEIN4})
    assert r.status_code == 200 and RUNNER.jobs[r.json()["job_id"]]["params"]["files"] == ["flux-2-klein-4b.safetensors"]
    assert r.json()["snapshot"] is False


# --- the fetch meter -----------------------------------------------------------------------------

def _lines(out: str, kind: str) -> list[str]:
    return [l[len(f"[cache] {kind} "):] for l in out.splitlines() if l.startswith(f"[cache] {kind} ")]


def test_meter_turns_the_hub_counters_into_bytes_progress_and_a_note(capsys):
    mod = _worker()
    mod.REPORT.min_interval = 0.0
    Meter = mod._meter_class()                                                  # a real hub-tqdm subclass, no terminal
    mod.REPORT.reset(300_000_000, 2)                                            # sized up front: 300 MB in 2 files
    a = Meter(total=100_000_000, initial=0, unit="B", unit_scale=True, desc="a.safetensors")
    a.update(100_000_000)
    a.close()
    b = Meter(total=200_000_000, initial=0, unit="B", unit_scale=True, desc="b.safetensors")
    b.update(50_000_000)
    b.refresh()
    out = capsys.readouterr().out
    prog = _lines(out, "progress")
    assert prog and abs(float(prog[-1]) - 0.5) < 1e-3                          # 150 of 300 MB
    note = _lines(out, "note")[-1]
    assert note.startswith("2 of 2 files · 150 of 300 MB") and "b.safetensors" in note
    b.update(150_000_000)
    b.close()
    out = capsys.readouterr().out
    assert abs(float(_lines(out, "progress")[-1]) - 0.999) < 1e-3              # never 1 before the task says so
    # unknown up front: the bars' own totals are the denominator, and GB reads with decimals
    mod.REPORT.reset(None, None)
    c = Meter(total=4_000_000_000, initial=1_000_000_000, unit="B", unit_scale=True, desc="big.safetensors")   # resume found 1 GB
    c.refresh()
    out = capsys.readouterr().out
    assert abs(float(_lines(out, "progress")[-1]) - 0.25) < 1e-3
    assert _lines(out, "note")[-1].startswith("file 1 · 1.00 of 4.00 GB")
    c.close()
    assert mod._eta(600e6, 10e6) == "~1 min left" and mod._eta(7200e6, 1e6) == "~2 h 00 min left" and mod._eta(1, 0) == ""
    assert mod._rate(2.5e6) == "2.5 MB/s" and mod._rate(45e6) == "45 MB/s"


def test_plan_bytes_sizes_the_fetch_from_the_hub_minus_the_cached(monkeypatch):
    mod = _worker()

    class Sib:
        def __init__(self, name, size):
            self.rfilename, self.size = name, size

    class Info:
        sha = "abc"
        siblings = [Sib("model_index.json", 500), Sib("transformer/model.safetensors", 4_000_000_000),
                    Sib("vae/model.safetensors", 300_000_000), Sib("flax.msgpack", 4_000_000_000)]

    class Api:
        def __init__(self, token=None):
            self.token = token

        def model_info(self, repo, revision=None, files_metadata=False):
            assert files_metadata is True and repo == "a/b"
            return Info()

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "HfApi", Api)
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache",
                        lambda repo, f, cache_dir=None, revision=None: "/cached" if f == "vae/model.safetensors" else None)
    total, n = mod._plan_bytes("a/b", [], None, "/hub", None, ["*.msgpack"])
    assert (total, n) == (4_000_000_500, 2)                                    # the vae is cached, the flax file ignored
    total, n = mod._plan_bytes("a/b", ["vae/model.safetensors", "model_index.json"], None, "/hub", None, None)
    assert (total, n) == (500, 1)
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda token=None: (_ for _ in ()).throw(RuntimeError("offline")))
    assert mod._plan_bytes("a/b", [], None, "/hub", None, None) == (None, None)


def test_verify_reports_progress_by_bytes_hashed(tmp_path, capsys):
    mod = _worker()
    hub = tmp_path / "home" / "hub"
    snap = hub / "models--a--b" / "snapshots" / "c1"
    snap.mkdir(parents=True)
    import hashlib
    for data in (b"a" * 3_000_000, b"b" * 1_000_000):                           # blobs are named by their sha256: hashed
        (snap / hashlib.sha256(data).hexdigest()).write_bytes(data)
    r = mod.task_verify({"repo_id": "a/b", "cache_home": str(tmp_path / "home")})
    assert r["ok"] is True and r["checked"] == 2
    out = capsys.readouterr().out
    prog = [float(x) for x in _lines(out, "progress")]
    assert prog[0] == 0.0 and abs(prog[-2] - 0.75) < 1e-3 and prog[-1] == 1.0   # the second file starts at 3 of 4 MB
    assert "3 of 4 MB" in _lines(out, "note")[-1]
