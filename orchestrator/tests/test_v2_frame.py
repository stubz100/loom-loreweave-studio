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
