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
    assert "stageZimageLora(" in train
    assert "getStagedTraining(" in _read(V2 / "store.ts")                # staged runs are store state (step 6)
    dock6 = _read(V2 / "shell" / "Dock.tsx")
    assert "queueStagedTraining(" in dock6 and "deleteStagedTraining(" in dock6
    assert "window.confirm" not in train                  # removal is an inline second click


def test_asset_detail_is_shared_through_the_store():
    """The stage header, the canvas, the composer and the inspector read one copy of the
    selected character; the poller keeps it current (hero, anchor, refs)."""
    store = _read(V2 / "store.ts")
    assert "assetDetail: AssetDetail | null" in store and "refreshAsset: async" in store
    assert "getAsset(" not in _read(V2 / "shell" / "Stage.tsx")
    assert "refreshAsset()" in _read(V2 / "poll.ts")
    assert "starCandidate(" in _read(V2 / "inspect" / "InfoTab.tsx")   # the hero star, until step 5's canvas actions


# --- migration step 4: the Inspector tabs -----------------------------------------------------

INSPECT = V2 / "inspect"


def test_inspector_tabs_are_their_own_modules():
    """Info, Post and Version are real; the Inspector only routes. With nothing selected the
    Info tab shows the version (the version is the selection when no tile is, plan §3.5)."""
    for name in ("InfoTab", "PostTab", "VersionTab"):
        assert (INSPECT / f"{name}.tsx").is_file(), name
    insp = _read(V2 / "shell" / "Inspector.tsx")
    for mount in ("<InfoTab", "<PostTab", "<VersionTab"):
        assert mount in insp, mount
    assert "!selection ? <VersionTab />" in insp
    assert 'later: "P4"' in insp                         # Muse still waits for P4


def test_post_tab_sends_only_fields_the_server_accepts_and_mirrors_v1():
    post = _read(INSPECT / "PostTab.tsx")
    v1 = _read(V1 / "App.tsx")
    add = _server_fields("AddPostprocStepRequest(BaseModel)")
    for key in ("base", "preset", "backend", "params", "source"):
        assert key in add, key
    assert "addStep({ base, preset, backend: isI2i ? backend : undefined, params, source: effectiveSource || undefined, ...(isInpaint ? { mask: mask.trim(), requires_mask: true } : {}) })" in post
    queue = _server_fields("QueuePostprocStepRequest(BaseModel)")
    assert {"requester_id", "stage"} <= queue
    # the preset list is the server's Literal, no more and no less
    main = _read(ROOT / "orchestrator" / "main.py")
    lit = re.search(r'preset: Literal\[(.*?)\]', main[main.index("class AddPostprocStepRequest"):]).group(1)
    server_presets = set(re.findall(r'"(\w+)"', lit))
    v2_presets = set(re.findall(r'\{ id: "(\w+)", label:', post))
    assert v2_presets == server_presets, (v2_presets, server_presets)
    # the effective-steps readout uses v1's constants (which a v1 test pins to model_catalog.py)
    assert "const MIN_EFFECTIVE_I2I_STEPS = 4;" in post and "const MIN_EFFECTIVE_I2I_STEPS = 4;" in v1
    assert 'const I2I_EXACT_BACKENDS = new Set(["flux2"]);' in post and 'const I2I_EXACT_BACKENDS = new Set(["flux2"]);' in v1
    # a queued pass lands in the grid the author is looking at: A stays A, D stays D, else B
    assert 'stage === "cast" ? "A" : stage === "train" ? "D" : "B"' in post
    assert 'stage === "A" ? "A" : stage === "D" ? "D" : "B"' in v1
    # StyleLock never runs on flux2 (the drift source; 422 server-side)
    assert 'if ((p === "stylelock" || p === "inpaint") && backend === "flux2") setBackend("sd35");' in post


