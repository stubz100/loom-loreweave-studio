"""M8 — full L1 World authoring (no GPU): world prose, the style global negative (applied
to generation), and the story spine → stub AssetProfile connector (R55 manual re-sync).
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


# --- world prose + global negative -----------------------------------------------------

def test_world_prose_roundtrip(client):
    r = client.put("/bible/world", json={"world": "# Lanternfall\nA city lit by living flame."})
    assert r.status_code == 200, r.text
    assert "Lanternfall" in client.get("/bible").json()["world"]


def test_global_negative_persists_and_applies_to_generation(client):
    r = client.put("/bible/style", json={"global_negative": "blurry, extra fingers, watermark"})
    assert r.status_code == 200 and "blurry" in r.json()["global_negative"]
    # applied to a zimage gen under the style gate, appended to the request's negative
    g = client.post("/generate", json={
        "pipeline": "zimage", "prompt": "a ranger", "negative_prompt": "lowres",
        "apply_style": True, "dry_run": True})
    assert g.status_code == 200, g.text
    argv = g.json()["argv"]
    neg = argv[argv.index("--negative-prompt") + 1]
    assert "lowres" in neg and "watermark" in neg          # request neg + global appended
    # opted out → not applied
    g2 = client.post("/generate", json={"pipeline": "zimage", "prompt": "a ranger",
                                        "apply_style": False, "dry_run": True})
    assert "watermark" not in " ".join(g2.json()["argv"])


def test_global_negative_skipped_for_multi(client):
    client.put("/bible/style", json={"global_negative": "watermark"})
    g = client.post("/generate", json={"pipeline": "multi", "prompt": "a hero",
                                       "num_candidates": 1, "dry_run": True})
    assert g.status_code == 200
    assert "--negative-prompt" not in g.json()["argv"]     # ideate takes no negative


def _hero_asset(ws):
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    a = assets.create_asset(ws, name="NegHero")["profile"]
    out = ws.out_dir / "job_negh"
    out.mkdir(parents=True, exist_ok=True)
    (out / "h.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "h"},
                        batch_id="bat_n", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="A")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_negh/h.png",
                                  "output_names": ["job_negh/h.png"]}
    assets.star_candidate(ws, a["id"], job_id=jid, source_output="job_negh/h.png",
                          version_id=a["active_version"], pipeline="zimage", seed=1)
    return a


def test_global_negative_is_global_stage_b_and_sketch(client, monkeypatch):
    """M8 review (Medium): the global negative must reach EVERY generation surface, not
    just /generate — Stage-B dataset cells and the video sketch too."""
    from orchestrator import components
    from orchestrator.runner import RUNNER
    monkeypatch.setattr(components, "image_model_present", lambda repo_id: True)
    client.put("/bible/style", json={"global_negative": "watermark, blurry"})
    a = _hero_asset(RUNNER.workspace)
    # Stage-B dry run — the first-cell argv carries the global negative
    sb = client.post(f"/assets/{a['id']}/stage-b",
                     json={"preset": "npc_lite", "character_clause": "a ranger",
                           "dry_run": True})
    assert sb.status_code == 200, sb.text
    argv = sb.json()["first_argv"]
    assert "watermark" in argv[argv.index("--negative-prompt") + 1]
    # sketch dry run — same
    sk = client.post(f"/assets/{a['id']}/stage-b/sketch",
                     json={"angle": "profile_left", "character_clause": "a ranger",
                           "dry_run": True})
    assert sk.status_code == 200, sk.text
    skargv = sk.json()["argv"]
    assert "watermark" in skargv[skargv.index("--negative-prompt") + 1]
    # opt-out drops it everywhere
    sb2 = client.post(f"/assets/{a['id']}/stage-b",
                      json={"preset": "npc_lite", "character_clause": "x",
                            "apply_style": False, "dry_run": True})
    assert "watermark" not in " ".join(sb2.json()["first_argv"])


# --- story spine ----------------------------------------------------------------------

def test_spine_premise_and_character_crud(client):
    assert client.put("/bible/spine/premise",
                      json={"premise": "A ranger hunts the last lantern."}).status_code == 200
    r = client.post("/bible/spine/character",
                    json={"name": "Mara", "snippet": "a weathered ranger, green cloak"})
    assert r.status_code == 200, r.text
    chars = r.json()["spine"]["characters"]
    assert len(chars) == 1 and chars[0]["name"] == "Mara" and chars[0]["id"].startswith("spc_")
    cid = chars[0]["id"]
    # edit the snippet — does NOT touch any profile (none linked yet)
    r2 = client.post("/bible/spine/character",
                     json={"character_id": cid, "snippet": "a weathered ranger, grey cloak"})
    assert "grey cloak" in r2.json()["spine"]["characters"][0]["snippet"]
    # delete
    r3 = client.request("DELETE", f"/bible/spine/character/{cid}")
    assert r3.status_code == 200 and r3.json()["spine"]["characters"] == []
    # empty name rejected
    assert client.post("/bible/spine/character", json={"name": "  "}).status_code == 400


def test_spine_stub_creates_profile_with_snippet_and_links(client):
    cid = client.post("/bible/spine/character",
                      json={"name": "Mara", "snippet": "a weathered ranger, green cloak"}
                      ).json()["spine"]["characters"][0]["id"]
    r = client.post("/bible/spine/character/stub", json={"character_id": cid})
    assert r.status_code == 200, r.text
    aid = r.json()["linked_asset_id"]
    # the stub's active version inherited the snippet as its prompt_template (R112)
    detail = client.get(f"/assets/{aid}").json()
    v = next(x for x in detail["versions"] if x["id"] == detail["profile"]["active_version"])
    assert v["prompt_template"] == "a weathered ranger, green cloak"
    # the spine entry is now linked
    ch = next(c for c in client.get("/bible").json()["spine"]["characters"] if c["id"] == cid)
    assert ch["linked_asset_id"] == aid
    # creating the stub again is refused (already linked)
    assert client.post("/bible/spine/character/stub",
                       json={"character_id": cid}).status_code == 409


def test_spine_resync_is_manual_and_pushes_snippet(client):
    """R55: re-sync is the ONLY thing that writes the snippet into a linked profile —
    editing the spine snippet alone never clobbers a hand-edited profile."""
    from orchestrator import assets
    from orchestrator.runner import RUNNER
    cid = client.post("/bible/spine/character",
                      json={"name": "Mara", "snippet": "v1 clause"}
                      ).json()["spine"]["characters"][0]["id"]
    aid = client.post("/bible/spine/character/stub",
                      json={"character_id": cid}).json()["linked_asset_id"]
    # the author hand-edits the profile clause
    assets.save_profile(RUNNER.workspace, aid, prompt_template="hand-edited clause")
    # editing the spine snippet does NOT touch the profile (no auto-clobber)
    client.post("/bible/spine/character", json={"character_id": cid, "snippet": "v2 clause"})
    detail = client.get(f"/assets/{aid}").json()
    v = next(x for x in detail["versions"] if x["id"] == detail["profile"]["active_version"])
    assert v["prompt_template"] == "hand-edited clause"        # untouched
    # explicit re-sync overwrites with the current snippet
    r = client.post(f"/bible/spine/character/{cid}/resync")
    assert r.status_code == 200 and r.json()["prompt_template"] == "v2 clause"
    detail2 = client.get(f"/assets/{aid}").json()
    v2 = next(x for x in detail2["versions"] if x["id"] == detail2["profile"]["active_version"])
    assert v2["prompt_template"] == "v2 clause"


def test_spine_stub_unknown_character_404(client):
    assert client.post("/bible/spine/character/stub",
                       json={"character_id": "spc_000000"}).status_code == 404
    assert client.post("/bible/spine/character/spc_000000/resync").status_code == 404
