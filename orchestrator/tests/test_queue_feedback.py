"""Queue-feedback fixes (review 2026-06-10): pause_reason surfaced + sticky-pause
auto-clear + `_canceled` hygiene + multi per-candidate progress + the `refs/keep`
error-path NameError + kill-tree cancel.

The user-reported symptom these pin down: "queue shows paused but nothing is running" —
caused by (a) resume-paused loads with no visible reason, and (b) `paused` staying True
after the last queued job was canceled/deleted.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # Force-open a fresh project at startup (lazy CONFIG.project_dir_override reads env
    # at call time) — same pattern as test_curation_scoping.
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    from orchestrator.config import CONFIG
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def _submit_queued(n: int = 1) -> list[str]:
    """Pause first so the worker can't dispatch (no GPU in tests), then queue n jobs."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    return [RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "x"},
                          batch_id="bat_qf", index=i, batch_size=n)
            for i in range(n)]


# --- pause_reason ---------------------------------------------------------------

def test_user_pause_reason_roundtrip(client):
    r = client.post("/queue/pause")
    assert r.json()["paused"] is True and r.json()["pause_reason"] == "user"
    r = client.post("/queue/unpause")
    assert r.json()["paused"] is False and r.json()["pause_reason"] is None


def test_resume_paused_load_reports_reason(client):
    from orchestrator.runner import RUNNER
    _submit_queued(1)
    # Re-bind the same workspace = the relaunch/open path (R88 resume-paused reload).
    RUNNER.bind(RUNNER.workspace)
    st = RUNNER.state()
    assert st["paused"] is True and st["pause_reason"] == "resume"
    assert client.get("/jobs").json()["pause_reason"] == "resume"
    RUNNER.unpause()  # leave the singleton clean for the next test


# --- sticky-pause auto-clear ------------------------------------------------------

def test_cancel_last_queued_clears_pause(client):
    from orchestrator.runner import RUNNER
    (jid,) = _submit_queued(1)
    assert RUNNER.is_paused()
    assert RUNNER.cancel(jid)
    st = RUNNER.state()
    assert st["counts"]["queued"] == 0
    assert st["paused"] is False and st["pause_reason"] is None   # no "paused (0 queued)"
    assert jid not in RUNNER._canceled                            # terminal — pruned


def test_cancel_one_of_two_keeps_pause(client):
    from orchestrator.runner import RUNNER
    jids = _submit_queued(2)
    assert RUNNER.cancel(jids[0])
    st = RUNNER.state()
    assert st["counts"]["queued"] == 1 and st["paused"] is True
    assert RUNNER.cancel(jids[1])
    assert RUNNER.state()["paused"] is False


def test_delete_last_queued_clears_pause(client):
    from orchestrator.runner import RUNNER
    (jid,) = _submit_queued(1)
    RUNNER.cancel(jid)            # -> terminal (and pause already cleared by cancel)
    RUNNER.pause()                # re-pause: deleting terminal work must also clear it
    assert RUNNER.delete(jid)
    st = RUNNER.state()
    assert st["paused"] is False and st["pause_reason"] is None