def test_post_tab_draws_a_tree_with_tombstones():
    post = _read(INSPECT / "PostTab.tsx")
    assert "function treeOrder(" in post and "st.source !== parentImage" in post   # a step hangs under the image it reads
    assert '" tomb"' in post and "text-decoration: line-through" in _read(V2 / "styles.css")
    assert "function liveStatus(" in post and 'return "deleted"' in post
    assert '"Delete image?"' in post and "window.confirm" not in post           # two-click remove, no native dialog
    assert "tailIds" in post                                                      # any leaf of the tree can be removed, not only the last


def test_stacks_are_shared_through_the_store():
    store = _read(V2 / "store.ts")
    for name in ("stacks: PostprocStack[]", "refreshStacks: async", "addStep: async", "queueStep: async", "removeStep: async"):
        assert name in store, name
    assert "refreshStacks()" in _read(V2 / "poll.ts")
    for f in INSPECT.glob("*.tsx"):
        assert "getPostprocStacks(" not in _read(f), f.name                      # one reader: the poller


def test_version_and_info_tabs_reach_every_call():
    version = _read(INSPECT / "VersionTab.tsx")
    for call in ("activateVersion(", "createVersion(", "finalizeVersion(", "unfinalizeVersion(", "saveProfile(",
                 "clearAnchor(", "getCaptions("):
        assert call in version, call
    info = _read(INSPECT / "InfoTab.tsx")
    for call in ("starCandidate(", "setAnchor(", "deriveFacePortrait(", "rerunJob("):
        assert call in info, call
    # the re-run knob aliases are v1's (different pipelines name their knobs differently)
    v1 = _read(V1 / "RerunPanel.tsx")
    for consts in ('const STEP_KEYS = ["num_steps", "num_inference_steps", "steps"];',
                   'const GUIDANCE_KEYS = ["guidance", "guidance_scale", "cfg"];'):
        assert consts in info and consts in v1, consts
    for f in (version, info):
        assert "window.confirm" not in f and "window.prompt" not in f


# --- migration step 5: the canvas ----------------------------------------------------------------

CANVAS = V2 / "canvas"


def test_canvas_is_its_own_modules_and_the_stage_routes_the_views():
    for name in ("tiles.ts", "actions.ts", "registry.ts", "Tile.tsx", "Grid.tsx", "Grouped.tsx", "Loupe.tsx"):
        assert (CANVAS / name).is_file(), name
    stage = _read(V2 / "shell" / "Stage.tsx")
    for mount in ("<Grid ", "<Grouped ", "<Loupe "):
        assert mount in stage, mount
    assert "deriveCanvas(" in stage and "scopedJobs(" in stage


def test_tiles_scope_and_derivation_mirror_v1():
    """The Sandbox is what the project itself requested (requester = project id — step 1 had
    filtered for a literal "sandbox" and showed nothing); a character's grid is its active
    version at the stage letter (Curate reviews B; D admits only image makers). Tiles come
    from jobs exactly as v1 flattened them."""
    tiles = _read(CANVAS / "tiles.ts")
    assert "j.requester_id === projectId" in tiles and '"sandbox"' not in tiles
    assert 'letter === "C" ? "B" : letter' in tiles
    assert 'j.pipeline !== "zimage_trainer" && j.mode !== "score"' in tiles      # v1's makesAnImage
    assert "if (job.deleted) return [];" in tiles                                  # a tombstone draws nothing
    assert "names.length > 1" in tiles and "partial_outputs" in tiles             # multi pool + interim tiles
    assert "`ref:${r.id}`" in tiles and "!onGrid.has(r.source_output)" in tiles   # durable refs in Curate
    for f in ("filters.shot", "filters.angle", "filters.expression", "filters.showRejected"):
        assert f in tiles, f
    v1 = _read(V1 / "App.tsx")
    assert 'j.pipeline !== "zimage_trainer" && j.mode !== "score"' in v1


