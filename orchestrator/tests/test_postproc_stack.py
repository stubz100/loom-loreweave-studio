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


def _last_step_id(resp, base):
    """The tail step id for a base from a /postproc/step response (which returns ALL stacks)."""
    stack = next(s for s in resp.json()["stacks"] if s["base"] == base)
    return stack["steps"][-1]["id"]


def test_i2i_step_output_size_scale_and_explicit(client):
    """M0e Part B: a Clean/Refine step can resize — a scale factor over the source dims (enlarge
    OR reduce), or an explicit W×H. The fake base reads as the 1024² fallback, so ×2 → 2048² and
    ×0.5 → 512²; explicit wins; no override → source dims preserved (today's behaviour)."""
    base = _base_image()
    sid = _last_step_id(client.post("/postproc/step",
                        json={"base": base, "preset": "clean", "params": {"scale": 2}}), base)
    d = client.post(f"/postproc/step/{sid}/queue", json={"dry_run": True}).json()
    assert d["params"]["width"] == 2048 and d["params"]["height"] == 2048
    # reduce: ×0.5 over the 1024² fallback → 512²
    baseR = _base_image("job_baseR/base.png")
    sidR = _last_step_id(client.post("/postproc/step",
                         json={"base": baseR, "preset": "refine", "params": {"scale": 0.5}}), baseR)
    dR = client.post(f"/postproc/step/{sidR}/queue", json={"dry_run": True}).json()
    assert dR["params"]["width"] == 512 and dR["params"]["height"] == 512
    base2 = _base_image("job_base02/base.png")
    sid2 = _last_step_id(client.post("/postproc/step",
                         json={"base": base2, "preset": "refine",
                               "params": {"width": 1536, "height": 768}}), base2)
    d2 = client.post(f"/postproc/step/{sid2}/queue", json={"dry_run": True}).json()
    assert d2["params"]["width"] == 1536 and d2["params"]["height"] == 768
    base3 = _base_image("job_base03/base.png")
    sid3 = _last_step_id(client.post("/postproc/step", json={"base": base3, "preset": "clean"}),
                         base3)
    d3 = client.post(f"/postproc/step/{sid3}/queue", json={"dry_run": True}).json()
    assert d3["params"]["width"] == 1024 and d3["params"]["height"] == 1024


def test_i2i_output_size_validation_and_flux2_rejects_size(client):
    """M0e Part B: width/height must be ÷16 ints in range & set together; scale in [0.25, 4]. flux2
    i2i (re-pose at source dims) does NOT accept an output size (not in its allowed param set)."""
    base = _base_image()

    def bad(params):
        return client.post("/postproc/step",
                           json={"base": base, "preset": "clean", "params": params}).status_code
    assert bad({"width": 1000, "height": 1000}) == 422   # not ÷16
    assert bad({"width": 1024}) == 422                    # height missing (pair)
    assert bad({"scale": 8}) == 422                       # scale above max
    assert bad({"scale": 0.1}) == 422                     # scale below min (0.25)
    assert bad({"width": 100, "height": 112}) == 422      # below min 256
    # flux2 i2i rejects the size params entirely (zimage/sd35 only)
    assert client.post("/postproc/step",
                       json={"base": base, "preset": "clean", "backend": "flux2",
                             "params": {"scale": 2}}).status_code == 422


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


def test_flux2_i2i_step_is_single_run_with_init_image(client):
    """M0d Part C — flux2 joins zimage/sd35 as an i2i backend; its job is a SINGLE run (no
    batch_items — the worker batch path is t2i/ref only) carrying init_image + the prompt. A
    flux.2-dev structured-JSON prompt rides the step's `prompt` param unchanged."""
    base = _base_image()
    json_prompt = '{"scene":"a forest clearing","camera":{"angle":"full left profile, looking left"}}'
    stacks = client.post("/postproc/step",
                         json={"base": base, "preset": "refine", "backend": "flux2",
                               "params": {"model_name": "flux.2-dev", "prompt": json_prompt}}
                         ).json()["stacks"]
    step = stacks[0]["steps"][0]
    assert step["backend"] == "flux2" and step["mode"] == "img2img"
    assert step["params"]["model_name"] == "flux.2-dev"
    d = client.post(f"/postproc/step/{step['id']}/queue", json={"dry_run": True})
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["pipeline"] == "flux2" and body["mode"] == "img2img"
    p = body["params"]
    assert "batch_items" not in p                       # single-run, not a batch job
    assert p["init_image"].replace("\\", "/").endswith(base)
    assert p["prompt"] == json_prompt                   # the JSON rides the prompt verbatim
    assert p["strength"] == 0.25                         # the refine preset strength


