"""sd35 adapter contract + zimage Stage-B modes — no GPU (P1/M3, §5, R121).

The 1-page contract check for the Stage-B expansion adapters that don't need weights: the
file-path argv for img2img/inpaint, manifest-status-as-truth parse_result, capabilities
honesty, and that zimage now wires img2img/inpaint too.
"""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.adapters import sd35, zimage
from orchestrator.adapters.base import JobSpec


def _spec(mode, **params):
    p = {"prompt": "a hero", "width": 1024, "height": 1024, "seed": 7}
    p.update(params)
    return JobSpec(pipeline="sd35", mode=mode, params=p, output_dir=Path("out/job_x"))


def test_sd35_img2img_argv():
    argv = sd35.build_argv(_spec("img2img", init_image="/abs/hero.png", strength=0.6),
                           "python", Path("x/sd35/run_pipeline.py"))
    assert argv[1].endswith("run_pipeline.py")        # file-path invocation, not -m
    assert argv[argv.index("--mode") + 1] == "img2img"
    assert argv[argv.index("--init-image") + 1] == "/abs/hero.png"
    assert argv[argv.index("--strength") + 1] == "0.6"
    assert "--mask-image" not in argv                  # img2img takes no mask


def test_sd35_inpaint_argv_includes_mask():
    argv = sd35.build_argv(_spec("inpaint", init_image="/abs/hero.png",
                                 mask_image="/abs/mask.png", strength=1.0),
                           "python", Path("x/sd35/run_pipeline.py"))
    assert argv[argv.index("--mode") + 1] == "inpaint"
    assert argv[argv.index("--mask-image") + 1] == "/abs/mask.png"


def test_sd35_capabilities_are_honest():
    caps = sd35.capabilities([])
    assert caps["pipeline"] == "sd35"
    # t2i wired 2026-06-10 (the sandbox experimentation surface); CN modes stay unwired.
    assert set(caps["modes"]) == {"t2i", "img2img", "inpaint"}
    assert "cn-inpaint" in caps["worker_modes"]                # informational full capability
    assert caps["cancellable"] and caps["progress"] == "coarse"


def test_sd35_progress_markers():
    assert sd35.progress("[stage1] Pipeline loaded in 3s") == 0.25
    assert sd35.progress("[stage2] Generated in 5s") == 0.8
    assert sd35.progress("[done] Pipeline completed in 9s") == 1.0
    assert sd35.progress("noise") is None


def _write_manifest(out_dir: Path, img: Path, statuses):
    out_dir.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    stages = [{"name": n, "status": s} for n, s in statuses]
    (out_dir / "sd35_x.json").write_text(json.dumps(
        {"stages": stages, "pipeline_duration_s": 8.1, "output_path": str(img)}), encoding="utf-8")


def test_sd35_parse_result_completed(tmp_path):
    out = tmp_path / "out" / "job_x"
    img = out / "sd35_x.png"
    _write_manifest(out, img, [("load_pipeline", "completed"), ("generate", "completed"),
                               ("save", "completed")])
    rec = sd35.parse_result(0, "", "", out)
    assert rec.ok is True and rec.outputs == [str(img)] and rec.manifest_status == "completed"
    assert rec.duration_s == 8.1


def test_sd35_parse_result_failed_stage(tmp_path):
    out = tmp_path / "out" / "job_y"
    img = out / "sd35_x.png"
    _write_manifest(out, img, [("load_pipeline", "completed"), ("generate", "failed")])
    rec = sd35.parse_result(1, "", "boom", out)
    assert rec.ok is False and rec.manifest_status == "failed"


def test_sd35_parse_result_no_manifest(tmp_path):
    out = tmp_path / "out" / "job_z"
    out.mkdir(parents=True)
    rec = sd35.parse_result(0, "", "", out)
    assert rec.ok is False and "no manifest" in (rec.error or "")


def test_zimage_now_wires_stage_b_modes():
    caps = zimage.capabilities([])
    assert {"t2i", "img2img", "inpaint"} <= set(caps["modes"])  # M3 wired img2img/inpaint