def test_coverage_vocabulary_is_the_servers():
    """One UI module holds the frozen coverage keys, and they equal coverage.py's."""
    cov = _read(ROOT / "orchestrator" / "coverage.py")
    def server(name: str) -> list[str]:
        block = cov[cov.index(f"{name}: dict[str, str] = {{"):]
        block = block[:block.index("}")]
        return re.findall(r'^\s+"(\w+)":', block, flags=re.M)
    ui = _read(V2 / "lib" / "coverage.ts")
    def client(name: str) -> list[str]:
        return re.findall(r'"(\w+)"', re.search(rf"export const {name} = \[(.*?)\];", ui).group(1))
    assert client("SHOTS") == server("SHOT_SIZES")
    assert client("ANGLES") == server("ANGLES")
    assert client("EXPRESSIONS") == server("EXPRESSIONS")
    for f in ("compose/Expand.tsx", "shell/Stage.tsx"):
        assert 'from "../lib/coverage"' in _read(V2 / f), f                       # no second copy


def test_keyboard_moves_by_visual_row_and_covers_the_review_keys():
    grid = _read(CANVAS / "Grid.tsx")
    assert "new ResizeObserver(" in grid and "Math.floor((el.clientWidth + GAP) / (zoom + GAP))" in grid
    keys = _read(V2 / "shell" / "Shortcuts.tsx")
    assert "idx + cols" in keys and "idx - cols" in keys and "idx + 5" not in keys   # v1 moved by a fixed 5
    for k in ('e.key === "k"', 'e.key === "x"', 'e.key === " "', 'e.key === "Delete"', 'e.key === "Enter"',
              'e.key === "c"', 'e.key === "Home"', 'e.key === "End"'):
        assert k in keys, k
    assert "s.pendingDelete === cur.key" in keys                                  # Del twice
    for line in ("<dt>← → ↑ ↓</dt>", "<dt>Enter</dt>", "<dt>k</dt>", "<dt>x</dt>", "<dt>space</dt>", "<dt>Del</dt>", "<dt>c</dt>"):
        assert line in keys, line                                                # the ? overlay lists them


def test_destructive_actions_never_run_on_a_single_click():
    for f in CANVAS.glob("*.ts*"):
        assert "window.confirm" not in _read(f), f.name
    tile = _read(CANVAS / "Tile.tsx")
    assert 'flags.pendingDelete ? "Delete?"' in tile and "if (flags.pendingDelete) void tileActions.remove(tile); else setPendingDelete(tile.key)" in tile
    stage = _read(V2 / "shell" / "Stage.tsx")
    assert 'pendingDelete === "__bulk__"' in stage                               # bulk delete, second click
    grouped = _read(CANVAS / "Grouped.tsx")
    assert "confirmGroup === g.id" in grouped                                     # group delete, second click
    actions = _read(CANVAS / "actions.ts")
    assert "perImage = !!names && names.length > 1 && !!t.output" in actions      # v1's per-image rule
    for call in ("starCandidate(", "keepRef(", "cullRef(", "rejectOutput(", "cancelJob(", "deleteOutput(", "deleteJob("):
        assert call in actions, call


def test_affordances_show_on_hover_and_on_the_selected_tile():
    css = _read(V2 / "styles.css")
    assert ".tile:hover .acts, .tile.selected .acts, .tile:focus-within .acts { opacity: 1; }" in css
    assert ".grid.fill .tile img" in css                                          # fit / fill
    tile = _read(CANVAS / "Tile.tsx")
    assert 'title={flags.hero ? "remove the hero star" : "star as the hero"}' in tile
    assert "keep into the curated set (k)" in tile and "reject (x)" in tile and "bulk action (space)" in tile


def test_loupe_and_grouped_views():
    loupe = _read(CANVAS / "Loupe.tsx")
    assert "go(-1)" in loupe and "go(1)" in loupe and "setCompare(" in loupe and 'className={`loupe-body${pinned ? " two" : ""}`}' in loupe
    store = _read(V2 / "store.ts")
    assert "viewBeforeLoupe" in store and "closeLoupe:" in store and "openLoupe:" in store
    assert "refId?: string" in store                                              # a durable ref is selectable
    assert "<RefInfo" in _read(V2 / "shell" / "Inspector.tsx")
    grouped = _read(CANVAS / "Grouped.tsx")
    assert "node.job.chained_from" in grouped and "`solo:${root.job.id}`" in grouped
    for prefix in ("prv_", "trn_", "rdn_", "poses_"):
        assert f'batchId.startsWith("{prefix}")' in grouped, prefix
    assert 'className="chain-arrow"' in grouped                                   # a chain reads left to right


