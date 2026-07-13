"""P2/M5 — train options: per-family presets, the sd35 spike front-gate, the backend
roster, and R68 seed-from-parent (no GPU).

The spec's M5 doctrine (added 2026-07-12): nothing builds ON TOP of the unproven sd35
trainer — staging refuses sd35 until `LOOM_TRAINER_SD35_GO` stamps the rig spike, and
diffusers-PEFT stays a DECLARED backend (R115) until the spike decides its role.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.config import CONFIG


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1,P2")
    monkeypatch.delenv("LOOM_TRAINER_SD35_GO", raising=False)
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


_CELL = {"shot_size": "portrait", "angle": "front", "expression": "neutral", "background": ""}


def _curated_asset(client, *, n=2):
    from orchestrator.runner import RUNNER

    ws = RUNNER.workspace
    a = client.post("/assets", json={"name": "Mara"}).json()["profile"]
    version_id = a["active_version"]
    RUNNER.pause()
    out_dir = ws.out_dir / "job_m5refs"
    out_dir.mkdir(parents=True, exist_ok=True)
    names, meta = [], {}
    for i in range(n):
        name = f"job_m5refs/ref{i}.png"
        (out_dir / f"ref{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n m5")
        names.append(name)
        meta[name] = {"coverage_cell": {**_CELL, "background": f"room {i}"}, "seed": 100 + i}
    jid = RUNNER.submit(
        pipeline="zimage", mode="img2img",
        params={"prompt": "dataset", "batch_items": [{}] * n},
        batch_id="bat_m5refs", index=0, batch_size=1,
        requester_id=version_id, profile_version_id=version_id, stage="B",
    )
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {
        "ok": True, "output_name": names[0], "output_names": names, "output_meta": meta,
    }
    for name in names:
        r = client.post(f"/assets/{a['id']}/refs/keep", json={"job_id": jid, "output": name})
        assert r.status_code == 200, r.text
    return a


def test_presets_roster_gates_sd35_until_the_spike(client, monkeypatch):
    r = client.get("/training/presets")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "DECLARED" in body["backends"]["peft"]
    assert "default" in body["backends"]["ai_toolkit"]
    by_fam = {p["base_family"]: p for p in body["presets"]}
    zi, sd = by_fam["zimage"], by_fam["sd35"]
    assert zi["enabled"] is True and zi["status"] == "validated"
    assert zi["vram_fit"]["resolution_max"] == 768
    assert sd["enabled"] is False and sd["status"] == "spike_pending"
    assert sd["gate_env"] == "LOOM_TRAINER_SD35_GO"
    assert sd["settings"]["arch"] == "sd3"
    assert sd["settings"]["base_model"].endswith("stable-diffusion-3.5-medium")

    monkeypatch.setenv("LOOM_TRAINER_SD35_GO", "1")
    sd2 = {p["base_family"]: p for p in
           client.get("/training/presets").json()["presets"]}["sd35"]
    assert sd2["enabled"] is True


def test_sd35_staging_refused_until_gate_then_writes_sd3_config(client, monkeypatch):
    asset = _curated_asset(client)
    r = client.post(f"/assets/{asset['id']}/lora/stage", json={"base_family": "sd35"})
    assert r.status_code == 400 and "front-gate" in r.text

    monkeypatch.setenv("LOOM_TRAINER_SD35_GO", "1")
    r = client.post(f"/assets/{asset['id']}/lora/stage",
                    json={"base_family": "sd35", "trigger_token": "mara_lw"})
    assert r.status_code == 200, r.text
    staged = r.json()
    assert staged["base_family"] == "sd35"
    assert staged["kind"] == "sd35_lora_train"
    assert staged["queue_job"]["params"]["base_family"] == "sd35"
    cfg = Path(staged["config_path"]).read_text(encoding="utf-8")
    assert "arch: sd3" in cfg
    assert "stabilityai/stable-diffusion-3.5-medium" in cfg
    assert staged["config_path"].endswith("train.yaml")
    assert "_sd35" in staged["queue_job"]["params"]["artifact_name"]


def test_peft_backend_is_declared_but_refused(client):
    asset = _curated_asset(client)
    r = client.post(f"/assets/{asset['id']}/lora/stage", json={"backend": "peft"})
    assert r.status_code == 400 and "R115" in r.text
    # unknown values are schema-refused before the module sees them
    assert client.post(f"/assets/{asset['id']}/lora/stage",
                       json={"backend": "banana"}).status_code == 422
    assert client.post(f"/assets/{asset['id']}/lora/stage",
                       json={"train_init": "warp"}).status_code == 422


def test_seed_from_parent_places_the_step0_checkpoint(client):
    """R68: duplicating v1 → v2 and staging v2 with seed_parent pre-places v1's promoted
    LoRA as the step-0 checkpoint in the run dir (ai-toolkit's own discovery picks it
    up); the staged record carries full seed provenance."""
    from orchestrator.runner import RUNNER

    asset = _curated_asset(client)
    detail = client.get(f"/assets/{asset['id']}").json()
    v1 = detail["versions"][0]
    # craft v1's promoted LoRA (promote itself is M6)
    ws = RUNNER.workspace
    v1_dir = (ws.asset_dir("characters", detail["profile"]["slug"]) / "versions" / "v1_base")
    (v1_dir / "lora").mkdir(parents=True, exist_ok=True)
    seed_bytes = b"seed-lora-weights"
    (v1_dir / "lora" / "loom_mara_v1_base_zimage.safetensors").write_bytes(seed_bytes)

    v2 = client.post(f"/assets/{asset['id']}/versions",
                     json={"name": "v2 seeded", "parent_version_id": v1["id"]}).json()
    v2_id = v2.get("id") or (v2.get("version") or {}).get("id")
    assert v2_id, v2

    r = client.post(f"/assets/{asset['id']}/lora/stage",
                    json={"version_id": v2_id, "train_init": "seed_parent"})
    assert r.status_code == 200, r.text
    staged = r.json()
    assert staged["train_init"] == "seed_parent"
    seed = staged["seed_artifact"]
    ckpt = Path(seed["checkpoint"])
    assert ckpt.is_file() and ckpt.read_bytes() == seed_bytes
    assert ckpt.name.endswith("_000000000.safetensors")
    assert ckpt.parent.name == ckpt.name.removesuffix("_000000000.safetensors")
    assert Path(seed["source"]).name == "loom_mara_v1_base_zimage.safetensors"
    assert staged["queue_job"]["params"]["seed_artifact"]["sha256"] == seed["sha256"]

    # the from_base default stays clean — no checkpoint is pre-placed
    base = client.post(f"/assets/{asset['id']}/lora/stage",
                       json={"version_id": v2_id}).json()
    assert base["train_init"] == "from_base" and base["seed_artifact"] is None


def test_seed_from_parent_refusals(client):
    """No parent (v1_base) → 400; a parent that was never promoted → 400 naming M6."""
    asset = _curated_asset(client)
    r = client.post(f"/assets/{asset['id']}/lora/stage", json={"train_init": "seed_parent"})
    assert r.status_code == 400 and "derived_from" in r.text

    detail = client.get(f"/assets/{asset['id']}").json()
    v2 = client.post(f"/assets/{asset['id']}/versions",
                     json={"name": "v2", "parent_version_id": detail["versions"][0]["id"]}).json()
    v2_id = v2.get("id") or (v2.get("version") or {}).get("id")
    r = client.post(f"/assets/{asset['id']}/lora/stage",
                    json={"version_id": v2_id, "train_init": "seed_parent"})
    assert r.status_code == 400 and "no promoted LoRA" in r.text


def test_legacy_zimage_route_still_stages(client):
    """The M2 route name keeps working (alias) and defaults to zimage/ai_toolkit."""
    asset = _curated_asset(client)
    r = client.post(f"/assets/{asset['id']}/lora/zimage/stage", json={"steps": 60})
    assert r.status_code == 200, r.text
    staged = r.json()
    assert staged["base_family"] == "zimage" and staged["backend"] == "ai_toolkit"
    assert staged["kind"] == "zimage_lora_train"
    assert json.loads(json.dumps(staged["settings"]))["steps"] == 60
