"""P2 post-M2.12 — durable style provenance + the double-styling fix (author 2026-08-08).

Locks the three things the author reported and the M2.12 spike had independently measured:
a generated image now RECORDS the style it ran under, a postproc step INHERITS that style
instead of restating it, and derivation has ONE authoritative edge.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from orchestrator.config import CONFIG


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1,P2")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def _style(client, name, fragment, *, activate=True):
    r = client.post("/bible/styles", json={"name": name, "fragment": fragment})
    assert r.status_code in (200, 201), r.text
    sid = next(s for s in r.json()["styles"] if s["name"] == name)["id"]
    if activate:
        assert client.post("/bible/styles/active",
                           json={"style_id": sid}).status_code == 200
    return sid


def _queue_step(client, step_id):
    """Fire a configured step and return its submitted job id (the endpoint returns the
    whole STORE; `mark_queued` stamps job_id onto the step)."""
    r = client.post(f"/postproc/step/{step_id}/queue", json={})
    assert r.status_code == 200, r.text
    for stack in r.json()["stacks"]:
        for st in stack["steps"]:
            if st["id"] == step_id:
                return st["job_id"]
    raise AssertionError(f"step {step_id} not found in the returned store")


def _add_step(client, base, preset, params=None):
    """POST /postproc/step returns the whole STORE — dig out the step just appended."""
    body = {"base": base, "preset": preset}
    if params:
        body["params"] = params
    r = client.post("/postproc/step", json=body)
    assert r.status_code == 200, r.text
    stack = next(s for s in r.json()["stacks"] if s["base"] == base)
    return stack["steps"][-1]["id"]


def _done_image(client, jid, name="job_x/img.png"):
    """Give a queued job a finished output on disk (the runner is paused in tests)."""
    from orchestrator.runner import RUNNER

    ws = RUNNER.workspace
    p = ws.out_dir / name
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), "gray").save(p)
    job = RUNNER.jobs[jid]
    job["status"] = "done"
    job["result"] = {"ok": True, "output_name": name, "output_names": [name]}
    return name


def test_a_generated_image_records_the_style_it_ran_under(client):
    """M2.12 finding 3: style provenance lived ONLY in the request, never on the artifact,
    so nothing downstream could answer "what style is this image?". Every /generate job now
    carries the RESOLVED id (None when the gate is off — that is real provenance too)."""
    from orchestrator.runner import RUNNER

    RUNNER.pause()
    sid = _style(client, "neon", "neon-drenched cyberpunk")

    r = client.post("/generate", json={"prompt": "a street", "pipeline": "zimage", "count": 1})
    assert r.status_code == 200, r.text
    job = RUNNER.get(r.json()["job_ids"][0])
    assert job["style_id"] == sid                       # the active style, stamped
    assert "neon-drenched cyberpunk" in job["params"]["prompt"]   # …and still applied (R104)

    # gate OFF → no style, and the prompt stays clean
    r2 = client.post("/generate", json={"prompt": "a street", "pipeline": "zimage",
                                        "count": 1, "apply_style": False})
    job2 = RUNNER.get(r2.json()["job_ids"][0])
    assert job2["style_id"] is None
    assert "neon" not in job2["params"]["prompt"]


def test_postproc_inherits_the_source_style_instead_of_restating_it(client):
    """The author's report: "when a style prompt is merged with the original prompt, further
    processing will not result in a clean output, as it will contain two different style
    definitions". A postproc step inherits the SOURCE's prompt — which already ends in the
    fragment that generated it — so appending the active style stacked two looks in one
    prompt. Default is now inherit-only: no second definition, and the output records the
    style it actually carries (baked into the pixels)."""
    from orchestrator.runner import RUNNER

    RUNNER.pause()
    sid = _style(client, "neon", "neon-drenched cyberpunk")
    gen = client.post("/generate", json={"prompt": "a street", "pipeline": "zimage",
                                         "count": 1}).json()["job_ids"][0]
    name = _done_image(client, gen)
    src_prompt = RUNNER.get(gen)["params"]["prompt"]
    assert src_prompt.count("neon-drenched cyberpunk") == 1

    sid_step = _add_step(client, name, "clean")
    pjob = RUNNER.get(_queue_step(client, sid_step))

    # exactly ONE style definition — inherited, never re-appended. (The job-level prompt is
    # just a "[clean postproc of …]" label; the real i2i prompt rides in batch_items.)
    item = pjob["params"]["batch_items"][0]["prompt"]
    assert item.lower().count("neon-drenched cyberpunk") == 1
    assert pjob["style_id"] == sid            # the style the output actually carries
    assert pjob["chained_from"] == gen        # the authoritative derivation edge


def test_a_deliberate_restyle_replaces_rather_than_stacks(client):
    """The per-step override the author chose: `apply_style` re-styles on purpose, and the
    INHERITED fragment is stripped first so the prompt never carries two definitions."""
    from orchestrator.runner import RUNNER

    RUNNER.pause()
    old = _style(client, "neon", "neon-drenched cyberpunk")
    new = _style(client, "pastel", "soft pastel watercolour", activate=False)
    gen = client.post("/generate", json={"prompt": "a street", "pipeline": "zimage",
                                         "count": 1, "style_id": old}).json()["job_ids"][0]
    name = _done_image(client, gen)

    step = _add_step(client, name, "clean",
                     params={"apply_style": True, "style_id": new})
    pjob = RUNNER.get(_queue_step(client, step))

    prompt = pjob["params"]["batch_items"][0]["prompt"].lower()
    assert "soft pastel watercolour" in prompt       # the new style is applied …
    assert "neon-drenched cyberpunk" not in prompt   # … and the old one is GONE, not stacked
    assert "a street" in prompt                      # the author's own wording survives
    assert pjob["style_id"] == new


def test_chained_from_is_the_authoritative_derivation_edge(client):
    """M2.12 finding 2: derivation was split across `postproc_stacks.json`, a
    `[X postproc of Y]` PROMPT STRING and `job.chained_from` — which was populated on 0 of
    661 real jobs. The manual postproc surface now records it, so the fact graph reads a
    real edge instead of reconstructing one from prose."""
    from orchestrator import factgraph
    from orchestrator.runner import RUNNER

    RUNNER.pause()
    _style(client, "neon", "neon-drenched cyberpunk")
    gen = client.post("/generate", json={"prompt": "a street", "pipeline": "zimage",
                                         "count": 1}).json()["job_ids"][0]
    name = _done_image(client, gen)
    step = _add_step(client, name, "clean")
    pj = _queue_step(client, step)
    out2 = _done_image(client, pj, name="job_y/clean.png")
    with RUNNER._lock:
        RUNNER._persist_locked()

    facts = factgraph.build(RUNNER.workspace)
    edges = [f for f in facts if f["p"] == "derived_from"]
    assert any(e["s"] == out2 and e["o"] == name
               and (e.get("attrs") or {}).get("via") == "chained_from" for e in edges)

    # …and the spike's two DEGRADED queries now resolve
    st = factgraph.style_of_output(facts, out2)
    assert st["resolved"] is True and st["via"] == "direct"
    report = factgraph.report(RUNNER.workspace)
    assert report["queries"]["style_of_output"] == "answerable"
