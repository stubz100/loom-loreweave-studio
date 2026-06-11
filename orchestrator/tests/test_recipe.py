"""Stage-B dataset recipe engine (P1 §7.1, P1-4) — no GPU.

Locks the coverage-matrix behaviour the done-line builds on: presets expand to valid coverage
cells, prompts are deterministic and structured (style + clause + cell), methods are auto-picked
per shot size (R113), backgrounds vary (subject isolation), and counts land near the R111 targets.
"""

from __future__ import annotations

import pytest

from orchestrator import coverage, recipe


def test_all_presets_present_with_metadata():
    assert set(recipe.preset_names()) == {
        "comprehensive", "full_coverage", "portrait_heavy", "full_body", "npc_lite"}
    for name in recipe.preset_names():
        assert recipe.PRESET_METADATA[name]["kept_target"]  # a [lo, hi] range


def test_build_recipe_cells_are_valid_and_structured():
    r = recipe.build_recipe("full_coverage", character_clause="a lone ranger, weathered coat",
                            style_fragment="noir comic, ink wash", base_seed=100)
    assert r["preset"] == "full_coverage" and r["cells"]
    assert r["target"] == len(r["cells"])
    for i, cell in enumerate(r["cells"]):
        coverage.validate_cell(cell["coverage_cell"])          # frozen contract holds
        assert cell["index"] == i and cell["seed"] == 100 + i  # reproducible per-cell seed
        # prompt = <cell fragment>, <clause>, <style> (user 2026-06-10: coverage terms lead
        # so they keep weight; style trails) — structured, no freeform typing
        assert cell["prompt"].endswith(", a lone ranger, weathered coat, noir comic, ink wash")
        assert not cell["prompt"].startswith("noir comic")
        assert cell["method"] in ("img2img", "inpaint")


def test_method_autopick_matches_shot_size():
    r = recipe.build_recipe("comprehensive", character_clause="x")
    for cell in r["cells"]:
        ss = cell["coverage_cell"]["shot_size"]
        expect = "img2img" if ss in ("face_closeup", "portrait") else "inpaint"
        assert cell["method"] == expect


def test_img2img_realization_drops_backgrounds():
    """M3 realizes every cell via img2img from the hero — the base image fixes the setting,
    so cells carry background="" and the prompt has no background term (user 2026-06-10)."""
    r = recipe.build_recipe("full_coverage", character_clause="x")
    for c in r["cells"]:
        assert c["coverage_cell"]["background"] == ""
        assert "background" not in c["prompt"]


def test_mixed_realization_restores_background_pool_for_inpaint_cells():
    """The M6+ inpaint path keeps the subject-isolation axis (§7.1): inpaint-method cells
    cycle the varied pool; img2img-method cells still inherit the base image."""
    r = recipe.build_recipe("full_coverage", character_clause="x", realize="mixed")
    inpaint_bgs = {c["coverage_cell"]["background"] for c in r["cells"]
                   if c["method"] == "inpaint"}
    assert len(inpaint_bgs) > 1
    assert inpaint_bgs <= set(recipe.DEFAULT_BACKGROUNDS)
    assert all(c["coverage_cell"]["background"] == "" for c in r["cells"]
               if c["method"] == "img2img")


def test_recipe_is_deterministic():
    kw = dict(character_clause="a ranger", style_fragment="noir", base_seed=7)
    assert recipe.build_recipe("portrait_heavy", **kw) == recipe.build_recipe("portrait_heavy", **kw)


def test_preset_counts_in_expected_ballpark():
    # approximate R111 headline sizes (matrix size); ranges, not magic numbers
    ranges = {"comprehensive": (70, 100), "full_coverage": (28, 45), "portrait_heavy": (28, 34),
              "full_body": (40, 50), "npc_lite": (14, 22)}
    for name, (lo, hi) in ranges.items():
        n = recipe.build_recipe(name, character_clause="x")["target"]
        assert lo <= n <= hi, f"{name}={n} outside {lo}..{hi}"


def test_full_coverage_includes_profile_and_back_angles():
    # the recipe must fix the "front-only" failure mode (§7.1 / kb-pipelines01)
    angles = {c["coverage_cell"]["angle"] for c in
              recipe.build_recipe("full_coverage", character_clause="x")["cells"]}
    assert {"profile_left", "profile_right", "back"} <= angles


def test_unknown_preset_and_empty_clause_raise():
    with pytest.raises(recipe.RecipeError):
        recipe.build_recipe("nope", character_clause="x")
    with pytest.raises(recipe.RecipeError):
        recipe.build_recipe("full_coverage", character_clause="   ")
