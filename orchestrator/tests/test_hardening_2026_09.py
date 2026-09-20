"""Hardening pass, review 2026-09-20 (no GPU — the card is out on RMA).

Four groups from the four-slice code review, each pinned here so it cannot come back:

1. **Externally reachable file writes** — the bundle-import destination guard
   (`assets._bundle_member_dest`), the pose-icon key gate on delete/serve, the token no
   longer baked into a production bundle.
2. **Durability on a machine that has lost power dozens of times** — fsync on every text
   store, atomic promote, a lock on the lineage index, the version-record lock carried into
   the P2 mutators, a content-based `dataset_hash`, seed-from-parent reading the record.
3. **Queue/runner races** — the dispatch-window cancel, a runner error reaping its live
   worker, graceful shutdown felling the tree, per-image delete not mutating served dicts.
4. **The tombstone rollout, finished** — a done step re-fires by REPLACING its image (and
   is refused while anything derives from it), tombstones are not branch points, tombstones
   collapse once their last descendant goes, `/rerun` keeps provenance, and the flat grid /
   Sandbox / postproc panel read `deleted` like the grouped view already did.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.config import CONFIG

APP = Path(__file__).resolve().parents[2] / "frontends" / "v1" / "src"
TAURI = Path(__file__).resolve().parents[2] / "frontends" / "shell" / "src-tauri" / "src"
SHARED = Path(__file__).resolve().parents[2] / "frontends" / "shared"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1,P2")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def _sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def _done_job(pipeline="zimage", mode="t2i", *, outputs=None, **kw) -> str:
    """A terminal job on the singleton runner (paused so nothing dispatches)."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    jid = RUNNER.submit(pipeline=pipeline, mode=mode, params={"prompt": "x"},
                        batch_id=kw.pop("batch_id", "bat_h"), index=0, batch_size=1, **kw)
    names = list(outputs or [])
    ws = RUNNER.workspace
    for n in names:
        p = ws.out_dir / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {
        "ok": True, "output_name": names[0] if names else None, "output_names": names,
        "output_meta": {n: {"seed": i} for i, n in enumerate(names)},
    }
    return jid


def _base_image(base="job_base01/base.png", prompt="a portrait"):
    """test_postproc_stack's helper: a base image on disk + its completed producing job."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    p = RUNNER.workspace.out_dir / base
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": prompt},
                        batch_id="bat_pp", index=0, batch_size=1, requester_id="sandbox")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": base, "output_names": [base]}
    return base


def _complete(jid, output):
    """Drive a queued step's job to done (+ the output file) and fire the observer."""
    from orchestrator.runner import RUNNER
    p = RUNNER.workspace.out_dir / output
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": output, "output_names": [output]}
    RUNNER._observer(RUNNER.jobs[jid])


def _stacks(client):
    return client.get("/postproc/stacks").json()["stacks"]


# =====================================================================================
# 1. Externally reachable file writes
# =====================================================================================

def test_bundle_member_destination_never_leaves_staging(tmp_path):
    """The raw-name guard passed `asset//etc/x` (parts normalise to asset/etc/x), but the
    stripped `/etc/x` re-roots to the drive under pathlib's `/`. Every re-rooting shape is
    refused; a normal member lands inside staging."""
    from orchestrator import assets, workspace as ws_mod
    staging = tmp_path / "stg"
    staging.mkdir()
    for bad in ("asset//etc/x", "asset/D:/evil/x", "asset/\\\\srv\\share\\x", "asset/\\x",
                "asset/a/../../x", "asset/x:stream", "asset/", "asset/C:x"):
        with pytest.raises(ws_mod.WorkspaceError):
            assets._bundle_member_dest(staging, bad)
    ok = assets._bundle_member_dest(staging, "asset/versions/v1/version.json")
    assert ok.resolve().is_relative_to(staging.resolve())
    assert ok == staging / "versions" / "v1" / "version.json"


