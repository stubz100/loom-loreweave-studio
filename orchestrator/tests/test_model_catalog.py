"""Model catalog (P1/M3) — no GPU.

Locks the catalog the UI/generation read: every pipeline's variants + params are well-formed,
sd3.5-medium is present + ungated (the prototyping model the user added), and — the load-bearing
one — the variant lists **stay in lockstep with the vendored pipeline source** (a drift guard
that regex-extracts the *_MODEL_INFO keys, no heavy torch/flux2 import).
"""

from __future__ import annotations

import re

import pytest

from orchestrator import model_catalog as mc
from orchestrator.config import CONFIG

_MULTISTACK = CONFIG.app_repo_root / "pipelines" / "multistack"
# vendored source files whose *_MODEL_INFO keys are the authoritative variant list
_SOURCES = {
    "flux2": (_MULTISTACK / "flux2" / "src" / "flux2" / "util.py", r'"(flux\.2-[a-z0-9-]+)"\s*:\s*\{'),
    "sd35": (_MULTISTACK / "src" / "pipeline" / "sd35" / "stage1_load_pipeline.py", r'"(sd3\.5-[a-z0-9-]+)"\s*:\s*\{'),
    "zimage": (_MULTISTACK / "src" / "pipeline" / "zimage" / "stage1_load_pipeline.py", r'"(zimage-[a-z0-9]+)"\s*:\s*\{'),
    "birefnet": (_MULTISTACK / "src" / "pipeline" / "postproc" / "birefnet" / "run_pipeline.py",
                 r'"(birefnet(?:-[a-z0-9]+)?)"\s*:\s*\{'),
    "ltxv": (_MULTISTACK / "src" / "pipeline" / "ltxv" / "stage1_load_pipeline.py",
             r'"((?:2b|13b)_[0-9.]+(?:_[a-z]+)?)"\s*:\s*\{'),
}


def test_catalog_has_five_pipelines():
    # birefnet joined at M3.5 (postproc-class); ltxv at M7 (video sketch)
    assert set(mc.pipelines()) == {"flux2", "sd35", "zimage", "birefnet", "ltxv"}


def test_variants_well_formed():
    for p in mc.pipelines():
        assert mc.variants(p), f"{p} has no variants"
        for v in mc.variants(p):
            assert v["id"] and v["repo_id"] and isinstance(v["gated"], bool)
            assert isinstance(v.get("defaults", {}), dict)


def test_params_well_formed():
    for p in mc.pipelines():
        for prm in mc.params(p):
            if prm.get("post"):
                # clean/polish post-passes are orchestrator-chained — never a CLI flag
                assert prm["name"] and prm["flag"] is None and prm["type"]
                continue
            assert prm["name"] and prm["flag"].startswith("--") and prm["type"]


def test_sd35_medium_present_and_ungated():
    v = mc.find_variant("sd35", "sd3.5-medium")
    assert v is not None and v["gated"] is False
    assert v["repo_id"] == "stabilityai/stable-diffusion-3.5-medium"


def test_zimage_variants_ungated():
    assert all(v["gated"] is False for v in mc.variants("zimage"))


def test_validate_params_accepts_and_drops_none():
    out = mc.validate_params("sd35", "img2img", {"drop_t5": True, "dtype": "float16",
                                                 "max_sequence_length": 256, "prompt_3": None})
    assert out == {"drop_t5": True, "dtype": "float16", "max_sequence_length": 256}


def test_validate_params_rejects_unknown_type_range_mode():
    with pytest.raises(mc.CatalogError):           # unknown key
        mc.validate_params("zimage", "t2i", {"nope": 1})
    with pytest.raises(mc.CatalogError):           # wrong type (flag wants bool)
        mc.validate_params("sd35", "t2i", {"drop_t5": "yes"})
    with pytest.raises(mc.CatalogError):           # enum not in choices
        mc.validate_params("zimage", "t2i", {"dtype": "int4"})
    with pytest.raises(mc.CatalogError):           # out of range
        mc.validate_params("sd35", "t2i", {"max_sequence_length": 9999})
    with pytest.raises(mc.CatalogError):           # mode-gated param in wrong mode
        mc.validate_params("sd35", "t2i", {"controlnet": "depth"})


def test_emit_argv_maps_flags_and_respects_mode():
    argv = mc.emit_argv("zimage", {"cfg_normalization": True, "guidance_scale": 4.0,
                                   "init_image": "h.png", "strength": 0.5}, "img2img")
    assert "--cfg-normalization" in argv                       # flag → bare
    assert argv[argv.index("--guidance-scale") + 1] == "4.0"   # value flag
    assert argv[argv.index("--init-image") + 1] == "h.png"     # img2img mode → emitted
    # in t2i the img2img-gated params are skipped
    t2i = mc.emit_argv("zimage", {"init_image": "h.png", "strength": 0.5, "guidance_scale": 4.0}, "t2i")
    assert "--init-image" not in t2i and "--strength" not in t2i
    assert "--guidance-scale" in t2i


def test_emit_argv_skips_flag_when_false():
    assert "--cfg-normalization" not in mc.emit_argv("zimage", {"cfg_normalization": False}, "t2i")


