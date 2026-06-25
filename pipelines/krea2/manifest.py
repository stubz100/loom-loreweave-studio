"""Krea 2 pipeline manifest records inputs, outputs, timings, and runtime metadata."""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class StageRecord:
    name: str
    status: str = "pending"  # pending | running | completed | failed
    start_time: float = 0.0
    end_time: float = 0.0
    duration_s: float = 0.0
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    debug: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class PipelineManifest:
    model_name: str
    prompt: str
    seed: int
    width: int
    height: int
    created_at: str = ""
    pipeline_start: float = 0.0
    pipeline_end: float = 0.0
    pipeline_duration_s: float = 0.0
    output_path: str = ""
    device: str = "cuda"
    stages: list[StageRecord] = field(default_factory=list)
    run_id: str = ""
    artifacts: list[dict] = field(default_factory=list)

    # Krea 2 spike metadata from kb-krea2 "Manifest Extensions".
    hf_repo: str = ""
    diffusers_version: str | None = None
    diffusers_commit: str | None = None
    transformers_version: str | None = None
    torch_version: str | None = None
    rocm_version: str | None = None
    dtype: str = "bfloat16"
    cpu_offload: bool = True
    device_map: str | None = None
    quant_backend: str | None = None
    quant_dtype: str | None = None
    quant_skip_modules: list[str] = field(default_factory=list)
    num_inference_steps: int = 0
    guidance_scale: float = 0.0
    max_sequence_length: int = 512
    is_distilled: bool | None = None
    vae_scale_factor: int | None = None
    patch_size: int | None = None
    lora_paths: list[str] = field(default_factory=list)
    lora_hashes: list[str | None] = field(default_factory=list)
    peak_vram_mb: int | None = None

    def begin_stage(self, name: str, inputs: dict) -> StageRecord:
        rec = StageRecord(name=name, status="running", start_time=time.time(), inputs=inputs)
        self.stages.append(rec)
        return rec

    def end_stage(self, rec: StageRecord, outputs: dict, debug: dict | None = None) -> None:
        rec.end_time = time.time()
        rec.duration_s = round(rec.end_time - rec.start_time, 4)
        rec.status = "completed"
        rec.outputs = outputs
        if debug:
            rec.debug = debug

    def fail_stage(self, rec: StageRecord, error: str) -> None:
        rec.end_time = time.time()
        rec.duration_s = round(rec.end_time - rec.start_time, 4)
        rec.status = "failed"
        rec.error = error

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, default=str)

    @staticmethod
    def load(path: Path) -> "PipelineManifest":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        stages = [StageRecord(**s) for s in data.pop("stages", [])]
        return PipelineManifest(**data, stages=stages)
