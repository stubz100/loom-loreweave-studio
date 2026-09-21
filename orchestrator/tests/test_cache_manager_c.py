"""M2.17 step c — the cache location as a loom setting: precedence (env > dotenv > setting >
HF_HOME > default), a validated change, the move job that switches on completion, the previous
tree kept until deleted. Fake homes in tmp; the repo's own .env value is neutralised."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator import config as config_mod
from orchestrator import weights
from orchestrator.config import CONFIG

DEV = "Comfy-Org/flux2-dev"
JUNE = "03d6521e6f6a47396b3f951cbea50f7e6c2f482e"


def _repo(hub: Path, repo_id: str, files: list[str], commit: str = JUNE) -> Path:
    rdir = hub / f"models--{repo_id.replace('/', '--')}"
    (rdir / "blobs").mkdir(parents=True)
    snap = rdir / "snapshots" / commit
    for f in files:
        (snap / f).parent.mkdir(parents=True, exist_ok=True)
        (snap / f).write_bytes(b"x" * 32)
    (rdir / "refs").mkdir()
    (rdir / "refs" / "main").write_text(commit, encoding="utf-8")
    return rdir


@pytest.fixture()
def homes(tmp_path, monkeypatch):
    """A cache home under HF_HOME with one repo; no env/dotenv source in force."""
    home = tmp_path / "home_a"
    (home / "hub").mkdir(parents=True)
    _repo(home / "hub", DEV, ["split_files/vae/flux2-vae.safetensors"])
    monkeypatch.setenv("HF_HOME", str(home))
    monkeypatch.setenv("LOOM_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("LOOM_MODELS_DIR", raising=False)
    monkeypatch.delitem(config_mod._FILE_ENV, "LOOM_MODELS_DIR", raising=False)
    return tmp_path, home


def test_precedence_env_dotenv_setting_hf_home_default(homes, monkeypatch):
    tmp, home = homes
    assert weights.location_source() == "hf_home" and weights.effective_home() == home
    weights.set_app_setting("models_dir", str(tmp / "home_b"))
    assert weights.location_source() == "setting" and weights.effective_home() == (tmp / "home_b").resolve()
    monkeypatch.setitem(config_mod._FILE_ENV, "LOOM_MODELS_DIR", str(tmp / "home_dotenv"))
    assert weights.location_source() == "dotenv" and weights.effective_home() == Path(CONFIG.hf_home)
    monkeypatch.setenv("LOOM_MODELS_DIR", str(tmp / "home_env"))
    assert weights.location_source() == "env" and weights.effective_home() == (tmp / "home_env").resolve()
    monkeypatch.delenv("LOOM_MODELS_DIR")
    monkeypatch.delitem(config_mod._FILE_ENV, "LOOM_MODELS_DIR")
    weights.set_app_setting("models_dir", None)
    monkeypatch.delenv("HF_HOME")
    assert weights.location_source() == "default"


def test_set_location_validates_and_records_the_previous(homes):
    tmp, home = homes
    with pytest.raises(weights.CacheError) as e:
        weights.set_location("relative/path")
    assert e.value.status == 400
    r = weights.set_location(str(tmp / "home_b"))
    assert r["source"] == "setting" and (tmp / "home_b" / "hub").is_dir()
    s = weights.app_settings()
    assert s["models_dir"] == str(tmp / "home_b") and s["previous_models_dir"] == str(home)
    inv = weights.inventory()
    assert inv["location"]["path"] == str((tmp / "home_b").resolve()) and inv["location"]["source"] == "setting"
    assert inv["location"]["managed"] is True and inv["location"]["previous"]["path"] == str(home)
    assert inv["location"]["previous"]["exists"] is True
    assert weights.worker_env()["HF_HOME"] == str((tmp / "home_b").resolve())      # the next job reads the new home


def test_set_location_refuses_while_an_env_source_wins(homes, monkeypatch):
    tmp, home = homes
    monkeypatch.setitem(config_mod._FILE_ENV, "LOOM_MODELS_DIR", str(home))
    with pytest.raises(weights.CacheError) as e:
        weights.set_location(str(tmp / "home_b"))
    assert e.value.status == 409 and ".env" in str(e.value)
    assert weights.inventory()["location"]["managed"] is False


def test_plan_move_checks_the_destination_and_the_space(homes, monkeypatch):
    tmp, home = homes
    with pytest.raises(weights.CacheError) as e:
        weights.plan_move(str(home))
    assert e.value.status == 409
    with pytest.raises(weights.CacheError):
        weights.plan_move(str(home / "inside"))
    plan = weights.plan_move(str(tmp / "home_b"))
    assert plan["from"] == str(home) and plan["to"] == str(tmp / "home_b") and plan["size_gb"] >= 0
    du = shutil.disk_usage(tmp)
    monkeypatch.setattr(weights.shutil, "disk_usage", lambda p: du._replace(free=1))
    with pytest.raises(weights.CacheError) as e:
        weights.plan_move(str(tmp / "home_c"))
    assert e.value.status == 409 and "free space" in str(e.value)


def test_finish_move_switches_only_on_a_done_move_and_previous_can_be_deleted(homes):
    tmp, home = homes
    to = tmp / "home_b"
    job = {"pipeline": "hf_cache", "mode": "move", "params": {"to": str(to), "cache_home": str(home)}, "result": {"ok": False}}
    assert weights.finish_move(job) is False and "models_dir" not in weights.app_settings()
    job["result"] = {"ok": True}
    (to / "hub").mkdir(parents=True)
    assert weights.finish_move(job) is True
    assert weights.effective_home() == to.resolve() and weights.app_settings()["previous_models_dir"] == str(home)
    assert weights.finish_move({"pipeline": "zimage", "mode": "t2i", "result": {"ok": True}, "params": {}}) is False
    r = weights.delete_previous()
    assert r["deleted"] == str(home / "hub") and not (home / "hub").exists()
    assert "previous_models_dir" not in weights.app_settings()
    with pytest.raises(weights.CacheError) as e:
        weights.delete_previous()
    assert e.value.status == 404


@pytest.fixture()
def client(monkeypatch, tmp_path, homes):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def test_location_and_move_endpoints(client, homes):
    from orchestrator.main import _on_job_complete
    from orchestrator.runner import RUNNER
    tmp, home = homes
    RUNNER.pause()
    assert client.get("/version").json()["hf_home_source"] == "hf_home"
    assert client.put("/cache/location", json={"path": "nope"}).status_code == 400
    r = client.post("/cache/move", json={"to": str(tmp / "home_b")})
    assert r.status_code == 200, r.text
    job = RUNNER.jobs[r.json()["job_id"]]
    assert job["pipeline"] == "hf_cache" and job["mode"] == "move"
    assert job["params"]["to"] == str(tmp / "home_b") and job["params"]["cache_home"] == str(home)
    assert r.json()["plan"]["from"] == str(home)
    # the observer switches the setting when the move completes
    (tmp / "home_b" / "hub").mkdir(parents=True)
    job["result"] = {"ok": True}
    _on_job_complete(dict(job))
    v = client.get("/version").json()
    assert v["hf_home"] == str((tmp / "home_b").resolve()) and v["hf_home_source"] == "setting"
    loc = client.get("/cache").json()["location"]
    assert loc["previous"]["path"] == str(home)
    assert client.post("/cache/move", json={"to": str(tmp / "home_b")}).status_code == 409   # the current one now
    r = client.put("/cache/location", json={"path": str(tmp / "home_c")})
    assert r.status_code == 200 and r.json()["source"] == "setting"
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "x"}, batch_id="b", index=0, batch_size=1, requester_id="sandbox")
    RUNNER.jobs[jid]["status"] = "running"
    try:
        assert client.delete("/cache/previous").status_code == 409
        assert client.put("/cache/location", json={"path": str(tmp / "home_d")}).status_code == 409
    finally:
        RUNNER.jobs[jid]["status"] = "canceled"
    r = client.delete("/cache/previous")
    assert r.status_code == 200 and not (tmp / "home_b" / "hub").exists()     # home_b became the previous when home_c was set
    assert client.delete("/cache/previous").status_code == 404
