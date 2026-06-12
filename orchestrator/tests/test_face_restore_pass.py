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
        "params": {"prompt": "p", "seed": 4, "width": 1280, "height": 720},
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
    # display dims carried from the parent (user finding 2026-06-12: io-pass tiles
    # rendered 16:9 regardless of the actual image)
    assert j["params"]["width"] == 1280 and j["params"]["height"] == 720
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


def test_build_argv_portrait_mode_flag(tmp_path):
    """mode 'portrait' rides into inputs.json — the worker outputs the restored aligned
    512² crop of the largest face instead of the in-place fix (M6.1 anchor derivation)."""
    for mode, want in (("portrait", True), ("restore", False)):
        out = tmp_path / mode
        out.mkdir()
        spec = JobSpec(pipeline="face_restore", mode=mode,
                       params={"batch_items": [{"input": "F:/x.png"}]}, output_dir=out)
        face_restore.build_argv(spec, "python", Path("x/run_pipeline.py"))
        payload = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
        assert payload["portrait"] is want


def test_derive_anchor_endpoint_queues_portrait_job(client, monkeypatch):
    """M6.1 (user idea): derive a dedicated face portrait from an owned output — the
    result is a normal Stage-A job output, so the existing ⚓ anchor flow picks it up."""
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    monkeypatch.setattr(components, "postproc_weights_status",
                        lambda tool, variant=None: (True, []))
    ws = RUNNER.workspace
    RUNNER.pause()
    a = assets.create_asset(ws, name="PortraitAsset")["profile"]
    out = ws.out_dir / "job_full01"
    out.mkdir(parents=True, exist_ok=True)
    (out / "fullbody.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "p"},
                        batch_id="bat_p", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="A")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_full01/fullbody.png",
                                  "output_names": ["job_full01/fullbody.png"]}
    r = client.post(f"/assets/{a['id']}/anchor/derive", json={"job_id": jid})
    assert r.status_code == 200, r.text
    job = RUNNER.get(r.json()["job_id"])
    assert job["pipeline"] == "face_restore" and job["mode"] == "portrait"
    assert job["params"]["batch_items"][0]["input"].replace("\\", "/").endswith(
        "job_full01/fullbody.png")
    assert job["params"]["width"] == 512 and job["params"]["height"] == 512
    assert job["stage"] == "A" and job["requester_id"] == a["active_version"]
    # ownership guard: another asset can't derive from this job
    b = assets.create_asset(ws, name="OtherPortrait")["profile"]
    r2 = client.post(f"/assets/{b['id']}/anchor/derive", json={"job_id": jid})
    assert r2.status_code == 409, r2.text


def test_fetch_postproc_is_single_file_for_filename_entries(monkeypatch):
    """M6 review (Medium): facefusion/models-3.0.0 mirrors DOZENS of models — the fetch
    must request exactly the one file the worker loads (hf_hub_download w/ filename),
    never an unrestricted snapshot."""
    import huggingface_hub
    calls: list[tuple] = []
    monkeypatch.setattr(huggingface_hub, "hf_hub_download",
                        lambda repo_id, filename=None, token=None, **kw:
                        calls.append(("file", repo_id, filename)) or "x")
    monkeypatch.setattr(huggingface_hub, "snapshot_download",
                        lambda repo_id, token=None, **kw:
                        calls.append(("snapshot", repo_id, None)) or "x")
    # only buffalo is "present" → exactly the gfpgan entry needs fetching
    monkeypatch.setattr(components, "_entry_present",
                        lambda e: bool(e.get("insightface_pack")))
    components.fetch_postproc("face_restore")
    assert ("file", "facefusion/models-3.0.0", "gfpgan_1.4.onnx") in calls
    assert not any(kind == "snapshot" for kind, *_ in calls)
