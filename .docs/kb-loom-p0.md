# Loreweave Studio — P0 Foundation build spec (`loom` Phase 0)

Created: 2026-06-01
Status: spec — **implemented through M3** (M0–M3 done & pushed; Phase B M4+ in progress). Live status + per-milestone log: [`kb-loom-p0-imp.md`](kb-loom-p0-imp.md).
Parent: [`kb-storyboard01.md`](kb-storyboard01.md) (product/UI overview + decision record R1–R103)
Engine spec for later phases: [`kb-pipelines01.md`](kb-pipelines01.md)

This is the **detailed build spec for Phase 0** — the foundation under everything else. P0
builds **no creative features** (no World bible, no Asset Studio, no Shots, no Flow). It builds
the **shell, the project workspace, the persistent job queue, and the pipeline-adapter layer**,
proven by running **one real generation job end-to-end**. Every decision here traces to a
resolved item (`Rnn`) in `kb-storyboard01.md` §10.0.

---

## 1. Purpose & the P0 done-line

**Purpose:** stand up the durable spine — a single-instance desktop app that creates a project
on the work disk, shows the three-pane shell + job-queue dock, and dispatches a generation job
to an existing pipeline CLI through a persistent, VRAM-aware queue, recording a manifest and a
lineage edge.

**P0 acceptance test (the "this proves the foundation" moment):**