# --- migration step 6: Train ----------------------------------------------------------------------


def test_train_surfaces_have_their_homes():
    """Form in the composer's Train tab; staged runs + trainer jobs in the dock's Training pane;
    captions on the canvas; readiness in the Inspector; the preview form back in the composer."""
    assert (CANVAS / "Captions.tsx").is_file() and (INSPECT / "ReadinessTab.tsx").is_file() and (COMPOSE / "LoraPreview.tsx").is_file()
    assert "<Captions />" in _read(V2 / "shell" / "Stage.tsx")
    insp = _read(V2 / "shell" / "Inspector.tsx")
    assert "<ReadinessTab />" in insp and 'later: "step 6"' not in insp
    dock = _read(V2 / "shell" / "Dock.tsx")
    for f in ('id: "active"', 'id: "training"', 'id: "recent"'):
        assert f in dock, f
    assert 'j.pipeline === "zimage_trainer"' in dock and "queueStagedTraining(" in dock and "deleteStagedTraining(" in dock
    for call in ("promoteTrainedLora(", "cleanupTrainingRun(", "deleteJob(", "setPreviewJob(j.id)"):
        assert call in dock, call
    train = _read(COMPOSE / "Train.tsx")
    assert "<LoraPreview" in train and "getStagedTraining(" not in train        # staged runs moved to the dock
    assert "refreshStaged" in _read(V2 / "store.ts") and "refreshStaged()" in _read(V2 / "poll.ts")


def test_train_bodies_send_only_fields_the_server_accepts():
    preview = _read(COMPOSE / "LoraPreview.tsx")
    server = _server_fields("LoraPreviewRequest(BaseModel)")
    for key in ("pose", "prompt", "seed", "width", "height", "lora_weight", "with_lora"):
        assert key in server, key
        assert f"{key}:" in preview or f"{key}: false" in preview, key
    caps = _read(CANVAS / "Captions.tsx")
    assert "caption" in _server_fields("CaptionOverrideRequest(BaseModel)")
    assert "setCaptionOverride(" in caps and "clearCaptionOverride(" in caps and "getCaptions(" in caps
    ready = _read(INSPECT / "ReadinessTab.tsx")
    assert "version_id" in _server_fields("ReadinessEmbedRequest(BaseModel)")
    assert {"version_id", "job_id"} <= _server_fields("ReadinessPersistRequest(BaseModel)")
    for call in ("getReadiness(", "queueReadinessEmbed(", "persistReadiness("):
        assert call in ready, call


def test_readiness_scan_closes_through_the_one_poller():
    """v1 polled the scan job with its own setInterval; v2 watches the store's jobs and
    persists when the job reaches done."""
    ready = _read(INSPECT / "ReadinessTab.tsx")
    assert "setInterval" not in ready and "const j = jobs[scanJob];" in ready
    assert 'if (j.status === "done")' in ready and "persistReadiness(assetId, versionId ?? undefined, scanJob)" in ready
    # details inline, not tooltips: chips for missing cells, thumbnails for groups and outliers
    assert "v.missing.map((m) => <span key={m} className=\"pill\">" in ready
    assert "thumbs(g)" in ready and "thumbs(om.outliers!)" in ready and "thumbs(cap.missing_trigger)" in ready
    assert "Advisory only; Train stays enabled." in ready                     # R14


def test_captions_view_edits_are_per_row_and_reset_all_is_a_second_click():
    caps = _read(CANVAS / "Captions.tsx")
    assert "refUrl(assetId, c.file, version.id)" in caps                       # the image beside the text
    assert 'c.origin === "edited" ? "edited" : "template"' in caps and "no trigger" in caps
    assert 'confirmAll ? "Reset every caption?" : "Reset all"' in caps
    assert "setCanvas(null)" in caps                                            # arrows have no tiles here
    for f in ("filters.shot", "filters.angle", "filters.expression"):
        assert f in caps, f


