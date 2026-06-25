"""P2/M2 Z-Image LoRA trainer adapter.

This is the queue-facing contract for staged training jobs. It deliberately lives
beside the inference adapters, but uses a distinct pipeline id (`zimage_trainer`)
so training progress, resumability and manifests are not confused with image
generation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .base import CompletionRecord, JobSpec

PIPELINE = "zimage_trainer"
SUPPORTED_MODES = ("lora",)


def resolve_script(_roots) -> Path | None:
    """Resolve the committed trainer wrapper, independent of pipeline_roots.

    `pipeline_roots` point at inference workers (`pipelines/...`). The trainer
    runtime is vendored under app `trainers/`, so resolving relative to this
    adapter avoids adding trainer paths to the inference-root contract.
    """
    root = Path(__file__).resolve().parents[2]
    script = root / "trainers" / "loom_zimage_lora.py"
    return script if script.is_file() else None


def capabilities(_roots) -> dict:
    script = resolve_script(_roots)
    return {
        "pipeline": PIPELINE,
        "worker": str(script or ""),
        "modes": list(SUPPORTED_MODES),
        "params": [
            "config_path", "run_dir", "runtime_overlay", "trainer_root",
            "artifact_name", "expected_steps", "resume_strategy",
        ],
        "cancellable": True,
        "resumable": True,
        "progress": "step",
    }


def build_argv(spec: JobSpec, python_exe: str, script: Path) -> list[str]:
    if spec.mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported {PIPELINE} mode: {spec.mode!r}")
    p = spec.params
    required = ("config_path", "run_dir")
    missing = [k for k in required if not p.get(k)]
    if missing:
        raise ValueError(f"{PIPELINE} missing required params: {', '.join(missing)}")
    manifest = spec.output_dir / "zimage_lora_train_manifest.json"
    argv = [
        python_exe, str(script),
        "--config", str(p["config_path"]),
        "--run-dir", str(p["run_dir"]),
        "--manifest", str(manifest),
    ]
    if p.get("runtime_overlay"):
        argv += ["--runtime-overlay", str(p["runtime_overlay"])]
    if p.get("trainer_root"):
        argv += ["--trainer-root", str(p["trainer_root"])]
    if p.get("artifact_name"):
        argv += ["--artifact-name", str(p["artifact_name"])]
    return argv


_STEP_PATTERNS = (
    re.compile(r"\bstep\s+(\d+)\s*/\s*(\d+)\b", re.I),
    re.compile(r"\bsteps?\s*[:=]\s*(\d+)\s*/\s*(\d+)\b", re.I),
)


def progress(line: str) -> float | None:
    s = line.strip()
    if "[train-preflight]" in s:
        return 0.05
    if "[train-resume]" in s:
        return 0.08
    for pat in _STEP_PATTERNS:
        m = pat.search(s)
        if m:
            done = max(0, int(m.group(1)))
            total = max(1, int(m.group(2)))
            return min(0.95, max(0.1, done / total))
    if "[train-done]" in s:
        return 1.0
    return None


def parse_result(returncode: int, stdout: str, _stderr: str, output_dir: Path) -> CompletionRecord:
    manifest = output_dir / "zimage_lora_train_manifest.json"
    manifest_status = None
    error = None
    duration_s = None
    outputs: list[str] = []
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            manifest_status = data.get("status")
            duration_s = data.get("duration_s")
            artifact = data.get("artifact")
            if isinstance(artifact, dict) and artifact.get("path"):
                ap = Path(artifact["path"])
                if ap.is_file():
                    outputs.append(str(ap))
            error = data.get("error")
        except (json.JSONDecodeError, OSError) as e:
            error = f"manifest unreadable: {e}"
    else:
        error = "no trainer manifest produced"

    ok = returncode == 0 and manifest_status == "completed" and bool(outputs)
    if not ok and error is None:
        if returncode != 0:
            error = f"trainer exited {returncode}"
        elif manifest_status != "completed":
            error = f"trainer manifest status {manifest_status!r}"
        else:
            error = "trainer completed but no artifact was found"

    tail = "\n".join(stdout.splitlines()[-60:])
    return CompletionRecord(
        ok=ok,
        returncode=returncode,
        outputs=outputs,
        manifest_path=str(manifest) if manifest.is_file() else None,
        duration_s=duration_s,
        stderr_tail=tail,
        manifest_status=manifest_status,
        error=error,
    )
