"""Stage-B dataset recipe engine (P1 §7.1, P1-4) — the coverage matrix loom auto-fills.

Stage B exists **strictly to build LoRA-training material**, so it is coverage-driven, not
freeform (R107/R109): the user picks a **recipe preset** (a coverage matrix) + the character
clause, and loom deterministically generates the prompt list to fill it — `<L1 style> +
<character clause> + <recipe cell: shot-size, angle, expression, background>` (no per-image
prompt typing). Each generated image carries its **frozen coverage_cell** ([[coverage]]) so
Stage C curates it and P2 captions it.

This module is **generation-side and mutable** (unlike `coverage.py`, the frozen P1→P2
contract): preset distributions, prompt wording, and per-cell method defaults can evolve.
It only *reads* the frozen vocabulary + reuses its canonical phrases.

- **Presets (R111):** Comprehensive (~80–100, main chars) · Full-coverage (~30–40, default) ·
  Portrait-heavy (~30, dialogue-led) · Full-body/outfit (~45, costume-critical) · NPC-lite
  (~20). Counts are the matrix size (approximate) — the user can tune target/emphasis later.
- **Per-cell method auto-pick (R113):** close-ups/portraits → `img2img` (expression/pose
  sweep from the hero); waist-up/full-body → `inpaint` (stable subject into varied scenes —
  the background-diversity axis). Auto-picked but exposed for override before generating.
- **Backgrounds** cycle a varied pool (subject isolation, §7.1) — but **only for cells whose
  realization can change the background** (inpaint, M6+). img2img-realized cells (all of M3)
  inherit the hero's setting, so they carry `background=""` and no background prompt term —
  a background clause in an img2img prompt only fights the base image (user, 2026-06-10).
"""

from __future__ import annotations

try:
    from . import coverage, flux2_prompt
except ImportError:  # pragma: no cover - direct-run convenience
    import coverage  # type: ignore
    import flux2_prompt  # type: ignore


class RecipeError(ValueError):
    """Unknown preset or malformed recipe request."""


# Angle / expression bundles used to build the per-preset matrices.
_A_FRONTQ = ("front", "three_quarter_left", "three_quarter_right")
_A_WITH_PROFILE = (*_A_FRONTQ, "profile_left", "profile_right")
_A_ALL = (*_A_WITH_PROFILE, "back")
_E_CORE = ("neutral", "smile", "serious")
_E_ALL = ("neutral", "smile", "serious", "sad", "surprised")

# A bucket = (shot_size, angles, expressions); a preset's matrix is the union of its buckets'
# rectangular (angle × expression) cells, in declared order. Distributions follow §7.1
# (identity from close-ups, proportions/outfit from full-body, angle variety incl. profile).
_PRESET_BUCKETS: dict[str, tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]] = {
    "comprehensive": (
        ("face_closeup", _A_WITH_PROFILE, _E_ALL),     # 25 — identity, every angle+expression
        ("portrait", _A_WITH_PROFILE, _E_ALL),         # 25 — expression variety
        ("waist_up", _A_ALL, _E_CORE),                 # 18 — angle variety incl. profile/back
        ("full_body", _A_WITH_PROFILE, ("neutral", "serious")),  # 10 — proportions/outfit
    ),  # ~78
    "full_coverage": (
        ("face_closeup", _A_FRONTQ, _E_CORE),          # 9
        ("portrait", _A_FRONTQ, ("neutral", "smile", "serious", "sad")),  # 12
        ("waist_up", _A_ALL, ("neutral",)),            # 6 — profile/back coverage
        ("full_body", ("front", *_A_FRONTQ[1:], "back"), ("neutral",)),   # 4
    ),  # ~31
    "portrait_heavy": (
        ("face_closeup", _A_FRONTQ, _E_ALL),           # 15
        ("portrait", _A_FRONTQ, _E_ALL),               # 15
    ),  # 30 — dialogue-led (close/portrait)
    "full_body": (
        ("full_body", _A_ALL, ("neutral", "serious")),  # 12 — every angle, costume-critical
        ("waist_up", _A_ALL, _E_CORE),                 # 18
        ("portrait", _A_FRONTQ, ("neutral", "smile")),  # 6
        ("face_closeup", _A_FRONTQ, _E_CORE),          # 9
    ),  # 45
    "npc_lite": (
        ("face_closeup", _A_FRONTQ, ("neutral", "smile")),     # 6
        ("portrait", _A_FRONTQ, ("neutral",)),                 # 3
        ("waist_up", _A_WITH_PROFILE, ("neutral",)),           # 5
        ("full_body", ("front", "three_quarter_left", "back"), ("neutral",)),  # 3
    ),  # 17
}

# UI/inspector metadata (R111): kept-after-curation target range + who it's for.
PRESET_METADATA: dict[str, dict] = {
    "comprehensive": {"kept_target": [40, 60], "for": "main characters (need detail)"},
    "full_coverage": {"kept_target": [25, 40], "for": "default main/supporting"},
    "portrait_heavy": {"kept_target": [20, 25], "for": "dialogue-led characters"},
    "full_body": {"kept_target": [30, 35], "for": "costume/proportion-critical"},
    "npc_lite": {"kept_target": [15, 20], "for": "NPCs (don't need much)"},
}

DEFAULT_PRESET = "full_coverage"

# Varied background pool — cycled across cells so the LoRA learns the subject, not a backdrop.
DEFAULT_BACKGROUNDS = (
    "plain studio", "outdoor street", "indoor room",
    "natural landscape", "soft gradient", "urban exterior",
)


