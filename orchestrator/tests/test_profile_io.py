"""M9 — profile export / import (no GPU; R66/R67).

Export = a portable .zip of the profile + ALL versions + their files. Import = ALWAYS a
new profile (fresh profile + version ids — so a re-import into the same project can't
cross-link the runner/lineage), `derived_from` remapped within the bundle, rename on
collision, never a merge.
"""

from __future__ import annotations

import zipfile

import pytest
from fastapi.testclient import TestClient

from orchestrator import assets
from orchestrator.config import CONFIG


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def _multi_version_asset(ws):
    """An asset with a kept ref in v1, a v2 derived from v1, finalized v1 — real content
    + cross-version `derived_from` to exercise the remap."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    a = assets.create_asset(ws, name="Mara", prompt_template="a ranger")["profile"]
    out = ws.out_dir / "job_e1"
    out.mkdir(parents=True, exist_ok=True)
    (out / "img0.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    jid = RUNNER.submit(pipeline="zimage", mode="img2img",
                        params={"prompt": "[ds]", "batch_items": [{}]},
                        batch_id="bat_e", index=0, batch_size=1,
                        requester_id=a["active_version"],
                        profile_version_id=a["active_version"], stage="B")
    cell = {"shot_size": "portrait", "angle": "front", "expression": "neutral", "background": ""}
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_e1/img0.png",
                                  "output_names": ["job_e1/img0.png"],
                                  "output_meta": {"job_e1/img0.png": {"coverage_cell": cell}}}
    assets.keep_ref(ws, a["id"], job_id=jid, source_output="job_e1/img0.png",
                    coverage_cell=cell, version_id=a["active_version"],
                    pipeline="zimage", seed=1, method="img2img")
    v1 = a["active_version"]
    res = assets.create_version(ws, a["id"], name="scar")          # v2 derived from v1
    v2 = res["version"]["id"]
    assets.finalize_version(ws, a["id"], v1)                       # lock v1
    return a, v1, v2


def test_export_bundles_profile_and_all_versions(client):
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a, v1, v2 = _multi_version_asset(ws)
    r = client.get(f"/assets/{a['id']}/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    import io
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert "loom_bundle.json" in names
        assert "asset/profile.json" in names
        # both versions + the kept ref file travel in the bundle
        assert sum(n.endswith("version.json") for n in names) == 2
        assert any("/refs/" in n and n.endswith(".png") for n in names)


def test_export_unknown_asset_404(client):
    assert client.get("/assets/ast_zzzzzz/export").status_code == 404


def test_export_requires_token(client):
    """M9 review — export packages every version+file, so it's token-gated like import."""
    from orchestrator.runner import RUNNER
    a, _v1, _v2 = _multi_version_asset(RUNNER.workspace)
    r = client.get(f"/assets/{a['id']}/export", headers={"X-Loom-Token": "wrong"})
    assert r.status_code == 401


def test_import_creates_new_profile_fresh_ids(client):
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a, v1, v2 = _multi_version_asset(ws)
    blob = client.get(f"/assets/{a['id']}/export").content
    r = client.post("/assets/import", content=blob,
                    headers={"Content-Type": "application/zip"})
    assert r.status_code == 200, r.text
    new = r.json()["profile"]
    assert new["id"] != a["id"] and new["id"].startswith("ast_")
    # fresh version ids (none shared with the source) — no runner/lineage cross-link
    assert set(new["versions"]).isdisjoint({v1, v2})
    assert len(new["versions"]) == 2
    # same project now has BOTH (import is a copy, never a move/merge)
    ids = {x["id"] for x in client.get("/assets").json()["assets"]}
    assert a["id"] in ids and new["id"] in ids
    # collision rename (R67): same source name → the import is renamed
    assert r.json()["renamed_from"] == "Mara" and new["name"] != "Mara"
    # the imported active version carries the content (prompt_template + the kept ref)
    detail = client.get(f"/assets/{new['id']}").json()
    av = next(v for v in detail["versions"] if v["id"] == new["active_version"])
    assert av["prompt_template"] == "a ranger"


def test_import_remaps_derived_from_and_preserves_finalized(client):
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a, v1, v2 = _multi_version_asset(ws)
    blob = client.get(f"/assets/{a['id']}/export").content
    new = client.post("/assets/import", content=blob).json()["profile"]
    detail = client.get(f"/assets/{new['id']}").json()
    by_name = {v["name"]: v for v in detail["versions"]}
    # v1 stayed finalized; v2's derived_from now points at the NEW v1 id (remapped in-bundle)
    assert by_name["v1_base"]["finalized"] is True
    assert by_name["scar"]["derived_from"] == by_name["v1_base"]["id"]
    assert by_name["scar"]["derived_from"] in new["versions"]


def test_import_round_trips_into_a_fresh_project(client, monkeypatch, tmp_path):
    """The real portability case: export from project A, import into project B."""
    from orchestrator.runner import RUNNER
    a, _v1, _v2 = _multi_version_asset(RUNNER.workspace)
    blob = client.get(f"/assets/{a['id']}/export").content
    # open a second, empty project (same process)
    p2 = client.post("/project", json={"dest": str(tmp_path / "proj2"), "name": "ProjB",
                                       "size_cap_gb": 50})
    assert p2.status_code == 200, p2.text
    assert client.get("/assets").json()["assets"] == []        # B starts empty
    r = client.post("/assets/import", content=blob)
    assert r.status_code == 200, r.text
    # no collision in the fresh project → keeps the original name
    assert r.json()["renamed_from"] is None
    assert r.json()["profile"]["name"] == "Mara"
    assert len(client.get("/assets").json()["assets"]) == 1


def test_import_rejects_non_bundle_zip(client):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("random.txt", "not a loom bundle")
    r = client.post("/assets/import", content=buf.getvalue())
    assert r.status_code == 400 and "loom asset bundle" in r.text
    # and a non-zip body
    assert client.post("/assets/import", content=b"garbage").status_code == 400


def test_import_rejects_unsupported_bundle_version(client):
    """M9 review — a future/incompatible bundle_version must be refused, not partially reshaped."""
    import io
    import json as _json
    from orchestrator.runner import RUNNER
    a, _v1, _v2 = _multi_version_asset(RUNNER.workspace)
    blob = client.get(f"/assets/{a['id']}/export").content
    # repackage with a bumped bundle_version
    src = io.BytesIO(blob)
    out = io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w") as zout:
        for n in zin.namelist():
            if n == "loom_bundle.json":
                m = _json.loads(zin.read(n)); m["bundle_version"] = 999
                zout.writestr(n, _json.dumps(m))
            else:
                zout.writestr(n, zin.read(n))
    r = client.post("/assets/import", content=out.getvalue())
    assert r.status_code == 400 and "bundle_version" in r.text
