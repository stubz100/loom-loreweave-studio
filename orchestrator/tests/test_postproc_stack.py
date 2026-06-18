"""M0c (P2) — PROJECT-LEVEL postprocess stack: persisted, independently-queued steps over
ANY base image (Sandbox or any character, any pipeline; keyed by the out/-relative base, not
a character version). No GPU — dry-run + paused queue + a directly-invoked completion observer
(the pattern test_identity_anchor uses to drive the anchor-verification observer).
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


def _base_image(base="job_base01/base.png", prompt="a portrait"):
    """A base image on disk in the open project's out/, with a completed producing job so a
    clean/refine step inherits its prompt + grid context (postproc is project-level — no asset
    needed). Pass prompt=None for an 'orphan' image with no inheritable prompt."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()                                  # queued jobs must not actually run
    p = RUNNER.workspace.out_dir / base
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    if prompt is not None:
        jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": prompt},
                            batch_id="bat_pp", index=0, batch_size=1, requester_id="sandbox")
        RUNNER.jobs[jid]["status"] = "done"
        RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": base, "output_names": [base]}
    return base


def _complete(jid, output):
    """Drive a queued step's job to done + fire the completion observer (records output)."""
    from orchestrator.runner import RUNNER
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": output, "output_names": [output]}
    RUNNER._observer(RUNNER.jobs[jid])


def _stacks(client):
    return client.get("/postproc/stacks").json()["stacks"]


def test_add_step_persists_configured_with_base_source(client):
    base = _base_image()
    r = client.post("/postproc/step", json={"base": base, "preset": "clean"})
    assert r.status_code == 200, r.text
    stacks = r.json()["stacks"]
    assert len(stacks) == 1 and stacks[0]["base"] == base
    step = stacks[0]["steps"][0]
    assert step["preset"] == "clean" and step["backend"] == "zimage" and step["mode"] == "img2img"
    assert step["params"]["strength"] == 0.5
    assert step["source"] == base and step["status"] == "configured" and step["output"] is None
    assert step["id"].startswith("pps_")
    # GET reflects the persisted store
    assert _stacks(client)[0]["steps"][0]["id"] == step["id"]


def test_works_without_an_asset_any_image(client):
    """The whole point of project-level: a Sandbox/any image (no character) gets a stack."""
    base = _base_image("job_sandbox/flux2_x.png")
    r = client.post("/postproc/step", json={"base": base, "preset": "refine"})
    assert r.status_code == 200, r.text
    assert r.json()["stacks"][0]["base"] == base


def test_cannot_stack_before_previous_step_has_output(client):
    base = _base_image()
    client.post("/postproc/step", json={"base": base, "preset": "clean"})
    r = client.post("/postproc/step", json={"base": base, "preset": "refine"})
    assert r.status_code == 409 and "previous step" in r.text


def test_param_validation(client):
    base = _base_image()
    assert client.post("/postproc/step",
                       json={"base": base, "preset": "clean", "params": {"blend": 0.5}}
                       ).status_code == 422
    assert client.post("/postproc/step",
                       json={"base": base, "preset": "restore", "backend": "zimage"}
                       ).status_code == 422
    assert client.post("/postproc/step",
                       json={"base": base, "preset": "clean", "params": {"model_name": "nope"}}
                       ).status_code == 422