def test_import_refuses_a_bundle_that_escapes_staging(client):
    from orchestrator.runner import RUNNER
    a = client.post("/assets", json={"name": "Mara"}).json()["profile"]
    blob = client.get(f"/assets/{a['id']}/export").content
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as zin, zipfile.ZipFile(out, "w") as zout:
        for zi in zin.infolist():
            zout.writestr(zi, zin.read(zi.filename))
        zout.writestr("asset//loom_pwned.txt", b"owned")     # passes the raw-name check
    r = client.post("/assets/import", content=out.getvalue(),
                    headers={"Content-Type": "application/zip"})
    assert r.status_code == 400 and "unsafe bundle member" in r.text
    ws = RUNNER.workspace
    anchor = Path(ws.temp_dir.resolve().anchor)
    assert not (anchor / "loom_pwned.txt").exists()
    assert not list(ws.temp_dir.glob("**/loom_pwned.txt"))
    # the staging dir was cleaned up on refusal
    assert not [p for p in ws.temp_dir.iterdir() if p.is_dir() and p.name.startswith("tmp")]


def test_pose_icon_key_is_validated_on_delete_and_serve(client):
    """Only the setter validated the key; delete/serve globbed the raw key, and `glob`
    walks `..`. An encoded traversal now 404s and touches nothing."""
    from orchestrator import bible, workspace as ws_mod
    from orchestrator.runner import RUNNER
    for bad in ("../x", "..\\..\\project", "a__b", "*", "portrait__front__neutral/../x"):
        with pytest.raises(ws_mod.WorkspaceError):
            bible._check_pose_key(bad)
    assert bible._check_pose_key("portrait__front__neutral") == "portrait__front__neutral"
    proj = RUNNER.workspace.project_json
    assert proj.is_file()
    r = client.delete("/bible/poses/..%5C..%5Cproject/icon")
    assert r.status_code == 404 and "invalid pose key" in r.text
    assert proj.is_file()
    r = client.delete("/bible/poses/..%5C..%5C*/icon")
    assert r.status_code == 404
    assert proj.is_file()
    r = client.get("/bible/poses/..%5C..%5Cjobs%5Cqueue/file")
    assert r.status_code == 404


def test_production_bundle_carries_no_token_fallback():
    """`import.meta.env?.X` makes Vite inline the WHOLE env object — token included — into
    the built bundle. Only per-key `import.meta.env.KEY` reads remain, and the token read is
    inside a DEV-only branch, so a production build contains no fallback at all."""
    for f in ("api/orchestrator.ts", "api/log.ts"):
        src = (SHARED / f).read_text(encoding="utf-8")
        assert "import.meta.env?." not in src, f
    orch = (SHARED / "api/orchestrator.ts").read_text(encoding="utf-8")
    assert "if (import.meta.env.DEV)" in orch
    assert "import.meta.env.VITE_LOOM_ORCH_TOKEN" in orch
    rs = (TAURI / "lib.rs").read_text(encoding="utf-8")
    assert ".on_page_load(" in rs and "inject_script(" in rs


# =====================================================================================
# 2. Durability
# =====================================================================================

def test_atomic_text_writer_fsyncs_and_leaves_no_temp(tmp_path, monkeypatch):
    import os
    from orchestrator import workspace as ws_mod
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd)))
    target = tmp_path / "deep" / "captions.jsonl"
    ws_mod.atomic_write_text(target, "a\nb\n")
    assert target.read_text(encoding="utf-8") == "a\nb\n"
    assert synced, "the text writer must fsync before the rename"
    assert not list(target.parent.glob("*.tmp"))
    # the trainer's text writer + the factgraph writer both go through it now
    from orchestrator import training
    src = Path(training.__file__).read_text(encoding="utf-8")
    assert "ws_mod.atomic_write_text(path, text)" in src
    fsrc = Path(ws_mod.__file__).with_name("factgraph.py").read_text(encoding="utf-8")
    assert "atomic_write_text" in fsrc and ".jsonl.tmp" not in fsrc


