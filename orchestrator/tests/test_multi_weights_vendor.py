"""No-GPU invariants for the M2 review follow-ups.

- **Preset-aware weight pre-flight** (`components.multi_weights_status`): the `multi`
  casting pipeline's gated flux2/sd35 checkpoints are checked per SELECTED ideation
  preset at cast time — and are kept OUT of the phase-scoped launch gate so they don't
  over-gate a rig. Locks in: the preset sets load from `models.json`; a present cache
  reports ok; a missing one reports the gated repos; and `weights_ok()` (the phase gate)
  is blind to presets by design.
- **Vendored `multi` stack** (`config.pipeline_roots`): the vendored `pipelines/multistack`
  root is registered ahead of the monorepo fallback, and `multi` resolves into it with the
  path math its module-invocation + self-locating stage_runner need (parents[2] == a dir
  whose `pipeline/multi/run_pipeline.py` exists, with a sibling `flux2/src` lib).

Run from the repo root: `python -m pytest orchestrator/tests -q`.
"""

from __future__ import annotations

import pytest

from orchestrator import components
from orchestrator.adapters import multi
from orchestrator.config import CONFIG


# --- preset-aware multi weights (not phase-scoped) ------------------------------

def test_presets_loaded_from_manifest():
    fast = {e["id"] for e in components.multi_preset_weights("fast")}
    refined = {e["id"] for e in components.multi_preset_weights("refined")}
    # the gated headliners each preset needs (mirror of pipeline/multi IDEATION_PRESETS)
    assert {"flux2-klein-4b", "flux2-dev-ae", "sd35-large-turbo"} <= fast
    assert {"flux2-klein-9b", "flux2-dev-ae", "sd35-large"} <= refined
    assert components.multi_preset_weights("nope") == []


def test_status_ok_when_all_probes_hit(monkeypatch):
    monkeypatch.setattr(components, "_hf_cache_probe", lambda repo, probe: True)
    ok, missing = components.multi_weights_status("fast")
    assert ok is True and missing == []


def test_text_encoder_resolves_to_exact_platform_repo(monkeypatch):
    # The flux2 Klein text encoder loads FP8 Qwen3 on CUDA but the non-FP8 repo on Windows
    # ROCm — the gate must check the EXACT repo that will load, not "either".
    te = next(e for e in components.multi_preset_weights("fast")
              if e["id"] == "qwen3-4b-text-encoder")
    # CUDA path: needs the FP8 repo
    monkeypatch.setattr(components, "_needs_fp8_workaround", lambda: False)
    assert components._entry_resolve_repo(te) == "Qwen/Qwen3-4B-FP8"
    # Windows ROCm path: needs the non-FP8 repo
    monkeypatch.setattr(components, "_needs_fp8_workaround", lambda: True)
    assert components._entry_resolve_repo(te) == "Qwen/Qwen3-4B"


def test_status_missing_reports_platform_repo(monkeypatch):
    # On ROCm, with only the FP8 TE cached (wrong variant for this platform), the preset must
    # report the non-FP8 repo as missing — i.e. exact gating, not a false pass.
    monkeypatch.setattr(components, "_needs_fp8_workaround", lambda: True)
    monkeypatch.setattr(components, "_hf_cache_probe",
                        lambda repo, probe: repo == "Qwen/Qwen3-4B-FP8")
    ok, missing = components.multi_weights_status("fast")
    assert ok is False
    te_missing = [m for m in missing if m["id"] == "qwen3-4b-text-encoder"]
    assert te_missing and te_missing[0]["repo_id"] == "Qwen/Qwen3-4B"


def test_fetch_records_success_after_download(monkeypatch):
    # Regression guard: the success path must re-check presence without a NameError
    # (the `probe`-undefined bug). Simulate a download that lands the repo in cache.
    monkeypatch.setattr(components, "_needs_fp8_workaround", lambda: False)
    import huggingface_hub
    landed = {"hit": False}

    def fake_snapshot(repo_id, token=None):
        landed["hit"] = True

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot, raising=False)
    # after a "download", presence flips to True so the result reads fetched=True
    monkeypatch.setattr(components, "_hf_cache_probe",
                        lambda repo, probe: landed["hit"])
    res = components.fetch_multi_preset("fast")
    assert res["results"] and all(r.get("fetched") for r in res["results"])
    assert "error" not in (res["results"][0])  # no NameError surfaced as a per-repo error


def test_status_fails_closed_on_unknown_preset():
    # A preset with no configured weight set must NOT silently pass (fail-open) — that would
    # disable the 412 pre-flight via a manifest edit. It reports not-ok with an explanation.
    ok, missing = components.multi_weights_status("does-not-exist")
    assert ok is False and missing and "no weight set" in (missing[0].get("error") or "")


