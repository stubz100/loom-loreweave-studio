"""Batch-mode worker wiring (user request 2026-06-10 #1): one `--jobs-file` invocation =
one model load for a whole Stage-B dataset sweep. Covers the adapter argv + jobs.json,
the batch-manifest parse (incl. outputs_meta), the Stage-B single-batch-job endpoint,
Stage-C keep via per-output meta, graceful stop, project close (#2), and the
vendor-sync drift guard (R162: vendored copies must equal the monorepo source).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.adapters import sd35, zimage, _batch
from orchestrator.adapters.base import JobSpec

APP_REPO = Path(__file__).resolve().parents[2]
MONOREPO = Path(__file__).resolve().parents[4]


# --- vendor-sync drift guard (R162) --------------------------------------------------

@pytest.mark.parametrize("rel, copies", [
    ("zimage/run_pipeline.py", ["pipelines/zimage/run_pipeline.py",
                                "pipelines/multistack/src/pipeline/zimage/run_pipeline.py"]),
    ("sd35/run_pipeline.py", ["pipelines/multistack/src/pipeline/sd35/run_pipeline.py"]),
    # M3.5: the birefnet matting worker + the postproc manifest lib it imports
    ("postproc/_common.py", ["pipelines/multistack/src/pipeline/postproc/_common.py"]),
    ("postproc/birefnet/run_pipeline.py",
     ["pipelines/multistack/src/pipeline/postproc/birefnet/run_pipeline.py"]),
    # M4: the identity-lock worker (inswapper to the version's anchor)
    ("postproc/identity/run_pipeline.py",
     ["pipelines/multistack/src/pipeline/postproc/identity/run_pipeline.py"]),
    # M6: the GFPGAN-onnx face-restore worker
    ("postproc/face_restore/run_pipeline.py",
     ["pipelines/multistack/src/pipeline/postproc/face_restore/run_pipeline.py"]),
    # M7: the ltxv video worker + the frame-harvest extractor
    ("ltxv/run_pipeline.py", ["pipelines/multistack/src/pipeline/ltxv/run_pipeline.py"]),
    ("ltxv/stage1_load_pipeline.py",
     ["pipelines/multistack/src/pipeline/ltxv/stage1_load_pipeline.py"]),
    ("postproc/frame_harvest/run_pipeline.py",
     ["pipelines/multistack/src/pipeline/postproc/frame_harvest/run_pipeline.py"]),
])
def test_vendored_workers_match_monorepo_source(rel, copies):
    """The batch mode landed in the monorepo first (R162); every vendored copy must be a
    byte-identical mirror — a drift here means a re-vendor was missed."""
    src = MONOREPO / "src" / "pipeline" / rel
    if not src.is_file():
        pytest.skip("monorepo src/pipeline not present (clone-only checkout)")
    want = hashlib.md5(src.read_bytes()).hexdigest()
    for c in copies:
        got = hashlib.md5((APP_REPO / c).read_bytes()).hexdigest()
        assert got == want, f"{c} drifted from monorepo {rel} — re-vendor it"


# --- adapter: batch argv + jobs.json --------------------------------------------------

def _items(n=3):
    return [{"prompt": f"cell {i}", "seed": 100 + i,
             "meta": {"coverage_cell": {"shot_size": "portrait", "angle": "front",
                                        "expression": "neutral", "background": ""},
                      "method": "img2img"}}
            for i in range(n)]


def test_zimage_batch_argv_writes_jobs_file(tmp_path):
    out = tmp_path / "out" / "job_b1"
    out.mkdir(parents=True)
    spec = JobSpec(pipeline="zimage", mode="img2img",
                   params={"prompt": "[dataset]", "batch_items": _items(),
                           "init_image": "F:/hero.png", "strength": 0.55,
                           "width": 1024, "height": 1024, "no_cpu_offload": True},
                   output_dir=out)
    argv = zimage.build_argv(spec, "python", Path("x/zimage/run_pipeline.py"))
    assert "--jobs-file" in argv and "--prompt" not in argv
    jobs = json.loads((out / "jobs.json").read_text(encoding="utf-8"))
    assert jobs["shared"]["mode"] == "img2img"
    assert jobs["shared"]["init_image"] == "F:/hero.png"
    assert jobs["shared"]["cpu_offload"] is False          # inverted catalog flag
    assert "no_cpu_offload" not in jobs["shared"]
    assert len(jobs["items"]) == 3 and jobs["items"][0]["seed"] == 100
    assert jobs["items"][1]["meta"]["coverage_cell"]["shot_size"] == "portrait"


def test_sd35_batch_argv_translates_slg_flag(tmp_path):
    out = tmp_path / "out" / "job_b2"
    out.mkdir(parents=True)
    spec = JobSpec(pipeline="sd35", mode="img2img",
                   params={"prompt": "[dataset]", "batch_items": _items(1),
                           "init_image": "F:/hero.png", "no_skip_layer_guidance": True},
                   output_dir=out)
    argv = sd35.build_argv(spec, "python", Path("x/sd35/run_pipeline.py"))
    assert "--jobs-file" in argv
    jobs = json.loads((out / "jobs.json").read_text(encoding="utf-8"))
    assert jobs["shared"]["skip_layer_guidance"] is False
    assert "no_skip_layer_guidance" not in jobs["shared"]


def test_sd35_zimage_serve_argv():
    """M2.7 Phase 2a: the warm-worker spawn argv — file-path invocation + `--serve` (the runner
    feeds same-warm_group cell-jobs to ONE persistent process; each cell's output dir + params ride
    the fed stdin spec). The runner gates the warm path on `hasattr(adapter, 'serve_argv')`."""
    for mod in (sd35, zimage):
        argv = mod.serve_argv("py", Path(f"x/{mod.PIPELINE}/run_pipeline.py"), "cuda", "F:/out")
        assert argv[0] == "py" and argv[1].endswith("run_pipeline.py")  # file path, NOT `-m module`
        assert "--serve" in argv and "-m" not in argv
        assert argv[argv.index("--device") + 1] == "cuda"
        assert argv[argv.index("--output-dir") + 1] == "F:/out"
        assert mod.SERVE_RESULT_PREFIX == "[serve-result] "


# --- adapter: batch manifest parse (outputs + outputs_meta) ---------------------------

def _write_batch_manifest(out: Path, pipeline: str, oks: int, fails: int = 0,
                          status: str = "completed"):
    out.mkdir(parents=True, exist_ok=True)
    items = []
    for i in range(oks):
        png = out / f"{pipeline}_x_i{i:03d}_s{100 + i}.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        items.append({"index": i, "status": "ok", "seed": 100 + i, "prompt": f"c{i}",
                      "output_path": str(png), "manifest_path": str(png.with_suffix(".json")),
                      "duration_s": 9.0, "error": None,
                      "meta": {"coverage_cell": {"shot_size": "portrait", "angle": "front",
                                                 "expression": "neutral", "background": ""},
                               "method": "img2img"}})
    for i in range(fails):
        items.append({"index": oks + i, "status": "failed", "seed": 200 + i, "prompt": "f",
                      "output_path": "", "manifest_path": "", "duration_s": 1.0,
                      "error": "boom", "meta": None})
    (out / f"{pipeline}_batch_20260610_120000.json").write_text(json.dumps({
        "kind": "jobs_batch", "schema_version": 1, "pipeline": pipeline,
        "model_name": "m", "mode": "img2img", "status": status, "error": None,
        "count": oks + fails, "ok": oks, "failed": fails, "skipped": 0,
        "load_duration_s": 33.0, "total_duration_s": 120.0, "items": items,
    }), encoding="utf-8")


def test_parse_batch_result_outputs_and_meta(tmp_path):
    out = tmp_path / "out" / "job_b3"
    _write_batch_manifest(out, "zimage", oks=2, fails=1)
    rec = zimage.parse_result(0, "", "", out)
    assert rec.ok is True                       # per-item failures don't fail the batch
    assert len(rec.outputs) == 2 == len(rec.outputs_meta)
    assert rec.outputs_meta[0]["coverage_cell"]["angle"] == "front"
    assert rec.outputs_meta[0]["seed"] == 100   # setdefault from the item record
    assert rec.outputs_meta[0]["manifest_path"]  # per-item sidecar rides along (lineage)
    assert rec.outputs_meta[0]["duration_s"] == 9.0  # per-IMAGE gen time (inspector); job total below
    assert rec.duration_s == 120.0
    # honesty (review): counts surface so a partial run can't read as fully green
    assert rec.batch == {"count": 3, "ok": 2, "failed": 1, "skipped": 0,
                         "status": "completed"}
    assert _batch.partial_note(rec.batch) == "partial dataset: 2/3 cells (1 failed, 0 skipped)"


def test_parse_batch_result_stopped_keeps_completed_and_status(tmp_path):
    out = tmp_path / "out" / "job_b4"
    _write_batch_manifest(out, "zimage", oks=2, status="stopped")
    rec = zimage.parse_result(0, "", "", out)
    assert rec.ok is True and len(rec.outputs) == 2
    # the REAL status is preserved (review: stopped was masked as completed)
    assert rec.manifest_status == "stopped"
    assert rec.batch["status"] == "stopped"
    note = _batch.partial_note({**rec.batch, "skipped": 1, "count": 3})
    assert note is not None and "stopped early" in note


def test_partial_note_none_for_full_success():
    assert _batch.partial_note({"count": 5, "ok": 5, "failed": 0, "skipped": 0,
                                "status": "completed"}) is None
    assert _batch.partial_note(None) is None


def test_lineage_one_edge_per_output(tmp_path):
    """Review 2026-06-10: a batch/multi job yields N outputs — the index must carry one
    edge per output (keyed job_id + output_file), all removed together on delete."""
    from orchestrator import lineage
    from orchestrator import workspace as ws_mod
    ws = ws_mod.Workspace.create(tmp_path / "proj", name="t", size_cap_gb=50)
    job = {"id": "job_abcdef01", "requester_id": "ver_x", "profile_version_id": "ver_x",
           "stage": "B",
           "result": {"output_name": "job_abcdef01/a.png",
                      "output_names": ["job_abcdef01/a.png", "job_abcdef01/b.png"],
                      "manifest_path": "batch.json",
                      "output_meta": {"job_abcdef01/b.png": {"manifest_path": "b.json"}}}}
    edges = lineage.record_output(ws, job)
    assert len(edges) == 2
    idx = lineage.load_index(ws)
    assert {e["output_file"] for e in idx["edges"]} \
        == {"job_abcdef01/a.png", "job_abcdef01/b.png"}
    by_file = {e["output_file"]: e for e in idx["edges"]}
    assert by_file["job_abcdef01/b.png"]["manifest"] == "b.json"      # per-item sidecar
    assert by_file["job_abcdef01/a.png"]["manifest"] == "batch.json"  # job-level fallback
    # retry-idempotent: re-record replaces (still 2), never duplicates
    lineage.record_output(ws, job)
    assert len(lineage.load_index(ws)["edges"]) == 2
    # delete removes ALL of the job's edges
    assert lineage.remove_edge(ws, "job_abcdef01") is True
    assert lineage.load_index(ws)["edges"] == []


def test_batch_progress_and_collect():
    prog = zimage.make_progress({"batch_items": _items(4)})
    assert prog("[stage1] Pipeline loaded in 30s (shared across 4 items)") == pytest.approx(0.10)
    assert prog("  Image: F:/p/out/j/zimage_x_i000_s100.png") == pytest.approx(0.10 + 0.88 / 4)
    assert zimage.collect_output("  Image: F:/p/out/j/zimage_x_i000_s100.png") \
        == "F:/p/out/j/zimage_x_i000_s100.png"
    assert zimage.collect_output("[stage2] Generated in 9s") is None
    # single runs keep the coarse markers
    assert zimage.make_progress({}) is zimage.progress


# --- endpoint flows --------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    from orchestrator.config import CONFIG
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def _asset_with_hero(ws):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    a = assets.create_asset(ws, name="BatchHero")["profile"]
    out = ws.out_dir / "job_hero01"
    out.mkdir(parents=True, exist_ok=True)
    (out / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "h"},
                        batch_id="bat_h", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="A")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_hero01/hero.png", "seed": 5}
    assets.star_candidate(ws, a["id"], job_id=jid, source_output="job_hero01/hero.png",
                          version_id=a["active_version"], pipeline="zimage", seed=5)
    return a


def test_stage_b_zimage_cells_are_individual_warm_jobs(client):
    """M2.7 Phase 2a: a zimage/sd35 Expansion with NO post-passes emits N INDIVIDUAL img2img
    cell-jobs (each a persistent tile surviving pause/resume) serviced by ONE warm worker — not a
    single opaque batch job. (With post-passes it falls back to the cold batch job; see below.)"""
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(RUNNER.workspace)
    RUNNER.pause()    # hold the queue — no GPU in tests
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "a test ranger"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 17 and body["items"] == 17     # one job PER cell, not a single batch
    groups = set()
    for jid in body["job_ids"]:
        job = RUNNER.get(jid)
        p = job["params"]
        assert "batch_items" not in p                       # individual, not a batch
        assert p["init_image"] and p["meta"]["method"] in ("img2img", "inpaint")
        assert job["coverage_cell"] is not None             # cell metadata on the job (curation)
        groups.add(job["warm_group"])
    assert len(groups) == 1 and next(iter(groups))          # all cells share ONE warm worker
    assert RUNNER.get(body["job_ids"][0])["coverage_cell"]["background"] == ""  # img2img realization
    # prompt order unchanged: cell fragment leads, clause follows, (default) style trails
    p0 = RUNNER.get(body["job_ids"][0])["params"]["prompt"]
    assert p0.startswith("front view") and "a test ranger" in p0
    assert p0.index("view") < p0.index("a test ranger")


def test_stage_b_post_passes_now_ride_warm_cells(client):
    """M2.7 Phase 2b: a sweep WITH post-passes (here: clean) no longer falls back to the cold batch —
    it streams warm cell-jobs, each CARRYING the post_passes so it chains its own pass(es) on
    completion (one pass tile per cell, all pause-safe). (`mixed` is the only remaining cold case —
    covered by test_birefnet_matting.py::test_stage_b_mixed_fires_two_batch_jobs.)"""
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(RUNNER.workspace)
    RUNNER.pause()
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x", "params": {"clean": True}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 17                              # warm cells, NOT one cold batch job
    for jid in body["job_ids"]:
        job = RUNNER.get(jid)
        assert "batch_items" not in job["params"] and job["warm_group"]
        assert "clean" in [p["pass"] for p in job["post_passes"]]   # each cell carries its passes


def test_stage_b_dry_run_reports_batch_shape(client):
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(RUNNER.workspace)
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "x", "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    # warm cells (M2.7 Phase 2a): one queue job per cell → planned_jobs == the cell count
    assert body["planned_jobs"] == body["items"] == 17


# --- weight pre-flight vs the params-channel model (review 2026-06-11) ----------------
# `model_name` is also a catalog param and overrides the top-level field on merge — the
# cache gate must check THAT model, not just the explicit/default one (it used to pass
# sd3.5-medium's check and then load sd3.5-large in the worker).

def _only_medium_cached(monkeypatch):
    from orchestrator import components
    monkeypatch.setattr(components, "image_model_present",
                        lambda repo_id: repo_id == "stabilityai/stable-diffusion-3.5-medium")


def test_generate_preflight_checks_params_channel_model(client, monkeypatch):
    _only_medium_cached(monkeypatch)
    from orchestrator.runner import RUNNER
    RUNNER.pause()    # belt-and-braces: a regression here would submit a real job
    r = client.post("/generate", json={"pipeline": "sd35", "prompt": "a ranger",
                                       "params": {"model_name": "sd3.5-large"}})
    assert r.status_code == 412, r.text
    assert "sd3.5-large" in r.text          # the gate saw the EFFECTIVE (merged) model


def test_stage_b_preflight_checks_params_channel_model(client, monkeypatch):
    _only_medium_cached(monkeypatch)
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(RUNNER.workspace)
    RUNNER.pause()
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "a ranger",
                          "pipeline": "sd35", "params": {"model_name": "sd3.5-large"}})
    assert r.status_code == 412, r.text
    assert "sd3.5-large" in r.text


def test_stage_b_params_channel_model_reaches_the_worker(client, monkeypatch):
    """Same precedence as /generate (params channel wins over the top-level field) — and
    the model that passed the gate is the one the worker params carry."""
    from orchestrator import components
    monkeypatch.setattr(components, "image_model_present", lambda repo_id: True)
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(RUNNER.workspace)
    RUNNER.pause()
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"preset": "npc_lite", "character_clause": "a ranger",
                          "pipeline": "sd35", "model_name": "sd3.5-medium",
                          "params": {"model_name": "sd3.5-large"}})
    assert r.status_code == 200, r.text
    job = RUNNER.get(r.json()["job_ids"][0])
    assert job["params"]["model_name"] == "sd3.5-large"


def test_keep_ref_resolves_cell_from_output_meta(client):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a = assets.create_asset(ws, name="MetaKeep")["profile"]
    out = ws.out_dir / "job_meta01"
    out.mkdir(parents=True, exist_ok=True)
    (out / "img0.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (out / "img1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    jid = RUNNER.submit(pipeline="zimage", mode="img2img",
                        params={"prompt": "[dataset]", "batch_items": [{}, {}]},
                        batch_id="bat_m", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="B")
    cell = {"shot_size": "waist_up", "angle": "back", "expression": "neutral", "background": ""}
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {
        "ok": True, "output_name": "job_meta01/img0.png",
        "output_names": ["job_meta01/img0.png", "job_meta01/img1.png"],
        "output_meta": {"job_meta01/img1.png":
                        {"coverage_cell": cell, "seed": 101, "method": "img2img"}},
    }
    # the meta-carrying output keeps fine (per-output cell)
    r = client.post(f"/assets/{a['id']}/refs/keep",
                    json={"job_id": jid, "output": "job_meta01/img1.png"})
    assert r.status_code == 200, r.text
    ref = r.json()["ref_set"][0]
    assert ref["coverage_cell"]["angle"] == "back" and ref["seed"] == 101
    # an output with NO meta and no job-level cell → 422
    r2 = client.post(f"/assets/{a['id']}/refs/keep",
                     json={"job_id": jid, "output": "job_meta01/img0.png"})
    assert r2.status_code == 422


def test_stop_batch_writes_stop_file(client):
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    jid = RUNNER.submit(pipeline="zimage", mode="img2img",
                        params={"prompt": "[dataset]", "batch_items": [{}]},
                        batch_id="bat_s", index=0, batch_size=1)
    # not running yet → 409
    assert client.post(f"/jobs/{jid}/stop").status_code == 409
    RUNNER.jobs[jid]["status"] = "running"
    (ws.out_dir / jid).mkdir(parents=True, exist_ok=True)
    r = client.post(f"/jobs/{jid}/stop")
    assert r.status_code == 200
    assert (ws.out_dir / jid / "STOP").is_file()
    RUNNER.jobs[jid]["status"] = "failed"   # leave the singleton tidy


def test_close_project_then_reopen(client):
    from orchestrator.runner import RUNNER
    path = RUNNER.workspace.path
    assert client.get("/project").json()["open"] is True
    r = client.post("/project/close")
    assert r.status_code == 200 and r.json() == {"open": False}
    assert client.get("/project").json() == {"open": False}
    # project-scoped routes 409 again
    assert client.post("/generate", json={"prompt": "x"}).status_code == 409
    assert client.get("/assets").status_code == 409
    # closing is not destructive: reopen restores it
    r2 = client.post("/project/open", json={"path": str(path)})
    assert r2.status_code == 200 and r2.json()["open"] is True
