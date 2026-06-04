# Loreweave Studio (`loom`)

A non-linear storyboard / story-generation desktop app. Tauri (Rust shell) +
React/TypeScript UI + a Python FastAPI orchestrator that wraps the existing
`run_pipeline.py` generation CLIs behind a single VRAM-aware job queue.

> **Status: Phase 0 (foundation), milestone M0.** This builds the *spine* — app
> shell, orchestrator handshake, project workspace, persistent job queue, and the
> pipeline-adapter contract — proven by one real generation job end-to-end. No
> creative features yet. Spec: [`kb-loom-p0.md`](../../.github/copilot/kb-loom-p0.md),
> decisions: [`kb-storyboard01.md`](../../.github/copilot/kb-storyboard01.md) §10.0.

## Layout

```
loom-loreweave-studio/
├── models.json        # weight manifest (R160) — what the app expects on disk + where to fetch it
├── app/               # Tauri 2 + React/TS desktop shell
│   ├── src/           #   React UI (three-pane shell + job-queue dock)
│   └── src-tauri/     #   Rust: single-instance, orchestrator sidecar spawn, READY handshake
└── orchestrator/      # Python FastAPI service (127.0.0.1)
    ├── main.py        #   app factory + /health, /version  (M0)
    ├── config.py      #   port/token + pipelines/models root + interpreter (R101/R103)
    └── requirements.txt
```

Pipeline CLIs and model **weights stay in the parent monorepo** (`../../src/`),
referenced via the configurable pipelines root — **not vendored** (R162). Weights
**never** live in git (R160).

## Dev quickstart

The orchestrator runs in the parent monorepo's shared `.venv` (R103).

**1. Orchestrator** (from this repo root):

```powershell
$env:LOOM_VENV_PYTHON = "..\..\.venv\Scripts\python.exe"
..\..\.venv\Scripts\python.exe -m orchestrator.main
# prints: LOOM_ORCH_READY url=http://127.0.0.1:8765 token=…
# then GET http://127.0.0.1:8765/health  ->  {"status":"ok",...}
```

**2. UI** (from `app/`): `npm install` then `npm run dev` → http://localhost:1420
(the shell probes `/health` and shows orchestrator status).

**3. Full desktop app** (`npm run tauri dev`) **requires the Rust toolchain**
(`rustup`), which is **not yet installed** on the dev box — see *Known gaps*.

## Known gaps (M0)

- **Rust toolchain not installed** → the Tauri window can't be built/booted yet.
  The `src-tauri/` layer is written build-ready (single-instance R74, sidecar
  spawn + READY handshake R101) but **unverified**. Install `rustup`, then
  `npm run tauri dev`. App icons still need generating (`npm run tauri icon`).
- Orchestrator token is generated but **not yet enforced** on endpoints (M1+, P0-16).
- `models.json` companion-repo URL + sha256 values are **placeholders** (R160) —
  filled when the HF companion repo is published.

## Configuration (env)

| Var | Default | Purpose |
| --- | --- | --- |
| `LOOM_ORCH_HOST` | `127.0.0.1` | orchestrator bind host (R101) |
| `LOOM_ORCH_PORT` | `8765` | orchestrator bind port |
| `LOOM_ORCH_TOKEN` | random | loopback handshake token (R101) |
| `LOOM_PIPELINES_ROOT` | parent `../../src` | pipeline CLIs + `village_ai/models` root (§4) |
| `LOOM_VENV_PYTHON` | current interpreter | python used to shell out to pipeline CLIs (R103) |
| `LOOM_APP_REPO` | `..` | app-repo cwd the Tauri shell spawns the orchestrator from |
| `VITE_LOOM_ORCH_URL` | `http://127.0.0.1:8765` | orchestrator URL the dev UI probes |
