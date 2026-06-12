"""Face-restore CLI (postproc tool; loom P1/M6) — GFPGAN 1.4 via ONNX.

Restores soft/degraded faces (AI mush, and especially the 128px softness the M4
identity swap leaves on close-ups): detect → align to the 512 canonical crop → GFPGAN →
feathered paste-back, blended with the original by `--blend` (1.0 = fully restored).

⚙ Deliberately **ONNX, not the gfpgan/basicsr pip packages** (those break on modern
torchvision — the M6 spike validated the `facefusion/models-3.0.0` mirror at ~0.3 s/face
on CPU). Detection/alignment reuse the insightface buffalo_l pack (same root as the
identity worker: `$LOOM_INSIGHTFACE_ROOT` > `<HF_HOME>/insightface`).

Batch-shaped like the other loom workers (one model load, loop items, STOP file,
`face_restore_batch_<ts>.json` jobs_batch manifest, `  Image:` per item) so the
orchestrator's streaming/⏹/partial-honesty machinery applies unchanged.

CLI:
  python run_pipeline.py --inputs-file <inputs.json> --output-dir <dir>
  python run_pipeline.py --input <img.png> --output-dir <dir>          # single-shot

inputs.json: {"blend": 0.8, "min_det_score": 0.5, "model_name": "gfpgan-1.4",
              "items": [{"input": <abs path>, "seed": 0, "meta": {…opaque…}}, …]}

Per item EVERY face ≥ min_det_score is restored; an image with no detectable face
passes through unchanged (meta.restore="no_face_passthrough").
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

_PKG_ROOT = Path(__file__).resolve().parents[2]   # …/src/pipeline (or a vendored mirror)
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

RESTORE_MODEL_INFO = {
    # GFPGAN v1.4 onnx (facefusion's model mirror; upstream GFPGAN is Apache-2.0).
    "gfpgan-1.4": {"repo_id": "facefusion/models-3.0.0", "filename": "gfpgan_1.4.onnx"},
}
_CANONICAL = 512    # GFPGAN works on 512×512 aligned face crops


def _insightface_root() -> str:
    env = os.environ.get("LOOM_INSIGHTFACE_ROOT")
    if env:
        return env
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return str(Path(hf_home) / "insightface")
    return str(Path.home() / ".insightface")


def _load_stack(model_name: str):
    if model_name not in RESTORE_MODEL_INFO:
        raise ValueError(f"unknown model_name {model_name!r}; "
                         f"one of {list(RESTORE_MODEL_INFO)}")
    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from insightface.app import FaceAnalysis
    info = RESTORE_MODEL_INFO[model_name]
    model_path = hf_hub_download(repo_id=info["repo_id"], filename=info["filename"])
    sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    app = FaceAnalysis(name="buffalo_l", root=_insightface_root(),
                       providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    return app, sess


def _restore_face(img, face, sess, blend: float):
    """Align → GFPGAN → blend → feathered inverse-affine paste-back. Returns the image
    with this face restored in place."""
    import cv2
    import numpy as np
    from insightface.utils import face_align

    est = face_align.estimate_norm(face.kps, _CANONICAL)
    M = est[0] if isinstance(est, tuple) else est
    crop = cv2.warpAffine(img, M, (_CANONICAL, _CANONICAL), borderValue=0)

    x = crop[:, :, ::-1].astype(np.float32) / 255.0      # BGR→RGB [0,1]
    x = ((x - 0.5) / 0.5).transpose(2, 0, 1)[None]       # [-1,1] NCHW
    inp = sess.get_inputs()[0]
    y = sess.run(None, {inp.name: x})[0][0]
    y = np.clip((y.transpose(1, 2, 0) + 1) / 2, 0, 1)
    restored = (y[:, :, ::-1] * 255).astype(np.float32)  # →BGR

    blended = blend * restored + (1.0 - blend) * crop.astype(np.float32)

    mask = np.full((_CANONICAL, _CANONICAL), 255, np.uint8)
    edge = 16
    mask[:edge, :] = 0
    mask[-edge:, :] = 0
    mask[:, :edge] = 0
    mask[:, -edge:] = 0
    mask = cv2.GaussianBlur(mask, (31, 31), 16).astype(np.float32) / 255.0

    h, w = img.shape[:2]
    inv = cv2.invertAffineTransform(M)
    back = cv2.warpAffine(blended, inv, (w, h), borderValue=0)
    mback = cv2.warpAffine(mask, inv, (w, h))[..., None]
    return (mback * back + (1.0 - mback) * img.astype(np.float32)).astype(np.uint8)


def run_batch(inputs_file: str, output_dir: str) -> int:
    import cv2

    spec = json.loads(Path(inputs_file).read_text(encoding="utf-8"))
    items = spec.get("items") or []
    blend = float(spec.get("blend", 0.8))
    min_det = float(spec.get("min_det_score", 0.5))
    model_name = spec.get("model_name") or "gfpgan-1.4"
    if not items:
        print("[batch-error] inputs file has no items")
        return 2
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stop_file = out_dir / "STOP"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"[batch] {len(items)} item(s) | face restore ({model_name}, blend {blend})")
    t0 = time.time()
    app, sess = _load_stack(model_name)
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
        out_path = out_dir / f"face_restore_{ts}_i{idx:03d}_s{seed}.png"
        t1 = time.time()
        try:
            in_path = item.get("input")
            if not in_path or not Path(in_path).is_file():
                raise FileNotFoundError(f"input not found: {in_path}")
            img = cv2.imread(in_path)
            if img is None:
                raise ValueError(f"unreadable image: {in_path}")
            faces = [f for f in app.get(img) if float(f.det_score) >= min_det]
            if not faces:
                shutil.copy2(in_path, out_path)
                meta["restore"] = "no_face_passthrough"
            else:
                for face in faces:
                    img = _restore_face(img, face, sess, blend)
                cv2.imwrite(str(out_path), img)
                meta["restore"] = "restored"
                meta["faces"] = len(faces)
            dt = round(time.time() - t1, 2)
            rows.append({"index": idx, "status": "ok", "seed": seed,
                         "prompt": item.get("prompt"),
                         "output_path": str(out_path), "manifest_path": "",
                         "meta": meta, "error": ""})
            n_ok += 1
            print(f"[item {idx + 1}/{len(items)}] done in {dt}s ({meta.get('restore')}"
                  + (f", {meta['faces']} face(s)" if "faces" in meta else "") + ")")
            print(f"  Image: {out_path}")
        except Exception as e:  # noqa: BLE001 - per-item isolation
            rows.append({"index": idx, "status": "failed", "seed": seed,
                         "prompt": item.get("prompt"),
                         "output_path": "", "manifest_path": "",
                         "meta": meta, "error": str(e)})
            n_fail += 1
            print(f"[item {idx + 1}/{len(items)}] FAILED: {e}")

    status = "stopped" if stopped else ("completed" if n_ok else "failed")
    manifest = {
        "kind": "jobs_batch", "pipeline": "face_restore", "mode": "restore",
        "status": status, "count": len(items),
        "ok": n_ok, "failed": n_fail, "skipped": n_skip,
        "model_name": model_name, "blend": blend, "min_det_score": min_det,
        "total_duration_s": round(time.time() - t0, 2),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": rows,
    }
    mpath = out_dir / f"face_restore_batch_{ts}.json"
    mpath.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"[batch-done] {n_ok} ok / {n_fail} failed / {n_skip} skipped "
          f"in {manifest['total_duration_s']}s")
    print(f"  BatchManifest: {mpath}")
    return 0 if n_ok else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="GFPGAN face restore (onnx, CPU): align -> restore -> paste back")
    parser.add_argument("--inputs-file", help="batch inputs JSON")
    parser.add_argument("--input", help="image to restore (single-shot mode)")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--blend", type=float, default=0.8)
    parser.add_argument("--min-det-score", type=float, default=0.5)
    parser.add_argument("--model-name", default="gfpgan-1.4",
                        choices=sorted(RESTORE_MODEL_INFO))
    parser.add_argument("--device", default="cpu", help="orchestrator symmetry; onnx CPU")
    args = parser.parse_args(argv)

    if args.inputs_file:
        return run_batch(args.inputs_file, args.output_dir)
    if not args.input:
        parser.error("either --inputs-file or --input is required")
    payload = {"blend": args.blend, "min_det_score": args.min_det_score,
               "model_name": args.model_name,
               "items": [{"input": args.input, "seed": 0, "meta": {}}]}
    tmp = Path(args.output_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    f = tmp / "inputs.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    return run_batch(str(f), args.output_dir)


if __name__ == "__main__":
    sys.exit(main())
