"""BiRefNet matting + mixed Stage-B realization (P1/M3.5) — no GPU.

The first postproc-class adapter: hero ★ → subject matte / cutout / **bgmask**, and
`realize="mixed"` Stage-B expansion that fires TWO batch jobs (img2img sweep cells +
inpaint cells repainting the background around the held subject — identity-safe,
restores the §7.1 background-diversity axis).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator import components
from orchestrator import model_catalog as mc
from orchestrator.adapters import birefnet
from orchestrator.adapters.base import JobSpec
from orchestrator.config import CONFIG


# --- adapter: script resolution + argv ------------------------------------------------

def test_resolve_script_finds_vendored_worker():
    p = birefnet.resolve_script(CONFIG.pipeline_roots)
    assert p is not None and p.name == "run_pipeline.py" and "birefnet" in str(p)


def test_build_argv_maps_catalog_flags(tmp_path):
    spec = JobSpec(pipeline="birefnet", mode="matte",
                   params={"input_image": "F:/hero.png", "dilate_px": 16,
                           "threshold": 0.6, "model_name": "birefnet"},
                   output_dir=tmp_path)
    argv = birefnet.build_argv(spec, "python", Path("x/postproc/birefnet/run_pipeline.py"))
    assert argv[argv.index("--input") + 1] == "F:/hero.png"
    assert argv[argv.index("--dilate-px") + 1] == "16"
    assert argv[argv.index("--threshold") + 1] == "0.6"
    assert "--output-dir" in argv


# --- adapter: manifest-as-truth parse ---------------------------------------------------

def _write_manifest(out: Path, *, fail_stage: str | None = None):
    out.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for role in ("matte", "cutout", "bgmask"):
        png = out / f"birefnet_x_{role}.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        artifacts.append({"path": str(png), "role": role, "kind": "image/png"})
    stages = []
    for name in ("load", "matte", "artifacts"):
        st = {"name": name, "status": "completed", "error": None}
        if name == fail_stage:
            st.update(status="failed", error="boom")
        stages.append(st)
    (out / "birefnet_20260611_120000.json").write_text(json.dumps({
        "module": "birefnet", "pipeline_duration_s": 7.5,
        "stages": stages, "artifacts": artifacts,
        "output_path": artifacts[-1]["path"]}), encoding="utf-8")


def test_parse_result_roles_ride_outputs_meta(tmp_path):
    out = tmp_path / "out" / "job_m1"
    _write_manifest(out)
    rec = birefnet.parse_result(0, "", "", out)
    assert rec.ok is True and rec.manifest_status == "completed"
    assert len(rec.outputs) == 3
    assert [m["role"] for m in rec.outputs_meta] == ["matte", "cutout", "bgmask"]
    assert rec.outputs[2].endswith("_bgmask.png")


def test_parse_result_failed_stage_is_honest(tmp_path):
    out = tmp_path / "out" / "job_m2"
    _write_manifest(out, fail_stage="matte")
    rec = birefnet.parse_result(0, "", "", out)
    assert rec.ok is False and rec.manifest_status == "failed"
    assert "matte" in (rec.error or "")


def test_progress_markers_and_output_collection():
    assert birefnet.progress("[stage1] Pipeline loaded in 5.2s") == 0.6
    assert birefnet.progress("[stage2] Matted in 1.1s -- 1024x1024") == 0.9
    assert birefnet.progress("[done] Pipeline completed in 7.5s") == 1.0
    assert birefnet.collect_output("  Image: F:/p/out/job/birefnet_x_matte.png") \
        == "F:/p/out/job/birefnet_x_matte.png"


# --- catalog + tool-scoped weights ------------------------------------------------------

def test_birefnet_catalog_validates_matte_params():
    out = mc.validate_params("birefnet", "matte", {"dilate_px": 8, "threshold": 0.4})
    assert out == {"dilate_px": 8, "threshold": 0.4}
    with pytest.raises(mc.CatalogError):      # not a matte param
        mc.validate_params("birefnet", "matte", {"strength": 0.5})


def test_postproc_weights_status_fails_closed():
    """An unknown/unconfigured tool must read as NOT-ok — a manifest edit can't silently
    disable the 412 pre-flight (mirrors the multi-preset posture)."""
    ok, missing = components.postproc_weights_status("not-a-tool")
    assert ok is False and missing
    assert "no weight set" in (missing[0].get("error") or "")


# --- API: matte endpoint + mixed stage-b -------------------------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def _asset_with_hero(ws):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    a = assets.create_asset(ws, name="MatteHero")["profile"]
    out = ws.out_dir / "job_hero09"
    out.mkdir(parents=True, exist_ok=True)
    (out / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "h"},
                        batch_id="bat_h9", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="A")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_hero09/hero.png", "seed": 5}
    assets.star_candidate(ws, a["id"], job_id=jid, source_output="job_hero09/hero.png",
                          version_id=a["active_version"], pipeline="zimage", seed=5)
    return a


def _matte_job(ws, vid, *, name="job_matte01/birefnet_x_bgmask.png", role="bgmask"):
    """A completed birefnet matte job owned by `vid` whose output carries `role` — the
    provenance that stage-b's bg_mask check requires (review 2026-06-11 High)."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()                                # never dispatch the fake job
    p = ws.out_dir / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    jid = RUNNER.submit(pipeline="birefnet", mode="matte", params={"input_image": "x"},
                        batch_id="bat_mt", index=0, batch_size=1,
                        requester_id=vid, profile_version_id=vid, stage="B")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_names": [name],
                                  "output_meta": {name: {"role": role}}}
    return name


