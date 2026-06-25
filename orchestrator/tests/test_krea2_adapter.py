"""Krea 2 Turbo adapter contract - no GPU."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.adapters import krea2
from orchestrator.adapters.base import JobSpec
from orchestrator.config import CONFIG


def test_krea2_present_and_capabilities():
    caps = krea2.capabilities(CONFIG.pipeline_roots)
    assert caps["present"] is True and caps["pipeline"] == "krea2"
    assert caps["modes"] == ["t2i"]
    assert caps["worker_modes"] == ["t2i"]
    assert "img2img" not in caps["modes"]
    from orchestrator.runner import ADAPTERS, VRAM_ESTIMATES
    assert ADAPTERS["krea2"] is krea2
    assert VRAM_ESTIMATES["krea2"] == 16.0


def test_krea2_t2i_argv():
    spec = JobSpec(
        pipeline="krea2",
        mode="t2i",
        params={
            "prompt": "a luminous city at dawn",
            "width": 768,
            "height": 768,
            "seed": 42,
            "model_name": "krea2-turbo",
            "num_steps": 8,
            "guidance_scale": 0.0,
            "quant_backend": "quanto",
            "quant_dtype": "int8",
            "quant_skip_modules": "linear,img_in",
        },
        output_dir=Path("out/job_krea"),
    )
    argv = krea2.build_argv(spec, "python", Path("x/krea2/run_pipeline.py"))
    assert argv[1].endswith("run_pipeline.py")
    assert "--mode" not in argv
    assert argv[argv.index("--model-name") + 1] == "krea2-turbo"
    assert argv[argv.index("--width") + 1] == "768"
    assert argv[argv.index("--quant-backend") + 1] == "quanto"
    assert argv[argv.index("--quant-dtype") + 1] == "int8"
    assert argv[argv.index("--quant-skip-modules") + 1] == "linear,img_in"


def _write_manifest(out_dir: Path, img: Path, statuses):
    out_dir.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    stages = [{"name": n, "status": s} for n, s in statuses]
    (out_dir / "krea2_x.json").write_text(json.dumps(
        {"stages": stages, "pipeline_duration_s": 12.5, "output_path": str(img)}), encoding="utf-8")


def test_krea2_parse_result_completed(tmp_path):
    out = tmp_path / "out" / "job_krea"
    img = out / "krea2_x.png"
    _write_manifest(out, img, [("load_pipeline", "completed"), ("generate", "completed"),
                               ("save", "completed")])
    rec = krea2.parse_result(0, "", "", out)
    assert rec.ok is True and rec.outputs == [str(img)] and rec.manifest_status == "completed"
    assert rec.duration_s == 12.5


def test_krea2_parse_result_failed_stage(tmp_path):
    out = tmp_path / "out" / "job_krea_fail"
    img = out / "krea2_x.png"
    _write_manifest(out, img, [("load_pipeline", "completed"), ("generate", "failed")])
    rec = krea2.parse_result(1, "", "boom", out)
    assert rec.ok is False and rec.manifest_status == "failed"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator import components
    monkeypatch.setattr(components, "weights_ok", lambda: (True, []))
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def test_generate_accepts_krea2_t2i_dry_run(client):
    r = client.post("/generate",
                    json={"pipeline": "krea2", "prompt": "a cinematic forest shrine",
                          "count": 1, "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pipeline"] == "krea2"
    assert "--mode" not in body["argv"]
    assert body["argv"][body["argv"].index("--width") + 1] == "768"
    assert body["argv"][body["argv"].index("--height") + 1] == "768"


def test_generate_rejects_krea2_img2img(client):
    r = client.post("/generate",
                    json={"pipeline": "krea2", "mode": "img2img", "prompt": "edit this",
                          "init_image": "x.png", "dry_run": True})
    assert r.status_code == 400
    assert "does not wire mode" in r.text
