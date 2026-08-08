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
    """Two byte-identical refs IN THE SAME coverage cell cluster (dHash distance 0) and the
    ratio crosses the warn line; the group names the ref ids so the author can cull one."""
    same = _CELLS[0]
    asset = _curated_asset(client, kinds=[1, 1, 3], cells=[same, same, _CELLS[2]])
    body = client.get(f"/assets/{asset['id']}/readiness").json()
    dup = body["dupes"]
    assert dup["status"] == "warn" and dup["extras"] == 1
    assert len(dup["duplicate_groups"]) == 1 and len(dup["duplicate_groups"][0]) == 2
    assert dup["scope"] == "coverage_cell"
    assert dup["cells_compared"] == 1 and dup["cells_total"] == 2
    assert any("near-duplicate" in s for s in body["advisory"]["reasons"])


def test_readiness_dupes_compare_only_within_a_coverage_cell(client):
    """Retuned 2026-08-08. Two refs in DIFFERENT cells are *supposed* to look different, so
    they can never be duplicates of each other — even byte-identical ones. Comparing across
    cells is what flagged 57 of char02's 79 refs (all distinct cells, 0 true duplicates):
    8×8 dHash cannot see pose at 9×8 px, so a front and a profile full-body in the same
    scene hash alike. The tier must stay silent here."""
    asset = _curated_asset(client, kinds=[1, 1, 1],
                           cells=[_CELLS[0], _CELLS[1], _CELLS[2]])
    body = client.get(f"/assets/{asset['id']}/readiness").json()
    dup = body["dupes"]
    assert dup["duplicate_groups"] == [] and dup["extras"] == 0
    assert dup["status"] == "ok"
    assert dup["cells_compared"] == 0 and dup["cells_total"] == 3
    assert not any("near-duplicate" in s for s in body["advisory"]["reasons"])


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


def _score_manifest(refs, scores, *, anchor=None, anchor_face=False):
    """Craft the identity worker's product: a score-mode batch manifest (rows carry no
    images, only meta). `scores` is one centroid_cos per ref; None = no face found."""
    items = []
    for i, (ref, cos) in enumerate(zip(refs, scores)):
        meta = {"ref_id": ref["id"], "file": ref["file"],
                "face": cos is not None, "det_score": 0.9}
        if cos is not None:
            meta["centroid_cos"] = cos
        items.append({"index": i, "status": "ok", "seed": 0, "prompt": None,
                      "output_path": "", "manifest_path": "", "meta": meta, "error": ""})
    return {"kind": "jobs_batch", "pipeline": "identity", "mode": "score",
            "status": "completed", "count": len(items), "ok": len(items),
            "failed": 0, "skipped": 0, "anchor": anchor, "anchor_face": anchor_face,
            "faces": sum(1 for it in items if it["meta"]["face"]),
            "min_det_score": 0.5, "total_duration_s": 1.0,
            "created_at": "2026-08-08T00:00:00+00:00", "items": items}


def _harvest(client, asset, refs, scores, **kw):
    """Queue the embed job, hand it the manifest the worker would have written, persist."""
    from orchestrator.runner import RUNNER

    jid = client.post(f"/assets/{asset['id']}/readiness/embed", json={}).json()["job_id"]
    out_dir = RUNNER.workspace.out_dir / jid
    out_dir.mkdir(parents=True, exist_ok=True)
    mpath = out_dir / "identity_batch_20260808_000000.json"
    mpath.write_text(json.dumps(_score_manifest(refs, scores, **kw)), encoding="utf-8")
    job = RUNNER.jobs[jid]
    job["status"] = "done"
    job["result"] = {"ok": True, "manifest_path": str(mpath)}
    r = client.post(f"/assets/{asset['id']}/readiness", json={"job_id": jid})
    assert r.status_code == 200, r.text
    return r.json()


def _refs_of(client, asset):
    return client.get(f"/assets/{asset['id']}").json()["versions"][0]["ref_set"]


_EXPRS = ("neutral", "smile", "serious", "sad", "surprised")


def _portrait_cells():
    return [{"shot_size": "portrait", "angle": "front", "expression": e, "background": ""}
            for e in _EXPRS]


