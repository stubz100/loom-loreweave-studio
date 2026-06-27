"""flux2 Stage-B multi-reference adapter (§11/R147) — no-GPU invariants.

The §11 spike (GO) proved FLUX.2 reference-conditioning carries the hero's identity into a new
pose/scene; this wires it as the Stage-B `ref` mode (the hero rides as an in-context reference).
These lock the plumbing the GPU run depends on: the adapter is present + module-invoked, the
batch jobs-file carries the SHARED reference + per-cell items, the Stage-B endpoint routes flux2
to a `ref` group (hero as reference, no init/strength), and the worker imports as a module.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.adapters import flux2
from orchestrator.adapters.base import JobSpec
from orchestrator.config import CONFIG


# --- adapter unit invariants ----------------------------------------------------

def test_flux2_present_and_capabilities():
    caps = flux2.capabilities(CONFIG.pipeline_roots)
    assert caps["present"] is True and caps["pipeline"] == "flux2"
    # ref + t2i + img2img wired (img2img = M0d Part C single-run i2i via the postproc step)
    assert "ref" in caps["worker_modes"] and caps["modes"] == ["ref", "t2i", "img2img"]
    # the multi-ref capability flag (§11) the UI / future callers key on
    assert caps["multi_ref"]["via"] == "encode_image_refs"
    assert caps["multi_ref"]["max_refs"] >= 4
    # registered in the runner's adapter table
    from orchestrator.runner import ADAPTERS, VRAM_ESTIMATES
    assert ADAPTERS["flux2"] is flux2 and "flux2" in VRAM_ESTIMATES


def test_flux2_batch_argv_writes_shared_ref_and_items(tmp_path):
    """Stage-B fires ONE --jobs-file batch: jobs.json carries the SHARED hero reference +
    per-cell prompt/seed/meta, and the argv MODULE-invokes the worker (relative imports)."""
    out = tmp_path / "job_fx"
    out.mkdir()
    items = [{"prompt": "front view, portrait, neutral, a ranger", "seed": 1,
              "meta": {"coverage_cell": {"shot_size": "portrait"}, "method": "ref"}},
             {"prompt": "profile_left view, full body shot, neutral, a ranger", "seed": 2,
              "meta": {"coverage_cell": {"shot_size": "full_body"}, "method": "ref"}}]
    spec = JobSpec(pipeline="flux2", mode="ref",
                   params={"ref_images": [str(tmp_path / "hero.png")], "width": 1024,
                           "height": 1024, "model_name": "flux.2-klein-4b", "batch_items": items},
                   output_dir=out)
    argv = flux2.build_argv(spec, "python", flux2.resolve_script(CONFIG.pipeline_roots))
    # module-invoked, NOT run by bare path (the worker uses package-relative imports)
    assert argv[:3] == ["python", "-m", "pipeline.flux2.run_pipeline"]
    assert "--jobs-file" in argv
    jobs = json.loads((out / "jobs.json").read_text())
    assert jobs["shared"]["mode"] == "ref"
    assert jobs["shared"]["ref_images"] == [str(tmp_path / "hero.png")]
    assert jobs["shared"]["model_name"] == "flux.2-klein-4b"
    assert [it["prompt"] for it in jobs["items"]] == [items[0]["prompt"], items[1]["prompt"]]
    # init_image / strength are NOT a flux2-ref concept
    assert "init_image" not in jobs["shared"] and "strength" not in jobs["shared"]


def test_flux2_single_argv_emits_ref_image(tmp_path):
    spec = JobSpec(pipeline="flux2", mode="ref",
                   params={"prompt": "a ranger in a forest", "ref_images": ["/abs/hero.png"]},
                   output_dir=tmp_path)
    argv = flux2.build_argv(spec, "python", flux2.resolve_script(CONFIG.pipeline_roots))
    assert argv[:3] == ["python", "-m", "pipeline.flux2.run_pipeline"]
    assert "--ref-image" in argv and "/abs/hero.png" in argv
    assert "--mode" in argv and argv[argv.index("--mode") + 1] == "ref"


def test_vendored_flux2_imports_as_module():
    """The exact thing the runner does: `python -m pipeline.flux2.run_pipeline` with cwd = the
    vendored src dir. Guards the whole import graph — incl. the SELF-BOOTSTRAPPED BFL `flux2`
    lib path (the worker must put `<multistack>/flux2/src` on sys.path for `import flux2.util`).
    `--help` imports the module + exits 0, no GPU."""
    script = flux2.resolve_script(CONFIG.pipeline_roots)
    if script is None or "multistack" not in str(script):
        pytest.skip("vendored flux2 not present")
    src_dir = script.parents[2]
    env = {"PYTHONIOENCODING": "utf-8"}
    import os
    r = subprocess.run([sys.executable, "-m", "pipeline.flux2.run_pipeline", "--help"],
                       cwd=str(src_dir), capture_output=True, text=True, timeout=120,
                       env={**os.environ, **env})
    assert r.returncode == 0, f"vendored flux2 failed to import:\n{r.stderr[-1500:]}"
    assert "--ref-image" in r.stdout and "ref" in r.stdout   # the multi-ref CLI is wired


# --- Stage-B endpoint routing (TestClient, no GPU) ------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def _asset_with_hero(client, ws, *, name="Ref"):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    a = assets.create_asset(ws, name=name, prompt_template="a ranger")["profile"]
    d = ws.out_dir / "job_castref"
    d.mkdir(parents=True, exist_ok=True)
    (d / "c0.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (d / "c0.json").write_text('{"seed": 5}', encoding="utf-8")
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "cast"},
                        batch_id="bat_cr", index=0, batch_size=1,
                        requester_id=a["active_version"], profile_version_id=a["active_version"],
                        stage="A")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_castref/c0.png",
                                  "output_names": ["job_castref/c0.png"], "seed": 5}
    client.post(f"/assets/{a['id']}/casting/star", json={"job_id": jid, "starred": True})
    return a


def test_stage_b_flux2_dry_run_routes_to_ref(client):
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(client, RUNNER.workspace)
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"pipeline": "flux2", "preset": "npc_lite", "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pipeline"] == "flux2" and body["planned_jobs"] == 1
    assert "ref" in body["split"] and body["split"]["ref"] > 0
    # the dry-run argv module-invokes flux2 with the hero as a reference
    assert "-m" in body["first_argv"] and "pipeline.flux2.run_pipeline" in body["first_argv"]
    assert "--ref-image" in body["first_argv"]
    # flux2 reference-conditioning carries identity → the inswapper pass is NOT auto-armed
    assert body["identity"] is False


def test_stage_b_flux2_dev_advanced_prompt_emits_json_cells(client):
    """M0d — flux.2-dev + advanced_prompt in Stage-B expansion emits per-cell STRUCTURED JSON
    prompts (the Mistral VLM parses JSON); klein/base get the labeled directive string. The
    dry-run reports json_prompt + the first cell's prompt is valid JSON."""
    import json
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(client, RUNNER.workspace, name="DevJson")
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"pipeline": "flux2", "preset": "npc_lite", "advanced_prompt": True,
                          "params": {"model_name": "flux.2-dev"}, "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["advanced_prompt"] is True and body["json_prompt"] is True
    obj = json.loads(body["first_cell"]["prompt"])          # the per-cell prompt is JSON
    assert obj["pose"] and obj["shot"] and obj["expression"]
    # a klein model with the same toggle stays the labeled (non-JSON) directive string
    r2 = client.post(f"/assets/{a['id']}/stage-b",
                     json={"pipeline": "flux2", "preset": "npc_lite", "advanced_prompt": True,
                           "dry_run": True})
    body2 = r2.json()
    assert body2["json_prompt"] is False
    assert not body2["first_cell"]["prompt"].lstrip().startswith("{")


