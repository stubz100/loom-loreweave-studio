"""multi adapter contract — no-GPU (P1/M2 step 2).

Covers the parts of the casting adapter that don't need weights: the module-invocation
argv, and `parse_result` reading a synthesized `multi_<run>.json` manifest into the pool of
candidate outputs (the 1-job → N-outputs shape the runner expands).
"""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.adapters import multi
from orchestrator.adapters.base import JobSpec


def test_build_argv_is_module_invocation(tmp_path):
    out = tmp_path / "proj" / "out" / "job_x"
    out.mkdir(parents=True)
    spec = JobSpec(pipeline="multi", mode="ideate",
                   params={"prompt": "a hero", "num_candidates": 2,
                           "ideation_mode": "fast", "width": 1024, "height": 1024, "seed": 7},
                   output_dir=out)
    argv = multi.build_argv(spec, "python", Path("x/pipeline/multi/run_pipeline.py"))
    assert argv[:4] == ["python", "-m", "pipeline.multi.run_pipeline", "ideate"]
    assert "--num-candidates" in argv and argv[argv.index("--num-candidates") + 1] == "2"
    assert argv[argv.index("--ideation-mode") + 1] == "fast"
    assert argv[argv.index("--seed") + 1] == "7"
    # sessions land under the project, not the monorepo default
    sdir = argv[argv.index("--sessions-dir") + 1]
    assert sdir.endswith("multi_sessions") and "proj" in sdir
    # candidates land under the job's out dir so /outputs can serve them
    assert argv[argv.index("--intermediate-root") + 1].endswith("_inter")


def _write_multi_manifest(out_dir: Path, candidate_paths: list[Path], status="completed"):
    out_dir.mkdir(parents=True, exist_ok=True)
    cands = [{"pipeline": p.parent.parent.name, "seed": 1, "candidate_index": i,
              "status": "ok", "output_path": str(p), "sub_manifest_path": "",
              "duration_s": 1.0, "error": ""} for i, p in enumerate(candidate_paths)]
    manifest = {
        "architecture": "batch", "prompt": "x", "seed": 1,
        "pipeline_duration_s": 12.3,
        "stages": [{"name": "ideate", "status": status,
                    "outputs": {"candidate_count": len(cands),
                                "succeeded": len(cands), "failed": 0,
                                "candidates": cands}}],
    }
    (out_dir / "multi_batch_20260605_s1.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_parse_result_collects_pool(tmp_path):
    out = tmp_path / "out" / "job_x"
    inter = out / "_inter" / "run" / "ideate"
    paths = []
    for pl in ("flux2", "sd35", "zimage"):
        d = inter / pl / "seed_1"
        d.mkdir(parents=True)
        f = d / f"{pl}_img.png"
        f.write_bytes(b"PNG")
        paths.append(f)
    _write_multi_manifest(out, paths)
    rec = multi.parse_result(0, "", "", out)
    assert rec.ok is True
    assert len(rec.outputs) == 3            # one job → the whole pool
    assert rec.manifest_status == "completed"
    assert rec.duration_s == 12.3          # job-level: the whole batch
    # per-IMAGE meta (inspector): each candidate carries its own gen time + sub-pipeline,
    # parallel to outputs so the runner can key it by output name.
    assert rec.outputs_meta is not None and len(rec.outputs_meta) == 3
    assert rec.outputs_meta[0]["duration_s"] == 1.0
    assert rec.outputs_meta[0]["pipeline"] == "flux2"


def test_parse_result_failed_ideate(tmp_path):
    out = tmp_path / "out" / "job_y"
    _write_multi_manifest(out, [], status="failed")
    rec = multi.parse_result(2, "", "", out)
    assert rec.ok is False and rec.outputs == []


def test_parse_result_no_manifest(tmp_path):
    out = tmp_path / "out" / "job_z"
    out.mkdir(parents=True)
    rec = multi.parse_result(0, "", "", out)
    assert rec.ok is False and "no multi manifest" in (rec.error or "")
