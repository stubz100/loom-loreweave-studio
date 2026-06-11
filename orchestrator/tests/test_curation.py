"""Stage-C curation + Save AssetProfile (P1/M3, the MVP done-line) — no GPU.

Locks the done-line data model: keeping a Stage-B candidate writes a curated `ref_set` entry
carrying its frozen coverage_cell + copies the image into the version's `refs/` (self-contained);
culling removes both; Save persists the identity clause (Saved, not Finalized); and a Saved
version survives a reload with its curated set intact.
"""

from __future__ import annotations

import pytest

from orchestrator import assets
from orchestrator import workspace as ws_mod


@pytest.fixture()
def ws(tmp_path):
    return ws_mod.Workspace.create(tmp_path / "proj", name="t", size_cap_gb=50)


def _stage_b_output(ws, job_id="job_b0000001"):
    """A completed Stage-B img2img output under out/<job>/."""
    d = ws.out_dir / job_id
    d.mkdir(parents=True, exist_ok=True)
    img = d / "zimage_x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n stageB")
    (d / "zimage_x.json").write_text('{"seed": 7}', encoding="utf-8")
    return f"{job_id}/{img.name}"


def _cell(**over):
    c = {"shot_size": "waist_up", "angle": "profile_left", "expression": "neutral",
         "background": "market"}
    c.update(over)
    return c


def test_keep_records_ref_with_cell_and_copies_image(ws):
    a = assets.create_asset(ws, name="Mara")["profile"]
    out = _stage_b_output(ws)
    version = assets.keep_ref(ws, a["id"], job_id="job_b0000001", source_output=out,
                              coverage_cell=_cell(), pipeline="zimage", seed=7, method="img2img")
    assert len(version["ref_set"]) == 1
    r = version["ref_set"][0]
    assert r["id"].startswith("ref_") and r["coverage_cell"]["angle"] == "profile_left"
    assert r["source_output"] == out and r["method"] == "img2img"
    # the image is copied into refs/ (self-contained) + survives deleting the source job
    copied = ws.asset_dir("characters", a["slug"]) / "versions" / "v1_base" / "refs" / r["file"]
    assert copied.is_file()
    import shutil
    shutil.rmtree(ws.out_dir / "job_b0000001")
    assert copied.is_file()


def test_keep_is_idempotent_on_source_output(ws):
    a = assets.create_asset(ws, name="Mara")["profile"]
    out = _stage_b_output(ws)
    assets.keep_ref(ws, a["id"], job_id="job_b0000001", source_output=out, coverage_cell=_cell())
    version = assets.keep_ref(ws, a["id"], job_id="job_b0000001", source_output=out,
                              coverage_cell=_cell())
    assert len(version["ref_set"]) == 1            # not double-added


def test_keep_rejects_invalid_cell(ws):
    a = assets.create_asset(ws, name="Mara")["profile"]
    out = _stage_b_output(ws)
    with pytest.raises(Exception):                 # CoverageError — frozen-contract guard
        assets.keep_ref(ws, a["id"], job_id="job_b0000001", source_output=out,
                        coverage_cell=_cell(angle="sideways"))


def test_cull_removes_ref_and_file(ws):
    a = assets.create_asset(ws, name="Mara")["profile"]
    out = _stage_b_output(ws)
    version = assets.keep_ref(ws, a["id"], job_id="job_b0000001", source_output=out,
                              coverage_cell=_cell())
    ref_id = version["ref_set"][0]["id"]
    refs_dir = ws.asset_dir("characters", a["slug"]) / "versions" / "v1_base" / "refs"
    assert any(refs_dir.iterdir())
    version = assets.remove_ref(ws, a["id"], ref_id=ref_id)
    assert version["ref_set"] == []
    assert not (refs_dir / f"{ref_id}.png").is_file()
    with pytest.raises(ws_mod.WorkspaceError):     # culling an unknown ref
        assets.remove_ref(ws, a["id"], ref_id="ref_999999")


def test_save_persists_prompt_template_and_survives_reload(ws):
    a = assets.create_asset(ws, name="Mara")["profile"]
    out = _stage_b_output(ws)
    assets.keep_ref(ws, a["id"], job_id="job_b0000001", source_output=out, coverage_cell=_cell())
    assets.save_profile(ws, a["id"], prompt_template="a lone ranger, weathered coat")
    # reopen the workspace fresh → the Saved version is intact (curated set + clause)
    ws2 = ws_mod.Workspace.open(ws.path)
    detail = assets.get_asset(ws2, a["id"])
    v = detail["versions"][0]
    assert v["prompt_template"] == "a lone ranger, weathered coat"
    assert v["finalized"] is False                 # Saved, not Finalized (R119)
    assert len(v["ref_set"]) == 1 and v["ref_set"][0]["coverage_cell"]["background"] == "market"
