"""M10 — MVP / P1 acceptance (no GPU): the §1 done-line as an executable assertion.

The P1 done-line (R40): define a **style** (L1) → **cast** a character (Stage-A grid) →
pick the **hero ★** → **expand** into a coverage dataset (Stage-B) → **curate** the on-model
results (Stage-C) → **save** a versioned AssetProfile → **reopen** the project and find it
intact. This walks that whole arc through the HTTP API the way a real session does.

GPU generation is simulated by injecting completed job results into the (paused) runner —
the *pixels* are the rig's acceptance pass; the *data-model contract* (what survives a
reopen) is locked here so the done-line can't silently regress. One end-to-end test plus a
couple of guards on the acceptance invariants (Saved-not-Finalized, single v1, reopen).
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


_CELL = {"shot_size": "portrait", "angle": "front", "expression": "neutral", "background": ""}


def _done_casting_job(ws, version_id, *, n=3, job_id="job_castA"):
    """A completed Stage-A casting job with `n` candidate outputs on disk (the no-GPU
    stand-in for a `multi`/`zimage` cast). Returns (job_id, [output_names])."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    d = ws.out_dir / job_id
    d.mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(n):
        (d / f"cand{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n cast")
        (d / f"cand{i}.json").write_text(f'{{"seed": {300 + i}}}', encoding="utf-8")
        names.append(f"{job_id}/cand{i}.png")
    jid = RUNNER.submit(pipeline="zimage", mode="t2i",
                        params={"prompt": "casting"}, batch_id="bat_castA", index=0,
                        batch_size=1, requester_id=version_id,
                        profile_version_id=version_id, stage="A")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": names[0],
                                  "output_names": names, "seed": 300}
    return jid, names


