"""M5 — profile versioning (R49–R51, R58–R61, §3.4) — no GPU.

Copy-on-create = a FULL deep-duplicate of ANY prior version (records + casting/refs/
faces files, anchor verification carried); finalize = pure-intent lock (every mutator
refuses); the active-version switch scopes everything downstream.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orchestrator import workspace as ws_mod
from orchestrator.config import CONFIG

_CELL = {"shot_size": "portrait", "angle": "front", "expression": "neutral",
         "background": ""}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def _rich_asset(ws):
    """An asset whose v1 has real content: starred candidate, kept ref, verified anchor,
    a rejected mark — the payload a deep-duplicate must carry."""
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    a = assets.create_asset(ws, name="VerAsset")["profile"]
    out = ws.out_dir / "job_ver01"
    out.mkdir(parents=True, exist_ok=True)
    names, meta = [], {}
    for i in range(3):
        (out / f"img{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        n = f"job_ver01/img{i}.png"
        names.append(n)
        meta[n] = {"coverage_cell": _CELL, "seed": i}
    jid = RUNNER.submit(pipeline="zimage", mode="img2img",
                        params={"prompt": "[ds]", "batch_items": [{}] * 3},
                        batch_id="bat_v", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="B")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": names[0],
                                  "output_names": names, "output_meta": meta}
    assets.star_candidate(ws, a["id"], job_id=jid, source_output=names[0],
                          version_id=a["active_version"], pipeline="zimage", seed=0)
    assets.keep_ref(ws, a["id"], job_id=jid, source_output=names[1],
                    coverage_cell=_CELL, version_id=a["active_version"],
                    pipeline="zimage", seed=1, method="img2img")
    assets.set_anchor(ws, a["id"], job_id=jid, source_output=names[2])
    assets.mark_anchor_verified(
        ws, a["active_version"],
        anchor_path=str(assets.anchor_file_path(ws, a["id"])), job_id=jid)
    assets.reject_output(ws, a["id"], source_output=names[2])
    return a, jid, names


def test_create_version_is_full_deep_duplicate(client):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a, _jid, _names = _rich_asset(ws)
    v1 = a["active_version"]
    v1_anchor = assets.anchor_file_path(ws, a["id"])

    r = client.post(f"/assets/{a['id']}/versions", json={"name": "scar"})
    assert r.status_code == 200, r.text
    body = r.json()
    v2 = body["version"]
    assert v2["id"] != v1 and v2["derived_from"] == v1
    assert v2["name"] == "scar" and v2["finalized"] is False
    assert body["profile"]["active_version"] == v2["id"]          # new version is active
    assert body["profile"]["versions"] == [v1, v2["id"]]
    # the payload carried: records…
    assert len(v2["casting"]) == 1 and v2["casting"][0]["starred"] is True
    assert len(v2["ref_set"]) == 1
    assert v2["rejected"] == ["job_ver01/img2.png"]
    # …the anchor incl. its durable verification (byte-identical file = proof carries)
    assert v2["anchor"]["verified_at"]
    # …and the FILES (own copies, independent of the parent)
    v2_anchor = assets.anchor_file_path(ws, a["id"], v2["id"])
    assert v2_anchor is not None and v2_anchor.is_file()
    assert str(v2_anchor) != str(v1_anchor)
    assert assets.casting_file_path(ws, a["id"], v2["casting"][0]["file"], v2["id"]).is_file()


def test_create_version_from_any_parent(client):
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a, _jid, _names = _rich_asset(ws)
    v1 = a["active_version"]
    r2 = client.post(f"/assets/{a['id']}/versions", json={"name": "v2"})
    assert r2.status_code == 200
    # v3 explicitly from v1 (NOT the now-active v2) — R59
    r3 = client.post(f"/assets/{a['id']}/versions",
                     json={"name": "v3_from_base", "parent_version_id": v1})
    assert r3.status_code == 200, r3.text
    assert r3.json()["version"]["derived_from"] == v1


def test_finalize_locks_every_mutator(client):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a, jid, names = _rich_asset(ws)
    v1 = a["active_version"]
    r = client.post(f"/assets/{a['id']}/versions/{v1}/finalize")
    assert r.status_code == 200 and r.json()["finalized"] is True
    # idempotent re-finalize
    assert client.post(f"/assets/{a['id']}/versions/{v1}/finalize").status_code == 200

    with pytest.raises(ws_mod.WorkspaceError, match="FINALIZED"):
        assets.star_candidate(ws, a["id"], job_id=jid, source_output=names[1],
                              version_id=v1, pipeline="zimage", seed=1)
    with pytest.raises(ws_mod.WorkspaceError, match="FINALIZED"):
        assets.keep_ref(ws, a["id"], job_id=jid, source_output=names[2],
                        coverage_cell=_CELL, version_id=v1)
    with pytest.raises(ws_mod.WorkspaceError, match="FINALIZED"):
        assets.reject_output(ws, a["id"], source_output=names[1], version_id=v1)
    with pytest.raises(ws_mod.WorkspaceError, match="FINALIZED"):
        assets.set_anchor(ws, a["id"], job_id=jid, source_output=names[0], version_id=v1)
    with pytest.raises(ws_mod.WorkspaceError, match="FINALIZED"):
        assets.clear_anchor(ws, a["id"], version_id=v1)
    with pytest.raises(ws_mod.WorkspaceError, match="finalized"):
        assets.save_profile(ws, a["id"], prompt_template="x", version_id=v1)
    # even the observer's verification stamp refuses a locked version (returns durable
    # truth only when it was already stamped pre-lock — never writes)
    already = assets.get_asset(ws, a["id"])
    v1_rec = next(x for x in already["versions"] if x["id"] == v1)
    saved_at_before = v1_rec["saved_at"]
    assets.mark_anchor_verified(ws, v1,
                                anchor_path=str(assets.anchor_file_path(ws, a["id"], v1)),
                                job_id="job_x")
    after = assets.get_asset(ws, a["id"])
    v1_after = next(x for x in after["versions"] if x["id"] == v1)
    assert v1_after["saved_at"] == saved_at_before          # untouched

    # …but copy-on-create FROM the locked version works and the copy is editable (R51)
    r2 = client.post(f"/assets/{a['id']}/versions", json={"name": "after_lock"})
    assert r2.status_code == 200, r2.text
    v2 = r2.json()["version"]
    assert v2["finalized"] is False
    assets.save_profile(ws, a["id"], prompt_template="editable again",
                        version_id=v2["id"])     # no raise


def test_unfinalize_unlocks_a_finalized_version(client):
    """User 2026-06-21: a finalized version's Curation can't be cleaned up (every mutator is
    locked). Unfinalize re-opens it for editing — idempotent, and curation mutators work again."""
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a, jid, names = _rich_asset(ws)
    v1 = a["active_version"]
    client.post(f"/assets/{a['id']}/versions/{v1}/finalize")
    with pytest.raises(ws_mod.WorkspaceError, match="FINALIZED"):
        assets.keep_ref(ws, a["id"], job_id=jid, source_output=names[2],
                        coverage_cell=_CELL, version_id=v1)
    u = client.post(f"/assets/{a['id']}/versions/{v1}/unfinalize")
    assert u.status_code == 200 and u.json()["finalized"] is False
    # idempotent
    assert client.post(f"/assets/{a['id']}/versions/{v1}/unfinalize").json()["finalized"] is False
    # the previously-locked version is editable again (keep also un-rejects names[2])
    assets.keep_ref(ws, a["id"], job_id=jid, source_output=names[2],
                    coverage_cell=_CELL, version_id=v1)        # no raise


def test_delete_asset_removes_profile_and_all_versions(client):
    """User 2026-06-21: delete a whole character (L2 asset) + all its versions. 404 on unknown."""
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a, _jid, _names = _rich_asset(ws)
    adir = assets._find_profile(ws, a["id"])[0]
    assert adir.is_dir()
    r = client.request("DELETE", f"/assets/{a['id']}")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert not adir.is_dir()
    assert a["id"] not in {x["id"] for x in client.get("/assets").json()["assets"]}
    assert client.request("DELETE", f"/assets/{a['id']}").status_code == 404   # now unknown


def test_activate_switches_and_validates(client):
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a, _jid, _names = _rich_asset(ws)
    v1 = a["active_version"]
    v2 = client.post(f"/assets/{a['id']}/versions", json={}).json()["version"]["id"]
    r = client.post(f"/assets/{a['id']}/versions/activate", json={"version_id": v1})
    assert r.status_code == 200 and r.json()["active_version"] == v1
    r2 = client.post(f"/assets/{a['id']}/versions/activate",
                     json={"version_id": "ver_nope00"})
    assert r2.status_code == 400
    # switch back — both versions stay loadable
    assert client.post(f"/assets/{a['id']}/versions/activate",
                       json={"version_id": v2}).status_code == 200
