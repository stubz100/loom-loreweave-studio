"""M2.5 — quantized `flux.2-dev` swap + gated-repo elimination — no-GPU invariants.

Locks the wiring the owed on-rig dev smoke depends on, and the elimination of the gated
heavyweight repos (`black-forest-labs/FLUX.2-dev`, `mistralai/Mistral-Small-*`):

- dev emits `--text-encoder`/`--fp8-matmul`; Klein never does; dev size default stays 512².
- the quantized dev backend is wired (catalog `repo_id` = Comfy; manifest carries a `quantized`
  field; the worker guards dev out of the batch `ref` sweep).
- NO active data structure (models.json / catalog / flux2.util registry) references either gated
  repo; the dev flow-model + text-encoder registry fns are guarded.
- Klein's VAE re-points to the public Comfy `flux2-vae.safetensors`: it remaps onto the BFL
  AutoEncoder key-for-key and strict-loads (gated on the file being cached locally).

Run from the loom root: `python -m pytest orchestrator/tests/test_flux2_dev_quantized.py -q`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from orchestrator import model_catalog as mc

# --- make the vendored flux2 lib + pipeline importable in-process (workers are normally
#     subprocess-invoked, so nothing puts them on sys.path) ----------------------------
_LOOM = Path(__file__).resolve().parents[2]
_MULTISTACK = _LOOM / "pipelines" / "multistack"
for _p in (_MULTISTACK / "src", _MULTISTACK / "flux2" / "src"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

GATED_REPOS = ("black-forest-labs/FLUX.2-dev", "mistralai/Mistral-Small")
DEV = "flux.2-dev"
COMFY_REPO = "Comfy-Org/flux2-dev"
COMFY_VAE_FILE = "split_files/vae/flux2-vae.safetensors"


# --- catalog argv + size (the dry-run surface the orchestrator emits) ------------------

def test_dev_emits_quantized_knobs_klein_does_not():
    dev = mc.emit_argv("flux2", {"model_name": DEV, "width": 512, "height": 512,
                                  "text_encoder": "fp8", "fp8_matmul": "auto"}, "t2i")
    assert "--text-encoder" in dev and "fp8" in dev
    assert "--fp8-matmul" in dev and "auto" in dev
    klein = mc.emit_argv("flux2", {"model_name": "flux.2-klein-4b", "text_encoder": "fp8",
                                   "fp8_matmul": "auto", "width": 1360, "height": 768}, "t2i")
    # Klein never carries the dev knobs (UI-gated); even if present, they aren't catalog-emitted
    assert "--text-encoder" not in klein and "--fp8-matmul" not in klein


def test_dev_size_default_stays_512():
    assert mc.model_size_default("flux2", DEV) == (512, 512)
    # Klein has no per-variant size override → pipeline default applies
    assert mc.model_size_default("flux2", "flux.2-klein-4b") == (None, None)


def test_dev_variant_points_at_comfy_backend():
    dev = next(v for v in mc.CATALOG["flux2"]["variants"] if v["id"] == DEV)
    assert dev["repo_id"] == COMFY_REPO and dev["ae_repo_id"] == COMFY_REPO
    assert dev["gated"] is False  # public Comfy mirror


def test_dev_quantized_defaults_use_stable_low_step_profile():
    """The Comfy q8 dev path was proven at 8 steps; 50-step full-dev defaults can NaN to black."""
    dev = next(v for v in mc.CATALOG["flux2"]["variants"] if v["id"] == DEV)
    assert dev["defaults"]["num_steps"] == 8
    assert dev["defaults"]["guidance"] == 4.0
    preset = next(p for p in mc.flux2_sampling_presets() if p["id"] == "dev")
    assert preset["num_steps"] == 8 and preset["guidance"] == 4.0

    from flux2 import util
    assert util.FLUX2_MODEL_INFO[DEV]["defaults"] == {"guidance": 4.0, "num_steps": 8}
    assert util.FLUX2_MODEL_INFO["flux.2-klein-base-4b"]["defaults"]["num_steps"] == 50
    assert util.FLUX2_MODEL_INFO["flux.2-klein-base-9b"]["defaults"]["num_steps"] == 50


# --- weight pre-flight gate: dev probes the Comfy split files, NOT model_index.json ----

def test_dev_weight_gate_probes_comfy_split_files(monkeypatch):
    """The Comfy repo has no model_index.json — the dev gate must probe its split_files/… so a
    cached rig isn't falsely told 'not in cache' (user-reported 2026-06-26)."""
    from orchestrator import components
    dev = next(v for v in mc.CATALOG["flux2"]["variants"] if v["id"] == DEV)
    assert dev.get("probe_files") and all(f.startswith("split_files/") for f in dev["probe_files"])
    seen: list[str] = []
    monkeypatch.setattr(components, "_hf_cache_probe", lambda repo, f: (seen.append(f) or True))
    assert components.variant_weights_present(dev) is True
    assert "model_index.json" not in seen
    assert any("flux2_dev_fp8mixed" in f for f in seen)


