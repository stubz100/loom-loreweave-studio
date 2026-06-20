"""flux2 advanced (structured) prompt builder — M0d Part A. No GPU.

Locks the pose fix: every frozen coverage angle has an explicit camera+pose DIRECTIVE that
names head AND body (the "three-quarter left → body one way, head the other" bug), the cell
prompt leads with composition then trails identity + style, and build_recipe(advanced_prompt=True)
swaps the flat coverage phrase for directives WITHOUT touching the frozen coverage vocabulary.
"""

from __future__ import annotations

from orchestrator import coverage, flux2_prompt, recipe


def test_every_coverage_angle_has_a_directive():
    # the directive table covers the frozen vocab exactly (no drift, no extras)
    assert set(flux2_prompt.ANGLE_DIRECTIVES) == set(coverage.ANGLES)
    assert set(flux2_prompt.SHOT_DIRECTIVES) == set(coverage.SHOT_SIZES)


def test_three_quarter_directive_pins_head_and_body():
    d = flux2_prompt.angle_directive("three_quarter_left")
    low = d.lower()
    assert "head" in low and "body" in low and "left" in low
    # the loose phrase that caused the mismatch is gone
    assert "three-quarter left view" not in low


def test_profile_and_front_and_back_directives_are_explicit():
    assert "profile" in flux2_prompt.angle_directive("profile_right").lower()
    assert "facing the camera" in flux2_prompt.angle_directive("front").lower()
    assert "behind" in flux2_prompt.angle_directive("back").lower()


def test_unknown_angle_falls_back_to_frozen_phrase():
    # defensive: an out-of-vocab angle returns the coverage phrase / the raw value, never crashes
    assert flux2_prompt.angle_directive("front") == flux2_prompt.ANGLE_DIRECTIVES["front"]
    assert flux2_prompt.angle_directive("nope") == "nope"


def test_build_cell_prompt_leads_with_pose_then_identity_then_style():
    cell = {"shot_size": "waist_up", "angle": "three_quarter_left",
            "expression": "neutral", "background": ""}
    out = flux2_prompt.build_cell_prompt(cell, "mara, red coat", "watercolor")
    # directive leads, identity clause in the middle, style trails
    assert out.startswith(flux2_prompt.angle_directive("three_quarter_left"))
    assert "mara, red coat" in out
    assert out.rstrip().endswith("watercolor")
    # framing + expression present
    assert "waist-up" in out and "neutral expression" in out


def test_build_cell_prompt_includes_background_when_present():
    cell = {"shot_size": "full_body", "angle": "front",
            "expression": "smile", "background": "market"}
    out = flux2_prompt.build_cell_prompt(cell, "clause", "")
    assert "market background" in out


def test_build_cell_prompt_validates_coverage_vocab():
    import pytest
    with pytest.raises(coverage.CoverageError):
        flux2_prompt.build_cell_prompt(
            {"shot_size": "waist_up", "angle": "diagonal", "expression": "neutral"}, "c")


def test_recipe_advanced_prompt_uses_directives_only_when_on():
    flat = recipe.build_recipe("npc_lite", character_clause="mara")
    adv = recipe.build_recipe("npc_lite", character_clause="mara", advanced_prompt=True)
    assert flat["advanced_prompt"] is False and adv["advanced_prompt"] is True
    # same matrix / cells / seeds, different phrasing
    assert [c["coverage_cell"] for c in flat["cells"]] == [c["coverage_cell"] for c in adv["cells"]]
    assert [c["seed"] for c in flat["cells"]] == [c["seed"] for c in adv["cells"]]
    # a three-quarter cell: flat carries the loose phrase, advanced carries the directive
    tq_flat = next(c for c in flat["cells"] if c["coverage_cell"]["angle"] == "three_quarter_left")
    tq_adv = next(c for c in adv["cells"] if c["coverage_cell"]["angle"] == "three_quarter_left")
    assert "three-quarter left view" in tq_flat["prompt"]
    assert "three-quarter left view" not in tq_adv["prompt"]
    assert "head" in tq_adv["prompt"].lower() and "body" in tq_adv["prompt"].lower()


def test_build_cell_prompt_json_for_dev():
    """as_json=True (flux.2-dev) emits a valid compact JSON object with the same fields as the
    labeled form — pose/shot/expression + subject/style, the loose phrase gone."""
    import json
    cell = {"shot_size": "waist_up", "angle": "three_quarter_left",
            "expression": "neutral", "background": "market"}
    out = flux2_prompt.build_cell_prompt(cell, "mara, red coat", "watercolor", as_json=True)
    obj = json.loads(out)                                   # must be valid JSON
    assert obj["subject"] == "mara, red coat"
    assert obj["pose"] == flux2_prompt.angle_directive("three_quarter_left")
    assert "head" in obj["pose"].lower() and "body" in obj["pose"].lower()
    assert obj["shot"] == flux2_prompt.shot_directive("waist_up")
    assert obj["expression"] == "neutral expression"
    assert obj["background"] == "market" and obj["style"] == "watercolor"
    assert "three-quarter left view" not in out


def test_build_cell_json_drops_empty_fields():
    import json
    cell = {"shot_size": "full_body", "angle": "front", "expression": "smile", "background": ""}
    obj = json.loads(flux2_prompt.build_cell_prompt(cell, "", "", as_json=True))
    assert "subject" not in obj and "background" not in obj and "style" not in obj
    assert obj["pose"] and obj["shot"] and obj["expression"]


def test_recipe_json_prompt_emits_json_cells():
    """build_recipe(json_prompt=True) → every cell prompt is JSON; advanced_prompt reported True
    (JSON is the dev directive form); same matrix/seeds as the labeled advanced build."""
    import json
    r = recipe.build_recipe("npc_lite", character_clause="mara", style_fragment="ink",
                            json_prompt=True)
    assert r["json_prompt"] is True and r["advanced_prompt"] is True
    for c in r["cells"]:
        obj = json.loads(c["prompt"])                       # every cell parses as JSON
        assert obj["subject"] == "mara" and obj["pose"]
    # same cells/seeds as the labeled advanced build (only the phrasing differs)
    labeled = recipe.build_recipe("npc_lite", character_clause="mara", style_fragment="ink",
                                  advanced_prompt=True)
    assert [c["seed"] for c in r["cells"]] == [c["seed"] for c in labeled["cells"]]


def test_recipe_advanced_prompt_is_deterministic():
    a = recipe.build_recipe("full_coverage", character_clause="x", style_fragment="y",
                            advanced_prompt=True)
    b = recipe.build_recipe("full_coverage", character_clause="x", style_fragment="y",
                            advanced_prompt=True)
    assert [c["prompt"] for c in a["cells"]] == [c["prompt"] for c in b["cells"]]
