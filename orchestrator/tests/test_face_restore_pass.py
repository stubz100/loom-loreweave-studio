"""M6 — GFPGAN face-restore pass (no GPU): the third pass family. Catalog-served
restore params, the chained io-pass job (items {"input"}, blend rides the params),
order (identity BEFORE restore — the lock first, then GFPGAN fixes the 128px swap
softness), tool-scoped weight gate, batch-manifest parse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator import components
from orchestrator import model_catalog as mc
from orchestrator.adapters import face_restore
from orchestrator.adapters.base import JobSpec
from orchestrator.config import CONFIG

_CELL = {"shot_size": "portrait", "angle": "front", "expression": "neutral",
         "background": ""}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


# --- catalog + extraction ----------------------------------------------------------

def test_restore_params_served_on_every_surface():
    for p in ("zimage", "sd35", "multi"):
        by_name = {s["name"]: s for s in mc.params(p)}
        assert by_name["restore"]["post"] is True
        assert by_name["restore_blend"]["post"] is True


def test_generate_restore_dry_run_builds_spec(client):
    r = client.post("/generate", json={
        "pipeline": "zimage", "prompt": "a ranger", "count": 1,
        "params": {"restore": True, "restore_blend": 0.6}, "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    # never a worker FLAG (the conftest's per-test out dir contains the test's own name,
    # so a naive substring check would trip on --output-dir)
    assert not any(a.startswith("--restore") for a in body["argv"])
    (p,) = body["post_passes"]
    assert p == {"pass": "restore", "backend": "face_restore", "blend": 0.6}


def test_restore_footgun_422(client):
    r = client.post("/generate", json={
        "pipeline": "zimage", "prompt": "x",
        "params": {"restore_blend": 0.5}, "dry_run": True})
    assert r.status_code == 422 and "restore" in r.text


def test_restore_weight_gate_412(client, monkeypatch):
    from orchestrator.runner import RUNNER
    monkeypatch.setattr(components, "image_model_present", lambda repo_id: True)
    monkeypatch.setattr(components, "postproc_weights_status",
                        lambda tool, variant=None: (False, [{"id": "gfpgan-1.4",
                                                             "repo_id": "x", "gated": False,
                                                             "pipeline": tool}]))
    RUNNER.pause()
    r = client.post("/generate", json={
        "pipeline": "zimage", "prompt": "x", "params": {"restore": True}})
    assert r.status_code == 412, r.text
    assert "postproc=face_restore" in r.text


# --- order: clean/polish → identity → restore (the lock first, restore final) -------

def test_stage_b_identity_inserts_before_restore(client):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    RUNNER.pause()
    a = assets.create_asset(ws, name="OrderAsset")["profile"]
    out = ws.out_dir / "job_ord01"
    out.mkdir(parents=True, exist_ok=True)
    (out / "face.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "p"},
                        batch_id="bat_o", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="A")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_ord01/face.png",
                                  "output_names": ["job_ord01/face.png"], "seed": 1}
    assets.star_candidate(ws, a["id"], job_id=jid, source_output="job_ord01/face.png",
                          version_id=a["active_version"], pipeline="zimage", seed=1)
    rr = client.post(f"/assets/{a['id']}/anchor",
                     json={"job_id": jid, "output": "job_ord01/face.png"})
    assert rr.status_code == 200, rr.text
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x",
                          "identity": True,                       # explicit (unverified)
                          "params": {"polish": True, "restore": True},
                          "dry_run": True})
    assert r.status_code == 200, r.text
    assert [p["pass"] for p in r.json()["post_passes"]] == ["polish", "identity", "restore"]


# --- runner: the chained restore job ---------------------------------------------------

def test_submit_chained_restore_builds_io_job(client):
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    parent = {
        "id": "job_respar01", "batch_id": "bat_r1", "requester_id": "ver_r",
        "profile_version_id": "ver_r", "stage": "B",
        "params": {"prompt": "p", "seed": 4},
        "post_passes": [{"pass": "restore", "backend": "face_restore", "blend": 0.7}],
        "result": {"output_names": ["job_respar01/a.png"],
                   "output_meta": {"job_respar01/a.png": {"coverage_cell": _CELL,
                                                          "seed": 11}}},
    }
    RUNNER._submit_chained(parent)
    chained = [j for j in RUNNER.jobs.values() if j.get("chained_from") == "job_respar01"]
    assert len(chained) == 1
    j = chained[0]
    assert j["pipeline"] == "face_restore" and j["mode"] == "restore"
    assert j["pass"] == "restore" and j["params"]["blend"] == 0.7
    items = j["params"]["batch_items"]
    assert items[0]["input"].replace("\\", "/").endswith("job_respar01/a.png")
    assert "prompt" not in items[0] and "init_image" not in items[0]
    assert items[0]["meta"]["coverage_cell"] == _CELL and items[0]["seed"] == 11
    RUNNER.jobs.pop(j["id"], None)


# --- adapter -----------------------------------------------------------------------------

def test_build_argv_writes_inputs_file(tmp_path):
    spec = JobSpec(pipeline="face_restore", mode="restore",
                   params={"batch_items": [{"input": "F:/out/a.png", "seed": 3}],
                           "blend": 0.65},
                   output_dir=tmp_path)
    argv = face_restore.build_argv(spec, "python", Path("x/postproc/face_restore/run_pipeline.py"))
    assert "--inputs-file" in argv
    payload = json.loads((tmp_path / "inputs.json").read_text(encoding="utf-8"))
    assert payload["blend"] == 0.65 and payload["model_name"] == "gfpgan-1.4"
    assert payload["items"][0]["input"] == "F:/out/a.png"


def test_parse_result_reads_batch_manifest(tmp_path):
    out = tmp_path / "out" / "job_fr1"
    out.mkdir(parents=True)
    png = out / "face_restore_x_i000_s0.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    (out / "face_restore_batch_20260611_230000.json").write_text(json.dumps({
        "kind": "jobs_batch", "pipeline": "face_restore", "status": "completed",
        "count": 1, "ok": 1, "failed": 0, "skipped": 0, "total_duration_s": 1.0,
        "items": [{"index": 0, "status": "ok", "seed": 0, "prompt": None,
                   "output_path": str(png), "manifest_path": "",
                   "meta": {"restore": "restored", "faces": 1}, "error": ""}]}),
        encoding="utf-8")
    rec = face_restore.parse_result(0, "", "", out)
    assert rec.ok is True
    assert rec.outputs_meta[0]["restore"] == "restored"
    assert rec.outputs_meta[0]["faces"] == 1


def test_resolve_script_finds_vendored_worker():
    p = face_restore.resolve_script(CONFIG.pipeline_roots)
    assert p is not None and "face_restore" in str(p)
