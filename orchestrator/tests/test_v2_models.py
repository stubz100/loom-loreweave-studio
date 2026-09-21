"""M2.17 step d — the v2 Models page and the cache pieces, pinned at the source level like the
frame tests: the client calls name routes the server registers, the page is mounted and holds
the location row, the roster with its actions and the prune that lists first, the banner and
the composers act on a refusal, and the inventory rides the one poller."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
V2 = ROOT / "frontends" / "v2" / "src"
SHARED = ROOT / "frontends" / "shared" / "api"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_client_cache_calls_match_the_server_routes():
    api = _read(SHARED / "orchestrator.ts")
    main = _read(ROOT / "orchestrator" / "main.py")
    routes = set(re.findall(r'@app\.(get|post|put|delete)\("(/cache[^"]*)"', main))
    server = {(m.upper(), p) for m, p in routes}
    wanted = {
        ("GET", "/cache"), ("POST", "/cache/{repo_id:path}/repair"), ("POST", "/cache/fetch"),
        ("POST", "/cache/{repo_id:path}/verify"), ("PUT", "/cache/{repo_id:path}/pin"),
        ("DELETE", "/cache/{repo_id:path}/revisions/{commit}"), ("DELETE", "/cache/{repo_id:path}"),
        ("POST", "/cache/prune"), ("PUT", "/cache/location"), ("POST", "/cache/move"), ("DELETE", "/cache/previous"),
        ("PUT", "/cache/token"), ("POST", "/cache/token/check"),
    }
    assert wanted <= server, wanted - server
    for name in ("getCache", "repairCacheRef", "fetchCache", "verifyCache", "pinCache", "deleteCacheRevision",
                 "deleteCacheRepo", "pruneCache", "setCacheLocation", "moveCache", "deletePreviousCache", "setCacheToken", "checkCacheToken"):
        assert f"export const {name}" in api or f"export async function {name}" in api, name
    for path in ('`/cache/${repoPath(repoId)}/repair`', '"/cache/fetch"', '`/cache/${repoPath(repoId)}/verify`',
                 '`/cache/${repoPath(repoId)}/pin`', '`/cache/${repoPath(repoId)}/revisions/${encodeURIComponent(commit)}`',
                 '`/cache/${repoPath(repoId)}`', '"/cache/prune"', '"/cache/location"', '"/cache/move"', '"/cache/previous"',
                 '"/cache/token"', '"/cache/token/check"'):
        assert path in api, path
    assert 'health: "ok" | "ref_drift" | "partial" | "missing" | "empty" | "unused" | "stale_extra"' in api
    assert "export interface CacheModel" in api and "export interface CacheUse" in api and "export interface CacheToken" in api
    assert "masked: string | null" in api and "token: string" not in api.split("export interface CacheToken")[1].split("}")[0]   # never the token itself


def test_models_page_is_mounted_and_complete():
    page = _read(V2 / "models" / "Models.tsx")
    assert "modelsOpen ? <Models />" in _read(V2 / "shell" / "Stage.tsx")
    for piece in ("Change…", "Move…", "Use this folder", "Start the move", "Delete the previous tree",
                  "Models loom uses", "Repos in the cache", "Other repos in this cache", "List what a prune would remove",
                  "Hugging Face token", "Save token", "Check first", "set by .env.local"):
        assert piece in page, piece
    for call in ("repairCacheRef(", "fetchCache(", "verifyCache(", "pinCache(", "deleteCacheRepo(", "deleteCacheRevision(",
                 "pruneCache(true)", "pruneCache(false)", "setCacheLocation(", "moveCache(", "deletePreviousCache(",
                 "setCacheToken(", "checkCacheToken("):
        assert call in page, call
    # by model: every repo line names its role, every model its stages; the token field never shows the token
    assert "cache.models" in page and "{r.role}" in page and 'm.where.join(" · ")' in page
    assert 'type="password"' in page and "tok.masked" in page
    assert "u.label" in page and "u.role" in page                                # the repo rows say who uses them, readably
    assert 'className={confirm === key ? "danger" : ""}' in page           # every destructive action a second click
    assert 'r.needed ? "Delete? loom needs it" : "Delete?"' in page
    assert "loc.managed" in page and "remove LOOM_MODELS_DIR from .env" in page.lower() or "Remove it there" in page
    assert "window.confirm" not in page and "window.prompt" not in page


def test_models_page_is_reachable_and_closes():
    top = _read(V2 / "shell" / "TopBar.tsx")
    assert ">Models…</button>" in top and "openModels" in top                 # File ▸ Models… and the status item
    assert "models{cache ?" in top
    keys = _read(V2 / "shell" / "Shortcuts.tsx")
    assert "if (s.modelsOpen) { s.closeModels();" in keys
    store = _read(V2 / "store.ts")
    for key in ("cache: CacheInventory | null", "modelsOpen: boolean", "refreshCache: async", "openModels:", "closeModels:"):
        assert key in store, key
    poll = _read(V2 / "poll.ts")
    assert "if (useApp.getState().modelsOpen || n % 15 === 1) await s.refreshCache();" in poll   # the one poller


def test_banner_and_composers_act_on_the_cache():
    banners = _read(V2 / "shell" / "Banners.tsx")
    assert 'r.health === "ref_drift"' in banners and "repairCacheRef(" in banners and 'label: "Repair"' in banners
    ui = _read(V2 / "models" / "cacheUi.tsx")
    assert "export function VariantWeights" in ui and "export function Problem" in ui
    assert '`catalog:${pipeline}/${model}`' in ui                              # the roster's used-by tag
    assert "cache.models" in ui and "r.role" in ui                              # every repo of the variant, the bad ones named
    assert '/"repo_id"\\s*:\\s*"([^"]+)"/' in ui                                # the 412 detail names the repo
    for f in ("compose/Composer.tsx", "compose/Expand.tsx"):
        src = _read(V2 / f)
        assert "<Problem text={problem} />" in src and "<VariantWeights" in src, f
        assert '<span className="form-error" role="alert">{problem}</span>' not in src, f
