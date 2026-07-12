"""Resize CLI (postproc tool; loom P2, post-M2.11) — model-free Lanczos resample.

Pixel-faithful scaling with NO model in the loop. Every diffusion-based "resize"
re-renders its source (the tile-CN downscale that motivated this pass smeared lines
and shapes); a Lanczos resample is the correct tool when the pixels should survive
the size change — especially downscaling. Pure PIL, CPU, milliseconds per image.

Batch-shaped like the other loom workers (loop items, STOP file,
`resize_batch_<ts>.json` jobs_batch manifest, `  Image:` per item) so the
orchestrator's streaming/⏹/partial-honesty machinery applies unchanged.

CLI:
  python run_pipeline.py --inputs-file <inputs.json> --output-dir <dir>
  python run_pipeline.py --input <img.png> --width 512 --height 512 --output-dir <dir>

inputs.json: {"width": 512, "height": 512,
              "items": [{"input": <abs path>, "seed": 0, "meta": {…opaque…}}, …]}

Alpha survives (RGBA in → RGBA out; palette images are promoted to RGBA first).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _resample(in_path: str, out_path: Path, width: int, height: int) -> dict:
    """Lanczos-resample one image to width×height; returns meta facts."""
    from PIL import Image
    with Image.open(in_path) as img:
        src_w, src_h = img.size
        if img.mode == "P":            # palette → RGBA so the resample interpolates colors
            img = img.convert("RGBA")
        resized = img.resize((width, height), Image.LANCZOS)
        resized.save(out_path)
    return {"resize": f"{src_w}x{src_h}->{width}x{height}", "resample": "lanczos"}


def run_batch(inputs_file: str, output_dir: str) -> int:
    spec = json.loads(Path(inputs_file).read_text(encoding="utf-8"))
    items = spec.get("items") or []
    width, height = int(spec.get("width") or 0), int(spec.get("height") or 0)
    if not items:
        print("[batch-error] inputs file has no items")
        return 2
    if width <= 0 or height <= 0:
        print(f"[batch-error] bad target size {width}x{height}")
        return 2
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stop_file = out_dir / "STOP"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"[batch] {len(items)} item(s) | lanczos resize -> {width}x{height}")
    t0 = time.time()

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
        out_path = out_dir / f"resize_{ts}_i{idx:03d}_s{seed}.png"
        t1 = time.time()
        try:
            in_path = item.get("input")
            if not in_path or not Path(in_path).is_file():
                raise FileNotFoundError(f"input not found: {in_path}")
            meta.update(_resample(in_path, out_path, width, height))
            dt = round(time.time() - t1, 2)
            rows.append({"index": idx, "status": "ok", "seed": seed,
                         "prompt": item.get("prompt"),
                         "output_path": str(out_path), "manifest_path": "",
                         "meta": meta, "error": ""})
            n_ok += 1
            print(f"[item {idx + 1}/{len(items)}] done in {dt}s ({meta['resize']})")
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
        "kind": "jobs_batch", "pipeline": "resize", "mode": "resize",
        "status": status, "count": len(items),
        "ok": n_ok, "failed": n_fail, "skipped": n_skip,
        "width": width, "height": height,
        "total_duration_s": round(time.time() - t0, 2),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": rows,
    }
    mpath = out_dir / f"resize_batch_{ts}.json"
    mpath.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"[batch-done] {n_ok} ok / {n_fail} failed / {n_skip} skipped "
          f"in {manifest['total_duration_s']}s")
    print(f"  BatchManifest: {mpath}")
    return 0 if n_ok else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="model-free Lanczos resize (PIL, CPU): resample, never re-render")
    parser.add_argument("--inputs-file", help="batch inputs JSON")
    parser.add_argument("--input", help="image to resize (single-shot mode)")
    parser.add_argument("--width", type=int, help="target width (single-shot mode)")
    parser.add_argument("--height", type=int, help="target height (single-shot mode)")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--device", default="cpu", help="orchestrator symmetry; PIL CPU")
    args = parser.parse_args(argv)

    if args.inputs_file:
        return run_batch(args.inputs_file, args.output_dir)
    if not args.input or not args.width or not args.height:
        parser.error("either --inputs-file or --input with --width/--height is required")
    payload = {"width": args.width, "height": args.height,
               "items": [{"input": args.input, "seed": 0, "meta": {}}]}
    tmp = Path(args.output_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    f = tmp / "inputs.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    return run_batch(str(f), args.output_dir)


if __name__ == "__main__":
    sys.exit(main())
