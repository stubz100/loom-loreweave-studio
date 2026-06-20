"""Identity-lock CLI (postproc tool; loom P1/M4) — ReActor-class face swap to an anchor.

Locks every input image's face to the **anchor face** (the per-version `anchor.png` a
character picked in the face-anchor sub-stage): detect → inswapper_128 swap → paste back.
Post-hoc and **model-agnostic** — works on any PNG regardless of which diffusion pipeline
made it — and CPU-cheap (~0.2 s/image via onnxruntime CPU; no GPU required).

Spike-validated 2026-06-11 (loom journal M4): ArcFace cosine to the anchor 0.105 → 0.870
after the swap on real casting candidates. ⚠ inswapper_128 weights are InsightFace
research/non-commercial (HF mirror `ezioruan/inswapper_128.onnx`) — `_license_gate.py`
posture applies. The buffalo_l detection/recognition pack auto-downloads on first use to
`$LOOM_INSIGHTFACE_ROOT` (default `<HF_HOME>/insightface`, else `~/.insightface`).

Batch-shaped like the zimage/sd35 `--jobs-file` workers (one model load, loop the items,
STOP file = graceful stop, `identity_batch_<ts>.json` summary, `  Image:` per item) so the
loom orchestrator's batch machinery (streaming tiles, ⏹, partial honesty, meta echo)
applies unchanged.

CLI:
  python run_pipeline.py --inputs-file <inputs.json> --output-dir <dir>
  python run_pipeline.py --anchor <face.png> --input <img.png> --output-dir <dir>   # single

inputs.json: {"anchor": <abs path>, "min_det_score": 0.5, "model_name": "inswapper-128",
              "items": [{"input": <abs path>, "seed": 0, "meta": {…opaque…}}, …]}

Per item: the largest face with det_score ≥ min_det_score is swapped to the anchor; an
image with **no detectable face passes through unchanged** (status ok,
meta.identity="no_face_passthrough") — correct for back views, where the face isn't
visible anyway, and keeps the dataset complete.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make the parent package importable when invoked as a script (postproc convention).
_PKG_ROOT = Path(__file__).resolve().parents[2]   # …/src/pipeline (or a vendored mirror)
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

IDENTITY_MODEL_INFO = {
    # ReActor-class swapper; 128px working resolution (pair with face-restore for extreme
    # close-ups — M6). Research/non-commercial license.
    "inswapper-128": {"repo_id": "ezioruan/inswapper_128.onnx",
                      "filename": "inswapper_128.onnx"},
}


def _insightface_root() -> str:
    env = os.environ.get("LOOM_INSIGHTFACE_ROOT")
    if env:
        return env
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return str(Path(hf_home) / "insightface")
    return str(Path.home() / ".insightface")


def _load_stack(model_name: str):
    """One model load shared across all items: detector+embedder pack + the swapper."""
    if model_name not in IDENTITY_MODEL_INFO:
        raise ValueError(f"unknown model_name {model_name!r}; "
                         f"one of {list(IDENTITY_MODEL_INFO)}")
    from huggingface_hub import hf_hub_download
    from insightface.app import FaceAnalysis
    from insightface import model_zoo
    info = IDENTITY_MODEL_INFO[model_name]
    swapper_path = hf_hub_download(repo_id=info["repo_id"], filename=info["filename"])
    app = FaceAnalysis(name="buffalo_l", root=_insightface_root(),
                       providers=["CPUExecutionProvider"])
    # Run the DETECTOR at the lenient anchor floor so app.get() also returns sub-0.5 faces;
    # per-use filtering (targets at min_det, the anchor leniently) happens in _best_face. With
    # insightface's default det_thresh=0.5 the detector would drop a stylized anchor face before
    # _best_face ever saw it.
    app.prepare(ctx_id=-1, det_thresh=_ANCHOR_DET_FLOOR, det_size=(640, 640))
    swapper = model_zoo.get_model(swapper_path, providers=["CPUExecutionProvider"])
    return app, swapper


# The author's chosen ANCHOR is accepted down to this lenient floor — stylized / 3-quarter /
# dramatically-lit character faces score low on SCRFD yet are clearly a face, and failing the
# anchor kills the WHOLE identity pass. Per-TARGET detection keeps the stricter min_det so we
# never swap onto a non-face. (Also the detector's own threshold, above.)
_ANCHOR_DET_FLOOR = 0.2


def _best_face(app, img, min_det_score: float):
    """The subject face: largest bbox among detections clearing the score floor."""
    faces = [f for f in app.get(img) if float(f.det_score) >= min_det_score]
    if not faces:
        return None
    return max(faces, key=lambda f: float((f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])))


def run_batch(inputs_file: str, output_dir: str) -> int:
    import cv2
    import numpy as np

    spec = json.loads(Path(inputs_file).read_text(encoding="utf-8"))
    anchor_path = spec.get("anchor")
    items = spec.get("items") or []
    min_det = float(spec.get("min_det_score", 0.5))
    model_name = spec.get("model_name") or "inswapper-128"
    if not anchor_path or not Path(anchor_path).is_file():
        print(f"[batch-error] anchor image not found: {anchor_path}")
        return 2
    if not items:
        print("[batch-error] inputs file has no items")
        return 2
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stop_file = out_dir / "STOP"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"[batch] {len(items)} item(s) | identity lock -> {Path(anchor_path).name} "
          f"| model={model_name}")
    t0 = time.time()
    app, swapper = _load_stack(model_name)
    anchor_img = cv2.imread(anchor_path)
    if anchor_img is None:
        print(f"[batch-error] anchor image unreadable: {anchor_path}")
        return 2
    anchor_face = _best_face(app, anchor_img, min_det)
    if anchor_face is None and min_det > _ANCHOR_DET_FLOOR:
        # The ANCHOR is the author's deliberately-chosen face — try HARDER than the per-target
        # floor before giving up (a faceless anchor kills the whole identity pass).
        anchor_face = _best_face(app, anchor_img, _ANCHOR_DET_FLOOR)
        if anchor_face is not None:
            print(f"[batch] anchor face accepted at lenient det>={_ANCHOR_DET_FLOOR} "
                  f"(score {float(anchor_face.det_score):.3f})")
    anchor_missing = anchor_face is None
    if anchor_missing:
        # DON'T fail the whole pass on a faceless anchor (it was killing the entire expansion):
        # pass every image through unchanged so the dataset stays complete, and warn that the
        # lock was skipped. Heavily-stylized character faces (chrome/neon/3-quarter) evade the
        # photoreal SCRFD detector even leniently — there's no "clearer" anchor for such a
        # character, so blocking would be a dead end. (A passthrough run locks nothing, so the
        # orchestrator does NOT mark the anchor verified.)
        print(f"[batch] WARNING: no face detected in the ANCHOR (even at det>={_ANCHOR_DET_FLOOR}) "
              "— identity lock SKIPPED; passing images through unchanged. Use a clearer/"
              "less-stylized anchor or derive a face portrait (✨) to enable the swap.")
    load_s = round(time.time() - t0, 2)
    print(f"[stage1] Pipeline loaded in {load_s}s (shared across {len(items)} items)")

    rows: list[dict] = []
    n_ok = n_fail = n_skip = 0
    stopped = False
    for idx, item in enumerate(items):
        if stop_file.is_file():
            print(f"[batch] STOP file found -- stopping before item {idx + 1}/{len(items)}")
            stopped = True
            for j in range(idx, len(items)):
                rows.append({"index": j, "status": "skipped",
                             "seed": items[j].get("seed", 0),
                             "prompt": items[j].get("prompt"),
                             "output_path": "", "manifest_path": "",
                             "meta": items[j].get("meta") or {}, "error": "stopped"})
                n_skip += 1
            break
        seed = item.get("seed", 0)
        meta = dict(item.get("meta") or {})
        out_path = out_dir / f"identity_{ts}_i{idx:03d}_s{seed}.png"
        t1 = time.time()
        try:
            in_path = item.get("input")
            if not in_path or not Path(in_path).is_file():
                raise FileNotFoundError(f"input not found: {in_path}")
            img = cv2.imread(in_path)
            if img is None:
                raise ValueError(f"unreadable image: {in_path}")
            face = None if anchor_missing else _best_face(app, img, min_det)
            if face is None:
                # No face to swap (back view, or the anchor itself had none) — pass through
                # unchanged so the dataset stays complete; honest marker in the echoed meta.
                shutil.copy2(in_path, out_path)
                meta["identity"] = ("anchor_no_face_passthrough" if anchor_missing
                                    else "no_face_passthrough")
            else:
                swapped = swapper.get(img, face, anchor_face, paste_back=True)
                cv2.imwrite(str(out_path), swapped)
                emb_ok = None
                sw_face = _best_face(app, swapped, min_det)
                if sw_face is not None:
                    emb_ok = float(np.dot(anchor_face.normed_embedding,
                                          sw_face.normed_embedding))
                meta["identity"] = "locked"
                meta["det_score"] = round(float(face.det_score), 3)
                if emb_ok is not None:
                    meta["anchor_cos"] = round(emb_ok, 3)   # P2 readiness reads this free
            dt = round(time.time() - t1, 2)
            rows.append({"index": idx, "status": "ok", "seed": seed,
                         "prompt": item.get("prompt"),
                         "output_path": str(out_path), "manifest_path": "",
                         "meta": meta, "error": ""})
            n_ok += 1
            print(f"[item {idx + 1}/{len(items)}] done in {dt}s "
                  f"({meta.get('identity')}"
                  + (f", cos {meta['anchor_cos']}" if "anchor_cos" in meta else "") + ")")
            print(f"  Image: {out_path}")
        except Exception as e:  # noqa: BLE001 - per-item isolation, keep looping
            rows.append({"index": idx, "status": "failed", "seed": seed,
                         "prompt": item.get("prompt"),
                         "output_path": "", "manifest_path": "",
                         "meta": meta, "error": str(e)})
            n_fail += 1
            print(f"[item {idx + 1}/{len(items)}] FAILED: {e}")

    status = "stopped" if stopped else ("completed" if n_ok else "failed")
    manifest = {
        "kind": "jobs_batch", "pipeline": "identity", "mode": "lock",
        "status": status, "count": len(items),
        "ok": n_ok, "failed": n_fail, "skipped": n_skip,
        "anchor": anchor_path, "model_name": model_name,
        "min_det_score": min_det,
        "total_duration_s": round(time.time() - t0, 2),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": rows,
    }
    mpath = out_dir / f"identity_batch_{ts}.json"
    mpath.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"[batch-done] {n_ok} ok / {n_fail} failed / {n_skip} skipped "
          f"in {manifest['total_duration_s']}s")
    print(f"  BatchManifest: {mpath}")
    return 0 if n_ok else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Identity lock: swap every input's face to the anchor face "
                    "(inswapper_128, CPU)")
    parser.add_argument("--inputs-file", help="batch inputs JSON (anchor + items)")
    parser.add_argument("--anchor", help="anchor face image (single-shot mode)")
    parser.add_argument("--input", help="image to lock (single-shot mode)")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--min-det-score", type=float, default=0.5)
    parser.add_argument("--model-name", default="inswapper-128",
                        choices=sorted(IDENTITY_MODEL_INFO))
    parser.add_argument("--device", default="cpu", help="accepted for orchestrator "
                        "symmetry; the stack runs on onnxruntime CPU")
    args = parser.parse_args(argv)

    if args.inputs_file:
        return run_batch(args.inputs_file, args.output_dir)
    if not (args.anchor and args.input):
        parser.error("either --inputs-file or BOTH --anchor and --input are required")
    # Single-shot convenience: wrap into a one-item batch (same outputs/manifest).
    payload = {"anchor": args.anchor, "min_det_score": args.min_det_score,
               "model_name": args.model_name,
               "items": [{"input": args.input, "seed": 0, "meta": {}}]}
    tmp = Path(args.output_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    f = tmp / "inputs.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    return run_batch(str(f), args.output_dir)


if __name__ == "__main__":
    sys.exit(main())