def test_queue_dry_run_real_and_completion_records_output(client):
    from orchestrator.runner import RUNNER
    base = _base_image()
    sid = client.post("/postproc/step",
                      json={"base": base, "preset": "clean", "params": {"strength": 0.4}}
                      ).json()["stacks"][0]["steps"][0]["id"]
    # dry-run previews the img2img job over the source; enqueues nothing
    d = client.post(f"/postproc/step/{sid}/queue", json={"dry_run": True})
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["dry_run"] and body["pipeline"] == "zimage" and body["mode"] == "img2img"
    assert body["params"]["strength"] == 0.4
    assert body["params"]["batch_items"][0]["init_image"].replace("\\", "/").endswith(base)
    # real queue: step → queued + linked job
    q = client.post(f"/postproc/step/{sid}/queue", json={})
    assert q.status_code == 200, q.text
    step = q.json()["stacks"][0]["steps"][0]
    assert step["status"] == "queued" and step["job_id"]
    jid = step["job_id"]
    assert RUNNER.jobs[jid]["pipeline"] == "zimage" and RUNNER.jobs[jid]["mode"] == "img2img"
    # re-queueing a queued step is refused
    assert client.post(f"/postproc/step/{sid}/queue", json={}).status_code == 409
    # completion → observer records the produced output + done
    _complete(jid, f"{jid}/clean_x.png")
    s2 = _stacks(client)[0]["steps"][0]
    assert s2["status"] == "done" and s2["output"] == f"{jid}/clean_x.png"
    # now a SECOND step can stack, source = the first step's output (the chain)
    r2 = client.post("/postproc/step", json={"base": base, "preset": "refine"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["stacks"][0]["steps"][1]["source"] == f"{jid}/clean_x.png"


def test_clean_inherits_source_prompt(client):
    """A clean/refine step has no prompt of its own → it must re-diffuse with the SOURCE
    image's prompt (the worker rejects an empty-prompt item → the job fails)."""
    base = _base_image(prompt="a red-haired ranger in a forest")
    sid = client.post("/postproc/step", json={"base": base, "preset": "clean"}
                      ).json()["stacks"][0]["steps"][0]["id"]
    d = client.post(f"/postproc/step/{sid}/queue", json={"dry_run": True}).json()
    assert d["params"]["batch_items"][0]["prompt"] == "a red-haired ranger in a forest"


def test_clean_without_inheritable_prompt_needs_one(client):
    """An orphan image (no producing job) + no typed prompt → 422 (never an empty-prompt job);
    an explicit prompt unblocks it."""
    base = _base_image("orphan/x.png", prompt=None)
    sid = client.post("/postproc/step", json={"base": base, "preset": "clean"}
                      ).json()["stacks"][0]["steps"][0]["id"]
    assert client.post(f"/postproc/step/{sid}/queue", json={"dry_run": True}).status_code == 422
    # a step that carries an explicit prompt is fine
    base2 = _base_image("orphan/y.png", prompt=None)
    stacks2 = client.post("/postproc/step",
                          json={"base": base2, "preset": "clean", "params": {"prompt": "a knight"}}
                          ).json()["stacks"]
    sid2 = next(s for s in stacks2 if s["base"] == base2)["steps"][0]["id"]
    d = client.post(f"/postproc/step/{sid2}/queue", json={"dry_run": True}).json()
    assert d["params"]["batch_items"][0]["prompt"] == "a knight"


def test_queue_routes_tile_to_requester_context(client):
    """The UI passes its current context so the queued tile lands in that grid: requester_id
    (a character version) + stage are stamped on the job; omitted ⇒ the project (Sandbox)."""
    from orchestrator.runner import RUNNER
    base = _base_image()
    sid = client.post("/postproc/step", json={"base": base, "preset": "clean"}
                      ).json()["stacks"][0]["steps"][0]["id"]
    jid = client.post(f"/postproc/step/{sid}/queue",
                      json={"requester_id": "ver_abc123", "stage": "A"}
                      ).json()["stacks"][0]["steps"][0]["job_id"]
    job = RUNNER.jobs[jid]
    assert job["requester_id"] == "ver_abc123"
    assert job["profile_version_id"] == "ver_abc123" and job["stage"] == "A"


def test_canceled_or_deleted_job_unsticks_the_step(client):
    """A queued step whose job is canceled/failed/deleted must not stay stuck 'queued':
    GET /postproc/stacks reconciles it with the live queue, and it can be re-queued."""
    from orchestrator.runner import RUNNER
    base = _base_image()
    sid = client.post("/postproc/step", json={"base": base, "preset": "clean"}
                      ).json()["stacks"][0]["steps"][0]["id"]
    jid = client.post(f"/postproc/step/{sid}/queue", json={}
                      ).json()["stacks"][0]["steps"][0]["job_id"]
    # the job is canceled (the completion observer never fires for non-OK jobs)
    RUNNER.jobs[jid]["status"] = "canceled"
    # GET reconciles the stuck 'queued' step to the job's real (canceled) state
    step = _stacks(client)[0]["steps"][0]
    assert step["status"] == "canceled"
    # and it can be re-queued (the stale 'queued' no longer 409s — the live job isn't active)
    rq = client.post(f"/postproc/step/{sid}/queue", json={})
    assert rq.status_code == 200, rq.text
    assert rq.json()["stacks"][0]["steps"][0]["status"] == "queued"


def test_deleted_job_reconciles_to_canceled(client):
    """A step whose job was DELETED from the queue (gone entirely) reconciles to canceled
    (so the UI can remove/re-queue it instead of being stuck)."""
    from orchestrator.runner import RUNNER
    base = _base_image()
    sid = client.post("/postproc/step", json={"base": base, "preset": "clean"}
                      ).json()["stacks"][0]["steps"][0]["id"]
    jid = client.post(f"/postproc/step/{sid}/queue", json={}
                      ).json()["stacks"][0]["steps"][0]["job_id"]
    RUNNER.jobs.pop(jid, None)                     # deleted from the queue
    assert _stacks(client)[0]["steps"][0]["status"] == "canceled"


def test_restore_preset_queues_io_job(client):
    base = _base_image()
    sid = client.post("/postproc/step",
                      json={"base": base, "preset": "restore", "params": {"blend": 0.7}}
                      ).json()["stacks"][0]["steps"][0]["id"]
    d = client.post(f"/postproc/step/{sid}/queue", json={"dry_run": True})
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["pipeline"] == "face_restore" and body["mode"] == "restore"
    item = body["params"]["batch_items"][0]
    assert item["input"].replace("\\", "/").endswith(base) and "init_image" not in item
    assert body["params"]["blend"] == 0.7


def test_remove_only_last_step(client):
    base = _base_image()
    sid1 = client.post("/postproc/step", json={"base": base, "preset": "clean"}
                       ).json()["stacks"][0]["steps"][0]["id"]
    jid = client.post(f"/postproc/step/{sid1}/queue", json={}
                      ).json()["stacks"][0]["steps"][0]["job_id"]
    _complete(jid, f"{jid}/o.png")
    sid2 = client.post("/postproc/step", json={"base": base, "preset": "refine"}
                       ).json()["stacks"][0]["steps"][1]["id"]
    # the first (non-tail) step can't be removed mid-chain
    assert client.delete(f"/postproc/step/{sid1}").status_code == 409
    # the tail can; the stack keeps one step
    assert client.delete(f"/postproc/step/{sid2}").status_code == 200
    assert len(_stacks(client)[0]["steps"]) == 1


def test_mask_is_stored_on_the_step(client):
    base = _base_image()
    r = client.post("/postproc/step",
                    json={"base": base, "preset": "clean",
                          "mask": "job_base01/mask.png", "requires_mask": True})
    assert r.status_code == 200, r.text
    step = r.json()["stacks"][0]["steps"][0]
    assert step["mask"] == "job_base01/mask.png" and step["requires_mask"] is True
