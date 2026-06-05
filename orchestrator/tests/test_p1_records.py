"""No-GPU invariants for the P1 L1/L2 record layer (M1.5 hardening).

These lock in the data-model contracts the M1 review hardened, so adapter work in M2+
can't quietly reintroduce them:

- **StoryBible style** (`bible.py`): default seed, atomic persist, schema rejects junk.
- **AssetProfile / ProfileVersion** (`assets.py`): neutral `ast_` id, slug-collision
  guard, `v1_base` is Saved-not-Finalized, and **`resolve_version` strictly loads the
  target version record** (the High finding — a corrupt-but-registered version must raise,
  never enqueue Stage A/B/C work against an unloadable version).
- **Launch gate** (`components.py`): the `p1_records` component exists, is phase-scoped to
  P1, and a broken P1 schema blocks startup once P1 is active.

Run from the repo root: `python -m pytest orchestrator/tests -q`.
"""

from __future__ import annotations

import pytest

from orchestrator import assets, bible, components
from orchestrator import workspace as ws_mod


@pytest.fixture()
def ws(tmp_path):
    return ws_mod.Workspace.create(tmp_path / "proj", name="t", size_cap_gb=50)


# --- StoryBible / L1 style -------------------------------------------------------

def test_style_default_then_persist(ws):
    style = bible.load_style(ws)
    assert style["fragment"] == bible.DEFAULT_STYLE_FRAGMENT
    assert style["enabled_default"] is True
    assert not ws.story_json.is_file()   # default is in-memory until first edit

    bible.set_style(ws, fragment="noir, ink wash", enabled_default=False)
    assert ws.story_json.is_file()
    reloaded = bible.load_style(ws)
    assert reloaded["fragment"] == "noir, ink wash"
    assert reloaded["enabled_default"] is False


def test_story_schema_rejects_malformed(ws):
    with pytest.raises(ws_mod.WorkspaceError):
        ws_mod.validate({"schema_version": 1, "id": "sto_000000"}, "story.schema.json")


# --- AssetProfile / ProfileVersion ----------------------------------------------

def test_create_asset_neutral_id_and_v1_base(ws):
    r = assets.create_asset(ws, name="Hero", asset_class="characters")
    prof = r["profile"]
    assert prof["id"].startswith("ast_"), prof["id"]      # neutral, not chr_
    assert prof["slug"] == "hero"
    assert prof["versions"] == [prof["active_version"]]
    v = r["versions"][0]
    assert v["name"] == "v1_base"
    assert v["finalized"] is False                         # Saved, not Finalized (R119)


def test_props_and_scenes_also_get_ast_id(ws):
    for cls in ("props", "scenes"):
        r = assets.create_asset(ws, name=f"x-{cls}", asset_class=cls)
        assert r["profile"]["id"].startswith("ast_")
        assert r["profile"]["asset_class"] == cls


@pytest.mark.parametrize("bad", [
    dict(name="", asset_class="characters"),
    dict(name="ok", asset_class="vehicles"),
])
def test_create_asset_rejects_bad_input(ws, bad):
    with pytest.raises(ws_mod.WorkspaceError):
        assets.create_asset(ws, **bad)


def test_duplicate_name_collides(ws):
    assets.create_asset(ws, name="Hero")
    with pytest.raises(ws_mod.WorkspaceError):
        assets.create_asset(ws, name="Hero")


def test_profile_schema_rejects_legacy_chr_id(ws):
    legacy = {"schema_version": 1, "id": "chr_000000", "name": "x",
              "asset_class": "characters", "created_at": "t",
              "active_version": "ver_000000", "versions": ["ver_000000"]}
    with pytest.raises(ws_mod.WorkspaceError):
        ws_mod.validate(legacy, "profile.schema.json")


# --- version resolution (the High finding) --------------------------------------

def test_resolve_version_active_and_explicit(ws):
    r = assets.create_asset(ws, name="Hero")
    aid, ver = r["profile"]["id"], r["profile"]["active_version"]
    assert assets.resolve_version(ws, aid) == ver           # active by default
    assert assets.resolve_version(ws, aid, ver) == ver      # explicit


def test_resolve_unknown_asset_and_version(ws):
    r = assets.create_asset(ws, name="Hero")
    aid = r["profile"]["id"]
    with pytest.raises(ws_mod.WorkspaceError):
        assets.resolve_version(ws, "ast_ffffff")
    with pytest.raises(ws_mod.WorkspaceError):
        assets.resolve_version(ws, aid, "ver_ffffff")       # not registered


def test_resolve_version_raises_on_corrupt_record(ws):
    """The regression guard: a registered-but-corrupt version.json must fail loudly at
    dispatch, not be silently dropped while the id still resolves."""
    r = assets.create_asset(ws, name="Hero")
    aid = r["profile"]["id"]
    slug = r["profile"]["slug"]
    vj = ws.asset_dir("characters", slug) / "versions" / "v1_base" / "version.json"
    vj.write_text("{ not: valid json", encoding="utf-8")
    with pytest.raises(ws_mod.WorkspaceError):
        assets.resolve_version(ws, aid)
    # the profile list survives (corrupt version skipped, not the whole library)
    assert assets.get_asset(ws, aid)["versions"] == []


# --- launch-gate P1 coverage ----------------------------------------------------

def test_p1_records_component_present_and_phase_scoped():
    comps = {c.id: c for c in components.components()}
    assert "p1_records" in comps
    assert comps["p1_records"].phase == "P1"
    assert comps["p1_records"].present is True   # schemas valid in-repo


def test_default_active_phases_include_p1():
    # the gate now treats P1 as runnable (review): both P0 and P1 gate by default
    monkey_env = components.CONFIG_active_phases_env()
    assert monkey_env in (None, "P0,P1") or "P1" in monkey_env
    # with no override the code default is {P0, P1}
    import os
    if "LOOM_ACTIVE_PHASES" not in os.environ:
        assert components.active_phases() == {"P0", "P1"}


def test_active_phases_routed_through_config_loader(monkeypatch):
    """The launch gate must read LOOM_ACTIVE_PHASES through the central config loader
    (real env > .env.local > .env), not the process env only (review) — so editing the
    committed `.env` takes effect. Guard: the indirection delegates to CONFIG."""
    from orchestrator import config
    # config loader is the source: stub it and confirm the gate reflects it without any
    # process-env var set.
    monkeypatch.delenv("LOOM_ACTIVE_PHASES", raising=False)
    monkeypatch.setattr(type(config.CONFIG), "active_phases_raw",
                        property(lambda self: "P0,P1,P2"))
    assert components.CONFIG_active_phases_env() == "P0,P1,P2"
    assert components.active_phases() == {"P0", "P1", "P2"}


def test_real_env_var_overrides_config_file(monkeypatch):
    """Precedence holds: a real LOOM_ACTIVE_PHASES env var wins over the file value."""
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0")
    assert components.active_phases() == {"P0"}


def test_broken_p1_schema_blocks_only_when_p1_active(monkeypatch):
    """A broken P1 record check must block launch once P1 is active, and be a non-blocking
    report when only P0 is active (phase-scoping holds)."""
    monkeypatch.setitem(components._CODE_CHECKS, "p1_records",
                        ("P1", lambda: (False, "simulated broken P1 schema")))

    monkeypatch.setattr(components, "active_phases", lambda: {"P0"})
    assert components.launch_report()["code_ok"] is True     # reported, not blocking

    monkeypatch.setattr(components, "active_phases", lambda: {"P0", "P1"})
    rep = components.launch_report()
    assert rep["code_ok"] is False                            # now blocking
    with pytest.raises(components.LaunchError):
        components.gate()
