"""L1 style COLLECTION (2026-06-13): multiple named styles, selectable per generation.

Locks: the legacy single `style` migrates to `styles[]` + an active default (back-compat
mirror kept); CRUD (add/edit/delete/set-active); and per-generation `style_id` selects WHICH
style's fragment + global negative apply at /generate (and Stage-B), with the active default
when omitted.
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


def test_default_collection_has_one_active_style(client):
    s = client.get("/bible/styles").json()
    assert len(s["styles"]) == 1 and s["active_style_id"] == s["styles"][0]["id"]
    assert s["styles"][0]["name"] == "Default" and s["enabled_default"] is True


def test_add_edit_delete_and_set_active(client):
    # add two
    a = client.post("/bible/styles", json={"name": "Noir", "fragment": "high-contrast b&w"})
    assert a.status_code == 200, a.text
    noir = next(s for s in a.json()["styles"] if s["name"] == "Noir")
    client.post("/bible/styles", json={"name": "Watercolor", "fragment": "soft watercolor"})
    s = client.get("/bible/styles").json()
    assert {x["name"] for x in s["styles"]} == {"Default", "Noir", "Watercolor"}
    # edit Noir
    e = client.put(f"/bible/styles/{noir['id']}", json={"fragment": "stark high-contrast b&w"})
    assert "stark" in next(x for x in e.json()["styles"] if x["id"] == noir["id"])["fragment"]
    # set active → Noir
    act = client.post("/bible/styles/active", json={"style_id": noir["id"]})
    assert act.json()["active_style_id"] == noir["id"]
    # the active mirror (/bible/style) now reflects Noir
    assert "high-contrast" in client.get("/bible/style").json()["fragment"]
    # delete Noir → active re-points to a remaining style
    d = client.request("DELETE", f"/bible/styles/{noir['id']}")
    assert d.status_code == 200 and noir["id"] not in {x["id"] for x in d.json()["styles"]}
    assert d.json()["active_style_id"] in {x["id"] for x in d.json()["styles"]}


def test_new_style_lands_at_the_top(client):
    """User 2026-06-21: a freshly added style should appear at the TOP of the list (ready to
    edit), not appended at the bottom. The active default is unchanged by an add."""
    before = client.get("/bible/styles").json()
    default_id = before["active_style_id"]
    a = client.post("/bible/styles", json={"name": "Noir", "fragment": "b&w"}).json()
    assert a["styles"][0]["name"] == "Noir"          # newest first
    b = client.post("/bible/styles", json={"name": "Watercolor"}).json()
    assert [s["name"] for s in b["styles"][:2]] == ["Watercolor", "Noir"]
    assert b["active_style_id"] == default_id          # add doesn't change the default


def test_cannot_delete_last_style(client):
    s = client.get("/bible/styles").json()
    only = s["styles"][0]["id"]
    r = client.request("DELETE", f"/bible/styles/{only}")
    assert r.status_code == 400 and "last style" in r.text


def test_set_active_unknown_404_and_empty_name_400(client):
    assert client.post("/bible/styles/active", json={"style_id": "sty_ffffff"}).status_code == 404
    assert client.post("/bible/styles", json={"name": "  "}).status_code == 400


def test_put_style_unknown_id_is_strict_404(client):
    """Review 2026-06-13: a MUTATION with an unknown style_id must 404 — never silently edit
    the active default (a stale client could overwrite it). Generation stays lenient."""
    r = client.put("/bible/style", json={"fragment": "SHOULD-NOT-LAND", "style_id": "sty_ffffff"})
    assert r.status_code == 404
    # the active style is untouched
    assert "SHOULD-NOT-LAND" not in client.get("/bible/style").json()["fragment"]
    # generation with an unknown style_id still renders (lenient fallback to active)
    g = client.post("/generate", json={"pipeline": "zimage", "prompt": "x",
                                       "apply_style": True, "style_id": "sty_ffffff",
                                       "dry_run": True})
    assert g.status_code == 200


def test_per_generation_style_id_selects_the_fragment(client):
    """A request's `style_id` picks WHICH style's fragment is appended; omitting it uses the
    active default."""
    noir = next(s for s in
                client.post("/bible/styles",
                            json={"name": "Noir", "fragment": "NOIRMARK high-contrast"}
                            ).json()["styles"] if s["name"] == "Noir")
    # default active is still "Default" → its fragment, not Noir's
    g0 = client.post("/generate", json={"pipeline": "zimage", "prompt": "a ranger",
                                        "apply_style": True, "dry_run": True})
    assert "NOIRMARK" not in g0.json()["prompt"]
    # explicitly select Noir → its fragment is appended
    g1 = client.post("/generate", json={"pipeline": "zimage", "prompt": "a ranger",
                                        "apply_style": True, "style_id": noir["id"],
                                        "dry_run": True})
    assert "NOIRMARK" in g1.json()["prompt"]
    # an unknown style_id falls back to the active default (lenient — never errors a gen)
    g2 = client.post("/generate", json={"pipeline": "zimage", "prompt": "a ranger",
                                        "apply_style": True, "style_id": "sty_zzzzzz",
                                        "dry_run": True})
    assert g2.status_code == 200 and "NOIRMARK" not in g2.json()["prompt"]


def test_per_generation_style_global_negative_selected(client):
    """The selected style's global negative (not the active one's) rides the request."""
    client.put("/bible/style", json={"global_negative": "DEFAULTNEG"})   # active = Default
    noir = next(s for s in
                client.post("/bible/styles",
                            json={"name": "Noir", "fragment": "noir",
                                  "global_negative": "NOIRNEG"}).json()["styles"]
                if s["name"] == "Noir")
    g = client.post("/generate", json={"pipeline": "zimage", "prompt": "x",
                                       "apply_style": True, "style_id": noir["id"],
                                       "dry_run": True})
    argv = g.json()["argv"]
    neg = argv[argv.index("--negative-prompt") + 1]
    assert "NOIRNEG" in neg and "DEFAULTNEG" not in neg