def test_on_model_outliers_are_judged_against_the_refs_own_coverage_cell(client):
    """Retuned 2026-08-08. All three FROZEN coverage axes move a face embedding on their
    own — measured on char02 (global mean 0.772): shot_size portrait +0.027 → full_body
    −0.097 · angle 3q-left +0.071 → front −0.063 · expression serious +0.033 → smile
    −0.054. One global threshold therefore flags whichever band sits lowest, i.e. exactly
    the diversity the coverage tier rewards (it flagged 4 `smile` refs of 6 outliers).

    Here every `full_body` ref scores 0.62 and every `portrait` 0.82 — a clean shot_size
    effect, not off-model refs. With the band offset NO ref is an outlier; against a global
    mean the whole full_body band would have been flagged."""
    cells = [{"shot_size": shot, "angle": "front", "expression": e, "background": ""}
             for shot in ("portrait", "full_body") for e in _EXPRS]
    asset = _curated_asset(client, kinds=list(range(1, 11)), cells=cells)
    refs = _refs_of(client, asset)
    scores = [0.82 if r["coverage_cell"]["shot_size"] == "portrait" else 0.62 for r in refs]

    om = _harvest(client, asset, refs, scores)["on_model"]
    assert om["scored"] == 10
    assert om["bands"]["shot_size"]["portrait"]["reference"] == "band"
    assert om["bands"]["shot_size"]["full_body"]["offset"] < -0.05      # the real effect
    # a whole band sitting low is a property of the axis, not a set of bad refs
    assert om["outliers"] == [] and om["status"] == "ok"
    assert "shot_size" in om["outlier_scope"]


def test_on_model_still_catches_a_ref_that_is_off_model_for_its_own_cell(client):
    """The band offset must not blunt the tier: a ref far below its OWN band still flags,
    and a big enough fraction of them still warns."""
    asset = _curated_asset(client, kinds=[1, 2, 3, 4, 5], cells=_portrait_cells())
    refs = _refs_of(client, asset)
    om = _harvest(client, asset, refs, [0.85, 0.84, 0.83, 0.82, 0.10])["on_model"]
    assert om["outliers"] == [refs[4]["id"]]
    assert om["outlier_ratio"] == pytest.approx(0.2, abs=1e-3)
    assert om["status"] == "warn"                      # 20 % > the 10 % warn ratio


def test_a_few_odd_refs_in_a_large_set_do_not_warn(client):
    """A handful of odd refs is normal in a big set — they are listed for review, but the
    verdict only turns on a meaningful FRACTION (char02: 3 of 77 = 3.9 %, reads ok)."""
    asset = _curated_asset(client, kinds=list(range(1, 21)), cells=_portrait_cells())
    refs = _refs_of(client, asset)
    scores = [0.80] * len(refs)
    scores[0] = 0.10                                   # 1 of 20 = 5 %
    om = _harvest(client, asset, refs, scores)["on_model"]
    assert om["outliers"] == [refs[0]["id"]]           # still surfaced for review
    assert om["outlier_ratio"] <= 0.1 and om["status"] == "ok"


def test_a_set_anchor_with_no_detectable_face_is_reported_not_hidden(client):
    """Rig 2026-08-08: char02 HAS an anchor and `embed_items` passed it correctly, but
    insightface found no face in it (masked, cropped close-up), so R120's centroid fallback
    engaged and every anchor_cos was null. The old tier reported a bare `mode: centroid` —
    indistinguishable from having no anchor at all. The author's anchor is a
    generation-support reference (flux2 ref image, inswapper deliberately off), so a
    rejected one is INFORMATION: it must surface as a note and never as a warn reason."""
    # ≥ MIN_REFS_INFO refs so the verdict can actually reach "recommended"
    cells = _portrait_cells() + [{"shot_size": "waist_up", "angle": "back",
                                  "expression": "neutral", "background": ""}]
    asset = _curated_asset(client, kinds=[1, 2, 3, 4, 5, 6], cells=cells)
    refs = _refs_of(client, asset)

    body = _harvest(client, asset, refs, [0.80] * 6,
                    anchor="C:/anchor.png", anchor_face=False)
    om, adv = body["on_model"], body["advisory"]
    assert om["mode"] == "centroid" and om["anchor_status"] == "no_face"
    assert any("anchor" in n for n in adv["notes"])         # said out loud
    assert not any("anchor" in r for r in adv["reasons"])   # but never a fault
    assert om["status"] == "ok" and adv["recommended"] is True

    # no anchor at all reads differently from a rejected one — same version, fresh scan
    body2 = _harvest(client, asset, refs, [0.80] * 6)
    assert body2["on_model"]["anchor_status"] == "absent"
    assert body2["advisory"]["notes"] == []


def test_readiness_never_blocks_staging_or_queueing(client):
    """R14 / §7, and the author's explicit 2026-08-08 instruction — the meter recommends,
    it never gates. A version reading `warn` + `recommended: false` must still stage AND
    queue a training job, and the payload says so in `blocking`."""
    asset = _curated_asset(client, kinds=[1, 2, 3, 4, 5], cells=_portrait_cells())
    refs = _refs_of(client, asset)

    body = _harvest(client, asset, refs, [0.85, 0.84, 0.83, 0.82, 0.10])
    assert body["advisory"]["recommended"] is False and body["advisory"]["status"] == "warn"
    assert body["advisory"]["blocking"] is False

    r = client.post(f"/assets/{asset['id']}/lora/stage", json={"steps": 10})
    assert r.status_code == 200, r.text
    queued = client.post(f"/training/staged/{r.json()['id']}/queue")
    assert queued.status_code == 200, queued.text    # a bad meter never stopped the train
