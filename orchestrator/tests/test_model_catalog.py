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
