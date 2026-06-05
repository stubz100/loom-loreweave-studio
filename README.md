# Loreweave Studio (`loom`)

A non-linear storyboard / story-generation desktop app. Tauri (Rust shell) +
React/TypeScript UI + a Python FastAPI orchestrator that wraps the
`run_pipeline.py` generation CLIs behind a single VRAM-aware job queue.

> **Status: Phase 0 (foundation) — ✅ COMPLETE & ACCEPTED (M0–M8, 7/7 acceptance green).**
> App shell + orchestrator handshake (M0), one real generation (M1), an N-image batch
> streaming into a selectable grid (M2), the hardened adapter contract — token-gated
> `/generate`, `capabilities()`, coarse progress, **cancel = subprocess kill**,
> manifest-status-as-truth (M3) — a **durable, resume-paused `queue.json`** with VRAM
> admission + OOM retry (M4), **durable bundle I/O + `loom init` project workspaces**
> (stable IDs, `schema_version`, atomic writes, JSON Schemas, lineage edge, footprint
> estimator; the queue + outputs + per-job logs live in a real `<project>/`, M5),
> a **continuously-polled disk guard** (two measures × two thresholds; hard-stop blocks
> new jobs, M6), a **phase-scoped launch gate** (3-state component manifest; refuse to
> start on a missing P0 code component; missing P0 weight → explicit HF fetch, M7), and
> the **§1 seven-step acceptance test green on real hardware** (M8). Next: **P1**
> (img2img/inpaint + multi-ref/SD3.5 adapters, World/Asset Studio embryo). Spec:
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
│   ├── src/           #   React UI (three-pane shell + job-queue dock + batch grid + project bar)
│   └── src-tauri/     #   Rust: single-instance, orchestrator sidecar spawn + kill, READY handshake
└── orchestrator/      # Python FastAPI service (127.0.0.1)
    ├── main.py        #   app factory + /health /version /generate /jobs /queue /project /disk /components /outputs
    ├── runner.py      #   durable, resume-paused single-worker queue, workspace-bound + disk-gated (M4/M5/M6)
    ├── workspace.py   #   bundle I/O: IDs, atomic writes, schema validation, footprint estimator (M5)
    ├── projects.py    #   project lifecycle (create/open/resume) + app-level last-project pointer (M5)
    ├── diskguard.py   #   two-measure/two-threshold space guard, continuously polled (M6, §9/R96)
    ├── components.py  #   phase-scoped 3-state launch gate + model-weight presence/fetch (M7, §11/R163)
    ├── logsetup.py    #   central logger → stderr + rotating file (.env LOOM_LOG_LEVEL brief|verbose)
    ├── lineage.py     #   per-output lineage edge → rebuildable lineage/index.json (M5, R98)
    ├── schemas/       #   JSON Schemas for the P0 records (project/job/manifest/lineage)
    ├── config.py      #   port/token + pipeline roots + interpreter + work disk (R101/R103/R72)
    ├── adapters/      #   one module per pipeline (zimage) → JobSpec/CompletionRecord
    └── requirements.txt
```

A **project** is a folder on the work disk (`<work disk>/<name>/`, default `F:\_tmp`, R72):

```
<project>/
├── project.json         # format, size cap, ids, schema_version (atomic, schema-validated)
├── jobs/
│   ├── queue.json       # durable, resume-paused job queue
│   └── logs/<id>.log    # per-job stdout/stderr
├── lineage/index.json   # rebuildable lineage edges (requester → job → output → manifest)
├── _temp/               # job scratch (training temp later)
└── out/<job_id>/        # generated PNG + sidecar manifest
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

- Job queue is **durable + resume-paused** (M4, `queue.json`); cancel works (M3); VRAM
  admission is enforced. Since **M5** the queue + outputs + per-job logs live in a real
  per-project `<project>/` workspace; `/generate` needs an **open project** (the last one
  re-opens on launch). **Opening** a project uses a **registry picker** ("Open ▾" — a list of
  recent projects from `.loom_state`, machine-local, not in git). **File-watch** (read-side change
  events) isn't wired yet, and **creating** a project is still prompt-based (native folder picker +
  format/cap wizard come later).
