"""P1-12 — curation throughput (no GPU): the persistent `rejected[]` record behind the
Stage-C bulk/keyboard reject sweep (~100→~30). Rejection is a lightweight cull-from-view
mark (out/-relative names in version.json — no image copy), mutually exclusive with
ref_set membership (keep wins), ownership-guarded like refs/keep.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orchestrator.config import CONFIG


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


_CELL = {"shot_size": "portrait", "angle": "front", "expression": "neutral",
         "background": ""}


def _asset_with_stage_b_job(ws, *, name="CurateAsset", n_outputs=2):
    """An asset + a done Stage-B batch job with n outputs carrying coverage_cell meta."""
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    a = assets.create_asset(ws, name=name)["profile"]
    out = ws.out_dir / "job_cur01"
    out.mkdir(parents=True, exist_ok=True)
    names, meta = [], {}
    for i in range(n_outputs):
        (out / f"img{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        n = f"job_cur01/img{i}.png"
        names.append(n)
        meta[n] = {"coverage_cell": _CELL, "seed": 100 + i}
    jid = RUNNER.submit(pipeline="zimage", mode="img2img",
                        params={"prompt": "[dataset]", "batch_items": [{}] * n_outputs},
                        batch_id="bat_cur", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="B")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": names[0],
                                  "output_names": names, "output_meta": meta}
    return a, jid, names


def test_reject_unreject_roundtrip_persists(client):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a, jid, names = _asset_with_stage_b_job(ws)
    r = client.post(f"/assets/{a['id']}/refs/reject",
                    json={"job_id": jid, "output": names[0]})
    assert r.status_code == 200, r.text
    assert r.json()["rejected"] == [names[0]]
    # persisted on disk (survives reload — the whole point of the record)
    detail = assets.get_asset(ws, a["id"])
    v = next(x for x in detail["versions"] if x["id"] == a["active_version"])
    assert v["rejected"] == [names[0]]
    # idempotent + reversible
    r2 = client.post(f"/assets/{a['id']}/refs/reject",
                     json={"job_id": jid, "output": names[0]})
    assert r2.json()["rejected"] == [names[0]]
    r3 = client.post(f"/assets/{a['id']}/refs/reject",
                     json={"job_id": jid, "output": names[0], "rejected": False})
    assert r3.json()["rejected"] == []


def test_reject_kept_output_409_and_keep_unrejects(client):
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a, jid, names = _asset_with_stage_b_job(ws)
    # keep img0 → rejecting it must 409 (cull first)
    rk = client.post(f"/assets/{a['id']}/refs/keep",
                     json={"job_id": jid, "output": names[0]})
    assert rk.status_code == 200, rk.text
    r = client.post(f"/assets/{a['id']}/refs/reject",
                    json={"job_id": jid, "output": names[0]})
    assert r.status_code == 409 and "KEPT" in r.text
    # reject img1, then keep it → the keep wins and clears the mark
    client.post(f"/assets/{a['id']}/refs/reject", json={"job_id": jid, "output": names[1]})
    rk2 = client.post(f"/assets/{a['id']}/refs/keep",
                      json={"job_id": jid, "output": names[1]})
    assert rk2.status_code == 200, rk2.text
    assert rk2.json()["rejected"] == []


def test_reject_contract_requires_done_and_coverage_cell(client):
    """Review 2026-06-11 Low: /refs/reject mirrors keep's Stage-C contract — only
    completed, coverage-bearing dataset outputs land in the cull record."""
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a, jid, names = _asset_with_stage_b_job(ws)
    RUNNER.jobs[jid]["status"] = "running"
    r = client.post(f"/assets/{a['id']}/refs/reject",
                    json={"job_id": jid, "output": names[0]})
    assert r.status_code == 409 and "done" in r.text
    RUNNER.jobs[jid]["status"] = "done"
    # an owned, done output WITHOUT a coverage_cell (not a dataset cell) → 422
    extra = "job_cur01/extra.png"
    (ws.out_dir / "job_cur01" / "extra.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    RUNNER.jobs[jid]["result"]["output_names"].append(extra)
    r2 = client.post(f"/assets/{a['id']}/refs/reject",
                     json={"job_id": jid, "output": extra})
    assert r2.status_code == 422 and "coverage_cell" in r2.text


def test_reject_scope_and_membership_guards(client):
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a, jid, names = _asset_with_stage_b_job(ws)
    b, _, _ = _asset_with_stage_b_job(ws, name="OtherAsset")
    # asset A's job can't be rejected into asset B (ownership, 409)
    r = client.post(f"/assets/{b['id']}/refs/reject",
                    json={"job_id": jid, "output": names[0]})
    assert r.status_code == 409, r.text
    # an output the job didn't produce → 409
    r2 = client.post(f"/assets/{a['id']}/refs/reject",
                     json={"job_id": jid, "output": "job_cur01/not_mine.png"})
    assert r2.status_code == 409, r2.text
