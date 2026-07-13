"""P2/M6 — promote (Stage E) + R13 manual cleanup + P2-11 preview (no GPU).

Locks the promote contract: a DONE trainer run's adapter is COPIED into the version's
`lora/` with a `lora.manifest.json` carrying the P2-13 graph-ready facts (caption policy
hash, captions hash, context digest, dataset hash), `version.lora` flips on, temp stays
until the explicit cleanup click, and the preview queues a sample gen with the fresh
un-promoted adapter.
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
    out_dir = ws.out_dir / "job_m6refs"
    out_dir.mkdir(parents=True, exist_ok=True)
    names, meta = [], {}
    for i in range(n):
        name = f"job_m6refs/ref{i}.png"
        (out_dir / f"ref{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n m6")
        names.append(name)
        meta[name] = {"coverage_cell": {**_CELL, "background": f"room {i}"}, "seed": 100 + i}
    jid = RUNNER.submit(
        pipeline="zimage", mode="img2img",
        params={"prompt": "dataset", "batch_items": [{}] * n},
        batch_id="bat_m6refs", index=0, batch_size=1,
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


def _finished_trainer_job(client, asset, *, trigger="mara_lw"):
    """Stage → queue → hand-finish the trainer job the way the wrapper would: the adapter
    lands in the run dir's <job_name>/ checkpoint layout + a trainer manifest in the
    job's out dir, and the parsed result points at both."""
    from orchestrator.runner import RUNNER

    staged = client.post(f"/assets/{asset['id']}/lora/stage",
                         json={"trigger_token": trigger, "steps": 60}).json()
    jid = client.post(f"/training/staged/{staged['id']}/queue").json()["job_id"]
    job = RUNNER.jobs[jid]
    params = job["params"]
    run_dir = Path(params["run_dir"])
    name = params["artifact_name"]
    ckpt_dir = run_dir / name.removesuffix(".safetensors")
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    artifact = ckpt_dir / name
    artifact.write_bytes(b"trained-lora-weights")

    ws = RUNNER.workspace
    out_dir = ws.out_dir / jid
    out_dir.mkdir(parents=True, exist_ok=True)
    tm = out_dir / "zimage_lora_train_manifest.json"
    tm.write_text(json.dumps({
        "status": "completed", "duration_s": 777.5,
        "artifact": {"path": str(artifact), "sha256": "X"},
    }), encoding="utf-8")
    job["status"] = "done"
    job["result"] = {"ok": True, "outputs": [str(artifact)], "manifest_path": str(tm)}
    return jid, staged


def _vroot(client, asset):
    from orchestrator.runner import RUNNER
    detail = client.get(f"/assets/{asset['id']}").json()
    return (RUNNER.workspace.asset_dir("characters", detail["profile"]["slug"])
            / "versions" / "v1_base")


