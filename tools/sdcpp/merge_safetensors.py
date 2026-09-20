"""Merge Hugging Face SHARDED safetensors into ONE file by byte copy — no torch, no dtype
decoding (bf16 / fp8 tensors are copied verbatim), seconds per 10 GB on NVMe.

Why (CPU/ggml spike, 2026-09-20): stable-diffusion.cpp wants each model as a single file,
while the HF cache holds the diffusers/transformers layouts in shards. For models whose key
layout the runtime already reads (the Qwen3-4B text encoder — the same HF layout Comfy's
`qwen_3_4b.safetensors` uses) this replaces an 8 GB download with a 17-second copy.
It does NOT convert key layouts: the diffusers Z-Image transformer merges fine but is refused
at load (split q/k/v vs the fused `attention.qkv` the runtime expects) — that one needs the
Comfy/original single file or a GGUF.

usage:  merge_safetensors.py <dir holding the shards (+ *.index.json)> <out.safetensors>
e.g.    merge_safetensors.py F:/HF_HOME/hub/models--Qwen--Qwen3-4B/snapshots/<rev> qwen_3_4b.safetensors

safetensors layout: u64 header length | JSON header {name: {dtype, shape, data_offsets}} | data.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time
from pathlib import Path


def merge(src_dir: Path, out: Path) -> None:
    idx = list(src_dir.glob("*.safetensors.index.json"))
    if idx:
        shards = sorted(set(json.load(open(idx[0], encoding="utf-8"))["weight_map"].values()))
    else:
        shards = sorted(p.name for p in src_dir.glob("*.safetensors"))
    if not shards:
        raise SystemExit(f"no safetensors shards in {src_dir}")
    t0 = time.time()
    entries: list[tuple] = []
    total = 0
    for sh in shards:
        p = src_dir / sh
        with open(p, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            hdr = json.loads(f.read(n))
        base = 8 + n
        for name, meta in hdr.items():
            if name == "__metadata__":
                continue
            a, b = meta["data_offsets"]
            entries.append((name, meta["dtype"], meta["shape"], p, base + a, b - a, total))
            total += b - a
    header: dict = {"__metadata__": {"format": "pt", "merged_from": str(src_dir)}}
    for name, dt, shape, _p, _start, size, off in entries:
        header[name] = {"dtype": dt, "shape": shape, "data_offsets": [off, off + size]}
    hb = json.dumps(header, separators=(",", ":")).encode("utf-8")
    hb += b" " * ((8 - len(hb) % 8) % 8)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with open(tmp, "wb") as o:
        o.write(struct.pack("<Q", len(hb)))
        o.write(hb)
        for _name, _dt, _shape, p, start, size, _off in entries:
            with open(p, "rb") as f:
                f.seek(start)
                left = size
                while left:
                    chunk = f.read(min(left, 64 << 20))
                    o.write(chunk)
                    left -= len(chunk)
        o.flush()
        os.fsync(o.fileno())
    os.replace(tmp, out)
    dts: dict[str, int] = {}
    for e in entries:
        dts[e[1]] = dts.get(e[1], 0) + 1
    print(f"merged {len(entries)} tensors from {len(shards)} shard(s) -> {out} "
          f"({out.stat().st_size / 1e9:.2f} GB, {time.time() - t0:.0f}s) dtypes={dts}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    merge(Path(sys.argv[1]), Path(sys.argv[2]))
