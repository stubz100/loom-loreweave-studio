"""Clean/polish as chained post-passes on ANY run (user request 2026-06-11): the params
ride the catalog channel of every pipeline, never become worker flags, and the runner
chains one batch img2img job per pass over the parent's outputs (streaming per item).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orchestrator import model_catalog as mc


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    from orchestrator.config import CONFIG
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def test_emit_argv_skips_post_params():
    argv = mc.emit_argv("zimage", {"clean": True, "clean_strength": 0.4,
                                   "polish_backend": "sd35", "width": 1024}, "t2i")
    assert "--width" in argv
    assert all("clean" not in a and "polish" not in a for a in argv)


def test_generate_zimage_with_post_passes_dry_run(client):
    """Point 1: clean/polish on a plain single-model run (not just multi)."""
    r = client.post("/generate", json={
        "pipeline": "zimage", "prompt": "a ranger", "count": 1,
        "params": {"clean": True, "clean_backend": "sd35", "clean_model": "sd3.5-medium",
                   "clean_strength": 0.45},
        "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert all("clean" not in a for a in body["argv"])    # not a worker flag
    (p,) = body["post_passes"]
    assert p == {"pass": "clean", "backend": "sd35", "model_name": "sd3.5-medium",
                 "strength": 0.45, "prompt": None, "negative_prompt": None}


def test_post_model_family_mismatch_422(client):
    r = client.post("/generate", json={
        "pipeline": "zimage", "prompt": "x",
        "params": {"clean": True, "clean_backend": "sd35", "clean_model": "zimage-turbo"},
        "dry_run": True})
    assert r.status_code == 422 and "clean_backend family" in r.text


def test_post_footgun_subparams_without_toggle_422(client):
    r = client.post("/generate", json={
        "pipeline": "zimage", "prompt": "x",
        "params": {"polish_strength": 0.2}, "dry_run": True})
    assert r.status_code == 422 and "polish" in r.text


def test_submitted_job_carries_post_passes(client):
    from orchestrator.runner import RUNNER
    RUNNER.pause()   # hold the queue — no GPU in tests
    r = client.post("/generate", json={
        "pipeline": "zimage", "prompt": "a ranger", "count": 1,
        "params": {"polish": True}})
    assert r.status_code == 200, r.text
    job = RUNNER.get(r.json()["job_ids"][0])
    assert job["post_passes"] == [{"pass": "polish", "backend": "sd35", "model_name": None,
                                   "strength": 0.22, "prompt": None, "negative_prompt": None}]
    # the post params were stripped — they never reach the worker argv path
    assert "polish" not in job["params"]


def test_submit_chained_builds_batch_pass_job(client):
    """The runner chains pass jobs over the parent's outputs: one item per output, with
    per-output prompt/seed/coverage_cell carried from output_meta (curation survives),
    requester/stage inherited (same grid), and the remaining passes riding along."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    ws = RUNNER.workspace
    cell = {"shot_size": "portrait", "angle": "front", "expression": "neutral",
            "background": ""}
    parent = {
        "id": "job_paren001", "batch_id": "bat_p1", "requester_id": "ver_p",
        "profile_version_id": "ver_p", "stage": "B",
        "params": {"prompt": "fallback prompt", "width": 1024, "height": 1024, "seed": 9},
        "post_passes": [
            {"pass": "clean", "backend": "zimage", "model_name": None, "strength": 0.5,
             "prompt": None, "negative_prompt": None},
            {"pass": "polish", "backend": "sd35", "model_name": "sd3.5-medium",
             "strength": 0.22, "prompt": None, "negative_prompt": None},
        ],
        "result": {
            "output_names": ["job_paren001/a.png", "job_paren001/b.png"],
            "output_meta": {"job_paren001/a.png":
                            {"coverage_cell": cell, "seed": 101, "prompt": "cell prompt a"}},
        },
    }
    RUNNER._submit_chained(parent)
    chained = [j for j in RUNNER.jobs.values() if j.get("chained_from") == "job_paren001"]
    assert len(chained) == 1
    j = chained[0]
    assert j["pipeline"] == "zimage" and j["mode"] == "img2img" and j["pass"] == "clean"
    assert j["requester_id"] == "ver_p" and j["stage"] == "B"     # same grid
    assert j["batch_id"] == "bat_p1"
    items = j["params"]["batch_items"]
    assert len(items) == 2
    assert items[0]["prompt"] == "cell prompt a" and items[0]["seed"] == 101
    assert items[0]["meta"]["coverage_cell"] == cell              # curation survives
    assert items[0]["init_image"].replace("\\", "/").endswith("job_paren001/a.png")
    assert items[1]["prompt"] == "fallback prompt" and items[1]["seed"] == 9
    # polish rides along — it chains off THIS job's outputs when it finishes
    assert j["post_passes"][0]["pass"] == "polish"
    assert j["params"]["strength"] == 0.5
    # tidy the singleton
    RUNNER.jobs.pop(j["id"], None)


def test_stopped_batch_does_not_chain(client):
    """⏹ graceful stop must NOT enqueue the clean/polish chain over the partial outputs
    (review 2026-06-11): a stopped batch is still ok (≥1 image), but the stop wins —
    silently firing another GPU pass right after the user said stop would undo it."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    parent = {
        "id": "job_stop0001", "batch_id": "bat_s1", "requester_id": "sandbox",
        "profile_version_id": None, "stage": None,
        "params": {"prompt": "p", "width": 1024, "height": 1024},
        "post_passes": [{"pass": "clean", "backend": "zimage", "model_name": None,
                         "strength": 0.5, "prompt": None, "negative_prompt": None}],
        "result": {"output_names": ["job_stop0001/a.png"], "manifest_status": "stopped",
                   "batch": {"count": 3, "ok": 1, "failed": 0, "skipped": 2,
                             "status": "stopped"}},
    }
    before = set(RUNNER.jobs)
    RUNNER._submit_chained(parent)
    assert set(RUNNER.jobs) == before       # nothing chained
    assert not [j for j in RUNNER.jobs.values()
                if j.get("chained_from") == "job_stop0001"]


def test_stage_b_accepts_post_passes(client):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a = assets.create_asset(ws, name="PolishSet")["profile"]
    out = ws.out_dir / "job_hero02"
    out.mkdir(parents=True, exist_ok=True)
    (out / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "h"},
                        batch_id="bat_h2", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="A")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_hero02/hero.png", "seed": 5}
    assets.star_candidate(ws, a["id"], job_id=jid, source_output="job_hero02/hero.png",
                          version_id=a["active_version"], pipeline="zimage", seed=5)
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "a ranger",
                          "params": {"polish": True, "polish_strength": 0.2},
                          "dry_run": True})
    assert r.status_code == 200, r.text
    (p,) = r.json()["post_passes"]
    assert p["pass"] == "polish" and p["strength"] == 0.2