def test_klein_weight_gate_still_uses_model_index(monkeypatch):
    from orchestrator import components
    klein = next(v for v in mc.CATALOG["flux2"]["variants"] if v["id"] == "flux.2-klein-4b")
    assert not klein.get("probe_files")
    seen: list[str] = []
    monkeypatch.setattr(components, "_hf_cache_probe", lambda repo, f: (seen.append(f) or True))
    components.variant_weights_present(klein)
    assert seen == ["model_index.json"]


def test_dev_gate_fails_when_a_split_file_missing(monkeypatch):
    from orchestrator import components
    dev = next(v for v in mc.CATALOG["flux2"]["variants"] if v["id"] == DEV)
    # transformer present, TE missing -> overall missing (all() must be False)
    monkeypatch.setattr(components, "_hf_cache_probe",
                        lambda repo, f: "fp8mixed" in f)
    assert components.variant_weights_present(dev) is False


# --- gated-repo elimination (structured, not a comment grep) ----------------------------

def _repo_strings_in(obj):
    """Yield every string value under a *-repo_id / repo_id / text_encoder key, recursively."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and (k.endswith("repo_id") or k == "text_encoder"):
                yield v
            else:
                yield from _repo_strings_in(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _repo_strings_in(v)


def test_models_json_has_no_gated_repos():
    data = json.loads((_LOOM / "models.json").read_text(encoding="utf-8"))
    refs = [r for r in _repo_strings_in(data) if any(g in r for g in GATED_REPOS)]
    assert refs == [], f"models.json still resolves gated repos: {refs}"


def test_catalog_flux2_has_no_gated_repos():
    refs = [r for r in _repo_strings_in(mc.CATALOG["flux2"]) if any(g in r for g in GATED_REPOS)]
    assert refs == [], f"flux2 catalog still references gated repos: {refs}"


def test_flux2_util_registry_has_no_gated_repos():
    from flux2 import util
    bad = []
    for name, cfg in util.FLUX2_MODEL_INFO.items():
        for key in ("repo_id", "ae_repo_id", "filename", "filename_ae"):
            val = str(cfg.get(key, ""))
            if any(g in val for g in GATED_REPOS):
                bad.append((name, key, val))
    assert bad == [], f"FLUX2_MODEL_INFO still references gated repos: {bad}"


def test_klein_ae_repo_is_comfy():
    from flux2 import util
    for name in ("flux.2-klein-4b", "flux.2-klein-9b", "flux.2-klein-base-9b"):
        assert util.FLUX2_MODEL_INFO[name]["ae_repo_id"] == COMFY_REPO
        assert util.FLUX2_MODEL_INFO[name]["filename_ae"] == COMFY_VAE_FILE


def test_dev_full_precision_loaders_are_guarded():
    """The full-precision flux2.util loaders must refuse dev (it's the quantized Comfy path)."""
    from flux2 import util
    with pytest.raises(RuntimeError, match="quantized"):
        util.load_flow_model(DEV)
    with pytest.raises(RuntimeError, match="quantized"):
        util.load_text_encoder(DEV)


# --- manifest lineage + batch guard ----------------------------------------------------

def test_manifest_has_quantized_field():
    from pipeline.flux2.manifest import PipelineManifest
    m = PipelineManifest(model_name=DEV, prompt="x", seed=1, width=512, height=512)
    assert m.quantized == {}  # empty for Klein/full-precision; populated only for quantized dev


def test_denoise_debug_and_guard_mark_nonfinite_latents():
    import torch
    from pipeline.flux2 import stage3_denoise

    result = {
        "x": torch.tensor([[float("nan"), 1.0, float("inf"), -2.0]], dtype=torch.float32),
        "timesteps": [1.0, 0.0],
    }
    debug = stage3_denoise.get_manifest_debug(result)
    assert debug["x_finite"] is False
    assert debug["x_finite_count"] == 2
    assert debug["x_total_count"] == 4
    assert debug["x_finite_ratio"] == 0.5
    assert debug["x_min"] == -2.0 and debug["x_max"] == 1.0
    with pytest.raises(FloatingPointError, match="non-finite latents"):
        stage3_denoise._ensure_finite_latents(result["x"], "denoise")


def test_batch_run_jobs_routes_dev_to_quantized(tmp_path, monkeypatch):
    """The expansion/coverage sweep now accepts dev (M2.5 batch): run_jobs routes it to the
    quantized Comfy loaders — NOT the old refusal, NOT the full-weight load_flow_model path."""
    from pipeline.flux2 import run_pipeline, scaled_fp8
    sentinel = RuntimeError("SENTINEL quantized loader reached")

    def _boom(*a, **k):
        raise sentinel
    # intercept at the first quantized call so we don't actually download/load weights
    monkeypatch.setattr(scaled_fp8, "resolve_hf_file", _boom)
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({
        "shared": {"mode": "ref", "model_name": DEV, "width": 512, "height": 512,
                   "text_encoder": "fp8", "fp8_matmul": "auto"},
        "items": [{"prompt": "front view, a ranger", "seed": 1}],
    }), encoding="utf-8")
    rc = run_pipeline.run_jobs(str(jobs), output_dir=str(tmp_path))
    assert rc == 2  # failed at the (intercepted) quantized load — i.e. dev WAS routed there
    summ = sorted(tmp_path.glob("flux2_batch_*.json"))[-1]
    data = json.loads(summ.read_text(encoding="utf-8"))
    assert data["backend_variant"] == "comfy-q8"      # quantized-dev lineage on the batch summary
    assert "SENTINEL" in (data.get("error") or "")     # reached the quantized loader, not a guard


