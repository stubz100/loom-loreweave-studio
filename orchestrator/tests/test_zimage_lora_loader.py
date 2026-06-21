"""Z-Image LoRA load contract — worker-level, no model/GPU required (P2/M1)."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


_STAGE1 = Path(__file__).resolve().parents[2] / "pipelines" / "zimage" / "stage1_load_pipeline.py"


def _load_stage1(monkeypatch):
    class FakePipe:
        def __init__(self):
            self.load_calls = []
            self.adapter_calls = []
            self.offloaded = False
            self.transformer = SimpleNamespace(set_attention_backend=lambda _value: None)

        def load_lora_weights(self, root, **kwargs):
            self.load_calls.append((root, kwargs))

        def set_adapters(self, name, adapter_weights):
            self.adapter_calls.append((name, adapter_weights))

        def enable_model_cpu_offload(self):
            self.offloaded = True

        def to(self, _device):
            pass

    class FakePipeline:
        calls = []

        @classmethod
        def from_pretrained(cls, repo_id, **kwargs):
            cls.calls.append((repo_id, kwargs))
            return FakePipe()

    torch = ModuleType("torch")
    torch.bfloat16 = "bf16"
    torch.float16 = "f16"
    diffusers = ModuleType("diffusers")
    diffusers.ZImagePipeline = FakePipeline
    diffusers.ZImageImg2ImgPipeline = FakePipeline
    diffusers.ZImageInpaintPipeline = FakePipeline
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "diffusers", diffusers)

    spec = importlib.util.spec_from_file_location("loom_test_zimage_stage1", _STAGE1)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, FakePipeline


def test_file_lora_is_loaded_named_weighted_and_hashed(monkeypatch, tmp_path):
    module, pipeline = _load_stage1(monkeypatch)
    weight = tmp_path / "char01.safetensors"
    weight.write_bytes(b"loom-lora-test")

    result = module.run(
        model_name="zimage-base", lora_path=str(weight), lora_name="char01_lw",
        lora_weight=0.85,
    )

    pipe = result["pipe"]
    assert pipeline.calls[0][0] == "Tongyi-MAI/Z-Image"
    assert pipe.load_calls == [(str(tmp_path), {
        "adapter_name": "char01_lw", "weight_name": "char01.safetensors",
    })]
    assert pipe.adapter_calls == [("char01_lw", 0.85)]
    assert pipe.offloaded is True
    assert result["lora"] == {
        "path": str(weight.resolve()), "name": "char01_lw", "weight": 0.85,
        "sha256": hashlib.sha256(b"loom-lora-test").hexdigest(),
    }


def test_missing_lora_fails_before_base_model_load(monkeypatch, tmp_path):
    module, pipeline = _load_stage1(monkeypatch)
    with pytest.raises(FileNotFoundError, match="LoRA path does not exist"):
        module.run(model_name="zimage-base", lora_path=str(tmp_path / "missing.safetensors"))
    assert pipeline.calls == []
