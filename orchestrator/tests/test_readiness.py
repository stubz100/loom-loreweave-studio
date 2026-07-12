"""P2/M4 — proxy readiness meter (no GPU).

Locks the advisory contract: coverage/dupes/captions computed inline from on-disk data,
the on-model tier harvested from an identity `score` job's batch manifest, snapshots
persisted to `readiness.json` + `version.readiness_status` — and all of it ADVISORY
(R14): nothing here ever blocks staging or training.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from orchestrator.config import CONFIG


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1,P2")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


_CELLS = [
    {"shot_size": "portrait", "angle": "front", "expression": "neutral", "background": ""},
    {"shot_size": "waist_up", "angle": "three_quarter_left", "expression": "smile", "background": ""},
    {"shot_size": "full_body", "angle": "profile_left", "expression": "serious", "background": "market"},
]


def _png(path: Path, kind: int) -> None:
    """A real, deterministic PNG per `kind` — distinct kinds get distinct dHashes,
    equal kinds are byte-identical (the near-duplicate case)."""
    img = Image.new("L", (64, 64))
    img.putdata([(x * (kind + 1) + y * kind + (x % (kind + 2)) * 37) % 256
                 for y in range(64) for x in range(64)])
    img.save(path)


def _curated_asset(client, *, kinds, cells=None):
    from orchestrator.runner import RUNNER

    ws = RUNNER.workspace
    a = client.post("/assets", json={"name": "Mara"}).json()["profile"]
    version_id = a["active_version"]
    RUNNER.pause()
    out_dir = ws.out_dir / "job_m4refs"
    out_dir.mkdir(parents=True, exist_ok=True)
    names, meta = [], {}
    for i, kind in enumerate(kinds):
        name = f"job_m4refs/ref{i}.png"
        _png(out_dir / f"ref{i}.png", kind)
        names.append(name)
        cell = (cells or _CELLS)[i % len(cells or _CELLS)]
        meta[name] = {"coverage_cell": cell, "seed": 100 + i}
    jid = RUNNER.submit(
        pipeline="zimage", mode="img2img",
        params={"prompt": "dataset", "batch_items": [{}] * len(kinds)},
        batch_id="bat_m4refs", index=0, batch_size=1,
        requester_id=version_id, profile_version_id=version_id, stage="B",
    )
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {
        "ok": True, "output_name": names[0], "output_names": names, "output_meta": meta,
    }
    for name in names:
        r = client.post(f"/assets/{a['id']}/refs/keep", json={"job_id": jid, "output": name})
        assert r.status_code == 200, r.text
    return a


def _vroot(client, asset):
    from orchestrator.runner import RUNNER
    detail = client.get(f"/assets/{asset['id']}").json()
    return (RUNNER.workspace.asset_dir("characters", detail["profile"]["slug"])
            / "versions" / "v1_base")


def test_readiness_live_view_scores_coverage_dupes_and_captions(client):
    """Three distinct refs across three cells: coverage axes report present+missing,
    the dupes tier finds nothing, captions count rides along, on_model is not_run —
    and the small set makes the whole meter 'info', never a block."""
    asset = _curated_asset(client, kinds=[1, 2, 3])
    r = client.get(f"/assets/{asset['id']}/readiness")
    assert r.status_code == 200, r.text
    body = r.json()
    cov = body["coverage"]
    assert cov["ref_count"] == 3 and cov["distinct_cells"] == 3
    assert cov["status"] == "info"                      # < MIN_REFS_INFO
    assert "back" in cov["axes"]["angle"]["missing"]
    assert cov["axes"]["angle"]["present"]["front"] == 1
    assert body["dupes"]["status"] == "ok" and body["dupes"]["extras"] == 0
    assert body["captions"]["count"] == 3
    assert body["on_model"]["status"] == "not_run"
    adv = body["advisory"]
    assert adv["recommended"] is False
    assert any("small ref set" in s for s in adv["reasons"])
    assert not (_vroot(client, asset) / "readiness.json").exists()   # GET never writes


def test_readiness_flags_near_duplicates(client):
    """Two byte-identical refs cluster (dHash distance 0) and the ratio crosses the warn
    line; the group names the ref ids so the author can cull one."""
    asset = _curated_asset(client, kinds=[1, 1, 3])
    body = client.get(f"/assets/{asset['id']}/readiness").json()
    dup = body["dupes"]
    assert dup["status"] == "warn" and dup["extras"] == 1
    assert len(dup["duplicate_groups"]) == 1 and len(dup["duplicate_groups"][0]) == 2
    assert any("near-duplicate" in s for s in body["advisory"]["reasons"])


def test_readiness_persist_writes_snapshot_and_version_status(client):
    asset = _curated_asset(client, kinds=[1, 2, 3])
    r = client.post(f"/assets/{asset['id']}/readiness", json={})
    assert r.status_code == 200, r.text
    vroot = _vroot(client, asset)
    snap = json.loads((vroot / "readiness.json").read_text(encoding="utf-8"))
    assert snap["kind"] == "loom.p2.readiness.v1"
    assert snap["persisted_at"] == snap["computed_at"]
    version = json.loads((vroot / "version.json").read_text(encoding="utf-8"))
    rs = version["readiness_status"]
    assert rs["status"] == "info" and rs["recommended"] is False
    assert rs["on_model"] == "not_run"
    # the live view now reports the snapshot's timestamp
    live = client.get(f"/assets/{asset['id']}/readiness").json()
    assert live["persisted_at"] == snap["persisted_at"]


def test_readiness_embed_queues_a_score_job_with_ref_meta(client):
    """The embed endpoint submits ONE identity `score` job: every curated ref as an
    absolute input stamped with meta.ref_id (the harvest key); no anchor set → the
    payload says so (centroid fallback R120). Queue only — nothing executes."""
    from orchestrator.runner import RUNNER

    asset = _curated_asset(client, kinds=[1, 2, 3])
    r = client.post(f"/assets/{asset['id']}/readiness/embed", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ref_count"] == 3 and body["anchor"] is False
    job = RUNNER.get(body["job_id"])
    assert job["pipeline"] == "identity" and job["mode"] == "score"
    assert job["stage"] == "D"
    p = job["params"]
    assert p["mode"] == "score" and p["anchor_image"] is None
    ids = {it["meta"]["ref_id"] for it in p["batch_items"]}
    detail = client.get(f"/assets/{asset['id']}").json()
    assert ids == {ref["id"] for ref in detail["versions"][0]["ref_set"]}
    assert all(Path(it["input"]).is_file() for it in p["batch_items"])


def test_readiness_harvests_a_done_score_job_into_on_model(client):
    """The client-closes-the-loop pattern: a DONE score job's batch manifest (scores in
    meta, no images) lands as the on_model tier — centroid mode without an anchor,
    outliers flagged below max(floor, mean−0.15) — and persists into readiness.json +
    version.readiness_status."""
    from orchestrator.runner import RUNNER

    asset = _curated_asset(client, kinds=[1, 2, 3])
    detail = client.get(f"/assets/{asset['id']}").json()
    refs = detail["versions"][0]["ref_set"]
    jid = client.post(f"/assets/{asset['id']}/readiness/embed", json={}).json()["job_id"]

    # craft the worker's product: a score-mode batch manifest (rows carry no images)
    ws = RUNNER.workspace
    out_dir = ws.out_dir / jid
    out_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for i, (ref, cos) in enumerate(zip(refs, [0.82, 0.79, 0.11])):   # ref 3 = off-model
        items.append({"index": i, "status": "ok", "seed": 0, "prompt": None,
                      "output_path": "", "manifest_path": "",
                      "meta": {"ref_id": ref["id"], "file": ref["file"],
                               "face": True, "det_score": 0.9, "centroid_cos": cos},
                      "error": ""})
    manifest = {"kind": "jobs_batch", "pipeline": "identity", "mode": "score",
                "status": "completed", "count": 3, "ok": 3, "failed": 0, "skipped": 0,
                "anchor": None, "anchor_face": False, "faces": 3,
                "min_det_score": 0.5, "total_duration_s": 1.2,
                "created_at": "2026-07-12T00:00:00+00:00", "items": items}
    mpath = out_dir / "identity_batch_20260712_000000.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    job = RUNNER.jobs[jid]
    job["status"] = "done"
    job["result"] = {"ok": True, "manifest_path": str(mpath)}

    r = client.post(f"/assets/{asset['id']}/readiness", json={"job_id": jid})
    assert r.status_code == 200, r.text
    om = r.json()["on_model"]
    assert om["mode"] == "centroid" and om["scored"] == 3
    assert om["status"] == "warn"
    assert om["outliers"] == [refs[2]["id"]]
    assert om["mean_cos"] == pytest.approx((0.82 + 0.79 + 0.11) / 3, abs=1e-3)

    vroot = _vroot(client, asset)
    snap = json.loads((vroot / "readiness.json").read_text(encoding="utf-8"))
    assert snap["on_model"]["job_id"] == jid
    version = json.loads((vroot / "version.json").read_text(encoding="utf-8"))
    assert version["readiness_status"]["on_model"] == "warn"
    # the live view keeps serving the harvested tier
    live = client.get(f"/assets/{asset['id']}/readiness").json()
    assert live["on_model"]["job_id"] == jid


def test_readiness_refusals(client):
    """Unknown asset 404s; an unfinished score job 409s; a job from another version is
    scope-guarded (409); embed on an empty ref set 400s; finalized versions refuse the
    mutators but keep the read view."""
    from orchestrator.runner import RUNNER

    assert client.get("/assets/ast_nope00/readiness").status_code == 404

    asset = _curated_asset(client, kinds=[1, 2, 3])
    jid = client.post(f"/assets/{asset['id']}/readiness/embed", json={}).json()["job_id"]
    assert client.post(f"/assets/{asset['id']}/readiness",
                       json={"job_id": jid}).status_code == 409      # still queued

    other = client.post("/assets", json={"name": "Rook"}).json()["profile"]
    assert client.post(f"/assets/{other['id']}/readiness/embed",
                       json={}).status_code == 400                   # empty ref_set
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True}
    assert client.post(f"/assets/{other['id']}/readiness",
                       json={"job_id": jid}).status_code == 409      # cross-version guard

    detail = client.get(f"/assets/{asset['id']}").json()
    vid = detail["versions"][0]["id"]
    assert client.post(f"/assets/{asset['id']}/versions/{vid}/finalize").status_code == 200
    assert client.post(f"/assets/{asset['id']}/readiness/embed", json={}).status_code == 400
    assert client.post(f"/assets/{asset['id']}/readiness", json={}).status_code == 400
    assert client.get(f"/assets/{asset['id']}/readiness").status_code == 200


def test_identity_adapter_score_contract(tmp_path):
    """Adapter contract for the new mode: build_argv writes a score-mode inputs.json with
    a null anchor; the score parser accepts a no-images manifest (ok without outputs);
    progress advances on `[item i/n]` lines instead of `  Image:` lines."""
    from orchestrator.adapters import identity
    from orchestrator.adapters.base import JobSpec

    out = tmp_path / "out"
    out.mkdir()
    spec = JobSpec(pipeline="identity", mode="score",
                   params={"anchor_image": None, "min_det_score": 0.4, "mode": "score",
                           "batch_items": [{"input": "x.png", "seed": 0,
                                            "meta": {"ref_id": "ref_000001"}}]},
                   output_dir=out)
    identity.build_argv(spec, "python", Path("run_pipeline.py"))
    payload = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
    assert payload["mode"] == "score" and payload["anchor"] is None
    assert payload["min_det_score"] == 0.4

    mpath = out / "identity_batch_20260712_000001.json"
    mpath.write_text(json.dumps({
        "kind": "jobs_batch", "pipeline": "identity", "mode": "score",
        "status": "completed", "count": 1, "ok": 1, "failed": 0, "skipped": 0,
        "items": [{"index": 0, "status": "ok", "seed": 0, "output_path": "",
                   "meta": {"ref_id": "ref_000001", "face": True, "centroid_cos": 0.9}}],
    }), encoding="utf-8")
    rec = identity.parse_result(0, "[batch-done]", "", out)
    assert rec.ok is True and rec.outputs == []
    assert rec.outputs_meta[0]["centroid_cos"] == 0.9

    prog = identity.make_progress(spec.params)
    assert prog("[stage1] Pipeline loaded in 3.2s (shared across 3 items)") == 0.10
    assert prog("[item 2/4] scored in 0.2s (face=True, cos 0.81)") == pytest.approx(0.54)
    assert prog("[batch-done] 4 ok / 0 failed / 0 skipped (4 face(s)) in 1s") == 0.99
    assert prog("noise") is None


def test_score_worker_source_contract():
    """The vendored worker must expose the score branch WITHOUT the swapper: `run_score`
    exists, `run_batch` dispatches on mode, and score never touches inswapper (the
    research-licensed weight stays un-fetched for measuring)."""
    import inspect
    worker = (Path(__file__).resolve().parents[2] / "pipelines" / "multistack" / "src"
              / "pipeline" / "postproc" / "identity" / "run_pipeline.py")
    src = worker.read_text(encoding="utf-8")
    assert "def run_score(" in src
    assert 'if (spec.get("mode") or "lock") == "score":' in src
    assert "with_swapper=False" in src
    score_body = src.split("def run_score(")[1].split("\ndef ")[0]
    assert "hf_hub_download" not in score_body       # no swapper fetch in score
    assert "centroid" in score_body                  # R120 fallback implemented
    assert 'output_path": ""' in score_body          # no images by design
