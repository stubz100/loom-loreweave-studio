"""Frozen P1→P2 coverage-cell contract (P1-16, kb-loom-p1 §13 contract-first).

These lock the contract P2's template captioner depends on **before** any Stage-B
generation is built on it, so it can't be evolved mid-milestone:

- the controlled vocabularies (shot size / angle / expression) + their caption phrases,
- `build_caption` output shape (kb-loom-p2 §6's deterministic template caption),
- the JSON schema mirrors `coverage.py` exactly (no drift between record-validation and
  the captioner's source of truth),
- `version.json`'s `ref_set[]` carries a valid coverage_cell on each curated ref.

Run from the repo root: `python -m pytest orchestrator/tests -q`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator import coverage
from orchestrator import workspace as ws_mod

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"


def _valid_cell(**over):
    cell = {"shot_size": "waist_up", "angle": "profile_left",
            "expression": "neutral", "background": "market"}
    cell.update(over)
    return cell


# --- vocabulary is frozen (a change here is a deliberate breaking change) --------

def test_vocab_keys_are_the_frozen_set():
    assert set(coverage.SHOT_SIZES) == {"face_closeup", "portrait", "waist_up", "full_body"}
    assert set(coverage.ANGLES) == {"front", "three_quarter_left", "three_quarter_right",
                                    "profile_left", "profile_right", "back"}
    assert set(coverage.EXPRESSIONS) == {"neutral", "smile", "serious", "sad", "surprised"}
    assert coverage.CONTRACT_VERSION == 1


# --- caption builder: the exact P2 template-caption shape -----------------------

def test_build_caption_shape_matches_p2():
    cell = _valid_cell(shot_size="waist_up", angle="profile_left",
                       expression="neutral", background="market")
    cap = coverage.build_caption(cell, "mara_lw")
    # <trigger>, <angle>, <shot-size>, <expression>[, <bg> background]
    assert cap == "mara_lw, left profile view, waist-up, neutral expression, market background"


def test_build_caption_omits_empty_background():
    cap = coverage.build_caption(_valid_cell(background=""), "mara_lw")
    assert cap == "mara_lw, left profile view, waist-up, neutral expression"
    assert not cap.endswith("background")


def test_build_caption_rejects_bad_cell():
    with pytest.raises(coverage.CoverageError):
        coverage.build_caption(_valid_cell(angle="sideways"), "t")


def test_validate_cell_guards_each_axis():
    coverage.validate_cell(_valid_cell())                       # ok
    for bad in ("shot_size", "angle", "expression"):
        with pytest.raises(coverage.CoverageError):
            coverage.validate_cell(_valid_cell(**{bad: "nope"}))
    with pytest.raises(coverage.CoverageError):
        coverage.validate_cell(_valid_cell(background=123))


# --- schema mirrors the vocab (no drift between record-validation + the captioner) ---

def test_schema_enums_match_coverage_module():
    schema = json.loads((_SCHEMA_DIR / "coverage_cell.schema.json").read_text(encoding="utf-8"))
    props = schema["properties"]
    assert set(props["shot_size"]["enum"]) == set(coverage.SHOT_SIZES)
    assert set(props["angle"]["enum"]) == set(coverage.ANGLES)
    assert set(props["expression"]["enum"]) == set(coverage.EXPRESSIONS)


def test_coverage_cell_schema_validates_and_rejects():
    ws_mod.validate(_valid_cell(), "coverage_cell.schema.json")          # ok
    with pytest.raises(ws_mod.WorkspaceError):
        ws_mod.validate(_valid_cell(angle="diagonal"), "coverage_cell.schema.json")
    with pytest.raises(ws_mod.WorkspaceError):
        ws_mod.validate({"shot_size": "portrait"}, "coverage_cell.schema.json")  # missing axes


# --- version.json ref_set carries coverage cells --------------------------------

def test_version_ref_set_accepts_curated_ref_with_cell():
    ref = {"id": "ref_0a1b2c", "file": "ref_0a1b2c.png", "coverage_cell": _valid_cell(),
           "source_output": "job_x/img.png", "job_id": "job_x", "pipeline": "sd35",
           "method": "inpaint", "seed": 7, "added_at": "t"}
    version = {"schema_version": 1, "id": "ver_000000", "name": "v1_base",
               "finalized": False, "saved_at": "t", "prompt_template": "",
               "ref_set": [ref], "casting": []}
    ws_mod.validate(version, "version.schema.json")


def test_version_ref_set_rejects_ref_without_cell():
    bad = {"id": "ref_0a1b2c", "file": "x.png"}      # no coverage_cell
    version = {"schema_version": 1, "id": "ver_000000", "name": "v1_base",
               "finalized": False, "saved_at": "t", "prompt_template": "",
               "ref_set": [bad], "casting": []}
    with pytest.raises(ws_mod.WorkspaceError):
        ws_mod.validate(version, "version.schema.json")