def test_status_reports_missing_with_gated_flags(monkeypatch):
    # nothing cached -> every preset entry is reported, carrying its gated flag + repo
    monkeypatch.setattr(components, "_hf_cache_probe", lambda repo, probe: False)
    ok, missing = components.multi_weights_status("fast")
    assert ok is False
    ids = {m["id"] for m in missing}
    assert {"flux2-klein-4b", "flux2-dev-ae", "sd35-large-turbo"} <= ids
    gated = {m["id"] for m in missing if m["gated"]}
    assert "flux2-klein-4b" in gated and "sd35-large-turbo" in gated
    # every entry exposes repo_id so the UI can point the user at the license page
    assert all(m["repo_id"] for m in missing)


def test_phase_gate_is_blind_to_multi_presets(monkeypatch):
    # Even with NO preset weights cached, the phase gate (weights_ok) must not flip —
    # multi presets are deliberately not phase-essential (no over-gating).
    monkeypatch.setattr(components, "_hf_cache_probe", lambda repo, probe: False)
    before_ok, _ = components.weights_ok()
    after_ok, _ = components.weights_ok()
    assert before_ok == after_ok  # multi presets never entered the phase gate


def test_unknown_preset_fetch_is_safe():
    res = components.fetch_multi_preset("nope")
    assert res["results"] == [] and "unknown preset" in res["error"]


# --- vendored multi stack -------------------------------------------------------

def test_multi_vendored_root_registered():
    vendored = CONFIG.app_repo_root / "pipelines" / "multistack" / "src" / "pipeline"
    assert vendored in CONFIG.pipeline_roots
    # vendored root precedes the monorepo fallback (src/pipeline)
    monorepo = CONFIG.src_root / "pipeline"
    if monorepo in CONFIG.pipeline_roots:
        assert CONFIG.pipeline_roots.index(vendored) < CONFIG.pipeline_roots.index(monorepo)


def test_multi_resolves_into_vendored_tree():
    script = multi.resolve_script(CONFIG.pipeline_roots)
    assert script is not None and script.is_file()
    assert "multistack" in str(script)            # vendored, not the monorepo
    src_dir = script.parents[2]                    # the runner's cwd for `-m`
    assert (src_dir / "pipeline" / "multi" / "run_pipeline.py").is_file()
    # the flux2 model lib the vendored stage_runner self-locates (REPO_ROOT/flux2/src)
    assert (src_dir.parent / "flux2" / "src" / "flux2" / "util.py").is_file()
    # _img2img is a transitive dep of multi (arch_batch imports pipeline._img2img) — vendored too
    assert (src_dir / "pipeline" / "_img2img" / "autodetect.py").is_file()


def test_vendored_multi_imports_as_module():
    """The exact thing the runner does: `python -m pipeline.multi.run_pipeline` with cwd = the
    vendored src dir. Guards the whole vendored import graph (this caught a missing `_img2img`
    that failed every cast with rc=1 'no multi manifest' — the build_argv/parse_result unit
    tests never actually imported the package). `-h` imports the module + exits 0, no GPU."""
    import subprocess
    import sys
    script = multi.resolve_script(CONFIG.pipeline_roots)
    if script is None or "multistack" not in str(script):
        pytest.skip("vendored multi not present")
    src_dir = script.parents[2]
    r = subprocess.run([sys.executable, "-m", "pipeline.multi.run_pipeline", "-h"],
                       cwd=str(src_dir), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"vendored multi failed to import:\n{r.stderr[-1500:]}"
    assert "ideate" in r.stdout      # the casting subcommand is wired


def test_fp8_workaround_mirrors_vendored_flux2():
    """`components._needs_fp8_workaround` hand-mirrors flux2's `_load_text_encoder_safe`
    platform test (they live in separate trees, so they can drift). Bind them: extract the
    VENDORED flux2 `_needs_fp8_workaround` SOURCE and run it, asserting it agrees with ours.
    The function depends only on `os` + `torch`, so we exec just it (no heavy `flux2.util`
    import) — fast, and a logic change in the vendored copy still flips this. Skips only if
    torch is unavailable (then ours degrades to False anyway)."""
    s1 = (CONFIG.app_repo_root / "pipelines" / "multistack" / "src" / "pipeline"
          / "flux2" / "stage1_load_models.py")
    if not s1.is_file():
        pytest.skip("vendored flux2 stage1_load_models not present")
    torch = pytest.importorskip("torch")
    import ast
    import os
    tree = ast.parse(s1.read_text(encoding="utf-8"))
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "_needs_fp8_workaround"), None)
    assert fn is not None, "vendored flux2 no longer defines _needs_fp8_workaround — mirror is stale"
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(s1), "exec"),
         {"os": os, "torch": torch}, ns)
    assert ns["_needs_fp8_workaround"]() == components._needs_fp8_workaround()