def test_matte_endpoint_dry_run_argv(client):
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(RUNNER.workspace)
    r = client.post(f"/assets/{a['id']}/stage-b/matte",
                    json={"params": {"dilate_px": 8}, "dry_run": True})
    assert r.status_code == 200, r.text
    argv = r.json()["argv"]
    # the hero ★ is the version's self-contained casting copy (cand_*.png), not out/
    inp = argv[argv.index("--input") + 1].replace("\\", "/")
    assert "/casting/" in inp and inp.endswith(".png")
    assert argv[argv.index("--dilate-px") + 1] == "8"


def test_matte_endpoint_weight_preflight_412(client, monkeypatch):
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(RUNNER.workspace)
    monkeypatch.setattr(components, "postproc_weights_status",
                        lambda tool, variant=None: (False, [{"id": "birefnet",
                                                             "repo_id": "ZhengPeng7/BiRefNet",
                                                             "gated": False,
                                                             "pipeline": "birefnet"}]))
    r = client.post(f"/assets/{a['id']}/stage-b/matte", json={})
    assert r.status_code == 412, r.text
    assert "postproc=birefnet" in r.text          # the fetch hint


def test_matte_endpoint_gate_is_variant_aware(client, monkeypatch):
    """Review 2026-06-11 Medium: params.model_name='birefnet-hr' must hit the gate for the
    HR repo — not clear a default-only check and die mid-worker. Only the base variant is
    'cached' here, so HR must 412 (naming the HR repo) while the default passes."""
    monkeypatch.setattr(components, "_entry_present",
                        lambda e: e.get("repo_id") == "ZhengPeng7/BiRefNet")
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(RUNNER.workspace)
    RUNNER.pause()
    r = client.post(f"/assets/{a['id']}/stage-b/matte",
                    json={"params": {"model_name": "birefnet-hr"}})
    assert r.status_code == 412, r.text
    assert "BiRefNet_HR" in r.text and "postproc_variant=birefnet-hr" in r.text
    # the default variant is cached → clears the gate and submits
    r2 = client.post(f"/assets/{a['id']}/stage-b/matte", json={})
    assert r2.status_code == 200, r2.text


def test_matte_endpoint_submits_birefnet_job(client, monkeypatch):
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(RUNNER.workspace)
    monkeypatch.setattr(components, "postproc_weights_status",
                        lambda tool, variant=None: (True, []))
    RUNNER.pause()
    r = client.post(f"/assets/{a['id']}/stage-b/matte", json={})
    assert r.status_code == 200, r.text
    job = RUNNER.get(r.json()["job_id"])
    assert job["pipeline"] == "birefnet" and job["mode"] == "matte"
    inp = job["params"]["input_image"].replace("\\", "/")
    assert "/casting/" in inp and inp.endswith(".png")      # the version's hero copy
    assert job["stage"] == "B" and job["requester_id"] == a["active_version"]


def test_stage_b_mixed_requires_bg_mask(client):
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(RUNNER.workspace)
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "a ranger",
                          "realize": "mixed", "dry_run": True})
    assert r.status_code == 422 and "bg_mask" in r.text


