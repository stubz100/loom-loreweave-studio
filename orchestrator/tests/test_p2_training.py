"""P2/M2 — staged Z-Image LoRA trainer skeleton (no GPU).

Locks the expensive-operation contract before the UI lands:

- template captions/context/dataset/config are materialized from curated refs;
- staged jobs persist in `jobs/staged.json`, not `queue.json`;
- staged → queued is explicit and marks the queue job resumable;
- the trainer adapter/manifest wrapper has a no-GPU contract.
"""

from __future__ import annotations

import importlib.util
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


def _curated_asset(client, *, n=3):
    from orchestrator.runner import RUNNER

    ws = RUNNER.workspace
    a = client.post("/assets", json={"name": "Mara"}).json()["profile"]
    version_id = a["active_version"]
    RUNNER.pause()
    out_dir = ws.out_dir / "job_p2refs"
    out_dir.mkdir(parents=True, exist_ok=True)
    names, meta = [], {}
    for i in range(n):
        name = f"job_p2refs/ref{i}.png"
        (out_dir / f"ref{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n p2")
        names.append(name)
        meta[name] = {"coverage_cell": {**_CELL, "background": f"room {i}"}, "seed": 100 + i}
    jid = RUNNER.submit(
        pipeline="zimage", mode="img2img",
        params={"prompt": "dataset", "batch_items": [{}] * n},
        batch_id="bat_p2refs", index=0, batch_size=1,
        requester_id=version_id, profile_version_id=version_id, stage="B",
    )
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {
        "ok": True,
        "output_name": names[0],
        "output_names": names,
        "output_meta": meta,
    }
    for name in names:
        r = client.post(f"/assets/{a['id']}/refs/keep", json={"job_id": jid, "output": name})
        assert r.status_code == 200, r.text
    return a


def test_stage_zimage_lora_writes_captions_context_and_staged_record(client):
    from orchestrator.runner import RUNNER

    asset = _curated_asset(client)
    r = client.post(
        f"/assets/{asset['id']}/lora/zimage/stage",
        json={"trigger_token": "mara_lw", "steps": 500, "resolution": 512},
    )
    assert r.status_code == 200, r.text
    staged = r.json()
    assert staged["kind"] == "zimage_lora_train"
    assert staged["status"] == "staged"
    assert staged["caption_count"] == 3
    assert staged["queue_job"]["pipeline"] == "zimage_trainer"
    assert staged["queue_job"]["resumable"] is True
    assert staged["queue_job"]["params"]["runtime_contract"]["do_not_mutate_shared_inference_venv"] is True

    ws = RUNNER.workspace
    assert (ws.jobs_dir / "staged.json").is_file()
    assert not any(j.get("pipeline") == "zimage_trainer" for j in RUNNER.jobs.values())

    detail = client.get(f"/assets/{asset['id']}").json()
    version = detail["versions"][0]
    vroot = ws.asset_dir("characters", detail["profile"]["slug"]) / "versions" / "v1_base"
    captions = (vroot / "captions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(captions) == 3
    assert json.loads(captions[0])["caption"].startswith("mara_lw, front view, portrait")
    assert (vroot / "caption_policy.json").is_file()
    assert (vroot / "training_context.json").is_file()
    assert version["caption_status"]["status"] == "ready"
    assert version["trigger_token"] == "mara_lw"

    dataset_manifest = json.loads(Path(staged["dataset_manifest"]).read_text(encoding="utf-8"))
    assert dataset_manifest["count"] == 3
    assert Path(staged["config_path"]).is_file()


def test_queue_staged_training_moves_to_resumable_queue_job(client):
    from orchestrator.runner import RUNNER

    asset = _curated_asset(client, n=1)
    staged = client.post(f"/assets/{asset['id']}/lora/zimage/stage", json={}).json()
    q = client.post(f"/training/staged/{staged['id']}/queue")
    assert q.status_code == 200, q.text
    job = RUNNER.get(q.json()["job_id"])
    assert job["pipeline"] == "zimage_trainer"
    assert job["mode"] == "lora"
    assert job["stage"] == "D"
    assert job["resumable"] is True
    assert job["params"]["resume_strategy"] == "ai_toolkit_checkpoint_discovery"
    assert client.get("/training/staged").json()["count"] == 0


def test_zimage_trainer_adapter_builds_and_parses_manifest(tmp_path):
    from orchestrator.adapters import zimage_trainer
    from orchestrator.adapters.base import JobSpec

    config = tmp_path / "train.yaml"
    config.write_text("job: extension\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    out_dir = tmp_path / "out"
    spec = JobSpec(
        pipeline="zimage_trainer",
        mode="lora",
        params={"config_path": str(config), "run_dir": str(run_dir), "artifact_name": "x.safetensors"},
        output_dir=out_dir,
    )
    argv = zimage_trainer.build_argv(spec, "python", Path("trainers/loom_zimage_lora.py"))
    assert argv[:2] == ["python", "trainers\\loom_zimage_lora.py"] or argv[:2] == ["python", "trainers/loom_zimage_lora.py"]
    assert "--config" in argv and str(config) in argv

    artifact = run_dir / "x.safetensors"
    artifact.write_bytes(b"lora")
    out_dir.mkdir()
    manifest = out_dir / "zimage_lora_train_manifest.json"
    manifest.write_text(json.dumps({
        "status": "completed",
        "duration_s": 12.5,
        "artifact": {"path": str(artifact), "sha256": "X"},
    }), encoding="utf-8")
    rec = zimage_trainer.parse_result(0, "[train-done]", "", out_dir)
    assert rec.ok is True
    assert rec.manifest_status == "completed"
    assert rec.outputs == [str(artifact)]


def test_trainer_wrapper_discovers_resume_state(tmp_path):
    worker = Path(__file__).resolve().parents[2] / "trainers" / "loom_zimage_lora.py"
    spec = importlib.util.spec_from_file_location("loom_zimage_lora", worker)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    run_dir = tmp_path / "run" / "loom_char"
    run_dir.mkdir(parents=True)
    artifact = run_dir / "loom_char.safetensors"
    artifact.write_bytes(b"adapter")
    opt = run_dir / "optimizer.pt"
    opt.write_bytes(b"optim")
    state = module.discover_resume_state(tmp_path / "run", "loom_char.safetensors")
    assert state["latest_artifact"]["sha256"]
    assert state["final_adapters"][0]["relative_path"] == "loom_char/loom_char.safetensors"
    assert state["optimizer_pt"]["relative_path"] == "loom_char/optimizer.pt"
