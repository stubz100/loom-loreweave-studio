"""P2 staged Z-Image LoRA training records.

M2's core promise is that training is proposed and persisted, not silently
queued. This module owns the durable `jobs/staged.json` store plus the first
Z-Image ai-toolkit job materialization:

- deterministic template captions from the frozen P1 coverage-cell contract;
- graph-ready `caption_policy.json` and `training_context.json`;
- temp dataset/config/run directories;
- staged → queued transition into `runner.submit(..., resumable=True)`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import assets
from . import coverage
from . import workspace as ws_mod
from .workspace import Workspace, new_id

STAGED_SCHEMA_VERSION = 1
CAPTION_POLICY_ID = "loom-template-caption-v1"
TRAINING_CONTEXT_KIND = "loom.p2.training_context.v1"

DEFAULT_ZIMAGE_SETTINGS: dict[str, Any] = {
    "base_model": "Tongyi-MAI/Z-Image",
    "model_name": "zimage-base",
    "steps": 500,
    "resolution": 512,
    "rank": 16,
    "alpha": 16,
    "batch_size": 1,
    "learning_rate": 0.0001,
    "optimizer": "adamw",
    "dtype": "bf16",
    "quantize": True,
    "qtype": "qfloat8",
    "low_vram": True,
    "gradient_checkpointing": True,
    "save_every": 50,
    "max_step_saves_to_keep": 2,
    "lora_weight_default": 1.0,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _staged_path(ws: Workspace) -> Path:
    return ws.jobs_dir / "staged.json"


def _empty_staged() -> dict:
    return {"schema_version": STAGED_SCHEMA_VERSION, "staged": {}}


def load_staged(ws: Workspace) -> dict:
    path = _staged_path(ws)
    if not path.is_file():
        return _empty_staged()
    data = ws_mod.read_json(path)
    if not isinstance(data, dict) or data.get("schema_version") != STAGED_SCHEMA_VERSION:
        raise ws_mod.WorkspaceError("staged.json has an unsupported schema_version")
    if not isinstance(data.get("staged"), dict):
        raise ws_mod.WorkspaceError("staged.json staged field must be an object")
    return data


def _persist_staged(ws: Workspace, data: dict) -> None:
    ws_mod.atomic_write_json(_staged_path(ws), data)


def list_staged(ws: Workspace) -> dict:
    data = load_staged(ws)
    rows = sorted(data["staged"].values(), key=lambda r: r.get("created_at", ""))
    return {"schema_version": STAGED_SCHEMA_VERSION, "count": len(rows), "staged": rows}


def delete_staged(ws: Workspace, staged_id: str) -> dict:
    data = load_staged(ws)
    if staged_id not in data["staged"]:
        raise ws_mod.WorkspaceError(f"staged job {staged_id!r} not found")
    record = data["staged"].pop(staged_id)
    _persist_staged(ws, data)
    return {"deleted": True, "staged_id": staged_id, "record": record}


def _trigger_from_profile(profile: dict) -> str:
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", (profile.get("slug") or profile.get("name") or "character").lower())
    base = re.sub(r"_+", "_", base).strip("_") or "character"
    if base[0].isdigit():
        base = f"char_{base}"
    return f"{base}_lw"


def _version_dir_for(ws: Workspace, asset_id: str, version_id: str | None) -> tuple[Path, dict, dict]:
    detail = assets.get_asset(ws, asset_id)
    profile = detail["profile"]
    vid = assets.resolve_version(ws, asset_id, version_id)
    found = assets._find_version(ws.asset_dir(profile["asset_class"], profile["slug"]), vid)  # noqa: SLF001
    if found is None:
        raise ws_mod.WorkspaceError(f"version {vid!r} is missing or unreadable on disk")
    return found[0], found[1], profile


def _write_captions(vdir: Path, version: dict, profile: dict, trigger_token: str,
                    *, base_family: str, settings: dict) -> dict:
    refs = version.get("ref_set") or []
    if not refs:
        raise ws_mod.WorkspaceError("cannot stage LoRA training: version ref_set is empty")

    policy = {
        "schema_version": 1,
        "id": CAPTION_POLICY_ID,
        "coverage_contract_version": coverage.CONTRACT_VERSION,
        "template": "<trigger>, <angle>, <shot-size>, <expression>[, <background> background]",
        "source_fields": list(coverage.AXES),
        "trigger_token": trigger_token,
        "omit_empty_background": True,
        "vlm": False,
        "created_at": _now(),
    }
    policy_bytes = json.dumps(policy, sort_keys=True).encode("utf-8")
    policy_hash = _sha256_bytes(policy_bytes)
    ws_mod.atomic_write_json(vdir / "caption_policy.json", policy)

    rows: list[dict[str, Any]] = []
    for ref in refs:
        cell = ref.get("coverage_cell") or {}
        caption = coverage.build_caption(cell, trigger_token)
        rows.append({
            "schema_version": 1,
            "id": ref["id"],
            "file": ref["file"],
            "caption": caption,
            "trigger_token": trigger_token,
            "coverage_cell": cell,
            "source_output": ref.get("source_output"),
            "source_job_id": ref.get("job_id"),
        })
    jsonl = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    captions_hash = _sha256_bytes(jsonl.encode("utf-8"))
    _atomic_write_text(vdir / "captions.jsonl", jsonl)

    context = {
        "schema_version": 1,
        "kind": TRAINING_CONTEXT_KIND,
        "asset_id": profile["id"],
        "asset_class": profile["asset_class"],
        "asset_name": profile["name"],
        "asset_slug": profile["slug"],
        "version_id": version["id"],
        "version_name": version["name"],
        "trigger_token": trigger_token,
        "base_family": base_family,
        "settings": settings,
        "refs": [
            {
                "id": r["id"],
                "file": r["file"],
                "coverage_cell": r.get("coverage_cell"),
                "source_output": r.get("source_output"),
                "job_id": r.get("job_id"),
                "seed": r.get("seed"),
            }
            for r in refs
        ],
        "caption_policy_hash": policy_hash,
        "captions_hash": captions_hash,
        "created_at": _now(),
    }
    context_bytes = json.dumps(context, sort_keys=True).encode("utf-8")
    context_digest = _sha256_bytes(context_bytes)
    context["context_digest"] = context_digest
    ws_mod.atomic_write_json(vdir / "training_context.json", context)

    version["caption_status"] = {
        "status": "ready",
        "caption_count": len(rows),
        "caption_policy_hash": policy_hash,
        "captions_hash": captions_hash,
        "updated_at": _now(),
    }
    version["training_context"] = {
        "file": "training_context.json",
        "context_digest": context_digest,
        "updated_at": _now(),
    }
    version["trigger_token"] = trigger_token
    assets._write_version(vdir, version)  # noqa: SLF001

    return {
        "captions": rows,
        "captions_path": str(vdir / "captions.jsonl"),
        "caption_policy_path": str(vdir / "caption_policy.json"),
        "training_context_path": str(vdir / "training_context.json"),
        "caption_policy_hash": policy_hash,
        "captions_hash": captions_hash,
        "context_digest": context_digest,
    }


def _prepare_dataset(vdir: Path, run_dir: Path, captions: list[dict]) -> dict:
    refs_dir = vdir / "refs"
    dataset_dir = run_dir / "dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for row in captions:
        src = refs_dir / row["file"]
        if not src.is_file():
            raise ws_mod.WorkspaceError(f"curated ref file missing: {src}")
        stem = Path(row["file"]).stem
        dst_img = dataset_dir / Path(row["file"]).name
        dst_txt = dataset_dir / f"{stem}.txt"
        shutil.copy2(src, dst_img)
        _atomic_write_text(dst_txt, row["caption"] + "\n")
        files.append({
            "ref_id": row["id"],
            "image": str(dst_img),
            "caption": str(dst_txt),
            "image_sha256": _sha256_file(dst_img),
        })
    manifest = {"schema_version": 1, "dataset_dir": str(dataset_dir), "count": len(files), "files": files}
    ws_mod.atomic_write_json(run_dir / "dataset_manifest.json", manifest)
    return manifest


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value))


def _write_aitk_config(config_path: Path, *, job_name: str, run_dir: Path, dataset_dir: Path,
                       trigger_token: str, settings: dict) -> None:
    s = {**DEFAULT_ZIMAGE_SETTINGS, **settings}
    text = f"""---