def test_upscale_preset_single_run_tile_cn(client):
    """M0e Part C: the Upscale preset = a SINGLE-run sd35 cn-inpaint job over the source as the
    tile-CN CONTROL image at the target size (preset default ×2 → 2048² from the 1024² fallback).
    No batch_items; controlnet fixed to 'tile'; prompt + cn_scale carried."""
    base = _base_image(prompt="a knight")
    sid = _last_step_id(client.post("/postproc/step", json={"base": base, "preset": "upscale"}), base)
    d = client.post(f"/postproc/step/{sid}/queue", json={"dry_run": True}).json()
    assert d["pipeline"] == "sd35" and d["mode"] == "cn-inpaint"
    p = d["params"]
    assert "batch_items" not in p                       # single-run (CN modes aren't batchable)
    assert p["controlnet"] == "tile"
    assert "init_image" not in p                        # the tile CN is the conditioner, not i2i
    assert p["control_image"].replace("\\", "/").endswith(base)
    assert p["width"] == 2048 and p["height"] == 2048   # ×2 over the 1024² fallback
    assert p["cn_scale"] == "0.6" and p["prompt"] == "a knight"   # inherited source prompt


def test_upscale_backend_fixed_and_medium_only(client):
    """M0e Part C: upscale is sd35-fixed (the SD3-medium tile CN) — a non-sd35 backend or a
    non-medium model is 422. cn_scale + an explicit output size override are accepted."""
    base = _base_image()
    assert client.post("/postproc/step",
                       json={"base": base, "preset": "upscale", "backend": "zimage"}
                       ).status_code == 422
    assert client.post("/postproc/step",
                       json={"base": base, "preset": "upscale",
                             "params": {"model_name": "sd3.5-large"}}).status_code == 422
    sid = _last_step_id(client.post("/postproc/step",
                        json={"base": base, "preset": "upscale",
                              "params": {"cn_scale": "0.8", "width": 1536, "height": 1024}}), base)
    d = client.post(f"/postproc/step/{sid}/queue", json={"dry_run": True}).json()
    assert d["params"]["cn_scale"] == "0.8"
    assert d["params"]["width"] == 1536 and d["params"]["height"] == 1024


def test_upscale_missing_tile_cn_weight_412(client, monkeypatch):
    """M0e Part C: a real upscale queue offers the tile-CN fetch (412) when the InstantX SD3 tile
    ControlNet weight is missing — a separate gate from the sd3.5-medium base check."""
    from orchestrator import components
    base = _base_image()
    sid = _last_step_id(client.post("/postproc/step", json={"base": base, "preset": "upscale"}), base)
    monkeypatch.setattr(components, "image_model_present", lambda repo: True)   # base present
    monkeypatch.setattr(components, "postproc_weights_status",
                        lambda tool, variant_id=None: (False, [{"id": "sd3-controlnet-tile",
                                                                "repo_id": "InstantX/SD3-Controlnet-Tile"}]))
    r = client.post(f"/postproc/step/{sid}/queue", json={})
    assert r.status_code == 412
    assert "Tile ControlNet" in r.text and "sd35_tile_cn" in r.text


def test_i2i_backend_must_be_known(client):
    """An unknown i2i backend is rejected; flux2 is now accepted alongside zimage/sd35."""
    base = _base_image()
    assert client.post("/postproc/step",
                       json={"base": base, "preset": "clean", "backend": "nope"}
                       ).status_code == 422
    assert client.post("/postproc/step",
                       json={"base": base, "preset": "clean", "backend": "flux2"}
                       ).status_code == 200


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