1. Launch loom (single instance; second launch focuses the existing window — R74).
2. On launch a **presence check** runs (R91/R97/R163): a missing phase-essential **code component**
   (pipeline venv/CLI) **fails fast with a clear message** (code can't be auto-fetched); a missing
   phase-essential **model weight** **offers a fetch** from the HF companion repo (R160) — launch
   proceeds if the fetch succeeds, and **fails only if the fetch is unavailable, declined, or fails**.
3. `loom init` a new project: pick an **empty** folder under `F:\_tmp` (warn if not empty),
   set **project format** (aspect+resolution locked, fps, audio master) and a **size cap**
   (default 250 GB / min 50 GB, R164) — loom validates **free space ≥ cap** (R45/R56/R79/R80).
4. The **three-pane shell** opens with an empty stage and the **Job Queue dock** docked
   bottom, showing **VRAM and disk usage** (R-shell).
5. From a **generate bar**, queue an **N-image `zimage` t2i batch** (smoke target, §12). Each
   image runs as a subprocess through the orchestrator (single GPU worker); results **stream
   into a simple selectable grid** in the stage, each with its **per-pipeline manifest + a
   lineage edge** written to the bundle (R-lineage/R98). This is the embryo of the casting grid.
6. Quit mid-job → relaunch: the **queue persists and resumes *paused*** (review/cancel, then
   unpause); the incomplete job's partial output was discarded but the **task is still queued**
   (R69/R78/R88).
7. Fill the disk past the threshold (simulate) → loom **warns at <5% headroom, hard-stops at
   <2%** on either project-cap or disk-free (R96).

If all seven hold, P0 is done. Note: step 5 uses **only the existing `src/pipeline/zimage`
CLI** — P0 adds no model code.

---

## 2. Scope: in vs. out

**In P0:**

- loom app scaffold (Tauri + React), single instance, hard-require + presence-check launch.
- **App source in the cloned `loom/loom-loreweave-studio/` repo** (R162); M0 scaffolds into the
  existing clone + extends its `.gitignore` to exclude weight caches, and adds a **`models.json`
  manifest** (HF companion-repo URLs + sha256 + target paths) with a **presence → on-demand fetch**
  flow (weights never live in git, R160). Pipelines/models referenced from the parent monorepo (§4).
- Python **orchestrator** service (FastAPI on localhost) wrapping existing `run_pipeline.py`
  CLIs as subprocesses.
- **Project workspace** on the work disk: `loom init`, folder skeleton, project format +
  size cap, empty-folder + free-space validation.
- **Bundle I/O core**: stable IDs, `schema_version`, atomic orchestrator-owned writes,
  file-watch, lineage edge writer.
- **Persistent Job Queue**: durable, single GPU worker, resume-paused, cancel, VRAM-aware
  admission, the disk guard.
- **Pipeline-adapter contract**: one typed job → one CLI invocation → normalized completion
  record; the "contract hardening" pass (progress, exit codes, cancellation, capability/presence
  discovery).
- **Component manifest** (essential vs optional).
- Minimal UI: shell + job-queue dock + a bare generate bar (enough to exercise the spine).

**Out of P0** (later phases, see `kb-storyboard01.md` §9.2):

- L1 World, L2 Asset Studio, profile **versioning**, casting/bootstrap, LoRA training (P1/P2).
- L3 Shots/continuity, audio/voice/lip-sync (P3). L4 Flow, Muse agent (P4). L5 Episode/Render
  (P5). Engine export, plugin API (P6).
- The SLM/VLM **tenant manager** is **stubbed** in P0 (registry + presence only); the live
  KV-cache start/stop lands when Muse features arrive (P3/P4). P0 only needs the queue to treat
  "a model is loaded" as a VRAM tenant abstractly.

---

## 3. P0 architecture (a subset of `kb-storyboard01.md` §8)

```
┌──────────── Desktop app (Tauri, single instance) ───────────────┐
│  React + TS UI                                                   │
│   • App shell (three-pane) + Job Queue dock                     │
│   • Bare generate bar (P0 only)                                 │
│   • Zustand store ← projection of bundle files (read)           │
└──────────────▲────────────────────────────────┬────────────────┘
               │ local HTTP (127.0.0.1)          │ change events (SSE/ws)
┌──────────────┴────────────────────────────────▼────────────────┐
│  Orchestrator (Python FastAPI)                                  │
│   • Workspace I/O (atomic writes, IDs, schema, file-watch)      │
│   • Persistent Job Queue (1 GPU worker; resume-paused)          │
│   • Pipeline adapters → subprocess run_pipeline.py              │
│   • Disk guard; lineage writer; component-manifest check        │
└──────────────┬───────────────────────────────────────────────── ┘
               │ subprocess (one heavy model at a time)
        existing  src/pipeline/zimage/run_pipeline.py   (P0 smoke target)
                  (+ flux2/sd35/… available, same adapter shape)
```

**Decisions locked for P0:**

- **Shell = Tauri** (Electron fallback) — R5. Thin Rust layer: spawn the orchestrator sidecar,
  enforce single-instance, emit file-change events; everything else is React + HTTP.
- **Orchestrator owns all bundle writes** (atomic, temp-file + rename); the UI requests changes
  via the typed API and consumes change events. Removes the file-watch write race (Codex).
- **Files are the source of truth**; the UI is a projection. External hand-edits are picked up
  read-only.

---

## 4. loom app repo layout (its own cloned GitHub repo — R162)

The loom **app source lives in its own GitHub repo, cloned to `loom/loom-loreweave-studio/`**
(origin `github.com/stubz100/loom-loreweave-studio`), nested inside this monorepo (R162). This
**pulls R97's "extract a shareable public repo" forward** — the public repo exists *first* and
development happens directly in it. The repo is **already cloned, with a remote and a `.gitignore`**,
so M0 **scaffolds into the existing clone** (no `git init`) and makes the first commit/push.

The **pipeline CLIs and model weights stay in the parent monorepo** (`src/pipeline/`,
`src/village_ai/models/`) for now — they are **referenced, not vendored**. The orchestrator
resolves a **configurable pipelines/models root** (it already pins the interpreter/`.venv`, R103);
during dev that root is the parent `src/`, one level up from the app repo. Folding the pipelines
into the app repo (vendoring or a submodule) for a fully self-contained public release stays a
later step under R97.

> **⏩ Update (brought forward, `ebc9014`):** pipeline code is now **vendored per-phase**. The P0
> worker **`zimage` lives in the in-repo `pipelines/`** dir; the orchestrator resolves each pipeline
> **in-repo first, parent `src/pipeline/` as fallback** (`config.pipeline_roots`). This pulls part of
> R97 forward so a clone can already run; remaining pipelines vendor as their phases land. Model
> **weights are still never vendored** (R160) — resolved under `src_root` (`LOOM_SRC_ROOT`).

```
stubz-002-tripo-sf/                      # parent monorepo (dev environment)
├── src/pipeline/…                       # existing pipeline CLIs — REFERENCED by the orchestrator
├── src/village_ai/models/               # model weights — OUTSIDE the app repo, .gitignored upstream
└── loom/loom-loreweave-studio/          # ← THE APP REPO (cloned: stubz100/loom-loreweave-studio)
    ├── .gitignore                       # present in the clone; extend to exclude any local model cache
    ├── models.json                      # HF companion-repo manifest (R160): url+sha256+path+phase
    ├── app/                             # Tauri + React shell
    │   ├── src-tauri/                   # Rust: single-instance, sidecar spawn, file-watch bridge
    │   └── src/                         # React + TS: shell, queue dock, generate bar, store
    ├── orchestrator/                    # Python FastAPI service
    │   ├── main.py                      # app factory, routes
    │   ├── workspace.py                 # bundle I/O: IDs, schema, atomic writes, file-watch
    │   ├── queue.py                     # persistent job queue + GPU worker + resume-paused
    │   ├── diskguard.py                 # two-threshold policing (5%/2%, project + disk)
    │   ├── adapters/                    # one module per pipeline (zimage first)
    │   │   ├── base.py                  # JobSpec → argv, manifest envelope, progress, cancel
    │   │   └── zimage.py
    │   ├── lineage.py                   # lineage-edge writer
    │   ├── components.py                # component manifest (essential vs optional) + presence check
    │   └── schemas/                     # JSON Schemas for P0 records (see §6)
    ├── docs/                            # kb-loom-p0.md lives here or in .github/copilot (this file)
    └── README.md
```

---

## 5. Project workspace & the work disk (R72, R45, R79, R80, R96)

A project is a folder on the **work disk** (default `F:\_tmp\<project>/`). The work disk holds
the **entire project workspace** (R72) — not just LoRA temp. For P0 the skeleton is minimal
(most subtrees are created lazily by later phases):

```
F:\_tmp\<project>\
├── project.json              # ← P0 writes this: format, size cap, ids, schema_version
├── jobs/
│   ├── queue.json            # ← persistent queue state (durable, resume-paused)
│   └── logs/<job_id>.log     # ← per-job stdout/stderr
├── lineage/
│   └── index.json            # ← rebuildable lineage index (asset@version+LoRA per output)
├── _temp/                    # ← transient (training temp later; P0: job scratch)
└── out/                      # ← P0 smoke outputs (PNG + sidecar manifest)
```

`loom init` (P0):

1. Prompt for a **destination folder** → must be **empty** (warn + refuse if not — R80).
2. Prompt for **project format**: aspect `[X]:[Y]` + resolution `[W]×[H]` **locked together**,
   **fps**, **audio master** (default WAV/AIFF 48 kHz 16-bit stereo). Presets default to
   **Wan2.2 native 1280×720** (R56); also 832×480 and upscaled targets (R45/R73).
3. Prompt for **size cap**: default **250 GB** (R164, raised from 100 GB to fit R161 PNG-seq
   masters), min **50 GB**, no max (R79). A **footprint estimator** (R164) projects the likely
   PNG-master size from target episode length × resolution and suggests/warns on the chosen cap.
4. **Validate free space ≥ cap** on the chosen disk (R80); refuse if not.
5. Write `project.json` (atomic), open the project.

`project.json` (P0 shape):

```json
{
  "schema_version": 1,
  "id": "prj_3f9a2c",
  "name": "my-story",
  "created_at": "2026-06-01T10:00:00Z",
  "workspace_path": "F:\\_tmp\\my-story",
  "format": {
    "aspect": [16, 9],
    "resolution": [1280, 720],
    "fps": 24,
    "audio_master": { "container": "wav", "rate_hz": 48000, "bits": 16, "channels": 2 }
  },
  "size_cap_gb": 250
}
```

---

## 6. Records, IDs, schema, atomic writes (R-data, §3.3)

P0 implements the **persistence core** that every later record reuses. Only two record types
exist at P0 (**StoryBible/project**, **Job**); the other four (AssetProfile+version, Shot+node-
version, FlowNode, Render) arrive in later phases but **must inherit these rules**:

- **Stable internal IDs** on every record (`prj_…`, `job_…`, later `chr_…/ver_…/node_…/rnd_…`),
  generated on creation; references use IDs, never names/paths.
- **`schema_version`** on every record; loader validates against the JSON Schemas in
  `orchestrator/schemas/`; **refuses partially-written JSON**.
- **Atomic, orchestrator-owned writes**: write temp → `fsync` → atomic rename; emit a change
  event. The UI never writes bundle files directly.
- **File-watch** surfaces external edits read-only; debounced; validates on load.
- **Lineage edge** per generated output: `{requester_id} → job_id → output_file → manifest`,
  **including `asset@version` + LoRA version** once those exist (R98). P0 records the subset it
  has (project + job + output + manifest). A small **rebuildable `lineage/index.json`** avoids
  scanning every manifest at startup (Codex).

JSON Schemas to author in P0: `project.schema.json`, `job.schema.json`, `manifest.schema.json`
(the normalized completion envelope, §8), `lineage.schema.json`.

---

## 7. Persistent Job Queue (R69, R78, R88; §6.6)

The queue is the operational heart on a 16 GB GPU.

- **Single active GPU worker** — subprocess isolation; never co-load two heavy models. CPU-only
  steps (later: extract-frame, compose, export) may run alongside, but P0 has none yet.
- **Durable**: `jobs/queue.json` persists every task with status (`queued|running|done|failed|
  canceled`), params, requester id, vram estimate, and result/manifest path. Survives shutdown.
- **Resume *paused* on relaunch** (R88): on open, the queue loads but **does not auto-run**; the
  dock shows pending tasks and a **[Review / Unpause]** control.
- **One job lifecycle (R159)** — every job carries a **`resumable`** flag (default **`false`**;
  set **`true`** only for workers that checkpoint to workspace temp — P2 training). What happens to
  a job that was `running` when the app went down depends on *how* it went down × `resumable`:

  | | graceful exit (R88, clean shutdown) | crash (sidecar died, P0-15) |
  | --- | --- | --- |
  | **`resumable=false`** (default) | → `queued`, **partial output discarded**, re-runs from scratch (R69/R78) | → `failed`, partial discarded, **user retries** |
  | **`resumable=true`** (e.g. LoRA train) | kept; on unpause **resumes from last checkpoint** (P2-10), not restarted | recovered to its last checkpoint; on unpause resumes (or user retries) |

  The queue always comes back **paused** (R88) in every cell; nothing spends GPU until the user
  unpauses. The graceful≠crash split is deliberate — a clean stop leaves a known-good state safe to
  re-queue, a crash leaves an unknown one the user should inspect.
- **Cancellation**: per-task cancel; cancel terminates the subprocess (SIGTERM→kill), marks
  `canceled`, cleans partial output.
- **VRAM-aware admission**: each job carries a VRAM estimate; if it won't fit, the queue
  suggests an offload mode (`group`/`sequential`) rather than OOM-failing, and records observed
  peaks to improve estimates (Codex). For P0, a static per-pipeline estimate table is enough.
- **Disk guard gate** (§9): before admitting any space-consuming job, check the two thresholds.
- **Retry**: visible, capped auto-retry on OOM with a heavier offload mode (mirrors Hunyuan/Wan
  notes in `kb-pipelines01.md`).

`queue.json` task (P0 shape):

```json
{
  "id": "job_8c21",
  "schema_version": 1,
  "pipeline": "zimage",
  "mode": "t2i",
  "params": { "prompt": "...", "seed": 42, "width": 1280, "height": 720, "count": 1 },
  "requester_id": "prj_3f9a2c",
  "vram_estimate_gb": 7.0,
  "status": "queued",
  "created_at": "...",
  "output_manifest": null
}
```

---

## 8. Pipeline-adapter contract + "contract hardening" (Codex; §8.1)

This is the P0 task that de-risks every later phase: a **normalized contract** between the
orchestrator and the existing `run_pipeline.py` workers, so the UI only ever exposes supported
actions and the queue gets uniform progress/results.

Per pipeline, an adapter provides:

- **`build_argv(job) -> list[str]`** — typed params → the real CLI (e.g.
  `python -m src.pipeline.zimage.run_pipeline t2i --prompt … --seed … --width … --height …
  --out <project>/out/`). Image path is positional where the CLI requires it (per
  `kb-trellis2.md`).
- **`parse_result(returncode, stdout, manifest_path) -> CompletionRecord`** — normalize the
  worker's native JSON manifest into a common **envelope**: `{ ok, outputs[], manifest_path,
  duration_s, peak_vram_gb?, stderr_tail }`.
- **`capabilities() -> {modes, params, present}`** — declares supported modes and does the
  **presence check** (binary/venv/model exists). Drives both the launch gate (R91/R97) and
  graying-out unsupported UI.
- **`progress(stdout_line) -> float?`** — best-effort progress parse for the dock bar.
- **`cancellable: bool`** + clean subprocess teardown.

**Hardening checklist (do once, in P0, across the existing workers):** consistent exit codes;
a stable manifest envelope; machine-readable progress (or accept "indeterminate"); cancellation
behavior; resume/latents behavior (declare, don't wire yet); capability/presence discovery.
Document gaps in `kb-loom-p0.md` as they're found; do **not** silently work around them.

P0 ships exactly **one** adapter (`zimage`) to prove the contract; the others (`flux2`, `sd35`,
`wan2`, `hunyuan`, `ltxv`, `trellis2`) follow the same shape in their phases.

---

## 9. Disk guard (R96; §4.2)

A continuously-polled guard with **two measures × two thresholds**:

| Measure | Source | Warn | Hard stop |
| --- | --- | --- | --- |
| Project-cap headroom | project folder size vs. `size_cap_gb` | <5% left | <2% left |
| Disk free space | work-disk free vs. total | <5% free | <2% free |

- Checked **at project creation** (free ≥ cap) **and continuously during work** (R96 — this
  *reverses* the earlier validate-only stance).
- **Hard stop** blocks admitting new space-consuming jobs (generation, later training/render);
  running jobs finish. The dock shows the live usage and the reason.
- Resolve by **raising the cap** or **freeing space** (manual file management — R80; no project
  manager in v1).

---

## 10. The shell (minimal P0 UI; §6.0/§6.1)

Three-pane shell (left rail / center stage / right inspector) + the always-docked **Job Queue**
strip. The P0 stage is a **generate bar + a simple selectable result grid** (the smoke target,
§12) — enough to drive the spine and already resemble the casting grid:

```
┌─ Loreweave Studio ── my-story ───────────────────────[ ⚙ ]─[ _ ▢ ✕ ]┐
│ (workspace switcher stubbed: only a "Sandbox" tab in P0)              │
│ ┌────────────┬───────────────────────────────────┬─────────────────┐ │
│ │ NAVIGATOR  │ STAGE  pipeline[zimage▾] mode[t2i▾]│   INSPECTOR     │ │
│ │ (empty)    │ prompt[__________] seed[rand] N[3] │ (selected image:│ │
│ │            │ [Generate ▶]                       │  job details +  │ │
│ │            │ grid: ▣ ▣ ▣   (star/cull, P0-lite) │  lineage)       │ │
│ └────────────┴───────────────────────────────────┴─────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ JOB QUEUE ⏸ paused (3) · VRAM 0.0/16.0G · disk 18/250G ▓░ [unpause]│ │
│ │  ◔ zimage t2i ×3   ·   recent: ● zimage t2i (12s)                 │ │
│ └──────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

