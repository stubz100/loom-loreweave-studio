"""P2/M4 proxy readiness meter — cheap, VLM-free training-readiness signals (spec §7).

Three INLINE tiers, computed on request from data already on disk (no GPU, no model):

- **coverage** — which frozen coverage-cell axis values (angle / shot_size / expression)
  the curated `ref_set` spans; `background` is advisory-only (P1 realizes cells with an
  empty background — spec §5 contract note).
- **dupes** — perceptual-hash (dHash, pure PIL) near-duplicate clusters over `refs/`.
- **captions** — template/edited counts + missing-trigger advisories (M3 preview).

Plus the **on_model** tier, harvested from an identity **`score`** job (face embedding
needs the insightface stack — always a queued job, never the API thread): anchor-cosine
when the version has a face anchor, set-centroid self-consistency otherwise (R120).

Everything here is **ADVISORY** (R14: models assist, the author decides) — the meter
recommends, it never blocks the Train button. Verdict thresholds below are v1 heuristics,
constants on purpose so they're findable and adjustable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import assets
from . import coverage
from . import training
from . import workspace as ws_mod
from .workspace import Workspace

READINESS_KIND = "loom.p2.readiness.v1"

# --- v1 advisory heuristics (documented constants, not magic) ------------------------
#
# ⚠ Retuned 2026-08-08 after the FIRST run on real data (char02, 79 refs) produced a
# `recommended: false` on a set whose coverage was a perfect 1.0. Both warning tiers were
# measuring **global similarity on a corpus engineered to be globally similar** — the
# failure mode is structural, so the fix is to make each tier compare like with like:
#
#   - dupes: two refs in DIFFERENT coverage cells are *supposed* to differ, so a duplicate
#     only means anything WITHIN a cell. Comparing across cells flagged 57 of 79 refs as
#     near-duplicates when 0 were (8×8 dHash cannot see pose at 9×8 px — a front and a
#     right-profile full-body in the same street scene hash alike).
#   - on-model: expression and angle are FROZEN coverage axes the set is REQUIRED to vary,
#     and they move the face embedding on their own (measured: serious 0.805 → smile 0.718;
#     3q-left 0.843 → back 0.707). Scoring every ref against one global centroid therefore
#     penalised exactly the diversity the coverage tier rewards. Outliers are now judged
#     against the ref's own expression band.
#
# The meter remains ADVISORY and is never consulted by staging or queueing (R14, §7).
DHASH_SIZE = 8                    # 8×8 gradient hash → 64 bits
DUPE_MAX_DISTANCE = 6             # ≤ this Hamming distance (of 64) = near-duplicate
DUPE_WARN_RATIO = 0.2             # >20 % of refs being extras in dupe clusters → warn
COVERAGE_WARN_BELOW = 0.5         # mean axis coverage below half the vocabulary → warn
MIN_REFS_INFO = 6                 # fewer refs than this → the whole meter is "info"
ONMODEL_ANCHOR_WARN_BELOW = 0.30  # mean ArcFace cos to the anchor below this → warn
ONMODEL_OUTLIER_FLOOR = 0.25      # absolute floor: no ref above this is an outlier
ONMODEL_OUTLIER_MARGIN = 0.15     # outlier = below max(floor, expected − this)
ONMODEL_MIN_BAND = 5              # a band needs this many scored refs to earn an offset
ONMODEL_OUTLIER_WARN_RATIO = 0.1  # a few odd refs in a big set is normal → warn above this
# The frozen coverage axes an on-model score is judged RELATIVE to (background excluded —
# it is a free descriptor, not a controlled axis). Order is display order, not semantic.
ONMODEL_BAND_AXES = ("shot_size", "angle", "expression")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- perceptual hash (pure PIL — no numpy/model dependency) ---------------------------

def dhash(path: Path, size: int = DHASH_SIZE) -> int | None:
    """Difference hash: grayscale → (size+1)×size → 1 bit per horizontal gradient.
    None for unreadable files (reported, never fatal)."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            img = im.convert("L").resize((size + 1, size), Image.LANCZOS)
            px = list(img.getdata())
    except Exception:
        return None
    bits = 0
    for r in range(size):
        row = px[r * (size + 1):(r + 1) * (size + 1)]
        for c in range(size):
            bits = (bits << 1) | (1 if row[c] > row[c + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


# --- tiers ----------------------------------------------------------------------------

def _coverage_tier(refs: list[dict]) -> dict:
    axes = {"angle": coverage.ANGLES, "shot_size": coverage.SHOT_SIZES,
            "expression": coverage.EXPRESSIONS}
    seen: dict[str, dict[str, int]] = {a: {} for a in axes}
    cells: dict[str, int] = {}
    with_bg = 0
    for ref in refs:
        cell = ref.get("coverage_cell") or {}
        for axis in axes:
            v = cell.get(axis)
            if v:
                seen[axis][v] = seen[axis].get(v, 0) + 1
        key = "__".join(str(cell.get(a, "")) for a in ("shot_size", "angle", "expression"))
        cells[key] = cells.get(key, 0) + 1
        if (cell.get("background") or "").strip():
            with_bg += 1
    per_axis = {
        axis: {
            "present": dict(sorted(seen[axis].items())),
            "missing": sorted(v for v in vocab if v not in seen[axis]),
        }
        for axis, vocab in axes.items()
    }
    score = (sum(len(seen[a]) / len(vocab) for a, vocab in axes.items()) / len(axes)
             if refs else 0.0)
    if len(refs) < MIN_REFS_INFO:
        status = "info"
    else:
        status = "ok" if score >= COVERAGE_WARN_BELOW else "warn"
    return {
        "status": status,
        "score": round(score, 3),
        "ref_count": len(refs),
        "distinct_cells": len(cells),
        "axes": per_axis,
        # advisory-only until inpaint-realized backgrounds exist (P1 contract note)
        "background_advisory": {"with_background": with_bg, "of": len(refs)},
    }


def _cell_key(ref: dict) -> str:
    """The pose a ref occupies — shot__angle__expression. Background is per-sweep noise
    (never pose identity), so it stays out of the key, exactly like `bible.pose_key`."""
    cell = ref.get("coverage_cell") or {}
    return "__".join(str(cell.get(a, "")) for a in ("shot_size", "angle", "expression"))


def _dupes_tier(refs_dir: Path, refs: list[dict]) -> dict:
    """Near-duplicate clusters, compared **only within a coverage cell**.

    Two refs in different cells are *supposed* to look different — flagging them tells the
    author nothing and (see the constants note) fired on 72 % of a clean set. A duplicate is
    only meaningful against a ref that was asked for the same pose."""
    hashes: dict[str, int] = {}
    unreadable: list[str] = []
    for ref in refs:
        p = refs_dir / ref["file"]
        h = dhash(p) if p.is_file() else None
        if h is None:
            unreadable.append(ref["id"])
        else:
            hashes[ref["id"]] = h
    by_cell: dict[str, list[str]] = {}
    for ref in refs:
        if ref["id"] in hashes:
            by_cell.setdefault(_cell_key(ref), []).append(ref["id"])

    groups: list[list[str]] = []
    for ids in by_cell.values():
        if len(ids) < 2:
            continue                      # a cell with one ref can hold no duplicate
        # greedy clustering — a cell holds a handful of refs, pairwise is fine
        cluster_of: dict[str, int] = {}
        clusters: list[list[str]] = []
        for i, a in enumerate(ids):
            for b in ids[:i]:
                if hamming(hashes[a], hashes[b]) <= DUPE_MAX_DISTANCE:
                    ci = cluster_of.get(b)
                    if ci is None:
                        ci = len(clusters)
                        clusters.append([b])
                        cluster_of[b] = ci
                    if a not in cluster_of:
                        clusters[ci].append(a)
                        cluster_of[a] = ci
                    break
        groups.extend(c for c in clusters if len(c) >= 2)
    extras = sum(len(g) - 1 for g in groups)
    ratio = extras / max(1, len(hashes))
    multi = sum(1 for ids in by_cell.values() if len(ids) >= 2)
    return {
        "status": "warn" if ratio > DUPE_WARN_RATIO else "ok",
        "hashed": len(hashes),
        "duplicate_groups": groups,
        "extras": extras,
        "ratio": round(ratio, 3),
        "unreadable": unreadable,
        # what was actually comparable: cells holding ≥2 refs (the rest cannot duplicate)
        "scope": "coverage_cell",
        "cells_compared": multi,
        "cells_total": len(by_cell),
    }


def _captions_tier(ws: Workspace, asset_id: str, version_id: str | None) -> dict:
    preview = training.list_captions(ws, asset_id, version_id=version_id)
    missing_trigger = [c["id"] for c in preview["captions"] if not c["has_trigger"]]
    return {
        "status": "ok" if preview["count"] else "warn",
        "count": preview["count"],
        "edited": preview["edited_count"],
        "trigger_token": preview["trigger_token"],
        "missing_trigger": missing_trigger,
    }


# --- on-model harvest (from a done identity `score` job) ------------------------------

def harvest_score_job(vdir: Path, version: dict, job: dict) -> dict:
    """Fold a finished identity-score job's batch manifest into the on_model tier.
    The manifest is the product (score rows carry no images); scores map back to refs
    via the `meta.ref_id` the embed submitter stamped on every item."""
    result = job.get("result") or {}
    manifest_path = result.get("manifest_path")
    if not manifest_path or not Path(manifest_path).is_file():
        raise ws_mod.WorkspaceError("score job has no readable batch manifest")
    import json as _json
    data = _json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if data.get("mode") != "score":
        raise ws_mod.WorkspaceError("job is not an identity score run")
    by_id = {r["id"]: r for r in version.get("ref_set") or []}
    rows = []
    for it in data.get("items") or []:
        meta = it.get("meta") or {}
        rid = meta.get("ref_id")
        if it.get("status") == "ok" and rid in by_id:
            cell = by_id[rid].get("coverage_cell") or {}
            rows.append({
                "ref_id": rid,
                "face": bool(meta.get("face")),
                "anchor_cos": meta.get("anchor_cos"),
                "centroid_cos": meta.get("centroid_cos"),
                **{a: cell.get(a) or "" for a in ONMODEL_BAND_AXES},
            })
    faces = [r for r in rows if r["face"]]
    anchor_mode = bool(data.get("anchor_face")) and any(
        r["anchor_cos"] is not None for r in faces)
    # Why centroid: an anchor that is PRESENT but holds no detectable face is not the same
    # as no anchor at all, and the old tier reported both as bare "mode: centroid". The
    # author's anchor is a generation-support reference (flux2 ref image, inswapper off) —
    # a rejected one is INFORMATION, never a fault to warn about.
    anchor_status = ("used" if anchor_mode
                     else "no_face" if data.get("anchor")
                     else "absent")
    metric = "anchor_cos" if anchor_mode else "centroid_cos"
    scored = [r for r in faces if r[metric] is not None]
    mean = (sum(r[metric] for r in scored) / len(scored)) if scored else None

    # All THREE frozen coverage axes are things the set is REQUIRED to vary, and each moves
    # the face embedding on its own — measured on char02 (global mean 0.772):
    #   shot_size   portrait +0.027 … full_body -0.097   (a full-body face is small in frame)
    #   angle       3q-left  +0.071 … front     -0.063
    #   expression  serious  +0.033 … smile     -0.054
    # Judging every ref against one global mean therefore flags whichever band sits lowest,
    # i.e. exactly the diversity the coverage tier rewards. Banding on the full cell would
    # leave ~1 ref per band (78 distinct cells over 79 refs), so each axis contributes an
    # additive offset instead and a ref is judged against what its OWN cell should score:
    #     expected = global mean + Δshot_size + Δangle + Δexpression
    # An axis value needs ONMODEL_MIN_BAND members to earn its offset; otherwise it
    # contributes 0 and that ref is judged globally, exactly as before.
    def _bands(axis: str) -> dict[str, dict[str, Any]]:
        acc: dict[str, list[float]] = {}
        for r in scored:
            acc.setdefault(r.get(axis) or "(none)", []).append(r[metric])
        out = {}
        for name, vals in acc.items():
            m = sum(vals) / len(vals)
            earns = len(vals) >= ONMODEL_MIN_BAND
            out[name] = {"n": len(vals), "mean": round(m, 3),
                         "offset": round(m - mean, 3) if (earns and mean is not None) else 0.0,
                         "reference": "band" if earns else "global"}
        return out

    bands = {a: _bands(a) for a in ONMODEL_BAND_AXES} if scored else {}

    def _expected(r: dict) -> float:
        return mean + sum(bands[a].get(r.get(a) or "(none)", {}).get("offset", 0.0)
                          for a in ONMODEL_BAND_AXES)

    outliers = []
    if mean is not None:
        for r in scored:
            if r[metric] < max(ONMODEL_OUTLIER_FLOOR,
                               _expected(r) - ONMODEL_OUTLIER_MARGIN):
                outliers.append(r["ref_id"])

    # A handful of odd refs in a large set is normal, not a verdict — the tier only warns
    # when they are a meaningful FRACTION. Below that they are still listed for review.
    outlier_ratio = len(outliers) / len(scored) if scored else 0.0
    if not faces:
        status = "info"          # e.g. an all-back-view set — nothing to measure
    elif anchor_mode and mean is not None and mean < ONMODEL_ANCHOR_WARN_BELOW:
        status = "warn"
    elif outlier_ratio > ONMODEL_OUTLIER_WARN_RATIO:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "mode": "anchor" if anchor_mode else "centroid",
        "anchor_status": anchor_status,
        "job_id": job.get("id"),
        "scored": len(scored),
        "faces": len(faces),
        "of": len(rows),
        "mean_cos": round(mean, 3) if mean is not None else None,
        "outlier_scope": "coverage-cell offsets (" + "+".join(ONMODEL_BAND_AXES) + ")",
        "outlier_ratio": round(outlier_ratio, 3),
        "bands": bands,
        "outliers": outliers,
        "refs": rows,
        "computed_at": _now(),
    }


# --- compute / persist -----------------------------------------------------------------

def _advisory(tiers: dict[str, dict], ref_count: int) -> dict:
    """Roll the tiers into one advisory verdict.

    **This never gates anything** (R14, §7): staging and queueing do not read readiness at
    all, and `blocking: false` says so in the payload. `recommended` is a suggestion the
    author is free to ignore — the Train button is live either way."""
    reasons: list[str] = []
    notes: list[str] = []
    cov = tiers["coverage"]
    if ref_count < MIN_REFS_INFO:
        reasons.append(f"small ref set ({ref_count} < {MIN_REFS_INFO})")
    if cov["status"] == "warn":
        missing = [f"{axis}: {', '.join(vals['missing'])}"
                   for axis, vals in cov["axes"].items() if vals["missing"]]
        reasons.append("coverage thin — missing " + "; ".join(missing))
    if tiers["dupes"]["status"] == "warn":
        reasons.append(f"{tiers['dupes']['extras']} near-duplicate ref(s) "
                       "within a coverage cell")
    if tiers["captions"]["missing_trigger"]:
        reasons.append(f"{len(tiers['captions']['missing_trigger'])} caption(s) missing "
                       "the trigger token")
    om = tiers["on_model"]
    if om["status"] == "not_run":
        reasons.append("on-model check not run (🔬 scan is optional)")
    elif om["status"] == "warn":
        reasons.append("on-model outliers — review the flagged refs"
                       if om.get("outliers") else "low mean similarity to the anchor")
    # An anchor that holds no detectable face is worth SAYING (the tier silently degrades
    # to centroid otherwise) but is not a fault: the anchor's job is generation support.
    if om.get("anchor_status") == "no_face":
        notes.append("anchor set but no face detected in it — scored against the set "
                     "centroid instead (R120 fallback; the anchor still serves generation)")
    statuses = [t["status"] for t in tiers.values()]
    overall = ("warn" if "warn" in statuses
               else "info" if ("info" in statuses or "not_run" in statuses) else "ok")
    recommended = "warn" not in statuses and ref_count >= MIN_REFS_INFO
    return {"status": overall, "recommended": recommended, "reasons": reasons,
            "notes": notes, "blocking": False}


def compute(ws: Workspace, asset_id: str, *, version_id: str | None = None) -> dict:
    """Live readiness view: inline tiers recomputed fresh; on_model merged from the last
    persisted snapshot (readiness.json) if one exists. Read-only."""
    detail = assets.get_asset(ws, asset_id)
    if detail is None:
        raise ws_mod.WorkspaceError(f"unknown asset {asset_id!r}")
    vdir, version = assets.resolve_version_dir(ws, asset_id, version_id)
    refs = version.get("ref_set") or []

    on_model: dict = {"status": "not_run"}
    persisted_at = None
    rj = vdir / "readiness.json"
    if rj.is_file():
        try:
            prev = ws_mod.read_json(rj)
            persisted_at = prev.get("computed_at")
            if isinstance(prev.get("on_model"), dict):
                on_model = prev["on_model"]
        except ws_mod.WorkspaceError:
            pass   # a corrupt snapshot never blocks the live view

    tiers = {
        "coverage": _coverage_tier(refs),
        "dupes": _dupes_tier(vdir / "refs", refs),
        "captions": _captions_tier(ws, asset_id, version_id),
        "on_model": on_model,
    }
    return {
        "schema_version": 1,
        "kind": READINESS_KIND,
        "asset_id": detail["profile"]["id"],
        "version_id": version["id"],
        "computed_at": _now(),
        "persisted_at": persisted_at,
        **tiers,
        "advisory": _advisory(tiers, len(refs)),
    }


def persist(ws: Workspace, asset_id: str, *, version_id: str | None = None,
            job: dict | None = None) -> dict:
    """Compute + atomically write `readiness.json` and stamp `version.readiness_status`.
    With `job` (a done identity score run), its scores become the on_model tier first."""
    vdir, version = assets.resolve_version_dir(ws, asset_id, version_id)
    if job is not None:
        on_model = harvest_score_job(vdir, version, job)
        snapshot = compute(ws, asset_id, version_id=version_id)
        snapshot["on_model"] = on_model
        tiers = {k: snapshot[k] for k in ("coverage", "dupes", "captions", "on_model")}
        snapshot["advisory"] = _advisory(tiers, len(version.get("ref_set") or []))
    else:
        snapshot = compute(ws, asset_id, version_id=version_id)
    snapshot["persisted_at"] = snapshot["computed_at"]
    ws_mod.atomic_write_json(vdir / "readiness.json", snapshot)
    version["readiness_status"] = {
        "status": snapshot["advisory"]["status"],
        "recommended": snapshot["advisory"]["recommended"],
        "on_model": snapshot["on_model"].get("status", "not_run"),
        "computed_at": snapshot["computed_at"],
    }
    assets.write_version(vdir, version)
    return snapshot


def embed_items(vdir: Path, version: dict) -> tuple[list[dict[str, Any]], str | None]:
    """The identity-score job payload for this version: every curated ref as an absolute
    input + `meta.ref_id` (the harvest key), and the anchor path when one is set."""
    refs = version.get("ref_set") or []
    if not refs:
        raise ws_mod.WorkspaceError("cannot scan on-model: version ref_set is empty")
    items = []
    for ref in refs:
        p = vdir / "refs" / ref["file"]
        if not p.is_file():
            raise ws_mod.WorkspaceError(f"curated ref file missing: {p}")
        items.append({"input": str(p), "seed": 0,
                      "meta": {"ref_id": ref["id"], "file": ref["file"]}})
    anchor = version.get("anchor") or {}
    anchor_path = None
    if anchor.get("file"):
        ap = vdir / "faces" / anchor["file"]
        if ap.is_file():
            anchor_path = str(ap)
    return items, anchor_path
