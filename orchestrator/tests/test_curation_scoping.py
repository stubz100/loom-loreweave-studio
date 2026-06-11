"""Curation/casting scope guard (P1/M3 review) — a candidate can only be kept/starred into the
version that produced it (its job's profile_version_id), never cross-asset. TestClient-level so
it exercises the real endpoint guard (`_require_job_owned_by`), not just the assets layer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # Force-open a fresh project at startup (lazy CONFIG.project_dir_override reads env at call time).
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    from orchestrator.config import CONFIG
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def _done_stage_b_job(asset_version_id: str):
    """Submit + fake-complete a Stage-B img2img job owned by `asset_version_id`."""
    from orchestrator.runner import RUNNER
    out = RUNNER.workspace.out_dir / "job_scope001"
    out.mkdir(parents=True, exist_ok=True)
    (out / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    jid = RUNNER.submit(
        pipeline="zimage", mode="img2img", params={"prompt": "x"},
        batch_id="bat_scope", index=0, batch_size=1,
        requester_id=asset_version_id, profile_version_id=asset_version_id, stage="B",
        coverage_cell={"shot_size": "portrait", "angle": "front",
                       "expression": "neutral", "background": "studio"})
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_scope001/img.png", "seed": 1}
    return jid


def test_keep_rejects_cross_asset_then_accepts_owning_asset(client):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    a = assets.create_asset(RUNNER.workspace, name="Mara")["profile"]
    b = assets.create_asset(RUNNER.workspace, name="Bob")["profile"]
    jid = _done_stage_b_job(a["active_version"])           # job belongs to Mara's version

    # keeping Mara's job into Bob → 409 (scope guard)
    r = client.post(f"/assets/{b['id']}/refs/keep", json={"job_id": jid})
    assert r.status_code == 409, r.text

    # keeping into Mara (the owner) → 200, one curated ref with its coverage_cell
    r2 = client.post(f"/assets/{a['id']}/refs/keep", json={"job_id": jid})
    assert r2.status_code == 200, r2.text
    refs = r2.json()["ref_set"]
    assert len(refs) == 1 and refs[0]["coverage_cell"]["angle"] == "front"


def test_star_rejects_cross_asset_job(client):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    a = assets.create_asset(RUNNER.workspace, name="Cara")["profile"]
    b = assets.create_asset(RUNNER.workspace, name="Dan")["profile"]
    jid = _done_stage_b_job(a["active_version"])
    r = client.post(f"/assets/{b['id']}/casting/star", json={"job_id": jid})
    assert r.status_code == 409, r.text
