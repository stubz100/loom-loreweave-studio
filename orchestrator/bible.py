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
# The seeded default style's id is FIXED (not random) so an UNPERSISTED default story reads
# back the same id every call — otherwise GET-then-DELETE/set-active would reference an id
# the next default regenerated (a 404). Persists with this id on first edit.
DEFAULT_STYLE_ID = "sty_000000"


# --- L1 styles: a COLLECTION of named prompt/style snippets (was a single fragment) ----
# The original storyboard envisioned a Visual Style with a `style_id` + revisions; the user
# (2026-06-13) asked for multiple named styles, selectable per generation. Canonical store =
# `styles[]` + `active_style_id` + a story-level on/off gate `style_enabled_default`. The
# legacy single `style` object is KEPT as a MIRROR of the ACTIVE style (id/fragment/
# enabled_default/global_negative) so older readers + the schema's `required: style` still
# work, and a project written before this migrates transparently on load.

def _default_story() -> dict:
    story = {
        "schema_version": STORY_SCHEMA_VERSION,
        "id": new_id("sto"),
        "styles": [{"id": DEFAULT_STYLE_ID, "name": "Default",
                    "fragment": DEFAULT_STYLE_FRAGMENT, "global_negative": ""}],
        "active_style_id": DEFAULT_STYLE_ID,
        "style_enabled_default": True,
    }
    return _sync_mirror(story)


def _normalize(story: dict) -> dict:
    """Migrate a legacy single-`style` story to the `styles[]` collection (in-memory;
    persisted on next write) + keep the active id valid + the gate present."""
    if not story.get("styles"):
        legacy = story.get("style") or {}
        sid = legacy.get("id") or new_id("sty")
        story["styles"] = [{
            "id": sid, "name": legacy.get("name") or "Default",
            "fragment": legacy.get("fragment", DEFAULT_STYLE_FRAGMENT),
            "global_negative": legacy.get("global_negative", ""),
        }]
        story.setdefault("active_style_id", sid)
        story.setdefault("style_enabled_default", legacy.get("enabled_default", True))
    ids = [s["id"] for s in story["styles"]]
    if story.get("active_style_id") not in ids:
        story["active_style_id"] = ids[0]
    story.setdefault("style_enabled_default", True)
    return story


def _active(story: dict) -> dict:
    return next(s for s in story["styles"] if s["id"] == story["active_style_id"])


def _find_style(story: dict, style_id: str | None) -> dict | None:
    if not style_id:
        return None
    return next((s for s in story["styles"] if s["id"] == style_id), None)


def _sync_mirror(story: dict) -> dict:
    """Keep `story['style']` = the ACTIVE style (+ the gate) for back-compat + schema."""
    _normalize(story)
    a = _active(story)
    story["style"] = {"id": a["id"], "fragment": a.get("fragment", ""),
                      "enabled_default": bool(story.get("style_enabled_default", True)),
                      "global_negative": a.get("global_negative", "")}
    return story


def _save(ws: Workspace, story: dict) -> dict:
    _sync_mirror(story)
    ws_mod.validate(story, "story.schema.json")
    ws.bible_dir.mkdir(parents=True, exist_ok=True)
    ws_mod.atomic_write_json(ws.story_json, story)
    return story


def load_story(ws: Workspace) -> dict:
    """Load `story.json` (schema-validated); seed a default in-memory story if absent
    (file is written on first edit, not on read). Normalizes legacy single-style stories
    into the `styles[]` collection + refreshes the active-style mirror."""
    if not ws.story_json.is_file():
        return _default_story()
    story = ws_mod.read_json(ws.story_json)
    ws_mod.validate(story, "story.schema.json")
    return _sync_mirror(story)


def load_style(ws: Workspace) -> dict:
    """The ACTIVE style block `{id, fragment, enabled_default, global_negative}` (the mirror)."""
    return load_story(ws)["style"]


def list_styles(ws: Workspace) -> dict:
    """The full style collection for the L1 manager + per-gen selectors."""
    story = load_story(ws)
    return {"styles": story["styles"], "active_style_id": story["active_style_id"],
            "enabled_default": bool(story.get("style_enabled_default", True))}


