"""M2.11 — pose icons (L1 · Poses) + the CellPicker's backend surface (no GPU).

The icons are the L1-styles sample pattern keyed on the POSE (shot__angle__expression):
generated once per project at 256² with the M0d directive prompts on a neutral subject,
stored durably bible-side, shared by every recipe that includes the cell.
"""

from __future__ import annotations

import json

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


def test_pose_cells_are_index_aligned_and_keyed(client):
    """GET /bible/poses mirrors the recipe's deterministic build — the CellPicker's indices
    must be exactly Stage-B's `cells` indices."""
    from orchestrator import bible, recipe
    r = client.get("/bible/poses", params={"preset": "npc_lite"})
    assert r.status_code == 200, r.text
    body = r.json()
    built = recipe.build_recipe("npc_lite", character_clause="x", base_seed=0, shared_seed=True)
    assert body["count"] == len(built["cells"])
    for got, want in zip(body["cells"], built["cells"]):
        assert got["index"] == want["index"]
        assert got["coverage_cell"] == want["coverage_cell"]
        assert got["key"] == bible.pose_key(want["coverage_cell"])
        assert got["icon"] is False                      # fresh project — nothing generated
    assert client.get("/bible/poses", params={"preset": "nope"}).status_code == 422


def test_generate_submits_one_256_job_per_distinct_pose(client, monkeypatch):
    """One t2i job per DISTINCT pose key (dedup), 256², dev JSON directive prompt, the pose
    key on the job meta, one shared warm_group; keys with an icon are skipped next call."""
    from orchestrator import components
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    monkeypatch.setattr(components, "variant_weights_present", lambda _v: True)
    r = client.post("/bible/poses/generate", json={"preset": "npc_lite"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == len(body["jobs"]) > 0
    keys = [j["key"] for j in body["jobs"]]
    assert len(keys) == len(set(keys))                   # dedup by pose key
    groups = set()
    for j in body["jobs"]:
        job = RUNNER.get(j["job_id"])
        assert job["pipeline"] == "flux2" and job["mode"] == "t2i"
        p = job["params"]
        assert p["width"] == 256 and p["height"] == 256
        assert p["model_name"] == "flux.2-dev"
        assert p["meta"]["pose_key"] == j["key"]
        obj = json.loads(p["prompt"])                    # dev → structured JSON prompt
        assert obj["pose"] and obj["shot"] and "mannequin" in obj["subject"]
        assert "style" not in obj                        # NO L1 style on icons
        groups.add(job["warm_group"])
        RUNNER.cancel(j["job_id"])
    assert len(groups) == 1                              # one resident worker for the set


def test_set_icon_is_durable_and_served_and_skipped_on_regenerate(client, monkeypatch):
    from orchestrator import components
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    monkeypatch.setattr(components, "variant_weights_present", lambda _v: True)
    ws = RUNNER.workspace
    out = ws.out_dir / "job_pi01"
    out.mkdir(parents=True, exist_ok=True)
    (out / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n pose")
    key = "portrait__front__neutral"
    r = client.post(f"/bible/poses/{key}/icon", json={"output": "job_pi01/icon.png"})
    assert r.status_code == 200, r.text
    assert r.json()["icons"][key] == f"{key}.png"
    assert (ws.bible_dir / "poses" / f"{key}.png").is_file()   # durable bible-side copy
    assert client.get(f"/bible/poses/{key}/file").status_code == 200
    assert client.get("/bible/poses/nope__x__y/file").status_code == 404
    assert client.post("/bible/poses/../evil/icon",
                       json={"output": "job_pi01/icon.png"}).status_code in (404, 422)
    # the listing shows it, and a re-generate SKIPS it (only the remaining cells fire)
    cells = client.get("/bible/poses", params={"preset": "npc_lite"}).json()["cells"]
    flags = {c["key"]: c["icon"] for c in cells}
    if key in flags:
        assert flags[key] is True
    g = client.post("/bible/poses/generate", json={"preset": "npc_lite"}).json()
    assert all(j["key"] != key for j in g["jobs"])
    for j in g["jobs"]:
        RUNNER.cancel(j["job_id"])


def test_per_icon_rerun_and_delete(client, monkeypatch):
    """Author 2026-07-05: batch fills left odd characters in the set — re-run ONE key
    (`keys=[k]` regenerates even when an icon exists, with the caller's seed — the batch
    seed would reproduce the same render; unknown key 422) and DELETE one icon (back to a
    text chip; 404 when none)."""
    from orchestrator import components
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    monkeypatch.setattr(components, "variant_weights_present", lambda _v: True)
    ws = RUNNER.workspace
    out = ws.out_dir / "job_pi02"
    out.mkdir(parents=True, exist_ok=True)
    (out / "i.png").write_bytes(b"\x89PNG\r\n\x1a\n x")
    key = client.get("/bible/poses", params={"preset": "npc_lite"}).json()["cells"][0]["key"]
    client.post(f"/bible/poses/{key}/icon", json={"output": "job_pi02/i.png"})
    r = client.post("/bible/poses/generate",
                    json={"preset": "npc_lite", "keys": [key], "seed": 123})
    assert r.status_code == 200, r.text
    assert [j["key"] for j in r.json()["jobs"]] == [key]     # exactly the one, icon or not
    jid = r.json()["jobs"][0]["job_id"]
    assert RUNNER.get(jid)["params"]["seed"] == 123          # fresh-seed lever honored
    RUNNER.cancel(jid)
    assert client.post("/bible/poses/generate",
                       json={"preset": "npc_lite", "keys": ["nope__x__y"]}).status_code == 422
    d = client.delete(f"/bible/poses/{key}/icon")
    assert d.status_code == 200 and key not in d.json()["icons"]
    assert client.get(f"/bible/poses/{key}/file").status_code == 404
    assert client.delete(f"/bible/poses/{key}/icon").status_code == 404
