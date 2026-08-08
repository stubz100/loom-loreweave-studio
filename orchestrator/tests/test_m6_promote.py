"""P2/M6 — promote (Stage E) + R13 manual cleanup + P2-11 preview (no GPU).

Locks the promote contract: a DONE trainer run's adapter is COPIED into the version's
`lora/` with a `lora.manifest.json` carrying the P2-13 graph-ready facts (caption policy
hash, captions hash, context digest, dataset hash), `version.lora` flips on, temp stays
until the explicit cleanup click, and the preview queues a sample gen with the fresh
un-promoted adapter.
"""

from __future__ import annotations

import json
import re
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


def _finished_trainer_job(client, asset, *, trigger="mara_lw", base_family=None):
    """Stage → queue → hand-finish the trainer job the way the wrapper would: the adapter
    lands in the run dir's <job_name>/ checkpoint layout + a trainer manifest in the
    job's out dir, and the parsed result points at both. `base_family` picks the trained
    base (sd35 needs its spike gate open — the caller sets it)."""
    from orchestrator.runner import RUNNER

    body = {"trigger_token": trigger, "steps": 60}
    if base_family:
        body["base_family"] = base_family
    staged = client.post(f"/assets/{asset['id']}/lora/stage", json=body).json()
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


def test_preview_rides_the_trainer_overlay_for_peft(client, monkeypatch):
    """Rig 2026-07-13 (`job_af29227d`): the preview's zimage worker failed with
    'PEFT backend is required for this method' — PEFT lives ONLY in the isolated
    trainer overlay (R103), never the shared venv. The overlay must ride the preview
    job's params (the job's own overlay first, rig default second) and the runner's
    cold spawn must prepend it to the worker's PYTHONPATH (source contract)."""
    import inspect
    from orchestrator import runner as runner_mod
    from orchestrator.runner import RUNNER

    asset = _curated_asset(client)
    monkeypatch.setenv("LOOM_TRAINER_OVERLAY", r"X:itk-overlay")
    jid, _ = _finished_trainer_job(client, asset)
    prev = client.post(f"/training/jobs/{jid}/preview", json={}).json()
    assert RUNNER.get(prev["job_id"])["params"]["runtime_overlay"] == r"X:itk-overlay"

    src = inspect.getsource(runner_mod.JobRunner._execute)
    assert 'params.get("runtime_overlay")' in src and "PYTHONPATH" in src

    # explicitly-empty overlay (no rig default) -> the param stays off the job
    monkeypatch.setenv("LOOM_TRAINER_OVERLAY", "")
    jid2, _ = _finished_trainer_job(client, asset)
    bare = client.post(f"/training/jobs/{jid2}/preview", json={}).json()
    assert "runtime_overlay" not in RUNNER.get(bare["job_id"])["params"]


def test_preview_scopes_to_the_version_grid_and_defaults_to_trained_res(client):
    """Rig 2026-07-15: the first real preview tile landed in the SANDBOX — the grid
    filters on requester_id == the VERSION id (the P1 /generate convention) but the
    submit used the asset id. And it silently rendered 1024² against a 512²-trained
    adapter. Preview now scopes to the version and defaults to the trained resolution;
    prompt/seed/size/weight are overridable; with_lora=false = the same-seed base A/B."""
    from orchestrator.runner import RUNNER

    asset = _curated_asset(client)
    jid, staged = _finished_trainer_job(client, asset)

    prev = client.post(f"/training/jobs/{jid}/preview", json={}).json()
    pjob = RUNNER.get(prev["job_id"])
    assert pjob["requester_id"] == staged["version_id"]          # the grid filter key
    assert pjob["params"]["width"] == 512 and pjob["params"]["height"] == 512

    tuned = client.post(f"/training/jobs/{jid}/preview", json={
        "prompt": "mara_lw, front view, full body, standing in a T-pose",
        "seed": 777, "width": 768, "height": 768, "lora_weight": 1.5, "num_steps": 30,
    }).json()
    tp = RUNNER.get(tuned["job_id"])["params"]
    assert tp["width"] == 768 and tp["lora_weight"] == 1.5 and tp["num_steps"] == 30
    assert tp["prompt"].endswith("T-pose") and tp["seed"] == 777

    base = client.post(f"/training/jobs/{jid}/preview",
                       json={"seed": 777, "with_lora": False}).json()
    bp = RUNNER.get(base["job_id"])["params"]
    assert "lora_path" not in bp and "runtime_overlay" not in bp   # bare base model
    assert bp["seed"] == 777                                       # same-seed comparison

    assert client.post(f"/training/jobs/{jid}/preview",
                       json={"width": 700}).status_code == 422     # ÷16 bound holds


