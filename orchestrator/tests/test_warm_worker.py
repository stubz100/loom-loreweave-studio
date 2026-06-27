"""M2.7 — warm-worker batch queue. Phase 1: the flux2 `--serve` stdin/stdout protocol.

The persistent worker reads one JSON job per stdin line, loads the model ONCE, and emits one
image + a `[serve-result] <json>` line per job. The GPU generation is exercised on-rig; these
no-GPU tests cover the PROTOCOL (line framing, lazy single load, shutdown, per-job failure
isolation) via an injected fake generator — exactly the contract the runner's warm dispatch
(Phase 1c) will speak.

Run from the loom root: `python -m pytest orchestrator/tests/test_warm_worker.py -q`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# the vendored pipeline must be importable in-process (workers are normally subprocess-invoked)
_LOOM = Path(__file__).resolve().parents[2]
_MULTISTACK = _LOOM / "pipelines" / "multistack"
for _p in (_MULTISTACK / "src", _MULTISTACK / "flux2" / "src"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


class _FakeGen:
    """Stand-in for _ServeGenerator — no model, records jobs, returns canned results."""

    def __init__(self):
        self.jobs: list[dict] = []
        self.closed = False
        self.load_count = 0  # a real generator lazy-loads once; the fake just counts generate()s

    def generate(self, job: dict) -> dict:
        self.jobs.append(job)
        if job.get("prompt") == "BOOM":
            raise RuntimeError("kaboom")  # one image's failure must not kill the worker
        return {"job_id": job.get("job_id"), "status": "ok",
                "output_path": f"/out/{job.get('job_id')}.png", "seed": job.get("seed"),
                "width": 512, "height": 512, "duration_s": 1.0,
                "meta": job.get("meta"), "error": None}

    def close(self):
        self.closed = True


def _run(lines):
    from pipeline.flux2.run_pipeline import run_serve, SERVE_RESULT_PREFIX
    out: list[str] = []
    fake = _FakeGen()
    rc = run_serve(in_stream=iter(lines), emit=out.append, generator=fake)
    results = [json.loads(s[len(SERVE_RESULT_PREFIX):]) for s in out if s.startswith(SERVE_RESULT_PREFIX)]
    return rc, fake, results


def test_serve_roundtrips_each_job_to_a_result_line():
    rc, fake, results = _run([
        json.dumps({"job_id": "j1", "prompt": "a", "seed": 1, "meta": {"cell": 1}}),
        json.dumps({"job_id": "j2", "prompt": "b", "seed": 2}),
    ])
    assert rc == 0 and fake.closed is True
    assert [r["job_id"] for r in results] == ["j1", "j2"]
    assert results[0]["status"] == "ok" and results[0]["output_path"] == "/out/j1.png"
    assert results[0]["meta"] == {"cell": 1}
    assert [j["job_id"] for j in fake.jobs] == ["j1", "j2"]


def test_serve_shutdown_stops_processing_remaining_lines():
    rc, fake, results = _run([
        json.dumps({"job_id": "j1", "prompt": "a"}),
        json.dumps({"cmd": "shutdown"}),
        json.dumps({"job_id": "j2", "prompt": "b"}),   # after shutdown — never processed
    ])
    assert rc == 0
    assert [j["job_id"] for j in fake.jobs] == ["j1"]   # j2 not generated
    assert [r["job_id"] for r in results] == ["j1"]
    assert fake.closed is True


def test_serve_bad_json_and_per_job_failure_are_isolated():
    rc, fake, results = _run([
        "this is not json",                                   # framing error → failed result, keep going
        json.dumps({"job_id": "ok1", "prompt": "fine"}),
        json.dumps({"job_id": "boom", "prompt": "BOOM"}),     # generate() raises → failed, worker lives
        json.dumps({"job_id": "ok2", "prompt": "fine"}),
    ])
    assert rc == 0 and fake.closed is True
    statuses = {r.get("job_id"): r["status"] for r in results}
    assert statuses[None] == "failed"          # the bad-json line
    assert statuses["ok1"] == "ok"
    assert statuses["boom"] == "failed"        # the raising job
    assert statuses["ok2"] == "ok"             # worker kept serving after the failure


def test_serve_blank_lines_ignored():
    rc, fake, results = _run(["", "   ", json.dumps({"job_id": "j1", "prompt": "a"}), ""])
    assert [r["job_id"] for r in results] == ["j1"]
