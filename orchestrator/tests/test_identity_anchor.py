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


def test_stage_b_identity_default_on_and_appended_last(client):
    from orchestrator.runner import RUNNER
    a = _asset_with_hero_and_anchor(client, RUNNER.workspace)
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "a ranger",
                          "params": {"polish": True}, "dry_run": True})
    assert r.status_code == 200, r.text
    passes = r.json()["post_passes"]
    assert [p["pass"] for p in passes] == ["polish", "identity"]   # lock is the final word
    ident = passes[-1]
    assert ident["backend"] == "identity" and ident["min_det_score"] == 0.5
    assert ident["anchor"].replace("\\", "/").endswith("/faces/anchor.png")


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


def test_stage_b_identity_weight_gate_412(client, monkeypatch):
    from orchestrator.runner import RUNNER
    monkeypatch.setattr(components, "image_model_present", lambda repo_id: True)
    monkeypatch.setattr(components, "postproc_weights_status",
                        lambda tool, variant=None: (False, [{"id": "inswapper-128",
                                                             "repo_id": "x", "gated": False,
                                                             "pipeline": "identity"}]))
    a = _asset_with_hero_and_anchor(client, RUNNER.workspace)
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x"})
    assert r.status_code == 412, r.text
    assert "postproc=identity" in r.text
