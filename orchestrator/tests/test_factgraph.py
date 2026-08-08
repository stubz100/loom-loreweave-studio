"""P2/M2.12 — GraphRAG retrieval-index SPIKE (R170, non-gating).

Locks what the spike actually established: the fact extractor rebuilds from disk, the two
NAMED relational queries behave correctly (one fully answerable, one honestly degraded),
derivation walks the union of the split provenance mechanisms, and the whole thing stays
CPU-only + embedding-free (P4 owns the real index, R137).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from orchestrator.config import CONFIG


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1,P2")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


_CELL_A = {"shot_size": "portrait", "angle": "front",
           "expression": "neutral", "background": ""}
_CELL_B = {"shot_size": "waist_up", "angle": "profile_left",
           "expression": "smile", "background": ""}


def _asset_with_refs(client, *, style_ids=(None, None)):
    """A curated 2-ref version; `style_ids` sets each kept ref's resolved L1 style so the
    style-provenance query can be exercised both ways."""
    from PIL import Image

    from orchestrator.runner import RUNNER

    ws = RUNNER.workspace
    a = client.post("/assets", json={"name": "Mara"}).json()["profile"]
    vid = a["active_version"]
    RUNNER.pause()
    out_dir = ws.out_dir / "job_fg"
    out_dir.mkdir(parents=True, exist_ok=True)
    names, meta = [], {}
    for i, (cell, sid) in enumerate(zip((_CELL_A, _CELL_B), style_ids)):
        Image.new("L", (32, 32), color=40 * (i + 1)).save(out_dir / f"r{i}.png")
        name = f"job_fg/r{i}.png"
        names.append(name)
        meta[name] = {"coverage_cell": cell, "seed": i,
                      **({"style_id": sid} if sid else {})}
    jid = RUNNER.submit(pipeline="flux2", mode="ref",
                        params={"prompt": "d", "batch_items": [{}, {}]},
                        batch_id="bat_fg", index=0, batch_size=1,
                        requester_id=vid, profile_version_id=vid, stage="B")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": names[0],
                                  "output_names": names, "output_meta": meta}
    for n in names:
        assert client.post(f"/assets/{a['id']}/refs/keep",
                           json={"job_id": jid, "output": n}).status_code == 200
    return a, vid


def test_facts_rebuild_from_disk_and_persist_to_the_p4_path(client):
    """The extractor is a derived VIEW — rebuildable, never a source of truth — and it
    writes to `context/project_facts.jsonl`, the path kb-loom-p4.md §5 already reserves."""
    from orchestrator.runner import RUNNER

    asset, vid = _asset_with_refs(client)
    r = client.get("/context/facts", params={"rebuild": "true"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["facts"] > 0
    assert body["nodes"]["assets"] == 1 and body["nodes"]["refs"] == 2

    path = RUNNER.workspace.path / "context" / "project_facts.jsonl"
    assert path.is_file()
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    assert lines[0]["kind"] == "loom.p2.project_facts.v1"
    assert lines[0]["count"] == len(lines) - 1          # header + one fact per line
    # the spine is present: asset -> version -> ref -> cell
    triples = {(f["s"], f["p"], f["o"]) for f in lines[1:]}
    assert (asset["id"], "has_version", vid) in triples
    assert any(p == "has_ref" and s == vid for s, p, _o in triples)
    assert any(p == "in_cell" and o == "portrait__front__neutral" for _s, p, o in triples)

    # deleting it costs nothing — a rebuild reproduces the same fact count
    path.unlink()
    again = client.get("/context/facts", params={"rebuild": "true"}).json()
    assert again["facts"] == body["facts"]


def test_named_query_cells_without_kept_ref_is_fully_answerable(client):
    """Spike query 2 — the one P2's data answers with NO gaps: the frozen vocabulary
    enumerates the matrix (4 shots × 6 angles × 5 expressions = 120) and `in_cell` covers
    it, so the missing list is exact and actionable."""
    from orchestrator import coverage

    _asset, vid = _asset_with_refs(client)
    r = client.get("/context/query", params={"q": "cells_without_kept_ref",
                                             "version_id": vid})
    assert r.status_code == 200, r.text
    body = r.json()
    expected = len(coverage.SHOT_SIZES) * len(coverage.ANGLES) * len(coverage.EXPRESSIONS)
    assert body["cells_total"] == expected == 120
    assert body["cells_filled"] == 2
    assert body["cells_missing"] == expected - 2
    assert "portrait__front__neutral" not in body["missing"]      # we kept that one
    assert "full_body__back__sad" in body["missing"]

    assert client.get("/context/query",
                      params={"q": "cells_without_kept_ref"}).status_code == 400


def test_named_query_refs_using_style_reports_its_own_blind_spot(client):
    """Spike query 1 — answerable ONLY for refs that carry a style edge. Finding 1: every
    post-M2.10 flux2 expansion ref has none, because route 1 runs those sweeps with the L1
    gate off (`resolve_l1` returns a null id) and the style arrives via the hero reference.
    An empty list would be a lie, so the query names the unattributed refs instead."""
    _asset, _vid = _asset_with_refs(client, style_ids=("sty_x", None))
    body = client.get("/context/query", params={"q": "refs_using_style",
                                                "style_id": "sty_x"}).json()
    assert len(body["refs"]) == 1                     # the one that carried an id
    assert body["refs_total"] == 2 and body["refs_with_a_style_edge"] == 1
    assert len(body["unattributed"]) == 1             # the gate-off one, named not hidden
    assert "hero" in body["note"]

    # a corpus where NO ref carries a style edge is reported as DEGRADED, not as "none"
    from orchestrator import factgraph
    from orchestrator.runner import RUNNER
    facts = [f for f in factgraph.build(RUNNER.workspace) if f["p"] != "used_style"]
    rep = factgraph.refs_using_style(facts, "sty_x")
    assert rep["refs"] == [] and rep["refs_with_a_style_edge"] == 0
    assert len(rep["unattributed"]) == 2


def test_derivation_walks_the_union_of_the_split_provenance_mechanisms(client):
    """Finding 2: derivation lives in THREE places and no one of them is complete —
    `postproc_stacks.json` steps, the `[X postproc of Y]` prompt string, and
    `job.chained_from` (schema-present, populated nowhere in the real project). The walk
    must union the first two; on the author's rig that union is exactly what lets the
    starred hero reach its origin (hop 1 stack/resize, hop 2 prompt/clean)."""
    from orchestrator import factgraph
    from orchestrator.runner import RUNNER

    _asset_with_refs(client)
    ws = RUNNER.workspace
    (ws.path / "postproc_stacks.json").write_text(json.dumps({"stacks": [
        {"base": "job_a/src.png", "steps": [
            {"id": "pps_1", "preset": "clean", "source": "job_a/src.png",
             "output": "job_b/mid.png", "job_id": "job_b", "status": "done"}]}]}),
        encoding="utf-8")
    # …and a SECOND hop recorded only as a prompt convention
    jid = RUNNER.submit(pipeline="zimage", mode="img2img",
                        params={"prompt": "[resize postproc of job_b/mid.png]"},
                        batch_id="bat_x", index=0, batch_size=1)
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": "job_c/top.png",
                                  "output_names": ["job_c/top.png"]}
    # the extractor reads the PERSISTED queue by design (a rebuildable view of disk, never
    # of live runner memory), so flush before asking it what the workspace contains
    with RUNNER._lock:
        RUNNER._persist_locked()

    facts = factgraph.build(ws)
    vias = {(f.get("attrs") or {}).get("via") for f in facts if f["p"] == "derived_from"}
    assert vias == {"postproc_stack", "prompt_convention"}

    walk = factgraph.derivation_chain(facts, "job_c/top.png")
    assert walk["hops"] == 2 and walk["origin"] == "job_a/src.png"
    assert [c["via"] for c in walk["chain"]] == ["prompt_convention", "postproc_stack"]

    # …and the style is still unresolvable through it — the gap P4's index must close
    st = factgraph.style_of_output(facts, "job_c/top.png")
    assert st["resolved"] is False and st["style_id"] is None


def test_spike_report_states_which_queries_the_data_can_answer(client):
    """The spike's deliverable is a VERDICT, not an index: the report must say plainly
    which named queries work and which are degraded, so P4 inherits the finding."""
    _asset_with_refs(client)
    body = client.get("/context/facts").json()
    q = body["queries"]
    assert q["cells_without_kept_ref"] == "answerable"
    assert "DEGRADED" in q["refs_using_style"]          # no style edges in this corpus
    assert "DEGRADED" in q["style_of_output"]
    assert body["style_provenance"]["with_style_edge"] == 0
    assert body["derivation_edges"]["total"] == 0        # nothing chained in this fixture

    assert client.get("/context/query", params={"q": "nope"}).status_code == 400


def test_spike_is_cpu_only_and_embedding_free(client):
    """R137/R170: the embedding model ships in P4 and the retrieval index uses that same
    family, so anything vector-shaped here would be throwaway. The spike must not import a
    model, and must not pretend to do similarity."""
    import inspect

    from orchestrator import factgraph

    src = inspect.getsource(factgraph)
    for forbidden in ("torch", "insightface", "sentence_transformers",
                      "embed(", "cosine", "faiss", "chromadb"):
        assert forbidden not in src, f"spike must stay embedding-free — found {forbidden!r}"
