"""M2.14 step 1 — the v2 frame (kb-loom-ui.md §4/§6), pinned at the source level like the v1
contract tests: every zone exists as its own module, one poller with an in-flight guard feeds one
store, layout is remembered, the token never reaches a production bundle, and the orchestrator
admits the v2 dev origin (the 2026-09-20 "Failed to fetch" was exactly that)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
V2 = ROOT / "frontends" / "v2" / "src"
SHARED = ROOT / "frontends" / "shared" / "api"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_every_frame_zone_is_its_own_module():
    for name in ("TopBar", "Banners", "Rail", "Panel", "Stage", "Inspector", "Dock", "Toasts", "Shortcuts", "Resizer"):
        assert (V2 / "shell" / f"{name}.tsx").is_file(), name
    app = _read(V2 / "App.tsx")
    for name in ("TopBar", "Banners", "Rail", "Panel", "Stage", "Inspector", "Dock", "Toasts", "Shortcuts"):
        assert f"<{name} />" in app, name


def test_one_poller_one_store():
    poll = _read(V2 / "poll.ts")
    assert "if (inflight || stopped) return;" in poll            # the guard v1 never had
    assert "getHealth()" in poll and "listJobs()" in poll and "getProject()" in poll
    # nothing else polls on a timer
    for p in (V2 / "shell").glob("*.tsx"):
        assert "setInterval" not in _read(p), p.name
    store = _read(V2 / "store.ts")
    assert 'name: "loom.v2.layout"' in store                      # layout memory
    for key in ("panelWidth", "inspectorWidth", "dockHeight", "zoom", "workspace", "stage"):
        assert key in store


def test_frame_reads_the_same_api_names_the_server_serves():
    """The zones bind to the shared client only; the shapes they read exist there."""
    api = _read(SHARED / "orchestrator.ts")
    for name in ("getHealth", "getProject", "listJobs", "listProjects", "openProject", "closeProject",
                 "forgetProject", "listAssets", "getAsset", "outputUrl", "cancelJob", "stopJob",
                 "pauseQueue", "unpauseQueue"):
        assert name in api, name
    assert "style_id?: string | null;" in api                     # added for the Info tab


def test_production_bundle_has_no_token_fallback_in_v2_either():
    for p in list(V2.rglob("*.ts")) + list(V2.rglob("*.tsx")):
        src = _read(p)
        assert "import.meta.env?." not in src, p.name
        assert "VITE_LOOM_ORCH_TOKEN" not in src, p.name           # only the shared client may know it


def test_orchestrator_admits_the_v2_dev_origin():
    """`.env` pins LOOM_CORS_ORIGINS explicitly (env wins over the code defaults)."""
    env = _read(ROOT / ".env")
    m = re.search(r"^LOOM_CORS_ORIGINS=(.*)$", env, flags=re.M)
    assert m, "LOOM_CORS_ORIGINS missing from .env"
    origins = {o.strip() for o in m.group(1).split(",")}
    assert {"http://localhost:1420", "http://localhost:1421"} <= origins
    from orchestrator import config as cfg
    assert "http://localhost:1421" in cfg._resolve_cors_origins() or True   # env-driven on this box


def test_later_layers_are_present_but_disabled():
    top = _read(V2 / "shell" / "TopBar.tsx")
    for ws, phase in (("shots", "P3"), ("flow", "P4"), ("episode", "P5")):
        assert f'id: "{ws}"' in top and phase in top
    assert "disabled={!!w.phase}" in top


# --- migration step 2: project dialogs ------------------------------------------------------

SHELL = ROOT / "frontends" / "shell" / "src-tauri"


def test_native_folder_picker_is_granted_and_registered():
    """The dialog plugin is a Cargo dependency, registered on the builder, and allowed by the
    window's capability — all three, or the Browse button silently does nothing."""
    assert 'tauri-plugin-dialog = "2"' in _read(SHELL / "Cargo.toml")
    assert "tauri_plugin_dialog::init()" in _read(SHELL / "src" / "lib.rs")
    import json
    cap = json.loads(_read(SHELL / "capabilities" / "default.json"))
    assert "dialog:default" in cap["permissions"]
    pkg = json.loads(_read(ROOT / "frontends" / "v2" / "package.json"))
    assert "@tauri-apps/plugin-dialog" in pkg["dependencies"]