def test_stage_b_flux2_dev_defaults_to_512(client):
    """User 2026-06-21: a flux.2-dev Stage-B expansion ran at the 1024² StageBRequest default
    (~4k tokens → tens of minutes). M0e Part A now reaches Stage-B too — an UNSET size resolves
    to dev's 512² (far faster); non-dev keeps the 1024² request default; explicit dims win;
    a params-channel model_name resolves it too."""
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(client, RUNNER.workspace, name="DevSize")

    def argv_dims(payload):
        argv = client.post(f"/assets/{a['id']}/stage-b",
                           json={"pipeline": "flux2", "preset": "npc_lite", "dry_run": True,
                                 **payload}).json()["first_argv"]
        return (argv[argv.index("--width") + 1], argv[argv.index("--height") + 1])

    assert argv_dims({"model_name": "flux.2-dev"}) == ("512", "512")             # dev → 512²
    assert argv_dims({"model_name": "flux.2-klein-4b"}) == ("1024", "1024")      # non-dev → request default
    assert argv_dims({"model_name": "flux.2-dev",
                      "width": 768, "height": 768}) == ("768", "768")            # explicit wins
    assert argv_dims({"params": {"model_name": "flux.2-dev"}}) == ("512", "512")  # params-channel model


def test_stage_b_flux2_rejects_mixed(client):
    from orchestrator.runner import RUNNER
    a = _asset_with_hero(client, RUNNER.workspace, name="Ref2")
    r = client.post(f"/assets/{a['id']}/stage-b",
                    json={"pipeline": "flux2", "preset": "npc_lite", "realize": "mixed",
                          "bg_mask": "x/y_bgmask.png", "dry_run": True})
    assert r.status_code == 422 and "reference-conditioned" in r.text


