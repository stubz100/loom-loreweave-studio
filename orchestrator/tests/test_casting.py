"""Stage-A casting persistence invariants (P1/M2 step 1, no GPU).

Locks the data model the user asked to nail *before* the `multi` adapter: completed
candidates persist into `version.json`'s `casting[]`, exactly one is the hero ★, the image
is copied into the version's `casting/` dir (self-contained / survives job deletion), and
the ops are idempotent + traversal-guarded.
"""

from __future__ import annotations

import pytest

from orchestrator import assets
from orchestrator import workspace as ws_mod


@pytest.fixture()
def ws(tmp_path):
    return ws_mod.Workspace.create(tmp_path / "proj", name="t", size_cap_gb=50)


def _fake_output(ws, job_id="job_aaaaaaaa", seed=200):
    """Drop a PNG + sidecar manifest under out/<job>/ as a completed job would."""
    d = ws.out_dir / job_id
    d.mkdir(parents=True, exist_ok=True)
    img = d / "zimage_x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    (d / "zimage_x.json").write_text(f'{{"seed": {seed}}}', encoding="utf-8")
    return f"{job_id}/{img.name}"


def test_star_records_candidate_and_copies_image(ws):
    a = assets.create_asset(ws, name="Mara")["profile"]
    out = _fake_output(ws)
    version = assets.star_candidate(ws, a["id"], job_id="job_aaaaaaaa",
                                    source_output=out, pipeline="zimage", seed=200)
    cast = version["casting"]
    assert len(cast) == 1
    c = cast[0]
    assert c["id"].startswith("cand_")
    assert c["starred"] is True
    assert c["job_id"] == "job_aaaaaaaa"
    assert c["source_output"] == out
    assert c["seed"] == 200
    # the image is copied into the version's casting/ dir (self-contained)
    copied = ws.asset_dir("characters", a["slug"]) / "versions" / "v1_base" / "casting" / c["file"]
    assert copied.is_file()
    # ...and survives deleting the source job's out/ dir
    import shutil
    shutil.rmtree(ws.out_dir / "job_aaaaaaaa")
    assert copied.is_file()


def test_single_hero_invariant(ws):
    a = assets.create_asset(ws, name="Mara")["profile"]
    o1 = _fake_output(ws, "job_11111111", seed=1)
    o2 = _fake_output(ws, "job_22222222", seed=2)
    assets.star_candidate(ws, a["id"], job_id="job_11111111", source_output=o1)
    version = assets.star_candidate(ws, a["id"], job_id="job_22222222", source_output=o2)
    starred = [c for c in version["casting"] if c["starred"]]
    assert len(version["casting"]) == 2
    assert len(starred) == 1 and starred[0]["job_id"] == "job_22222222"


def test_star_is_idempotent_on_job_id(ws):
    a = assets.create_asset(ws, name="Mara")["profile"]
    o1 = _fake_output(ws, "job_11111111")
    assets.star_candidate(ws, a["id"], job_id="job_11111111", source_output=o1)
    version = assets.star_candidate(ws, a["id"], job_id="job_11111111", source_output=o1)
    assert len(version["casting"]) == 1            # no duplicate candidate
    assert version["casting"][0]["starred"] is True


def test_unstar_toggles_hero_off(ws):
    a = assets.create_asset(ws, name="Mara")["profile"]
    o1 = _fake_output(ws, "job_11111111")
    assets.star_candidate(ws, a["id"], job_id="job_11111111", source_output=o1)
    version = assets.star_candidate(ws, a["id"], job_id="job_11111111",
                                    source_output=o1, starred=False)
    assert version["casting"][0]["starred"] is False


def test_set_hero_explicit_and_clear(ws):
    a = assets.create_asset(ws, name="Mara")["profile"]
    o1 = _fake_output(ws, "job_11111111")
    o2 = _fake_output(ws, "job_22222222")
    assets.star_candidate(ws, a["id"], job_id="job_11111111", source_output=o1)
    v = assets.star_candidate(ws, a["id"], job_id="job_22222222", source_output=o2)
    cid1 = next(c["id"] for c in v["casting"] if c["job_id"] == "job_11111111")
    v = assets.set_hero(ws, a["id"], candidate_id=cid1)
    assert [c for c in v["casting"] if c["starred"]][0]["id"] == cid1
    v = assets.set_hero(ws, a["id"], candidate_id=None)             # clear
    assert not any(c["starred"] for c in v["casting"])
    with pytest.raises(ws_mod.WorkspaceError):
        assets.set_hero(ws, a["id"], candidate_id="cand_ffffff")    # unknown


def test_star_rejects_missing_or_unsafe_output(ws):
    a = assets.create_asset(ws, name="Mara")["profile"]
    with pytest.raises(ws_mod.WorkspaceError):
        assets.star_candidate(ws, a["id"], job_id="job_x", source_output="nope/none.png")
    with pytest.raises(ws_mod.WorkspaceError):
        assets.star_candidate(ws, a["id"], job_id="job_x", source_output="../escape.png")


def test_casting_file_path_resolves_and_guards(ws):
    a = assets.create_asset(ws, name="Mara")["profile"]
    o1 = _fake_output(ws, "job_11111111")
    v = assets.star_candidate(ws, a["id"], job_id="job_11111111", source_output=o1)
    fname = v["casting"][0]["file"]
    assert assets.casting_file_path(ws, a["id"], fname).is_file()
    with pytest.raises(ws_mod.WorkspaceError):
        assets.casting_file_path(ws, a["id"], "../../secret.png")


def test_multi_pool_candidates_are_output_keyed(ws):
    """A multi cast = one job → N candidates. Each candidate is a distinct casting entry
    (identity = its output path, not the shared job_id), and any one can be the hero."""
    a = assets.create_asset(ws, name="Mara")["profile"]
    job_id = "job_multi001"
    d = ws.out_dir / job_id / "_inter" / "run" / "ideate"
    outs = []
    for pl in ("flux2", "sd35", "zimage"):
        sub = d / pl / "seed_1"
        sub.mkdir(parents=True)
        f = sub / f"{pl}.png"
        f.write_bytes(b"PNG")
        outs.append(f"{job_id}/_inter/run/ideate/{pl}/seed_1/{pl}.png")
    # star two distinct candidates from the same job
    assets.star_candidate(ws, a["id"], job_id=job_id, source_output=outs[0], pipeline="flux2")
    v = assets.star_candidate(ws, a["id"], job_id=job_id, source_output=outs[1], pipeline="sd35")
    assert len(v["casting"]) == 2                      # two entries, one job
    starred = [c for c in v["casting"] if c["starred"]]
    assert len(starred) == 1 and starred[0]["source_output"] == outs[1]
    # re-starring the same output doesn't duplicate
    v = assets.star_candidate(ws, a["id"], job_id=job_id, source_output=outs[0], pipeline="flux2")
    assert len(v["casting"]) == 2


def test_persisted_casting_survives_reload(ws):
    """The whole point: casting[] + hero are durable across reopening the project."""
    a = assets.create_asset(ws, name="Mara")["profile"]
    o1 = _fake_output(ws, "job_11111111")
    assets.star_candidate(ws, a["id"], job_id="job_11111111", source_output=o1)
    reopened = ws_mod.Workspace.open(ws.path)
    got = assets.get_asset(reopened, a["id"])
    cast = got["versions"][0]["casting"]
    assert len(cast) == 1 and cast[0]["starred"] is True
