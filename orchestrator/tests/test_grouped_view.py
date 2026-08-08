"""Grouped image browser — the operation/derivation tree (author 2026-08-08, obs. 2).

The tree itself is FE-only (no JS runner in this repo — the established pattern is a source
contract here plus a behavioural check on the job data it stands on). What actually matters is
that the backend really emits the two keys the tree nests by: a shared `batch_id` per operation
and a `chained_from` pointing at the job an image was derived from.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from orchestrator.config import CONFIG

APP = Path(__file__).resolve().parents[2] / "app" / "src"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1,P2")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def test_an_operation_shares_one_batch_id_and_a_pass_points_at_its_source(client):
    """The two keys the tree nests by, asserted on real submissions:

    - every job of ONE operation carries the SAME `batch_id` (that is the group), and
    - a postprocessed image carries `chained_from` = the job it was made from (that is the
      nesting edge). Before 2026-08-08 the manual postproc surface set neither, so a
      grouped view would have shown a flat pile of unrelated tiles.
    """
    from orchestrator.runner import RUNNER

    RUNNER.pause()
    gen = client.post("/generate", json={"prompt": "a street", "pipeline": "zimage",
                                         "count": 3})
    assert gen.status_code == 200, gen.text
    ids = gen.json()["job_ids"]
    assert len(ids) == 3
    batch_ids = {RUNNER.get(i)["batch_id"] for i in ids}
    assert len(batch_ids) == 1 and batch_ids.pop()          # ONE group, non-empty

    # finish one of them so a postproc step has a real source image
    ws = RUNNER.workspace
    name = "job_g/img.png"
    (ws.out_dir / "job_g").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), "gray").save(ws.out_dir / name)
    src_job = RUNNER.jobs[ids[0]]
    src_job["status"] = "done"
    src_job["result"] = {"ok": True, "output_name": name, "output_names": [name]}

    store = client.post("/postproc/step", json={"base": name, "preset": "clean"}).json()
    step_id = next(s for s in store["stacks"] if s["base"] == name)["steps"][-1]["id"]
    q = client.post(f"/postproc/step/{step_id}/queue", json={})
    assert q.status_code == 200, q.text
    pp_id = next(st["job_id"] for st in
                 next(s for s in q.json()["stacks"] if s["base"] == name)["steps"]
                 if st["id"] == step_id)
    pp = RUNNER.get(pp_id)

    assert pp["chained_from"] == ids[0]      # nests under the tile it was made from
    assert pp["pass"] == "clean"             # and the branch is labelled with the operation
    # a solo postproc job has no batch of its own — the tree keys it individually rather than
    # collapsing every unrelated pass into one "no batch" bucket
    assert pp["batch_id"] == ""


def test_grouped_view_source_contract():
    """The FE contract: a DEDICATED module (M2.8 monolith policy), grouping by `batch_id`,
    nesting by `chained_from`, ONE tile renderer shared with the flat grid so the two views
    cannot drift, and a group delete that loops the audited per-job DELETE (R80) instead of
    inventing bulk semantics."""
    tree = (APP / "GroupedGrid.tsx").read_text(encoding="utf-8")
    app = (APP / "App.tsx").read_text(encoding="utf-8")

    assert "chained_from" in tree and "batch_id" in tree     # the two nesting keys
    assert "export function buildGroups" in tree
    # children hang off the parent JOB (a chained pass is 1→N, so per-output nesting would lie)
    assert "nodes.get(parentId)" in tree

    # the flat grid survives, behind a toggle, over the same scope
    assert 'setGrouped(false)' in app and 'setGrouped(true)' in app
    assert "{stageCells.map(renderTile)}" in app             # flat view still renders
    assert "const renderTile = (c: TileRef)" in app          # …with the SHARED renderer
    assert "renderTile={renderTile}" in app                  # …handed to the tree verbatim

    # group delete = N audited single deletes, and it reports what it could not remove
    assert "const onDeleteGroup" in app and "await deleteJob(j.id)" in app
    assert "cancel running jobs first" in app
