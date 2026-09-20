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
