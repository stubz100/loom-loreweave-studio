"""StoryBible / L1 World — M1 seeded the **style fragment**; M8 brings the full L1 World
authoring (P1 §6): long-form **world** prose, a style **global negative**, and the **story
spine** (premise + characters that materialize into stub AssetProfiles, R55 manual re-sync).

The style fragment auto-applies to every image generation, appended after the character
prompt (R104, amended 2026-06-10); the global negative pairs with it. Records inherit P0's
atomic-write + schema rules (validated against `story.schema.json`).
"""

from __future__ import annotations

try:
    from . import workspace as ws_mod
    from .workspace import Workspace, new_id
except ImportError:  # pragma: no cover - direct-run convenience
    import workspace as ws_mod  # type: ignore
    from workspace import Workspace, new_id  # type: ignore

STORY_SCHEMA_VERSION = 1
# A sensible generic starting style — the user edits it in L1; it auto-appends (R104).
DEFAULT_STYLE_FRAGMENT = "cinematic, dramatic lighting, highly detailed, sharp focus"


def _default_story() -> dict:
    return {
        "schema_version": STORY_SCHEMA_VERSION,
        "id": new_id("sto"),
        "style": {"id": new_id("sty"), "fragment": DEFAULT_STYLE_FRAGMENT,
                  "enabled_default": True},
    }


def _save(ws: Workspace, story: dict) -> dict:
    ws_mod.validate(story, "story.schema.json")
    ws.bible_dir.mkdir(parents=True, exist_ok=True)
    ws_mod.atomic_write_json(ws.story_json, story)
    return story


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
              enabled_default: bool | None = None,
              global_negative: str | None = None) -> dict:
    """Update the style fragment / default-on flag / global negative (M8) and persist
    `story.json` atomically. Creates the story on first write."""
    story = load_story(ws)
    if fragment is not None:
        story["style"]["fragment"] = fragment
    if enabled_default is not None:
        story["style"]["enabled_default"] = bool(enabled_default)
    if global_negative is not None:
        story["style"]["global_negative"] = global_negative
    return _save(ws, story)["style"]


# --- M8: L1 World — world prose + story spine -------------------------------------

def set_world(ws: Workspace, world: str) -> dict:
    """Set the long-form world summary (markdown). Returns the full story."""
    story = load_story(ws)
    story["world"] = world
    return _save(ws, story)


def _spine(story: dict) -> dict:
    return story.setdefault("spine", {"premise": "", "characters": []})


def set_premise(ws: Workspace, premise: str) -> dict:
    story = load_story(ws)
    _spine(story)["premise"] = premise
    return _save(ws, story)


def upsert_spine_character(ws: Workspace, *, character_id: str | None = None,
                          name: str | None = None, snippet: str | None = None) -> dict:
    """Add (no id) or edit (id given) a spine character. Returns the full story.
    Editing the snippet here NEVER touches a linked profile — that's the explicit
    `resync_spine_character` action (R55: no auto-clobber of hand-edited profiles)."""
    story = load_story(ws)
    chars = _spine(story).setdefault("characters", [])
    if character_id is None:
        if not (name and name.strip()):
            raise ws_mod.WorkspaceError("spine character name must not be empty")
        chars.append({"id": new_id("spc"), "name": name.strip(),
                      "snippet": (snippet or "").strip(), "linked_asset_id": None})
        return _save(ws, story)
    entry = next((c for c in chars if c["id"] == character_id), None)
    if entry is None:
        raise ws_mod.WorkspaceError(f"spine character {character_id!r} not found")
    if name is not None:
        if not name.strip():
            raise ws_mod.WorkspaceError("spine character name must not be empty")
        entry["name"] = name.strip()
    if snippet is not None:
        entry["snippet"] = snippet
    return _save(ws, story)


def remove_spine_character(ws: Workspace, character_id: str) -> dict:
    """Drop a spine character (does NOT delete a linked AssetProfile — they're
    independent once materialized, R55)."""
    story = load_story(ws)
    spine = _spine(story)
    spine["characters"] = [c for c in spine.get("characters", [])
                           if c["id"] != character_id]
    return _save(ws, story)


def link_spine_character(ws: Workspace, character_id: str, asset_id: str) -> dict:
    """Record which AssetProfile a spine character was materialized into (the
    create-stub endpoint calls this after the profile exists)."""
    story = load_story(ws)
    entry = next((c for c in _spine(story).get("characters", [])
                  if c["id"] == character_id), None)
    if entry is None:
        raise ws_mod.WorkspaceError(f"spine character {character_id!r} not found")
    entry["linked_asset_id"] = asset_id
    return _save(ws, story)


def spine_character(ws: Workspace, character_id: str) -> dict | None:
    story = load_story(ws)
    return next((c for c in _spine(story).get("characters", [])
                 if c["id"] == character_id), None)
