"""M7 — video-sketch harvest (no GPU): ltxv catalog/adapter, the frame_harvest worker
run END-TO-END on a synthetic video (OpenCV, CPU — a real no-GPU worker test), the
chained harvest pass with the job-level coverage_cell fallback, and the sketch endpoint.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator import components
from orchestrator import model_catalog as mc
from orchestrator.adapters import frame_harvest, ltxv
from orchestrator.adapters.base import JobSpec
from orchestrator.config import CONFIG

_CELL = {"shot_size": "waist_up", "angle": "profile_left", "expression": "neutral",
         "background": ""}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


# --- ltxv adapter ---------------------------------------------------------------------

def test_ltxv_catalog_and_adapter_argv(tmp_path):
    assert mc.default_model("ltxv") == "2b_0.9.7_distilled"
    spec = JobSpec(pipeline="ltxv", mode="i2v",
                   params={"prompt": "side view, a ranger, turns left",
                           "init_image": "F:/hero.png", "num_frames": 65,
                           "model_name": "2b_0.9.7_distilled"},
                   output_dir=tmp_path)
    argv = ltxv.build_argv(spec, "python", Path("x/ltxv/run_pipeline.py"))
    assert argv[2] == str(Path("x/ltxv/run_pipeline.py")) or "i2v" in argv  # subcommand present
    assert "i2v" in argv
    assert argv[argv.index("--variant") + 1] == "2b_0.9.7_distilled"   # NOT --model-name
    assert argv[argv.index("--init-image") + 1] == "F:/hero.png"
    assert argv[argv.index("--num-frames") + 1] == "65"


def test_ltxv_parse_result_manifest_as_truth(tmp_path):
    out = tmp_path / "out" / "job_lx1"
    out.mkdir(parents=True)
    mp4 = out / "ltxv_i2v_2bd097_s42_20260612.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (out / "ltxv_i2v_2bd097_s42_20260612.json").write_text(json.dumps({
        "pipeline_duration_s": 99.0, "output_path": str(mp4),
        "stages": [{"name": n, "status": "completed"} for n in
                   ("load_pipeline", "generate", "decode", "export")]}), encoding="utf-8")
    rec = ltxv.parse_result(0, "", "", out)
    assert rec.ok is True and rec.outputs == [str(mp4)]
    # failed stage → honest failure
    (out / "ltxv_i2v_2bd097_s42_20260612.json").write_text(json.dumps({
        "output_path": str(mp4),
        "stages": [{"name": "generate", "status": "failed", "error": "OOM"}]}),
        encoding="utf-8")
    rec2 = ltxv.parse_result(0, "", "", out)
    assert rec2.ok is False and "generate" in (rec2.error or "")


def test_ltxv_progress_and_video_line():
    assert ltxv.progress("[stage2] Latents saved to x") == 0.8
    assert ltxv.progress("[done] Pipeline completed in 99s") == 1.0
    assert ltxv.collect_output("  Video:    F:/p/out/job/ltxv_i2v.mp4") \
        == "F:/p/out/job/ltxv_i2v.mp4"


# --- frame_harvest: REAL worker end-to-end (CPU, no models) ----------------------------

def test_frame_harvest_worker_end_to_end(tmp_path):
    import cv2
    import numpy as np
    vid = tmp_path / "sketch.mp4"
    w = cv2.VideoWriter(str(vid), cv2.VideoWriter_fourcc(*"mp4v"), 24, (64, 64))
    for i in range(30):
        w.write(np.full((64, 64, 3), i * 8 % 255, np.uint8))
    w.release()

    out = tmp_path / "out" / "job_h1"
    spec = JobSpec(pipeline="frame_harvest", mode="harvest",
                   params={"batch_items": [{"input": str(vid), "seed": 7,
                                            "meta": {"coverage_cell": _CELL}}],
                           "every": 6, "max_frames": 4},
                   output_dir=out)
    out.mkdir(parents=True)
    script = frame_harvest.resolve_script(CONFIG.pipeline_roots)
    assert script is not None
    argv = frame_harvest.build_argv(spec, sys.executable, script)
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    rec = frame_harvest.parse_result(proc.returncode, proc.stdout, "", out)
    assert rec.ok is True and len(rec.outputs) == 4          # 30 frames / every 6, cap 4
    assert all(Path(o).is_file() for o in rec.outputs)
    # the sketch's target cell + frame number ride every harvested still
    assert rec.outputs_meta[0]["coverage_cell"] == _CELL
    assert rec.outputs_meta[1]["frame"] == 6


# --- chained harvest pass (runner) -----------------------------------------------------

def test_submit_chained_harvest_uses_job_cell_fallback(client):
    """The ltxv parent has ONE mp4 and no per-output meta — harvested frames must inherit
    the sketch's TARGET cell from the job-level coverage_cell field."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    parent = {
        "id": "job_skpar01", "batch_id": "bat_sk1", "requester_id": "ver_s",
        "profile_version_id": "ver_s", "stage": "B",
        "params": {"prompt": "p", "seed": 42, "width": 704, "height": 480},
        "coverage_cell": _CELL,
        "post_passes": [{"pass": "harvest", "backend": "frame_harvest",
                         "every": 4, "max_frames": 12}],
        "result": {"output_names": ["job_skpar01/sketch.mp4"]},
    }
    RUNNER._submit_chained(parent)
    chained = [j for j in RUNNER.jobs.values() if j.get("chained_from") == "job_skpar01"]
    assert len(chained) == 1
    j = chained[0]
    assert j["pipeline"] == "frame_harvest" and j["mode"] == "harvest"
    assert j["params"]["every"] == 4 and j["params"]["max_frames"] == 12
    item = j["params"]["batch_items"][0]
    assert item["input"].replace("\\", "/").endswith("job_skpar01/sketch.mp4")
    assert item["meta"]["coverage_cell"] == _CELL            # the job-cell fallback
    RUNNER.jobs.pop(j["id"], None)


