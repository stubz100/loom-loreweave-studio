"""P2/M2.12 — GraphRAG retrieval-index **SPIKE** (R170; non-gating).

**This is a spike, not the index.** P4 implements the persistent GraphRAG/vector index
alongside the embedding model (R137/R170, `kb-loom-p4.md` §13). What lives here is the
cheap half that answers the feasibility question P2 owes: *can a small local graph over the
artifacts P2 already writes answer the relational questions the author actually asks?* —
e.g. "which curated refs used style X?" and "which coverage cells lack a kept ref?"

Deliberately **CPU-only and embedding-free**: the embedding model ships in P4 and the
retrieval index uses that same family, so anything vector-shaped here would be throwaway.
Facts are plain typed triples, rebuilt from disk on demand and written to
`context/project_facts.jsonl` — the file `kb-loom-p4.md` §5 already reserves for exactly
this. Nothing here is authoritative: the workspace records are, and this is a derived view.

Sources mined (all already on disk, all P2 outputs):
  story.json ................ L1 styles + the active one
  bible/styles|poses ........ durable sample/icon presence
  assets/*/profile.json ..... asset identity
  versions/*/version.json ... ref_set (coverage_cell, style_id, source job), casting +
                              starred hero, promoted `lora`, anchor, readiness_status
  versions/*/lora.manifest.json  promoted-adapter provenance hashes (P2-13)
  lineage/index.json ........ job -> version/stage edges
  postproc_stacks.json ...... image derivation edges (source -> output)
  jobs/queue.json ........... job pipeline/mode/status + the `[X postproc of Y]` prompts

See the journal entry "M2.12 — GraphRAG spike" for the feasibility verdict and the three
provenance findings this shook out; the docstrings below flag them at the point of impact.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from . import assets
from . import bible
from . import workspace as ws_mod
from .workspace import Workspace

FACTS_KIND = "loom.p2.project_facts.v1"

# `[clean postproc of job_x/img.png]` — the prompt convention some postproc jobs carry
# INSTEAD of a structured edge. Parsing a prompt for provenance is exactly the fragility
# this spike exists to surface (finding 2); the real index must not depend on it.
_POSTPROC_PROMPT = re.compile(r"^\[(\w+) postproc of (\S+?)\]")


def facts_path(ws: Workspace) -> Path:
    return ws.path / "context" / "project_facts.jsonl"


def _fact(subject: str, predicate: str, obj: str | None, **attrs: Any) -> dict[str, Any]:
    """One typed triple. `attrs` carry the qualifiers a pure triple would lose (a cell's
    axes, a job's pipeline) — P4 can promote them to nodes if the index wants them."""
    f = {"s": subject, "p": predicate, "o": obj}
    if attrs:
        f["attrs"] = {k: v for k, v in attrs.items() if v is not None}
    return f


# --- extraction ------------------------------------------------------------------------

def _style_facts(ws: Workspace) -> list[dict]:
    story = bible.load_story(ws)
    out: list[dict] = []
    active = story.get("active_style_id")
    samples = {p.stem for p in (ws.bible_dir / "styles").glob("*") if p.is_file()}
    for s in story.get("styles") or []:
        sid = s["id"]
        out.append(_fact(sid, "is_a", "style", name=s.get("name")))
        if sid == active:
            out.append(_fact(sid, "is_active_style", None))
        if sid in samples:
            out.append(_fact(sid, "has_sample", None))
    return out


def _pose_facts(ws: Workspace) -> list[dict]:
    return [_fact(key, "is_a", "pose_icon", file=name)
            for key, name in bible.list_pose_icons(ws).items()]


def _asset_facts(ws: Workspace) -> list[dict]:
    """Assets, versions, refs, casting, promoted adapters — the spine of the graph.

    ⚠ Finding 1 surfaces here: `ref.style_id` is the ONLY direct ref→style edge, and it is
    null for every post-M2.10 flux2 expansion ref, because route 1 deliberately runs those
    sweeps with the L1 gate off (the hero reference carries the style, not the prompt).
    The edge is emitted when present and simply absent otherwise — the query below reports
    that honestly rather than pretending the corpus has no style."""
    out: list[dict] = []
    for _path, profile in assets._iter_profiles(ws):
        aid = profile["id"]
        out.append(_fact(aid, "is_a", "asset", name=profile.get("name"),
                         asset_class=profile.get("asset_class", "characters")))
        adir = ws.asset_dir(profile.get("asset_class", "characters"), profile["slug"])
        for version in assets._load_versions(adir, profile):
            vid = version["id"]
            out.append(_fact(aid, "has_version", vid, name=version.get("name"),
                             finalized=bool(version.get("finalized"))))
            if profile.get("active_version") == vid:
                out.append(_fact(aid, "active_version", vid))
            if version.get("trigger_token"):
                out.append(_fact(vid, "has_trigger", version["trigger_token"]))
            if version.get("anchor"):
                out.append(_fact(vid, "has_anchor", version["anchor"].get("file")))
            rs = version.get("readiness_status") or {}
            if rs:
                out.append(_fact(vid, "readiness", rs.get("status"),
                                 recommended=rs.get("recommended"),
                                 on_model=rs.get("on_model")))
            lora = version.get("lora") or {}
            if lora:
                out.append(_fact(vid, "has_lora", lora.get("file"),
                                 base_family=lora.get("base_family"),
                                 trigger=lora.get("trigger_token"),
                                 job_id=lora.get("job_id"), sha256=lora.get("sha256")))
            for c in version.get("casting") or []:
                out.append(_fact(vid, "has_candidate", c["id"],
                                 output=c.get("source_output"), pipeline=c.get("pipeline"),
                                 job_id=c.get("job_id")))
                if c.get("starred"):
                    out.append(_fact(vid, "hero_is", c["id"], output=c.get("source_output")))
            for r in version.get("ref_set") or []:
                cell = r.get("coverage_cell") or {}
                out.append(_fact(vid, "has_ref", r["id"], output=r.get("source_output"),
                                 job_id=r.get("job_id"), pipeline=r.get("pipeline"),
                                 method=r.get("method")))
                if cell:
                    out.append(_fact(r["id"], "in_cell", bible.pose_key(cell), **cell))
                if r.get("style_id"):
                    out.append(_fact(r["id"], "used_style", r["style_id"]))
    return out


def _derivation_facts(ws: Workspace) -> list[dict]:
    """Image derivation edges (`output derived_from source`).

    ⚠ Finding 2 surfaces here: derivation is split across THREE mechanisms and no single
    one is complete — `postproc_stacks.json` steps, the `[X postproc of Y]` prompt string,
    and `job.chained_from` (which exists in the schema but is populated nowhere). We union
    the first two and label each edge's `via` so the gap is measurable, not hidden."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    stacks = []
    sp = ws.path / "postproc_stacks.json"
    if sp.is_file():
        try:
            stacks = (ws_mod.read_json(sp) or {}).get("stacks") or []
        except ws_mod.WorkspaceError:
            stacks = []
    for stack in stacks:
        for step in stack.get("steps") or []:
            src, dst = step.get("source"), step.get("output")
            if src and dst and (dst, src) not in seen:
                seen.add((dst, src))
                out.append(_fact(dst, "derived_from", src, via="postproc_stack",
                                 preset=step.get("preset"), job_id=step.get("job_id"),
                                 backend=step.get("backend")))

    for job in _jobs(ws).values():
        prompt = (job.get("params") or {}).get("prompt")
        name = (job.get("result") or {}).get("output_name")
        if not isinstance(prompt, str) or not name:
            continue
        m = _POSTPROC_PROMPT.match(prompt.strip())
        if m and (name, m.group(2)) not in seen:
            seen.add((name, m.group(2)))
            out.append(_fact(name, "derived_from", m.group(2), via="prompt_convention",
                             preset=m.group(1), job_id=job.get("id")))
    return out


def _job_facts(ws: Workspace) -> list[dict]:
    out: list[dict] = []
    for job in _jobs(ws).values():
        jid = job.get("id")
        if not jid:
            continue
        out.append(_fact(jid, "is_a", "job", pipeline=job.get("pipeline"),
                         mode=job.get("mode"), status=job.get("status"),
                         stage=job.get("stage")))
        if job.get("profile_version_id"):
            out.append(_fact(jid, "for_version", job["profile_version_id"]))
        for name in (job.get("result") or {}).get("output_names") or []:
            out.append(_fact(jid, "produced", name))
    return out


def _jobs(ws: Workspace) -> dict[str, dict]:
    p = ws.queue_path
    if not p.is_file():
        return {}
    try:
        return (ws_mod.read_json(p) or {}).get("jobs") or {}
    except ws_mod.WorkspaceError:
        return {}


def build(ws: Workspace) -> list[dict]:
    """Rebuild every fact from disk. Cheap enough to run on demand (the whole char02
    project is ~700 jobs and rebuilds in well under a second) — no incremental index."""
    facts: list[dict] = []
    for part in (_style_facts, _pose_facts, _asset_facts, _job_facts, _derivation_facts):
        facts.extend(part(ws))
    return facts


def write(ws: Workspace) -> dict:
    """Persist to `context/project_facts.jsonl` (the path kb-loom-p4.md §5 reserves).
    Rebuildable by construction — safe to delete, never a source of truth."""
    facts = build(ws)
    path = facts_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": FACTS_KIND, "count": len(facts)}) + "\n")
        for f in facts:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return {"path": str(path), "facts": len(facts), **stats(facts)}


def stats(facts: Iterable[dict]) -> dict:
    preds: dict[str, int] = {}
    for f in facts:
        preds[f["p"]] = preds.get(f["p"], 0) + 1
    return {"predicates": dict(sorted(preds.items(), key=lambda kv: -kv[1]))}


# --- queries (the spike's actual question: can these be answered?) ----------------------

def _index(facts: list[dict]) -> dict[str, list[dict]]:
    by_pred: dict[str, list[dict]] = {}
    for f in facts:
        by_pred.setdefault(f["p"], []).append(f)
    return by_pred


def refs_using_style(facts: list[dict], style_id: str) -> dict:
    """**Named spike query 1** — "which curated refs used style X?"

    Answers DIRECTLY from `used_style`, and reports how much of the corpus can never
    answer it that way (finding 1): a ref generated with the L1 gate off carries no style
    id, because no L1 style was applied — its style came from the hero reference image.
    For those, style is a TRANSITIVE property of the hero, not an attribute of the ref, so
    the honest answer names the gap instead of returning a misleading empty list."""
    by = _index(facts)
    direct = [f["s"] for f in by.get("used_style", []) if f["o"] == style_id]
    all_refs = {f["o"] for f in by.get("has_ref", [])}
    styled = {f["s"] for f in by.get("used_style", [])}
    return {
        "style_id": style_id,
        "refs": sorted(direct),
        "refs_with_a_style_edge": len(styled),
        "refs_total": len(all_refs),
        "unattributed": sorted(all_refs - styled),
        "note": ("refs with no style edge ran with the L1 gate OFF — post-M2.10 flux2 "
                 "expansion lets the hero reference carry the style, so style is "
                 "transitive through `hero_is` + `derived_from`, not a ref attribute"),
    }


def cells_without_kept_ref(facts: list[dict], version_id: str) -> dict:
    """**Named spike query 2** — "which coverage cells lack a kept ref?"

    Fully answerable from P2's own data, no gaps: the frozen vocabulary enumerates the
    matrix and `in_cell` covers it. This is the query that validates the whole idea."""
    from . import coverage

    by = _index(facts)
    ref_ids = {f["o"] for f in by.get("has_ref", []) if f["s"] == version_id}
    filled = {f["o"] for f in by.get("in_cell", []) if f["s"] in ref_ids}
    every = [f"{s}__{a}__{e}"
             for s in coverage.SHOT_SIZES for a in coverage.ANGLES
             for e in coverage.EXPRESSIONS]
    missing = sorted(set(every) - filled)
    return {"version_id": version_id, "cells_total": len(every),
            "cells_filled": len(filled), "cells_missing": len(missing),
            "missing": missing}


def derivation_chain(facts: list[dict], output: str, *, limit: int = 32) -> dict:
    """Walk `derived_from` back to an origin image. Cycle-safe and depth-capped."""
    back = {f["s"]: f for f in facts if f["p"] == "derived_from"}
    chain: list[dict] = []
    cur, seen = output, {output}
    while len(chain) < limit:
        edge = back.get(cur)
        if edge is None:
            break
        chain.append({"output": cur, "source": edge["o"],
                      **{k: v for k, v in (edge.get("attrs") or {}).items()}})
        cur = edge["o"]
        if cur in seen:
            chain[-1]["cycle"] = True
            break
        seen.add(cur)
    return {"output": output, "origin": cur, "hops": len(chain), "chain": chain}


def style_of_output(facts: list[dict], output: str) -> dict:
    """Transitive style resolution — the shape finding 1 implies. Walks derivation back to
    an origin, then asks whether anything on the path carries a style edge. Today this
    usually returns `null` (see the journal): the origin generation's style was never
    recorded on the image, only on the job's request. **This is the concrete gap P4's
    index must close** — one durable `generated_under_style` edge at generation time."""
    walk = derivation_chain(facts, output)
    by = _index(facts)
    styled_outputs = {f["s"]: f["o"] for f in by.get("used_style", [])}
    path = [output] + [c["source"] for c in walk["chain"]]
    hit = next((styled_outputs[p] for p in path if p in styled_outputs), None)
    return {"output": output, "origin": walk["origin"], "hops": walk["hops"],
            "style_id": hit, "resolved": hit is not None}


def report(ws: Workspace) -> dict:
    """The spike's verdict, computed from the live workspace: what the graph holds, which
    named queries it can answer, and where provenance is missing."""
    facts = build(ws)
    by = _index(facts)
    versions = [f["o"] for f in by.get("has_version", [])]
    styles = [f["s"] for f in by.get("is_a", []) if f["o"] == "style"]
    derivations = by.get("derived_from", [])
    via: dict[str, int] = {}
    for d in derivations:
        k = (d.get("attrs") or {}).get("via", "?")
        via[k] = via.get(k, 0) + 1
    refs_total = len({f["o"] for f in by.get("has_ref", [])})
    styled_refs = len({f["s"] for f in by.get("used_style", [])})
    return {
        "facts": len(facts),
        "nodes": {"assets": len([f for f in by.get("is_a", []) if f["o"] == "asset"]),
                  "versions": len(versions), "styles": len(styles),
                  "pose_icons": len([f for f in by.get("is_a", []) if f["o"] == "pose_icon"]),
                  "jobs": len([f for f in by.get("is_a", []) if f["o"] == "job"]),
                  "refs": refs_total},
        "derivation_edges": {"total": len(derivations), "by_source": via},
        "style_provenance": {"refs_total": refs_total, "with_style_edge": styled_refs,
                             "coverage": round(styled_refs / refs_total, 3) if refs_total else None},
        "queries": {
            "cells_without_kept_ref": "answerable",
            "refs_using_style": ("answerable" if styled_refs else
                                 "DEGRADED — no ref carries a style edge (L1 gate off; "
                                 "style arrives via the hero reference)"),
            "derivation_chain": "answerable (union of stack + prompt edges)",
            "style_of_output": ("DEGRADED — no durable generated_under_style edge exists "
                                "on an image; the style lives only in the job request"),
        },
        **stats(facts),
    }
