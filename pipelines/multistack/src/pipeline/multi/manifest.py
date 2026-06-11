"""MultiPipelineManifest — composes per-pipeline manifests into a single record."""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class StageRecord:
    """Mirror of the per-pipeline StageRecord shape; sub-pipeline manifests are
    referenced by path in `outputs.sub_manifest_path` rather than nested dicts."""

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
class MultiPipelineManifest:
    architecture: str
    prompt: str
    seed: int
    created_at: str = ""
    pipeline_start: float = 0.0
    pipeline_end: float = 0.0
    pipeline_duration_s: float = 0.0
    output_path: str = ""
    intermediate_dir: str = ""
    keep_intermediates: bool = False
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
    def load(path: Path) -> "MultiPipelineManifest":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        stages = [StageRecord(**s) for s in data.pop("stages", [])]
        return MultiPipelineManifest(**data, stages=stages)
