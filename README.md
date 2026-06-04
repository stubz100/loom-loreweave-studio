# Loreweave Studio (`loom`)

A non-linear storyboard / story-generation desktop app. Tauri (Rust shell) +
React/TypeScript UI + a Python FastAPI orchestrator that wraps the
`run_pipeline.py` generation CLIs behind a single VRAM-aware job queue.

> **Status: Phase 0 (foundation) — M0–M3 done (Phase B underway).**
> App shell + orchestrator handshake (M0), one real generation (M1), an N-image batch
> streaming into a selectable grid (M2), and the hardened adapter contract — token-gated
> `/generate`, `capabilities()`, coarse progress, **cancel = subprocess kill**, and
> manifest-status-as-truth (M3). Next: the durable resume-paused queue / bundle I/O /
> disk guard / launch gate (M4–M8). Spec:
> [`kb-loom-p0.md`](../../.github/copilot/kb-loom-p0.md), decisions:
> [`kb-storyboard01.md`](../../.github/copilot/kb-storyboard01.md) §10.0, journal:
> [`kb-loom-p0-imp.md`](../../.github/copilot/kb-loom-p0-imp.md).

## Layout

```
loom-loreweave-studio/
├── models.json        # weight manifest (R160) — what the app expects on disk + where to fetch it
├── pipelines/         # VENDORED pipeline code, per-phase (R162): zimage now; others as phases land
│   ├── _artifact_id.py
│   └── zimage/        #   run_pipeline.py + stages + manifest.py
├── app/               # Tauri 2 + React/TS desktop shell
│   ├── src/           #   React UI (three-pane shell + job-queue dock + batch grid)
│   └── src-tauri/     #   Rust: single-instance, orchestrator sidecar spawn + kill, READY handshake
└── orchestrator/      # Python FastAPI service (127.0.0.1)
    ├── main.py        #   app factory + /health /version /generate /jobs /outputs
    ├── runner.py      #   in-memory single-worker job runner (M4 → durable queue)
    ├── config.py      #   port/token + pipeline roots + interpreter (R101/R103)
    ├── adapters/      #   one module per pipeline (zimage) → JobSpec/CompletionRecord
    └── requirements.txt
```

**Pipeline code is vendored into this repo per-phase** (R162): the P0 worker
(`zimage`) lives in `pipelines/` so a clone can generate on its own. The orchestrator
resolves each pipeline **in-repo first**, falling back to the parent monorepo's
`src/pipeline/` during dev (for pipelines not vendored yet). Model **weights are never
vendored** (R160) — they stay external (HF / the parent monorepo's
`src/village_ai/models/`) and are fetched on demand.

## Dev quickstart

The orchestrator runs in the parent monorepo's shared `.venv` (R103); install its
deps once: `pip install -r orchestrator/requirements.txt`.

**1. Orchestrator** (from this repo root):

```powershell
$env:LOOM_VENV_PYTHON = "..\..\.venv\Scripts\python.exe"
..\..\.venv\Scripts\python.exe -m orchestrator.main
# prints: LOOM_ORCH_READY url=http://127.0.0.1:8765 token=…
# GET /health -> {"status":"ok",...} ; POST /generate {"prompt":"…","count":3}
```

**2. UI only** (from `app/`): `npm install` then `npm run dev` → http://localhost:1420.

**3. Full desktop app** (from `app/`): `npm run tauri dev` — boots the window, spawns
the orchestrator as a sidecar, and kills it on exit. (Requires the Rust toolchain.)

## Known gaps (P0, by milestone)

- Job state is **in-memory** (no persistence/resume/VRAM admission) until M4 — cancel
  works (M3), but the queue doesn't survive a restart yet.
- Only **t2i** is wired; img2img/inpaint (+ image inputs) arrive in **P1**.
- `models.json` companion-repo URL + sha256 are **placeholders** (R160) — filled when
  the HF companion repo is published; the on-demand HF **fetch** flow isn't built yet.
- App icon is **placeholder art**.

## Configuration

A central **`.env`** at the repo root is the master config, read by both the
orchestrator (`config.py`) and the Vite UI (`envDir: ".."`). **`.env` is committed
(non-secret only).** Secrets — the orchestrator **token** — go in a gitignored
**`.env.local`** (`LOOM_ORCH_TOKEN` + `VITE_LOOM_ORCH_TOKEN`); copy the template at the
bottom of `.env`. In the packaged Tauri app the token is generated at runtime and
injected into the webview, so no `.env.local` is needed there. Precedence: real env var
> `.env.local` > `.env` > built-in default.

**Security:** `POST /generate` requires the `X-Loom-Token` header (the no-surprise-GPU
gate, R141–143); CORS is restricted to the dev + Tauri origins. `npm run dev` sends the
token from `.env.local`.

| Var | Default | Purpose |
| --- | --- | --- |
| `LOOM_ORCH_HOST` | `127.0.0.1` | orchestrator bind host (R101) |
| `LOOM_ORCH_PORT` | `8765` | orchestrator bind port |
| `LOOM_ORCH_TOKEN` | random (dev: `.env.local`) | loopback token, enforced on `/generate` (R101) |
| `LOOM_CORS_ORIGINS` | localhost:1420 + tauri.localhost | allowed browser origins (comma-sep) |
| `LOOM_PIPELINES_DIR` | in-repo `pipelines/`, then parent `../../src/pipeline` | pipeline-code roots (vendored-first) |
| `LOOM_SRC_ROOT` | parent `../../src` | monorepo `src/` (holds `village_ai/models`, R160) |
| `LOOM_VENV_PYTHON` | current interpreter | python used to shell out to pipeline CLIs (R103) |
| `LOOM_DEV_OUT` | `<repo>/.dev_out` | smoke-output dir (→ per-project `out/` at M5) |
| `LOOM_APP_REPO` | `../..` (from `src-tauri/`) | app-repo cwd the Tauri shell spawns the orchestrator from |
| `VITE_LOOM_ORCH_URL` | `http://127.0.0.1:8765` | orchestrator URL the dev UI probes |