def test_delete_single_output_keeps_the_rest_then_whole_job(client):
    """User 2026-06-21: deleting a multi-output tile (a multi-cast candidate / Stage-B batch
    image) must remove ONLY that image, leaving the pool intact; the whole job is dropped only
    when its last output goes. DELETE /jobs/{id}/output."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    out = RUNNER.workspace.out_dir
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "x"},
                        batch_id="bat_del", index=0, batch_size=1)
    names = [f"{jid}/a.png", f"{jid}/b.png"]
    for n in names:
        p = out / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        p.with_suffix(".json").write_text("{}", encoding="utf-8")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_names": names, "output_name": names[0],
                                  "output_meta": {names[0]: {"seed": 1}, names[1]: {"seed": 2}}}
    # delete the first image → its file (+ sidecar) gone, the other stays, job survives pruned
    r = client.request("DELETE", f"/jobs/{jid}/output", params={"output": names[0]})
    assert r.status_code == 200 and r.json()["outcome"] == "output", r.text
    assert not (out / names[0]).exists() and not (out / names[0]).with_suffix(".json").exists()
    assert (out / names[1]).exists()
    job = client.get(f"/jobs/{jid}").json()
    assert job["result"]["output_names"] == [names[1]]
    assert names[0] not in (job["result"].get("output_meta") or {})
    assert job["result"]["output_name"] == names[1]
    # an unknown output → 404, the job is untouched
    assert client.request("DELETE", f"/jobs/{jid}/output",
                          params={"output": f"{jid}/nope.png"}).status_code == 404
    # deleting the last remaining output drops the WHOLE job (+ its dir)
    r2 = client.request("DELETE", f"/jobs/{jid}/output", params={"output": names[1]})
    assert r2.status_code == 200 and r2.json()["outcome"] == "job"
    assert client.get(f"/jobs/{jid}").status_code == 404
    assert not (out / jid).exists()


# --- multi per-candidate progress -------------------------------------------------

def test_multi_make_progress_counts_candidates():
    from orchestrator.adapters import multi
    prog = multi.make_progress({"num_candidates": 2})   # total = 2 × 3 pipelines = 6
    assert prog("[arch] minted new session ses_x") == pytest.approx(0.02)
    assert prog("noise") is None
    seen = []
    for i in range(6):
        s = prog(f"[stage_runner] $ python -m pipeline.x.run_pipeline {i}")
        d = prog("[done] Pipeline completed in 12.3s")
        seen += [s, d]
    assert seen == sorted(seen)                       # monotonic
    assert seen[1] == pytest.approx(0.05 + 0.90 / 6)  # first completion = 1/6
    assert seen[-1] == pytest.approx(0.95)            # all 6 done -> 0.95 (1.0 = parse_result)
    assert prog("[batch] ideate produced 6 candidate(s)") == pytest.approx(0.95)


def test_multi_make_progress_failed_candidate_does_not_freeze():
    from orchestrator.adapters import multi
    prog = multi.make_progress({"num_candidates": 1})   # total = 3
    prog("[stage_runner] $ a")          # cand 1 starts
    # cand 1 fails (no [done]); cand 2 starting must still advance past cand 1's slot
    p2 = prog("[stage_runner] $ b")
    assert p2 == pytest.approx(0.05 + 0.90 / 3)


# --- refs/keep error path (the un-imported `coverage` NameError) -------------------

def test_keep_ref_failure_is_400_not_500(client, monkeypatch):
    from orchestrator import assets, workspace as ws_mod
    from orchestrator.runner import RUNNER
    a = assets.create_asset(RUNNER.workspace, name="ErrPath")["profile"]
    out = RUNNER.workspace.out_dir / "job_errpath"
    out.mkdir(parents=True, exist_ok=True)
    (out / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    jid = RUNNER.submit(
        pipeline="zimage", mode="img2img", params={"prompt": "x"},
        batch_id="bat_err", index=0, batch_size=1,
        requester_id=a["active_version"], profile_version_id=a["active_version"],
        stage="B", coverage_cell={"shot_size": "portrait", "angle": "front",
                                  "expression": "neutral", "background": "studio"})
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_errpath/img.png", "seed": 1}

    def boom(*args, **kwargs):
        raise ws_mod.WorkspaceError("forced keep failure")
    monkeypatch.setattr(assets, "keep_ref", boom)
    # Pre-fix this path raised NameError (`coverage` not imported in main) -> a 500.
    r = client.post(f"/assets/{a['id']}/refs/keep", json={"job_id": jid})
    assert r.status_code == 400 and "forced keep failure" in r.text


# --- kill-tree cancel ---------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="taskkill tree-kill is the win32 path")
def test_kill_tree_fells_grandchild():
    """The multi-cancel regression: killing only the direct child leaves the grandchild
    (the process actually 'holding the GPU') running. _kill_tree must take both."""
    from orchestrator.runner import JobRunner
    child_src = ("import subprocess,sys,time;"
                 "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                 "print(p.pid,flush=True);time.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", child_src],
                            stdout=subprocess.PIPE, text=True)
    grandchild_pid = int(proc.stdout.readline().strip())
    JobRunner._kill_tree(proc, grace_s=10.0)
    assert proc.poll() is not None
    q = subprocess.run(["tasklist", "/FI", f"PID eq {grandchild_pid}"],
                       capture_output=True, text=True)
    assert str(grandchild_pid) not in q.stdout, "grandchild survived the cancel"


@pytest.mark.skipif(sys.platform != "win32", reason="per-job Job Object is the win32 path")
def test_cancel_job_object_fells_tree():
    """The per-job kill Job Object (review 2026-06-13, High): TerminateJobObject reaps the
    worker AND its descendants ATOMICALLY — independent of taskkill tree-walking (which the
    review flagged as unchecked + able to miss a reparented descendant). The descendant is
    spawned AFTER the worker joins the job (as in the real spawn → assign → worker-runs flow),
    so it auto-joins the job and TerminateJobObject takes it too."""
    from orchestrator.runner import (_create_kill_job, _assign_to_job, _terminate_job, _close_job)
    job = _create_kill_job()
    assert job, "_create_kill_job returned no handle on win32"
    # the worker waits on stdin before spawning its grandchild — so we can assign it to the
    # job FIRST (the grandchild then inherits job membership, like a real multi/flux2 worker).
    child_src = ("import subprocess,sys,time;"
                 "sys.stdin.readline();"
                 "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                 "print(p.pid,flush=True);time.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", child_src],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert _assign_to_job(proc, job)            # worker joins the job
    proc.stdin.write("go\n")
    proc.stdin.flush()
    grandchild_pid = int(proc.stdout.readline().strip())   # spawned AFTER joining → in the job
    assert _terminate_job(job)                  # atomic tree kill
    proc.wait(timeout=10)
    assert proc.poll() is not None
    q = subprocess.run(["tasklist", "/FI", f"PID eq {grandchild_pid}"],
                       capture_output=True, text=True)
    assert str(grandchild_pid) not in q.stdout, "grandchild survived TerminateJobObject"
    _close_job(job)
