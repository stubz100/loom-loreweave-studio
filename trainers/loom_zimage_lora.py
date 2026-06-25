"""Loom P2 Z-Image LoRA training wrapper.

This wrapper is intentionally thin: it runs the vendored ai-toolkit with a
generated config, but owns Loom-specific runtime hygiene and manifest writing:

- isolated dependency overlay via PYTHONPATH, never mutating the shared venv;
- `AI_TOOLKIT_MINIMAL_ZIMAGE=1` so the vendored snapshot loads the proven Z-Image path;
- pre-run checkpoint/artifact discovery so a resumed queue job records what it is
  resuming from rather than merely relying on `resumable=true`;
- a compact JSON manifest consumed by `orchestrator.adapters.zimage_trainer`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def discover_resume_state(run_dir: Path, artifact_name: str | None = None) -> dict[str, Any]:
    """Discover ai-toolkit checkpoint/resume state in a run directory.

    ai-toolkit owns the actual restore mechanics. Loom records the state it sees
    before launch so a resumed queued job is auditable: final adapter(s), optimizer
    checkpoint, sqlite state, and the newest plausible artifact.
    """
    run_dir = run_dir.resolve()
    state: dict[str, Any] = {
        "run_dir": str(run_dir),
        "exists": run_dir.exists(),
        "final_adapters": [],
        "step_adapters": [],
        "optimizer_pt": None,
        "sqlite_db": None,
        "latest_artifact": None,
    }
    if not run_dir.exists():
        return state

    safes = sorted(run_dir.rglob("*.safetensors"), key=lambda p: p.stat().st_mtime)
    final_adapters = []
    step_adapters = []
    for p in safes:
        rel = p.relative_to(run_dir).as_posix()
        entry = {
            "path": str(p),
            "relative_path": rel,
            "size": p.stat().st_size,
            "mtime": p.stat().st_mtime,
        }
        if artifact_name and p.name == artifact_name:
            final_adapters.append(entry)
        elif "step" in p.stem.lower() or p.parent.name.lower().startswith("checkpoint"):
            step_adapters.append(entry)
        else:
            final_adapters.append(entry)
    state["final_adapters"] = final_adapters
    state["step_adapters"] = step_adapters
    if safes:
        newest = safes[-1]
        state["latest_artifact"] = {
            "path": str(newest),
            "relative_path": newest.relative_to(run_dir).as_posix(),
            "size": newest.stat().st_size,
            "sha256": _sha256(newest),
        }

    optimizer = sorted(run_dir.rglob("optimizer.pt"), key=lambda p: p.stat().st_mtime)
    if optimizer:
        p = optimizer[-1]
        state["optimizer_pt"] = {
            "path": str(p),
            "relative_path": p.relative_to(run_dir).as_posix(),
            "size": p.stat().st_size,
        }

    sqlite = sorted(run_dir.rglob("*.sqlite"), key=lambda p: p.stat().st_mtime)
    if sqlite:
        p = sqlite[-1]
        state["sqlite_db"] = {
            "path": str(p),
            "relative_path": p.relative_to(run_dir).as_posix(),
            "size": p.stat().st_size,
        }
    return state


def _write_manifest(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _resolve_final_artifact(run_dir: Path, artifact_name: str | None) -> Path | None:
    if artifact_name:
        matches = sorted(run_dir.rglob(artifact_name), key=lambda p: p.stat().st_mtime)
        if matches:
            return matches[-1]
    safes = sorted(run_dir.rglob("*.safetensors"), key=lambda p: p.stat().st_mtime)
    return safes[-1] if safes else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run a Loom Z-Image LoRA ai-toolkit training job")
    ap.add_argument("--config", required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--runtime-overlay")
    ap.add_argument("--trainer-root")
    ap.add_argument("--artifact-name")
    args = ap.parse_args(argv)

    config = Path(args.config).resolve()
    run_dir = Path(args.run_dir).resolve()
    manifest_path = Path(args.manifest).resolve()
    trainer_root = Path(args.trainer_root).resolve() if args.trainer_root else Path(__file__).parent / "ai-toolkit"
    run_py = trainer_root / "run.py"
    t0 = time.time()
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "loom.zimage_lora_train",
        "status": "running",
        "started_at": _now(),
        "config_path": str(config),
        "run_dir": str(run_dir),
        "trainer_root": str(trainer_root),
        "runtime": {
            "python": sys.executable,
            "runtime_overlay": str(Path(args.runtime_overlay).resolve()) if args.runtime_overlay else None,
            "minimal_zimage": True,
        },
        "resume_state_before": discover_resume_state(run_dir, args.artifact_name),
    }
    _write_manifest(manifest_path, manifest)

    if not config.is_file():
        manifest.update({"status": "failed", "error": f"config not found: {config}", "finished_at": _now()})
        _write_manifest(manifest_path, manifest)
        print(f"[train-error] config not found: {config}", flush=True)
        return 2
    if not run_py.is_file():
        manifest.update({"status": "failed", "error": f"ai-toolkit run.py not found: {run_py}", "finished_at": _now()})
        _write_manifest(manifest_path, manifest)
        print(f"[train-error] ai-toolkit run.py not found: {run_py}", flush=True)
        return 2

    print(f"[train-preflight] config={config}", flush=True)
    resume_before = manifest["resume_state_before"]
    if resume_before.get("latest_artifact") or resume_before.get("optimizer_pt"):
        print("[train-resume] checkpoint/artifact state discovered before launch", flush=True)
    else:
        print("[train-resume] no prior checkpoint/artifact state discovered", flush=True)

    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "AI_TOOLKIT_MINIMAL_ZIMAGE": "1",
    }
    pythonpath = [str(trainer_root)]
    if args.runtime_overlay:
        pythonpath.insert(0, str(Path(args.runtime_overlay).resolve()))
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)

    cmd = [sys.executable, str(run_py), str(config)]
    proc = subprocess.run(
        cmd,
        cwd=str(trainer_root),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    duration = round(time.time() - t0, 2)
    artifact = _resolve_final_artifact(run_dir, args.artifact_name)
    if proc.returncode == 0 and artifact and artifact.is_file():
        manifest.update({
            "status": "completed",
            "finished_at": _now(),
            "duration_s": duration,
            "returncode": proc.returncode,
            "resume_state_after": discover_resume_state(run_dir, args.artifact_name),
            "artifact": {
                "path": str(artifact),
                "name": artifact.name,
                "size": artifact.stat().st_size,
                "sha256": _sha256(artifact),
            },
        })
        _write_manifest(manifest_path, manifest)
        print(f"[train-done] artifact={artifact}", flush=True)
        return 0

    error = f"ai-toolkit exited {proc.returncode}"
    if proc.returncode == 0 and artifact is None:
        error = "ai-toolkit exited 0 but no safetensors artifact was found"
    manifest.update({
        "status": "failed",
        "finished_at": _now(),
        "duration_s": duration,
        "returncode": proc.returncode,
        "resume_state_after": discover_resume_state(run_dir, args.artifact_name),
        "error": error,
    })
    _write_manifest(manifest_path, manifest)
    print(f"[train-error] {error}", flush=True)
    return proc.returncode or 3


if __name__ == "__main__":
    raise SystemExit(main())