def test_batch_shared_block_carries_dev_knobs(tmp_path):
    """A flux.2-dev coverage sweep writes the quantized knobs into the jobs.json shared block so
    the worker applies them once for the whole batch."""
    from orchestrator.adapters import flux2
    from orchestrator.adapters.base import JobSpec
    out = tmp_path / "sweep"
    out.mkdir()
    items = [{"prompt": "front view, a ranger", "seed": 1, "meta": {}}]
    spec = JobSpec(pipeline="flux2", mode="ref", params={
        "model_name": DEV, "width": 512, "height": 512,
        "ref_images": [str(tmp_path / "hero.png")], "batch_items": items,
        "text_encoder": "fp8", "fp8_matmul": "auto"}, output_dir=out)
    argv = flux2.build_argv(spec, "python", flux2.resolve_script([]))
    assert "--jobs-file" in argv
    shared = json.loads((out / "jobs.json").read_text(encoding="utf-8"))["shared"]
    assert shared["model_name"] == DEV
    assert shared["text_encoder"] == "fp8" and shared["fp8_matmul"] == "auto"


def test_dev_loaders_use_float32_vae_bf16_transformer(monkeypatch):
    """Regression: the dev VAE must load in float32 (Klein parity) so ref/i2i encode of a float32
    image works (decode is fine — inv_normalize promotes the bf16 latent). A bf16 VAE broke
    encode_image_refs ('Input type (float) and bias type (BFloat16)'). Transformer + TE stay bf16."""
    import torch
    from pipeline.flux2 import stage1_load_models as s1
    from pipeline.flux2 import scaled_fp8 as q
    seen: dict[str, object] = {}
    monkeypatch.setattr(q, "resolve_hf_file", lambda repo, fn, **k: fn)
    monkeypatch.setattr(q, "load_comfy_mistral_text_encoder",
                        lambda p, device, dtype, **k: (seen.update(te=dtype), (object(), object(), {}))[1])
    monkeypatch.setattr(q, "load_comfy_flux2_transformer",
                        lambda p, device, dtype, **k: (seen.update(tr=dtype), (object(), {}))[1])
    monkeypatch.setattr(q, "load_comfy_vae",
                        lambda p, device, dtype, **k: (seen.update(vae=dtype), (object(), {}))[1])
    s1._load_dev_quantized("cpu", cpu_offload=False, dtype="bfloat16",
                           local_files_only=True, fp8_matmul="auto", text_encoder_variant="fp8")
    assert seen["te"] == torch.bfloat16 and seen["tr"] == torch.bfloat16
    assert seen["vae"] == torch.float32  # the fix


