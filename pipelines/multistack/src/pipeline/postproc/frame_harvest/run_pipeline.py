"""Frame-harvest CLI (postproc tool; loom P1/M7) — extract stills from a video sketch.

The second half of the video-sketch ladder (R11/§4.1): the ltxv i2v sketch produces a
short MP4; this worker pulls **every k-th frame** (up to a cap) out as PNGs — the
multi-pose/angle reference candidates Stage-C curates. Pure OpenCV, CPU, no models.

Batch-shaped like the other loom workers — with one twist: ONE input video yields MANY
outputs, so the `frame_harvest_batch_<ts>.json` manifest writes **one item per saved
frame** (the manifest is the truth the orchestrator parses; the inputs file's item list
is just the work order). Each frame item echoes its source item's `meta` (the sketch's
target coverage_cell rides through to curation) + the frame number.

CLI:
  python run_pipeline.py --inputs-file <inputs.json> --output-dir <dir>
  python run_pipeline.py --input <video.mp4> --output-dir <dir>        # single-shot

inputs.json: {"every": 6, "max_frames": 24,
              "items": [{"input": <abs video>, "seed": 0, "meta": {…opaque…}}, …]}
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def run_batch(inputs_file: str, output_dir: str) -> int:
    import cv2

    spec = json.loads(Path(inputs_file).read_text(encoding="utf-8"))
    items = spec.get("items") or []
    every = max(1, int(spec.get("every", 6)))
    max_frames = max(1, int(spec.get("max_frames", 24)))
    if not items:
        print("[batch-error] inputs file has no items")
        return 2
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stop_file = out_dir / "STOP"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    total_planned = len(items) * max_frames
    print(f"[batch] {len(items)} video(s) | harvest every {every}th frame, "
          f"max {max_frames} per video")
    t0 = time.time()
    print(f"[stage1] Pipeline loaded in 0.0s (shared across {total_planned} items)")

    rows: list[dict] = []
    n_ok = n_fail = n_skip = 0
    stopped = False
    out_idx = 0
    for item in items:
        if stopped:
            break
        seed = item.get("seed", 0)
        base_meta = dict(item.get("meta") or {})
        in_path = item.get("input")
        try:
            if not in_path or not Path(in_path).is_file():
                raise FileNotFoundError(f"input not found: {in_path}")
            cap = cv2.VideoCapture(str(in_path))
            if not cap.isOpened():
                raise ValueError(f"unreadable video: {in_path}")
            saved = 0
            frame_no = -1
            while saved < max_frames:
                if stop_file.is_file():
                    print(f"[batch] STOP file found -- stopping after {saved} frame(s)")
                    stopped = True
                    n_skip += max_frames - saved
                    break
                ok, frame = cap.read()
                if not ok:
                    break
                frame_no += 1
                if frame_no % every:
                    continue
                out_path = out_dir / f"frame_harvest_{ts}_i{out_idx:03d}_s{seed}.png"
                cv2.imwrite(str(out_path), frame)
                meta = dict(base_meta)
                meta["frame"] = frame_no
                rows.append({"index": out_idx, "status": "ok", "seed": seed,
                             "prompt": item.get("prompt"),
                             "output_path": str(out_path), "manifest_path": "",
                             "meta": meta, "error": ""})
                n_ok += 1
                saved += 1
                out_idx += 1
                print(f"[item {out_idx}/{total_planned}] frame {frame_no}")
                print(f"  Image: {out_path}")
            cap.release()
            if saved == 0 and not stopped:
                raise ValueError("no frames harvested (empty/short video?)")
        except Exception as e:  # noqa: BLE001 - per-video isolation
            rows.append({"index": out_idx, "status": "failed", "seed": seed,
                         "prompt": item.get("prompt"),
                         "output_path": "", "manifest_path": "",
                         "meta": base_meta, "error": str(e)})
            n_fail += 1
            out_idx += 1
            print(f"[item {out_idx}/{total_planned}] FAILED: {e}")

    status = "stopped" if stopped else ("completed" if n_ok else "failed")
    manifest = {
        "kind": "jobs_batch", "pipeline": "frame_harvest", "mode": "harvest",
        "status": status, "count": len(rows) + n_skip,
        "ok": n_ok, "failed": n_fail, "skipped": n_skip,
        "every": every, "max_frames": max_frames,
        "total_duration_s": round(time.time() - t0, 2),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": rows,
    }
    mpath = out_dir / f"frame_harvest_batch_{ts}.json"
    mpath.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"[batch-done] {n_ok} ok / {n_fail} failed / {n_skip} skipped "
          f"in {manifest['total_duration_s']}s")
    print(f"  BatchManifest: {mpath}")
    return 0 if n_ok else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Harvest stills from a video sketch (every k-th frame, OpenCV CPU)")
    parser.add_argument("--inputs-file", help="batch inputs JSON")
    parser.add_argument("--input", help="video to harvest (single-shot mode)")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--every", type=int, default=6)
    parser.add_argument("--max-frames", type=int, default=24)
    parser.add_argument("--device", default="cpu", help="orchestrator symmetry; CPU only")
    args = parser.parse_args(argv)

    if args.inputs_file:
        return run_batch(args.inputs_file, args.output_dir)
    if not args.input:
        parser.error("either --inputs-file or --input is required")
    payload = {"every": args.every, "max_frames": args.max_frames,
               "items": [{"input": args.input, "seed": 0, "meta": {}}]}
    tmp = Path(args.output_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    f = tmp / "inputs.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    return run_batch(str(f), args.output_dir)


if __name__ == "__main__":
    sys.exit(main())
