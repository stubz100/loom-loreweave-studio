"""Read a GGUF file's header — the metadata and the tensor table — without the `gguf` package,
torch, or the whole file: a local path, or a hub URL read with one Range request (the header
of a 20 GB quant is a few MB). Spike tool (2026-09-21, kb-loom-flux2-weights.md).

    python tools/gguf/inspect_gguf.py <path-or-url> [--bytes 8000000] [--tensors 40] [--json out.json]

Prints the general.* metadata, the tensor count, the quant types with their tensor counts and
byte totals, and the first N tensor names, so a loader mapping can be judged before a download.
GGUF v3: https://github.com/ggml-org/ggml/blob/master/docs/gguf.md
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

MAGIC = b"GGUF"
# ggml_type enum → (name, block size, type size in bytes)
GGML_TYPES = {
    0: ("F32", 1, 4), 1: ("F16", 1, 2), 2: ("Q4_0", 32, 18), 3: ("Q4_1", 32, 20), 6: ("Q5_0", 32, 22),
    7: ("Q5_1", 32, 24), 8: ("Q8_0", 32, 34), 9: ("Q8_1", 32, 36), 10: ("Q2_K", 256, 84), 11: ("Q3_K", 256, 110),
    12: ("Q4_K", 256, 144), 13: ("Q5_K", 256, 176), 14: ("Q6_K", 256, 210), 15: ("Q8_K", 256, 292),
    16: ("IQ2_XXS", 256, 66), 17: ("IQ2_XS", 256, 74), 18: ("IQ3_XXS", 256, 98), 19: ("IQ1_S", 256, 50),
    20: ("IQ4_NL", 32, 18), 21: ("IQ3_S", 256, 110), 22: ("IQ2_S", 256, 82), 23: ("IQ4_XS", 256, 136),
    24: ("I8", 1, 1), 25: ("I16", 1, 2), 26: ("I32", 1, 4), 27: ("I64", 1, 8), 28: ("F64", 1, 8),
    29: ("IQ1_M", 256, 56), 30: ("BF16", 1, 2), 34: ("TQ1_0", 256, 54), 35: ("TQ2_0", 256, 66),
}


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, fmt: str):
        size = struct.calcsize(fmt)
        if self.pos + size > len(self.data):
            raise EOFError(f"header longer than the {len(self.data)} bytes read; pass a larger --bytes")
        out = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return out[0] if len(out) == 1 else out

    def string(self) -> str:
        n = self.take("<Q")
        if self.pos + n > len(self.data):
            raise EOFError("string past the bytes read; pass a larger --bytes")
        s = self.data[self.pos:self.pos + n].decode("utf-8", errors="replace")
        self.pos += n
        return s

    def value(self, vtype: int):
        scalar = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
        if vtype in scalar:
            return self.take(scalar[vtype])
        if vtype == 8:
            return self.string()
        if vtype == 9:
            itype = self.take("<I")
            n = self.take("<Q")
            if n > 100_000:                       # a tokenizer vocabulary: skip the values, keep the count
                for _ in range(n):
                    self.value(itype)
                return f"<array of {n} type {itype}>"
            return [self.value(itype) for _ in range(n)]
        raise ValueError(f"unknown GGUF value type {vtype}")


def read_header(data: bytes) -> dict:
    r = _Reader(data)
    if r.take("4s") != MAGIC:
        raise ValueError("not a GGUF file (magic)")
    version = r.take("<I")
    n_tensors = r.take("<Q")
    n_kv = r.take("<Q")
    kv: dict[str, object] = {}
    for _ in range(n_kv):
        key = r.string()
        kv[key] = r.value(r.take("<I"))
    tensors = []
    for _ in range(n_tensors):
        name = r.string()
        n_dims = r.take("<I")
        dims = [r.take("<Q") for _ in range(n_dims)]
        ttype = r.take("<I")
        offset = r.take("<Q")
        tname, block, tsize = GGML_TYPES.get(ttype, (f"type{ttype}", 1, 0))
        n_elems = 1
        for d in dims:
            n_elems *= d
        nbytes = (n_elems // block) * tsize if block and tsize else 0
        tensors.append({"name": name, "dims": dims, "type": tname, "bytes": nbytes, "offset": offset})
    return {"version": version, "n_tensors": n_tensors, "kv": kv, "tensors": tensors, "header_bytes": r.pos}


def fetch(source: str, nbytes: int) -> tuple[bytes, int | None]:
    p = Path(source)
    if p.is_file():
        with open(p, "rb") as fp:
            return fp.read(nbytes), p.stat().st_size
    req = urllib.request.Request(source, headers={"Range": f"bytes=0-{nbytes - 1}", "User-Agent": "loom-gguf-inspect"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = None
        cr = resp.headers.get("Content-Range")
        if cr and "/" in cr:
            try:
                total = int(cr.rsplit("/", 1)[1])
            except ValueError:
                total = None
        return resp.read(), total


def summarize(h: dict, total_size: int | None, n_names: int) -> dict:
    by_type: dict[str, dict] = defaultdict(lambda: {"tensors": 0, "bytes": 0})
    for t in h["tensors"]:
        by_type[t["type"]]["tensors"] += 1
        by_type[t["type"]]["bytes"] += t["bytes"]
    prefixes = Counter(t["name"].split(".")[0] for t in h["tensors"])
    general = {k: v for k, v in h["kv"].items() if k.startswith("general.") and not isinstance(v, list)}
    return {
        "version": h["version"], "n_tensors": h["n_tensors"], "header_bytes": h["header_bytes"], "file_bytes": total_size,
        "general": general, "other_keys": [k for k in h["kv"] if not k.startswith("general.")],
        "by_type": {k: dict(v) for k, v in sorted(by_type.items(), key=lambda kv: -kv[1]["bytes"])},
        "name_prefixes": dict(prefixes.most_common()),
        "first_tensors": [{"name": t["name"], "dims": t["dims"], "type": t["type"]} for t in h["tensors"][:n_names]],
        "high_precision": [t["name"] for t in h["tensors"] if t["type"] in ("F32", "F16", "BF16") and t["dims"] and len(t["dims"]) >= 2][:60],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source")
    ap.add_argument("--bytes", type=int, default=8_000_000)
    ap.add_argument("--tensors", type=int, default=40)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    data, total = fetch(a.source, a.bytes)
    h = read_header(data)
    s = summarize(h, total, a.tensors)
    print(json.dumps(s, indent=1, default=str))
    if a.json:
        Path(a.json).write_text(json.dumps({**s, "tensors": h["tensors"]}, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