# --- the sketch endpoint ----------------------------------------------------------------

def _asset_with_hero(ws):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    a = assets.create_asset(ws, name="SketchAsset")["profile"]
    out = ws.out_dir / "job_hero77"
    out.mkdir(parents=True, exist_ok=True)
    (out / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "h"},
                        batch_id="bat_s7", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="A")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_hero77/hero.png",
                                  "output_names": ["job_hero77/hero.png"]}
    assets.star_candidate(ws, a["id"], job_id=jid, source_output="job_hero77/hero.png",
                          version_id=a["active_version"], pipeline="zimage", seed=1)
    return a


def test_sketch_dry_run_prompt_and_chain(client):
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(RUNNER.workspace)
    r = client.post(f"/assets/{a['id']}/stage-b/sketch",
                    json={"shot_size": "waist_up", "angle": "profile_left",
                          "expression": "neutral", "character_clause": "a test ranger",
                          "motion_prompt": "turns slowly left", "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    # prompt order: cell fragment LEADS, clause, motion, (style trails)
    assert body["prompt"].index("view") < body["prompt"].index("a test ranger")
    assert "turns slowly left" in body["prompt"]
    assert body["cell"]["angle"] == "profile_left" and body["cell"]["background"] == ""
    (h,) = body["post_passes"]
    assert h["pass"] == "harvest" and h["backend"] == "frame_harvest"
    argv = body["argv"]
    assert "i2v" in argv and "--init-image" in argv


def test_sketch_submits_with_cell_and_harvest_chain(client, monkeypatch):
    from orchestrator.runner import RUNNER
    monkeypatch.setattr(components, "image_model_present", lambda repo_id: True)
    a = _asset_with_hero(RUNNER.workspace)
    r = client.post(f"/assets/{a['id']}/stage-b/sketch",
                    json={"angle": "back", "shot_size": "full_body",
                          "character_clause": "a test ranger",
                          "every": 8, "max_frames": 16})
    assert r.status_code == 200, r.text
    job = RUNNER.get(r.json()["job_id"])
    assert job["pipeline"] == "ltxv" and job["mode"] == "i2v"
    assert job["coverage_cell"]["angle"] == "back"           # first-class job field
    (h,) = job["post_passes"]
    assert h == {"pass": "harvest", "backend": "frame_harvest",
                 "every": 8, "max_frames": 16}
    assert job["stage"] == "B" and job["requester_id"] == a["active_version"]


def test_sketch_rejects_bad_cell_and_videos_not_keepable(client):
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(RUNNER.workspace)
    r = client.post(f"/assets/{a['id']}/stage-b/sketch",
                    json={"angle": "upside_down", "character_clause": "x",
                          "dry_run": True})
    assert r.status_code == 422, r.text
    # an mp4 output can't be kept into the ref_set (only harvested frames are refs)
    jid = RUNNER.submit(pipeline="ltxv", mode="i2v", params={"prompt": "p"},
                        batch_id="bat_v", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="B",
                        coverage_cell=dict(_CELL))
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_v/sketch.mp4",
                                  "output_names": ["job_v/sketch.mp4"]}
    r2 = client.post(f"/assets/{a['id']}/refs/keep", json={"job_id": jid})
    assert r2.status_code == 422 and "harvested frames" in r2.text