def test_model_name_choices_filled_from_variants():
    specs = {p["name"]: p for p in mc.params("sd35")}
    assert specs["model_name"]["choices"] == mc.variant_ids("sd35")  # auto-synced
    # and an unknown model_name in the params channel is now rejected (enum has choices)
    with pytest.raises(mc.CatalogError):
        mc.validate_params("sd35", "img2img", {"model_name": "not-a-real-model"})


def test_validate_model_rejects_unknown_allows_known_and_none():
    assert mc.validate_model("zimage", None) is None                  # unset → caller default
    assert mc.validate_model("sd35", "sd3.5-medium")["id"] == "sd3.5-medium"
    with pytest.raises(mc.CatalogError):
        mc.validate_model("zimage", "bogus-model")


def test_flux2_sampling_presets_reference_real_variants():
    """M0d Part B: every Sampling preset maps to a real flux2 variant + sane steps/guidance,
    exactly one is the default, and it's served on the flux2 catalog entry."""
    presets = mc.flux2_sampling_presets()
    assert presets, "no flux2 sampling presets"
    ids = {p["id"] for p in presets}
    assert {"fast", "balanced", "quality"}.issubset(ids)   # the ≥3 researched presets
    flux2_variants = set(mc.variant_ids("flux2"))
    defaults = 0
    for p in presets:
        assert p["model_name"] in flux2_variants, f"{p['id']} → unknown variant {p['model_name']}"
        assert isinstance(p["num_steps"], int) and p["num_steps"] >= 1
        assert 0.0 <= float(p["guidance"]) <= 30.0
        defaults += 1 if p.get("default") else 0
    assert defaults == 1, "exactly one preset must be the default"
    # the default preset uses a distilled (fast) variant; the recommended one is non-distilled
    rec = next(p for p in presets if p.get("recommended"))
    rec_variant = mc.find_variant("flux2", rec["model_name"])
    assert rec_variant and rec_variant.get("distilled") is False
    # served to the UI on the flux2 entry
    assert mc.catalog_for_api()["flux2"]["sampling_presets"] == presets


def test_flux2_guidance_fixed_flag_is_accurate():
    """The Sampling guard keys on `guidance_fixed`, not `distilled`: only the step-distilled klein
    variants pin guidance (worker fixed_params). flux.2-dev IS distilled yet honours guidance
    (default 4.0) → must be guidance_fixed=False so the UI doesn't falsely warn; -base too."""
    fixed = {v["id"]: v.get("guidance_fixed") for v in mc.variants("flux2")}
    assert fixed["flux.2-klein-4b"] is True and fixed["flux.2-klein-9b"] is True
    assert fixed["flux.2-klein-9b-kv"] is True
    assert fixed["flux.2-klein-base-4b"] is False and fixed["flux.2-klein-base-9b"] is False
    assert fixed["flux.2-dev"] is False        # the bug: dev honours guidance, must not warn
    # every flux2 variant declares the flag
    assert all(isinstance(v.get("guidance_fixed"), bool) for v in mc.variants("flux2"))


def test_model_size_default_dev_is_512_others_none():
    """M0e Part A: flux.2-dev carries a per-variant 512² size default (it's far faster at low
    res on 16 GB ROCm); other flux2 models + sd35/zimage have no override → (None, None) so the
    caller falls back to the pipeline param default. Unknown/None model → (None, None)."""
    assert mc.model_size_default("flux2", "flux.2-dev") == (512, 512)
    assert mc.model_size_default("flux2", "flux.2-klein-4b") == (None, None)
    assert mc.model_size_default("flux2", "flux.2-klein-base-9b") == (None, None)
    assert mc.model_size_default("sd35", "sd3.5-medium") == (None, None)
    assert mc.model_size_default("zimage", "zimage-turbo") == (None, None)
    assert mc.model_size_default("flux2", None) == (None, None)
    assert mc.model_size_default("flux2", "bogus") == (None, None)
    # served on the catalog variant so the UI drawer can advertise it
    dev = mc.find_variant("flux2", "flux.2-dev")
    assert dev["defaults"]["width"] == 512 and dev["defaults"]["height"] == 512


def test_flux2_angle_directives_served_for_json_tree():
    """M0d Part C: the angle→pose directive vocab is served on the flux2 entry (single source
    with Part A's flux2_prompt.ANGLE_DIRECTIVES) so the dev JSON tree's pose presets don't drift."""
    from orchestrator import coverage, flux2_prompt
    served = mc.catalog_for_api()["flux2"]["angle_directives"]
    assert served == flux2_prompt.ANGLE_DIRECTIVES
    assert set(served) == set(coverage.ANGLES)   # frozen vocab coverage


@pytest.mark.parametrize("pipeline", ["flux2", "sd35", "zimage", "birefnet", "ltxv"])
def test_catalog_variants_match_vendored_source(pipeline):
    """Drift guard: the catalog's variant ids == the *_MODEL_INFO keys in the vendored worker
    source. If a pipeline adds/renames a model, this fails until the catalog is updated."""
    path, pattern = _SOURCES[pipeline]
    if not path.is_file():
        pytest.skip(f"vendored source missing: {path}")
    src_ids = set(re.findall(pattern, path.read_text(encoding="utf-8")))
    assert src_ids, f"no variant ids matched in {path}"
    assert set(mc.variant_ids(pipeline)) == src_ids, (
        f"catalog {pipeline} variants {set(mc.variant_ids(pipeline))} != source {src_ids}")