def test_stage_b_mixed_dry_run_reports_split(client):
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(RUNNER.workspace)
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x",
                          "realize": "mixed", "bg_mask": "job_x/mask.png", "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["planned_jobs"] == 2
    assert body["split"] == {"img2img": 9, "inpaint": 8}     # npc_lite's method mix


def test_stage_b_mixed_fires_two_batch_jobs(client, monkeypatch):
    """The M3.5 done-line shape: one img2img batch (close framing, hero's setting held,
    bg-less cells) + one inpaint batch (wider framing, background repainted around the
    held subject — per-cell background prompts + the hero's bg mask), same batch_id."""
    from orchestrator.runner import RUNNER
    monkeypatch.setattr(components, "image_model_present", lambda repo_id: True)
    a = _asset_with_hero(RUNNER.workspace)
    mask_name = _matte_job(RUNNER.workspace, a["active_version"])
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "a ranger",
                          "realize": "mixed", "bg_mask": mask_name})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2 and body["realize"] == "mixed"
    assert body["split"]["img2img"] + body["split"]["inpaint"] == body["items"] == 17
    j_i2i, j_inp = (RUNNER.get(j) for j in body["job_ids"])
    assert j_i2i["mode"] == "img2img" and j_inp["mode"] == "inpaint"
    assert j_i2i["batch_id"] == j_inp["batch_id"]            # one batch, one grid story
    # inpaint batch: the bg mask + per-cell background prompts (subject isolation)
    assert j_inp["params"]["mask_image"].replace("\\", "/").endswith("birefnet_x_bgmask.png")
    assert j_inp["params"]["strength"] == 0.95               # inpaint_strength default
    inp_items = j_inp["params"]["batch_items"]
    assert all(it["meta"]["coverage_cell"]["background"] for it in inp_items)
    assert all("background" in it["prompt"] for it in inp_items)
    assert all(it["meta"]["method"] == "inpaint" for it in inp_items)
    # img2img batch keeps the hero's setting: bg-less cells (the M3 contract holds)
    i2i_items = j_i2i["params"]["batch_items"]
    assert all(it["meta"]["coverage_cell"]["background"] == "" for it in i2i_items)
    assert j_i2i["params"]["strength"] == 0.55               # req.strength default


# --- bg_mask PROVENANCE (review 2026-06-11 High) -----------------------------------------
# Any file under out/ used to pass; the mask must be the role=="bgmask" output of a
# completed birefnet job for THIS version, or Stage-B/P2 corpus generation gets poisoned.

def test_stage_b_mixed_rejects_unprovenanced_mask(client, monkeypatch):
    from orchestrator.runner import RUNNER
    monkeypatch.setattr(components, "image_model_present", lambda repo_id: True)
    a = _asset_with_hero(RUNNER.workspace)
    loose = RUNNER.workspace.out_dir / "job_x" / "hand_dropped_mask.png"
    loose.parent.mkdir(parents=True, exist_ok=True)
    loose.write_bytes(b"\x89PNG\r\n\x1a\n")                  # exists under out/ — not enough
    RUNNER.pause()
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x",
                          "realize": "mixed", "bg_mask": "job_x/hand_dropped_mask.png"})
    assert r.status_code == 422, r.text
    assert "not an output of a completed birefnet" in r.text


def test_stage_b_mixed_rejects_wrong_role(client, monkeypatch):
    """The matte/cutout siblings are real birefnet outputs but NOT the inpaint mask —
    inpainting against the soft matte would repaint the subject instead of the bg."""
    from orchestrator.runner import RUNNER
    monkeypatch.setattr(components, "image_model_present", lambda repo_id: True)
    a = _asset_with_hero(RUNNER.workspace)
    name = _matte_job(RUNNER.workspace, a["active_version"],
                      name="job_matte02/birefnet_x_matte.png", role="matte")
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x",
                          "realize": "mixed", "bg_mask": name})
    assert r.status_code == 422, r.text
    assert "role" in r.text and "matte" in r.text


def test_stage_b_mixed_rejects_other_versions_mask(client, monkeypatch):
    from orchestrator.runner import RUNNER
    monkeypatch.setattr(components, "image_model_present", lambda repo_id: True)
    a = _asset_with_hero(RUNNER.workspace)
    name = _matte_job(RUNNER.workspace, "ver_someoneelse",
                      name="job_matte03/birefnet_y_bgmask.png")
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x",
                          "realize": "mixed", "bg_mask": name})
    assert r.status_code == 422, r.text
    assert "not an output of a completed birefnet" in r.text
