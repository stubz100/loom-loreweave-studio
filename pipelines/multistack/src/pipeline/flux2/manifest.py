"""Pipeline manifest — records inputs, outputs, timing, and debug info for each stage."""

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
    # --- multi-image-aware fields (kb-multi-image §Revision 2) ---
    # run_id: minted at run start by the orchestrator (format
    #   "run_<UTC-ts>_s<seed>"). Empty string for older manifests.
    # artifacts: list of artifact records (image/png + future kinds)
    #   produced during this run. Empty for older manifests.
    # Per-pipeline manifests intentionally carry NO multi-image lineage
    # fields (parent_run_id, source, etc.) -- those live only in the
    # session manifest at src/state/sessions/.
    run_id: str = ""
    artifacts: list[dict] = field(default_factory=list)
    # --- quantized-backend lineage (M2.5) ---
    # Populated only for the `flux.2-dev` quantized (Comfy-Org split-file) path:
    #   {backend_variant: "comfy-q8", hf_repo, transformer_file, text_encoder_file,
    #    text_encoder_variant, vae_file, fp8_matmul, dtype, cpu_offload}.
    # Empty {} for Klein / full-precision runs — distinguishes quantized-dev outputs in lineage
    # even though the user-facing model id stays `flux.2-dev`.
    quantized: dict = field(default_factory=dict)

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
