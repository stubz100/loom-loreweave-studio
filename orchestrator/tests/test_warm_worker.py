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


# --- Phase 1c: runner warm-worker dispatch (fake serve proc — no real subprocess / GPU) -----

import pytest  # noqa: E402
from pathlib import Path as _P  # noqa: E402


class _FakeStdin:
    def __init__(self, proc): self.proc = proc; self.closed = False; self.writes = []
    def write(self, s): self.writes.append(s); self.proc._on_stdin(s)
    def flush(self): pass
    def close(self): self.closed = True


class _FakeStdout:
    def __init__(self, proc): self.proc = proc
    def __iter__(self): return self
    def __next__(self):
        if self.proc._out:
            return self.proc._out.pop(0)
        raise StopIteration


class _FakeServeProc:
    """A fake `--serve` worker: each fed job writes a PNG into its `output_dir` and queues a
    `[serve-result]` line. ONE instance == one model load (the runner reuses it across a group)."""
    def __init__(self, die_without_result=False):
        self._out: list[str] = []
        self.stdin = _FakeStdin(self)
        self.stdout = _FakeStdout(self)
        self.jobs: list[dict] = []
        self.terminated = False
        self._die = die_without_result

    def _on_stdin(self, line):
        line = line.strip()
        if not line:
            return
        job = json.loads(line)
        if job.get("cmd") == "shutdown":
            return
        self.jobs.append(job)
        if self._die:
            return  # emit nothing → the runner sees EOF and fails the cell
        out_dir = _P(job["output_dir"]); out_dir.mkdir(parents=True, exist_ok=True)
        png = out_dir / "flux2_fake.png"; png.write_bytes(b"PNG")
        self._out.append("[serve-result] " + json.dumps({
            "job_id": job["job_id"], "status": "ok", "output_path": str(png),
            "seed": job.get("seed"), "duration_s": 1.0, "meta": job.get("meta"), "error": None}))

    def poll(self): return None
    def terminate(self): self.terminated = True
    def kill(self): self.terminated = True
    def wait(self, timeout=None): return 0


@pytest.fixture()
def bound_runner(monkeypatch, tmp_path):
    """A RUNNER bound to a fresh project + PAUSED so the worker thread never dispatches — the
    tests drive `_execute_warm` directly."""
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from fastapi.testclient import TestClient
    from orchestrator.main import app
    from orchestrator.runner import RUNNER
    with TestClient(app):
        RUNNER.pause()
        yield RUNNER
        if RUNNER._warm_proc is not None:
            RUNNER._warm_proc = RUNNER._warm_group = RUNNER._warm_pipeline = None
        RUNNER.unpause()


def _patch_spawn(monkeypatch, RUNNER, fakes, *, die=False):
    def fake_spawn(pipeline, group):
        p = _FakeServeProc(die_without_result=die)
        fakes.append(p)
        RUNNER._warm_proc, RUNNER._warm_group, RUNNER._warm_pipeline = p, group, pipeline
    monkeypatch.setattr(RUNNER, "_spawn_warm", fake_spawn)


def _submit_cell(RUNNER, group, i):
    return RUNNER.submit(
        pipeline="flux2", mode="ref", batch_id="bat_w", index=i, batch_size=99, warm_group=group,
        params={"prompt": f"cell{i}", "seed": i, "model_name": "flux.2-klein-4b",
                "meta": {"coverage_cell": {"angle": "front", "shot_size": "portrait"}}})


def test_warm_reuses_one_worker_and_records_each_cell(bound_runner, monkeypatch):
    R = bound_runner
    fakes: list = []
    _patch_spawn(monkeypatch, R, fakes)
    ids = [_submit_cell(R, "grp-A", i) for i in range(3)]
    for jid in ids:
        R._execute_warm(jid, "flux2", "ref", R.get(jid)["params"], "grp-A")
    for jid in ids:
        j = R.get(jid)
        assert j["status"] == "done"
        name = j["result"]["output_name"]
        assert name.endswith("flux2_fake.png")
        assert j["result"]["output_meta"][name]["coverage_cell"]["angle"] == "front"
        assert j["result"]["output_meta"][name]["seed"] == j["params"]["seed"]
    assert len(fakes) == 1                 # ONE worker serviced all 3 (model loaded once)
    assert len(fakes[0].jobs) == 3
    assert R._warm_proc is None            # evicted once the group drained


