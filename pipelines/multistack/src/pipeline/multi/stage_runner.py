"""Stage runner — thin subprocess wrappers around each per-model run_pipeline CLI.

Subprocess (rather than in-process import) is used for two reasons:

1. **VRAM isolation**: each pipeline gets a fresh process, so model memory is
   released back to the OS between invocations. Critical on 16 GB cards where
   multiple 8B-12B models cannot co-reside.
2. **Import-path uniformity**: the Flux2 orchestrator uses package-relative
   imports while SD 3.5 and Z-Image use bare imports relative to their package
   directory. Subprocess sidesteps the difference.

All wrappers take a target `output_dir` (typically a per-stage subdir of the
multi-pipeline `IntermediateStore`) and discover the produced PNG + JSON
manifest by globbing for `<seed>` in that dir after the subprocess returns.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# --- Project layout constants ------------------------------------------------

_THIS = Path(__file__).resolve()
SRC_DIR = _THIS.parents[2]                      # f:/.../src
REPO_ROOT = SRC_DIR.parent                      # f:/.../stubz-002-tripo-sf
FLUX2_PKG = SRC_DIR / "pipeline" / "flux2"
SD35_PKG = SRC_DIR / "pipeline" / "sd35"
ZIMAGE_PKG = SRC_DIR / "pipeline" / "zimage"
FLUX2_LIB_SRC = REPO_ROOT / "flux2" / "src"     # for `import flux2.util`


def _build_env_for(extra_paths: list[Path]) -> dict[str, str]:
    """Return a copy of os.environ with PYTHONPATH prepended with extra_paths."""
    env = os.environ.copy()
    sep = os.pathsep
    existing = env.get("PYTHONPATH", "")
    parts = [str(p) for p in extra_paths] + ([existing] if existing else [])
    env["PYTHONPATH"] = sep.join(parts)
    return env


def _find_latest_outputs(output_dir: Path, prefix: str, seed: int) -> tuple[Path, Path]:
    """Locate the PNG and JSON manifest produced by a per-pipeline run.

    Per-pipeline orchestrators write `{prefix}_<UTC-ts>_s<seed>.png` + `.json`.
    We pick the most recently modified PNG matching `{prefix}_*_s{seed}.png`.
    """
    pattern = f"{prefix}_*_s{seed}.png"
    candidates = sorted(output_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(
            f"No output PNG found in {output_dir} matching {pattern!r}. "
            "The per-pipeline subprocess may have failed before saving."
        )
    png = candidates[0]
    manifest = png.with_suffix(".json")
    if not manifest.exists():
        raise FileNotFoundError(f"Found {png} but no matching manifest {manifest}")
    return png, manifest


def _run_subprocess(cmd: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    """Run a subprocess; capture stderr, stream stdout. Returns (returncode, stderr)."""
    print(f"[stage_runner] $ {' '.join(cmd)}")
    print(f"[stage_runner]   cwd={cwd}")
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=sys.stdout,           # let per-pipeline progress print live
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",            # sub-runs emit UTF-8 (PYTHONIOENCODING); decode to match
        errors="replace",            # a stray byte must never fail the capture (cp1252 decode of
                                     # a UTF-8 stderr byte 0x8f raised → every candidate wrongly
                                     # marked failed even though its image saved fine)
    )
    return proc.returncode, proc.stderr or ""


# --- Public invokers ---------------------------------------------------------


def invoke_flux2(
    *,
    prompt: str,
    output_dir: Path,
    seed: int,
    model_name: str = "flux.2-klein-4b",
    width: int = 512,
    height: int = 512,
    num_steps: int | None = None,
    guidance: float | None = None,
    cpu_offload: bool = False,
    extra_args: list[str] | None = None,
) -> dict:
    """Run the Flux2 pipeline as a subprocess. Returns
    {output_path, manifest_path, duration_s, returncode, stderr}."""
    output_dir = Path(output_dir).resolve()  # absolute -- per-pipeline subprocess runs with a different cwd
    output_dir.mkdir(parents=True, exist_ok=True)
    env = _build_env_for([FLUX2_LIB_SRC, SRC_DIR])

    cmd: list[str] = [
        sys.executable, "-m", "pipeline.flux2.run_pipeline",
        "--prompt", prompt,
        "--model-name", model_name,
        "--width", str(width),
        "--height", str(height),
        "--seed", str(seed),
        "--output-dir", str(output_dir),
    ]
    if num_steps is not None:
        cmd += ["--num-steps", str(num_steps)]
    if guidance is not None:
        cmd += ["--guidance", str(guidance)]
    if cpu_offload:
        cmd.append("--cpu-offload")
    if extra_args:
        cmd += extra_args

    t0 = time.time()
    rc, err = _run_subprocess(cmd, cwd=SRC_DIR, env=env)
    duration = round(time.time() - t0, 4)
    if rc != 0:
        return {"output_path": "", "manifest_path": "", "duration_s": duration,
                "returncode": rc, "stderr": err}

    png, manifest = _find_latest_outputs(output_dir, prefix="flux2", seed=seed)
    return {"output_path": str(png), "manifest_path": str(manifest),
            "duration_s": duration, "returncode": 0, "stderr": err}


def invoke_sd35(
    *,
    prompt: str,
    output_dir: Path,
    seed: int,
    model_name: str = "sd3.5-medium",
    width: int = 1024,
    height: int = 1024,
    num_steps: int | None = None,
    guidance_scale: float | None = None,
    negative_prompt: str | None = None,
    cpu_offload: bool = True,
    drop_t5: bool = False,
    dtype: str = "bfloat16",
    extra_args: list[str] | None = None,
) -> dict:
    """Run the SD 3.5 pipeline as a subprocess.

    Note: sd35/run_pipeline.py uses bare imports so we invoke it as a script
    from inside its directory rather than via `python -m`."""
    output_dir = Path(output_dir).resolve()  # absolute -- per-pipeline subprocess runs with a different cwd
    output_dir.mkdir(parents=True, exist_ok=True)
    env = _build_env_for([SD35_PKG, SRC_DIR])

    cmd: list[str] = [
        sys.executable, str(SD35_PKG / "run_pipeline.py"),
        "--prompt", prompt,
        "--model-name", model_name,
        "--width", str(width),
        "--height", str(height),
        "--seed", str(seed),
        "--output-dir", str(output_dir),
        "--dtype", dtype,
    ]
    if num_steps is not None:
        cmd += ["--num-steps", str(num_steps)]
    if guidance_scale is not None:
        cmd += ["--guidance-scale", str(guidance_scale)]
    if negative_prompt is not None:
        cmd += ["--negative-prompt", negative_prompt]
    if not cpu_offload:
        cmd.append("--no-cpu-offload")
    if drop_t5:
        cmd.append("--drop-t5")
    if extra_args:
        cmd += extra_args

    t0 = time.time()
    rc, err = _run_subprocess(cmd, cwd=SD35_PKG, env=env)
    duration = round(time.time() - t0, 4)
    if rc != 0:
        return {"output_path": "", "manifest_path": "", "duration_s": duration,
                "returncode": rc, "stderr": err}

    png, manifest = _find_latest_outputs(output_dir, prefix="sd35", seed=seed)
    return {"output_path": str(png), "manifest_path": str(manifest),
            "duration_s": duration, "returncode": 0, "stderr": err}


def invoke_zimage(
    *,
    prompt: str,
    output_dir: Path,
    seed: int,
    model_name: str = "zimage-turbo",
    width: int = 1024,
    height: int = 1024,
    num_steps: int | None = None,
    guidance_scale: float | None = None,
    negative_prompt: str | None = None,
    cfg_normalization: bool = False,
    cpu_offload: bool = True,
    dtype: str = "bfloat16",
    attention_backend: str | None = None,
    extra_args: list[str] | None = None,
) -> dict:
    """Run the Z-Image pipeline as a subprocess.

    Note: zimage/run_pipeline.py uses bare imports so we invoke it as a script
    from inside its directory rather than via `python -m`."""
    output_dir = Path(output_dir).resolve()  # absolute -- per-pipeline subprocess runs with a different cwd
    output_dir.mkdir(parents=True, exist_ok=True)
    env = _build_env_for([ZIMAGE_PKG, SRC_DIR])

    cmd: list[str] = [
        sys.executable, str(ZIMAGE_PKG / "run_pipeline.py"),
        "--prompt", prompt,
        "--model-name", model_name,
        "--width", str(width),
        "--height", str(height),
        "--seed", str(seed),
        "--output-dir", str(output_dir),
        "--dtype", dtype,
    ]
    if num_steps is not None:
        cmd += ["--num-steps", str(num_steps)]
    if guidance_scale is not None:
        cmd += ["--guidance-scale", str(guidance_scale)]
    if negative_prompt is not None:
        cmd += ["--negative-prompt", negative_prompt]
    if cfg_normalization:
        cmd.append("--cfg-normalization")
    if not cpu_offload:
        cmd.append("--no-cpu-offload")
    if attention_backend is not None:
        cmd += ["--attention-backend", attention_backend]
    if extra_args:
        cmd += extra_args

    t0 = time.time()
    rc, err = _run_subprocess(cmd, cwd=ZIMAGE_PKG, env=env)
    duration = round(time.time() - t0, 4)
    if rc != 0:
        return {"output_path": "", "manifest_path": "", "duration_s": duration,
                "returncode": rc, "stderr": err}

    png, manifest = _find_latest_outputs(output_dir, prefix="zimage", seed=seed)
    return {"output_path": str(png), "manifest_path": str(manifest),
            "duration_s": duration, "returncode": 0, "stderr": err}


# --- Self-test ---------------------------------------------------------------


def _self_test(output_dir: Path, seed: int = 4242, pipelines: list[str] | None = None) -> int:
    """Run a tiny T2I invocation through each pipeline and report results."""
    pipelines = pipelines or ["flux2", "sd35", "zimage"]
    output_dir = Path(output_dir)
    output_dir = Path(output_dir).resolve()  # absolute -- per-pipeline subprocess runs with a different cwd
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[self-test] output_dir={output_dir}  seed={seed}  pipelines={pipelines}")

    prompt = "a red balloon"
    results: dict[str, dict] = {}

    if "flux2" in pipelines:
        sub = output_dir / "flux2"
        results["flux2"] = invoke_flux2(
            prompt=prompt, output_dir=sub, seed=seed, width=512, height=512,
        )
    if "sd35" in pipelines:
        sub = output_dir / "sd35"
        results["sd35"] = invoke_sd35(
            prompt=prompt, output_dir=sub, seed=seed,
            model_name="sd3.5-medium", width=512, height=512,
            num_steps=8,                # smoke-test speed
        )
    if "zimage" in pipelines:
        sub = output_dir / "zimage"
        results["zimage"] = invoke_zimage(
            prompt=prompt, output_dir=sub, seed=seed,
            model_name="zimage-turbo", width=512, height=512,
        )

    print("\n[self-test] summary")
    print("-" * 64)
    failed = 0
    for name, r in results.items():
        ok = r["returncode"] == 0 and r["output_path"]
        status = "OK " if ok else "FAIL"
        print(f"  {status}  {name:8s} rc={r['returncode']}  duration={r['duration_s']}s")
        if r["output_path"]:
            print(f"           image    = {r['output_path']}")
            print(f"           manifest = {r['manifest_path']}")
        if not ok:
            failed += 1
            if r["stderr"]:
                print(f"           stderr (last 500 chars): ...{r['stderr'][-500:]}")
    print("-" * 64)
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-pipeline stage runner")
    parser.add_argument("--self-test", action="store_true",
                        help="Run a tiny T2I invocation through each pipeline")
    parser.add_argument("--output-dir", default="src/assets/pics/intermediate/_self_test")
    parser.add_argument("--seed", type=int, default=4242)
    parser.add_argument("--pipeline", action="append", default=None,
                        choices=["flux2", "sd35", "zimage"],
                        help="Restrict self-test to a single pipeline (repeatable)")
    args = parser.parse_args()
    if args.self_test:
        return _self_test(Path(args.output_dir), seed=args.seed, pipelines=args.pipeline)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