def test_preview_pose_menu_is_vocabulary_backed_and_icon_keyed(client):
    """Author request 2026-08-08: the preview framing is a PICK, not a fixed portrait.
    Four of the five poses are real coverage cells, so their prompt must come from the
    FROZEN caption builder verbatim — that keeps the preview prompt in the same shape the
    adapter was trained on. `t_pose` is deliberately out-of-vocabulary (adding it to the
    frozen enums would break CONTRACT_VERSION and every caption hash), so it is flagged
    and carries a hand-written prompt. Icon keys match the M2.11 `bible/poses/` scheme."""
    from orchestrator import coverage, training

    asset = _curated_asset(client)
    r = client.get("/training/preview-poses",
                   params={"asset_id": asset["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    poses = {p["id"]: p for p in body["poses"]}

    assert set(poses) == {"t_pose", "full_body", "waist_up", "portrait", "face_closeup"}
    assert body["default"] == "portrait" and poses["portrait"]["default"] is True

    # the four cell poses render through the frozen builder, byte-for-byte
    trigger = body["trigger_token"]
    for pid, shot in (("full_body", "full_body"), ("waist_up", "waist_up"),
                      ("portrait", "portrait"), ("face_closeup", "face_closeup")):
        cell = {"shot_size": shot, "angle": "front",
                "expression": "neutral", "background": ""}
        assert poses[pid]["in_vocabulary"] is True
        assert poses[pid]["prompt"] == coverage.build_caption(cell, trigger)
        assert poses[pid]["pose_key"] == f"{shot}__front__neutral"

    # t_pose is the honest exception: flagged, hand-written, and NOT in the frozen vocab
    assert poses["t_pose"]["in_vocabulary"] is False
    assert "T-pose" in poses["t_pose"]["prompt"]
    assert "t_pose" not in coverage.SHOT_SIZES          # the contract stays frozen
    assert coverage.CONTRACT_VERSION == 1
    # every key is a legal bible pose key, so the L1 · Poses icon machinery accepts it
    for p in body["poses"]:
        assert re.fullmatch(r"[a-z0-9_]+__[a-z0-9_]+__[a-z0-9_]+", p["pose_key"])
        assert isinstance(p["has_icon"], bool)

    # the default pose reproduces the OLD hardcoded prompt exactly — no silent change
    assert training.preview_pose_prompt("portrait", "mara_lw") == \
        "mara_lw, front view, portrait, neutral expression"


def test_preview_pose_picks_the_framing_and_prompt_still_wins(client):
    """`pose` selects the framing; an explicit `prompt` overrides it; an unknown pose is
    a 400 rather than a silent fallback to the portrait."""
    from orchestrator.runner import RUNNER

    asset = _curated_asset(client)
    jid, _staged = _finished_trainer_job(client, asset)

    tpose = client.post(f"/training/jobs/{jid}/preview", json={"pose": "t_pose"}).json()
    assert "T-pose" in RUNNER.get(tpose["job_id"])["params"]["prompt"]

    full = client.post(f"/training/jobs/{jid}/preview", json={"pose": "full_body"}).json()
    assert RUNNER.get(full["job_id"])["params"]["prompt"] == \
        "mara_lw, front view, full body, neutral expression"

    # default (no pose) is unchanged from the pre-2026-08-08 fixed prompt
    plain = client.post(f"/training/jobs/{jid}/preview", json={}).json()
    assert RUNNER.get(plain["job_id"])["params"]["prompt"] == \
        "mara_lw, front view, portrait, neutral expression"

    # an explicit prompt beats the pose
    both = client.post(f"/training/jobs/{jid}/preview",
                       json={"pose": "t_pose", "prompt": "mara_lw, sitting"}).json()
    assert RUNNER.get(both["job_id"])["params"]["prompt"] == "mara_lw, sitting"

    assert client.post(f"/training/jobs/{jid}/preview",
                       json={"pose": "nope"}).status_code == 400


def test_stage_d_grid_shows_previews_instead_of_the_sandbox(client):
    """Rig 2026-08-08 (`job_9a2dad37`): the 2026-07-15 scoping fix corrected requester_id
    but the grid filter is a CONJUNCTION — `requester_id === active_version && stage ===
    gridStage` — and gridStage was only ever "A"|"B", so a stage-D preview matched nothing
    and fell through to the Sandbox. Stage D also rendered no grid at all. The submitted
    job must carry BOTH halves, and the FE must map stage D → its own grid."""
    from orchestrator.runner import RUNNER

    asset = _curated_asset(client)
    jid, staged = _finished_trainer_job(client, asset)
    prev = client.post(f"/training/jobs/{jid}/preview", json={}).json()
    pjob = RUNNER.get(prev["job_id"])
    assert pjob["requester_id"] == staged["version_id"]   # half 1 (fixed 2026-07-15)
    assert pjob["stage"] == "D"                           # half 2 — the missed one

    app_tsx = (Path(__file__).resolve().parents[2]
               / "app" / "src" / "App.tsx").read_text(encoding="utf-8")
    # gridStage maps D to "D" (not the "A"/"B" fallback that stranded the tile)
    assert 'stage === "D" ? "D"' in app_tsx
    # and stage D no longer short-circuits its grid to empty
    assert 'const stageCells = stage === "D" ? []' not in app_tsx


def test_stage_d_grid_admits_only_image_producing_jobs(client):
    """Stage D is shared by three job kinds and only the M6 preview makes an image. Now
    that stage D HAS a grid, the other two must not surface as blank "—" tiles:

    - the **readiness scan** (`identity`/`score`) is version-scoped like the preview, so
      only its mode keeps it out — this is the one the filter actually earns its keep on;
    - the **trainer run** is stage D but requests as the ASSET, so it never matched the
      grid anyway (it is listed inside the Train panel, found via `profile_version_id`)."""
    from orchestrator.runner import RUNNER

    asset = _curated_asset(client)
    jid, staged = _finished_trainer_job(client, asset)
    version_id = staged["version_id"]

    trainer = RUNNER.get(jid)
    scan = RUNNER.get(client.post(f"/assets/{asset['id']}/readiness/embed",
                                  json={"version_id": version_id}).json()["job_id"])
    preview = RUNNER.get(client.post(f"/training/jobs/{jid}/preview",
                                     json={}).json()["job_id"])

    for j in (trainer, scan, preview):
        assert j["stage"] == "D"
    # scan + preview share the grid's version scope — only mode separates them
    assert scan["requester_id"] == version_id and preview["requester_id"] == version_id
    assert scan["mode"] == "score" and not scan["params"].get("prompt")
    assert preview["pipeline"] == "zimage" and preview["mode"] == "t2i"
    # the trainer is out on requester alone, and the panel finds it by version instead
    assert trainer["pipeline"] == "zimage_trainer"
    assert trainer["requester_id"] == asset["id"] != version_id
    assert trainer["profile_version_id"] == version_id

    app_tsx = (Path(__file__).resolve().parents[2]
               / "app" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert 'j.pipeline !== "zimage_trainer" && j.mode !== "score"' in app_tsx
    assert 'gridStage !== "D" || makesAnImage(j)' in app_tsx


def test_sd35_preview_uses_the_sd35_pipeline_after_the_spike_go(client, monkeypatch):
    """M5 spike GO 2026-08-08 (`job_a5edadc9`: sd35-medium, 500 steps @ 512², 342 s) — sd35
    trains, so it must also PREVIEW. The author hit exactly this wall: a finished sd35 run
    could not be eyeballed because `preview_request` was hardwired to zimage.

    An adapter is trained against ONE base, so the preview pipeline must FOLLOW the trained
    family — previewing an sd35 LoRA through the zimage worker would just render zimage's
    prior (the 2026-07-15 resolution lesson in another disguise). The overlay still rides
    along, because sd35's `load_lora_weights` needs PEFT just as zimage's does (R103)."""
    from orchestrator.runner import RUNNER

    monkeypatch.setenv("LOOM_TRAINER_SD35_GO", "1")     # the spike passed
    monkeypatch.setenv("LOOM_TRAINER_OVERLAY", r"X:itk-overlay")
    asset = _curated_asset(client)
    jid, staged = _finished_trainer_job(client, asset, base_family="sd35")

    prev = client.post(f"/training/jobs/{jid}/preview", json={})
    assert prev.status_code == 200, prev.text
    pjob = RUNNER.get(prev.json()["job_id"])
    assert pjob["pipeline"] == "sd35"                           # NOT zimage
    assert pjob["params"]["model_name"] == "sd3.5-medium"
    assert pjob["params"]["lora_path"].endswith(".safetensors")
    assert pjob["params"]["runtime_overlay"] == r"X:itk-overlay"   # PEFT, same as zimage
    assert pjob["stage"] == "D" and pjob["requester_id"] == staged["version_id"]
    # the pose picker + trained-res default work identically across families
    assert pjob["params"]["width"] == 512 and pjob["params"]["height"] == 512
    assert pjob["params"]["prompt"] == "mara_lw, front view, portrait, neutral expression"

    # the sd35 worker + catalog + adapter must actually carry the three LoRA flags, or the
    # job above would be silently dropped on the floor at argv time
    from orchestrator import model_catalog
    from orchestrator.adapters import sd35 as sd35_adapter

    names = {p["name"] for p in model_catalog.CATALOG["sd35"]["params"]}
    assert {"lora_path", "lora_name", "lora_weight"} <= names
    assert {"lora_path", "lora_name", "lora_weight"} <= set(sd35_adapter.WIRED_PARAMS)
    worker = (Path(__file__).resolve().parents[2] / "pipelines" / "multistack" / "src"
              / "pipeline" / "sd35" / "stage1_load_pipeline.py").read_text(encoding="utf-8")
    assert "load_lora_weights" in worker and "set_adapters" in worker


def test_preview_refuses_a_family_with_no_inference_lora_path(client):
    """The guard is a roster, not a zimage special-case: a family that cannot load a LoRA
    at inference must refuse loudly rather than preview through the wrong base."""
    from orchestrator import training
    from orchestrator.runner import RUNNER

    assert set(training.PREVIEW_PIPELINES) == {"zimage", "sd35"}
    asset = _curated_asset(client)
    jid, _staged = _finished_trainer_job(client, asset)
    RUNNER.get(jid)["params"]["base_family"] = "flux2"      # never trained/loadable here
    r = client.post(f"/training/jobs/{jid}/preview", json={})
    assert r.status_code == 400 and "flux2" in r.text