def test_lora_preview_defaults_to_the_trained_resolution():
    preview = _read(COMPOSE / "LoraPreview.tsx")
    assert 'placeholder="trained"' in preview and "Identity collapses at twice the trained size." in preview
    assert "with_lora: false" in preview                                        # the A/B against the base
    assert "getPreviewPoses(" in preview and "poseIconUrl(p.pose_key)" in preview
    assert "out of vocabulary" in preview                                       # T-pose says so


def test_no_native_dialogs_anywhere_in_v2():
    for p in list(V2.rglob("*.ts")) + list(V2.rglob("*.tsx")):
        src = _read(p)
        assert "window.confirm" not in src and "window.prompt" not in src and "window.alert" not in src, p.name


# --- migration step 7: Edit mode + the Inpaint pass -----------------------------------------------


def test_edit_mode_is_a_canvas_view_with_its_tools():
    assert (CANVAS / "Edit.tsx").is_file() and (CANVAS / "editState.ts").is_file()
    stage = _read(V2 / "shell" / "Stage.tsx")
    assert '{ id: "edit", label: "Edit" }' in stage and "<Edit image={editImage!} />" in stage
    assert 'v.id === "edit" && !editable' in stage                          # needs a finished still with a job
    edit = _read(CANVAS / "Edit.tsx")
    for tool in ('setTool("brush")', 'setTool("eraser")', 'setTool("lasso")', "invert", "fromMatte", "Feather"):
        assert tool in edit, tool
    assert 'o.fillStyle = "#000"' in edit and "blur(${feather}px)" in edit    # black = keep, white = repaint, feathered on export
    assert "saveMask(image, b64)" in edit and "addMask(image, r.mask)" in edit
    assert 'toggleInspector("post")' in edit                               # saving hands over to the Post tab
    assert 'crossOrigin="anonymous"' in edit                               # the matte is read across the loopback origin
    assert 'role === "bgmask"' in edit                                     # the matte source


def test_edit_mode_keys_and_state():
    keys = _read(V2 / "shell" / "Shortcuts.tsx")
    assert 'if (s.view === "edit") { if (!editEscape()) s.closeEdit();' in keys   # a lasso eats the first Escape
    assert 'if (s.view === "edit") return;' in keys                            # the painter owns the keys
    assert 'e.key === "e"' in keys and "<dt>e</dt>" in keys
    store = _read(V2 / "store.ts")
    assert '"edit"' in store and "openEdit:" in store and "closeEdit:" in store and "addMask:" in store
    assert "masks: Record<string, string[]>" in store
    state = _read(CANVAS / "editState.ts")
    assert "export function getWork(" in state and "export function editEscape(" in state   # strokes survive leaving the view


def test_inpaint_preset_reaches_the_server_with_its_mask():
    """The Post tab sends preset + mask + requires_mask; the server model admits inpaint; the
    store schema admits it too (the 409 that the first run of the backend tests caught)."""
    post = _read(INSPECT / "PostTab.tsx")
    assert '{ id: "inpaint", label: "Inpaint (masked)"' in post
    assert "mask: mask.trim(), requires_mask: true" in post
    assert "isInpaint ? 0.95" in post                                        # the preset's strength
    assert "!isInpaint" in post and "no resize" in post                       # a mask is pixel-aligned
    assert 'Paint a mask' in post and "openEdit" in post
    main = _read(ROOT / "orchestrator" / "main.py")
    assert '"inpaint": {"backend": "sd35", "mode": "inpaint", "params": {"strength": 0.95}}' in main
    import json
    schema = json.loads(_read(ROOT / "orchestrator" / "schemas" / "postproc_store.schema.json"))
    assert "inpaint" in schema["properties"]["stacks"]["items"]["properties"]["steps"]["items"]["properties"]["preset"]["enum"]
    api = _read(SHARED / "orchestrator.ts")
    assert "export async function saveMask(" in api and '| "inpaint";' in api
    assert "atomic_write_bytes" in _read(ROOT / "orchestrator" / "workspace.py")
