"""CPU VAE decode contract — worker-level, no model/GPU required."""

from __future__ import annotations

from contextlib import nullcontext
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys


_STAGE2 = Path(__file__).resolve().parents[2] / "pipelines" / "zimage" / "stage2_generate.py"


def _load_stage2(monkeypatch):
    events: list[object] = []

    class FakeTensor:
        def detach(self):
            events.append("detach")
            return self

        def to(self, *args, **kwargs):
            events.append(("tensor.to", args, kwargs))
            return self

        def __truediv__(self, value):
            events.append(("scale", value))
            return self

        def __add__(self, value):
            events.append(("shift", value))
            return self

    class FakePILImage:
        width = 1024
        height = 1024
        mode = "RGB"
        size = (1024, 1024)

    class FakeVAE:
        def __init__(self):
            self.dtype = "bf16"
            self.config = SimpleNamespace(scaling_factor=0.3611, shift_factor=0.1159)
            self.use_tiling = True
            self.tile_sample_min_size = 512
            self.tile_latent_min_size = 64
            self.device = SimpleNamespace(type="cpu")
            self.to_calls = []
            self.decode_was_tiled = None

        def parameters(self):
            return iter([SimpleNamespace(device=self.device)])

        def to(self, device):
            self.to_calls.append(device)
            self.device = device if hasattr(device, "type") else SimpleNamespace(type=str(device))
            return self

        def disable_tiling(self):
            self.use_tiling = False

        def enable_tiling(self):
            self.use_tiling = True

        def decode(self, latents, return_dict=False):
            self.decode_was_tiled = self.use_tiling
            events.append(("decode", return_dict))
            return (latents,)

    class FakeImageProcessor:
        def postprocess(self, decoded, output_type):
            events.append(("postprocess", output_type))
            return [FakePILImage()]

    class FakePipeline:
        def __init__(self):
            self.vae = FakeVAE()
            self.image_processor = FakeImageProcessor()
            self._all_hooks = [object()]
            self.call_kwargs = None
            self.hooks_removed = False
            self.offload_restored = False

        def __call__(self, **kwargs):
            self.call_kwargs = kwargs
            kwargs["callback_on_step_end"](self, 0, None, {})
            return SimpleNamespace(images=FakeTensor())

        def remove_all_hooks(self):
            self.hooks_removed = True
            self._all_hooks = []

        def enable_model_cpu_offload(self, device=None):
            self.offload_restored = True
            self._all_hooks = [object()]

    class FakeGenerator:
        def __init__(self, device):
            self.device = device

        def manual_seed(self, seed):
            self.seed = seed
            return self

    cuda = SimpleNamespace(
        is_available=lambda: True,
        empty_cache=lambda: events.append("empty_cache"),
    )
    torch = ModuleType("torch")
    torch.Generator = FakeGenerator
    torch.inference_mode = nullcontext
    torch.cuda = cuda

    diffusers = ModuleType("diffusers")
    diffusers.ZImagePipeline = FakePipeline
    diffusers.ZImageImg2ImgPipeline = type("FakeImg2ImgPipeline", (), {})
    diffusers.ZImageInpaintPipeline = type("FakeInpaintPipeline", (), {})
    diffusers_utils = ModuleType("diffusers.utils")
    diffusers_utils.load_image = lambda path: path

    pil = ModuleType("PIL")
    pil.Image = SimpleNamespace(Image=FakePILImage)

    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "diffusers", diffusers)
    monkeypatch.setitem(sys.modules, "diffusers.utils", diffusers_utils)
    monkeypatch.setitem(sys.modules, "PIL", pil)

    spec = importlib.util.spec_from_file_location("loom_test_zimage_stage2_cpu_vae", _STAGE2)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, FakePipeline, events


def test_cpu_vae_requests_latents_decodes_on_cpu_and_restores_offload(monkeypatch):
    module, pipeline_cls, events = _load_stage2(monkeypatch)
    pipe = pipeline_cls()

    result = module.run(pipe=pipe, prompt="a hero", cpu_vae=True)

    assert pipe.call_kwargs["output_type"] == "latent"
    assert pipe.hooks_removed is True
    assert pipe.offload_restored is True
    assert pipe.vae.to_calls == ["cpu"]
    assert pipe.vae.decode_was_tiled is False
    assert pipe.vae.use_tiling is True
    assert pipe.vae.tile_sample_min_size == 512
    assert pipe.vae.tile_latent_min_size == 64
    assert "empty_cache" in events
    assert ("scale", 0.3611) in events
    assert ("shift", 0.1159) in events
    assert ("postprocess", "pil") in events
    assert result["cpu_vae"] is True
    assert result["width"] == 1024 and result["height"] == 1024


def test_default_path_frees_gpu_reserve_then_decodes_on_gpu_and_restores(monkeypatch):
    """Default (cpu_vae off) path: stop after denoise, drop offload hooks + empty_cache to hand
    the transformer's VRAM reserve back to the driver, then decode on the GPU — so MIOpen gets
    its conv workspace (the fix for the ~15-min ROCm decode) instead of the naive solver."""
    module, pipeline_cls, events = _load_stage2(monkeypatch)
    pipe = pipeline_cls()

    result = module.run(pipe=pipe, prompt="a hero")  # cpu_vae defaults to False

    assert pipe.call_kwargs["output_type"] == "latent"   # we control the decode, not the pipeline
    assert pipe.hooks_removed is True                     # offload hooks dropped so VAE stays put
    assert pipe.vae.to_calls == ["cuda"]                  # decode happens ON the GPU
    assert pipe.vae.decode_was_tiled is False             # full-frame (tiling is slow even at 15GB free)
    assert pipe.vae.use_tiling is True                    # tiling restored for reuse
    assert "empty_cache" in events                        # reserve returned to the driver first
    assert ("scale", 0.3611) in events                    # pipeline-equivalent scale/shift decode
    assert ("shift", 0.1159) in events
    assert ("postprocess", "pil") in events
    assert pipe.offload_restored is True                  # state restored for batch/warm reuse
    assert result["cpu_vae"] is False
    assert result["width"] == 1024 and result["height"] == 1024