def test_warm_evicts_resident_worker_on_group_change(bound_runner, monkeypatch):
    R = bound_runner
    fakes: list = []
    _patch_spawn(monkeypatch, R, fakes)
    a1 = _submit_cell(R, "g1", 0); _a2 = _submit_cell(R, "g1", 1)   # a2 keeps g1 alive (no end-evict)
    b1 = _submit_cell(R, "g2", 0)
    R._execute_warm(a1, "flux2", "ref", R.get(a1)["params"], "g1")
    assert R._warm_group == "g1" and len(fakes) == 1               # still resident (g1 has a2 queued)
    R._execute_warm(b1, "flux2", "ref", R.get(b1)["params"], "g2")
    assert fakes[0].stdin.closed is True                           # g1 worker evicted on group change
    assert len(fakes) == 2 and fakes[1].jobs[0]["job_id"] == b1     # a fresh worker for g2


def test_warm_cell_fails_when_worker_dies_without_result(bound_runner, monkeypatch):
    R = bound_runner
    fakes: list = []
    _patch_spawn(monkeypatch, R, fakes, die=True)
    jid = _submit_cell(R, "grp-X", 0)
    R._execute_warm(jid, "flux2", "ref", R.get(jid)["params"], "grp-X")
    j = R.get(jid)
    assert j["status"] == "failed"
    assert R._warm_proc is None            # the dead worker was dropped


def test_warm_cell_chains_its_post_passes_on_completion(bound_runner, monkeypatch):
    """M2.7 Phase 2b: a warm cell carrying post_passes chains its pass(es) over its OWN output when
    it finishes (the same `_submit_chained` the cold path uses) — so a Stage-B sweep gets identity/
    clean/polish PER cell, each chained pass its own pause-safe tile, with the cell's coverage_cell
    carried through so curation still works."""
    R = bound_runner
    fakes: list = []
    _patch_spawn(monkeypatch, R, fakes)
    jid = R.submit(pipeline="flux2", mode="ref", batch_id="bat_pp", index=0, batch_size=1,
                   warm_group="grp-PP",
                   params={"prompt": "c", "seed": 1, "model_name": "flux.2-klein-4b",
                           "meta": {"coverage_cell": {"angle": "front", "shot_size": "portrait"}}},
                   post_passes=[{"pass": "clean", "backend": "zimage"}])
    R._execute_warm(jid, "flux2", "ref", R.get(jid)["params"], "grp-PP")
    assert R.get(jid)["status"] == "done"
    chained = [j for j in R.jobs.values() if j.get("chained_from") == jid]
    assert len(chained) == 1                                  # one chained pass over this cell
    cj = chained[0]
    assert cj["pipeline"] == "zimage" and cj["pass"] == "clean" and cj["status"] == "queued"
    items = cj["params"]["batch_items"]
    assert len(items) == 1 and items[0]["init_image"].endswith("flux2_fake.png")
    assert items[0]["meta"]["coverage_cell"]["angle"] == "front"   # curation survives the pass
    cj["status"] = "canceled"   # tidy: this real pass job is queued — don't let the shared runner
                                # dispatch it (a stray subprocess) once the fixture unpauses on teardown


# --- keep-warm-across-pause grace (M2.7): a quick pause→resume skips the model reload -----------

def test_warm_kept_across_a_brief_pause_then_evicted_after_grace(bound_runner, monkeypatch):
    """The resident worker is KEPT while a sweep is paused mid-flight (so resume reuses the loaded
    model), and only evicted once the pause outlasts WARM_IDLE_GRACE_S — or immediately when the
    sweep is drained / the project is gone / shutting down."""
    import time as _time
    from orchestrator import runner as _runner
    R = bound_runner                                # bound_runner leaves the queue PAUSED
    R._warm_proc = object()                         # pretend a worker is resident
    R._warm_group = "grp-P"
    _submit_cell(R, "grp-P", 0)                     # a queued same-group cell keeps the sweep alive

    # paused mid-sweep, grace clock not started yet → KEEP
    R._warm_idle_since = None
    with R._lock:
        assert R._warm_evict_reason_locked() is None
    # paused within the grace window → KEEP
    R._warm_idle_since = _time.time()
    with R._lock:
        assert R._warm_evict_reason_locked() is None
    # paused PAST the grace window → evict ('grace')
    R._warm_idle_since = _time.time() - (_runner.WARM_IDLE_GRACE_S + 5)
    with R._lock:
        assert R._warm_evict_reason_locked() == "grace"
    # no more queued same-group cells → 'drained' regardless of the clock
    R._warm_idle_since = _time.time()
    for j in R.jobs.values():
        if j.get("warm_group") == "grp-P":
            j["status"] = "done"
    with R._lock:
        assert R._warm_evict_reason_locked() == "drained"
    R._warm_proc = None                             # tidy the singleton for the next test