job: extension
config:
  name: {job_name}
  process:
    - type: diffusion_trainer
      training_folder: {_yaml_scalar(run_dir)}
      sqlite_db_path: {_yaml_scalar(run_dir / "aitk_db.sqlite")}
      device: cuda:0
      trigger_word: {trigger_token}
      performance_log_every: 10
      network:
        type: lora
        linear: {int(s["rank"])}
        linear_alpha: {int(s["alpha"])}
      save:
        dtype: {s["dtype"]}
        save_every: {int(s["save_every"])}
        max_step_saves_to_keep: {int(s["max_step_saves_to_keep"])}
        save_format: diffusers
        push_to_hub: false
      datasets:
        - folder_path: {_yaml_scalar(dataset_dir)}
          caption_ext: txt
          caption_dropout_rate: 0.0
          shuffle_tokens: false
          cache_latents_to_disk: true
          resolution: [{int(s["resolution"])}]
          num_repeats: 1
      train:
        batch_size: {int(s["batch_size"])}
        steps: {int(s["steps"])}
        gradient_accumulation: 1
        train_unet: true
        train_text_encoder: false
        gradient_checkpointing: {_yaml_scalar(bool(s["gradient_checkpointing"]))}
        noise_scheduler: flowmatch
        optimizer: {s["optimizer"]}
        optimizer_params:
          weight_decay: 0.0001
        lr: {float(s["learning_rate"])}
        timestep_type: weighted
        content_or_style: balanced
        unload_text_encoder: false
        cache_text_embeddings: false
        ema_config:
          use_ema: false
          ema_decay: 0.99
        skip_first_sample: true
        force_first_sample: false
        disable_sampling: true
        dtype: {s["dtype"]}
      logging:
        log_every: 1
        use_ui_logger: false
      model:
        name_or_path: {s["base_model"]}
        arch: zimage
        quantize: {_yaml_scalar(bool(s["quantize"]))}
        qtype: {s["qtype"]}
        quantize_te: {_yaml_scalar(bool(s["quantize"]))}
        qtype_te: {s["qtype"]}
        low_vram: {_yaml_scalar(bool(s["low_vram"]))}
        layer_offloading: false
        compile: false
      sample:
        sampler: flowmatch
        sample_every: 1000
        width: {int(s["resolution"])}
        height: {int(s["resolution"])}
        samples:
          - prompt: "{trigger_token}, front view, portrait, neutral expression"
        neg: ""
        seed: 42
        walk_seed: false
        guidance_scale: 4
        sample_steps: 30