def test_stage_b_flux2_cells_are_individual_warm_jobs(client, monkeypatch):
    """M2.7: flux2 Expansion emits N INDIVIDUAL cell-jobs (not one batch), each a `ref`-mode warm
    job — meta.method is 'ref' (the actual realization, persisted into ref_set.method), the hero
    rides as the reference, and ALL cells share one `warm_group` so a single warm worker services
    the whole sweep (the model loads once)."""
    from orchestrator import components
    from orchestrator.runner import RUNNER
    monkeypatch.setattr(components, "image_model_present", lambda repo_id: True)  # skip weight gate
    RUNNER.pause()                                          # queue them; never dispatch to the GPU
    a = _asset_with_hero(client, RUNNER.workspace, name="RefMeta")
    r = client.post(f"/assets/{a['id']}/stage-b", json={"pipeline": "flux2", "preset": "npc_lite"})
    assert r.status_code == 200, r.text
    job_ids = r.json()["job_ids"]
    assert len(job_ids) >= 2                                # one job PER cell, not a single batch
    groups = set()
    for jid in job_ids:
        job = RUNNER.jobs[jid]
        p = job["params"]
        assert "batch_items" not in p                      # individual, not a batch
        assert p["meta"]["method"] == "ref"
        assert p["ref_images"] and "init_image" not in p   # hero rides as the reference
        assert job["coverage_cell"] is not None            # cell metadata on the job (curation)
        groups.add(job["warm_group"])
    assert len(groups) == 1 and next(iter(groups))         # all cells share ONE warm worker
    # user 2026-06-27: ONE seed for the whole sweep (not 0,1,2…); unset → a single random draw.
    seeds = {RUNNER.jobs[jid]["params"]["seed"] for jid in job_ids}
    assert len(seeds) == 1                                 # every cell shares the sweep's seed


def test_flux2_t2i_casting_dry_run(client):
    """flux2 is now a first-class casting/sandbox t2i generator (not only inside `multi`).
    /generate accepts pipeline=flux2 mode=t2i and module-invokes the worker with the offload
    swap forced (klein flow + Qwen3 encoder don't co-fit 16 GB on a single run)."""
    r = client.post("/generate",
                    json={"pipeline": "flux2", "prompt": "a lone astronaut", "count": 1,
                          "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pipeline"] == "flux2"
    assert "-m" in body["argv"] and "pipeline.flux2.run_pipeline" in body["argv"]
    assert "--mode" in body["argv"] and body["argv"][body["argv"].index("--mode") + 1] == "t2i"
    assert "--cpu-offload" in body["argv"]      # forced — fits 16 GB via the CPU<->GPU swap


def test_flux2_single_argv_forces_cpu_offload():
    from orchestrator.adapters import flux2 as fx
    from orchestrator.adapters.base import JobSpec
    spec = JobSpec(pipeline="flux2", mode="t2i", params={"prompt": "x"}, output_dir=Path("."))
    argv = fx.build_argv(spec, "python", fx.resolve_script(CONFIG.pipeline_roots))
    assert argv.count("--cpu-offload") == 1     # added once, not duplicated


def test_capabilities_includes_flux2(client):
    """Review 2026-06-13 Low: the runner registers flux2 + the adapter exposes caps, so
    /capabilities must list it."""
    caps = client.get("/capabilities").json()["pipelines"]
    assert "flux2" in caps
    assert caps["flux2"]["present"] is True and caps["flux2"]["modes"] == ["ref", "t2i", "img2img"]
    assert caps["flux2"]["multi_ref"]["via"] == "encode_image_refs"
