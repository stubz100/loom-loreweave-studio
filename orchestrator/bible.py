"""StoryBible / L1 World — minimal at M1 (just the **style fragment**, P1 §6).

The style fragment is a **fixed prepend** to every image generation, with a per-generation
override (R104). M1 seeds one editable fragment in `story.json`; the full L1 World UI
(asset classes, naming, spine) lands at M8. Records inherit P0's atomic-write + schema
rules (validated against `story.schema.json`).
"""

from __future__ import annotations

try:
    from . import workspace as ws_mod
    from .workspace import Workspace, new_id
except ImportError:  # pragma: no cover - direct-run convenience
    import workspace as ws_mod  # type: ignore
    from workspace import Workspace, new_id  # type: ignore

STORY_SCHEMA_VERSION = 1
# A sensible generic starting style — the user edits it in L1; it auto-prepends (R104).
DEFAULT_STYLE_FRAGMENT = "cinematic, dramatic lighting, highly detailed, sharp focus"


def _default_story() -> dict:
    return {
        "schema_version": STORY_SCHEMA_VERSION,
        "id": new_id("sto"),
        "style": {"id": new_id("sty"), "fragment": DEFAULT_STYLE_FRAGMENT,
                  "enabled_default": True},
    }


def load_story(ws: Workspace) -> dict:
    """Load `story.json` (schema-validated); seed a default in-memory story if absent
    (file is written on first edit, not on read)."""
    if not ws.story_json.is_file():
        return _default_story()
    story = ws_mod.read_json(ws.story_json)
    ws_mod.validate(story, "story.schema.json")
    return story


def load_style(ws: Workspace) -> dict:
    """The active style block `{id, fragment, enabled_default}`."""
    return load_story(ws)["style"]


def set_style(ws: Workspace, *, fragment: str | None = None,
              enabled_default: bool | None = None) -> dict:
    """Update the style fragment / default-on flag and persist `story.json` atomically.
    Creates the story on first write."""
    story = load_story(ws)
    if fragment is not None:
        story["style"]["fragment"] = fragment
    if enabled_default is not None:
        story["style"]["enabled_default"] = bool(enabled_default)
    ws_mod.validate(story, "story.schema.json")
    ws.bible_dir.mkdir(parents=True, exist_ok=True)
    ws_mod.atomic_write_json(ws.story_json, story)
    return story["style"]