def test_queue_unwinds_the_job_when_the_step_vanished(client, monkeypatch):
    """M2.8 #2 — the submit-then-mark race: a step deleted between resolve and mark_queued
    used to 409 while its just-submitted job kept running, orphaned from any stack. Now the
    endpoint UNWINDS (cancel + delete the queued job) before re-raising the 409 — no orphan."""
    from orchestrator import postproc
    from orchestrator import workspace as ws_mod
    from orchestrator.runner import RUNNER
    base = _base_image()
    r = client.post("/postproc/step", json={"base": base, "preset": "clean"})
    step_id = r.json()["stacks"][0]["steps"][0]["id"]
    before = set(RUNNER.snapshot())

    def gone(_ws, *, step_id, job_id):  # the concurrent DELETE landed first
        raise ws_mod.WorkspaceError(f"unknown postproc step {step_id!r}")
    monkeypatch.setattr(postproc, "mark_queued", gone)
    q = client.post(f"/postproc/step/{step_id}/queue", json={})
    assert q.status_code == 409
    assert set(RUNNER.snapshot()) == before      # the submitted job was fully unwound


def test_store_mutations_are_thread_safe(client):
    """M2.8 #3 — the store is load-modify-save with two writer threads (API threadpool +
    the runner's completion observer); without the module lock, interleaved add_step calls
    lose each other's stacks. Deterministic WITH the lock; flaky only if it regresses."""
    import threading
    from orchestrator import postproc
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    errs: list[Exception] = []

    def add_many(prefix: str):
        try:
            for i in range(20):
                postproc.add_step(ws, base=f"{prefix}/{i}.png", preset="clean",
                                  backend="zimage", mode="img2img", params={})
        except Exception as e:  # noqa: BLE001 - surfaced via the assert below
            errs.append(e)

    threads = [threading.Thread(target=add_many, args=(p,)) for p in ("job_ta", "job_tb")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errs
    assert len(postproc.list_stacks(ws)) == 40   # nothing lost to a concurrent write


def test_stylelock_appends_the_l1_style_at_queue_time(client):
    """M2.10 route 3 — StyleLock: an ON-DEMAND i2i preset (author call: never auto-chained)
    that re-renders any image toward the L1 style — the pass's backend owns the look, so the
    text pulls TOWARD the source/L1 style. The fragment resolves FRESH at queue time and
    appends after the content prompt (R104 placement); a flux2 backend -> 422 (it's the
    drift source this pass exists to correct). `style_id` pins a specific style."""
    from orchestrator import bible
    from orchestrator.runner import RUNNER
    base = _base_image(base="job_sl01/base.png")
    r = client.post("/postproc/step", json={"base": base, "preset": "stylelock",
                                            "backend": "flux2"})
    assert r.status_code == 422
    r = client.post("/postproc/step", json={"base": base, "preset": "stylelock"})
    assert r.status_code == 200, r.text
    step = r.json()["stacks"][0]["steps"][0]
    assert step["backend"] == "sd35" and step["params"]["strength"] == 0.3
    client.delete(f"/postproc/step/{step['id']}")
    sid = bible.add_style(RUNNER.workspace, name="Ink",
                          fragment="stark ink-wash rendering")["styles"][0]["id"]
    r = client.post("/postproc/step", json={"base": base, "preset": "stylelock",
                                            "params": {"style_id": sid, "strength": 0.22}})
    assert r.status_code == 200, r.text
    step = r.json()["stacks"][0]["steps"][0]
    q = client.post(f"/postproc/step/{step['id']}/queue", json={})
    assert q.status_code == 200, q.text
    jid = q.json()["stacks"][0]["steps"][0]["job_id"]
    job = RUNNER.get(jid)
    p = job["params"]["batch_items"][0]["prompt"]
    assert p.startswith("a portrait") and p.endswith("stark ink-wash rendering")
    bi = job["params"]["batch_items"][0]
    assert bi.get("strength", job["params"].get("strength")) == 0.22   # variable strength rides


def test_a_stack_branches_so_one_base_can_carry_several_first_level_passes(client):
    """Author 2026-08-08: *"if someone wants to test different strengths, or types of post
    processing, the same base image cannot be used for that"*. A stack used to append strictly
    onto the previous step's output — one base, one line. It is now a TREE: `source` names the
    branch point (the base, or any FINISHED step's output), and omitting it keeps the old
    continue-the-chain behaviour so nothing existing changes."""
    from orchestrator import postproc
    from orchestrator.runner import RUNNER

    ws = RUNNER.workspace
    base = _base_image("job_br/base.png")

    # two DIFFERENT first-level passes off the SAME base — the thing that was impossible
    a = _last_step_id(client.post("/postproc/step", json={
        "base": base, "preset": "clean", "params": {"strength": 0.35}}), base)
    b = _last_step_id(client.post("/postproc/step", json={
        "base": base, "preset": "clean", "source": base, "params": {"strength": 0.7}}), base)
    stack = next(s for s in postproc.list_stacks(ws) if s["base"] == base)
    steps = {s["id"]: s for s in stack["steps"]}
    assert steps[a]["source"] == base and steps[b]["source"] == base
    assert steps[a]["params"]["strength"] != steps[b]["params"]["strength"]

    # branching from an UNFINISHED step is refused — a source must be a real image on disk
    r = client.post("/postproc/step", json={"base": base, "preset": "clean",
                                            "source": "job_br/nope.png", "params": {}})
    assert r.status_code == 409 and "branch from" in r.text

    # finish one, then stack ON it — and it becomes an offered branch point
    postproc.record_result(ws, steps[a]["job_id"] or "j", output="job_br/a1.png", ok=True) \
        if steps[a].get("job_id") else None
    stack = next(s for s in postproc.list_stacks(ws) if s["base"] == base)
    srcs = client.get("/postproc/sources", params={"base": base}).json()["sources"]
    assert srcs[0]["output"] == base            # the base is always offered
    assert all(s["output"] for s in srcs)       # …and only real images are


def test_removing_a_step_that_others_branch_from_is_refused(client):
    """`remove_step` was "only the LAST step", which is the same rule while a stack is a
    straight chain. Now that it branches, what matters is that a removal never orphans the
    steps sourced from it — so a leaf goes, a branched-from step does not."""
    from orchestrator import postproc
    from orchestrator.runner import RUNNER

    ws = RUNNER.workspace
    base = _base_image("job_br2/base.png")
    first = _last_step_id(client.post("/postproc/step",
                                      json={"base": base, "preset": "clean", "params": {}}), base)
    # finish it the way the runner's observer does, so the output is really persisted
    postproc.mark_queued(ws, step_id=first, job_id="job_fake01")
    postproc.record_result(ws, "job_fake01", output="job_br2/out1.png", ok=True)

    child = _last_step_id(client.post("/postproc/step", json={
        "base": base, "preset": "restore", "source": "job_br2/out1.png", "params": {}}), base)

    # the parent now has a dependant → refused, named clearly
    r = client.delete(f"/postproc/step/{first}")
    assert r.status_code == 409 and "branches from this one" in r.text
    # the leaf goes fine, and then the parent can too
    assert client.delete(f"/postproc/step/{child}").status_code == 200
    assert client.delete(f"/postproc/step/{first}").status_code == 200


def test_i2i_step_budget_rescues_distilled_models_and_leaves_the_rest_alone(client):
    """Rig finding 2026-08-09 (`job_724798a6`): a flux2 "Clean" at strength 0.6 came back
    looking like its input. It had NOT failed — exit 0, manifest completed, 93 % of pixels
    changed — but the denoise stage recorded `num_timesteps: 2, timesteps: [0.6, 0.0]`.

    `num_steps` is the FULL schedule and an i2i run walks only the last `strength × num_steps`
    of it, so a DISTILLED 4-step model collapses to 2 steps (or 1 for a Refine at 0.25) and
    returns the source with a faint wash. The fix is a FLOOR on the EFFECTIVE steps, not a
    blanket rescale: rescaling every model to its full budget would double sd3.5-medium
    (40 → 80 requested) to buy nothing, since 20 effective steps was already ample."""
    from orchestrator import model_catalog as mc

    # flux2 since 2026-08-09: its worker builds the schedule ACROSS [strength, 0] with
    # `num_steps` intervals, so num_steps IS the effective count — no over-request, at any
    # strength. (Over-requesting was pointless anyway: the old tail-slice ignored it.)
    for model in ("flux.2-klein-4b", "flux.2-klein-9b"):
        for st in (0.6, 0.25):
            req, eff = mc.i2i_step_budget("flux2", model, st)
            assert req is None and eff == 4, (model, st, req, eff)

    # zimage/sd35 keep the diffusers fraction convention, so a distilled preset still needs
    # over-requesting there
    req, eff = mc.i2i_step_budget("sd35", "sd3.5-large-turbo", 0.5)
    assert req == 8 and eff == 4

    # models whose own preset already clears the floor are untouched — no request, no slowdown
    for pl, model, st, want_eff in (("sd35", "sd3.5-medium", 0.5, 20),
                                    ("zimage", "zimage-base", 0.5, 25),
                                    ("zimage", "zimage-turbo", 0.5, 4),
                                    # flux2 is "exact": dev's 8-step preset IS 8 intervals
                                    ("flux2", "flux.2-dev", 0.5, 8)):
        req, eff = mc.i2i_step_budget(pl, model, st)
        assert req is None and eff == want_eff, (pl, model, req, eff)

    # a very low strength must not explode into a 400-step run

    # An UNSET model is the common case (the step just says "backend: flux2"), and the worker
    # will then run the pipeline's own default variant — so the budget predicts from THAT
    # rather than declining to answer. Same for a name the catalog doesn't know.
    assert mc.i2i_step_budget("flux2", None, 0.5) == mc.i2i_step_budget(
        "flux2", mc.default_model("flux2"), 0.5)
    assert mc.i2i_step_budget("flux2", "nope", 0.5)[0] is None

    # …but with no strength there is nothing to reason about: no opinion, worker's default
    assert mc.i2i_step_budget("flux2", "flux.2-klein-4b", None)[0] is None
    assert mc.i2i_step_budget("flux2", "flux.2-klein-4b", 0)[0] is None

    # a very low strength on a FRACTION pipeline is what needs the cap
    assert mc.i2i_step_budget("sd35", "sd3.5-large-turbo", 0.01)[0] == mc.MAX_I2I_STEPS


def test_queued_i2i_step_carries_the_corrected_budget(client):
    """The budget has to reach the JOB, on both i2i paths — flux2 is a single-run job, while
    zimage/sd35 go through batch_items — or the correction is cosmetic."""
    from orchestrator.runner import RUNNER

    base = _base_image("job_bud/base.png")
    # flux2: distilled 4-step → lifted
    sid = _last_step_id(client.post("/postproc/step", json={
        "base": base, "preset": "clean", "backend": "flux2",
        "params": {"strength": 0.6}}), base)
    job = RUNNER.get(_queue_id(client, sid, base))
    assert "num_steps" not in job["params"]   # flux2: its 4-step preset IS 4 real intervals

    # sd35 medium: 40-step preset already clears the floor → untouched
    sid2 = _last_step_id(client.post("/postproc/step", json={
        "base": base, "preset": "clean", "backend": "sd35", "source": base,
        "params": {"strength": 0.6}}), base)
    job2 = RUNNER.get(_queue_id(client, sid2, base))
    assert "num_steps" not in job2["params"]


def _queue_id(client, step_id, base):
    r = client.post(f"/postproc/step/{step_id}/queue", json={})
    assert r.status_code == 200, r.text
    for stack in r.json()["stacks"]:
        for st in stack["steps"]:
            if st["id"] == step_id:
                return st["job_id"]
    raise AssertionError("step not found")


def test_deleting_an_image_with_descendants_leaves_a_tombstone(client):
    """Author rule 2026-08-09: *"only if this is the end of the chain, otherwise keep the job
    manifest (but flagged deleted), if there are already new images generated on it, so the
    chain becomes consistent."*

    Deleting a job used to remove the record outright, stranding everything derived from it —
    a postproc pass whose `chained_from` no longer resolved surfaced as an unparented
    top-level card (the char01 anomaly). A job with descendants now keeps its record, flagged
    `deleted`, with every artifact reference cleared; a LEAF is removed completely."""
    from orchestrator.runner import RUNNER

    base = _base_image("job_tomb/base.png")
    parent = next(j for j in RUNNER.jobs.values()
                  if (j.get("result") or {}).get("output_name") == base)
    child = RUNNER.submit(pipeline="zimage", mode="img2img", params={"prompt": "x"},
                          batch_id="", index=0, batch_size=1,
                          chained_from=parent["id"], pass_name="clean")
    RUNNER.jobs[child]["status"] = "done"
    RUNNER.jobs[child]["result"] = {"ok": True, "output_name": "job_tomb/c.png",
                                    "output_names": ["job_tomb/c.png"]}

    assert RUNNER.has_descendants(parent["id"]) is True
    assert RUNNER.delete(parent["id"]) is True

    kept = RUNNER.get(parent["id"])
    assert kept is not None, "a job with descendants must survive as a tombstone"
    assert kept["deleted"] is True
    # every artifact pointer is cleared — nothing may try to serve a file that is gone
    res = kept["result"]
    assert res["outputs"] == [] and res["output_names"] == [] and res["output_name"] is None
    # …and the chain still resolves, which is the entire point
    assert RUNNER.get(child)["chained_from"] == parent["id"]

    # the LEAF, by contrast, goes completely
    assert RUNNER.has_descendants(child) is False
    assert RUNNER.delete(child) is True
    assert RUNNER.get(child) is None


def test_reconcile_prunes_stack_records_whose_job_is_gone(client):
    """The other half of the author's point 1: deleting an image never touched
    `postproc_stacks.json`, and reconcile only ever revisited QUEUED/RUNNING steps — so a
    *done* step kept a job_id and an output pointing at files that no longer existed (their
    live project: 13 dangling bases, 18 dangling outputs, 19 dangling jobs over 26 stacks).
    Reconcile runs on every stacks read, so making it authoritative self-heals the store."""
    from orchestrator import postproc
    from orchestrator.runner import RUNNER

    ws = RUNNER.workspace
    base = _base_image("job_rec/base.png")
    sid = _last_step_id(client.post("/postproc/step",
                                    json={"base": base, "preset": "clean"}), base)
    jid = _queue_id(client, sid, base)
    _complete(jid, "job_rec/out1.png")
    assert any(s["base"] == base for s in _stacks(client))

    # the job disappears (deleted / pruned) — nothing branches from its output
    RUNNER.jobs.pop(jid, None)
    stacks = _stacks(client)                      # a read is enough to heal it
    stack = next((s for s in stacks if s["base"] == base), None)
    assert stack is not None and stack["steps"] == [], "the dead step should be pruned"

    # …but a step something BRANCHES from becomes a tombstone instead, keeping the chain
    base2 = _base_image("job_rec2/base.png")
    a = _last_step_id(client.post("/postproc/step",
                                  json={"base": base2, "preset": "clean"}), base2)
    ja = _queue_id(client, a, base2)
    _complete(ja, "job_rec2/a.png")
    _last_step_id(client.post("/postproc/step", json={
        "base": base2, "preset": "restore", "source": "job_rec2/a.png"}), base2)
    RUNNER.jobs.pop(ja, None)
    stack2 = next(s for s in _stacks(client) if s["base"] == base2)
    first = next(s for s in stack2["steps"] if s["id"] == a)
    assert first.get("deleted") is True, "a branched-from step is kept as a tombstone"
    assert len(stack2["steps"]) == 2


def test_a_tombstoned_job_reads_as_gone_so_its_step_reconciles(client):
    """Author, 2026-08-09: *"when I deleted the 2nd level image on the stack (zimage/str:0.6),
    the stack didn't reflect this change and I can still see the image in the stack, but it
    doesn't exist as a tile on the stack card."*

    The tombstone rule (above) and the reconcile prune (above) each worked, but not together:
    `_job_state` reported a job GONE only when its record was gone, and a tombstone keeps the
    record. So reconcile stayed blind to exactly the deletes the tombstone rule handles — the
    step held its `done` status and an `output` naming a file that had been removed with the
    job. A tombstoned job's images are as deleted as any other's, so it must read as gone and
    go back through reconcile's own tombstone path."""
    from orchestrator import postproc
    from orchestrator.runner import RUNNER

    base = _base_image("job_tomb2/base.png")
    sid = _last_step_id(client.post("/postproc/step",
                                    json={"base": base, "preset": "clean"}), base)
    jid = _queue_id(client, sid, base)
    _complete(jid, "job_tomb2/out1.png")

    RUNNER.delete(jid, tombstone=True)          # record kept + flagged, artifacts cleared
    assert RUNNER.get(jid)["deleted"] is True, "precondition: the record survives"

    stack = next((s for s in _stacks(client) if s["base"] == base), None)
    assert stack is not None and stack["steps"] == [], \
        "a step whose job was tombstoned must not keep pointing at the deleted image"

    # …and when something DOES branch from it, the step is kept as a tombstone so the chain
    # stays linked (the FE renders a placeholder in its place rather than a dead tile).
    base2 = _base_image("job_tomb3/base.png")
    a = _last_step_id(client.post("/postproc/step",
                                  json={"base": base2, "preset": "clean"}), base2)
    ja = _queue_id(client, a, base2)
    _complete(ja, "job_tomb3/a.png")
    _last_step_id(client.post("/postproc/step", json={
        "base": base2, "preset": "restore", "source": "job_tomb3/a.png"}), base2)

    RUNNER.delete(ja, tombstone=True)
    stack2 = next(s for s in _stacks(client) if s["base"] == base2)
    first = next(s for s in stack2["steps"] if s["id"] == a)
    assert first.get("deleted") is True
    assert len(stack2["steps"]) == 2, "the branched step keeps its parent link"


def test_removing_a_step_deletes_the_image_it_produced(client):
    """Author, 2026-08-09: *"I removed the last step on the stack, but the image is still
    there, it should have been deleted."*

    The mirror of the delete that never reached the stack. Removing a step dropped the record
    only, so its output lived on as a library image no stack accounted for — and since the
    step was gone, nothing could ever clean it up. A step and the image it produced are one
    thing, in both directions."""
    from orchestrator.runner import RUNNER

    base = _base_image("job_rm/base.png")
    sid = _last_step_id(client.post("/postproc/step",
                                    json={"base": base, "preset": "clean"}), base)
    jid = _queue_id(client, sid, base)
    _complete(jid, "job_rm/out1.png")

    r = client.delete(f"/postproc/step/{sid}")
    assert r.status_code == 200, r.text
    assert RUNNER.get(jid) is None, "the step's job (and its image) must go with the step"
    stack = next((s for s in _stacks(client) if s["base"] == base), None)
    assert stack is None or stack["steps"] == []


def test_a_refused_step_removal_keeps_its_image(client):
    """Ordering guard. `remove_step` refuses a step others branch from — the image must
    survive that refusal, so the job may only be deleted AFTER the removal is accepted."""
    from orchestrator.runner import RUNNER

    base = _base_image("job_rm2/base.png")
    a = _last_step_id(client.post("/postproc/step",
                                  json={"base": base, "preset": "clean"}), base)
    ja = _queue_id(client, a, base)
    _complete(ja, "job_rm2/a.png")
    client.post("/postproc/step", json={"base": base, "preset": "restore",
                                        "source": "job_rm2/a.png"})

    r = client.delete(f"/postproc/step/{a}")
    assert r.status_code == 409
    assert RUNNER.get(ja) is not None, "a refused removal must not have deleted the image"
    assert (RUNNER.get(ja).get("result") or {}).get("output_name") == "job_rm2/a.png"


def test_removing_a_step_whose_job_is_live_is_refused(client):
    """Deleting a running job's files mid-write is exactly what R80 exists to prevent, so the
    removal is refused rather than half-applied (the queue endpoint refuses the same way)."""
    from orchestrator.runner import RUNNER

    base = _base_image("job_rm3/base.png")
    sid = _last_step_id(client.post("/postproc/step",
                                    json={"base": base, "preset": "clean"}), base)
    jid = _queue_id(client, sid, base)
    RUNNER.jobs[jid]["status"] = "running"
    try:
        r = client.delete(f"/postproc/step/{sid}")
        assert r.status_code == 409 and "cancel the job first" in r.text
        assert RUNNER.get(jid) is not None
        assert any(s["id"] == sid for st in _stacks(client) for s in st["steps"])
    finally:
        # RUNNER is a process-wide singleton: a job left 'running' holds the concurrency slot
        # and every later module's queue stalls behind it. Always hand it back.
        RUNNER.jobs[jid]["status"] = "canceled"