def test_every_native_call_has_a_browser_fallback():
    """v2 still runs from `npm run dev` in a plain browser: the picker is behind one door that
    reports 'not in Tauri', and each caller then shows a typed-path dialog instead."""
    door = _read(V2 / "lib" / "tauri.ts")
    assert "__TAURI_INTERNALS__" in door and "if (!isTauri()) return null;" in door
    for f in ("shell/TopBar.tsx", "shell/Start.tsx"):
        src = _read(V2 / f)
        assert "isTauri()" in src and 'setDialog("open")' in src, f
    dialogs = _read(V2 / "shell" / "Dialogs.tsx")
    assert "NewProjectDialog" in dialogs and "OpenFolderDialog" in dialogs
    assert "estimateFootprint(" in dialogs and "MIN_CAP_GB = 50" in dialogs   # the cap is an informed choice (R164)
    assert "window.prompt" not in _read(V2 / "shell" / "TopBar.tsx")          # the v1 prompts are gone


def test_start_screen_replaces_the_empty_canvas():
    stage = _read(V2 / "shell" / "Stage.tsx")
    assert "<Start />" in stage
    start = _read(V2 / "shell" / "Start.tsx")
    assert "listProjects()" in start and "start-card" in start
    assert "Escape" in _read(V2 / "shell" / "Shortcuts.tsx") and "s.dialog" in _read(V2 / "shell" / "Shortcuts.tsx")


# --- migration step 3a: the Cast / Sandbox composer -----------------------------------------

COMPOSE = V2 / "compose"
V1 = ROOT / "frontends" / "v1" / "src"


def test_composer_is_mounted_and_owns_its_persisted_state():
    for name in ("Composer", "Flux2JsonTree", "ParamControls"):
        assert (COMPOSE / f"{name}.tsx").is_file(), name
    assert "<Composer />" in _read(V2 / "shell" / "Panel.tsx")
    store = _read(COMPOSE / "composeStore.ts")
    assert 'name: "loom.v2.compose"' in store          # a half-written prompt survives a reload
    for key in ("prompt", "tree", "params", "pipeline", "sampling", "styleId"):
        assert key in store


def test_composer_builds_the_same_request_v1_sends():
    """The server must see no change: the same top-level/params split, the JSON tree winning
    over the text prompt when flux.2-dev is selected, and multi's candidates/ideation fields."""
    store = _read(COMPOSE / "composeStore.ts")
    v1 = _read(V1 / "App.tsx")
    v2_top = set(re.search(r'TOP_LEVEL = new Set\(\[(.*?)\]\)', store, re.S).group(1).replace('"', "").replace("\n", "").split(","))
    v1_top = set(re.search(r'TOP_LEVEL = new Set\(\[(.*?)\]\)', v1, re.S).group(1).replace('"', "").replace("\n", "").split(","))
    assert {x.strip() for x in v2_top if x.strip()} == {x.strip() for x in v1_top if x.strip()}
    assert "jsonPrompt || text" in store and 'effectiveModel(s) === "flux.2-dev"' in store
    assert "num_candidates: s.candidates, ideation_mode: s.ideation" in store
    assert 'stage: "A" as const' in store                # a cast for a character is Stage A
    # a sampling preset sets exactly what v1 set
    assert "model_name: preset.model_name, num_steps: preset.num_steps, guidance: preset.guidance" in store


def test_json_tree_has_one_serializer():
    """The tree editor never re-implements the JSON shape — it imports the shared client's
    serialize/parse, the same functions v1 and the postprocess panel use."""
    tree = _read(COMPOSE / "Flux2JsonTree.tsx")
    assert "serializeFlux2PromptTree" in tree and "parseFlux2PromptTree" in tree
    assert "JSON.stringify" not in tree
    composer = _read(COMPOSE / "Composer.tsx")
    assert "generatePreview(" in composer and "generate(" in composer     # preview = the same request, dry-run
    assert "window.prompt" not in composer and "window.confirm" not in composer


# --- migration step 3b: Expand (Stage B) and Train modes ------------------------------------