Design language per `kb-storyboard01.md` §6.0 (dark graphite, amber accent, status dots,
non-destructive). Accessibility: status as **icon+text**, not color alone (Codex).

---

## 11. Launch requirement & component manifest (R91, R97; §8.3)

- **Hard-require, presence-only (v1) — but *phase-scoped*:** at launch, read the **component
  manifest** and hard-require only the components **essential to the phases actually built** (for
  P0: `zimage` + the queue + workspace I/O). A missing **phase-essential code component** → **clear
  error, refuse to start** (no degraded mode — code can't be auto-fetched). Only **presence**, not
  version (R97); version-pinning is post-v1. *(Missing phase-essential **model weights** are handled
  differently — they get an on-demand HF fetch before failing; see §11.1 and R163.)*
- **Three states, not two** — a component is **phase-essential** (launch-blocking once its phase
  ships), **installed-but-unavailable** (present, but its phase isn't active yet — reported, *not*
  blocking), or **missing** (only blocking if it's phase-essential *now*). This stops P0 from
  demanding P3/P6 components (e.g. **`trellis2` is P6** — it must never block a P0 launch).
- **Component manifest** (`orchestrator/components.py`): records each pipeline/tool with its
  **phase** (P0…P6), repo origin, env (shared vs isolated), and presence. The launch gate filters
  by *active phase*:
  - **P0 phase-essential:** `zimage`, queue, workspace I/O.
  - **Later phase-essential (declared, presence-checked, NOT P0-blocking):** `multi`/`_img2img`/`sd35` (P1), the trainer (P2), `wan2`/`hunyuan`/`ltxv` + lip-sync/TTS (P3), `trellis2` (P6).
  - **Optional/external:** exotic postproc.
  P0 populates the manifest and gates on the P0 subset only.

### 11.1 Model manifest & Hugging Face companion repo (R160)

**Weights do not live in git.** The Q8 8B Qwen models alone are ~24 GB, and the diffusion/video
checkpoints are tens of GB more — committing them would make the repo unclonable. So:

- **The app repo already exists** — it's the cloned `loom/loom-loreweave-studio/` (R162). M0
  **scaffolds into the existing clone** (no `git init`) and makes the first commit/push; the
  application code (`app/`, `orchestrator/`, adapters, the manifests) is version-controlled from day
  one (R160). The clone ships a `.gitignore`; **extend it to exclude any local weight cache**
  (`*.safetensors`/`*.gguf`/`*.ckpt` and any in-repo model dir). The bulk weights live in the parent
  monorepo's `src/village_ai/models/`, **outside this repo entirely**, so they can't be committed by
  accident. The project **workspace** (`F:\_tmp\…`) stays separate and **non-git** (§3.3) — this is
  about the *app* repo only.
- **A `models.json` manifest ships in the app** (the weight-level twin of the component manifest):
  one entry per required model → **Hugging Face URL** (a **companion HF repo** that mirrors the
  weights), filename, **sha256**, target local path (e.g. `src/village_ai/models/…`), size, and the
  **phase** that first needs it. This is the single list of "what the app expects on disk and where
  to get it."
- **Presence-check → offer fetch → fail only if that fails (R163, upgrades R91/R97):** the launch
  gate's presence check already knows what's missing; with the manifest it can now **offer to
  download** the missing phase-essential weights from the HF companion repo (`huggingface_hub`,
  resumable, checksum-verified) instead of dead-ending on a "missing model" error. **Launch then
  fails fast — same clear-error, refuse-to-start path as a missing code component — only if the
  fetch is unavailable (no network / repo down), declined by the user, or fails checksum (R163).**
  Fetch is **explicit and on-demand** (never an auto-download at an unexpected time — consistent with
  the no-surprise-GPU/no-surprise-work posture), and a model that isn't needed by an active phase is
  reported, not fetched.
- **The companion HF repo is the distribution channel**, the in-repo `models.json` is the contract.
  Updating a model = update its manifest entry (URL + sha256); the app re-fetches on next presence
  check.

> P0 only needs to **author `models.json` + the `.gitignore` + the presence→fetch flow** for the P0
> model(s) (`zimage`). Later phases append their entries as the weights become essential.

> **⏩ Status (`f17e70a` / `ebc9014`):** `models.json` + `.gitignore` are authored; **the HF
> presence→*fetch* download flow is NOT built yet** (`zimage` weights are present locally / pulled by
> diffusers). Separately, the **transport token is now enforced**: `POST /generate` requires
> `X-Loom-Token` and CORS is locked to dev/Tauri origins (the no-surprise-GPU gate, R141–143) — this
> pulled **P0-16** forward. The launch **presence gate** (§11, refuse-to-start) is still **M7**.

---

## 12. P0 milestones — walking skeleton first (build order)

P0 is sequenced to **retire integration risk first and show a creative-ish artifact early**
(§14), then thicken the foundation underneath. The smoke target is a **multi-image batch in a
simple selectable grid** — the embryo of the casting grid (§4.1/§6.3) — not a single bare image,
so the spine already *looks like* the product on day one while staying P0-scoped (no
versioning/training/world-bible).

### Phase A — Walking skeleton (retire the integration unknown)

> Goal: prove **shell ↔ orchestrator ↔ CLI ↔ GPU** end-to-end with deliberately crude internals.
> Use an **in-memory queue** and **naive (non-atomic) writes** here — they get replaced in Phase B.

1. **M0 — scaffolds + handshake.** Scaffold the `loom/loom-loreweave-studio/` tree **into the
   existing clone (R162)**; **extend the `.gitignore` (weight caches) + add a first-pass `models.json`
   (R160), initial commit**; Tauri app boots a window (single-instance); FastAPI orchestrator boots;
   Tauri spawns it as a sidecar; health check round-trips.
2. **M1 — one adapter, one job, one image.** Minimal `zimage` adapter (`build_argv` +
   read-the-saved-manifest); a `POST /generate` that shells out to
   `python -m src.pipeline.zimage.run_pipeline t2i …` (in the shared `.venv`, R103); the PNG +
   its manifest come back. **In-memory queue, naive writes.** No persistence yet.
3. **M2 — batch grid on screen.** Generate bar fires an **N-image batch** (e.g. 3, ≤ R38's
   cap) → results stream into a **simple selectable grid** in the stage; click = preview. This is
   the **smoke target** and the first visible payoff. *Skeleton done: integration proven.*

### Phase B — Thicken the foundation (in priority order, §14)

4. **M3 — adapter contract + hardening.** Promote the M1 adapter to the full contract
   (`capabilities()`/presence, normalized completion **envelope**, coarse progress, cancel =
   subprocess kill, manifest-status-as-truth); run the **1-page contract check** on `zimage`
   (§15) and document findings. *(Highest residual risk — do it right after the skeleton.)*
5. **M4 — persistent, resume-paused queue.** Replace the in-memory queue with durable
   `jobs/queue.json`; single GPU worker; cancel; **resume *paused*** on relaunch; VRAM admission
   (static table); auto-retry-with-offload (capped, visible).
6. **M5 — durable bundle I/O.** Replace naive writes with **stable IDs, `schema_version`,
   atomic temp-file+rename, JSON Schemas, file-watch**; write the **lineage edge** per output
   (records `asset@version`+LoRA once they exist — R98). Promote `loom init` to full **project
   creation** (format + cap + empty-folder + free-space validation).
7. **M6 — disk guard.** Two-threshold continuous polling (warn <5% / hard-stop <2%, project-cap
   + disk-free, R96); dock shows live disk/project usage; guard gates job admission.
8. **M7 — launch gate + component manifest.** Presence-only hard-require at startup (R91/R97);
   populate `orchestrator/components.py` with the **phase-scoped 3-state model** (§11 —
   phase-essential / installed-but-unavailable / missing); refuse to start only on a **P0-essential
   code component** missing (`zimage`/queue/workspace I/O), with a clear message. A missing
   **P0-essential model weight** instead **offers the HF fetch first** and fails only if that fails
   (R163, §11.1). Future-phase components (e.g. `trellis2`, P6) are declared but **never block a P0
   launch**.

### Done-line

9. **M8 — acceptance.** Run the **§1 seven-step acceptance test** green (now that the grid
   replaces "one image", step 5 = the batch lands in the grid with manifests + lineage). Record
   any worker-contract gaps found for per-pipeline onboarding later.

This keeps every P0 deliverable but front-loads the integration proof + the batch-grid payoff,
and tackles the worker contract (the real risk) immediately after the skeleton rather than last.

---

## 13. P0 setup decisions (round 12 — settled, R100–R103)

1. **loom repo location** — **its own cloned GitHub repo at `loom/loom-loreweave-studio/`** (R162,
   supersedes R100's plain in-repo `loom/`; realizes R97 up front). Pipelines/models stay in the
   parent monorepo, referenced not vendored (§4).
2. **Orchestrator transport** — **FastAPI on `127.0.0.1`** (R101).
3. **P0 done-line** — the **§1 seven-step acceptance test is approved** (R102).
4. **Env strategy** — **reuse the current `.venv` for now** (R103); compose a dedicated
   `requirements.txt` (FastAPI etc.) inside the app repo, separate from the heavy pipeline deps. So
   P0's orchestrator runs in the existing parent `.venv` and shells out to the same
   `python -m src.pipeline.…` CLIs via the configured pipelines root (§4).

## 14. Reservation 1 — "P0 is big before the first creative payoff." Mitigation: walking skeleton

The concern is valid: as written, P0 is **all foundation** (shell, init, versioned/atomic bundle
I/O, persistent queue, disk guard, sidecar, contract hardening, launch checks) and the only
visible output is one smoke-test image. That's a long runway before anything feels creative.

The fix is **ordering, not scope-cutting**: build the **thinnest end-to-end path first**, then
thicken underneath. Don't gold-plate persistence before you've seen an image come back.

- **Skeleton first (1–2 sittings):** Tauri window → FastAPI sidecar → **one `zimage` job via the
  adapter** → image shows in the stage. Use a **crude in-memory queue and naive (non-atomic)
  writes** at this point. This proves the integration (shell↔orchestrator↔CLI↔GPU) — the riskiest
  unknown — immediately.
- **Then thicken, in priority order:** (1) the **adapter contract** (§15 — the real risk),
  (2) **persistent + resume-paused queue**, (3) **atomic writes + schema + IDs + lineage**,
  (4) **disk guard**, (5) **launch presence-check + component manifest**. Each is independently
  testable against the already-working skeleton.
- **Pull the first creative payoff forward.** The MVP (R40) is *casting → curate → AssetProfile*.
  Consider making P0's smoke target a **3-image `multi`/`zimage` batch shown in a simple
  selectable grid** instead of a single bare image — it exercises the identical spine but already
  *looks like* the casting grid (§4.1/§6.3), so the foundation has visible creative value on day
  one and bleeds naturally into P1. (Keeps P0 honest: still no versioning/training/world bible.)
- **Honest caveat:** some foundation genuinely can't be faked away without rework — versioned
  records, atomic writes, and the queue's durability *will* be needed. The skeleton doesn't skip
  them; it **sequences** them after the integration is proven, so motivation and risk both improve.

Net: same P0 deliverables, reordered so the integration risk is retired first and a creative-ish
artifact appears early.

## 15. Reservation 2 — "the existing CLIs need hard contracts." Audit findings (grounded)

This is the **highest-risk dependency**, and the author is right to flag it. I audited the
`zimage` wrapper (the P0 smoke target) and surveyed the others. Findings are **more reassuring
than feared**, with a few real gaps:

**What already exists (good):**

- **All 10 pipelines share a `manifest.py` / `PipelineManifest` convention** (`begin_stage /
  end_stage / fail_stage`, per-stage status, timings, a `save()` to a JSON file beside the
  output). So a **machine-readable result already exists** — the hardest part of a "hard
  contract" is largely done, not missing.
- **`zimage` uses save-then-raise on failure:** a failing stage calls `fail_stage` → `save()` →
  **`raise`**. The exception propagates out of `main()`, so the **process exits non-zero** *and*
  leaves a **saved failed-manifest** + traceback on stderr. So for zimage, **exit codes are
  reliable** and the failure is introspectable. (This is the opposite of the "returns 0 on
  failure" anti-pattern.)
- **Output + sidecar manifest** land at a deterministic path (`<output>.json`), and a `[done]`
  line is printed on success.

**Real gaps (v1-acceptable with adapter handling):**

| Gap | Reality | P0 stance |
| --- | --- | --- |
| **Progress events** | only coarse stage prints (`[stage1]`, `[stage2]`, `[done]`), no per-diffusion-step progress | adapter reports **coarse/indeterminate** progress; don't fake fine-grained bars |
| **Cancellation** | no signal handling | cancel = **subprocess terminate/kill**; acceptable v1 |
| **VRAM estimate** | not emitted up-front (gpu info appears post-hoc in manifest debug) | **static per-pipeline estimate table** + learn observed peaks over time |
| **Resume/latents** | declared in some (ltxv/hunyuan), not wired | **declare, don't rely on**, in P0 |
| **Cross-pipeline uniformity** | shared `manifest.py` *pattern*, but only `zimage` verified end-to-end | **verify per pipeline as it's onboarded**, not all 10 up front |

**Strategy — the adapter is the shock absorber, hardening is incremental:**

- The **adapter normalizes** whatever each wrapper does into the common envelope
  (`{ ok, outputs[], manifest_path, duration_s, peak_vram_gb?, stderr_tail }`), reading the
  **manifest's stage status as the source of truth** for success (belt-and-suspenders with the
  exit code).
- Codify a tiny **shared contract convention** the wrappers already nearly follow: (1) save a
  manifest always (success or fail), (2) **non-zero exit on failure** (zimage already does via
  `raise`), (3) deterministic output+manifest paths. Where a wrapper deviates, that's a small,
  well-scoped fix in *that* wrapper — not a model rewrite.
- **Harden exactly one (`zimage`) in P0**, prove the adapter + envelope, then **onboard each
  other pipeline to the same contract in its own phase** (`multi`/`_img2img`/`sd35`/`ltxv` in P1,
  the trainer in P2, video in P3, **`trellis2` in P6** — R128). This spreads the cost and avoids
  auditing pipelines you won't touch for phases.
- **Do a 1-page contract check per pipeline at onboarding** (exit code on failure, manifest
  schema, output path, progress signal, cancel behavior) — cheap, and it keeps the UI honest by
  only exposing verified capabilities (the `capabilities()`/presence mechanism, §8/§11).

**Bottom line:** the contract risk is real but **bounded** — the shared manifest convention and
zimage's save-then-raise mean we're hardening and *normalizing* an existing contract, not
inventing one. The app's reliability rides on the **adapter envelope + per-pipeline onboarding
checks**, both of which are modest, incremental work rather than a big upfront pipeline rewrite.

## 16. Work-package breakdown (WBS) — what P0 actually contains

*Rough solo-dev sizing: **S** ≤1 d · **M** 2–4 d · **L** 1–2 wk · **XL** 3 wk+. Risk: 🟢 routine ·
🟡 unknowns · 🔴 R&D / make-or-break. Maps to the §12 milestones.*

| WP | Work package | Maps to | Size | Risk |
| --- | --- | --- | --- | --- |
| P0-1 | `loom/loom-loreweave-studio/` tree (scaffold into the clone, R162) + Tauri window boot + single-instance | M0 | M | 🟡 first Tauri |
| P0-1b | **Scaffold into cloned app repo + extend `.gitignore` (weight caches) + `models.json` manifest (HF companion-repo URLs + sha256 + paths) + presence→on-demand fetch + configured pipelines/models root to parent `src/` (R160/R162)** — **◐ M0 done; pipeline code now VENDORED per-phase (`zimage` in-repo `pipelines/`, ebc9014, pulls R97 fwd, see §4 note); HF presence→fetch download flow still TODO** | M0 | M | 🟡 *folded — HF hosting + split repo* |
| P0-2 | FastAPI orchestrator + Tauri **sidecar spawn** + health handshake (port/token) | M0 | M | 🟡 sidecar lifecycle |
| P0-3 | `zimage` adapter MVP (`build_argv` + read-saved-manifest) | M1 | S | 🟢 |
| P0-4 | `POST /generate` → shell `run_pipeline` → return PNG + manifest | M1 | S | 🟢 |
| P0-5 | **Batch-grid UI** (N-image batch → selectable grid + preview) — *smoke payoff* | M2 | M | 🟢 |
| P0-6 | **Adapter contract hardening** (capabilities/presence, completion envelope, coarse progress, cancel=kill, manifest-as-truth) + 1-page `zimage` check | M3 | L | 🔴 highest residual |
| P0-7 | **Persistent resume-paused queue** (`queue.json`, 1 GPU worker, cancel, VRAM admission table, capped auto-retry-offload) | M4 | L | 🟡 |
| P0-8 | **Durable bundle I/O** (stable IDs, `schema_version`, atomic temp+rename, JSON Schemas, file-watch, lineage edge) | M5 | L | 🟡 |
| P0-9 | `loom init` full project creation (format + cap + empty-folder + free-space validation) + **footprint estimator** (episode-length × resolution → projected PNG-master size → cap suggest/warn, R161/R164) | M5 | M | 🟢 |
| P0-10 | Disk guard (two-threshold polling, dock usage meter, gates admission) | M6 | M | 🟢 |
| P0-11 | Launch gate + component manifest (presence hard-require; **code component missing → fail fast; model weight missing → offer HF fetch, fail only if that fails**, R163) | M7 | S | 🟢 |
| P0-12 | Minimal shell UI (dock, stage, generate bar) | M0–M2 | M | 🟢 |
| P0-13 | §1 seven-step acceptance test green | M8 | S | 🟢 |
| P0-14 | **Subprocess log capture + streaming** — per-job stdout/stderr → a job log pane; persisted with the job for post-mortem | M1/M3 | M | 🟡 *folded from gap* |
| P0-15 | **Orchestrator/sidecar lifecycle + crash recovery** — health monitor, restart policy, mark in-flight job failed (not lost) on a sidecar crash — **◐ BROUGHT FORWARD in part (ebc9014): sidecar now killed on app exit (no orphaned orchestrator/port collision); health-monitor + restart-policy + mark-in-flight-failed still TODO (M4)** | M0/M4 | M | 🟡 *folded from gap* |
| P0-16 | **Runtime transport + interpreter pinning** — loopback **port/token handshake** (R101 transport security) + the resolved Python/`.venv` interpreter recorded (R103), not assumed — **✅ BROUGHT FORWARD (f17e70a): `X-Loom-Token` enforced on `POST /generate`, CORS restricted, Tauri→webview token inject, interpreter recorded; central `.env` added. (Full transport hardening = minimal; revisit if needed)** | M0/M5 | S | 🟡 *folded from gap* |
| P0-17 | **Tauri packaging/installer** (build, optional signing) — low priority; named so it isn't forgotten, can slip to a packaging pass | — | M | 🟢 *folded from gap* |

**Rollup:** ~17 WP; **risk concentrated in P0-6 (contract) + the P0-7/P0-8 queue/bundle pair** —
~60% of the engineering, ~0% of the visible payoff (the §14 reservation, quantified). **P0-14–P0-17
were surfaced by the WBS gap-scan and are now planned** (log streaming + crash recovery are the
material ones; packaging is deferrable).

**Design notes for the folded-in WPs:**
- **P0-14 log streaming** sits alongside the adapter's *coarse progress* channel (§8) — same transport, a second stream carrying raw CLI output; the job record keeps the tail for debugging a failed manifest.
- **P0-15 crash recovery** pairs with the resume-*paused* queue (R88) and the **one-lifecycle table (R159)**: on a sidecar restart, a `running` **non-resumable** job is marked `failed` (its subprocess is gone) and the user retries; a `running` **resumable** job (P2 training) is recovered to its last checkpoint and resumes on unpause. The queue comes back paused either way — no silent loss, no silent restart.

> ### ⏩ Live implementation status — items brought forward (don't redo when you reach the milestone)
> The build is at **Phase A complete (M0–M2)**; some later WPs were pulled forward during review passes. **Full per-milestone log lives in [`kb-loom-p0-imp.md`](kb-loom-p0-imp.md)** — check it before starting any WP. Brought-forward so far:
> - **P0-16 (transport/token) — ✅ mostly done early** (`f17e70a`): token enforced on `/generate`, CORS locked, interpreter recorded, central `.env`. When M7/transport comes up, only revisit if deeper hardening is wanted.
> - **P0-15 (sidecar lifecycle) — ◐ partly done** (`ebc9014`): kill-on-exit done; crash-recovery/health-monitor/mark-in-flight-failed still owed (M4).
> - **Pipeline vendoring (R97/§4) — ◐ started** (`ebc9014`): `zimage` vendored in-repo; other pipelines vendor in their phases.
> - **Review #2 cheap fixes done early:** API dim-validation, READY-after-bind, dock global counts (`f17e70a`). **Deferred to M3:** `parse_result` manifest-as-truth (#4).

---

## Source / traceability

- Decisions: `kb-storyboard01.md` §10.0 (R1–R103) and the per-section bodies cited inline.
- Worker contracts: `kb-pipelines01.md`, `kb-trellis2.md` (CLI + manifest shapes), the existing
  `src/pipeline/*/run_pipeline.py`.
- Models present: `src/village_ai/models/` (verified).
