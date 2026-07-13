"""P2/M7 — the acceptance NARRATIVE (no GPU): the §1 done-line walked in order.

    P1 character → template-captioned (edited) → readiness ✓ → STAGED → added to queue
    → trained → PROMOTED → test-gen queued with the LoRA — with caption_policy_hash +
    context_digest present in the training manifest.

This is the contract walk the author's rig acceptance repeats with a real GPU train
(procedure: journal "M7"). Every transition here is the real API — the only substitution
is the GPU step itself (the trainer job is hand-finished exactly the way the wrapper
finishes it, the pattern the M2/M6 suites established).
"""

from __future__ import annotations

import json
from pathlib import Path

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


_CELLS = [
    {"shot_size": "portrait", "angle": "front", "expression": "neutral", "background": ""},
    {"shot_size": "waist_up", "angle": "three_quarter_left", "expression": "smile", "background": ""},
    {"shot_size": "full_body", "angle": "profile_left", "expression": "serious", "background": "market"},
    {"shot_size": "portrait", "angle": "three_quarter_right", "expression": "neutral", "background": ""},
]


def _png(path: Path, kind: int) -> None:
    img = Image.new("L", (64, 64))
    img.putdata([(x * (kind + 3) + y * (kind + 1) + (x % (kind + 2)) * 41) % 256
                 for y in range(64) for x in range(64)])
    img.save(path)


def test_p2_done_line_walks_end_to_end(client):
    from orchestrator.runner import RUNNER

    ws = RUNNER.workspace
    RUNNER.pause()

    # ---- P1 character with a curated ref set (Stage B/C product) --------------------
    asset = client.post("/assets", json={"name": "Mara"}).json()["profile"]
    vid = asset["active_version"]
    out_dir = ws.out_dir / "job_p1"
    out_dir.mkdir(parents=True, exist_ok=True)
    names, meta = [], {}
    for i, cell in enumerate(_CELLS):
        name = f"job_p1/ref{i}.png"
        _png(out_dir / f"ref{i}.png", i + 1)
        names.append(name)
        meta[name] = {"coverage_cell": cell, "seed": 100 + i, "style_id": "sty_000000"}
    src = RUNNER.submit(pipeline="zimage", mode="img2img",
                        params={"prompt": "dataset", "batch_items": [{}] * len(names)},
                        batch_id="bat_p1", index=0, batch_size=1,
                        requester_id=vid, profile_version_id=vid, stage="B")
    RUNNER.jobs[src]["status"] = "done"
    RUNNER.jobs[src]["result"] = {"ok": True, "output_name": names[0],
                                  "output_names": names, "output_meta": meta}
    for name in names:
        assert client.post(f"/assets/{asset['id']}/refs/keep",
                           json={"job_id": src, "output": name}).status_code == 200

    # ---- D1: template captions, reviewed + one EDITED (M3 override layer) -----------
    caps = client.get(f"/assets/{asset['id']}/captions").json()
    assert caps["count"] == 4 and all(c["origin"] == "template" for c in caps["captions"])
    target = caps["captions"][1]["id"]
    edited = "mara_lw, three quarter left view, waist-up, smiling, carrying a satchel"
    assert client.put(f"/assets/{asset['id']}/captions/{target}",
                      json={"caption": edited}).status_code == 200

    # ---- D2: readiness ✓ persisted (advisory — recommends, never blocks) ------------
    ready = client.post(f"/assets/{asset['id']}/readiness", json={}).json()
    assert ready["coverage"]["ref_count"] == 4
    assert ready["captions"]["edited"] == 1
    version = client.get(f"/assets/{asset['id']}").json()["versions"][0]
    assert version["readiness_status"]["computed_at"]

    # ---- D3: STAGED (auto-generate, R118 — nothing on the GPU queue yet) -------------
    staged = client.post(f"/assets/{asset['id']}/lora/stage",
                         json={"trigger_token": "mara_lw", "steps": 60}).json()
    assert staged["status"] == "staged" and staged["caption_count"] == 4
    assert not any(j.get("pipeline") == "zimage_trainer" for j in RUNNER.jobs.values())
    # the staged dataset carries the EDIT (M3 → M2 handshake)
    ds = json.loads(Path(staged["dataset_manifest"]).read_text(encoding="utf-8"))
    texts = [Path(f["caption"]).read_text(encoding="utf-8").strip() for f in ds["files"]]
    assert edited in texts

    # ---- explicit "Add to queue" (the FIRST moment GPU work could start) -------------
    jid = client.post(f"/training/staged/{staged['id']}/queue").json()["job_id"]
    job = RUNNER.jobs[jid]
    assert job["resumable"] is True and job["stage"] == "D"
    assert client.get("/training/staged").json()["count"] == 0

    # ---- "trained": hand-finish the run the way the wrapper does (GPU = rig-owed) ----
    params = job["params"]
    run_dir = Path(params["run_dir"])
    ckpt_dir = run_dir / params["artifact_name"].removesuffix(".safetensors")
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    artifact = ckpt_dir / params["artifact_name"]
    artifact.write_bytes(b"acceptance-lora")
    jdir = ws.out_dir / jid
    jdir.mkdir(parents=True, exist_ok=True)
    tman = jdir / "zimage_lora_train_manifest.json"
    tman.write_text(json.dumps({"status": "completed", "duration_s": 3600.0,
                                "artifact": {"path": str(artifact), "sha256": "X"}}),
                    encoding="utf-8")
    job["status"] = "done"
    job["result"] = {"ok": True, "outputs": [str(artifact)], "manifest_path": str(tman)}

    # ---- E: PROMOTE — the training manifest carries the §1 facts ---------------------
    promoted = client.post(f"/training/jobs/{jid}/promote").json()
    man = promoted["manifest"]
    assert man["caption_policy_hash"] == staged["caption_policy_hash"]   # §1 requirement
    assert man["context_digest"] == staged["context_digest"]            # §1 requirement
    assert man["captions_hash"] == staged["captions_hash"]
    assert man["dataset_hash"] and man["trigger_token"] == "mara_lw"
    detail = client.get(f"/assets/{asset['id']}").json()
    assert detail["versions"][0]["lora"]["file"].endswith(".safetensors")

    # ---- verify: test-gen queued WITH the LoRA (on-model eyeball = rig) --------------
    prev = client.post(f"/training/jobs/{jid}/preview", json={"seed": 42}).json()
    pjob = RUNNER.get(prev["job_id"])
    assert pjob["params"]["lora_path"] == str(artifact)
    assert pjob["params"]["prompt"].startswith("mara_lw")
    assert pjob["profile_version_id"] == version["id"]

    # ---- R13 close: explicit temp cleanup, version keeps the promoted copy ----------
    assert client.post(f"/training/jobs/{jid}/cleanup").json()["cleaned"] is True
    vroot = (ws.asset_dir("characters", detail["profile"]["slug"]) / "versions" / "v1_base")
    assert (vroot / "lora" / detail["versions"][0]["lora"]["file"]).is_file()
    assert not run_dir.exists()
