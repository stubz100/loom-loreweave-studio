"""M4 — face anchor (R94) + identity-lock pass (R86/R93) — no GPU.

Spike-validated design (journal M4): a ReActor-class inswapper swap to the per-version
anchor, batch-shaped (one face-stack load, per-item streaming, ⏹), chained as a post-pass
over Stage-B output — ON by default once an anchor exists, opt-out.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator import components
from orchestrator.adapters import identity
from orchestrator.adapters.base import JobSpec
from orchestrator.config import CONFIG


# --- adapter: inputs.json + batch-manifest parse ----------------------------------------

def test_build_argv_writes_inputs_file(tmp_path):
    spec = JobSpec(pipeline="identity", mode="lock",
                   params={"anchor_image": "F:/v1/faces/anchor.png",
                           "batch_items": [{"input": "F:/out/a.png", "seed": 7,
                                            "meta": {"coverage_cell": {"x": 1}}}],
                           "min_det_score": 0.6},
                   output_dir=tmp_path)
    argv = identity.build_argv(spec, "python", Path("x/postproc/identity/run_pipeline.py"))
    assert "--inputs-file" in argv
    payload = json.loads((tmp_path / "inputs.json").read_text(encoding="utf-8"))
    assert payload["anchor"] == "F:/v1/faces/anchor.png"
    assert payload["min_det_score"] == 0.6
    assert payload["items"][0]["input"] == "F:/out/a.png"
    assert payload["items"][0]["meta"]["coverage_cell"] == {"x": 1}


def test_parse_result_reads_identity_batch_manifest(tmp_path):
    out = tmp_path / "out" / "job_id1"
    out.mkdir(parents=True)
    pngs = []
    for i in range(2):
        p = out / f"identity_x_i{i:03d}_s{i}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        pngs.append(str(p))
    (out / "identity_batch_20260611_170000.json").write_text(json.dumps({
        "kind": "jobs_batch", "pipeline": "identity", "status": "completed",
        "count": 2, "ok": 2, "failed": 0, "skipped": 0, "total_duration_s": 3.2,
        "items": [
            {"index": 0, "status": "ok", "seed": 0, "prompt": None,
             "output_path": pngs[0], "manifest_path": "",
             "meta": {"identity": "locked", "anchor_cos": 0.87,
                      "coverage_cell": {"angle": "front"}}, "error": ""},
            {"index": 1, "status": "ok", "seed": 1, "prompt": None,
             "output_path": pngs[1], "manifest_path": "",
             "meta": {"identity": "no_face_passthrough"}, "error": ""},
        ]}), encoding="utf-8")
    rec = identity.parse_result(0, "", "", out)
    assert rec.ok is True and rec.manifest_status == "completed"
    assert len(rec.outputs) == 2
    assert rec.outputs_meta[0]["identity"] == "locked"
    assert rec.outputs_meta[0]["anchor_cos"] == 0.87            # P2 readiness reads this
    assert rec.outputs_meta[0]["coverage_cell"] == {"angle": "front"}   # curation survives
    assert rec.outputs_meta[1]["identity"] == "no_face_passthrough"


def test_parse_result_without_manifest_is_honest(tmp_path):
    rec = identity.parse_result(2, "", "boom", tmp_path)
    assert rec.ok is False and "no identity batch manifest" in (rec.error or "")


# --- runner: the chained identity pass ----------------------------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def test_submit_chained_identity_builds_lock_job(client):
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    cell = {"shot_size": "portrait", "angle": "front", "expression": "neutral",
            "background": ""}
    parent = {
        "id": "job_idpar001", "batch_id": "bat_id1", "requester_id": "ver_i",
        "profile_version_id": "ver_i", "stage": "B",
        "params": {"prompt": "p", "width": 1024, "height": 1024, "seed": 3},
        "post_passes": [{"pass": "identity", "backend": "identity",
                         "anchor": "F:/v1/faces/anchor.png", "min_det_score": 0.5}],
        "result": {"output_names": ["job_idpar001/a.png", "job_idpar001/b.png"],
                   "output_meta": {"job_idpar001/a.png":
                                   {"coverage_cell": cell, "seed": 9}}},
    }
    RUNNER._submit_chained(parent)
    chained = [j for j in RUNNER.jobs.values() if j.get("chained_from") == "job_idpar001"]
    assert len(chained) == 1
    j = chained[0]
    assert j["pipeline"] == "identity" and j["mode"] == "lock" and j["pass"] == "identity"
    assert j["params"]["anchor_image"] == "F:/v1/faces/anchor.png"
    items = j["params"]["batch_items"]
    assert len(items) == 2
    assert items[0]["input"].replace("\\", "/").endswith("job_idpar001/a.png")
    assert "init_image" not in items[0] and "prompt" not in items[0]   # identity vocabulary
    assert items[0]["meta"]["coverage_cell"] == cell                   # curation survives
    assert items[0]["seed"] == 9 and items[1]["seed"] == 3
    assert j["requester_id"] == "ver_i" and j["stage"] == "B"
    RUNNER.jobs.pop(j["id"], None)


# --- assets + API: the face-anchor record --------------------------------------------------

def _asset_with_done_job(ws, *, name="AnchorAsset"):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    a = assets.create_asset(ws, name=name)["profile"]
    out = ws.out_dir / "job_face01"
    out.mkdir(parents=True, exist_ok=True)
    (out / "face.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "portrait"},
                        batch_id="bat_f1", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="A")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_face01/face.png",
                                  "output_names": ["job_face01/face.png"], "seed": 5}
    return a, jid


def test_anchor_set_serve_clear_roundtrip(client):
    from orchestrator.runner import RUNNER
    a, jid = _asset_with_done_job(RUNNER.workspace)
    r = client.post(f"/assets/{a['id']}/anchor",
                    json={"job_id": jid, "output": "job_face01/face.png"})
    assert r.status_code == 200, r.text
    anchor = r.json()["anchor"]
    assert anchor["file"].startswith("anchor") and anchor["job_id"] == jid
    # the copy is self-contained in the version's faces/ dir + served
    assert client.get(f"/assets/{a['id']}/anchor/file").status_code == 200
    # clear = opt out (R93)
    r2 = client.post(f"/assets/{a['id']}/anchor", json={"job_id": None})
    assert r2.status_code == 200 and r2.json()["anchor"] is None
    assert client.get(f"/assets/{a['id']}/anchor/file").status_code == 404


def test_anchor_rejects_foreign_job_and_unknown_output(client):
    from orchestrator.runner import RUNNER
    a, jid = _asset_with_done_job(RUNNER.workspace)
    b, _ = _asset_with_done_job(RUNNER.workspace, name="OtherAsset")
    # job owned by asset A can't anchor asset B (scope guard, 409)
    r = client.post(f"/assets/{b['id']}/anchor",
                    json={"job_id": jid, "output": "job_face01/face.png"})
    assert r.status_code == 409, r.text
    # an output the job didn't produce → 422
    r2 = client.post(f"/assets/{a['id']}/anchor",
                     json={"job_id": jid, "output": "job_face01/not_mine.png"})
    assert r2.status_code == 422, r2.text


# --- stage_b: identity pass default-on / opt-out / gates -----------------------------------

def _asset_with_hero_and_anchor(client, ws):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    a, jid = _asset_with_done_job(ws, name="IdHero")
    assets.star_candidate(ws, a["id"], job_id=jid, source_output="job_face01/face.png",
                          version_id=a["active_version"], pipeline="zimage", seed=5)
    r = client.post(f"/assets/{a['id']}/anchor",
                    json={"job_id": jid, "output": "job_face01/face.png"})
    assert r.status_code == 200, r.text
    return a


def _verify_anchor(ws, a):
    """A done+ok identity job using THIS anchor, started after it was picked — the
    computed verification the default-on rule requires (M4 review, Medium)."""
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    anchor_path = assets.anchor_file_path(ws, a["id"])
    jid = RUNNER.submit(pipeline="identity", mode="lock",
                        params={"anchor_image": str(anchor_path), "batch_items": [{}],
                                "prompt": "[verify]"},
                        batch_id="bat_ver", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="B")
    RUNNER.jobs[jid]["status"] = "done"
    # A REAL verification = the run actually locked ≥1 face (output_meta carries identity=locked);
    # a passthrough-only run (no detectable face) must NOT verify the anchor.
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_names": ["o.png"],
                                  "output_meta": {"o.png": {"identity": "locked"}}}
    return jid


def test_stage_b_identity_unverified_anchor_defaults_off(client):
    """M4 review (Medium): a freshly-picked anchor is UNVERIFIED (it may have no
    detectable face — the worker only finds out at run time), so default-on must wait;
    the response says why, and an explicit identity:true is the verification run."""
    from orchestrator.runner import RUNNER
    a = _asset_with_hero_and_anchor(client, RUNNER.workspace)
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x", "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["identity"] is False and body["post_passes"] == []
    assert "UNVERIFIED" in (body["identity_note"] or "")
    # explicit identity:true is allowed unverified — that run verifies the anchor
    r2 = client.post(f"/assets/{a['id']}/stage-b",
                     json={"preset": "npc_lite", "character_clause": "x",
                           "identity": True, "dry_run": True})
    assert r2.status_code == 200 and r2.json()["identity"] is True


def test_stage_b_identity_default_on_and_appended_last(client):
    from orchestrator.runner import RUNNER
    a = _asset_with_hero_and_anchor(client, RUNNER.workspace)
    _verify_anchor(RUNNER.workspace, a)            # verified → default-on engages (R93)
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "a ranger",
                          "params": {"polish": True}, "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["identity"] is True and body["identity_note"] is None
    passes = body["post_passes"]
    assert [p["pass"] for p in passes] == ["polish", "identity"]   # lock is the final word
    ident = passes[-1]
    assert ident["backend"] == "identity" and ident["min_det_score"] == 0.5
    assert ident["anchor"].replace("\\", "/").endswith("/faces/anchor.png")


def test_anchor_verification_is_durable(client):
    """M5-prep review (Medium): the verification fact must survive queue pruning — the
    runner's completion observer stamps verified_at/verified_by_job on version.anchor,
    and default-on reads the stamp first (job history only as fallback)."""
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a = _asset_with_hero_and_anchor(client, ws)
    jid = _verify_anchor(ws, a)
    assert RUNNER._observer is not None             # wired by the lifespan
    RUNNER._observer(RUNNER.jobs[jid])              # what finalize fires on ok jobs
    detail = assets.get_asset(ws, a["id"])
    v = next(x for x in detail["versions"] if x["id"] == a["active_version"])
    assert v["anchor"]["verified_at"] and v["anchor"]["verified_by_job"] == jid
    # prune the verifying job — default-on STILL engages (the durable stamp)
    RUNNER.jobs.pop(jid, None)
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x", "dry_run": True})
    assert r.status_code == 200, r.text
    assert r.json()["identity"] is True


def test_passthrough_identity_does_not_verify_anchor(client):
    """A faceless/heavily-stylized anchor → the worker passes images through (locks NOTHING).
    Such an 'ok' run must NOT verify the anchor (else default-on identity arms a permanent
    no-op): neither the durable observer nor the stage-b history scan accepts it."""
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a = _asset_with_hero_and_anchor(client, ws)
    anchor_path = assets.anchor_file_path(ws, a["id"])
    jid = RUNNER.submit(pipeline="identity", mode="lock",
                        params={"anchor_image": str(anchor_path), "batch_items": [{}]},
                        batch_id="bat_pt", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="B")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {
        "ok": True, "output_names": ["o.png"],
        "output_meta": {"o.png": {"identity": "anchor_no_face_passthrough"}}}
    RUNNER._observer(RUNNER.jobs[jid])
    detail = assets.get_asset(ws, a["id"])
    v = next(x for x in detail["versions"] if x["id"] == a["active_version"])
    assert not (v["anchor"] or {}).get("verified_at")        # observer didn't verify
    # the stage-b history scan also rejects the passthrough job → identity defaults OFF
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x", "dry_run": True})
    assert r.status_code == 200 and r.json()["identity"] is False


def test_stage_b_lazily_promotes_legacy_verification(client):
    """A verification that predates the durable stamp (only in job history) is promoted
    to version.anchor on the next stage-b check — then survives pruning too."""
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a = _asset_with_hero_and_anchor(client, ws)
    jid = _verify_anchor(ws, a)                     # history only — no observer call
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x", "dry_run": True})
    assert r.status_code == 200 and r.json()["identity"] is True
    detail = assets.get_asset(ws, a["id"])
    v = next(x for x in detail["versions"] if x["id"] == a["active_version"])
    assert v["anchor"]["verified_at"] and v["anchor"]["verified_by_job"] == jid


def test_stage_b_identity_repick_invalidates_verification(client):
    """Re-picking the anchor (same faces/anchor.png path, NEW content) must reset the
    computed verification — only runs started AFTER the re-pick count."""
    import time
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    a = _asset_with_hero_and_anchor(client, RUNNER.workspace)
    _verify_anchor(RUNNER.workspace, a)
    time.sleep(0.01)                                # set_at must pass the old job's created_at
    r0 = client.post(f"/assets/{a['id']}/anchor",   # re-pick (same source, fresh set_at)
                     json={"job_id": RUNNER.jobs and next(
                         j["id"] for j in RUNNER.jobs.values()
                         if j.get("pipeline") == "zimage" and j.get("status") == "done"
                         and j.get("profile_version_id") == a["active_version"]),
                           "output": "job_face01/face.png"})
    assert r0.status_code == 200, r0.text
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x", "dry_run": True})
    assert r.status_code == 200 and r.json()["identity"] is False


def test_stage_b_identity_opt_out_and_require(client):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    a = _asset_with_hero_and_anchor(client, RUNNER.workspace)
    # explicit opt-out (R93)
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x",
                          "identity": False, "dry_run": True})
    assert r.status_code == 200 and r.json()["post_passes"] == []
    # identity=true without an anchor → 422 with the hint
    assets.clear_anchor(RUNNER.workspace, a["id"])
    r2 = client.post(f"/assets/{a['id']}/stage-b",
                     json={"preset": "npc_lite", "character_clause": "x",
                           "identity": True, "dry_run": True})
    assert r2.status_code == 422 and "anchor" in r2.text
    # and with no anchor, the default is OFF (no identity pass)
    r3 = client.post(f"/assets/{a['id']}/stage-b",
                     json={"preset": "npc_lite", "character_clause": "x", "dry_run": True})
    assert r3.status_code == 200 and r3.json()["post_passes"] == []


def test_keep_ref_blocks_pre_lock_outputs(client):
    """M4 review (High): a done job with PENDING post-passes is not the end of its chain —
    curating its (pre-identity-lock) outputs poisons the ref_set/P2 corpus. 409 unless the
    explicit allow_unlocked escape is set (the deliberate stopped-chain case)."""
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    RUNNER.pause()
    a = assets.create_asset(ws, name="PreLock")["profile"]
    out = ws.out_dir / "job_prelock1"
    out.mkdir(parents=True, exist_ok=True)
    (out / "img0.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    cell = {"shot_size": "portrait", "angle": "front", "expression": "neutral",
            "background": ""}
    jid = RUNNER.submit(pipeline="zimage", mode="img2img",
                        params={"prompt": "[dataset]", "batch_items": [{}]},
                        batch_id="bat_pl", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="B",
                        post_passes=[{"pass": "identity", "backend": "identity",
                                      "anchor": "x", "min_det_score": 0.5}])
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_prelock1/img0.png",
                                  "output_names": ["job_prelock1/img0.png"],
                                  "output_meta": {"job_prelock1/img0.png":
                                                  {"coverage_cell": cell}}}
    r = client.post(f"/assets/{a['id']}/refs/keep", json={"job_id": jid})
    assert r.status_code == 409, r.text
    assert "identity" in r.text and "pending" in r.text
    # explicit escape hatch — e.g. a ⏹-stopped chain curated deliberately
    r2 = client.post(f"/assets/{a['id']}/refs/keep",
                     json={"job_id": jid, "allow_unlocked": True})
    assert r2.status_code == 200, r2.text


def test_buffalo_pack_gated_on_filesystem(client, monkeypatch, tmp_path):
    """M4 review (Medium): the buffalo_l detector pack is part of the identity gate —
    probed on the FILESYSTEM at the worker's insightface root (it's a github-release
    zip, not an HF repo), so a fresh rig 412s with a fetch hint instead of dying mid-job."""
    monkeypatch.setenv("LOOM_INSIGHTFACE_ROOT", str(tmp_path / "iface"))
    ok, missing = components.postproc_weights_status("identity")
    assert ok is False
    assert any(m.get("id") == "buffalo-l" for m in missing)
    # hydrating the probe file flips the entry to present
    probe = tmp_path / "iface" / "models" / "buffalo_l" / "w600k_r50.onnx"
    probe.parent.mkdir(parents=True)
    probe.write_bytes(b"onnx")
    _ok2, missing2 = components.postproc_weights_status("identity")
    assert not any(m.get("id") == "buffalo-l" for m in missing2)


def test_stage_b_identity_weight_gate_412(client, monkeypatch):
    from orchestrator.runner import RUNNER
    monkeypatch.setattr(components, "image_model_present", lambda repo_id: True)
    monkeypatch.setattr(components, "postproc_weights_status",
                        lambda tool, variant=None: (False, [{"id": "inswapper-128",
                                                             "repo_id": "x", "gated": False,
                                                             "pipeline": "identity"}]))
    a = _asset_with_hero_and_anchor(client, RUNNER.workspace)
    # explicit identity:true (the unverified anchor defaults identity OFF — correct;
    # the verification run itself must still clear the weight gate)
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x", "identity": True})
    assert r.status_code == 412, r.text
    assert "postproc=identity" in r.text