meta:
  name: {job_name}
  version: "p2m2-zimage-default"
"""
    _atomic_write_text(config_path, text)


def stage_zimage_lora(ws: Workspace, asset_id: str, *, version_id: str | None = None,
                      trigger_token: str | None = None, runtime_overlay: str | None = None,
                      settings: dict | None = None) -> dict:
    vdir, version, profile = _version_dir_for(ws, asset_id, version_id)
    if version.get("finalized"):
        raise ws_mod.WorkspaceError("cannot stage LoRA training for a finalized version; unlock or duplicate it first")
    merged_settings = {**DEFAULT_ZIMAGE_SETTINGS, **(settings or {})}
    trigger = (trigger_token or version.get("trigger_token") or _trigger_from_profile(profile)).strip()
    if not re.match(r"^[A-Za-z][A-Za-z0-9_]{2,48}$", trigger):
        raise ws_mod.WorkspaceError("trigger_token must start with a letter and contain only letters, digits or underscores")

    caption_info = _write_captions(
        vdir, version, profile, trigger,
        base_family="zimage",
        settings=merged_settings,
    )
    staged_id = new_id("stg", 8)
    safe_version = re.sub(r"[^a-zA-Z0-9_]+", "_", version["name"]).strip("_") or version["id"]
    job_name = f"loom_{profile['slug'].replace('-', '_')}_{safe_version}_zimage"
    run_dir = ws.temp_dir / f"lora_{profile['slug']}_{version['id']}_{staged_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    dataset = _prepare_dataset(vdir, run_dir, caption_info["captions"])
    config_path = run_dir / "train.yaml"
    _write_aitk_config(
        config_path,
        job_name=job_name,
        run_dir=run_dir,
        dataset_dir=Path(dataset["dataset_dir"]),
        trigger_token=trigger,
        settings=merged_settings,
    )

    trainer_root = Path(__file__).resolve().parents[1] / "trainers" / "ai-toolkit"
    artifact_name = f"{job_name}.safetensors"
    params = {
        "config_path": str(config_path),
        "run_dir": str(run_dir),
        "trainer_root": str(trainer_root),
        "runtime_overlay": runtime_overlay,
        "artifact_name": artifact_name,
        "expected_steps": int(merged_settings["steps"]),
        "resume_strategy": "ai_toolkit_checkpoint_discovery",
        "runtime_contract": {
            "isolated_dependency_overlay": bool(runtime_overlay),
            "requires_peft_for_lora_inference": True,
            "do_not_mutate_shared_inference_venv": True,
            "minimal_zimage_extension": True,
        },
        "promotion": {
            "asset_id": profile["id"],
            "version_id": version["id"],
            "version_dir": str(vdir),
            "lora_dir": str(vdir / "lora"),
            "artifact_name": artifact_name,
        },
    }
    record = {
        "schema_version": STAGED_SCHEMA_VERSION,
        "id": staged_id,
        "kind": "zimage_lora_train",
        "status": "staged",
        "created_at": _now(),
        "asset_id": profile["id"],
        "asset_name": profile["name"],
        "version_id": version["id"],
        "version_name": version["name"],
        "trigger_token": trigger,
        "caption_count": len(caption_info["captions"]),
        "caption_policy_hash": caption_info["caption_policy_hash"],
        "captions_hash": caption_info["captions_hash"],
        "context_digest": caption_info["context_digest"],
        "dataset_manifest": str(run_dir / "dataset_manifest.json"),
        "config_path": str(config_path),
        "run_dir": str(run_dir),
        "settings": merged_settings,
        "queue_job": {
            "pipeline": "zimage_trainer",
            "mode": "lora",
            "params": params,
            "resumable": True,
            "stage": "D",
            "requester_id": profile["id"],
            "profile_version_id": version["id"],
        },
    }
    data = load_staged(ws)
    data["staged"][staged_id] = record
    _persist_staged(ws, data)
    return record


def queue_staged(ws: Workspace, staged_id: str, runner) -> dict:
    data = load_staged(ws)
    record = data["staged"].get(staged_id)
    if record is None:
        raise ws_mod.WorkspaceError(f"staged job {staged_id!r} not found")
    job = record["queue_job"]
    batch_id = "trn_" + staged_id.split("_", 1)[-1]
    job_id = runner.submit(
        pipeline=job["pipeline"],
        mode=job["mode"],
        params=job["params"],
        batch_id=batch_id,
        index=0,
        batch_size=1,
        requester_id=job.get("requester_id") or "training",
        profile_version_id=job.get("profile_version_id"),
        stage=job.get("stage") or "D",
        resumable=bool(job.get("resumable")),
    )
    data["staged"].pop(staged_id)
    _persist_staged(ws, data)
    return {"staged_id": staged_id, "queued": True, "job_id": job_id, "batch_id": batch_id}
