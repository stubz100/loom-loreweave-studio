"""Coverage-cell metadata — the **frozen P1→P2 contract** (P1 §7.1, P1-16; kb-loom-p2 §6).

A *coverage cell* is the structured description of one Stage-B dataset image along the
four LoRA-coverage axes (shot size · angle/yaw · expression · background). P1 Stage B
generates images to fill a coverage matrix and records each kept ref's cell; **P2's
template captioner consumes these fields verbatim** to build a deterministic caption
(no VLM, R116) — e.g. `mara_lw, left profile view, waist-up, neutral expression, market
background`. Because P2 depends on it, this vocabulary is a **hard contract, frozen up
front and versioned** (kb-loom-p1 §13 "contract-first"): treat any change to the keys or
to `build_caption`'s output shape as a **breaking change to P2** (bump CONTRACT_VERSION).

The controlled vocabularies (shot size / angle / expression) are the axes' enumerated
values from P1 §7.1; `background` is a free short descriptor ("varied per image", not a
fixed set). The canonical key→phrase maps live here as the single source of truth so P1
(metadata) and P2 (captions) can't drift; `coverage_cell.schema.json` mirrors the enums
(a test asserts they stay in lockstep).
"""

from __future__ import annotations

# Bump if the keys, their meaning, or build_caption's output shape change — P2 keys off this.
CONTRACT_VERSION = 1

# --- controlled vocabularies (ordered: matrix/display order) --------------------
# value -> caption phrase (P2 reads the phrase; P1 stores the value).

SHOT_SIZES: dict[str, str] = {
    "face_closeup": "face close-up",
    "portrait": "portrait",
    "waist_up": "waist-up",
    "full_body": "full body",
}

ANGLES: dict[str, str] = {
    "front": "front view",
    "three_quarter_left": "three-quarter left view",
    "three_quarter_right": "three-quarter right view",
    "profile_left": "left profile view",
    "profile_right": "right profile view",
    "back": "back view",
}

EXPRESSIONS: dict[str, str] = {
    "neutral": "neutral expression",
    "smile": "smiling",
    "serious": "serious expression",
    "sad": "sad expression",
    "surprised": "surprised expression",
}

# the four required axes of a coverage cell (background is a free string, below)
AXES = ("shot_size", "angle", "expression", "background")


class CoverageError(ValueError):
    """A coverage cell violates the frozen vocabulary."""


def validate_cell(cell: dict) -> None:
    """Raise CoverageError unless `cell` is a valid coverage cell: the three enum axes
    present with in-vocabulary values + a `background` string. The authoritative runtime
    check (the JSON schema mirrors this for record validation)."""
    if not isinstance(cell, dict):
        raise CoverageError(f"coverage cell must be an object, got {type(cell).__name__}")
    for axis, vocab in (("shot_size", SHOT_SIZES), ("angle", ANGLES), ("expression", EXPRESSIONS)):
        v = cell.get(axis)
        if v not in vocab:
            raise CoverageError(f"{axis}={v!r} not in {sorted(vocab)}")
    if not isinstance(cell.get("background", ""), str):
        raise CoverageError("background must be a string")


def build_caption(cell: dict, trigger: str) -> str:
    """The **frozen** deterministic caption P2's template captioner emits for a ref
    (kb-loom-p2 §6): `<trigger>, <angle>, <shot-size>, <expression>[, <background> background]`.
    Identity stays implicit (carried by the trigger token), so the caption describes only
    what *varies*. `background` is appended as `"<bg> background"` when non-empty. Provided
    here (not P2) so the contract is executable + drift-tested; P2 imports this verbatim."""
    validate_cell(cell)
    parts = [
        trigger.strip(),
        ANGLES[cell["angle"]],
        SHOT_SIZES[cell["shot_size"]],
        EXPRESSIONS[cell["expression"]],
    ]
    bg = (cell.get("background") or "").strip()
    if bg:
        parts.append(f"{bg} background")
    return ", ".join(p for p in parts if p)