def resolve_l1(ws: Workspace, apply_style_req: bool | None,
               style_id: str | None = None) -> tuple[bool, str, str]:
    """The L1 style GATE, single source of truth (R104; M8 global negative): returns
    `(apply, fragment, global_negative)`. `apply_style_req` is the per-gen on/off override —
    None honors the story-level `style_enabled_default`. `style_id` picks WHICH style (the
    per-gen selection); None / unknown → the active style. Every generation surface
    (/generate, Stage-B, sketch) resolves through THIS so the choice is consistent. Both
    strings are "" when the gate is off."""
    story = load_story(ws)
    apply = apply_style_req if apply_style_req is not None \
        else bool(story.get("style_enabled_default", True))
    if not apply:
        return False, "", ""
    sty = _find_style(story, style_id) or _active(story)
    return (True, (sty.get("fragment") or "").strip(),
            (sty.get("global_negative") or "").strip())


def join_negative(existing: str | None, global_negative: str) -> str | None:
    """Append the L1 global negative to a request's negative_prompt (M8). Returns the
    merged string, or the original when there's nothing to add."""
    existing = (existing or "").strip()
    if not global_negative:
        return existing or None
    return f"{existing}, {global_negative}" if existing else global_negative


def set_style(ws: Workspace, *, fragment: str | None = None,
              enabled_default: bool | None = None,
              global_negative: str | None = None,
              style_id: str | None = None) -> dict:
    """Edit a style's fragment/global-negative (the ACTIVE one unless `style_id` given) +
    the story-level on/off gate (`enabled_default`). Back-compat shape (returns the active
    mirror). Persists atomically; creates the story on first write.

    STRICT on `style_id` (review 2026-06-13): an unknown id RAISES — a mutation must never
    silently fall back to the active style and overwrite the default (a stale client could
    clobber it). Generation's `resolve_l1` stays lenient (fallback) — a bad id never errors
    a render."""
    story = load_story(ws)
    if style_id is not None:
        target = _find_style(story, style_id)
        if target is None:
            raise ws_mod.WorkspaceError(f"style {style_id!r} not found")
    else:
        target = _active(story)
    if fragment is not None:
        target["fragment"] = fragment
    if global_negative is not None:
        target["global_negative"] = global_negative
    if enabled_default is not None:
        story["style_enabled_default"] = bool(enabled_default)
    return _save(ws, story)["style"]


def add_style(ws: Workspace, *, name: str, fragment: str = "",
              global_negative: str = "") -> dict:
    """Append a new named style to the collection. Returns the full styles view."""
    if not (name and name.strip()):
        raise ws_mod.WorkspaceError("style name must not be empty")
    story = load_story(ws)
    story["styles"].append({"id": new_id("sty"), "name": name.strip(),
                            "fragment": fragment, "global_negative": global_negative})
    _save(ws, story)
    return list_styles(ws)


def update_style(ws: Workspace, style_id: str, *, name: str | None = None,
                 fragment: str | None = None, global_negative: str | None = None) -> dict:
    """Edit any style by id (name/fragment/negative). Returns the full styles view."""
    story = load_story(ws)
    sty = _find_style(story, style_id)
    if sty is None:
        raise ws_mod.WorkspaceError(f"style {style_id!r} not found")
    if name is not None:
        if not name.strip():
            raise ws_mod.WorkspaceError("style name must not be empty")
        sty["name"] = name.strip()
    if fragment is not None:
        sty["fragment"] = fragment
    if global_negative is not None:
        sty["global_negative"] = global_negative
    _save(ws, story)
    return list_styles(ws)


def remove_style(ws: Workspace, style_id: str) -> dict:
    """Drop a style. Refuses the last one; re-points `active_style_id` if it was active."""
    story = load_story(ws)
    if _find_style(story, style_id) is None:
        raise ws_mod.WorkspaceError(f"style {style_id!r} not found")
    if len(story["styles"]) <= 1:
        raise ws_mod.WorkspaceError("can't delete the last style — edit it instead")
    story["styles"] = [s for s in story["styles"] if s["id"] != style_id]
    if story["active_style_id"] == style_id:
        story["active_style_id"] = story["styles"][0]["id"]
    _save(ws, story)
    return list_styles(ws)


def set_active_style(ws: Workspace, style_id: str) -> dict:
    """Set the default style (the one used when a generation doesn't pick one)."""
    story = load_story(ws)
    if _find_style(story, style_id) is None:
        raise ws_mod.WorkspaceError(f"style {style_id!r} not found")
    story["active_style_id"] = style_id
    _save(ws, story)
    return list_styles(ws)


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