def _method_for(shot_size: str) -> str:
    """Auto-pick the realization method per shot size (R113, §7.1), overridable downstream.
    Close framing → img2img pose/expression sweep from the hero; wider framing → inpaint the
    subject into varied scenes (background diversity)."""
    return "img2img" if shot_size in ("face_closeup", "portrait") else "inpaint"


def _cell_prompt_fragment(cell: dict) -> str:
    """The generation-prompt fragment for a cell — the canonical coverage phrases joined
    WITHOUT a trigger token (the trigger is a P2 captioning concept, not an image prompt)."""
    parts = [
        coverage.ANGLES[cell["angle"]],
        coverage.SHOT_SIZES[cell["shot_size"]],
        coverage.EXPRESSIONS[cell["expression"]],
    ]
    bg = (cell.get("background") or "").strip()
    if bg:
        parts.append(f"{bg} background")
    return ", ".join(parts)


def preset_names() -> list[str]:
    return list(_PRESET_BUCKETS)


def _matrix(preset: str) -> list[tuple[str, str, str]]:
    """The ordered (shot_size, angle, expression) triples for a preset's buckets."""
    if preset not in _PRESET_BUCKETS:
        raise RecipeError(f"unknown recipe preset {preset!r}; one of {preset_names()}")
    cells: list[tuple[str, str, str]] = []
    for shot_size, angles, exprs in _PRESET_BUCKETS[preset]:
        for angle in angles:
            for expr in exprs:
                cells.append((shot_size, angle, expr))
    return cells


def build_recipe(preset: str, *, character_clause: str, style_fragment: str = "",
                 base_seed: int = 0, shared_seed: bool = False,
                 backgrounds: tuple[str, ...] | None = None,
                 realize: str = "img2img", advanced_prompt: bool = False,
                 json_prompt: bool = False) -> dict:
    """Expand a preset into the concrete Stage-B work list (P1-4). **Deterministic**: the same
    (preset, clause, style, base_seed, backgrounds, realize) yields the same cells/prompts/seeds,
    in a fixed order. Each cell carries a validated `coverage_cell` (the frozen contract), the
    auto-generated `prompt`, an auto-picked `method` (overridable), and a `seed`.

    `shared_seed` (user 2026-06-27): the seed is the **same across the whole sweep** (every cell
    gets `base_seed`) so the only thing that varies across cells is the pose/angle/expression —
    the natural expectation when you set a seed, and identity-friendly for the flux2 ref path.
    Off (the default) keeps the legacy per-cell `base_seed + index` for back-compat / reproducible
    distinct draws.

    **Prompt order (user decision 2026-06-10): `<cell fragment>, <clause>, <style>`** — the
    coverage terms lead so they keep weight against a long character clause (tokens at the front
    of the prompt dominate); the style fragment trails (it mostly restates the base image anyway).

    `realize` is how the cells will actually be generated. M3 realizes **everything via
    img2img from the hero** — the base image already fixes the setting, so a background term in
    the prompt only *fights* it (user finding 2026-06-10): with `realize="img2img"` cells get
    `background=""` (the frozen contract allows it; P2's caption simply omits it) and no
    background clause in the prompt. `realize="mixed"` (the M6+ inpaint path) restores the
    cycled `backgrounds` pool for inpaint-method cells (subject isolation, §7.1).

    `character_clause` defaults to the asset's stub prompt-template snippet (R112) — passed in
    by the caller, kept fixed across the set so the LoRA learns the character, not the noise.

    `advanced_prompt` (M0d Part A, flux2): build each cell prompt from the explicit
    camera+pose **directive** form (`flux2_prompt.build_cell_prompt`) instead of the flat
    coverage phrase — pins head/body alignment for flux2 `ref`-mode (the loose-pose fix). The
    coverage vocabulary is unchanged; only the phrasing differs. Off ⇒ today's flat string.

    `json_prompt` (M0d, flux.2-dev only): emit each advanced cell prompt as a structured-JSON
    object (the Mistral-VLM dev parses JSON precisely) instead of the labeled directive string.
    Implies `advanced_prompt` (JSON is the directive form for dev)."""
    if not character_clause or not character_clause.strip():
        raise RecipeError("character_clause must not be empty (defaults to the asset's snippet)")
    if realize not in ("img2img", "mixed"):
        raise RecipeError(f"unknown realize {realize!r}; one of ['img2img', 'mixed']")
    bgs = backgrounds or DEFAULT_BACKGROUNDS
    if not bgs:
        raise RecipeError("backgrounds pool must not be empty")
    clause = character_clause.strip()
    style = (style_fragment or "").strip()

    cells: list[dict] = []
    for i, (shot_size, angle, expr) in enumerate(_matrix(preset)):
        method = _method_for(shot_size)
        # Background only when the realization can actually change it (inpaint, M6+).
        bg = bgs[i % len(bgs)] if (realize == "mixed" and method == "inpaint") else ""
        cov = {"shot_size": shot_size, "angle": angle, "expression": expr, "background": bg}
        coverage.validate_cell(cov)   # frozen-contract guard
        if advanced_prompt or json_prompt:
            prompt = flux2_prompt.build_cell_prompt(cov, clause, style, as_json=json_prompt)
        else:
            prompt = ", ".join(p for p in (_cell_prompt_fragment(cov), clause, style) if p)
        cells.append({
            "index": i,
            "coverage_cell": cov,
            "prompt": prompt,
            "method": method,
            "seed": base_seed if shared_seed else base_seed + i,
        })

    return {
        "preset": preset,
        "target": len(cells),
        "kept_target": PRESET_METADATA[preset]["kept_target"],
        "character_clause": clause,
        "style_fragment": style,
        "advanced_prompt": advanced_prompt or json_prompt,
        "json_prompt": json_prompt,
        "cells": cells,
    }