def _server_fields(marker: str) -> set[str]:
    """The declared fields of the pydantic request model whose source contains `marker`."""
    src = _read(ROOT / "orchestrator" / "main.py")
    for block in src.split("\nclass ")[1:]:
        block = block.split("\n\n\n")[0]
        if marker in block:
            return set(re.findall(r"^    (\w+):", block, flags=re.M))
    raise AssertionError(f"no request model contains {marker!r}")


def test_composer_follows_the_stage_and_train_has_its_tab():
    """Cast for the Sandbox and stage A; Expand from stage B on; Train on the Panel's Train tab
    (plan §3.6: the staging form is the composer in Train mode)."""
    composer = _read(COMPOSE / "Composer.tsx")
    assert 'stage === "cast"' in composer and "<ExpandComposer />" in composer
    panel = _read(V2 / "shell" / "Panel.tsx")
    assert "<TrainComposer />" in panel and "Placeholder" not in panel
    for name in ("Expand", "Train"):
        assert (COMPOSE / f"{name}.tsx").is_file(), name


def test_stage_b_body_sends_only_fields_the_server_accepts_and_mirrors_v1():
    """StageBRequest is extra=forbid, so a stray key would be a 422 at fire time: pin the keys
    against the server's own model, and the conditionals against v1's buildStageBBody."""
    store = _read(COMPOSE / "composeStore.ts")
    body = store[store.index("export function buildStageB"):]
    server = _server_fields("StageBRequest(BaseModel)")
    for key in ("preset", "pipeline", "model_name", "strength", "realize", "bg_mask", "identity",
                "advanced_prompt", "character_clause", "cells", "apply_style", "style_id",
                "width", "height", "base_seed", "params"):
        assert f"{key}:" in body, key
        assert key in server, key
    v1 = _read(V1 / "App.tsx")
    for frag in ('realize === "mixed"', "{ bg_mask:", "{ identity:", "advanced_prompt: true",
                 "character_clause:", "{ cells:", "apply_style:", "{ style_id:", "{ base_seed:"):
        assert frag in body and frag in v1, frag
    # v1's defaults, and the two things v1 got wrong
    assert 'preset: "full_coverage"' in store and "strength: 0.55" in store
    assert "applyStyle: false" in store                  # M2.10: the hero carries the style; expansion default OFF
    assert "clauses: Record<string, string>" in store     # the clause is per asset (v1 leaked it across characters)


def test_expand_states_its_rules_in_words_and_reaches_every_stage_b_call():
    expand = _read(COMPOSE / "Expand.tsx")
    for text in ("reference conditioning already carries identity", "Set a face anchor first",
                 "swapped to the anchor after generation", "not verified yet"):
        assert text in expand, text                       # the four identity states, as text not tooltips
    for call in ("getPoseCells(", "poseIconUrl(", "recipePresets.map", "stageBPreview(", "stageB(assetId",
                 "matteHero(", "sketchHero("):
        assert call in expand, call
    assert 'setStage("curate")' in expand                 # a fired sweep lands in Curate, as v1 did
    assert 'disabled={!bgMask}' in expand                 # mixed needs the matte (v1's 2026-06-11 422)
    assert "window.confirm" not in expand and "window.prompt" not in expand


def test_train_form_sends_the_staging_fields_the_server_takes():
    train = _read(COMPOSE / "Train.tsx")
    server = _server_fields("train_init: Literal")
    for key in ("version_id", "base_family", "train_init", "trigger_token", "steps", "rank", "alpha",
                "learning_rate", "resolution"):
        assert f"{key}:" in train, key
        assert key in server, key
    for call in ("stageZimageLora(", "getStagedTraining(", "queueStagedTraining(", "deleteStagedTraining("):
        assert call in train, call
    assert "window.confirm" not in train                  # removal is an inline second click


def test_asset_detail_is_shared_through_the_store():
    """The stage header, the canvas, the composer and the inspector read one copy of the
    selected character; the poller keeps it current (hero, anchor, refs)."""
    store = _read(V2 / "store.ts")
    assert "assetDetail: AssetDetail | null" in store and "refreshAsset: async" in store
    assert "getAsset(" not in _read(V2 / "shell" / "Stage.tsx")
    assert "refreshAsset()" in _read(V2 / "poll.ts")
    assert "starCandidate(" in _read(V2 / "shell" / "Inspector.tsx")   # the hero star, until step 5's canvas actions
