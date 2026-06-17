"""multi full-parameter surface (user request 2026-06-10): catalog-served tunables,
the batch subcommand with opt-in clean/polish, multi-stage parse_result, interim-result
collection, and the /generate params channel for multi (was a hard 400).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator import model_catalog as mc
from orchestrator.adapters import multi
from orchestrator.adapters.base import JobSpec


# --- catalog surface ---------------------------------------------------------------

def test_multi_params_catalog_served_and_validated():
    assert {p["name"] for p in mc.params("multi")} >= {
        "width", "height", "seed", "clean", "clean_backend", "clean_strength",
        "polish", "polish_backend", "polish_strength"}
    api = mc.catalog_for_api()
    assert "multi" in api and api["multi"]["params"] is mc.MULTI_PARAMS
    out = mc.validate_params("multi", "ideate", {"clean": True, "clean_strength": 0.4,
                                                 "polish": True, "polish_backend": None})
    assert out == {"clean": True, "clean_strength": 0.4, "polish": True}
    with pytest.raises(mc.CatalogError):
        mc.validate_params("multi", "ideate", {"clean_backend": "nope-img2img"})
    with pytest.raises(mc.CatalogError):
        mc.validate_params("multi", "ideate", {"polish_strength": 2.0})


def test_post_pass_models_are_dropdowns():
    """User 2026-06-11: clean/polish model was freetext — now an enum over the union of
    the two backend families (family consistency is enforced at /generate)."""
    by_name = {p["name"]: p for p in mc.params("multi")}
    for key in ("clean_model", "polish_model"):
        spec = by_name[key]
        assert spec["type"] == "enum" and spec.get("post") is True
        assert "zimage-turbo" in spec["choices"] and "sd3.5-medium" in spec["choices"]
    # the same shared specs ride the zimage/sd35 catalogs (post-passes on any run)
    assert {p["name"] for p in mc.params("zimage")} >= {"clean", "polish", "polish_model"}
    assert {p["name"] for p in mc.params("sd35")} >= {"clean", "polish", "clean_model"}


# --- build_argv: subcommand pick + clean/polish flags --------------------------------

def _spec(params: dict, out: Path) -> JobSpec:
    out.mkdir(parents=True, exist_ok=True)
    return JobSpec(pipeline="multi", mode="ideate",
                   params={"prompt": "a hero", "num_candidates": 2,
                           "ideation_mode": "fast", "width": 1024, "height": 1024, **params},
                   output_dir=out)


def test_build_argv_plain_cast_stays_ideate(tmp_path):
    argv = multi.build_argv(_spec({}, tmp_path / "proj" / "out" / "job_a"),
                            "python", Path("x/pipeline/multi/run_pipeline.py"))
    assert argv[3] == "ideate"
    assert "--clean" not in argv and "--polish" not in argv


def test_build_argv_post_params_never_reach_the_worker(tmp_path):
    """Clean/polish are orchestrator-chained (2026-06-11): even if post params reached
    the adapter, they must not become worker CLI flags — loom always invokes `ideate`."""
    argv = multi.build_argv(
        _spec({"clean": True, "clean_strength": 0.4, "polish": True, "seed": 7},
              tmp_path / "proj" / "out" / "job_b"),
        "python", Path("x/pipeline/multi/run_pipeline.py"))
    assert argv[3] == "ideate"
    assert "--clean" not in argv and "--polish" not in argv
    assert "--clean-strength" not in argv
    assert argv[argv.index("--seed") + 1] == "7"


# --- parse_result: clean/polish outputs join the pool --------------------------------

def test_parse_result_includes_clean_and_polish_outputs(tmp_path):
    out = tmp_path / "out" / "job_c"
    inter = out / "_inter" / "run"
    paths = {}
    for stage, sub in (("ideate", "ideate/zimage/seed_1"), ("clean", "clean"),
                       ("polish", "polish")):
        d = inter / sub
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{stage}_img.png"
        f.write_bytes(b"PNG")
        paths[stage] = f
    manifest = {
        "architecture": "batch", "prompt": "x", "seed": 1, "pipeline_duration_s": 9.9,
        "stages": [
            {"name": "ideate", "status": "completed",
             "outputs": {"succeeded": 1, "failed": 0, "candidates": [
                 {"pipeline": "zimage", "seed": 1, "candidate_index": 0, "status": "ok",
                  "output_path": str(paths["ideate"]), "sub_manifest_path": "",
                  "duration_s": 1.0, "error": ""}]}},
            {"name": "clean", "status": "completed",
             "outputs": {"succeeded": 1, "failed": 0, "cleaned": [
                 {"status": "ok", "output_path": str(paths["clean"]),
                  "source_candidate_index": 0}]}},
            {"name": "polish", "status": "completed",
             "outputs": {"succeeded": 1, "failed": 0, "polished": [
                 {"status": "ok", "output_path": str(paths["polish"]),
                  "source_candidate_index": 0}]}},
        ],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "multi_batch_20260610_s1.json").write_text(json.dumps(manifest), encoding="utf-8")
    rec = multi.parse_result(0, "", "", out)
    assert rec.ok is True
    assert len(rec.outputs) == 3            # ideate + cleaned + polished tiles


# --- interim results: collect_output + progress totals -------------------------------

def test_collect_output_matches_image_and_batch_pass_lines():
    assert multi.collect_output("  Image: F:/p/out/job/_inter/r/ideate/zimage/seed_1/z.png") \
        == "F:/p/out/job/_inter/r/ideate/zimage/seed_1/z.png"
    assert multi.collect_output("[batch] clean OK  zimage s1 -> F:/p/out/job/_inter/r/clean/c.png") \
        == "F:/p/out/job/_inter/r/clean/c.png"
    assert multi.collect_output("[batch] polish FAIL zimage s1 -> something bad") is None
    assert multi.collect_output("[stage2] Generated in 9s -- 1024x1024") is None


def test_make_progress_counts_clean_polish_passes():
    # 1 candidate × 3 pipelines × (ideate + clean) = 6 units
    prog = multi.make_progress({"num_candidates": 1, "clean": True})
    assert prog("[done] Pipeline completed in 9s") == pytest.approx(0.05 + 0.90 / 6)
    prog("[done] Pipeline completed in 9s")
    prog("[done] Pipeline completed in 9s")
    assert prog("[batch] ideate produced 3 ok / 0 failed candidate(s)") \
        == pytest.approx(0.05 + 0.90 * 3 / 6)
    assert prog("[batch] clean OK  zimage s1 -> c.png") == pytest.approx(0.05 + 0.90 * 4 / 6)


# --- /generate: multi params channel (was 400) + footgun guard -----------------------

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    from orchestrator.config import CONFIG
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def test_generate_multi_accepts_params_dry_run(client):
    r = client.post("/generate", json={
        "pipeline": "multi", "prompt": "a hero", "num_candidates": 1,
        "params": {"clean": True, "clean_strength": 0.4, "polish": True},
        "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    # the cast itself stays a plain ideate run — no worker clean/polish flags
    argv = body["argv"]
    assert "ideate" in argv
    assert "--clean" not in argv and "--polish" not in argv
    # the passes chain as separate jobs, planned + previewable
    passes = body["post_passes"]
    assert [p["pass"] for p in passes] == ["clean", "polish"]
    assert passes[0]["backend"] == "zimage" and passes[0]["strength"] == 0.4
    assert passes[1]["backend"] == "sd35" and passes[1]["strength"] == 0.22


def test_multi_unset_size_uses_catalog_default_not_project_default(client):
    """M6 review #2 (user finding): the drawer advertises the CATALOG default (1024² —
    the member models' native square), but the request-level 1280×720 default silently
    won for an unset cast. Unset → catalog default; explicit values (top-level or params
    channel) still win."""
    r = client.post("/generate", json={"pipeline": "multi", "prompt": "a hero",
                                       "num_candidates": 1, "dry_run": True})
    assert r.status_code == 200, r.text
    argv = r.json()["argv"]
    assert argv[argv.index("--width") + 1] == "1024"
    assert argv[argv.index("--height") + 1] == "1024"
    r2 = client.post("/generate", json={"pipeline": "multi", "prompt": "a hero",
                                        "width": 1280, "height": 720, "dry_run": True})
    argv2 = r2.json()["argv"]
    assert argv2[argv2.index("--width") + 1] == "1280"
    assert argv2[argv2.index("--height") + 1] == "720"
    r3 = client.post("/generate", json={"pipeline": "multi", "prompt": "a hero",
                                        "params": {"width": 1536}, "dry_run": True})
    argv3 = r3.json()["argv"]
    assert argv3[argv3.index("--width") + 1] == "1536"


def test_single_pipeline_unset_size_uses_catalog_default_not_project_default(client):
    """User 2026-06-14: a 1024² sd35 cast silently ran at 1280×720. The multi branch got the
    M6 review #2 display==reality fix but the SINGLE pipelines (sd35/zimage/flux2) didn't —
    so an unset cast kept the 1280×720 GenerateRequest default instead of the catalog default
    the drawer advertises. Unset → the pipeline's catalog default; explicit values (top-level
    or params channel) still win."""
    for pipe, w, h in (("sd35", "1024", "1024"), ("zimage", "1024", "1024"),
                       ("flux2", "1360", "768")):
        r = client.post("/generate", json={"pipeline": pipe, "prompt": "a hero",
                                           "count": 1, "dry_run": True})
        assert r.status_code == 200, r.text
        argv = r.json()["argv"]
        assert argv[argv.index("--width") + 1] == w, f"{pipe} width"
        assert argv[argv.index("--height") + 1] == h, f"{pipe} height"
    # explicit top-level dims still win
    r2 = client.post("/generate", json={"pipeline": "sd35", "prompt": "a hero", "count": 1,
                                        "width": 1280, "height": 720, "dry_run": True})
    argv2 = r2.json()["argv"]
    assert argv2[argv2.index("--width") + 1] == "1280"
    assert argv2[argv2.index("--height") + 1] == "720"
    # an explicit params-channel dim still wins over the catalog default
    r3 = client.post("/generate", json={"pipeline": "sd35", "prompt": "a hero", "count": 1,
                                        "params": {"width": 1536}, "dry_run": True})
    argv3 = r3.json()["argv"]
    assert argv3[argv3.index("--width") + 1] == "1536"


def test_generate_multi_footgun_subparams_without_toggle_422(client):
    r = client.post("/generate", json={
        "pipeline": "multi", "prompt": "a hero",
        "params": {"clean_strength": 0.4}, "dry_run": True})
    assert r.status_code == 422
    assert "clean" in r.text


def test_generate_multi_unknown_param_422(client):
    r = client.post("/generate", json={
        "pipeline": "multi", "prompt": "a hero",
        "params": {"nope": 1}, "dry_run": True})
    assert r.status_code == 422
