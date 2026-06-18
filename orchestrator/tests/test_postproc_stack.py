"""M0c (P2) — per-image postprocess stack: persisted, independently-queued steps over a
selected base image (Clean/Refine/custom i2i presets + GFPGAN restore). No GPU — dry-run +
paused queue + a directly-invoked completion observer (the same pattern test_identity_anchor
uses to drive the anchor-verification observer).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orchestrator.config import CONFIG


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def _asset_with_base_image(name="PPAsset", base="job_base01/base.png"):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    RUNNER.pause()                                  # queued jobs must not actually run
    ws = RUNNER.workspace
    a = assets.create_asset(ws, name=name)["profile"]
    p = ws.out_dir / base
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    return a, base


def _complete(jid, output):
    """Drive a queued step's job to done + fire the completion observer (records output)."""
    from orchestrator.runner import RUNNER
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": output, "output_names": [output]}
    RUNNER._observer(RUNNER.jobs[jid])


def test_add_step_persists_configured_with_base_source(client):
    a, base = _asset_with_base_image()
    r = client.post(f"/assets/{a['id']}/postproc/step", json={"base": base, "preset": "clean"})
    assert r.status_code == 200, r.text
    stacks = r.json()["postproc_stacks"]
    assert len(stacks) == 1 and stacks[0]["base"] == base
    step = stacks[0]["steps"][0]
    assert step["preset"] == "clean" and step["backend"] == "zimage" and step["mode"] == "img2img"
    assert step["params"]["strength"] == 0.5
    assert step["source"] == base and step["status"] == "configured" and step["output"] is None
    assert step["id"].startswith("pps_")


def test_cannot_stack_before_previous_step_has_output(client):
    a, base = _asset_with_base_image()
    client.post(f"/assets/{a['id']}/postproc/step", json={"base": base, "preset": "clean"})
    r = client.post(f"/assets/{a['id']}/postproc/step", json={"base": base, "preset": "refine"})
    assert r.status_code == 409 and "previous step" in r.text


def test_param_validation(client):
    a, base = _asset_with_base_image()
    # a restore-only param on an i2i preset
    assert client.post(f"/assets/{a['id']}/postproc/step",
                       json={"base": base, "preset": "clean", "params": {"blend": 0.5}}
                       ).status_code == 422
    # restore's backend is fixed (face_restore)
    assert client.post(f"/assets/{a['id']}/postproc/step",
                       json={"base": base, "preset": "restore", "backend": "zimage"}
                       ).status_code == 422
    # an unknown model for the chosen backend
    assert client.post(f"/assets/{a['id']}/postproc/step",
                       json={"base": base, "preset": "custom", "params": {"model_name": "nope"}}
                       ).status_code == 422


def test_queue_dry_run_real_and_completion_records_output(client):
    from orchestrator.runner import RUNNER
    a, base = _asset_with_base_image()
    sid = client.post(f"/assets/{a['id']}/postproc/step",
                      json={"base": base, "preset": "clean", "params": {"strength": 0.4}}
                      ).json()["postproc_stacks"][0]["steps"][0]["id"]
    # dry-run: previews the img2img job over the source; enqueues nothing
    d = client.post(f"/assets/{a['id']}/postproc/step/{sid}/queue", json={"dry_run": True})
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["dry_run"] and body["pipeline"] == "zimage" and body["mode"] == "img2img"
    assert body["params"]["strength"] == 0.4
    assert body["params"]["batch_items"][0]["init_image"].replace("\\", "/").endswith(base)
    # real queue: step → queued + linked job
    q = client.post(f"/assets/{a['id']}/postproc/step/{sid}/queue", json={})
    assert q.status_code == 200, q.text
    step = q.json()["postproc_stacks"][0]["steps"][0]
    assert step["status"] == "queued" and step["job_id"]
    jid = step["job_id"]
    assert RUNNER.jobs[jid]["pipeline"] == "zimage" and RUNNER.jobs[jid]["mode"] == "img2img"
    # re-queueing a queued step is refused
    assert client.post(f"/assets/{a['id']}/postproc/step/{sid}/queue", json={}).status_code == 409
    # completion → observer records the produced output + done
    _complete(jid, f"{jid}/clean_x.png")
    s2 = client.get(f"/assets/{a['id']}").json()["versions"][0]["postproc_stacks"][0]["steps"][0]
    assert s2["status"] == "done" and s2["output"] == f"{jid}/clean_x.png"
    # now a SECOND step can stack, its source = the first step's output (the chain)
    r2 = client.post(f"/assets/{a['id']}/postproc/step", json={"base": base, "preset": "refine"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["postproc_stacks"][0]["steps"][1]["source"] == f"{jid}/clean_x.png"


def test_restore_preset_queues_io_job(client):
    a, base = _asset_with_base_image()
    sid = client.post(f"/assets/{a['id']}/postproc/step",
                      json={"base": base, "preset": "restore", "params": {"blend": 0.7}}
                      ).json()["postproc_stacks"][0]["steps"][0]["id"]
    # dry-run (skips the weight pre-flight that real restore would require)
    d = client.post(f"/assets/{a['id']}/postproc/step/{sid}/queue", json={"dry_run": True})
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["pipeline"] == "face_restore" and body["mode"] == "restore"
    item = body["params"]["batch_items"][0]
    assert item["input"].replace("\\", "/").endswith(base) and "init_image" not in item
    assert body["params"]["blend"] == 0.7


def test_remove_only_last_step(client):
    a, base = _asset_with_base_image()
    sid1 = client.post(f"/assets/{a['id']}/postproc/step", json={"base": base, "preset": "clean"}
                       ).json()["postproc_stacks"][0]["steps"][0]["id"]
    jid = client.post(f"/assets/{a['id']}/postproc/step/{sid1}/queue", json={}
                      ).json()["postproc_stacks"][0]["steps"][0]["job_id"]
    _complete(jid, f"{jid}/o.png")
    sid2 = client.post(f"/assets/{a['id']}/postproc/step", json={"base": base, "preset": "refine"}
                       ).json()["postproc_stacks"][0]["steps"][1]["id"]
    # the first (non-tail) step can't be removed mid-chain
    assert client.delete(f"/assets/{a['id']}/postproc/step/{sid1}").status_code == 409
    # the tail can; the stack keeps one step
    assert client.delete(f"/assets/{a['id']}/postproc/step/{sid2}").status_code == 200
    detail = client.get(f"/assets/{a['id']}").json()
    assert len(detail["versions"][0]["postproc_stacks"][0]["steps"]) == 1


def test_mask_is_stored_on_the_step(client):
    a, base = _asset_with_base_image()
    r = client.post(f"/assets/{a['id']}/postproc/step",
                    json={"base": base, "preset": "custom",
                          "mask": "job_base01/mask.png", "requires_mask": True})
    assert r.status_code == 200, r.text
    step = r.json()["postproc_stacks"][0]["steps"][0]
    assert step["mask"] == "job_base01/mask.png" and step["requires_mask"] is True


def test_finalized_version_refuses_step(client):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    a, base = _asset_with_base_image()
    assets.finalize_version(RUNNER.workspace, a["id"], a["active_version"])
    r = client.post(f"/assets/{a['id']}/postproc/step", json={"base": base, "preset": "clean"})
    assert r.status_code == 409 and "FINALIZED" in r.text.upper()
