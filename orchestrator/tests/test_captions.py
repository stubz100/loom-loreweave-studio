"""P2/M3 — the caption-edit override layer (no GPU).

Locks the M3 design fork the M2 close flagged:

- edits are DURABLE per-ref overrides on the version (they survive re-staging);
- staging RESPECTS them (dataset `.txt` + captions.jsonl carry the edited text,
  `origin: template|edited` per row);
- `captions_hash` reflects the FINAL text (an edit changes it; a reset restores it),
  while `caption_policy_hash` still identifies the template.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.config import CONFIG


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1,P2")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


_CELL = {"shot_size": "portrait", "angle": "front", "expression": "neutral", "background": ""}


def _curated_asset(client, *, n=3):
    from orchestrator.runner import RUNNER

    ws = RUNNER.workspace
    a = client.post("/assets", json={"name": "Mara"}).json()["profile"]
    version_id = a["active_version"]
    RUNNER.pause()
    out_dir = ws.out_dir / "job_m3refs"
    out_dir.mkdir(parents=True, exist_ok=True)
    names, meta = [], {}
    for i in range(n):
        name = f"job_m3refs/ref{i}.png"
        (out_dir / f"ref{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n m3")
        names.append(name)
        meta[name] = {"coverage_cell": {**_CELL, "background": f"room {i}"}, "seed": 100 + i}
    jid = RUNNER.submit(
        pipeline="zimage", mode="img2img",
        params={"prompt": "dataset", "batch_items": [{}] * n},
        batch_id="bat_m3refs", index=0, batch_size=1,
        requester_id=version_id, profile_version_id=version_id, stage="B",
    )
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {
        "ok": True,
        "output_name": names[0],
        "output_names": names,
        "output_meta": meta,
    }
    for name in names:
        r = client.post(f"/assets/{a['id']}/refs/keep", json={"job_id": jid, "output": name})
        assert r.status_code == 200, r.text
    return a


def _vroot(client, asset):
    from orchestrator.runner import RUNNER
    detail = client.get(f"/assets/{asset['id']}").json()
    slug = detail["profile"]["slug"]
    return RUNNER.workspace.asset_dir("characters", slug) / "versions" / "v1_base"


def test_captions_listing_previews_templates_without_staging(client):
    """GET /assets/{id}/captions is a read-only preview: template rows from the frozen
    coverage contract, derived trigger, and NO captions.jsonl side effect."""
    asset = _curated_asset(client)
    r = client.get(f"/assets/{asset['id']}/captions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3 and body["edited_count"] == 0
    assert body["trigger_token"] == "mara_lw"
    for row in body["captions"]:
        assert row["origin"] == "template"
        assert row["caption"] == row["template_caption"]
        assert row["caption"].startswith("mara_lw, front view, portrait, neutral")
        assert row["has_trigger"] is True
    assert not (_vroot(client, asset) / "captions.jsonl").exists()   # preview ≠ staging
    assert client.get("/assets/ast_nope00/captions").status_code == 404


def test_caption_override_persists_and_staging_respects_it(client):
    """The core M3 contract: an edit is durable on version.json, the next stage emits it
    into the dataset `.txt` + captions.jsonl (origin 'edited', template kept for the
    record), captions_hash CHANGES, caption_policy_hash does NOT, and the version's
    caption_status counts the edit."""
    asset = _curated_asset(client)
    base = client.post(f"/assets/{asset['id']}/lora/zimage/stage", json={}).json()

    listing = client.get(f"/assets/{asset['id']}/captions").json()
    target = listing["captions"][1]
    edited_text = "mara_lw, front view, portrait, neutral expression, holding a red lantern"
    r = client.put(f"/assets/{asset['id']}/captions/{target['id']}",
                   json={"caption": edited_text})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["origin"] == "edited" and row["caption"] == edited_text
    assert row["template_caption"] == target["template_caption"]

    # durable on version.json (fresh read straight from disk)
    vjson = json.loads((_vroot(client, asset) / "version.json").read_text(encoding="utf-8"))
    assert vjson["caption_overrides"][target["id"]]["caption"] == edited_text

    staged = client.post(f"/assets/{asset['id']}/lora/zimage/stage", json={}).json()
    assert staged["captions_hash"] != base["captions_hash"]           # hash honesty
    assert staged["caption_policy_hash"] == base["caption_policy_hash"]

    rows = [json.loads(line) for line in
            (_vroot(client, asset) / "captions.jsonl").read_text(encoding="utf-8").splitlines()]
    by_id = {r["id"]: r for r in rows}
    assert by_id[target["id"]]["caption"] == edited_text
    assert by_id[target["id"]]["origin"] == "edited"
    assert by_id[target["id"]]["template_caption"] == target["template_caption"]
    assert all(r["origin"] == "template" for rid, r in by_id.items() if rid != target["id"])

    dataset = json.loads(Path(staged["dataset_manifest"]).read_text(encoding="utf-8"))
    txts = {Path(f["caption"]).name: Path(f["caption"]).read_text(encoding="utf-8").strip()
            for f in dataset["files"]}
    assert edited_text in txts.values()                               # the trainer sees the edit

    detail = client.get(f"/assets/{asset['id']}").json()
    assert detail["versions"][0]["caption_status"]["edited_count"] == 1
    context = json.loads((_vroot(client, asset) / "training_context.json").read_text(encoding="utf-8"))
    origins = {ref["id"]: ref["caption_origin"] for ref in context["refs"]}
    assert origins[target["id"]] == "edited"


def test_reset_restores_template_and_the_original_hash(client):
    """DELETE per-ref returns the row to the template; a re-stage reproduces the ORIGINAL
    captions_hash (override → hash A→B, reset → B→A: byte-identical determinism)."""
    asset = _curated_asset(client)
    base = client.post(f"/assets/{asset['id']}/lora/zimage/stage", json={}).json()
    ref_id = client.get(f"/assets/{asset['id']}/captions").json()["captions"][0]["id"]

    client.put(f"/assets/{asset['id']}/captions/{ref_id}", json={"caption": "totally custom"})
    edited = client.post(f"/assets/{asset['id']}/lora/zimage/stage", json={}).json()
    assert edited["captions_hash"] != base["captions_hash"]

    r = client.delete(f"/assets/{asset['id']}/captions/{ref_id}")
    assert r.status_code == 200 and r.json()["cleared"] == 1
    restored = client.post(f"/assets/{asset['id']}/lora/zimage/stage", json={}).json()
    assert restored["captions_hash"] == base["captions_hash"]
    # idempotent repeat: nothing left to clear, still 200
    assert client.delete(f"/assets/{asset['id']}/captions/{ref_id}").json()["cleared"] == 0


def test_clear_all_overrides_and_trigger_flag(client):
    """DELETE on the collection resets everything; an override that drops the trigger
    token is flagged (has_trigger=False) but allowed — advisory, author decides (R14)."""
    asset = _curated_asset(client)
    ids = [c["id"] for c in client.get(f"/assets/{asset['id']}/captions").json()["captions"]]
    client.put(f"/assets/{asset['id']}/captions/{ids[0]}", json={"caption": "no trigger here"})
    client.put(f"/assets/{asset['id']}/captions/{ids[1]}", json={"caption": "mara_lw, custom"})

    listing = client.get(f"/assets/{asset['id']}/captions").json()
    flags = {c["id"]: c["has_trigger"] for c in listing["captions"]}
    assert flags[ids[0]] is False and flags[ids[1]] is True
    assert listing["edited_count"] == 2

    r = client.delete(f"/assets/{asset['id']}/captions")
    assert r.status_code == 200 and r.json()["cleared"] == 2
    after = client.get(f"/assets/{asset['id']}/captions").json()
    assert after["edited_count"] == 0
    assert all(c["origin"] == "template" for c in after["captions"])


def test_caption_edit_refusals(client):
    """Unknown ref → 400; whitespace-only → 400; empty → 422 (model bound); a finalized
    version refuses the mutators (finalize locks every mutator, P1) until unlocked."""
    asset = _curated_asset(client, n=1)
    ref_id = client.get(f"/assets/{asset['id']}/captions").json()["captions"][0]["id"]

    assert client.put(f"/assets/{asset['id']}/captions/ref_ffffff",
                      json={"caption": "x"}).status_code == 400
    assert client.delete(f"/assets/{asset['id']}/captions/ref_ffffff").status_code == 404
    assert client.put(f"/assets/{asset['id']}/captions/{ref_id}",
                      json={"caption": "   "}).status_code == 400
    assert client.put(f"/assets/{asset['id']}/captions/{ref_id}",
                      json={"caption": ""}).status_code == 422
    assert client.put(f"/assets/{asset['id']}/captions/{ref_id}",
                      json={"caption": "y" * 1001}).status_code == 422

    detail = client.get(f"/assets/{asset['id']}").json()
    vid = detail["versions"][0]["id"]
    assert client.post(f"/assets/{asset['id']}/versions/{vid}/finalize").status_code == 200
    assert client.put(f"/assets/{asset['id']}/captions/{ref_id}",
                      json={"caption": "x"}).status_code == 400
    assert client.delete(f"/assets/{asset['id']}/captions").status_code == 404
    # read stays open on a finalized version (review ≠ mutation)
    r = client.get(f"/assets/{asset['id']}/captions")
    assert r.status_code == 200 and r.json()["finalized"] is True
