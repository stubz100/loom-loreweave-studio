"""M2.14 step 7 — the Inpaint postproc preset over a painted mask, and POST /outputs/masks.

No GPU: real PNGs from PIL on disk, a paused queue, and the queue endpoint's dry run — which
returns the planned job so the mask's path into the worker is asserted, not assumed."""

from __future__ import annotations

import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from orchestrator.config import CONFIG


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_PROJECT_DIR", str(tmp_path / "proj"))
    monkeypatch.setenv("LOOM_ACTIVE_PHASES", "P0,P1")
    from orchestrator.main import app
    with TestClient(app) as c:
        c.headers.update({"X-Loom-Token": CONFIG.token})
        yield c


def _png(w: int, h: int, color=(0, 0, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _source(w=64, h=48, base="job_src01/base.png", prompt="a portrait, front view"):
    """A real PNG in out/ with a completed producing job (the inpaint step inherits its prompt)."""
    from orchestrator.runner import RUNNER
    RUNNER.pause()
    p = RUNNER.workspace.out_dir / base
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(_png(w, h, (90, 120, 200)))
    jid = RUNNER.submit(pipeline="zimage", mode="t2i", params={"prompt": prompt},
                        batch_id="bat_src", index=0, batch_size=1, requester_id="sandbox")
    RUNNER.jobs[jid]["status"] = "done"
    RUNNER.jobs[jid]["result"] = {"ok": True, "output_name": base, "output_names": [base]}
    return base


def test_mask_upload_lands_in_out_masks_atomically(client):
    from orchestrator.runner import RUNNER
    src = _source()
    r = client.post("/outputs/masks", json={"source": src, "png_base64": _b64(_png(64, 48, (255, 255, 255)))})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mask"].startswith("masks/base_") and body["mask"].endswith(".png")
    assert body["width"] == 64 and body["height"] == 48 and body["source"] == src
    path = RUNNER.workspace.out_dir / body["mask"]
    assert path.is_file() and path.read_bytes().startswith(b"\x89PNG")
    assert not list(path.parent.glob("*.tmp"))                   # temp → replace, nothing left over
    # and it is served like any output
    assert client.get(f"/outputs/{body['mask']}").status_code == 200


def test_mask_upload_refuses_bad_input(client):
    src = _source()
    assert client.post("/outputs/masks", json={"source": "../evil.png", "png_base64": _b64(_png(64, 48))}).status_code == 400
    assert client.post("/outputs/masks", json={"source": "nope/none.png", "png_base64": _b64(_png(64, 48))}).status_code == 404
    assert client.post("/outputs/masks", json={"source": src, "png_base64": "not-base64!!"}).status_code == 400
    assert client.post("/outputs/masks", json={"source": src, "png_base64": _b64(b"GIF89a" + b"\x00" * 32)}).status_code == 400
    r = client.post("/outputs/masks", json={"source": src, "png_base64": _b64(_png(32, 32))})
    assert r.status_code == 422 and "pixel for pixel" in r.text  # a mask must match its image
    # token-gated like every write
    r = client.post("/outputs/masks", json={"source": src, "png_base64": _b64(_png(64, 48))}, headers={"X-Loom-Token": "wrong"})
    assert r.status_code == 401


def test_inpaint_step_needs_a_mask_that_exists(client):
    src = _source()
    r = client.post("/postproc/step", json={"base": src, "preset": "inpaint"})
    assert r.status_code == 422 and "needs a mask" in r.text
    r = client.post("/postproc/step", json={"base": src, "preset": "inpaint", "mask": "masks/missing.png"})
    assert r.status_code == 404
    r = client.post("/postproc/step", json={"base": src, "preset": "inpaint", "mask": "../x.png"})
    assert r.status_code == 400
    mask = client.post("/outputs/masks", json={"source": src, "png_base64": _b64(_png(64, 48, (255, 255, 255)))}).json()["mask"]
    r = client.post("/postproc/step", json={"base": src, "preset": "inpaint", "mask": mask, "backend": "flux2"})
    assert r.status_code == 422 and "flux2" in r.text            # no wired inpaint mode there
    r = client.post("/postproc/step", json={"base": src, "preset": "inpaint", "mask": mask, "params": {"scale": 2}})
    assert r.status_code == 422                                   # a mask is pixel-aligned: no resize


def test_inpaint_step_persists_and_its_dry_run_carries_the_mask_into_the_job(client):
    src = _source()
    mask = client.post("/outputs/masks", json={"source": src, "png_base64": _b64(_png(64, 48, (255, 255, 255)))}).json()["mask"]
    r = client.post("/postproc/step", json={"base": src, "preset": "inpaint", "mask": mask,
                                            "params": {"prompt": "a clean hand", "strength": 0.9}})
    assert r.status_code == 200, r.text
    step = r.json()["stacks"][0]["steps"][0]
    assert step["preset"] == "inpaint" and step["mode"] == "inpaint" and step["backend"] == "sd35"
    assert step["mask"] == mask and step["requires_mask"] is True
    assert step["params"]["strength"] == 0.9 and step["params"]["prompt"] == "a clean hand"
    d = client.post(f"/postproc/step/{step['id']}/queue", json={"dry_run": True})
    assert d.status_code == 200, d.text
    plan = d.json()
    assert plan["dry_run"] is True and plan["pipeline"] == "sd35" and plan["mode"] == "inpaint"
    item = plan["params"]["batch_items"][0]
    assert item["prompt"] == "a clean hand"
    assert item["init_image"].replace("\\", "/").endswith("/" + src)
    assert item["mask_image"].replace("\\", "/").endswith("/" + mask)
    assert plan["params"]["strength"] == 0.9
    assert (plan["params"]["width"], plan["params"]["height"]) == (64, 48)   # source dims kept


def test_inpaint_defaults_and_zimage_backend(client):
    src = _source()
    mask = client.post("/outputs/masks", json={"source": src, "png_base64": _b64(_png(64, 48, (255, 255, 255)))}).json()["mask"]
    r = client.post("/postproc/step", json={"base": src, "preset": "inpaint", "mask": mask, "backend": "zimage"})
    assert r.status_code == 200, r.text
    step = r.json()["stacks"][0]["steps"][0]
    assert step["backend"] == "zimage" and step["params"]["strength"] == 0.95
    d = client.post(f"/postproc/step/{step['id']}/queue", json={"dry_run": True}).json()
    assert d["params"]["batch_items"][0]["prompt"] == "a portrait, front view"   # inherited from the source
    assert d["params"]["strength"] == 0.95


def test_inpaint_queue_refuses_when_the_mask_was_deleted(client):
    from orchestrator.runner import RUNNER
    src = _source()
    mask = client.post("/outputs/masks", json={"source": src, "png_base64": _b64(_png(64, 48, (255, 255, 255)))}).json()["mask"]
    step = client.post("/postproc/step", json={"base": src, "preset": "inpaint", "mask": mask}).json()["stacks"][0]["steps"][0]
    (RUNNER.workspace.out_dir / mask).unlink()
    d = client.post(f"/postproc/step/{step['id']}/queue", json={"dry_run": True})
    assert d.status_code == 404 and "paint it again" in d.text
