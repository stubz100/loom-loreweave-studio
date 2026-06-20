# Loreweave Studio — P0 implementation journal (`kb-loom-p0-imp`)

started: 2026-06-03 18:05:10
finished: 2026-06-05 18:57:09

Running log of what was **actually built** for Phase 0, milestone by milestone.
Brief points + any parameters/settings worth remembering later. Spec:
[`kb-loom-p0.md`](kb-loom-p0.md); decisions: [`kb-storyboard01.md`](kb-storyboard01.md) §10.0.

Convention: each entry dated; ⚙ marks a setting/param that later code depends on;
⚠ marks a known gap / deferred item.

---

## M0 — scaffold + orchestrator handshake — 2026-06-04

**Goal (spec §12):** scaffold the app-repo tree into the existing clone; Tauri
window boots (single-instance); FastAPI orchestrator boots; Tauri spawns it as a
sidecar; health check round-trips.

### Environment discovered (parent monorepo)
- App repo already cloned at `loom/loom-loreweave-studio/` — origin
  `https://github.com/stubz100/loom-loreweave-studio.git`, 1 commit (`c03b2fa Initial commit`),
  clean tree, Python-flavoured `.gitignore` present.
- Toolchain: Node v22.20.0, npm 10.9.3, Python 3.12.10, git 2.50.0. ⚠ **No Rust
  toolchain** (`cargo`/`rustc` absent) → Tauri window cannot be built/booted yet.
- Shared `.venv` at `<monorepo>/.venv/Scripts/python.exe`; FastAPI/uvicorn were
  **not** installed (added — see below).
- `src/pipeline/zimage/run_pipeline.py` confirmed: CLI flags `--prompt --model-name
  --width --height --seed --num-steps --guidance-scale --output-dir --device
  --mode --init-image --mask-image --strength` (⚙ **no `--count`/`--out`**; output
  auto-named `zimage_<UTCstamp>_s<seed>.png` + sidecar `<same>.json`). Relative
  `--output-dir` is anchored to **repo root** (`parents[3]`), not cwd. Default model
  `zimage-turbo` (repo `Tongyi-MAI/Z-Image-Turbo`, 9 steps, guidance 0.0, no CFG/neg).
- Models present locally in `src/village_ai/models/` (Qwen3-VL GGUFs etc.).

### Done
- **Deps:** installed `fastapi 0.136.3`, `uvicorn[standard] 0.49.0`,
  `huggingface_hub 1.8.0` into the shared `.venv` (R103). Pinned loosely in
  `orchestrator/requirements.txt` (kept separate from heavy pipeline deps).
- **Orchestrator package** (`orchestrator/`): `__init__.py` (version + SCHEMA_VERSION),
  `config.py` (frozen `Config`, env-overridable, path resolution), `main.py`
  (FastAPI factory + `/health` + `/version`, prints the READY handshake line).
- **Proved the handshake:** booted `python -m orchestrator.main`; it printed
  `LOOM_ORCH_READY url=http://127.0.0.1:8765 token=…`; `GET /health` →
  `{"status":"ok","app_version":"0.0.1","schema_version":1,"pid":…,"uptime_s":…}`;
  `GET /version` correctly reported `pipelines_root = <monorepo>/src` (exists) and
  the resolved `.venv` interpreter. ✅ Orchestrator half of M0 verified.
- **App scaffold** (`app/`): hand-written (no network scaffolder) — `package.json`
  (React 18, Vite 5, Tauri CLI 2, zustand), `vite.config.ts` (port 1420 strict),
  `tsconfig*.json`, `index.html`, `src/main.tsx`, `src/App.tsx` (three-pane shell
  + job-queue dock per §10, **live orchestrator `/health` probe** every 2 s with
  status dot), `src/lib/orchestrator.ts` (health client), `src/styles.css` (dark
  graphite + amber, status = icon+text per §6.0 accessibility).
- **Tauri shell** (`app/src-tauri/`): `Cargo.toml` (Tauri 2 + single-instance
  plugin), `tauri.conf.json`, `build.rs`, `capabilities/default.json`, `src/lib.rs`
  (single-instance focus R74; spawn `python -m orchestrator.main` as sidecar; parse
  the READY line → `OrchestratorEndpoint` state; `orchestrator_endpoint` command for
  the UI), `src/main.rs`. ✅ **Compiles** (`cargo build` clean — see *Build verification*).
- **`models.json`** (R160): `zimage-turbo` as `hf_diffusers` (P0, accurate) +
  Qwen3-VL generative (P3) & embedding (P4) as `file` entries showing the
  companion-repo fetch pattern. ⚠ companion-repo URL + all sha256 are placeholders.
- **`.gitignore`** extended: weight caches (`*.safetensors/*.gguf/*.ckpt/*.pt/*.bin`,
  `/models/`, `hf_cache/`), `node_modules/`, `app/dist`, `app/src-tauri/target` & `gen`,
  workspace folders.
- **`README.md`**: layout, dev quickstart, known gaps, env-var config table.
- **Journal**: this file.

### ⚙ Parameters / settings that later code depends on
| Setting | Value | Where | Why it matters |
| --- | --- | --- | --- |
| Orchestrator host:port | `127.0.0.1:8765` | `config.py` / env `LOOM_ORCH_HOST`,`LOOM_ORCH_PORT` | UI + sidecar must agree (R101) |
| Handshake line prefix | `LOOM_ORCH_READY url=… token=…` | `main.py`, `lib.rs` | **stable contract** — sidecar parses this exact prefix |
| Loopback token | env `LOOM_ORCH_TOKEN` or random `token_urlsafe(24)` | `config.py` | R101; ⚠ generated but **not yet enforced** on endpoints (M1+) |
| `SCHEMA_VERSION` | `1` | `orchestrator/__init__.py` | every bundle record carries it (R-data) |
| Pipelines/models root | env `LOOM_PIPELINES_ROOT` else `<monorepo>/src` | `config.py` | adapters shell out here (§4, R103) |
| `.venv` interpreter | env `LOOM_VENV_PYTHON` else `sys.executable` | `config.py`, `lib.rs` | runs pipeline CLIs (R103) |
| Vite dev port | `1420` (strict) | `vite.config.ts`, `tauri.conf.json devUrl` | Tauri loads this in dev |
| App-repo spawn cwd | env `LOOM_APP_REPO` else `../..` (from `src-tauri/`) | `lib.rs` | where the sidecar `python -m orchestrator.main` runs — must be the **app-repo root** (holds `orchestrator/`); set absolutely when launching the built exe |
| App icon source | `app/app-icon.png` (1024² RGBA placeholder) → `tauri icon` → `src-tauri/icons/` | generator | regenerate icon set if the source changes; ⚠ placeholder art |
| zimage output naming | `zimage_<UTCstamp>_s<seed>.png` + `.json` sidecar | (pipeline) | adapter must **discover** outputs, not pass `--out` |
| zimage relative out-dir anchor | repo root (`parents[3]`) | (pipeline) | pass an **absolute** `--output-dir` from the adapter to be safe |

### Build verification (Rust installed) — 2026-06-04
- **Rust toolchain installed** by the user: `cargo 1.96.0` (MSVC). `npm install` done
  (`node_modules` present).
- **App icon**: generated a 1024² placeholder (`app/app-icon.png` — graphite tile +
  amber woven lattice, via Pillow) and ran `npm run tauri icon` → full icon set in
  `src-tauri/icons/`; wired `bundle.icon` (32/128/128@2x/icns/ico) in `tauri.conf.json`.
- **Frontend build**: `npm run build` (`tsc && vite build`) clean — 32 modules → `app/dist/`
  (index ~0.4 kB, css ~2.5 kB, js ~145 kB). Confirms the TS/React compiles.
- **Rust build**: `cargo build` **exit 0** in 48.7 s → `tauri 2.11.2`,
  `tauri-plugin-single-instance 2.4.2`, `tao 0.35.3`, `webview2-com 0.38.2`. Produced
  `src-tauri/target/debug/loreweave-studio.exe`. **The whole M0 scaffold now compiles.**
- **Bug found + fixed during review**: the sidecar spawn cwd default in `lib.rs` was `".."`
  (only `src-tauri → app`), but the `orchestrator/` package lives at the **app-repo root**
  (`src-tauri → ../..`). Corrected the default to `"../.."`; launch sets `LOOM_APP_REPO`
  absolutely for the built exe.

### ✅ Runtime launch verified — M0 DONE — 2026-06-04
- User ran **`npm run tauri dev`** → **Loreweave Studio window booted** successfully.
  The Tauri shell spawns the orchestrator sidecar and the React UI probes `/health`.
  **M0 acceptance met:** shell ↔ orchestrator handshake works end-to-end.
- Remaining M0-adjacent niceties carried forward (non-blocking): see gaps below.

