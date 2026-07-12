"""post-M2.11 — the model-free Lanczos `resize` postproc step (no GPU).

User 2026-07-12: a tile-CN "downscale" (1024²→512² via the Upscale✨ preset) smeared its
source — every diffusion-based step re-renders. `resize` is a pure-PIL io-worker: the
pixels survive the size change exactly; no model, no weights, no VRAM, no prompt.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.adapters import resize
from orchestrator.adapters.base import JobSpec
from orchestrator.config import CONFIG

APP_REPO = Path(__file__).resolve().parents[2]
WORKER = APP_REPO / "pipelines/multistack/src/pipeline/postproc/resize/run_pipeline.py"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def _base_image(base="job_rz01/base.png", prompt=None):
    """A base image on disk in the open project's out/ (the test_postproc_stack pattern).
    Resize needs NO producing job — it never re-renders, so no prompt to inherit."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    p = RUNNER.workspace.out_dir / base
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    return base


def _last_step_id(resp, base):
    stack = next(s for s in resp.json()["stacks"] if s["base"] == base)
    return stack["steps"][-1]["id"]


# --- the worker itself: real Lanczos end-to-end (pure PIL — cheap enough to execute) ----

def _load_worker():
    spec = importlib.util.spec_from_file_location("resize_worker", WORKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_worker_lanczos_resample_end_to_end(tmp_path):
    """A real 64² gradient → 32²: output exists at the target size, alpha survives, and the
    jobs_batch manifest is the shared shape (streaming/partial-honesty machinery contract)."""
    from PIL import Image
    src = tmp_path / "src.png"
    img = Image.new("RGBA", (64, 64))
    img.putdata([(x * 4, y * 4, 128, 255 if x < 32 else 0)
                 for y in range(64) for x in range(64)])
    img.save(src)
    out = tmp_path / "out"
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps({"width": 32, "height": 32,
                                  "items": [{"input": str(src), "seed": 7,
                                             "meta": {"k": "v"}}]}), encoding="utf-8")
    rc = _load_worker().run_batch(str(inputs), str(out))
    assert rc == 0
    (mpath,) = out.glob("resize_batch_*.json")
    m = json.loads(mpath.read_text(encoding="utf-8"))
    assert m["kind"] == "jobs_batch" and m["pipeline"] == "resize" and m["mode"] == "resize"
    assert m["status"] == "completed" and m["ok"] == 1 and m["failed"] == 0
    row = m["items"][0]
    assert row["status"] == "ok" and row["meta"]["k"] == "v"
    assert row["meta"]["resize"] == "64x64->32x32" and row["meta"]["resample"] == "lanczos"
    with Image.open(row["output_path"]) as got:
        assert got.size == (32, 32)
        assert got.mode == "RGBA"                    # alpha survives the resample
    # a missing input fails per-item (isolation), whole-batch rc=2 when nothing succeeded
    inputs.write_text(json.dumps({"width": 32, "height": 32,
                                  "items": [{"input": str(tmp_path / "nope.png")}]}),
                      encoding="utf-8")
    assert _load_worker().run_batch(str(inputs), str(tmp_path / "out2")) == 2


# --- adapter: argv + inputs.json --------------------------------------------------------

def test_adapter_writes_inputs_and_argv(tmp_path):
    out = tmp_path / "job_rz"
    out.mkdir(parents=True)
    spec = JobSpec(pipeline="resize", mode="resize",
                   params={"prompt": "[resize postproc of x]", "width": 512, "height": 512,
                           "batch_items": [{"input": "F:/img.png"}]},
                   output_dir=out)
    argv = resize.build_argv(spec, "python", Path("x/postproc/resize/run_pipeline.py"))
    assert "--inputs-file" in argv and "--output-dir" in argv
    payload = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
    assert payload["width"] == 512 and payload["height"] == 512
    assert payload["items"] == [{"input": "F:/img.png"}]
    assert resize.resolve_script(CONFIG.pipeline_roots) is not None   # vendored worker present


# --- API: configure + queue (no GPU, no prompt, no weights) -----------------------------

def test_resize_step_configure_queue_and_run_shape(client):
    """Configure (defaults to ×0.5) → queue: a `resize`-pipeline io job over the source with
    the Part B-resolved target dims; NO prompt inheritance needed (the base has no producing
    job on purpose) and NO weight/VRAM gate (pure CPU)."""
    from orchestrator.runner import RUNNER
    base = _base_image()
    r = client.post("/postproc/step", json={"base": base, "preset": "resize"})
    assert r.status_code == 200, r.text
    step = r.json()["stacks"][0]["steps"][0]
    assert step["backend"] == "resize" and step["mode"] == "resize"
    assert step["params"]["scale"] == 0.5                 # the preset default = the downscale
    sid = step["id"]
    d = client.post(f"/postproc/step/{sid}/queue", json={"dry_run": True})
    assert d.status_code == 200, d.text
    dj = d.json()
    # the stub base is unreadable → the 1024² dims fallback; ×0.5 → 512²
    assert dj["pipeline"] == "resize" and dj["mode"] == "resize"
    assert dj["params"]["width"] == 512 and dj["params"]["height"] == 512
    assert dj["params"]["batch_items"][0]["input"].endswith("base.png")
    q = client.post(f"/postproc/step/{sid}/queue", json={})
    assert q.status_code == 200, q.text
    jid = q.json()["stacks"][0]["steps"][0]["job_id"]
    job = RUNNER.get(jid)
    assert job["pipeline"] == "resize" and job["status"] == "queued"
    RUNNER.cancel(jid)
    # explicit W×H wins over the scale default (a second base — stacks are per-image)
    base2 = _base_image("job_rz02/base.png")
    sid2 = _last_step_id(client.post(
        "/postproc/step",
        json={"base": base2, "preset": "resize",
              "params": {"width": 768, "height": 432}}), base2)
    d2 = client.post(f"/postproc/step/{sid2}/queue", json={"dry_run": True}).json()
    assert d2["params"]["width"] == 768 and d2["params"]["height"] == 432


def test_resize_step_rejects_model_prompt_and_bad_size(client):
    base = _base_image("job_rz03/base.png")
    for bad in ({"strength": 0.3}, {"prompt": "x"}, {"model_name": "sd3.5-medium"},
                {"blend": 0.5}, {"scale": 8}, {"width": 512}):
        r = client.post("/postproc/step",
                        json={"base": base, "preset": "resize", "params": bad})
        assert r.status_code == 422, f"{bad} should be rejected: {r.text}"
    # the backend is preset-fixed (like restore/upscale)
    assert client.post("/postproc/step",
                       json={"base": base, "preset": "resize", "backend": "zimage"}
                       ).status_code == 422