def test_atomic_writers_use_per_writer_temp_names(tmp_path):
    from orchestrator import workspace as ws_mod
    p = tmp_path / "index.json"
    a, b = ws_mod._tmp_for(p), ws_mod._tmp_for(p)
    assert a != b and a.parent == p.parent and a.name.startswith("index.json.")
    ws_mod.atomic_write_json(p, {"n": 1})
    assert json.loads(p.read_text(encoding="utf-8")) == {"n": 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_copy_keeps_the_old_file_when_the_rename_fails(tmp_path, monkeypatch):
    import os
    from orchestrator import workspace as ws_mod
    src, dst = tmp_path / "new.bin", tmp_path / "live.bin"
    src.write_bytes(b"new-adapter")
    dst.write_bytes(b"old-good-adapter")
    real = os.replace

    def boom(a, b):
        if str(b).endswith("live.bin"):
            raise OSError("simulated cut")
        return real(a, b)
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        ws_mod.atomic_copy(src, dst)
    assert dst.read_bytes() == b"old-good-adapter"
    assert not list(tmp_path.glob("*.tmp"))
    monkeypatch.setattr(os, "replace", real)
    ws_mod.atomic_copy(src, dst)
    assert dst.read_bytes() == b"new-adapter"


def test_lineage_index_survives_concurrent_writers(client):
    """`record_output` (worker thread) and `remove_edge` (API thread) each load→modify→write
    the same index; unserialized, one tore the other and the loader reset it to EMPTY."""
    from orchestrator import lineage
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace

    def job(i, n):
        return {"id": f"job_{n}{i:03d}", "requester_id": "sandbox",
                "result": {"output_names": [f"job_{n}{i:03d}/a.png"], "manifest_path": None}}
    for i in range(30):
        lineage.record_output(ws, job(i, "b"))          # what thread B will remove
    errors = []

    def writer_a():
        try:
            for i in range(30):
                lineage.record_output(ws, job(i, "a"))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def writer_b():
        try:
            for i in range(30):
                lineage.remove_edge(ws, f"job_b{i:03d}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ta, tb = threading.Thread(target=writer_a), threading.Thread(target=writer_b)
    ta.start(); tb.start(); ta.join(); tb.join()
    assert not errors, errors
    idx = lineage.load_index(ws)
    ids = {e["job_id"] for e in idx["edges"]}
    assert ids == {f"job_a{i:03d}" for i in range(30)}
    assert not list(ws.lineage_index.parent.glob("*.tmp"))


def test_per_image_delete_drops_only_that_lineage_edge(client):
    from orchestrator import lineage
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    jid = _done_job(outputs=["job_li/a.png", "job_li/b.png"])
    lineage.record_output(ws, RUNNER.jobs[jid])
    assert len(lineage.load_index(ws)["edges"]) == 2
    assert RUNNER.delete_output(jid, "job_li/a.png") == "output"
    edges = lineage.load_index(ws)["edges"]
    assert [e["output_file"] for e in edges] == ["job_li/b.png"]


def test_p2_mutators_take_the_version_lock(client):
    """Every version.json mutator outside assets.py now runs under `_VERSION_LOCK` — proven
    by holding the lock and watching a caption edit wait for it."""
    from orchestrator import assets, readiness, training
    for fn in (training.set_caption_override, training.clear_caption_overrides,
               training.stage_lora, training.promote_lora, readiness.persist):
        assert getattr(fn, "__wrapped__", None) is not None, fn.__name__
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a = client.post("/assets", json={"name": "Mara"}).json()["profile"]
    jid = _done_job(mode="img2img", outputs=["job_cap/r0.png"],
                    requester_id=a["active_version"], profile_version_id=a["active_version"],
                    stage="B")
    cell = {"shot_size": "portrait", "angle": "front", "expression": "neutral", "background": ""}
    RUNNER.jobs[jid]["result"]["output_meta"] = {"job_cap/r0.png": {"coverage_cell": cell}}
    r = client.post(f"/assets/{a['id']}/refs/keep", json={"job_id": jid, "output": "job_cap/r0.png"})
    assert r.status_code == 200, r.text
    ref_id = r.json()["ref"]["id"] if "ref" in r.json() else None
    if ref_id is None:
        _vdir, v = assets.resolve_version_dir(ws, a["id"])
        ref_id = v["ref_set"][0]["id"]
    done = threading.Event()

    def edit():
        training.set_caption_override(ws, a["id"], ref_id, "edited caption")
        done.set()
    assets._VERSION_LOCK.acquire()
    try:
        t = threading.Thread(target=edit, daemon=True)
        t.start()
        assert not done.wait(0.4), "the edit must wait for the records lock"
    finally:
        assets._VERSION_LOCK.release()
    assert done.wait(5)
    _vdir, v = assets.resolve_version_dir(ws, a["id"])
    assert v["caption_overrides"][ref_id]["caption"] == "edited caption"


def test_dataset_hash_is_content_based(tmp_path):
    from orchestrator import training
    d1, d2 = tmp_path / "stg_a" / "dataset", tmp_path / "stg_b" / "dataset"
    for d in (d1, d2):
        d.mkdir(parents=True)
        (d / "r0.txt").write_text("mara_lw, portrait\n", encoding="utf-8")
        (d / "r1.txt").write_text("mara_lw, full body\n", encoding="utf-8")

    def manifest(d, order):
        return {"files": [{"ref_id": f"ref_{i}", "image": str(d / f"r{i}.png"),
                           "caption": str(d / f"r{i}.txt"), "image_sha256": f"sha{i}"}
                          for i in order]}
    assert training._dataset_content_hash(manifest(d1, [0, 1])) == \
        training._dataset_content_hash(manifest(d2, [1, 0]))   # paths + order don't matter
    (d2 / "r1.txt").write_text("mara_lw, waist up\n", encoding="utf-8")
    assert training._dataset_content_hash(manifest(d1, [0, 1])) != \
        training._dataset_content_hash(manifest(d2, [0, 1]))   # a caption edit does


def test_seed_from_parent_reads_the_promoted_record_not_sort_order(client):
    """v1 was promoted for sd35 but still holds an older zimage adapter file; by sort order
    `..._sd35` < `..._zimage`, so the old picker ALWAYS returned the zimage file. The record
    wins now — and a family mismatch or a changed file is refused, not silently used."""
    from orchestrator import assets, training, workspace as ws_mod
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    a = client.post("/assets", json={"name": "Mara"}).json()["profile"]
    vdir, v1 = assets.resolve_version_dir(ws, a["id"])
    (vdir / "lora").mkdir(parents=True, exist_ok=True)
    (vdir / "lora" / "loom_mara_v1_base_zimage.safetensors").write_bytes(b"zimage-weights")
    sd = vdir / "lora" / "loom_mara_v1_base_sd35.safetensors"
    sd.write_bytes(b"sd35-weights")
    v1["lora"] = {"file": sd.name, "sha256": hashlib.sha256(b"sd35-weights").hexdigest(),
                  "manifest": "lora/lora.manifest.json", "base_family": "sd35",
                  "trigger_token": "mara_lw", "lora_weight_default": 1.0,
                  "promoted_at": datetime.now(timezone.utc).isoformat(), "job_id": "job_x"}
    assets.write_version(vdir, v1)
    v2 = client.post(f"/assets/{a['id']}/versions",
                     json={"name": "v2", "parent_version_id": v1["id"]}).json()
    v2_id = v2.get("id") or (v2.get("version") or {}).get("id")
    _d2, v2rec = assets.resolve_version_dir(ws, a["id"], v2_id)
    assert training._resolve_parent_lora(ws, a["id"], v2rec, base_family="sd35") == sd
    with pytest.raises(ws_mod.WorkspaceError, match="trained for 'sd35'"):
        training._resolve_parent_lora(ws, a["id"], v2rec, base_family="zimage")
    sd.write_bytes(b"sd35-weights-CHANGED")
    with pytest.raises(ws_mod.WorkspaceError, match="sha256"):
        training._resolve_parent_lora(ws, a["id"], v2rec, base_family="sd35")


# =====================================================================================
# 3. Queue / runner races
# =====================================================================================

def test_cancel_during_dispatch_window_kills_the_worker(client):
    """`running` is set before the process exists; a cancel in that window found no handle
    and was ignored. Registration now re-checks and fells the freshly spawned worker."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "x"},
                        batch_id="bat_cw", index=0, batch_size=1)
    RUNNER.jobs[jid]["status"] = "running"          # the dispatch window: no proc yet
    assert RUNNER.cancel(jid) is True
    assert jid in RUNNER._canceled and RUNNER.jobs[jid]["status"] == "running"
    proc = _sleeper()
    try:
        assert RUNNER._register_proc(jid, proc, None) is True
        proc.wait(timeout=20)
        assert proc.poll() is not None, "the late cancel must fell the worker"
    finally:
        if proc.poll() is None:
            proc.kill()
        with RUNNER._lock:
            RUNNER._procs.pop(jid, None)
            RUNNER._canceled.discard(jid)
            RUNNER.jobs[jid]["status"] = "canceled"
            RUNNER._persist_locked()


def test_runner_error_reaps_the_live_worker(client):
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "x"},
                        batch_id="bat_re", index=0, batch_size=1)
    RUNNER.jobs[jid]["status"] = "running"
    proc = _sleeper()
    try:
        assert RUNNER._register_proc(jid, proc, None) is False
        RUNNER._reap_after_runner_error(jid, RuntimeError("log write: disk full"))
        assert proc.poll() is not None, "the orphaned worker must be felled"
        j = RUNNER.jobs[jid]
        assert j["status"] == "failed" and "disk full" in j["result"]["error"]
        assert jid not in RUNNER._procs and jid not in RUNNER._cancel_jobs
    finally:
        if proc.poll() is None:
            proc.kill()


def test_graceful_shutdown_fells_the_tree_through_the_job_object(client, monkeypatch):
    from orchestrator import runner as runner_mod
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": "x"},
                        batch_id="bat_gs", index=0, batch_size=1)
    RUNNER.jobs[jid]["status"] = "running"
    proc = _sleeper()
    handle = object()
    seen = []
    monkeypatch.setattr(runner_mod, "_terminate_job", lambda h: (seen.append(h), True)[1])
    try:
        with RUNNER._lock:
            RUNNER._procs[jid] = proc
            RUNNER._cancel_jobs[jid] = handle
        RUNNER.graceful_shutdown()
        assert seen == [handle], "shutdown must use the per-job Job Object, not terminate()"
    finally:
        if proc.poll() is None:
            proc.kill()
        with RUNNER._lock:
            RUNNER._shutting_down = False
            RUNNER._procs.pop(jid, None)
            RUNNER._cancel_jobs.pop(jid, None)
            RUNNER.jobs[jid]["status"] = "canceled"
            RUNNER._persist_locked()


def test_per_image_delete_does_not_mutate_a_served_snapshot(client):
    from orchestrator.runner import RUNNER
    jid = _done_job(outputs=["job_sn/a.png", "job_sn/b.png"])
    served = RUNNER.snapshot()[jid]["result"]["output_meta"]     # what /jobs is encoding
    assert RUNNER.delete_output(jid, "job_sn/a.png") == "output"
    assert "job_sn/a.png" in served                                # untouched
    assert "job_sn/a.png" not in RUNNER.jobs[jid]["result"]["output_meta"]
    assert RUNNER.jobs[jid]["result"]["output_names"] == ["job_sn/b.png"]


# =====================================================================================
# 4. The tombstone rollout, finished
# =====================================================================================

def test_refiring_a_done_step_with_dependants_is_refused(client):
    from orchestrator.runner import RUNNER
    base = _base_image()
    s1 = client.post("/postproc/step", json={"base": base, "preset": "clean"}).json()["stacks"][0]["steps"][0]
    j1 = client.post(f"/postproc/step/{s1['id']}/queue", json={}).json()["stacks"][0]["steps"][0]["job_id"]
    _complete(j1, f"{j1}/clean.png")
    s2 = client.post("/postproc/step", json={"base": base, "preset": "refine",
                                             "source": f"{j1}/clean.png"}).json()["stacks"][0]["steps"][1]
    assert s2["source"] == f"{j1}/clean.png"
    r = client.post(f"/postproc/step/{s1['id']}/queue", json={})
    assert r.status_code == 409 and "branch from this step" in r.text
    assert RUNNER.jobs[j1]["status"] == "done"                     # nothing was destroyed
    assert (RUNNER.workspace.out_dir / j1 / "clean.png").is_file()


def test_refiring_a_leaf_step_replaces_its_image(client):
    from orchestrator.runner import RUNNER
    base = _base_image()
    s1 = client.post("/postproc/step", json={"base": base, "preset": "clean"}).json()["stacks"][0]["steps"][0]
    j1 = client.post(f"/postproc/step/{s1['id']}/queue", json={}).json()["stacks"][0]["steps"][0]["job_id"]
    _complete(j1, f"{j1}/clean.png")
    r = client.post(f"/postproc/step/{s1['id']}/queue", json={})
    assert r.status_code == 200, r.text
    step = r.json()["stacks"][0]["steps"][0]
    assert step["status"] == "queued" and step["job_id"] != j1 and step["output"] is None
    assert j1 not in RUNNER.jobs, "the replaced image's job is gone (a leaf, no tombstone)"
    assert not (RUNNER.workspace.out_dir / j1).exists()
    assert RUNNER.jobs[step["job_id"]]["status"] == "queued"


def test_a_tombstoned_step_is_not_a_branch_point(client):
    from orchestrator import postproc, workspace as ws_mod
    from orchestrator.runner import RUNNER
    ws = RUNNER.workspace
    base = _base_image()
    s1 = client.post("/postproc/step", json={"base": base, "preset": "clean"}).json()["stacks"][0]["steps"][0]
    j1 = client.post(f"/postproc/step/{s1['id']}/queue", json={}).json()["stacks"][0]["steps"][0]["job_id"]
    out1 = f"{j1}/clean.png"
    _complete(j1, out1)
    s2 = client.post("/postproc/step", json={"base": base, "preset": "refine", "source": out1}).json()["stacks"][0]["steps"][1]
    j2 = client.post(f"/postproc/step/{s2['id']}/queue", json={}).json()["stacks"][0]["steps"][1]["job_id"]
    _complete(j2, f"{j2}/refine.png")
    assert RUNNER.delete(j1) is True and RUNNER.jobs[j1]["deleted"] is True   # tombstone
    steps = _stacks(client)[0]["steps"]
    assert steps[0]["deleted"] is True and steps[0]["output"] == out1
    # not offered as a source, not accepted as one, and "continue" skips it
    assert out1 not in {s["output"] for s in postproc.stack_sources(ws, base)}
    r = client.post("/postproc/step", json={"base": base, "preset": "clean", "source": out1})
    assert r.status_code == 409 and "cannot branch" in r.text
    r = client.post("/postproc/step", json={"base": base, "preset": "clean"})
    assert r.status_code == 200
    assert r.json()["stacks"][0]["steps"][-1]["source"] == f"{j2}/refine.png"


def test_only_tombstones_left_means_continue_from_the_base(client):
    from orchestrator.runner import RUNNER
    base = _base_image()
    s1 = client.post("/postproc/step", json={"base": base, "preset": "clean"}).json()["stacks"][0]["steps"][0]
    j1 = client.post(f"/postproc/step/{s1['id']}/queue", json={}).json()["stacks"][0]["steps"][0]["job_id"]
    _complete(j1, f"{j1}/clean.png")
    s2 = client.post("/postproc/step", json={"base": base, "preset": "refine"}).json()["stacks"][0]["steps"][1]
    j2 = client.post(f"/postproc/step/{s2['id']}/queue", json={}).json()["stacks"][0]["steps"][1]["job_id"]
    _complete(j2, f"{j2}/refine.png")
    RUNNER.delete(j1)                     # tombstone (j2 derives from it)
    RUNNER.delete(j2)                     # leaf → gone, and j1's tombstone collapses with it
    assert j2 not in RUNNER.jobs and j1 not in RUNNER.jobs
    stacks = _stacks(client)              # reconcile: both steps gone, base alive → stack kept
    assert stacks and stacks[0]["steps"] == []
    r = client.post("/postproc/step", json={"base": base, "preset": "clean"})
    assert r.status_code == 200 and r.json()["stacks"][0]["steps"][0]["source"] == base


def test_tombstones_collapse_when_the_last_descendant_goes(client):
    from orchestrator.runner import RUNNER
    a = _done_job(outputs=["job_a/a.png"])
    b = _done_job(mode="img2img", outputs=["job_b/b.png"], chained_from=a, pass_name="clean")
    c = _done_job(mode="img2img", outputs=["job_c/c.png"], chained_from=b, pass_name="resize")
    assert RUNNER.delete(a) and RUNNER.jobs[a]["deleted"]
    assert RUNNER.jobs[a]["result"]["manifest_path"] is None
    assert RUNNER.delete(b) and RUNNER.jobs[b]["deleted"]
    assert RUNNER.delete(c)
    assert c not in RUNNER.jobs and b not in RUNNER.jobs and a not in RUNNER.jobs
    # parent-first (the group-delete order) with a live sibling: the parent stays a tombstone
    # exactly as long as something still derives from it
    p = _done_job(outputs=["job_p/p.png"])
    k1 = _done_job(mode="img2img", outputs=["job_k1/k.png"], chained_from=p, pass_name="clean")
    k2 = _done_job(mode="img2img", outputs=["job_k2/k.png"], chained_from=p, pass_name="clean")
    RUNNER.delete(p); RUNNER.delete(k1)
    assert RUNNER.jobs[p]["deleted"] and k1 not in RUNNER.jobs
    RUNNER.delete(k2)
    assert p not in RUNNER.jobs and k2 not in RUNNER.jobs
    persisted = json.loads(RUNNER.workspace.queue_path.read_text(encoding="utf-8"))
    ids = {j["id"] for j in (persisted.get("jobs") or persisted.get("queue") or [])} \
        if isinstance(persisted, dict) else set()
    assert not ids & {a, b, c, p, k1, k2}


def test_rerun_carries_provenance(client):
    from orchestrator.runner import RUNNER
    parent = _done_job(outputs=["job_rp/p.png"])
    src = _done_job(mode="img2img", outputs=["job_rs/s.png"], chained_from=parent,
                    pass_name="clean", style_id="sty_abc")
    r = client.post(f"/jobs/{src}/rerun", json={})
    assert r.status_code == 200, r.text
    new = RUNNER.jobs[r.json()["job_id"]]
    assert new["chained_from"] == parent and new["pass"] == "clean" and new["style_id"] == "sty_abc"
    assert RUNNER.has_descendants(parent)


def test_frontend_reads_tombstones_everywhere_the_grouped_view_did():
    """The 2026-08-09 tombstone semantics reached the server and GroupedGrid only. The flat
    grid, the Sandbox scope, the postproc panel and the branch picker read `deleted` now."""
    app = (APP / "App.tsx").read_text(encoding="utf-8")
    assert "if (job?.deleted) return [];" in app                        # flat grid: no tile
    assert "st.output && !st.deleted" in app                            # branch picker
    assert 'if (st.deleted) return "deleted";' in app                   # panel status
    assert "forgetGoneJobs" in app and "!ids.includes(b) || !!r.jobs[b]" in app   # sandbox scope
    assert 'stage === "A" ? "A" : stage === "D" ? "D" : "B") : undefined' in app  # Stage D routing
    gg = (APP / "GroupedGrid.tsx").read_text(encoding="utf-8")
    assert "r.children.length === 0 && !r.job.deleted" in gg
    css = (APP / "styles.css").read_text(encoding="utf-8")
    assert ".pp-status.pp-deleted" in css


def test_frontend_step_budget_mirrors_the_exact_semantics():
    from orchestrator import model_catalog as mc
    app = (APP / "App.tsx").read_text(encoding="utf-8")
    m = re.search(r'const I2I_EXACT_BACKENDS = new Set\(\[(.*?)\]\);', app)
    assert m, "the FE must name the exact-semantics backends so they can be pinned"
    fe = {x.strip().strip('"') for x in m.group(1).split(",") if x.strip()}
    be = {k for k, v in mc._I2I_STEP_SEMANTICS.items() if v == "exact"}
    assert fe == be
    assert "I2I_EXACT_BACKENDS.has(b)" in app
