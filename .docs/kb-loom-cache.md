# kb-loom-cache — managing the model cache from inside loom

*Drafted 2026-09-21 by Claude Code after the flux.2-dev "weights missing" refusal. A plan, not a build: it extends the "fix it for good" remedy into a proper model-cache manager — inventory, health, fetch, repair, delete, and the cache's location on disk — all owned by loom. **Adopted 2026-09-21 as M2.17 — model cache manager** (the author accepted D1–D7 as recommended); spec §12 entry 3k, README and memory swept the same day. **Step a built** (journal "🧰 M2.17 step a"): `orchestrator/weights.py`, the probe over the resolver, the pinned revisions in every worker env, `GET /cache`, `POST /cache/{repo}/repair`, the flux2 worker's `revision=`. **Step b built** (journal "🧰 M2.17 step b"): fetch / verify / move as queued io jobs, delete, prune, pin, the other loaders' pins. **Step c built** (journal "🧰 M2.17 step c"): the location as a setting with the documented precedence, `PUT /cache/location`, `POST /cache/move` with the switch on completion, `DELETE /cache/previous`. **Step d built** (journal "🧰 M2.17 step d"): the v2 Models page, the banner, the composers' Fetch / Repair and weight status. **Step e built** (journal "🧰 M2.17 step e", after the author's first click): the roster **by model** (label · role · where used), whole-repo needs with snapshot fetches, the Hugging Face token as a loom setting (D8), the fetch worker online. **M2.17 is built (a–e); the author's click-through is the acceptance.***

---

## 0. Why

On 2026-09-21 a flux.2-dev generation was refused with `flux2 model 'flux.2-dev' not in cache` although every weight sat on disk. The Hugging Face cache for `Comfy-Org/flux2-dev` holds two snapshots: the June one with all nine files (107 GB) and an August one with only the VAE. The repo's `refs/main` points at the August snapshot. Both loom's pre-flight probe and the flux2 worker resolve files through that ref, so the transformer and the text encoder read as absent.

Nothing in loom can see this, explain it, or fix it. Worse: the standard hub clean-up (`huggingface-cli delete-cache`, or any "prune unreferenced revisions") would treat the June snapshot as garbage and delete the 107 GB that actually work. The cache is also shared with the parent monorepo's other tools, is 745 GB, and its location is a `.env` line that only takes effect at process start. The author wants the whole thing managed from loom, including where it lives.

## 1. What loom does today

| Concern | Where | What it does | Gap |
| --- | --- | --- | --- |
| Location | `config.hf_home` (`LOOM_MODELS_DIR`, default `<work disk>\loom-models`); `main.py` sets `HF_HOME` at startup for the orchestrator and every worker | one setting, read once per process | a change needs an orchestrator restart; nothing validates the path or its free space; no move |
| What is needed | four places: `models.json` `models` (phase weights, 3 entries), `models.json` `multi_presets` (the casting stacks), `model_catalog.py` variants (`repo_id`, optional `probe_files`), `components.postproc_weights(tool)` (BiRefNet, GFPGAN, SD3 tile CN, Turbo LoRA) | each has its own presence check | no single roster; "what uses this repo" is not answerable |
| Presence | `components._hf_cache_probe` → `try_to_load_from_cache(repo, file)` with no revision | follows `refs/main` only | ref drift = false "missing"; the probe cannot say *why* |
| Fetch | `fetch_missing_weights` (phase manifest), `fetch_multi_preset`, `fetch_postproc` via `snapshot_download` / `hf_hub_download` in the request thread | works, on demand | blocks the request, no progress, no cancel, no resume feedback; a 50 GB fetch on a 1 MB/s link is a multi-day HTTP call |
| Delete / prune / repair | none | — | a wrong revision, a partial download or a dead repo stays forever; the only tool is the CLI, which would delete the good snapshot above |
| Verify | TODO since P0 (companion repo sha256) | — | a truncated blob is found by the worker at load time |
| UI | v1: launch-gate banner + Fetch now; the 412 text inline. v2: the 412 text inline only; no banner, no fetch action | — | nothing shows the cache, its size, or a per-model fetch/repair |
| Worker side | `flux2/scaled_fp8.resolve_hf_file` → `hf_hub_download(local_files_only=True)`, no revision; sd35/zimage/krea2 diffusers loaders likewise | offline by default (good) | the same ref-only resolution; the orchestrator's verdict and the worker's can disagree |

## 2. The cache as it is (read-only scan, 2026-09-21)

`huggingface_hub.scan_cache_dir` over `F:\HF_HOME\hub` takes 0.3 s and returns everything the plan needs (repos, revisions, files, sizes, refs). Facts that shape the design:

| Fact | Consequence |
| --- | --- |
| 29 repos, 744.7 GB | the inventory must sort and total; deleting must be deliberate |
| `Comfy-Org/flux2-dev`: `main` → a 1-file revision (0.3 GB); the 9-file revision (107.2 GB) has **no ref** | health must detect "ref points at an incomplete snapshot while a complete one exists" and offer *Repair*; prune must never delete a complete unreferenced revision loom's roster needs |
| three `FLUX.2-klein-9B` variants at 52.9 GB each (plain, base, kv) | "used by" per variant, so the author can see which of the three anything still calls |
| repos loom never uses (`stable-video-diffusion-img2vid-xt`, `Qwen2-Audio`, `clap`, `nsfw_image_detection`, `facefusion`, …) | the cache is shared with other tools in the monorepo: label them *not used by loom*, never delete them by default |
| two empty repos (`unsloth/…-GGUF`, 0 files) from the CPU spike's cache probes | health: *empty* |
| upstream `Comfy-Org/flux2-dev` moved again on 2026-08-17 | a naive "fetch missing" re-downloads ~50 GB; the plan must prefer *repair* over *fetch* when the bytes exist |

## 3. Goals and non-goals

**Goals.** (1) Loom never refuses a model whose bytes are in the cache. (2) The author can see what is cached, how big it is, what loom needs it for, and what is wrong with it. (3) Fetch, repair, delete and verify are loom actions with progress, cancel and a second click, never CLI folklore. (4) The cache location is a loom setting with validation, and moving the cache is a resumable, verified job. (5) Everything runs without a GPU and without the network except the fetch and verify actions themselves.

**Non-goals.** Managing other tools' repos beyond listing them. Mirroring the hub's own cache format (loom keeps the standard layout so `hf` and other tools keep working). Per-project caches (R160: one shared cache). Auto-downloads at startup (R163: explicit, on demand).

## 4. Design

### 4.1 One roster — `orchestrator/weights.py`

A single registry built at import from the four sources, no new manifest:

```
Need(id, repo_id, kind: "diffusers"|"files"|"file_target", files: [..] | None,
     used_by: ["catalog:flux2/flux.2-dev", "multi:fast", "postproc:birefnet", "phase:P0"],
     gated: bool, phase: str | None, size_hint_gb: float | None)
```

`files` is the exact list a consumer opens (the catalog's `probe_files`; a diffusers repo lists `model_index.json` plus the component folders it loads; a postproc tool its declared files). The roster is the only thing the inventory, the probe, the fetch and the UI consult. Existing entry points (`variant_weights_present`, `multi_weights_status`, `postproc_weights_status`, `weights_ok`) become thin wrappers over it, so nothing else changes.

### 4.2 The resolver — the "fix it for good"

`weights.resolve(repo_id, filename) -> Resolved | None`, in this order: (1) the revision `refs/main` names, (2) any cached revision of the repo that holds the file, newest first, (3) nothing. Never the network. The result carries the revision it came from and whether that revision is the ref'd one.

- The presence probe uses it, so today's refusal disappears: the transformer resolves from the June snapshot and the verdict says *present, via an unreferenced revision (ref drift)*.
- **Workers get the revision, not a hope.** The orchestrator resolves every file the job needs at admission and passes the chosen revision to the worker (`LOOM_HF_REVISIONS` in the worker's environment: a JSON map `repo_id → commit`), and the worker's `resolve_hf_file` passes `revision=` to `hf_hub_download`. The orchestrator's verdict and the worker's are then the same bytes. `HF_HUB_OFFLINE=1` is set for workers so a stale ref can never start a surprise download mid-job.
- The flux2 worker is the first consumer; sd35, zimage and krea2 diffusers loaders take a `revision` the same way (their `from_pretrained` accepts it).

### 4.3 Inventory and health — `GET /cache`

```
{ location: { path, exists, free_gb, total_gb, source: "env" | "dotenv" | "setting" | "hf_home" | "default", managed, previous },
  token: { set, source: "env" | "dotenv" | "setting" | null, masked: "hf_…ab12", managed },     // step e: never the token itself
  scanned_at, size_gb, repos: [ {
    repo_id, size_gb, used_by: [...], needed: bool, gated, whole,
    uses: [ { tag, label: "FLUX.2 · flux.2-klein-4b", role: "vae", where: ["Cast", "Expand"] } ],   // step e
    revisions: [ { commit, ref: "main" | null, files, size_gb, needed_present, needed_total, weights, complete_for_loom: bool, last_modified } ],
    health: "ok" | "ref_drift" | "partial" | "missing" | "empty" | "unused" | "stale_extra",
    detail: "main points at 1 of 9 files; a complete revision 03d6521 exists (repair)" } ],
  models: [ { tag, label, kind: "model" | "preset" | "tool" | "train" | "manifest", where: [...], health,     // step e: the roster by model
              repos: [ { repo_id, role, health, size_gb, gated } ] } ],
  needs_missing: [ Need... ] }
```

A **whole** need (step e) is a repo its consumer opens with `from_pretrained` — the diffusers repos, the Qwen text encoders, BiRefNet, the Tile ControlNet, the trainer bases: its fetch is a snapshot, and a revision counts as complete only when it holds weight files, not just the probe. Split-file repos (the Comfy flux2-dev files, the klein single files, the onnx tools, the Turbo LoRA) stay per-file.

Health rules, in order: *missing* (roster needs it, no revision has the files) · *ref_drift* (the ref'd revision lacks a needed file that another revision has) · *partial* (no revision has every needed file; some do have some) · *stale_extra* (complete and healthy, plus extra revisions or blobs nothing references) · *empty* (a repo folder with no files) · *unused* (not in the roster) · *ok*. The scan is cheap (0.3 s for 745 GB) so `GET /cache` scans on every call; the UI polls it only while the Models page is open.

### 4.4 Actions (all token-gated, all logged)

| Action | Endpoint | Behaviour | Guards |
| --- | --- | --- | --- |
| Repair ref | `POST /cache/{repo}/repair` | rewrite `refs/main` to the newest revision that is complete for loom | refuses while a job that uses the repo runs; records the previous value in the response and the log |
| Fetch | `POST /cache/fetch` `{need_id \| repo_id, files?}` → an **io job** in the queue (`pipeline: "hf_fetch"`) | `hf_hub_download` per file with resume, the job's note = file n of m and bytes; cancel = stop after the current file; the roster tells it which files, so a diffusers repo never pulls files loom does not load | the disk guard's free-space check before admission (size from the hub API when online, else the roster's hint); gated repos need `HF_TOKEN` (412 with the hint as today) |
| Delete | `DELETE /cache/{repo}` or `DELETE /cache/{repo}/{revision}` | the hub's `delete_revisions` strategy (blobs go only when no revision references them) | refuses while any job runs; a second click in the UI; *needed* repos warn ("the roster needs this; the next generation will 412") |
| Prune | `POST /cache/prune` `{dry_run}` | removes *stale_extra* revisions and orphaned blobs; **never** an unreferenced revision that is complete for loom (that is the flux2 June snapshot) | dry run first, then the list is shown and confirmed |
| Verify | `POST /cache/{repo}/verify` → an io job | sha256 of each needed blob against the hub API's LFS metadata (online) or the size at least (offline) | optional; slow on 100 GB; reports per file |
| Pin | `PUT /cache/{repo}/pin` `{commit}` | loom uses that revision regardless of the ref (stored in the settings block) | cleared when the revision is deleted |

Fetch as a queue job is deliberate: the dock already shows progress, pause, cancel and history; a download that takes a day belongs there, not in an HTTP request.

### 4.5 Location — a loom setting, and a move

- **Where the setting lives.** `.loom_state/app.json` gains `settings.models_dir`. Precedence stays *real env > .env.local > .env > setting > default*, and the UI states which source is in force ("set by `.env`; edit it there or remove the line to manage it here"). Rationale: the cache location is per machine, not per checkout, and `.loom_state` is the machine-local store already.
- **Applying it.** The orchestrator passes `cache_dir` explicitly to every hub call it makes (scan, probe, fetch) and sets `HF_HOME` in each worker's environment at spawn. Changing the setting takes effect for the next scan and the next job with **no restart**; `/version` reports the effective path.
- **Validation.** Absolute path; exists or creatable; free space shown; a path on the system drive gets a note, not a refusal.
- **Move** — `POST /cache/move` `{to}` → an io job: (1) copy the `hub/` tree preserving the **relative symlinks** the hub layout depends on (`shutil.copytree(symlinks=True)`; the fact that the current cache holds symlinks proves the privilege exists on this box), skipping files that already exist with the same size so a cancelled move resumes; (2) verify file count and total size; (3) switch the setting; (4) leave the old tree in place and show it as *previous location, n GB* with an explicit Delete. Refuses while any job runs. A move of 745 GB is hours; the dock shows it.

### 4.6 UI in v2

- **Models page** (File ▸ Models…, and the disk item in the status cluster): a canvas view like Start. Top: the location row (path · free of total · source · Change… · Move…). Then the roster table: name · used by · status pill · size · actions (Fetch / Repair / Verify / Delete). Then *other repos in this cache* (unused, with sizes, Delete each). Transfers show as dock rows.
- **Banner**: when the roster has *missing* or *ref_drift* entries the banner slot says so with **Repair** or **Fetch** inline (v2 has no weights banner today; v1 had the launch-gate one).
- **The 412 in the composer** gains a **Fetch** button that queues the fetch job and a **Repair** button when the detail says drift; the toast then says where to watch it.
- **Model catalog cross-link**: the composer's variant picker shows a small status dot per variant from the same data, so a missing model is visible before Generate.

### 4.7 Safety rules

Never delete while a job runs. Never delete a revision that is complete for loom, even unreferenced, except by an explicit per-revision delete. Never touch repos outside the roster except by an explicit delete. Every mutation logs the repo, revision and bytes. Every destructive button is a second click. Fetch never starts without the free-space check. The cache layout stays the standard hub layout.

## 5. Milestones (proposed M2.17)

| Step | Deliverable | Verification (no GPU) | Size |
| --- | --- | --- | --- |
| **a. Resolver + repair — ✅ DONE 2026-09-21** | `weights.py` roster + `resolve()`; the probe (and through it the four status functions) over it; `LOOM_HF_REVISIONS` + `HF_HUB_OFFLINE` in the worker env (`LOOM_WORKERS_ONLINE=1` restores online); flux2 `resolve_hf_file(revision=)` via the torch-free `hf_pins.py`; `GET /cache` (inventory + health + location); `POST /cache/{repo}/repair` (409 while a job runs). The diffusers loaders (sd35 · zimage · krea2) take their pin in step b | a fake cache in tmp (blobs · snapshots · refs) reproducing today's drift: the probe says present, health says `ref_drift`, repair rewrites the ref, a job's env carries the revision; the real refusal is gone (TestClient) | 1 day; **closes the 2026-09-21 bug** |
| **b. Fetch as a job, delete, prune, verify — ✅ DONE 2026-09-21** | the `hf_cache` io adapter + torch-free worker (`pipelines/hf_cache/run_pipeline.py`: fetch per file with the library's resume, verify = sha256 / git-sha1 of each blob against the name the hub gave it, move = symlink-preserving resumable copy); `POST /cache/fetch` (the roster's missing files, or named files; gated → 412 without HF_TOKEN; nothing missing → no job), `POST /cache/{repo}/verify`, `PUT /cache/{repo}/pin` (app settings), `DELETE /cache/{repo}/revisions/{commit}`, `DELETE /cache/{repo}`, `POST /cache/prune` (dry run by default; never a complete-for-loom revision, never another tool's repo, orphan blobs only where snapshots link); every mutation 409 while a job runs; the pointer keeps the settings block; the sd35 · zimage · krea2 loaders read the pin (`hf_pins.py` beside each); cache folders matched ignoring case; non-diffusers catalog repos probe `config.json` | fake hub in tmp; the worker by file path with `hf_hub_download` mocked; guard tests (running → 409; the June snapshot never pruned) | 1.5 days |
| **c. Location + move — ✅ DONE 2026-09-21** | `weights.location_source()` / `effective_home()` with the precedence **env > .env > setting > HF_HOME > default** (read on every call: a setting change applies to the next scan and the next job, no restart; `worker_env` carries `HF_HOME`; startup and `/version` read it, `/version` also says the source); `PUT /cache/location` (absolute path, creates the folder, refused while an env source is in force with the reason, records the old home as *previous*); `POST /cache/move` (validated: not the current home or inside it, free space ≥ the tree; the `hf_cache` move job; the completion observer switches the setting and keeps the old tree as *previous*); `DELETE /cache/previous`; the inventory's `location` carries `managed` + `previous` with its size | precedence walked through all five sources; the change refused under `.env`; the move plan's checks; the observer switching only on a done move; the endpoints incl. 409 while a job runs; `/version` reporting the switch | 1 day |
| **e. The roster by model, the token, the fetch fixed — ✅ DONE 2026-09-21** (after the author's first click) | every use of a repo carries a `label` (the model it belongs to), a `role` (model · text encoder · vae · ControlNet · LoRA · tool weights · base model) and `where` in loom it is used (Cast · Expand · Poses · Post: … · LoRA preview · Train · Expand: matte / identity lock · Launch gate); the inventory lists the roster **by model** (`models`: one entry per catalog variant, casting preset, tool, trainer preset and manifest phase with its repos and the worst of their health); the klein text encoders enter the roster from the catalog through the platform rule (non-FP8 on Windows ROCm, FP8 elsewhere) and the manifest's 8B entry mirrors the 4B one; a **whole** need fetches a snapshot and is complete only with weight files; the **token** as a loom setting (`settings.hf_token`; env > `.env.local` > setting, D8; `PUT /cache/token`, `POST /cache/token/check` = whoami; workers get `HF_TOKEN`); the fetch worker lifts `HF_HUB_OFFLINE` **before** importing the hub library and the runner leaves the flag out for `hf_cache` jobs; v2: the token row (masked · source · Set / Change… / Clear / Check, a password field with *Check first*), **Models loom uses** by model with Fetch / Repair per repo, readable *used by* on the repo rows, `VariantWeights` naming the bad repo with its role; **the fetch meter** (author, same day): the worker hands the hub library a `tqdm_class` that reports into one thread-safe tally — bytes done of bytes planned (sized up front by `model_info(files_metadata=True)` minus what is cached, else the bars' own totals), file k of m, a 10-second rate, an ETA, the current file — as `[cache] progress` / `[cache] note` lines once a second; verify reports by bytes hashed; the row on the Models page, the composer's refusal line and the dock show the bar with a second-click Cancel (a cancelled fetch resumes) | `test_cache_manager_d.py` (13 tests): the roster's labels / roles / whole flags on both platforms, the manifest entry, `where_used` against the composers and the Post tab, a probe-only snapshot reads *partial* and fetches a snapshot, the by-model view with the worst health and the FP8-twin note, the token's precedence / masking / refusals / endpoints, the cache worker online, the worker lifting the flag before the library reads it (a fresh import), the snapshot fetch with its ignore patterns; `test_v2_models.py` extended | 0.5 day |
| **d. Models page + banners — ✅ DONE 2026-09-21** | `frontends/v2/src/models/Models.tsx` (File ▸ Models…, the *models* item in the status cluster, Esc closes): the location row (path · free of total · source pill · Change… · Move…, both disabled with the reason while `.env` is in force) · the previous location with Delete · **Models loom uses** (health pill · size · detail · used by; Repair on drift, Fetch on missing/partial, Verify, Delete on a second click, the revisions folded with Pin / Delete revision) · **Other repos in this cache** · **Prune** (list first, then a second click); `cacheUi.tsx`: `VariantWeights` under the model picker (Cast + Expand) and `Problem`, the refusal line that names the repo and offers Repair / Fetch / Models; the banner slot says *ref drift* with Repair and *partly cached* with Models; the inventory rides the one poller (every tick while the page is open, every 30 s otherwise) | `test_v2_models.py`: every client call names a route the server registers; the page complete and second-click safe; reachability + Escape + the poller cadence; the banner and the composers acting on the cache | 1 day |

**Acceptance.** The author opens Models, sees the real cache (745 GB, 29 repos) with flux2-dev flagged *ref drift*, presses Repair, and the next flux.2-dev generation is admitted. A deliberate `hf download` of one file no longer breaks a model. A move to another folder completes and survives a cancel.

## 6. Decisions for the author

| # | Question | Recommendation |
| --- | --- | --- |
| D1 | Where the location setting lives: `.loom_state/app.json` (machine-local) vs `.env.local` | `app.json`; `.env` stays an override and the UI says when it is in force |
| D2 | Auto-repair drift silently at probe time vs an explicit Repair | explicit, with the banner; the resolver already makes the *reading* side tolerant, so nothing is blocked while the author decides |
| D3 | Fetch through the job queue (dock progress, pause, cancel) vs a direct request | the queue |
| D4 | Move = copy · verify · switch · keep the old tree until deleted, vs an in-place move | copy-and-keep; a 745 GB cache is not something to move atomically |
| D5 | Verify depth: sizes offline, sha256 online, or always sha256 | sizes by default, sha256 on demand |
| D6 | Show other tools' repos at all | yes, labelled *not used by loom*, deletable only one by one |
| D8 | Where the Hugging Face token lives and who wins | `settings.hf_token` in `app.json` behind the same rule as the location (a real env var, then `.env.local`, then the setting): one rule for both, and the page says *set by .env.local* with the reason until that line is removed. The alternative — the setting overriding `.env.local` — would have saved the author one edit at the price of a second precedence rule. **Accepted by building (2026-09-21)** |
| D9 | What a fetch of a whole-repo model pulls | the snapshot (diffusers repos skip the flax / tf / ckpt / onnx duplicates), with the library's resume; the roster's per-file fetch stays for split-file repos. Completeness offline = the probe **and** at least one weight file; a cancelled snapshot therefore reads *partial* and fetches again. **Accepted by building (2026-09-21)** |
| D7 | Schedule: before or after the v2 click-through and the M2.16 sdcpp adapter | **a first** (one day, closes a live bug and the trap in §2); b–d after the click-through, before M2.16, because the sdcpp adapter's GGUF files will live in the same cache and want the same roster |

## 7. What to do today, before any of this

The 40-byte fix stands: point `refs/main` of `Comfy-Org/flux2-dev` back at `03d6521e6f6a47396b3f951cbea50f7e6c2f482e`. Step **a** then makes the ref irrelevant to loom. Do not run a hub prune before step **a** exists.