### ⚠ Known gaps / carried forward
- Token **not enforced** yet (P0-16, M1+).
- `models.json` companion-repo URL + sha256 are placeholders (fill at publish).
- App icon is **placeholder art** (replace later).

### Next (M1)
Minimal `zimage` adapter + `POST /generate`, in-memory queue, naive writes (§12 Phase A).

---

## M1 — one adapter, one job, one image — 2026-06-04

**Goal (spec §12):** minimal `zimage` adapter (`build_argv` + read-the-saved-manifest);
`POST /generate` shells out to the zimage worker in the shared `.venv` (R103); the PNG
+ its manifest come back. In-memory, naive writes. ✅ **DONE — real image generated.**

### Done
- **`orchestrator/adapters/`**: `base.py` (`JobSpec` in, `CompletionRecord` envelope out:
  `{ok, returncode, outputs[], manifest_path, duration_s, peak_vram_gb?, stderr_tail}`),
  `zimage.py` (`build_argv` + `parse_result` + `present()`), `__init__.py`.
- **`POST /generate`** (in `main.py`): typed `GenerateRequest` → zimage adapter →
  `subprocess.run` (cwd = monorepo root) → envelope. Synchronous; **in-memory `JOBS`
  dict** (naive — durable queue is M4). Plus `GET /jobs` and `GET /jobs/{id}`, and a
  `dry_run` flag that returns the argv without touching the GPU (test hook).
- **`config.dev_out_dir`** = `<app repo>/.dev_out` (env `LOOM_DEV_OUT`), gitignored.
  Replaced by the per-project workspace `out/` at M5 (R72).

### CONTRACT FINDINGS (zimage) — feed per-pipeline onboarding (§8/§15)
- **Invoke by absolute FILE PATH, not `python -m src.pipeline...`.** `run_pipeline.py`
  uses bare imports (`import stage1_load_pipeline`), which only resolve when the
  `zimage/` dir is `sys.path[0]` — i.e. when the script is run by path. `-m` would put
  cwd on the path instead and break those imports. *(The spec's `-m` example was
  illustrative; reality needs the file path. Updated the adapter + journal.)*
- **No `--count` / `--out`.** The CLI auto-names `zimage_<UTCstamp>_s<seed>.png` + a
  `<same>.json` sidecar under `--output-dir`, and prints `  Image: <path>` /
  `  Manifest: <path>` on success → adapter parses those lines (fallback: newest PNG in
  the out dir). Pass an **absolute** `--output-dir` (relative would anchor to repo root).
- Exit code is reliable (save-then-`raise` on failure, per §15). Manifest carries
  `pipeline_duration_s` (used for `duration_s`). **No up-front VRAM estimate** →
  `peak_vram_gb` stays null until M3 learns observed peaks.

### Verified
- **Dry-run** `POST /generate {dry_run:true}` → correct argv (file-path, absolute
  out-dir, seed/mode/size). cwd = monorepo root.
- **Real run** `POST /generate {prompt:"a lone knight on a windswept cliff at dusk…",
  seed:42, 1280×720}` → **`ok:true`**, `job_e72bf1ec`, PNG+manifest in `.dev_out/`,
  pipeline `duration_s` 240.3 s / wall 248 s, ROCm **AOTriton** SDPA backend, 9 turbo
  steps. **Output image is coherent** (knight on a cliff). Full spine proven:
  typed params → adapter → subprocess → real GPU gen → normalized envelope over HTTP.
- Weights already cached (no download): `Z-Image-Turbo` ~30.6 GB, `Z-Image` ~19.1 GB
  in `~/.cache/huggingface/hub`.

### ⚙ New settings / params
| Setting | Value | Where | Note |
| --- | --- | --- | --- |
| Smoke output dir | `<app repo>/.dev_out` | `config.dev_out_dir` / env `LOOM_DEV_OUT` | M1-only; → project `out/` at M5 |
| Default gen size | 1280×720 (must be ÷16) | `GenerateRequest`, `zimage.build_argv` | Wan2.2-native (R56) |
| Default device | `cuda` (maps to ROCm/HIP) | `zimage.build_argv` | AMD RX 9070 XT |
| zimage worker path | `<pipelines_root>/pipeline/zimage/run_pipeline.py` | `zimage.script_path` | invoked by file path |
| subprocess cwd | monorepo root (`pipelines_root.parent`) | `/generate` | |
| `/generate` test hook | `dry_run:true` → returns argv only | `main.py` | no GPU |

### ⚠ Gaps carried forward (by design — later milestones)
- **No queue/persistence/cancel** — synchronous + in-memory (M4 makes it durable,
  resume-paused, single GPU worker, cancel, VRAM admission).
- **No UI wiring** — `/generate` is HTTP-only for now; the generate bar + selectable
  **batch grid** is **M2** (single image now; N-image batch next).
- No VRAM estimate / progress streaming yet (M3 + P0-14).

### Next (M2)
Wire the generate bar → N-image batch → selectable grid (click = preview). *Skeleton done.*

---

## M2 — batch grid on screen — 2026-06-04

**Goal (spec §12):** generate bar fires an **N-image batch** → results **stream into a
simple selectable grid** in the stage; click = preview. The smoke payoff / casting-grid
embryo. ✅ **DONE — Phase A walking skeleton complete (integration proven).**

### Done
- **`orchestrator/runner.py`** — in-memory **single-worker FIFO runner** (`JobRunner` +
  `RUNNER` singleton): a daemon thread pulls one `job_id` at a time off a `queue.Queue`
  and runs the adapter subprocess. **One job at a time** — the same never-co-load-two-
  models invariant M4's durable queue keeps (§7). Naive on purpose: no persistence /
  resume / cancel / VRAM admission (all M4).
- **`POST /generate`** now takes **`count` (1–`MAX_BATCH`=8)**; enqueues N jobs under one
  `batch_id` and returns `{batch_id, count, job_ids}` immediately (non-blocking). Seed
  rule: if `seed` given, image *i* uses **`seed+i`** (distinct but reproducible); else
  random per image. `dry_run` still returns argv only.
- **`GET /jobs`** → `{jobs, counts}` (counts = queued/running/done/failed) for UI polling;
  **`GET /jobs/{id}`**; **`GET /outputs/{name}`** serves a PNG from `dev_out_dir`
  (path-traversal guarded). **CORS** enabled (loopback-only; lets `npm run dev` @1420 +
  the Tauri webview fetch cross-origin). Worker enriches each job result with
  `output_name` (servable basename) + `seed` (read from the manifest).
- **UI** (`app/src/App.tsx` + `lib/orchestrator.ts`): generate bar (prompt + N, Enter to
  fire) → `POST /generate` → polls `/jobs` every 1.2 s while anything is pending →
  **streams cells into a grid** (`queued… → generating… → image`, amber border while
  running, red on fail). **Click selects** → inspector shows preview + seed / wall /
  pipeline-duration / file (+ stderr tail on failure). Dock shows live running/done counts.

### Verified (real GPU batch)
- `POST /generate {count:2, seed:200}` → `bat_c1e27afa`, jobs `[03bbbd87, 0cbb5e6b]`.
- **Streaming + serialization** (poll timeline): `q=1 r=1 d=0` → `q=0 r=1 d=1` →
  `q=0 r=0 d=2` — exactly one running at a time, results appearing progressively.