- The **disk guard** (M6) polls project-cap headroom + work-disk free continuously; a
  **hard-stop (<2%) returns 507** on `/generate` and holds queued jobs (running jobs
  finish). Project size is an `os.walk` sum each poll — fine for P0; an incremental
  accountant is a later refinement once PNG-sequence masters make projects large.
- A finished generation can be **deleted safely** from the grid (🗑 on a tile): the orchestrator
  atomically removes the output dir + sidecar manifest, the per-job log, the queue entry, and the
  lineage edge (`DELETE /jobs/{id}`) — no orphaned files (vs hand-deleting). The **persistent
  browse-past-generations grid** (showing prior `done` images on reopen) is the **P1 casting grid**;
  the P0 grid is per-session (data isn't lost — it's in the lineage index + manifests).
- The **launch gate** (M7) hard-requires the P0-essential code components at startup
  (`zimage`, queue, workspace I/O) and **refuses to start** with a clear error if one is
  missing. A missing P0 **weight** doesn't block startup — it's reported (`/components`,
  `/health.weights_ok`) so the UI can **fetch on demand** (`POST /components/fetch`), and
  `/generate` returns **412** until it's present. Presence-only (R97); **sha256 verify on
  fetch is TODO** until the companion HF repo publishes hashes.
- On app exit the Tauri shell does a **graceful shutdown handshake** (P0-15): it calls
  `POST /shutdown` so the orchestrator re-queues the in-flight job + marks a clean stop
  (relaunch resumes it **queued/paused**, not failed — R159), then hard-kills as a
  fallback so the port is never left held (the worker is also Job-Object-reaped — no
  orphaned GPU). A genuine *crash* (no handshake) still correctly lands the in-flight job
  as `failed` for the user to inspect.
- Only **t2i** is wired; img2img/inpaint (+ image inputs) arrive in **P1**.
- The on-demand HF **fetch** flow is built (M7: `POST /components/fetch` + the UI's
  [Fetch now]); what's still pending is **publishing** the artifacts — `models.json`'s
  companion-repo URL + sha256s are **placeholders** (R160), so `file`-type fetches and
  **checksum verification** can't be exercised until that repo exists. (`hf_diffusers`
  weights like `zimage` already fetch via diffusers/the hub cache.)
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
| `LOOM_WORK_DISK` | `F:\_tmp` (win) | default parent for new project workspaces (R72) |
| `LOOM_PROJECT_DIR` | _(unset)_ | force-open/create a project at startup (tests/CI/GPU-verify) |
| `LOOM_STATE_DIR` | `<repo>/.loom_state` | **app-level** state (last-project pointer `app.json`) |
| `LOOM_VRAM_BUDGET_GB` | `16` | VRAM admission budget (RX 9070 XT) |
| `LOOM_DISK_POLL_S` | `5` | disk-guard poll cadence (M6, §9) |
| `LOOM_ACTIVE_PHASES` | `P0` | phases the launch gate hard-requires (comma-sep, M7, §11) |
| `LOOM_LOG_LEVEL` | `brief` | backend log verbosity: `brief`(INFO) / `verbose`(DEBUG) / level name |
| `VITE_LOOM_ORCH_TOKEN` | `.env.local` | dev UI token (sent as `X-Loom-Token`) |
| `VITE_LOOM_LOG_LEVEL` | `brief` | frontend (webview console) log verbosity |
| `LOOM_DEV_OUT` | `<repo>/.dev_out` | **legacy** scratch (dry-run only; real output → `<project>/out/`) |
| `LOOM_APP_REPO` | `../..` (from `src-tauri/`) | app-repo cwd the Tauri shell spawns the orchestrator from |
| `VITE_LOOM_ORCH_URL` | `http://127.0.0.1:8765` | orchestrator URL the dev UI probes |
