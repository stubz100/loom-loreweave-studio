"""LTX-Video 0.9.x pipeline manifest.

Records inputs, outputs, timing, and debug info for each stage. Matches the
shape of src/pipeline/hunyuan/manifest.py and src/pipeline/wan2/manifest.py so
the multi-image session manifest can consume LTXV runs the same way.

Per kb-ltx09.md Part 3 "Manifest schema".

Phase 1+2 scope: t2v and i2v fields. Keyframes / extend / control fields are
declared in the dataclass so the schema is forward-compatible with Phase 3+,
but they remain empty/default for Phase 1+2 runs.
"""

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
    # Core generation params
    prompt: str = ""
    negative_prompt: str = ""
    mode: str = "i2v"                 # "t2v" | "i2v" | "keyframes" | "extend" | "control"
    model_variant: str = "2b_0.9.7_distilled"  # key into LTXV_VARIANTS
    seed: int = 42
    width: int = 704
    height: int = 480
    num_frames: int = 121
    fps: int = 24
    num_inference_steps: int = 8      # 8 for distilled, 25-40 for dev
    guidance_scale: float = 3.0       # distilled: 1.0-3.5; dev: 3.0-5.0

    # I2V-only
    init_image: str = ""              # first-frame image path

    # Keyframes-only (Phase 3+, declared for forward-compat)
    keyframe_images: list[str] = field(default_factory=list)
    keyframe_indices: list[int] = field(default_factory=list)

    # Extend-only (Phase 3+)
    extend_source_video: str = ""
    extend_overlap_frames: int = 8

    # Control-only (13B 0.9.7+ Phase 5+)
    control_type: str = ""            # "pose" | "depth" | "canny" | ""
    control_video: str = ""           # path to control-signal video

    # Run metadata (same as zimage/wan2/hunyuan)
    created_at: str = ""
    pipeline_start: float = 0.0
    pipeline_end: float = 0.0
    pipeline_duration_s: float = 0.0
    output_path: str = ""
    working_dir: str = ""
    device: str = "cuda"
    run_id: str = ""                  # mint via _artifact_id.mint_run_id(seed)
    artifacts: list[dict] = field(default_factory=list)

    # Lineage — multiple upstream runs (keyframes/extend modes can reference many).
    # Phase 1+2 i2v populates with the source still's run_id when available; t2v
    # leaves it empty.
    upstream_run_ids: list[str] = field(default_factory=list)

    # ROCm / VRAM strategy used (debug)
    offload_strategy: str = "none"    # 2B fits without offload; 13B needs "model" or "sequential"
    vae_dtype: str = "bfloat16"
    attention_backend: str = "sdpa"   # "sdpa" | "flash_attn"

    stages: list[StageRecord] = field(default_factory=list)

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
