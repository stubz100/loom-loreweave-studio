"""flux2 advanced (structured) prompt builder — M0d Part A (kb-loom-p2 §12 "M0d").

flux2 `ref`-mode Stage-B holds identity (carried by the hero reference) but follows pose
**loosely**: the default step-distilled klein adheres weakly, and loom's per-cell prompt used a
flat coverage phrase (`"three-quarter left view"`) that the reference easily overrides on
composition — so "three-quarter left" comes back with the body turned one way and the head the
other. This builds an explicit, **directive-led** prompt instead: each coverage **angle** maps to
an unambiguous camera+pose directive (head AND body), framing + expression follow, and the
identity clause + L1 style trail.

The coverage **vocabulary stays frozen** ([[coverage]]) — this module only *reads* it and decides
how to *phrase* a cell to flux2; it never changes the keys or the captioner's output. Mirrors
FLUX.2 prompting guidance (BFL / RunDiffusion, kb-loom-p2 §12 "Sources"): most-important tokens
first (so the pose dominates the loosely-adhering model), concrete phrasing, **positive only**
(FLUX.2 takes no negative prompts — loom already drops the L1 global-negative for flux2).
"""

from __future__ import annotations

import json

try:
    from . import coverage
except ImportError:  # pragma: no cover - direct-run convenience
    import coverage  # type: ignore


# coverage ANGLE value -> explicit camera+pose directive (head AND body), replacing the loose
# "<x> view". This is the M0d pose fix: it pins head/body alignment the flat phrasing missed.
ANGLE_DIRECTIVES: dict[str, str] = {
    "front": "facing the camera directly, head and shoulders squared to the viewer",
    "three_quarter_left":
        "body and head both turned three-quarters toward the viewer's left (¾ left view)",
    "three_quarter_right":
        "body and head both turned three-quarters toward the viewer's right (¾ right view)",
    "profile_left":
        "full left profile, body and face turned 90° to the viewer's left, looking left",
    "profile_right":
        "full right profile, body and face turned 90° to the viewer's right, looking right",
    "back": "seen from behind, back to the camera, head facing away",
}

# coverage SHOT_SIZE value -> explicit framing directive (more concrete than the caption phrase).
SHOT_DIRECTIVES: dict[str, str] = {
    "face_closeup": "tight face close-up, the head filling the frame",
    "portrait": "portrait framing, head and shoulders",
    "waist_up": "waist-up medium shot",
    "full_body": "full-body shot, head to feet in frame",
}


def angle_directive(angle: str) -> str:
    """The explicit camera+pose directive for a coverage angle (falls back to the frozen phrase)."""
    return ANGLE_DIRECTIVES.get(angle, coverage.ANGLES.get(angle, angle))


def shot_directive(shot_size: str) -> str:
    """The explicit framing directive for a coverage shot size (falls back to the frozen phrase)."""
    return SHOT_DIRECTIVES.get(shot_size, coverage.SHOT_SIZES.get(shot_size, shot_size))


def build_cell_prompt(cell: dict, character_clause: str, style_fragment: str = "",
                      *, as_json: bool = False) -> str:
    """The advanced flux2 cell prompt. Default (klein/base, labeled): **`<camera+pose directive>,
    <framing>, <expression>[, <bg> background], <identity clause>, <style>`** — pose/composition
    LEAD so they dominate the loosely-adhering model; identity rides the reference image + the
    clause; style trails. With **`as_json=True`** (flux.2-dev, whose Mistral VLM parses JSON
    precisely — M0d Part A/C): the same fields as a compact structured-JSON object instead.
    Deterministic; the coverage vocabulary is validated + frozen either way."""
    coverage.validate_cell(cell)
    angle = angle_directive(cell["angle"])
    shot = shot_directive(cell["shot_size"])
    expr = coverage.EXPRESSIONS[cell["expression"]]
    bg = (cell.get("background") or "").strip()
    clause = (character_clause or "").strip()
    style = (style_fragment or "").strip()
    if as_json:
        return build_cell_json(clause, angle, shot, expr, bg, style)
    directive_parts = [angle, shot, expr]
    if bg:
        directive_parts.append(f"{bg} background")
    directive = ", ".join(directive_parts)
    return ", ".join(p for p in (directive, clause, style) if p)


def build_cell_json(clause: str, angle: str, shot: str, expr: str,
                    bg: str = "", style: str = "") -> str:
    """A compact structured-JSON cell prompt for flux.2-dev (its Mistral VLM parses JSON precisely).
    Mirrors the labeled form's fields as a JSON object; empty fields are dropped; non-ASCII is kept
    (the VLM reads ¾/° directly). Deterministic, key order fixed."""
    obj: dict[str, str] = {}
    if clause:
        obj["subject"] = clause
    obj["pose"] = angle
    obj["shot"] = shot
    obj["expression"] = expr
    if bg:
        obj["background"] = bg
    if style:
        obj["style"] = style
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