- Seeds **200 / 201** (the `seed+i` increment), both `done`, wall **~72–73 s** each
  (warm; M1's 240 s included cold model load).
- **`GET /outputs/<name>` → 200, ~1.13 MB PNG.** Images coherent + distinct (lighthouse).
- Fast checks: orchestrator import/factory OK; dry-run batch argv OK; `tsc && vite build` clean.

### ⚙ New settings / params
| Setting | Value | Where | Note |
| --- | --- | --- | --- |
| Batch cap | `MAX_BATCH = 8` | `main.py` | ≤ R38 cap; UI N input clamps 1–8 |
| Default batch N | 3 | `GenerateRequest.count`, UI | |
| Batch seed rule | `seed+i` if seed set, else random | `/generate` | distinct + reproducible |
| Job poll interval | 1.2 s (UI), stops when no pending | `App.tsx` | replaced by SSE/ws later (§3) |
| Worker model | single daemon thread, FIFO, 1 job at a time | `runner.py` | M4 → durable `queue.py` |
| Output serving | `GET /outputs/{name}` from `dev_out_dir` (traversal-guarded) | `main.py` | M5 → project `out/` |
| CORS | `allow_origins=["*"]` (loopback-only) | `main.py` | token enforcement is P0-16 |

### ⚠ Gaps carried forward
- **In-memory only** — no persistence/resume/cancel/VRAM admission (M4).
- **Orchestrator URL/token not yet surfaced from Tauri** — UI uses the fixed loopback URL;
  token unenforced (deferred with P0-16, low value until enforcement lands).
- Each batch image **reloads the model** (subprocess isolation) — inherent until a
  persistent worker; fine for P0 (R-queue subprocess isolation).
- No coarse progress bar yet (per-image is indeterminate; M3 + P0-14 log streaming).

### Next (M3)
Promote the `zimage` adapter to the **full contract** (§8): `capabilities()`/presence,
normalized envelope (have it), **coarse/indeterminate progress**, **cancel = subprocess
kill**, manifest-status-as-truth; run the 1-page contract check (§15) and log findings.
*(Highest residual risk — do it right after the skeleton, §14.)*

---

## Review & hardening pass (breather before M3) — 2026-06-04

User called a breather to (1) resolve a concern that the repo couldn't run without the
parent monorepo's pipelines, and (2) get a code review/sanity check.

### Sanity check
- **Confirmed:** the loom repo had **no pipeline code** — it shelled out to the parent
  `src/pipeline/`. A clone could not generate. (The `lib/` gitignore bug from M2 was the
  other "missing code" issue, already fixed.)
- `git ls-files` audit: all 81 tracked files present; only `__pycache__/` ignored. No
  other source silently untracked.

### Decision — vendor pipeline code per-phase (chosen over vendor-all / keep-referencing)
- **Vendored `zimage` + `_artifact_id.py`** → in-repo `pipelines/` (mirrors the parent's
  `src/pipeline/` layout: a root that directly holds `zimage/` + `_artifact_id.py`).
  Dependency closure verified first (zimage needs only its own stages + `manifest.py` +
  sibling `_artifact_id.py`; torch/diffusers/PIL stay in `.venv`). Weights still **never
  vendored** (R160).
- **config refactor:** `pipelines_root` (single) → **`pipeline_roots`** (ordered list:
  in-repo `pipelines/` **preferred**, parent `src/pipeline` fallback) + split out
  **`src_root`** so `models_dir` (weights) resolves independently. New env:
  **`LOOM_PIPELINES_DIR`** (replaces `LOOM_PIPELINES_ROOT`), **`LOOM_SRC_ROOT`**.
- **adapter:** `script_path(root)` → **`resolve_script(roots)`** (first existing,
  vendored-first); `present(roots)`; `build_argv(spec, python, script)` takes the resolved
  path. `runner`/`/generate` resolve per job; `/version` now reports `pipeline_roots` +
  the resolved `zimage_worker`.
- **Verified:** `/version` + dry-run resolve the **vendored** worker; a **real gen with
  `LOOM_PIPELINES_DIR` forced to in-repo only** → `ok=True, seed 7, 73 s` (proves the repo
  is self-contained, no parent fallback). Others (`multi`/`wan2`/… ) still reference the
  parent until vendored in their phases.

### Review fixes applied now
- **🔴 Orchestrator sidecar lifecycle (`lib.rs`):** removed the `std::mem::forget(child)`
  leak; the `Child` is now held and **killed on `RunEvent::Exit`** (`build()` + `run(|_,
  event| …)`). Prevents an orphaned orchestrator holding port 8765 and breaking the next
  launch. `cargo build` clean.
- **🟡 `.gitignore` hardening:** anchored `build/`/`dist/` → `/build/`/`/dist/` (and the
  M2 `/lib/`,`/lib64/` fix) so root-only Python-packaging patterns can't swallow nested
  app dirs again. Added an explanatory note in the file.

### Review items deferred (slated, not now)
- `parse_result` newest-PNG fallback → **manifest-status-as-truth in M3**.
- Unbounded in-memory `JOBS` / `/jobs` returns all → bounded in **M4** durable queue.
- Token unenforced (P0-16); width/height ÷16 not pre-validated (worker surfaces it).

### ⚙ Settings changed
| Setting | Old | New |
| --- | --- | --- |
| Pipeline root | `pipelines_root` = parent `src` | **`pipeline_roots`** = [in-repo `pipelines/`, parent `src/pipeline`] |
| Env | `LOOM_PIPELINES_ROOT` | **`LOOM_PIPELINES_DIR`** (+ **`LOOM_SRC_ROOT`** for weights) |
| Worker invocation cwd | monorepo root | `script.parents[2]` (root of the chosen copy) |
| Sidecar on app exit | leaked (`mem::forget`) | **killed** (`RunEvent::Exit`) |

---

## Security + central config pass (2nd review) — 2026-06-04

User's 2nd code review (5 findings) triaged vs the P0 docs; chose to action the
security fix + a central config + the cheap wins now, leaving #4 for M3.

### Central config (`.env`) — the user-chosen master config
- **Committed `.env`** at the app-repo root (non-secret: host/port, `VITE_LOOM_ORCH_URL`,
  `LOOM_CORS_ORIGINS`) + **gitignored `.env.local`** (the dev token:
  `LOOM_ORCH_TOKEN` + `VITE_LOOM_ORCH_TOKEN`). `.gitignore` flipped to **track `.env`**,
  ignore `.env.local`/`.env.*.local`.
- `config.py` gained a **dependency-free `.env` loader**; precedence **real env >
  `.env.local` > `.env` > default** (all reads routed through `_get`).
- `vite.config.ts` **`envDir: ".."`** so Vite reads the same root `.env` (verified: the
  dev token is inlined into the built bundle).

### #1 (High) — unauth /generate + CORS `*` → **fixed now** (pulled P0-16 forward)
- **Token gate:** `require_token` dependency enforces **`X-Loom-Token`** on **POST
  /generate** (the GPU-spending endpoint) — 401 otherwise. GET reads stay open;
  `/outputs` must (it's loaded via `<img>`). Aligns with no-surprise-GPU (R141–143).
- **CORS restricted** from `*` to known origins (`localhost:1420` + `tauri.localhost`),
  `LOOM_CORS_ORIGINS`-overridable. Defense-in-depth; the token is the real gate.
- **Token → UI plumbing:** the Tauri shell `eval`s `window.__LOOM_TOKEN__` +
  `__LOOM_ORCH_URL__` into the webview when it parses the READY line (`lib.rs`); the UI
  reads those (Tauri) or `VITE_LOOM_ORCH_TOKEN` (`npm run dev`) and sends the header.
- **Verified:** 401 no-token / 200 with-token (dry-run).

### #2 (Med) — width/height validated at the API → **fixed now**
- `GenerateRequest`: `width`/`height` `Field(ge=256, le=2048, multiple_of=16)`,
  `num_steps`/`guidance_scale` bounded, non-empty `prompt` validator. Bad dims now **422
  before any model load** (verified width=100 → 422, empty prompt → 422).

### #3 (Med) — READY printed before bind → **fixed now**
- READY moved into a **FastAPI `lifespan` startup** (runs after uvicorn binds the
  socket) + `RUNNER.start()` there too. A port conflict now fails *before* any false
  READY (verified the line still emits on clean start).

### #4 (Med-low) — parse_result misattribution → **deferred to M3** (as planned)
- Already slated: §8/§15 "manifest-status-as-truth". Optional interim (per-job out dir)
  not taken; will fix properly in M3.

### #5 (Low) — dock under-reports queue → **fixed now**
- `App.tsx` dock now uses the orchestrator's **global `counts`** (from `/jobs`), seeded
  on mount, not just the current `batchIds`.

### ⚙ New settings
| Setting | Value | Where |
| --- | --- | --- |
| Central config | committed `.env` + gitignored `.env.local` | app-repo root; `config.py` `_get`, Vite `envDir:".."` |
| Token enforcement | `X-Loom-Token` on POST /generate | `require_token` (`main.py`); UI sends via header |
| CORS origins | localhost:1420 + tauri.localhost (env `LOOM_CORS_ORIGINS`) | `config.cors_origins` |
| Dim bounds | 256–2048, ÷16; steps 1–200; guidance 0–30 | `GenerateRequest` |
| READY emit | from `lifespan` startup (post-bind) | `main.py` |

### ✅ Runtime-verified + follow-ups
- **Tauri token injection works** — user ran `npm run tauri dev` and Generate succeeds, so
  `window.__LOOM_TOKEN__` (eval'd on READY) → `X-Loom-Token` → 200 is proven end-to-end. #1 closed.
- **UI fix (`2774fbc`):** the Generate button showed a `not-allowed` cursor even when enabled —
  leftover M0-mockup CSS (`cursor:not-allowed` hardcoded). Now `pointer` + hover-brighten when
  enabled; `not-allowed`/dim only via `:disabled` (orchestrator offline). `pipeline`/`mode`
  selects stay disabled-styled on purpose (P0 placeholders).
- **Doc-marking:** annotated `kb-loom-p0.md` so brought-forward work isn't redone — §16 WBS rows
  **P0-16 (✅), P0-15 (◐), P0-1b (◐)** + a "Live implementation status" callout; §4 vendoring note;
  §11.1 token/fetch-status note. Rule of thumb recorded there: **check this journal before starting
  any WP.**

---

## M3 — adapter contract hardening (Phase B begins) — 2026-06-04

**Goal (spec §12 / P0-6, highest residual risk):** promote the `zimage` adapter to the
full contract — `capabilities()`/presence, **coarse progress**, **cancel = subprocess
kill**, **manifest-status-as-truth** — and run the 1-page contract check (§15).
✅ **DONE — all pieces verified on the GPU.**

### Done
- **`capabilities()`** (adapter) + **`GET /capabilities`** — declares modes/params/presence,
  `cancellable`, `progress:"coarse"`, `vram_estimate_gb:null`. Drives the UI / launch gate.
- **Manifest-status-as-truth `parse_result`** (closes review **#4**): success = the saved
  manifest's stages all `completed` **AND** output exists **AND** exit 0; failing stage's
  error is surfaced. Envelope gained `manifest_status` + `error` (`base.py`).
- **Per-job output isolation** — each job writes to `.dev_out/<job_id>/`, so the manifest is
  unambiguous (no newest-PNG misattribution). `output_name` is now `<job_id>/<file>`;
  `GET /outputs/{name:path}` serves nested (traversal-guarded via `is_relative_to`).
- **Runner `Popen` refactor** — streams the worker's merged stdout/stderr line-by-line →
  **coarse progress** (`adapter.progress`: stage1 .25 / stage2 .8 / stage3 .95 / done 1.0)
  + a 60-line **`log_tail`** (P0-14 partial). Tracks the live process per job.
- **Cancel = subprocess kill** — `RUNNER.cancel()` + **`POST /jobs/{id}/cancel`** (token-gated):
  queued → marked `canceled` (worker skips); running → `proc.terminate()`, finalized
  `canceled`, **partial `out_dir` removed**. New `canceled` status throughout (+ counts).
- **UI** — grid cells show a coarse **progress bar** while running, a **✕ cancel** button on
  queued/running cells, and a `⊘ canceled` state; inspector shows `error` + `log_tail` on
  failure/cancel. Cell changed `<button>`→`<div role=button>` (to nest the cancel button).

### Verified (GPU)
- **Cancel a running job:** `status=canceled`, per-job `out_dir` **removed** (partial cleanup).
- **Normal completion:** `done, ok=True, manifest_status=completed, progress=1, seed=11`,
  per-job dir holds exactly `{png,json}`, `/outputs/<job>/<file>` → **200, 871 KB**.
- `/capabilities` returns the contract; orchestrator import + `tsc`/`vite` build clean.

### 1-page contract check — `zimage` (§15)
| Check | zimage reality | Adapter/runner handling |
| --- | --- | --- |
| Exit code on failure | non-zero (save-then-`raise`) | trusted, **cross-checked with manifest** |
| Manifest schema | `stages[].status` (`completed`/`failed`), `output_path`, `pipeline_duration_s` | **manifest-as-truth** for success |
| Output path | auto-named in `--output-dir`; prints `Image:`/`Manifest:` | **per-job dir** → unambiguous; manifest `output_path` authoritative |
| Progress signal | coarse stage prints; per-step tqdm on stderr (`\r`) | **coarse stage mapping only** (no fake fine-grained bar, §15) |
| Cancel behavior | no in-worker signal handling | runner **`terminate()`** (→ kill) + cleanup; acceptable v1 |
| VRAM estimate | not emitted up-front | none yet — static table arrives with the **M4** queue |

### ⚠ Carried to M4 (durable queue)
- Static per-pipeline **VRAM estimate table** + admission; **persistence/resume** (`queue.json`);
  capped auto-retry-with-offload; bound the in-memory `JOBS`/log history.
- Crash-recovery half of **P0-15** (mark in-flight `failed` on sidecar restart) still owed.

### Next (M4)
Replace the in-memory runner with the **durable, resume-*paused* queue** (`queue.json`, single
GPU worker, cancel [have it], VRAM-aware admission, capped auto-retry-offload) — R69/R78/R88/R159.

### M3 follow-up — review #3 (4 findings, all fixed now) — 2026-06-04
- **#1 Honest contract** — `capabilities()` advertised img2img/inpaint + `init_image` etc. that
  `GenerateRequest` silently dropped. Now: capabilities lists **t2i only** (`WIRED_MODES/PARAMS`;
  full CLI capability shown as `worker_modes`), `mode: Literal["t2i"]`, and **`extra="forbid"`**
  so unknown params **422** instead of vanishing. img2img/inpaint stay **P1**. (Verified: caps =
  t2i only; `mode=img2img`→422; `init_image`→422; t2i→200.)
- **#2 Cancel grace-kill** — `cancel()` was `terminate()`-only; a worker ignoring SIGTERM would
  block the worker thread (stdout read) and stall the queue. Added `_grace_kill`:
  `wait(timeout=5)` then `kill()` in a daemon thread.
- **#3 Cancel/completion race** — a cancel landing between subprocess-exit and finalization could
  delete a just-completed output. Fixed two ways: `cancel()` is a **no-op once the proc has exited**
  (`poll() is not None` → return False/409), and the worker only treats it as canceled when the run
  **didn't actually succeed** (`canceled and not rec.ok`) — a cancel that loses the race keeps the
  finished image (→ done).
- **#4 README** refreshed to **M0–M3** (token enforced, cancel/capabilities present; t2i-only noted).

---

## M4 — durable, resume-paused queue — 2026-06-05

**Goal (spec §12 / P0-7):** replace the in-memory runner with a **durable `queue.json`**,
single GPU worker, **resume *paused*** on relaunch, the one-job-lifecycle reconcile (R159),
static **VRAM-estimate admission**, and **capped auto-retry on OOM**. ✅ **DONE — verified.**

### Done (`runner.py` rewrite + `config.py` + `main.py` + UI)
- **Durable**: every state change writes `queue.json` **atomically** (temp + `fsync` +
  `os.replace`). Lives at `.loom_state/queue.json` (env `LOOM_STATE_DIR`; gitignored) —
  **M5 relocates to `<project>/jobs/queue.json`** (R72). Job record gained `schema_version`,
  `requester_id` (default `"sandbox"`), `vram_estimate_gb`, `resumable`, `retry_count`, `note`.
- **Worker is now table-driven** (no `queue.Queue`): a `threading.Condition` worker picks the
  **oldest `queued`** job (FIFO by `created_at`) when **not paused**; submit/unpause/cancel
  `notify()`. Single job at a time (the §7 invariant) preserved; cancel (M3) preserved.
- **Resume *paused* (R88)**: on load, any `queued` work → **`paused=True`** (the queue loads but
  doesn't auto-run); the dock shows `⏸ paused (n)` + an **[unpause]** button.
- **One-job lifecycle reconcile (R159)** at load, for a `running` job in the file:
  **graceful** (clean_shutdown flag, set by the lifespan shutdown which re-queues running jobs) →
  `queued` + partial discarded; **crash** (no flag) → `failed` + partial discarded; **resumable**
  (P2 only) → recovered. P0 jobs are all non-resumable.
- **VRAM admission (§7)**: static table `{zimage: 11 GB}` vs `vram_budget_gb` (16, env
  `LOOM_VRAM_BUDGET_GB`); each job carries its estimate (dock shows the budget).
- **Capped auto-retry on OOM**: a failed job whose log matches OOM markers re-queues once
  (`MAX_OOM_RETRIES=1`) with a visible `note`. (zimage already runs cpu_offload; richer offload
  escalation is per-adapter, lands with the video pipelines in P3.)
- **API**: `/jobs` now returns `{jobs, counts, paused, vram_budget_gb}`; **`POST /queue/pause`** +
  **`/queue/unpause`** (token-gated). Lifespan **shutdown** calls `RUNNER.graceful_shutdown()`.
- **UI**: dock shows paused/running/idle + counts (incl. `canceled`) + VRAM budget + an
  **[unpause]** button; poll keeps running while paused-with-pending (so the control stays live).

### Verified
- **T1 durability + resume-paused**: pause → submit 2 (stayed queued, no GPU) → **hard-kill →
  restart same state dir → still 2 `queued`, `paused=True`** (R88). `queue.json` present.
- **T2 crash reconcile**: crafted `running` + `clean_shutdown:false` → load → **`failed`**
  ("orchestrator crashed mid-job").
- **T3 graceful reconcile**: crafted `running` + `clean_shutdown:true` (+a queued) → load →
  **`running`→`queued`**, queue **`paused`**.
- **T4 pause/unpause** API: `True → False`.
- **T5 real job through the durable worker**: `done, ok=True, vram_est=11, progress=1`, ~92 s;
  **`queue.json` persisted `status=done`**. Orchestrator import + `tsc`/`vite` clean.

### ⚠ Carried forward
- **Graceful vs crash on the packaged app**: Tauri kills the sidecar with `terminate()` on exit
  (hard kill on Windows) → that's the **crash** branch (running → failed). A truly graceful
  re-queue needs the shell to ask the orchestrator to stop first (a clean `POST /shutdown` or
  SIGTERM it handles) — **P0-15 refinement**, noted, not yet wired.
- In-memory `JOBS`/log history still unbounded; per-job `jobs/logs/<id>.log` files (P0-14) not yet
  written (log tail is persisted in the record). Disk-guard gating of admission = **M6**.

### Next (M5)
**Durable bundle I/O** + `loom init` full project creation (format + size cap + empty-folder +
free-space validation, footprint estimator) — stable IDs, `schema_version`, atomic writes, JSON
Schemas, file-watch, the lineage edge; **relocate `queue.json` + outputs into `<project>/`**.

### M4 review follow-up (5 findings, all fixed now) — 2026-06-05
- **#1 (High) orphaned GPU worker** — Tauri hard-kills the orchestrator; its `Popen` worker child
  wasn't reaped → kept spending GPU. Fixed with a **Windows Job Object (`KILL_ON_JOB_CLOSE`)**:
  every worker is `AssignProcessToJobObject`'d, so when the orchestrator dies *for any reason* the
  worker dies too. Plus `graceful_shutdown()` now **terminates live workers** and a `_shutting_down`
  guard makes the clean-stop path **re-queue** the in-flight job (not mark it failed because we
  killed it). **Verified:** worker alive → hard-kill orchestrator only → worker **reaped** (gone).
- **#2 (High) VRAM admission not enforced** — `/generate` now **rejects `est > budget` with 422**
  (was recorded only). **Verified:** `LOOM_VRAM_BUDGET_GB=1` → zimage (est 11) → **422**.
- **#3 (Med) resume-paused not reviewable** — the UI now **seeds the grid from the persisted
  pending jobs** on load, so after a relaunch they appear with their ✕ cancel before unpause
  (R88 "Review/Unpause").
- **#4 (Med-low) retry-with-offload** — the OOM retry now calls an optional adapter
  `escalate_offload(params, attempt)` hook; **zimage has none → plain retry** (cpu_offload already
  on), real offload escalation lands with the video pipelines' group/sequential modes in **P3**.
- **#5 (Low) README** refreshed to **M0–M4** (durable queue; the packaged-app hard-kill →
  worker-reaped-but-job-failed nuance is noted as a P0-15 refinement).

> **P0-15 refinement still owed (noted):** the packaged app exit is a hard kill → the worker is
> reaped (no orphan) but the in-flight job becomes `failed` (crash branch), not gracefully
> re-queued. A clean re-queue needs the Tauri shell to ask the orchestrator to stop first
> (`POST /shutdown` or a handled signal) before falling back to kill.

**Follow-up (2 Low, `32a5834`):** Job Object failures are now **loud** (warn to stderr on
Create/SetInformation/Assign failure) instead of silent, and **`/version` exposes
`worker_reap: "job_object" | "none"`** so the reap mechanism's state is observable. Stale strings
fixed: README `runner.py` label → "durable, resume-paused single-worker queue"; `/version`
`token_required` now lists **all** gated endpoints (`/generate`, `/jobs/{id}/cancel`,
`/queue/pause`, `/queue/unpause`), not just `/generate`.

---

## M5 — durable bundle I/O + `loom init` project workspace — 2026-06-05

**Goal (spec §5/§6 / P0-8 + P0-9):** the **persistence core** every later record reuses (stable
IDs, `schema_version`, atomic temp+`fsync`+rename, JSON Schemas, the lineage edge) **and** `loom
init` full project creation (format + size cap + empty-folder + free-space validation + footprint
estimator) — **relocating `queue.json` + outputs out of the interim `.loom_state/` + `.dev_out/`
into a real per-project `<project>/` workspace** (R72). ✅ **DONE — verified on GPU.**

### New modules
- **`workspace.py`** — the bundle-I/O spine:
  - `new_id(prefix)` → stable `prj_…`/`job_…` IDs (refs use IDs, never paths).
  - `atomic_write_json` (temp → `fsync` → `os.replace`) + `read_json` that **refuses partial/
    corrupt JSON** (parse error raises, never a silent empty record).
  - **Dependency-free JSON-Schema validation** (`validate`) — a draft-07 subset checker
    (`required`/`type`/`const`/`enum`/`pattern`/`minimum`) driven by the authored schema files;
    no `jsonschema` dep (R97/R103 "keep orchestrator deps minimal"). `bool`-isn't-`int` guarded.
  - **`Workspace`** — the `<project>/` tree (`project.json`, `jobs/`, `jobs/logs/`, `lineage/`,
    `_temp/`, `out/`) with `create()` (validates empty dest **R80** + free space ≥ cap **R80**,
    builds the tree, writes `project.json` atomically) and `open()` (schema-validated load, heals
    a missing subdir). `info()` feeds the API/UI.
  - **Footprint estimator (R161/R164)**: `estimate_footprint_gb` (length_s × fps × W×H ×
    `PNG_BYTES_PER_PIXEL=1.5`) + `suggest_cap_gb` (footprint × 1.3, ceil-to-10, floor 50) +
    `footprint_report` (adds a **warning** when the chosen cap < projected master).
  - Defaults: `DEFAULT_FORMAT` = Wan 1280×720 @24fps + WAV 48k/16/stereo (R56); `DEFAULT_SIZE_CAP_GB`
    = 250 (R164); `MIN_SIZE_CAP_GB` = 50 (R79 floor, no max).
- **`lineage.py`** — per-output **lineage edge** (R98): `{requester_id → job_id → output_file →
  manifest}` with `asset_version`/`lora_version` slots present but `null` at P0. Stored in a
  **rebuildable** `lineage/index.json` (atomic, schema-validated); `record_output` is **idempotent
  per job_id** (replaces on retry); a corrupt index → start fresh (never block generation).
- **`projects.py`** — lifecycle glue: `create_project`/`open_project` (validate → `Workspace` →
  **`RUNNER.bind`** → write the app pointer), `resolve_startup` (re-open the last project on launch),
  and the **app-level pointer** `<app state>/app.json` (records *which* project is active + recent
  list — the one piece of state that legitimately stays outside any project).
- **`orchestrator/schemas/`** — `project.schema.json`, `job.schema.json`, `manifest.schema.json`,
  `lineage.schema.json` (the four P0 record types, §6).

### Changed
- **`runner.py` is now workspace-bound.** `bind(ws)` loads `<project>/jobs/queue.json` (R159
  reconcile, resume-paused R88) and points outputs at `<project>/out/<job>/`; the worker **idles
  until a project is bound**. `_persist_locked` uses `workspace.atomic_write_json`; job IDs via
  `new_id`. **Per-job log file** `jobs/logs/<id>.log` now written (full subprocess stdout/stderr —
  **P0-14 folded in** now that the workspace exists; the in-memory tail still drives the live pane).
  On success it writes the **lineage edge** (best-effort — a lineage failure never fails a good gen).
  `bind` **refuses while a job is running** (can't strand a live worker on the old project).
- **`config.py`**: `.loom_state/` is now **app-level** state only (`app.json` pointer); added
  `app_pointer_path`, `work_disk_root` (R72 default `F:\_tmp`, env `LOOM_WORK_DISK`),
  `project_dir_override` (env `LOOM_PROJECT_DIR` — tests/CI/GPU-verify auto-open/create). `queue_path`
  /`dev_out_dir` demoted to **legacy** (dry-run scratch only).
- **`main.py`**: `/generate` now **409s until a project is open** and stamps `requester_id` = project
  id (lineage). New endpoints: **`GET /project`** (info), **`POST /project`** (loom init; token),
  **`POST /project/open`** (token), **`POST /project/estimate`** (footprint; unauth pure calc).
  `/outputs` serves from the **active project's** `out/`. `/version` adds `work_disk_root` +
  `active_project`; `token_required` adds `/project` + `/project/open`. Lifespan calls
  `projects.resolve_startup()` after `RUNNER.start()`.
- **UI**: titlebar shows the **active project name** + **`+ New` / `Open`** buttons (prompt-based
  `loom init` — shows the footprint estimate before asking for the cap; a native folder picker +
  full format/cap wizard is a later UI pass). Generate is **disabled with no project open** + a
  banner; dock disk meter reads the project's `size_cap_gb`. New client fns in `orchestrator.ts`
  (`getProject`/`createProject`/`openProject`/`estimateFootprint`); `generate` surfaces 409.

### Verified
- **Backend smoke** (TestClient + unit): footprint 30-min 720p24 → **55.6 GB** master, suggests
  80 GB; **empty-folder / free-space / floor / bad-schema / partial-JSON all refused**; lineage edge
  written + idempotent; `/generate` pre-project **409**; create without token **401**; create with
  token **200**; dry-run `output_dir` under `<project>/out`; API non-empty dest **400**.
- **End-to-end on GPU**: auto-created project bound at boot → real `zimage` gen (seed 700) →
  **PNG in `<project>/out/<job>/`**, **lineage edge** (requester = `prj_…`), `/outputs` served the
  886 KB PNG (200), **per-job log + `queue.json` in `<project>/jobs/`**. Then pause → enqueue a 2nd
  job (stayed queued, persisted) → **hard-kill → relaunch same project** → **resume-paused**
  (`paused=True`), **job1 durably `done`, job2 durably `queued`**. `tsc` + `vite build` clean.

### ⚙ Settings / parameters added
- `LOOM_WORK_DISK` (default `F:\_tmp`) — default parent for new project workspaces (R72).
- `LOOM_PROJECT_DIR` — force-open/create a project at startup (tests/CI/GPU-verify); auto-create
  uses the **50 GB floor** cap so it works on any disk with ≥50 GB free.
- `LOOM_STATE_DIR` — now **app-level** state dir (`app.json` pointer), not the queue.
- `workspace.PNG_BYTES_PER_PIXEL = 1.5` — footprint heuristic; tune once real PNG masters exist.
- `DEFAULT_SIZE_CAP_GB=250` / `MIN_SIZE_CAP_GB=50` (R164/R79); `DEFAULT_FORMAT` = Wan 1280×720@24 + WAV.

### ⚠ Carried forward
- **File-watch** (§6 "surfaces external edits read-only, debounced") is **not yet wired** — writes
  are atomic + orchestrator-owned, but the read-side watcher/change-event bus is deferred (no second
  writer in P0; revisit when the React store projects bundle files). Noted, low risk for P0.
- Native **folder picker** + full **format/fps/audio + cap wizard** UI (prompt-based for now).
- Switching projects requires the queue **idle** (409 if a job is running) — fine for single-user P0.
- Still owed from M4: **P0-15** graceful re-queue on packaged-app exit; unbounded in-memory `JOBS`.

### M5 review follow-up (2 findings, both fixed now) — 2026-06-05
- **#1 (High) corrupt queue masked-as-empty + overwritten** — `_load_locked` read the queue but
  never validated it, and a corrupt file → warn + open with an **empty** queue, so the next write
  **overwrote recoverable history** (and a valid-but-malformed record could reach the worker missing
  `id`/`status`). Fixed: the loader now (a) **validates the envelope + every job against
  `job.schema.json`** (and checks the dict key == `id`), and (b) **quarantines** any corrupt/invalid
  queue — renames it to `jobs/queue.corrupt-<utcstamp>.json` **before** starting a fresh empty queue,
  so the bad bytes are preserved, never overwritten. If the rename itself fails, it warns and leaves
  the original untouched (skips the load-time rewrite). **Verified:** corrupt `{ not json` →
  quarantined (original bytes intact) + fresh empty `queue.json`; a job missing `status` → quarantined;
  a valid queue with a valid job → kept (no false-positive), `paused` preserved.
- **#2 (Med) project schema under-enforced** — the dependency-free validator ignored `minLength`,
  `minItems`, `maxItems` (and `maxLength`/`maximum`), so `POST /project` accepted `aspect:[16]` /
  `resolution:[1280]`, undermining the "format locked together" foundation. Added those constraints
  to `_validate`. **Verified:** `aspect:[16]`, `resolution:[1280]`, `aspect:[16,9,3]`, `aspect:[]`,
  and empty `name` are all now rejected; a well-formed `[X,Y]` format still passes.

### M5 review follow-up #2 (2 findings, both fixed now) — 2026-06-05
- **#1 (Med) queue *envelope* trusted without validation** — jobs were schema-checked but the
  wrapper wasn't, so `clean_shutdown:"false"` (a string) → `bool("false")` = True → a running
  non-resumable job was **re-queued as graceful instead of crash-recovered** (violates the R159
  contract). Added **`queue.schema.json`** (typed `schema_version`/`paused`/`clean_shutdown`/`jobs`)
  and `_load_locked` now **validates the envelope** before trusting any field, using the validated
  boolean directly (no `bool(str)` coercion). A bad envelope **quarantines** the whole queue.
  **Verified:** string `clean_shutdown`, non-bool `paused`, `schema_version:2` all quarantine;
  proper bools still drive **crash→failed / graceful→queued** correctly.
- **#2 (Med) inconsistent format geometry accepted** — array lengths were enforced but not the
  **aspect⇄resolution lock**, so `aspect:[1,1]` with 1280×720 passed. Added **`validate_project`**
  (schema + the cross-field invariant `aspect_w·H == aspect_h·W`), used in create/open/load_project;
  `create` now validates **before** building the tree (no half-built dir on a bad format).
  **Verified:** 1:1, 4:3, 16:10 vs 1280×720 rejected; 16:9@1280×720 and 4:3@640×480 accepted.

### Next (M6)
**Disk guard** — two-threshold continuous polling (warn <5% / hard-stop <2%, project-cap aware,
R45/R56/R79/R80), a dock usage meter, and **gating queue admission** on free space.

---

## M6 — disk guard — 2026-06-05

**Goal (spec §9 / P0-10, R96):** a **continuously-polled** guard with **two measures × two
thresholds** that **gates job admission** on space (R96 reverses the validate-only stance — not
just a check at `loom init`). ✅ **DONE — verified (logic + real GPU).**

| Measure | Source | Warn | Hard stop |
| --- | --- | --- | --- |
| Project-cap headroom | project folder size vs `size_cap_gb` | <5% left | <2% left |
| Disk free space | work-disk free vs total | <5% free | <2% free |

### Done (`diskguard.py` + runner + main + UI)
- **`diskguard.py`** — `DiskGuard` owns a **daemon poll thread** (`LOOM_DISK_POLL_S`, default 5 s)
  that recomputes + caches status. `WARN_PCT=5`, `HARD_PCT=2`. State = `ok|warn|hard` = worst of the
  two measures; `blocked = state=="hard"`; `reason` names the offending measure(s). Disk free via
  `shutil.disk_usage` (cheap); project size via `os.walk` sum (best-effort, tolerates raced deletes).
  Reads the active workspace through an injected getter and **wakes the runner** (injected callback)
  when a hard-stop **clears**, so dispatch-held jobs resume the instant space frees.
- **Runner dispatch gate** — `set_disk_gate(fn)` + `wake()`; the worker's wait condition now also
  blocks on `_disk_blocked()`, so a queued job **won't start under a hard-stop** (running jobs
  finish — §9). No disk import in `runner.py` (gate + wake are injected from `main`).
- **`main.py`** — `GUARD` (getter=`RUNNER.workspace`, on_change=`RUNNER.wake`); lifespan starts it
  after `resolve_startup`, stops it before `graceful_shutdown`, and wires `set_disk_gate`.
  **`/generate` → 507** (Insufficient Storage) when hard-blocked, **before** admitting/enqueuing
  (nothing is queued). **`/jobs`** now carries `disk` (live status for the dock); new **`GET /disk`**.
  Project **create/open refresh the guard immediately** (don't wait up to one poll for the new
  project's measure to appear).
- **UI** — dock meter shows live **`proj <used>/<cap>G · disk <free%> free`** coloured by state
  (amber warn / red hard, with ⚠/⛔); a **hard-stop banner** explains the block and Generate is
  **disabled** while blocked; `generate()` surfaces the 507.

### Verified
- **Thresholds (unit, patched measurements):** project cap 50 G → used 1 G = **ok** (98%), 48.5 G =
  **warn** (3%), 49.5 G = **hard** (1%, blocked); disk 40% = ok, 4% = warn, 1.5% = hard;
  **worst-of-two** (disk hard + project ok) = hard.
- **Poll-loop recovery:** start hard → free space → guard **clears + fires the wake callback** once.
- **API (TestClient):** `/disk` + `/jobs.disk` populated; under a forced hard-stop **`/generate` →
  507 and nothing is admitted** (`queued==0`); `RUNNER._disk_blocked()` toggles with the gate and
  clears on recovery.
- **Real GPU:** with the guard live (state ok, ~12% disk free), a real `zimage` gen ran to **done**
  (no false-positive block), output landed in `<project>/out/`, and `/jobs.disk` tracked usage.
  `tsc` + `vite build` clean.

### ⚙ Settings / parameters added
- `LOOM_DISK_POLL_S` (default 5 s) — guard poll cadence.
- `diskguard.WARN_PCT=5` / `HARD_PCT=2` — the two thresholds (both measures).

### ⚠ Carried forward
- Project size is an **`os.walk` sum each poll** — fine for P0/small projects; once PNG-sequence
  masters make projects large this wants an **incremental accountant** (track bytes on write) rather
  than a full re-walk. Noted, low risk now (5 s cadence, single user).
- Admission uses the **cached** status (≤ poll-interval stale) + a fresh refresh on project change;
  no per-request re-walk (deliberate — keeps `/generate` fast).

### M6 review follow-up (1 Low, fixed now) — 2026-06-05
- **(Low) DiskGuard not restartable in-process** — `stop()` set `_stop` but never joined/cleared
  the thread, and `start()` early-returned when `_thread is not None`, so a **second lifespan**
  (tests/dev reuse) printed READY with the **poller dead** — `/disk` could serve a stale state while
  looking alive. Fixed: `start()` now seeds + recreates the thread **only if the old one isn't
  alive** (clearing `_stop` first); `stop()` **joins** the thread (timeout `poll_s+1`) and sets
  `_thread=None`. **Verified:** lifespan #1 alive → exit joins/clears → lifespan #2 **alive again**
  and actually polling (force hard → `/disk` refreshes to hard after a poll; recover → ok).

### Next (M7)
**Launch gate + component manifest** — presence-only hard-require at startup (R91/R97);
`orchestrator/components.py` with the phase-scoped 3-state model; refuse to start only on a
**P0-essential code component** missing (zimage/queue/workspace I/O); a missing **P0-essential
weight** offers the HF fetch first, fails only if that fails (R163, §11.1).

---

## M7 — launch gate + component manifest — 2026-06-05

**Goal (spec §11/§11.1 / P0-11, R91/R97/R163):** a **presence-only**, **phase-scoped** launch gate
that hard-requires only what the built phases need (P0: `zimage` + queue + workspace I/O), with the
**3-state model** so P0 never demands P3/P6 components; a missing **code** component refuses to
start, a missing **weight** offers an **explicit on-demand HF fetch** first (R163). ✅ **DONE —
verified (logic + real boot).**

### Done (`components.py` + main + UI)
- **`components.py`** — the manifest + gate. **3 states** (§11): `phase-essential` (active phase +
  missing → blocking), `installed-but-unavailable` (present, phase not active — reported), `missing`
  (declared/future = non-blocking). `active_phases()` = `{P0}` (env `LOOM_ACTIVE_PHASES`).
  - **Code components (P0):** `zimage` (worker script resolves), `queue` (runner import +
    queue/job schemas), `workspace_io` (all 5 schemas load + an **atomic write→read→validate
    roundtrip**). A real broken install (missing schema, unwritable) is caught, not assumed-OK.
  - **Weight components** from `models.json`: `hf_diffusers` presence via
    `huggingface_hub.try_to_load_from_cache(repo_id, "model_index.json")`; `file` via target-path
    existence (relative to monorepo/app-repo root).
  - `launch_report()` → `{active_phases, code_ok, weights_ok, launch_ok, blocking, weights_missing,
    components[]}`; `gate()` raises **`LaunchError`** only on a missing P0 **code** component;
    `weights_ok()` is the light check for the `/generate` precondition;
    `fetch_missing_weights()` = explicit on-demand fetch (`snapshot_download` / `hf_hub_download`).
- **`main.py`** — lifespan runs `components.gate()` **before** the worker/guard start; a `LaunchError`
  prints `LOOM_ORCH_LAUNCH_REFUSED …` and **re-raises → uvicorn aborts startup** (refuse-to-start, no
  degraded mode). A missing **weight** does **not** abort — it's logged + cached in `_LAUNCH`.
  `/generate` → **412** when a P0 weight is missing (offer fetch, don't fail mid-GPU-run). New
  **`GET /components`** (live report) + **`POST /components/fetch`** (token). `/health` gains
  `launch_ok`/`weights_ok`; `/version` `token_required` adds `/components/fetch`.
- **UI** — the connect probe fetches `/components`; a **missing-weight banner** lists the weights +
  a **[Fetch now]** button (`POST /components/fetch`, explicit/on-demand) and **Generate is disabled**
  while `weights_ok=false`; `generate()` surfaces the 412. (A missing **code** component means the
  orchestrator never starts → the existing "orchestrator: offline" state covers it.)

### Verified
- **Happy path (unit + real boot):** all 3 P0 code components `phase-essential` (present);
  `zimage-turbo` weight cached → `phase-essential`; the **P3/P4 Qwen weights → `installed-but-
  unavailable`** (present but inactive phase, **non-blocking**); `code_ok/weights_ok/launch_ok` all
  true; `gate()` doesn't raise; a real orchestrator boot serves `/health launch_ok=true` + `/components`.
- **Refuse-to-start:** forcing the zimage script unresolvable → `gate()` raises `LaunchError`
  ("missing P0-essential code component(s): zimage …").
- **Missing weight:** forced missing → `code_ok=true` (no hard refuse), `weights_ok=false`,
  `weights_missing=["zimage-turbo"]`; `gate()` still passes; `/health.weights_ok=false`; `/components`
  reports it; **`/generate` → 412**. `tsc` + `vite build` clean.

### ⚠ Carried forward
- **sha256 checksum verify** on fetch is **TODO** until the companion HF repo publishes hashes
  (`models.json` sha256 = null; presence is existence-only per R97). `hf_diffusers` integrity rides on
  hub etags; `file` fetch is checksum-TODO.
- The fetch endpoint downloads but the **companion repo is still a placeholder** (only `hf_diffusers`
  /`zimage` is real on this rig); the `file`-type fetch path is wired but unexercised until publish.
- Launch gate is **presence-only** (R97); version-pinning is post-v1.

### M7 review follow-up (2 findings, both fixed now) — 2026-06-05
- **#1 (Med) broken `models.json` silently green** — `_load_models_manifest` swallowed read/JSON
  errors and returned `{"models": []}`, so a missing/malformed weight contract reported **no weights
  required** (`weights_ok=true`, `/generate` 200). Fixed: it now **raises `ManifestError`** (missing
  file / unparseable / no `models` list), and the **manifest is itself a P0-essential code
  component** (`models_manifest`) — a broken one → `code_ok=false` → **`gate()` refuses to start**;
  `weights_ok()` returns `(False, ["models.json: …"])` (never silent-green). A valid **empty**
  `models: []` is still fine (genuinely no weights). **Verified:** broken → blocking
  `models_manifest` + gate refuses; empty-but-valid → still green.
- **#2 (Med-low) file-type fetch didn't satisfy its own presence check** — `_weight_present` checks
  the declared `target` path, but `fetch_missing_weights` called `hf_hub_download` (cache only) and
  never placed the file at `target`, so the fetch "succeeded" yet the weight stayed missing. Fixed:
  the `file` branch downloads with **`local_dir=<target dir>`** so the file lands at `target`;
  presence is re-checked after. (Future-phase — the Qwen `file` entries — but the path now actually
  works.) **Verified** with a fake `hf_hub_download`: target created, `_weight_present`→true,
  `fetched=true`.

### Next (M8)
**Acceptance** — run the **§1 seven-step acceptance test** green end-to-end (now the grid replaces
"one image": step 5 = the batch lands in the grid with manifests + lineage); record any worker-
contract gaps for later per-pipeline onboarding.

---

## M8 — §1 seven-step acceptance — 2026-06-05  ✅ **7/7 GREEN → P0 ACCEPTED**

**Goal (spec §1 / P0-13):** run the seven-step acceptance test that proves the foundation, end to
end, with a **real GPU** batch. Done via a one-shot harness driving a real orchestrator subprocess
(steps 2–6) + an in-process disk-threshold check (step 7); step 1 cited (Tauri GUI behavior).

| # | Step | Result |
| --- | --- | --- |
| 1 | **Single instance** (R74) — 2nd launch focuses the window | ✅ cited (Tauri single-instance plugin, `src-tauri/lib.rs`, M0; GUI-only) |
| 2 | **Launch presence gate** (R91/R97/R163) | ✅ `code_ok` + `launch_ok` true; all 4 P0 code components (`zimage`/`queue`/`workspace_io`/`models_manifest`) **phase-essential** |
| 3 | **`loom init`** — empty-folder + free-space + locked format (R79/R80/R164) | ✅ non-empty dest → **400**; init → 200; `project.json` format `[16,9]@1280×720`, cap 50 |
| 4 | **Shell + dock shows VRAM + disk** | ✅ `/jobs` carries `vram_budget_gb=16` + live `disk` (free% + project used) |
| 5 | **N-image `zimage` batch → grid + per-job manifest + lineage** (R98) | ✅ **real GPU** batch of 2 → both `done`, each PNG in `out/<job>/` + sidecar manifest + lineage edge, `/outputs` **200** |
| 6 | **Quit mid-job → resume PAUSED** (R69/R78/R88) | ✅ via the **real** desktop quit path (P0-15 graceful handshake, see below): relaunch → **`queued`** + **paused** + **partial discarded** |
| 7 | **Disk guard warns <5% / hard-stops <2%** (R96) | ✅ 3% headroom → `warn`; 1% → `hard`+`blocked`; `/generate` → **507** |

**Result:** `P0 ACCEPTANCE: 7/7 steps PASSED`. (The acceptance harness was a temp script; the only
committed code change for M8 is a runner robustness fix below.)

> **⚠️ Step-6 correction (review):** the *first* M8 pass proved step 6 with a **crafted**
> `clean_shutdown:true` reconcile — but the **actual desktop quit hard-killed the sidecar**
> (`lib.rs` `RunEvent::Exit` → `kill`), so the real path hit the **crash branch → `failed`**, not
> the graceful branch. Marking it green on a path the product didn't take was wrong. Fixed by
> **closing P0-15** (below); step 6 now passes for the genuine quit sequence.

### Code change
- **`runner.bind()` clears `_shutting_down`** — `graceful_shutdown()` sets it and (in the real flow)
  the process then dies, so it never mattered; but for an **in-process re-bind** (and general
  restartability) a lingering `_shutting_down=True` would wrongly re-queue the next job at finalize.
  Binding a project = operational again, so `bind()` resets it. (Mirrors the M6 guard-restart fix.)

### Worker-contract gaps recorded (for later per-pipeline onboarding)
- **Progress is coarse** (zimage emits stage markers → 0.25/0.8/0.95/1.0, not a fine %); finer
  progress needs per-step stdout from the worker. Acceptable for P0; note for pipelines that can do better.
- **`peak_vram_gb` is not reported** by the zimage worker → the completion envelope's slot stays null;
  observed-peak-driven VRAM estimates (Codex idea) await a worker that prints it.
- **Cancel granularity** is whole-subprocess (no mid-step checkpoint) — fine for image t2i; video/
  training pipelines (P2/P3) will want checkpoint-aware cancel/resume (already flagged via `resumable`).
- **t2i only** wired (img2img/inpaint declared, P1).

### M8 review follow-up — P0-15 closed (graceful shutdown handshake) — 2026-06-05
- **Finding (High):** step 6 didn't hold for the **real desktop quit** — Tauri hard-killed the
  sidecar on exit, so the orchestrator died **without** running `graceful_shutdown()`; the reload
  took the **crash branch → `failed`** (reproduced: `clean_shutdown:false`→failed,
  `true`→queued+paused). The first M8 pass used a crafted clean state, masking this.
- **Fix (closes the long-owed P0-15):**
  - **Orchestrator** — new token-gated **`POST /shutdown`** → `RUNNER.graceful_shutdown()` (+ stop
    the disk guard): re-queues the in-flight job + persists `clean_shutdown:true`, process stays up
    (Tauri kills it next). The worker `_run_loop` now also blocks on `_shutting_down` (no re-dispatch
    after a shutdown request), and the `_execute` shutdown branch **discards the partial** so
    'partial discarded' is deterministic regardless of process-death timing.
  - **Tauri (`lib.rs`)** — on `RunEvent::Exit`, `graceful_shutdown_orchestrator()` sends a raw
    loopback `POST /shutdown` (with the token; no HTTP crate — one fixed request over `TcpStream`,
    2 s/5 s timeouts), blocking until the orchestrator responds (i.e. the clean state is persisted),
    **then** `kill_orchestrator()` as the fallback. `cargo build` clean.
- **Verified (real quit sequence, deterministic):** **A** — submit → running → `POST /shutdown` →
  kill process → relaunch ⇒ **`queued` + paused + partial discarded**. **B (contrast)** — running →
  hard kill **without** the handshake → relaunch ⇒ **`failed`** (crash branch unchanged). So the
  handshake is exactly what flips quit-mid-job from FAILED to QUEUED+PAUSED — step 6 genuinely holds.

### P0 DONE-LINE
**M0–M8 complete + accepted** (incl. the P0-15 graceful-shutdown closure). The durable spine stands:
single-instance shell + orchestrator
handshake, phase-scoped launch gate, per-project workspaces with atomic schema'd bundle I/O +
lineage, a durable resume-paused VRAM-aware queue, the hardened `zimage` adapter contract, a
continuously-polled disk guard, and the seven-step acceptance green on real hardware. **Next: P1**
(see `kb-loom-p1.md`) — img2img/inpaint + the multi-ref/SD3.5 adapters, World/Asset Studio embryo.

---

## Post-P0 (pre-P1) — safe delete-generation (user findings) — 2026-06-05

Two grid observations from the user before P1. Verdicts + the one we acted on:

1. **Reopening a project doesn't show existing `out/` images in the grid.** *Not a bug — data is
   safe* (each generation is tracked in the `queue.json` record, the `lineage/index.json` edge, and
   on disk in `out/<job>/` + sidecar manifest). The P0 grid only seeds from **pending** jobs and is
   ephemeral by design; the persistent browse-past-generations view is the **casting grid (R44)** in
   the P1 Asset Studio. **Deferred to P1** (user choice).
2. **No safe way to delete a generation.** *Valid gap.* Hand-deleting a PNG orphans its sidecar
   manifest, per-job log, `queue.json` entry, and lineage edge — the exact inconsistency the
   orchestrator-owned-atomic-writes rule prevents. The design anticipates deliberate deletes (§6.7
   "delete this run's temp"; R44 cull; the mockup's "(star/cull, P0-lite)"). **Built now:**
   - `runner.delete(job_id)` — **terminal-only** (done/failed/canceled; a running/queued job must be
     canceled first → 409). Drops the **durable record first** (queue entry + persist), then removes
     `out/<job>/`, `jobs/logs/<job>.log`, and the lineage edge (`lineage.remove_edge`) — so a crash
     mid-delete leaves at worst harmless orphan *files*, never a record pointing at deleted files.
   - **`DELETE /jobs/{id}`** (token-gated) → `GUARD.refresh()` (usage dropped → dock updates).
     `/version token_required` updated.
   - **UI:** a confirm-guarded **🗑** on finished tiles (hover-revealed); removes the tile + clears
     selection. New `deleteJob` client fn.
   - **Verified** (TestClient + fabricated done job): all four artifacts present → delete → **all
     four gone** + persisted; **409** on a queued job; **401** without token; re-delete → 409;
     `/jobs` no longer lists it. `tsc` + `vite build` clean.

---

## Post-P0 (pre-P1) — project picker + configurable logging (user requests) — 2026-06-05

Two usability asks before P1.

### 1. Project picker (registry) — no more typing paths
The app pointer already kept a `recent` paths list; promoted it to a real **registry** + UI picker.
**App-level machine state (gitignored), NOT committed** (project locations are local; committing
would leak paths + break other clones).
- `projects.list_projects()` — recent paths (now keep 20) enriched from each `project.json`
  (name/id/cap) + a **liveness check** (`exists:false` for a moved/deleted one); active flag;
  most-recent-first. `projects.forget_project(path)` drops a stale entry (files untouched).
- **`GET /projects`** (read) + **`POST /project/forget`** (token).
- **UI**: titlebar **"Open ▾"** opens a dropdown of known projects (name + path + cap, ● open
  marker, missing ones greyed) — click to open, **✕** to forget, **"Browse folder…"** keeps manual
  entry. Replaces the bare type-in prompt. New client fns `listProjects`/`forgetProject`.
- **Verified**: registry most-recent-first + active flag; `exists:false` after deleting a folder;
  forget removes it; forget **401** without token.

### 2. Logging — `.env`-configurable brief/verbose, backend + frontend
Was ad-hoc `print`/stderr. Now a real layer (user: valuable for "complicated deliveries").
- **Backend `logsetup.py`**: one `loom` logger → **stderr** (shows by uvicorn in the dev terminal)
  **+ a rotating file** `.loom_state/logs/orchestrator.log` (2 MB ×3, gitignored). Level from
  **`LOOM_LOG_LEVEL`** (`brief`=INFO default / `verbose`=DEBUG / standard names). The ad-hoc
  `_warn` in runner/diskguard/projects now routes through it, plus **lifecycle INFO**: start, launch
  gate, ready, project open, job **queued/running/done**(+wall s)/**failed**, generate batch, disk
  **warn/hard/recovered** (transitions only; per-poll detail at DEBUG), graceful shutdown, delete.
  `config.log_level`/`log_dir`; `/version` exposes `log_level` + `log_file`.
- **Frontend `lib/log.ts`**: console logger gated by **`VITE_LOOM_LOG_LEVEL`** (same brief/verbose
  vocab) + a 300-line ring buffer (for a future in-app log pane). Wired at the user actions (UI
  start, project open/create, generate, delete, errors).
- **`.env`**: documents `LOOM_LOG_LEVEL` + `VITE_LOOM_LOG_LEVEL` (committed defaults `brief`).
- **Verified**: log file written with all lifecycle lines; `verbose` shows DEBUG disk detail;
  level mapping (brief→INFO / verbose→DEBUG / warning→WARNING); `/version` reports the level/path.
  `tsc` + `vite build` clean.
