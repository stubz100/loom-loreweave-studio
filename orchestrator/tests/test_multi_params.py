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
    # Phase 2c: a cast fans out into INDIVIDUAL t2i candidate jobs (not one `multi`/`ideate` run) —
    # the dry-run previews the FIRST candidate's argv (fast lineup → flux2 t2i). No clean/polish flags
    # on the candidate; they chain as separate post-pass jobs.
    assert body["cast"] is True and body["count"] == 3        # 1 candidate × 3 pipelines
    assert [m["pipeline"] for m in body["lineup"]] == ["flux2", "sd35", "zimage"]
    argv = body["argv"]
    assert "--mode" in argv and argv[argv.index("--mode") + 1] == "t2i"
    assert "--clean" not in argv and "--polish" not in argv
    # the passes chain as separate jobs, planned + previewable
    passes = body["post_passes"]
    assert [p["pass"] for p in passes] == ["clean", "polish"]
    assert passes[0]["backend"] == "zimage" and passes[0]["strength"] == 0.4
    assert passes[1]["backend"] == "sd35" and passes[1]["strength"] == 0.22


def test_ideation_lineup_models_are_valid():
    """Phase 2c: every (pipeline, model) a cast fans out across must be a real catalog variant — the
    lineup mirrors the vendored worker's IDEATION_PRESETS, this guards drift."""
    for preset in ("fast", "refined"):
        lineup = mc.ideation_lineup(preset)
        assert [p for p, _ in lineup] == ["flux2", "sd35", "zimage"]
        for pl, model in lineup:
            assert mc.find_variant(pl, model) is not None, f"{preset}: {pl}/{model} not in catalog"


def test_cast_fans_out_into_individual_warm_t2i_candidates(client, monkeypatch):
    """Phase 2c: a Cast submits num_candidates × |lineup| INDIVIDUAL t2i jobs (not one opaque `multi`
    job), each its own pause-safe queue entry; same-(pipeline,model) candidates share a warm_group,
    and the candidate seed is shared across the 3 pipelines."""
    from collections import Counter
    from orchestrator import components
    from orchestrator.runner import RUNNER
    monkeypatch.setattr(components, "multi_weights_status", lambda preset: (True, []))
    RUNNER.pause()    # queue them; never dispatch to the GPU
    r = client.post("/generate", json={"pipeline": "multi", "prompt": "a hero",
                                       "num_candidates": 2, "ideation_mode": "fast", "seed": 42})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 6 and len(body["job_ids"]) == 6       # 2 candidates × 3 pipelines
    jobs = [RUNNER.get(j) for j in body["job_ids"]]
    assert all(j["pipeline"] in ("flux2", "sd35", "zimage") and j["mode"] == "t2i" for j in jobs)
    assert all("batch_items" not in j["params"] for j in jobs)    # individual, not a batch
    groups = Counter(j["warm_group"] for j in jobs)               # one per (pipeline, model)
    assert len(groups) == 3 and set(groups.values()) == {2}       # 3 groups × 2 candidates each
    by_pl: dict[str, list[int]] = {}
    for j in jobs:
        by_pl.setdefault(j["pipeline"], []).append(j["params"]["seed"])
    assert all(sorted(seeds) == [42, 43] for seeds in by_pl.values())   # seed shared across pipelines


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


def test_flux2_dev_unset_size_defaults_to_512(client):
    """M0e Part A: flux.2-dev is the heaviest flux2 variant and far faster at low res, so an
    UNSET dev cast resolves to its per-variant 512² default — not flux2's 1360×768 pipeline
    default. Non-dev flux2 models keep 1360×768; explicit dims (top-level or params) still win;
    a params-channel model_name also drives the size default."""
    r = client.post("/generate", json={"pipeline": "flux2", "prompt": "a hero",
                                       "model_name": "flux.2-dev", "count": 1, "dry_run": True})
    assert r.status_code == 200, r.text
    argv = r.json()["argv"]
    assert argv[argv.index("--width") + 1] == "512"
    assert argv[argv.index("--height") + 1] == "512"
    # a non-dev flux2 model keeps the pipeline default (no size override)
    r2 = client.post("/generate", json={"pipeline": "flux2", "prompt": "a hero",
                                        "model_name": "flux.2-klein-4b", "count": 1, "dry_run": True})
    argv2 = r2.json()["argv"]
    assert argv2[argv2.index("--width") + 1] == "1360"
    assert argv2[argv2.index("--height") + 1] == "768"
    # explicit dims still win on dev
    r3 = client.post("/generate", json={"pipeline": "flux2", "prompt": "a hero",
                                        "model_name": "flux.2-dev", "width": 1024, "height": 1024,
                                        "count": 1, "dry_run": True})
    argv3 = r3.json()["argv"]
    assert argv3[argv3.index("--width") + 1] == "1024"
    assert argv3[argv3.index("--height") + 1] == "1024"
    # a params-channel model_name also resolves the 512² default
    r4 = client.post("/generate", json={"pipeline": "flux2", "prompt": "a hero",
                                        "params": {"model_name": "flux.2-dev"}, "count": 1,
                                        "dry_run": True})
    argv4 = r4.json()["argv"]
    assert argv4[argv4.index("--width") + 1] == "512"
    assert argv4[argv4.index("--height") + 1] == "512"


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