def test_promote_copies_artifact_writes_manifest_and_flips_version_lora(client):
    asset = _curated_asset(client)
    jid, staged = _finished_trainer_job(client, asset)
    r = client.post(f"/training/jobs/{jid}/promote")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["promoted"] is True

    vroot = _vroot(client, asset)
    dst = vroot / "lora" / Path(res["artifact"]).name
    assert dst.is_file() and dst.read_bytes() == b"trained-lora-weights"

    manifest = json.loads((vroot / "lora" / "lora.manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "loom.p2.lora_manifest.v1"
    # P2-13: the graph-ready facts match THIS run's stage-time records
    assert manifest["caption_policy_hash"] == staged["caption_policy_hash"]
    assert manifest["captions_hash"] == staged["captions_hash"]
    assert manifest["context_digest"] == staged["context_digest"]
    assert manifest["dataset_hash"]                      # the copied dataset snapshot
    assert manifest["trigger_token"] == "mara_lw"
    assert manifest["trained_by_job"] == jid
    assert manifest["duration_s"] == 777.5
    assert manifest["trainer_manifest_status"] == "completed"
    assert manifest["expected_steps"] == 60
    assert manifest["replaces"] is None

    version = json.loads((vroot / "version.json").read_text(encoding="utf-8"))
    lora = version["lora"]
    assert lora["file"] == dst.name and lora["sha256"] == manifest["artifact"]["sha256"]
    assert lora["trigger_token"] == "mara_lw" and lora["job_id"] == jid
    # the asset detail (what the version selector reads) now shows LoRA presence
    detail = client.get(f"/assets/{asset['id']}").json()
    assert detail["versions"][0]["lora"]["file"] == dst.name
    # temp is NOT cleaned by promote (R13 — explicit click)
    assert Path(staged["run_dir"]).is_dir()


def test_repromote_overwrites_and_records_what_it_replaced(client):
    asset = _curated_asset(client)
    jid1, _ = _finished_trainer_job(client, asset)
    first = client.post(f"/training/jobs/{jid1}/promote").json()
    jid2, _ = _finished_trainer_job(client, asset)
    second = client.post(f"/training/jobs/{jid2}/promote").json()
    assert second["manifest"]["replaces"] == first["sha256"]
    vroot = _vroot(client, asset)
    version = json.loads((vroot / "version.json").read_text(encoding="utf-8"))
    assert version["lora"]["job_id"] == jid2


def test_promote_refusals(client):
    from orchestrator.runner import RUNNER

    asset = _curated_asset(client)
    assert client.post("/training/jobs/job_nope0000/promote").status_code == 404

    # not done yet → 409
    staged = client.post(f"/assets/{asset['id']}/lora/stage", json={}).json()
    jid = client.post(f"/training/staged/{staged['id']}/queue").json()["job_id"]
    assert client.post(f"/training/jobs/{jid}/promote").status_code == 409

    # a done NON-trainer job → 400
    gen = next(j for j in RUNNER.jobs.values() if j["pipeline"] == "zimage")
    assert client.post(f"/training/jobs/{gen['id']}/promote").status_code == 400

    # artifact vanished (temp cleaned first) → 400 names the cause
    done_jid, s2 = _finished_trainer_job(client, asset)
    import shutil as _sh
    _sh.rmtree(s2["run_dir"])
    RUNNER.jobs[done_jid]["result"]["outputs"] = []
    r = client.post(f"/training/jobs/{done_jid}/promote")
    assert r.status_code == 400 and "not found" in r.text

    # finalized version → 400
    fresh_jid, _ = _finished_trainer_job(client, asset)
    vid = client.get(f"/assets/{asset['id']}").json()["versions"][0]["id"]
    assert client.post(f"/assets/{asset['id']}/versions/{vid}/finalize").status_code == 200
    assert client.post(f"/training/jobs/{fresh_jid}/promote").status_code == 400


def test_cleanup_is_guarded_and_idempotent(client):
    from orchestrator.runner import RUNNER

    asset = _curated_asset(client)
    jid, staged = _finished_trainer_job(client, asset)
    run_dir = Path(staged["run_dir"])
    assert run_dir.is_dir()

    r = client.post(f"/training/jobs/{jid}/cleanup")
    assert r.status_code == 200 and r.json()["cleaned"] is True
    assert not run_dir.exists()
    assert client.post(f"/training/jobs/{jid}/cleanup").json()["cleaned"] is False

    # a queued run refuses cleanup (409); a foreign run_dir refuses deletion (400)
    s2 = client.post(f"/assets/{asset['id']}/lora/stage", json={}).json()
    j2 = client.post(f"/training/staged/{s2['id']}/queue").json()["job_id"]
    assert client.post(f"/training/jobs/{j2}/cleanup").status_code == 409
    RUNNER.jobs[j2]["status"] = "canceled"
    RUNNER.jobs[j2]["params"]["run_dir"] = str(RUNNER.workspace.path / "assets")
    r = client.post(f"/training/jobs/{j2}/cleanup")
    assert r.status_code == 400 and "outside the project temp" in r.text
    assert (RUNNER.workspace.path / "assets").is_dir()   # nothing was deleted


def test_preview_queues_a_sample_gen_with_the_fresh_adapter(client):
    """P2-11: the preview job loads the RUN-DIR artifact (not a promoted copy), derives
    the prompt from the trigger, scopes to the version's grid, and honors overrides."""
    from orchestrator.runner import RUNNER

    asset = _curated_asset(client)
    jid, staged = _finished_trainer_job(client, asset)
    r = client.post(f"/training/jobs/{jid}/preview", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    prev = RUNNER.get(body["job_id"])
    assert prev["pipeline"] == "zimage" and prev["mode"] == "t2i"
    p = prev["params"]
    assert p["prompt"].startswith("mara_lw, front view, portrait")
    assert Path(p["lora_path"]).is_file()
    assert str(Path(staged["run_dir"])) in p["lora_path"]     # fresh, un-promoted
    assert p["lora_weight"] == 1.0 and p["model_name"] == "zimage-base"
    assert prev["profile_version_id"] == staged["version_id"] and prev["stage"] == "D"

    custom = client.post(f"/training/jobs/{jid}/preview",
                         json={"prompt": "mara_lw riding a bicycle", "seed": 7}).json()
    p2 = RUNNER.get(custom["job_id"])["params"]
    assert p2["prompt"] == "mara_lw riding a bicycle" and p2["seed"] == 7

    # refusals: not-done 409 (fresh queued trainer), artifact gone 400
    s3 = client.post(f"/assets/{asset['id']}/lora/stage", json={}).json()
    j3 = client.post(f"/training/staged/{s3['id']}/queue").json()["job_id"]
    assert client.post(f"/training/jobs/{j3}/preview", json={}).status_code == 409