def _done_stage_b_job(ws, version_id, *, n=3, job_id="job_dsetB"):
    """A completed Stage-B coverage-dataset batch job with `n` coverage-celled outputs."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    d = ws.out_dir / job_id
    d.mkdir(parents=True, exist_ok=True)
    names, meta = [], {}
    for i in range(n):
        (d / f"img{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n dset")
        nm = f"{job_id}/img{i}.png"
        names.append(nm)
        meta[nm] = {"coverage_cell": _CELL, "seed": 400 + i}
    jid = RUNNER.submit(pipeline="zimage", mode="img2img",
                        params={"prompt": "[dataset]", "batch_items": [{}] * n},
                        batch_id="bat_dsetB", index=0, batch_size=1,
                        requester_id=version_id, profile_version_id=version_id, stage="B")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": names[0],
                                  "output_names": names, "output_meta": meta}
    return jid, names


def test_done_line_end_to_end(client, tmp_path):
    """style → cast → hero → expand → curate → save → reopen → intact + versioned."""
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    proj_a = str(ws.path)

    # 1 — STYLE (L1): set the minimal style fragment, default-on.
    r = client.put("/bible/style",
                   json={"fragment": "watercolor, muted palette", "enabled_default": True})
    assert r.status_code == 200, r.text

    # 2 — create the AssetProfile (v1 active).
    a = client.post("/assets", json={"name": "Mara"}).json()["profile"]
    v1 = a["active_version"]
    assert a["name"] == "Mara"

    # 3 — CAST (Stage A) → pick the HERO ★ from the selectable grid.
    cast_jid, cands = _done_casting_job(ws, v1)
    r = client.post(f"/assets/{a['id']}/casting/star",
                    json={"job_id": cast_jid, "output": cands[1], "starred": True})
    assert r.status_code == 200, r.text
    casting = r.json()["casting"]
    assert sum(c["starred"] for c in casting) == 1            # exactly one hero

    # 4 — EXPAND (Stage B): the recipe dry-run proves the hero resolves and the per-cell
    # prompt weaves clause + the L1 style fragment (R104 append) before any GPU work.
    dry = client.post(f"/assets/{a['id']}/stage-b",
                      json={"preset": "npc_lite", "pipeline": "zimage",
                            "character_clause": "a weathered ranger", "dry_run": True})
    assert dry.status_code == 200, dry.text
    body = dry.json()
    assert body["dry_run"] is True and body["items"] > 0 and body["planned_jobs"] >= 1
    prompt0 = body["first_cell"]["prompt"]
    assert "ranger" in prompt0 and "watercolor" in prompt0   # clause + style both present

    # ...then the realized dataset (no-GPU stand-in for the recipe sweep).
    b_jid, outs = _done_stage_b_job(ws, v1)

    # 5 — CURATE (Stage C): keep 2 on-model cells, reject 1.
    for nm in outs[:2]:
        rk = client.post(f"/assets/{a['id']}/refs/keep", json={"job_id": b_jid, "output": nm})
        assert rk.status_code == 200, rk.text
    rj = client.post(f"/assets/{a['id']}/refs/reject", json={"job_id": b_jid, "output": outs[2]})
    assert rj.status_code == 200, rj.text

    # 6 — SAVE the AssetProfile (R119: Saved, not Finalized).
    rs = client.post(f"/assets/{a['id']}/save",
                     json={"prompt_template": "a weathered ranger, watercolor study"})
    assert rs.status_code == 200, rs.text
    assert rs.json()["finalized"] is False

    # 7 — REOPEN: switch to a fresh project (unbinds A), confirm it's gone from view, then
    # re-open A from disk and assert the whole done-line survived the round-trip.
    p2 = client.post("/project", json={"dest": str(tmp_path / "projB"), "name": "B",
                                       "size_cap_gb": 50})
    assert p2.status_code == 200, p2.text
    assert client.get("/assets").json()["assets"] == []       # B is empty
    assert client.post("/project/open", json={"path": proj_a}).status_code == 200

    detail = client.get(f"/assets/{a['id']}").json()
    assert detail["profile"]["name"] == "Mara"
    assert detail["profile"]["asset_class"] == "characters"
    assert detail["profile"]["active_version"] == v1
    assert len(detail["versions"]) == 1                       # a single version v1
    v = detail["versions"][0]
    assert v["id"] == v1 and v["finalized"] is False          # Saved, not Finalized
    assert v["prompt_template"] == "a weathered ranger, watercolor study"
    assert len(v["ref_set"]) == 2                             # the curated corpus
    assert all(ref["coverage_cell"] for ref in v["ref_set"])  # P1→P2 contract intact
    assert v["rejected"] == [outs[2]]                         # the persistent cull mark
    assert sum(c["starred"] for c in v["casting"]) == 1       # the hero survived

    # the L1 style also persisted across the reopen
    style = client.get("/bible/style").json()
    assert "watercolor" in style["fragment"] and style["enabled_default"] is True


def _asset_with_hero(client, ws, *, name="Lyra"):
    """An AssetProfile whose active version already has a starred hero ★ (the Stage-B
    precondition) — built no-GPU via an injected done casting job."""
    a = client.post("/assets", json={"name": name}).json()["profile"]
    jid, cands = _done_casting_job(ws, a["active_version"], job_id=f"job_cast_{name}")
    client.post(f"/assets/{a['id']}/casting/star",
                json={"job_id": jid, "output": cands[0], "starred": True})
    return a


def test_done_line_uses_the_p1_adapter_path(client):
    """M10 review (Medium): the §1 done-line MINIMUM is `multi` casting + `_img2img`/`sd35`
    Stage-B (kb-loom-p1.md §11.1, R121). The persistence test stands in zimage; this pins the
    actual user-facing adapter wiring with no-GPU dry-runs so a regression in the multi/sd35
    routing fails M10 rather than passing on the zimage stand-in."""
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a = _asset_with_hero(client, ws)
    vid = a["active_version"]

    # CAST via `multi`, asset-scoped — the real Stage-A casting adapter.
    cast = client.post("/generate",
                       json={"pipeline": "multi", "prompt": "cast Lyra", "asset_id": a["id"],
                             "stage": "A", "num_candidates": 1, "dry_run": True})
    assert cast.status_code == 200, cast.text
    cbody = cast.json()
    assert cbody["pipeline"] == "multi" and cbody["num_candidates"] == 1
    assert cbody["profile_version_id"] == vid          # scoped to the asset version
    assert "ideate" in cbody["argv"] and any("multi" in tok for tok in cbody["argv"])

    # EXPAND via `sd35` img2img — the real Stage-B expansion adapter.
    sb = client.post(f"/assets/{a['id']}/stage-b",
                     json={"pipeline": "sd35", "character_clause": "a star-pilot",
                           "preset": "full_coverage", "dry_run": True})
    assert sb.status_code == 200, sb.text
    sbody = sb.json()
    assert sbody["pipeline"] == "sd35" and sbody["planned_jobs"] >= 1
    assert "img2img" in sbody["split"]
    assert any("sd35" in tok for tok in sbody["first_argv"]) and "img2img" in sbody["first_argv"]

    # M3.5 mixed realization splits cells into TWO batch jobs (img2img sweep + inpaint
    # background-diversity) — prove the routing, dry-run echoes the bg_mask unchecked.
    mixed = client.post(f"/assets/{a['id']}/stage-b",
                        json={"pipeline": "sd35", "character_clause": "a star-pilot",
                              "preset": "full_coverage", "realize": "mixed",
                              "bg_mask": "job_x/hero_bgmask.png", "dry_run": True})
    assert mixed.status_code == 200, mixed.text
    mbody = mixed.json()
    assert mbody["realize"] == "mixed"
    assert mbody["split"].get("img2img", 0) >= 1 and mbody["split"].get("inpaint", 0) >= 1


def test_save_is_reopenable_without_curation(client, tmp_path):
    """The minimum done-line is a saved, reopenable profile — even a bare style→save (no
    curated refs yet) must reopen as a valid, editable v1 (not lost, not finalized)."""
    from orchestrator.runner import RUNNER
    proj_a = str(RUNNER.workspace.path)
    client.put("/bible/style", json={"fragment": "ink wash", "enabled_default": True})
    a = client.post("/assets", json={"name": "Bram"}).json()["profile"]
    client.post(f"/assets/{a['id']}/save", json={"prompt_template": "a quiet smith"})
    # reopen
    client.post("/project", json={"dest": str(tmp_path / "projC"), "name": "C", "size_cap_gb": 50})
    assert client.post("/project/open", json={"path": proj_a}).status_code == 200
    detail = client.get(f"/assets/{a['id']}").json()
    assert detail["profile"]["name"] == "Bram"
    assert detail["versions"][0]["prompt_template"] == "a quiet smith"
    assert detail["versions"][0]["finalized"] is False