# --- the two map_comfy_vae_key copies must not drift -----------------------------------

def test_vae_keymap_copies_agree():
    from flux2 import util as flux2_util
    from pipeline.flux2 import scaled_fp8
    sample_keys = [
        "decoder.conv_norm_out.weight",
        "decoder.up_blocks.0.resnets.1.conv1.weight",
        "encoder.down_blocks.2.downsamplers.0.conv.weight",
        "decoder.mid_block.attentions.0.to_q.weight",
        "decoder.mid_block.attentions.0.to_out.0.bias",
        "post_quant_conv.weight",
        "quant_conv.bias",
        "encoder.conv_in.weight",
        "bn.num_batches_tracked",
    ]
    for k in sample_keys:
        a_key, _ = flux2_util.map_comfy_vae_key(k)
        b_key, _ = scaled_fp8.map_comfy_vae_key(k)
        assert a_key == b_key, f"VAE key remap drift for {k!r}: util={a_key} scaled_fp8={b_key}"


# --- Klein VAE re-point: the Comfy VAE strict-loads into the BFL AutoEncoder ------------

def _comfy_vae_cached() -> str | None:
    try:
        from huggingface_hub import try_to_load_from_cache
        hit = try_to_load_from_cache(COMFY_REPO, COMFY_VAE_FILE)
        return hit if isinstance(hit, str) else None
    except Exception:
        return None


@pytest.mark.skipif(_comfy_vae_cached() is None,
                    reason="Comfy flux2-vae.safetensors not cached (no-weights rig)")
def test_comfy_vae_remaps_onto_bfl_autoencoder_strict():
    """The public Comfy VAE remaps key-for-key onto the BFL AutoEncoder and strict-loads — the
    structural guarantee behind Klein's VAE re-point (value equivalence proven once, journaled)."""
    import torch
    from safetensors.torch import load_file as load_sft
    from flux2.util import _is_comfy_vae_layout, _remap_comfy_vae_state_dict
    from flux2.autoencoder import AutoEncoder, AutoEncoderParams

    sd = load_sft(_comfy_vae_cached(), device="cpu")
    assert _is_comfy_vae_layout(sd)
    sd = _remap_comfy_vae_state_dict(sd)
    with torch.device("meta"):
        ae = AutoEncoder(AutoEncoderParams())
    expected = set(ae.state_dict().keys())
    assert set(sd.keys()) == expected  # exact key-for-key bijection
    ae.load_state_dict(sd, strict=True, assign=True)  # the load load_ae() performs
