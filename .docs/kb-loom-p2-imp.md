# Loreweave Studio — P2 implementation journal (`kb-loom-p2-imp`)

started: 2026-06-14 21:33:40
finished:

Running log of what was **actually built** for Phase 2 (**LoRA training** — Stages D & E: make a P1
character reproducible by training a LoRA from its curated, captioned ref set), milestone by
milestone. Brief points + any parameters/settings worth remembering later.
Spec: [`kb-loom-p2.md`](kb-loom-p2.md); decisions: [`kb-storyboard01.md`](kb-storyboard01.md) §10.0
(R6/R13/R21/R58/R68/R96/R98/R114/R115–R118/R122/R147/R159/R169/R170);
predecessor spine: [`kb-loom-p1.md`](kb-loom-p1.md) / [`kb-loom-p1-imp.md`](kb-loom-p1-imp.md)
(P1 functionally complete & closed 2026-06-14, HEAD `44cd411`).

Convention: each milestone records **start + finish** timestamps (get the real clock, don't guess);
⚙ marks a setting/param later code depends on; ⚠ marks a known gap / deferred item.
**Push at milestone close** (carried from P0/P1): every milestone/acceptance close ends with a
commit+push of the loom repo, recorded here with its hash. **R162 vendoring** still holds — any new
pipeline/trainer worker code lands in the monorepo `src/pipeline/` (or the trainer's equivalent)
**first**, then is copied byte-identically into `loom/loom-loreweave-studio/pipelines/…` (MD5
drift-guard).

P2 build order (spec §12 — **walking skeleton first**):

**Phase A — Training skeleton (prove a LoRA can be made + used on this rig):**
- **M1 — training spike (no UI).** Includes **P2-0**, the make-or-break front-gate: prove
  **ai-toolkit even trains on RX 9070 XT / ROCm** (hard go/no-go — if no-go the whole training
  approach changes). Then vendor ai-toolkit, train ONE `zimage` LoRA from a fixed P1 `ref_set`,
  load it at inference, confirm it reproduces the character. Find the per-model default preset here.
- **M2 — trainer skeleton as a (staged) queued job.** Wrap the trainer in the P0 job-queue +
  manifest envelope with **staged-job** semantics (R118: auto-generate the spec, do NOT auto-queue;
  separate `jobs/staged.json`; explicit "Add to queue"). [Train LoRA] from the Asset Studio → temp
  run → promote → verify. Also **P2-10** (resume-from-checkpoint, R88/R159 — first `resumable=true`
  job), **P2-12** (training-time ETA). *Skeleton only — NOT the done-line (R169).*

**Phase B — Thicken (all VLM-free):**
- **M3 — template captioning.** `captions.jsonl` deterministically from the frozen P1 coverage-cell
  metadata + trigger token; `caption_policy.json`; review/edit UI. (No VLM.)
- **M4 — proxy readiness meter.** Coverage (from metadata) + perceptual-hash dupes + face-embedding
  on-model (anchor distance, or no-anchor centroid fallback R120) → `readiness.json` → advisory
  meter. (No VLM.) ***P2 done-line reachable here*** (M2 trainer + M3 captions + M4 readiness).
- **M5 — train options + `sd35` + PEFT backend.** train-from-base / seed-from-parent (R68);
  per-model presets + advanced knobs; **diffusers-PEFT** advanced backend; onboard the **sd35**
  trainer; full training manifests. Also **P2-9** (VRAM-fit presets per base).
- **M6 — promote + manual cleanup + LoRA management.** Promote into the version; one-click temp
  cleanup (R13); version selector shows LoRA presence; **P2-11** (LoRA preview before promote);
  **P2-13** (graph-ready facts: `training_context.json` + `caption_policy_hash` + `context_digest`
  in the manifest). (No style-LoRA path — declared only, R122; built in P5 with multi-LoRA stacking.)

**Done-line:**
- **M7 — acceptance.** A P1 character → template-captioned → readiness ✓ → **staged → added to
  queue → trained → promoted → test-gen reproduces it on-model**, all recorded in the training
  manifest (§1), with `caption_policy_hash` + `context_digest` present.

⚠ **Phase risk (spec §11):** P2 is the **first phase that builds genuinely new capability** (a
trainer), not a wrapper around an existing inference CLI. **P2-0/P2-1 are the whole phase's
make-or-break** — does ai-toolkit train on ROCm at all? Everything after the gate + spike is
conventional. Resist pulling the **VLM** (→ P4, R116) or **GraphRAG** (→ post-v1, R170) into P2.

---

*(P2 kicked off 2026-06-14; the phase-boundary docs commit — P1 journal closed, this journal opened,
README status updated — pushed `ff29c0c`. **M0 (UI reset) added by the author 2026-06-18 and built
first**, before the trainer gate; see below.)*

---

## M0 — shell/workspace UI + postprocess workflow reset (spec §12 M0; WBS P2-M0a/b/c/d)

started: 2026-06-18
finished: 2026-06-18 18:35 for a+b+c (✅ built — visual sign-off owed); **M0d ✅ COMPLETE 2026-06-20:
Parts A (structured prompting) + B (sampling presets) + C-t2i (dev JSON tree) + C-i2i (flux2-img2img
on the M0c postproc step) all built same day** (see "### M0d" below). Visual sign-off owed.

**Author (2026-06-18):** before trainer work, a **UI/workflow reset** over the P0/P1 MVP so later
P2/P3 controls inhabit a better surface (spec §12 M0 — a product-shape correction, not a trainer
feature). Three WPs: **M0a** shell + workspace-nav reset · **M0b** L1 tabbed authoring · **M0c** L2
postprocess stack. Built WP-by-WP (build → push) so each is reviewable. ⭐ Two layout forks decided
by the author up front: **File menu = in-app `File ▾` dropdown** (not a native OS menubar — stays in
React, matches the existing picker); **workspace tabs = left-rail tabs evolved** (not a top strip).

### M0a — shell + workspace navigation reset — finished 2026-06-18 17:28

**Frontend-only (App.tsx + styles.css).** Done:
- **File menu (in-app `File ▾`):** the titlebar's inline `+ New` / `Open ▾` / `Close` strip collapses
  into one **`File ▾`** dropdown — `New project…`, `Open folder…`, `Close project` (when open), a
  separator, then a **RECENT** list (reuses the existing `.picker-row` open/forget rows). The top bar
  now carries only the current project name + the orchestrator status dot. Reuses the existing
  handlers (`onNewProject`/`onBrowseProject`/`openByPath`/`onForgetProject`/`onCloseProject`) and the
  `showPicker`/`onTogglePicker` list-load; each action closes the menu. New CSS `.filemenu*`; dead
  `.picker-wrap`/`.picker`/`.picker-browse` rules removed (row sub-classes still used by RECENT).
- **Workspace-scoped rail:** the `L2·Assets` / `L1·World` tabs stay at the top of the left rail, but
  the panel below now **swaps per workspace** — L2 shows the ASSETS library (`+ Character` / `⤒
  Import` / Sandbox + characters), L1 shows a `WORLD` rail. The always-visible ASSETS strip no longer
  bleeds into the L1 workspace (the "cramped shared controls" the spec calls out). The L1 sub-tabs
  (Visual Styles / World / Story Spine) land in **M0b**; for now L1's rail is a minimal header.
- **Asset panel +20% wider:** `.panes` rail column `200px → 240px`.

`tsc` + `vite build` clean. No backend touched (244 tests stand). ⚠ Visual sign-off owed (user, on
the running app). **✅ PUSHED `379652f`.** ⏭ Next: **M0b** L1 sub-tabs + readable multi-line editors.

### M0b — L1 tabbed authoring + readable editors — finished 2026-06-18 17:38

**Frontend-only (App.tsx + styles.css).** The L1 World workspace was one long cramped scroll of
three sections with two-line inputs. Now:
- **L1 sub-tabs in the rail:** the M0a-stubbed `WORLD` rail gains **🎨 Visual styles · 🌍 World ·
  🧬 Story spine** nav buttons (reuse `.asset-row`). New App state `l1Tab` is passed to
  `WorldWorkspace` as `tab`; the workspace renders **only the selected section** (the in-pane "L1
  WORLD —…" header is gone — the rail is the nav now). The component stays mounted across sub-tab
  switches, so unsaved drafts (world/premise/style edits) persist when flipping tabs.
- **Readable multi-line editors** (spec: "not two-line inputs"): World prose `rows 6→18`, premise
  `rows 3→8` (+ a field label); StyleRow fragment `rows 2→4`, global-negative `rows 1→3`. The
  **Story-spine character** went from an inline single-line row to a **card** (`.spine-row` →
  bordered card; name + save/stub/re-sync/✕ on a `.spine-row-head` row; the snippet is now a
  multi-line `.spine-snippet` textarea). The "+ character" add form stacks name + a snippet textarea
  + button.

`tsc` + `vite build` clean. No backend touched (244 tests stand). ⚠ Visual sign-off owed (user).
**✅ PUSHED `3801cae`.**

**M0b refinements (user, 2026-06-18):** (a) on Visual Styles the **"+ add style" form moved to the
top** of the section (create-then-it-appends-below reads better); style **fragment `4→8 rows`**,
**global negative `3→2 rows`** (`d30de5e`). (b) spaced the add-style form off the list with a divider
(`028b82a`). (c) the L1 `.world` block had only 2px horizontal padding so content butted the
scrollbar → **right padding 16px** (clears the scrollbar; max-width keeps it off the inspector)
(`3fe640b`). All frontend-only, build clean.

### M0c — L2 postprocess stack (decoupled from generation) — finished 2026-06-18 18:35

⭐ Two author forks up front: **persist a stack record** (durable/replayable, not derive-from-jobs)
+ **inline panel on the selected image** (not a dedicated stage). Today postprocessing was coupled
to a generation run (clean/polish/restore as drawer toggles → chained jobs). M0c **decouples** it:
postprocess any existing image via a persisted, independently-queued stack. The clean/polish/restore
**workers are unchanged** — M0c is the reorganization the spec frames ("only the UI/data shape +
source/output lineage").

**Data model (`version.schema.json`):** new optional `postproc_stacks[]` per version — each
`{base, steps[]}`; a step = `{id pps_…, preset, backend, mode, params, mask, requires_mask,
source, output, job_id, status, added_at}`. A stack is a **linear chain**: a step's `source` is the
previous step's `output` (or the base); steps append/remove at the tail. `mask` + `requires_mask`
are the **mask-ready contract** (stored + carried; no mask-consuming worker in M0).

**Backend (`assets.py` + `main.py`):** `assets.add_postproc_step` (configured, source = prior
output or base; refuses to stack before the tail has an output), `remove_postproc_step` (tail only),
`mark_postproc_step_queued`, `record_postproc_result` (observer side, by job_id), `resolve_postproc_step`.
Endpoints: **`POST /assets/{id}/postproc/step`** (configure — presets clean/refine/custom = img2img
strength presets backend zimage|sd35, restore = GFPGAN; param whitelist + model/backend validation,
422), **`POST …/step/{step_id}/queue`** (fire ONE batch job over the source image — img2img or the
restore io-worker; weight pre-flight 412 + VRAM 422; `dry_run` previews; mirrors `_submit_chained`'s
job shape; reads the source's true dims via PIL so aspect/tiles are right), **`DELETE …/step/{step_id}`**.
All finalized→409 (R60). ⚙ The single runner **completion observer** now fans out (`_on_job_complete`
→ anchor verification **+** `record_postproc_output`) so a finished step records its produced output
durably (matched by job_id) — a no-op for non-postproc jobs.

**Frontend (`orchestrator.ts` + `App.tsx` + css):** `PostprocStep`/`PostprocStack` types +
`postproc_stacks?` on `ProfileVersion`; `addPostprocStep`/`queuePostprocStep`/`removePostprocStep`.
A new **`PostprocPanel`** in the Inspector shows when a done (non-video) image is selected on an
unlocked asset version: lists the stack's steps with **live status** (reads the linked job so it
updates on the existing poll), a per-step **▶ queue** (configured) + tail **✕ remove**, and an **add
form** (preset select + optional strength) gated until the tail step is done. A small reconciliation
effect re-fetches the version when a queued step's job finishes (lands the persisted output + re-opens
the add gate; self-terminating).

**Tests:** `test_postproc_stack.py` (**+8**): add persists configured w/ base source; can't stack
before the tail has output; param/backend/model validation (422); queue dry-run → img2img job over
the source + real queue → queued+job_id + completion-observer records output + chains the next step's
source; restore → io job (input/blend); remove tail only; mask stored; finalized → 409. **252 backend
tests** (244→+8), green. `tsc` + `vite build` clean. **No `src/pipeline/` touched → no re-vendor.**
⚠ Visual sign-off owed (user, on the running app). **✅ PUSHED `94174f4`.**

**M0c refinements (user, 2026-06-18, `3c329e8`):** PostprocPanel renders **below the image** (after
the Inspector preview, was above); each step lists **all set attributes** (backend, strength/blend,
model, prompt, negative — not just strength) and the add-form exposes backend + strength + prompt +
negative (i2i) / blend (restore); **dropped the `custom` preset** (a bare i2i step, redundant once
Clean/Refine expose their attributes) from the UI + backend preset map + request Literal. **#3 RESOLVED — re-scoped postproc to PROJECT-level (`ce8d61d`):** "available on every image
regardless of origin." Traced: NOT flux2-specific (no pipeline gate; `output_name` set for flux2 like
any pipeline) — the panel was hidden only in the **unscoped Sandbox** (no character → nowhere to
persist the version-scoped stack). **User chose project-level** (origin-agnostic). Moved the store
from the character version → **`<project>/postproc_stacks.json`** keyed by base image (new
`orchestrator/postproc.py` + `postproc_store.schema.json`); endpoints are now project-scoped
(`GET /postproc/stacks`, `POST /postproc/step`, `…/step/{id}/queue`, `DELETE …/step/{id}` — no asset
id). The queue job **inherits its source's producing-job requester/version** so the output lands in
the SAME grid as the source (character or Sandbox); the completion observer records by job_id only.
Removed the version-scoped postproc (assets.py fns + `version.schema` field) — ⚠ consequence: stacks
**no longer travel with a character** (not in profile export, not frozen by finalize); postproc is a
project-wide image scratchpad (keep an output into a character via Stage-C curation). Frontend loads
project-level stacks (`getPostprocStacks`), the panel shows for any done non-video image with a
project open (no asset needed), and a done step gets a 🔍 to view its result in the lightbox (so
Sandbox results are visible even though they don't auto-land in the batch grid). Tests rewritten for
project scope (+ a no-asset case); **252 backend tests**; build clean. No `src/pipeline/` touched.

**M0c refinement (user, `f3d6ca4`):** the add-form now also exposes a **model-variant picker**
(not just the backend) — a dropdown of the chosen i2i backend's catalog variants (default = the
backend's preset model); switching backend resets it. Backend already accepted + validated
`model_name`; this just surfaces it. Frontend-only, build clean.

**M0c refinement (user, `f9466f4`):** queuing a step now shows a **queued tile** in the grid the
author is looking at (it streams queued→running→done like a generation). The grid is
requester/stage-scoped (character) or batch-id-driven (Sandbox), so a postproc job often didn't
surface. The queue endpoint gained optional `requester_id` + `stage`; the UI passes the active
character's version + current bootstrap stage (tile lands in that grid), and for the Sandbox tracks
the returned job id in `batchIds`. Backend still falls back to the source's producing job, else the
project. +1 test, 253 backend tests, build clean.

**M0c bugfix (user "postproc clean using sd35 fails", `55b414a`):** a Clean/Refine step submitted
an **empty per-item prompt**, but the batch worker rejects an empty-prompt item (sd35 `run_jobs`
`return 2` → the whole job fails, no output). The chained polish succeeded only because it inherits
the parent's prompt. Fix (orchestrator-side, worker unchanged → no re-vendor): a clean/refine step
now **re-diffuses with the SOURCE image's own prompt** when the author types none — from the source's
producing job, or (chained step) the previous step's per-output meta prompt; if none is inheritable
and none typed → **422** with a clear "type a prompt" message rather than a doomed job. +2 tests
(inherit; orphan needs/accepts explicit prompt); **255 backend tests**.

**M0c bugfix (user "step stuck after cancel/delete from the queue", `eabdf01`):** the completion
observer fires for **SUCCESSFUL jobs only** (`job_snapshot = … if rec.ok else None`), so a step whose
job was canceled/failed/deleted stayed stuck `queued` — the stack card couldn't add/remove (on delete
the live job is gone, so the UI fell back to the stale persisted status). Fix: **`GET /postproc/stacks`
now reconciles** each queued/running step against the live job table (`postproc.reconcile` + a runner
resolver in main.py): job terminal → that status (done also records output); **job gone → canceled**;
corrections persisted (+`canceled` in the store-schema enum). The **queue endpoint blocks re-queue
only if the linked job is genuinely still active** (live queued/running), not on a stale persisted
`queued` — so a dead step re-fires. Frontend: the reconcile effect fires on any terminal/vanished job;
`liveStatus` treats a vanished job as canceled; a dead tail shows **↻ re-queue + ✕ remove** so the
stack is never stuck. +2 tests; **257 backend tests**; build clean.

**✅ M0a+b+c COMPLETE.** (M0d added after — below.) ⏭ After M0d: **M1** training spike — the
**P2-0 ROCm go/no-go front-gate** (does ai-toolkit train on RX 9070 XT / ROCm at all?).

### M0d — flux.2 advanced prompting + sampling presets + dev JSON tree (spec §12 "M0d"; WBS P2-M0d)

started: 2026-06-20 15:28

Author request (2026-06-20): flux2 `ref`-mode Stage-B holds identity but **follows pose loosely**
(e.g. "three-quarter left" → body one way, head the other). Design (spec §12 "M0d solution design",
committed `5089850`/`1e89375`) has three additive levers: **A** structured/labeled prompting with
explicit angle→camera/pose directives (the pose fix); **B** configurable guidance/steps fronted by a
Sampling preset pull-down; **C** a `flux.2-dev`-gated structured-JSON prompt tree for t2i/i2i. Built
part-by-part (build → push). **No `src/pipeline/` worker code changes → no re-vendor** (the catalog
already exposes the flux2 variants + `guidance`/`num_steps`; the worker already honours distilled-vs-
base sampling — M0d is orchestrator + frontend surface only).

**Part B — Sampling preset pull-down (finished 2026-06-20 15:28).** ⚙ Backend: `FLUX2_SAMPLING_PRESETS`
in `model_catalog.py` (4 rows — **Fast** klein-4b 4/1.0 ★default, **Balanced** klein-base-4b 24/4.0
⭐recommended = the one-click pose fix, **Quality** klein-base-9b 40/4.5, **Dev/JSON** flux.2-dev
50/4.5 at original M0d time; M2.5 supersedes dev to quantized-safe 8/4.0) attached to
`CATALOG["flux2"]["sampling_presets"]` so `GET /models` serves it; `flux2_sampling_presets()`
helper. Each preset's `model_name` is a real variant (asserted). Frontend: `Flux2SamplingPreset` type +
`sampling_presets?` on `PipelineModels`; a reusable **`Flux2SamplingSelect`** dropdown on the **Stage-B
bar** (flux2 family) and the **t2i cast bar** (castPipeline=flux2). Picking a preset sets the model +
merges `num_steps`+`guidance` into the params drawer (Stage-B: `stageBModel`+`advParamsB`; cast:
`advParamsA.model_name`+steps+guidance, both top-level/channel-routed as before). **Custom** = the
hand-set fields; hand-editing model/steps/guidance (or reset / pipeline change) falls the label back to
Custom. **Distilled guard:** a ⚠ hint shows when guidance > ~1.5 on a step-distilled variant (CFG inert
there). Note: `guidance`/`num_steps` already rendered as individual fields in the flux2 ⚙ params drawer
(catalog params) — Part B adds the one-click combos + the guard on top. **Tests:** `test_model_catalog.py`
+1 (`test_flux2_sampling_presets_reference_real_variants` — real variants, exactly one default,
recommended is non-distilled, served on the entry). **259 backend tests** (257→+2 incl. earlier), green;
`tsc --noEmit` clean. **✅ PUSHED `d327719`.**

**Part A — structured (directive-led) prompting (finished 2026-06-20 16:57).** The pose fix.
New **`orchestrator/flux2_prompt.py`**: an `ANGLE_DIRECTIVES` table mapping each frozen coverage
angle → an explicit camera+pose directive that names **head AND body** (e.g. `three_quarter_left`
→ "body and head both turned three-quarters toward the viewer's left (¾ left view)", replacing the
loose "three-quarter left view" the reference overrode), plus `SHOT_DIRECTIVES` for framing.
`build_cell_prompt(cell, clause, style)` assembles `<pose directive>, <framing>, <expression>[, <bg>
background], <clause>, <style>` — same slot order as the flat builder (pose leads → dominates the
loosely-adhering model; identity rides the reference + clause; style trails), positive-only (FLUX.2
takes no negatives). **Coverage vocab stays frozen** — the module only reads + rephrases it (a test
asserts the directive tables cover `coverage.ANGLES`/`SHOT_SIZES` exactly). `recipe.build_recipe`
gained `advanced_prompt=False`: when on, cells use `flux2_prompt.build_cell_prompt` instead of the
flat phrase (everything else — matrix, cells, seeds — identical); returns the flag. `main.py` Stage-B:
`StageBRequest.advanced_prompt` (extra="forbid" model), passed as `advanced_prompt and is_flux2`
(**gated to flux2** so zimage/sd35 keep flat phrasing), echoed in the dry-run payload. Frontend: an
**"advanced prompting" checkbox** on the Stage-B bar's flux2 branch (`advancedPromptB` → `advanced_prompt`
in `buildStageBBody`, flux2-only); the existing dry-run **Preview** shows the resolved directive
prompt (first_cell). **Tests:** `test_flux2_prompt.py` (**+9** — directive coverage, head+body pin,
fallbacks, prompt order, bg inclusion, vocab validation, recipe on/off determinism). **268 backend
tests**, green; `tsc` clean. No `src/pipeline/` → no re-vendor. **✅ PUSHED `d88eb01`.**

**Part C — `flux.2-dev` structured-JSON prompt tree, t2i (finished 2026-06-20 17:06).** When
**flux.2-dev** is the selected cast model, a **🌲 JSON prompt** tree authors the FLUX.2 schema
directly. ⚙ Backend: `model_catalog` now serves `CATALOG["flux2"]["angle_directives"]` (= Part A's
`flux2_prompt.ANGLE_DIRECTIVES`, single source so the tree's pose presets don't drift; +1 test).
Frontend lib (`orchestrator.ts`): `Flux2PromptTree`/`Subject`/`Camera` types + `emptyFlux2PromptTree`,
`serializeFlux2PromptTree` (drops every empty field/array → compact JSON string, "" when nothing
authored), `parseFlux2PromptTree` (lenient, throws on invalid JSON), `angle_directives?` on
`PipelineModels`. App.tsx: a **`Flux2JsonTreeEditor`** (scene · subjects[] add/remove · camera
{angle + a pose-preset dropdown reusing Part A directives, lens, dof} · lighting · style · mood ·
color_palette[] add/remove · **view/apply raw JSON** with an invalid-JSON flag, never silently sent).
Gated render: a **🌲 JSON prompt** toggle + panel show **only when `castPipeline==="flux2"` and
`advParamsA.model_name==="flux.2-dev"`** (`castDevSelected`); klein/base never see it. `buildGenerateReq`:
a non-empty tree serializes to the `prompt` (empty ⇒ the plain text prompt; the "enter a prompt"
guard relaxes when the tree is filled). Flows through the existing flux2 t2i path + the dry-run
Preview — **no adapter/contract change** (JSON rides the prompt string). **Tests:** +1 catalog
(directives served == flux2_prompt, frozen-vocab coverage). **269 backend tests**, green; `tsc` +
`vite build` clean. No `src/pipeline/` → no re-vendor. **✅ PUSHED `3aac9ac`.**

**Part C — i2i via flux2-img2img on the M0c postprocess step (finished 2026-06-20 17:22).**
*(author chose "build flux2-img2img now" over deferring.)* flux2 now joins zimage/sd35 as an **i2i
backend** on the M0c postprocess stack, so a `flux.2-dev` step edits/re-poses an existing image with
the **same JSON tree**. Key finding: the flux2 worker's **batch** `run_jobs` does t2i/ref only —
img2img is its **single-run** `run_img2img` path — so a flux2 i2i step is a **single-run job (no
`batch_items`)**, **no `src/pipeline/` change → no re-vendor**. Adapter: `WIRED_MODES` += `img2img`,
`WIRED_PARAMS` += `init_image`/`strength` (capabilities now advertises `[ref, t2i, img2img]`). Backend
(`main.py`): `add_postproc_step` accepts `backend="flux2"` for the i2i presets (clean/refine); the
queue endpoint branches on `backend=="flux2"` to build a single-run `{prompt, init_image, strength,
width, height, model_name}` job (mode img2img) instead of a batch item — the existing prompt-resolution
(typed > source-prompt) means the dev JSON string rides `prompt` verbatim; weight pre-flight + VRAM
(`estimate_vram("flux2")`=13 GB) + the completion observer (records the runner's out/-relative output)
all work generically. Frontend: PostprocPanel add-form gains a **flux2 ✨** i2i backend; when
`model==="flux.2-dev"` it renders the **`Flux2JsonTreeEditor`** in place of the plain prompt (serialized
JSON → the step's `prompt`), hides the negative field (flux2 takes none), and hints to pick dev on
klein/base; `angle_directives` passed through. **Tests:** `test_postproc_stack.py` (**+2** — flux2 i2i
is single-run with init_image + the JSON prompt + refine strength; unknown i2i backend 422 / flux2
accepted); updated 2 flux2-adapter caps tests for the new mode. **271 backend tests**, green; `tsc` +
`vite build` clean. **✅ PUSHED `16874e4`. M0d COMPLETE (A + B + C-t2i + C-i2i).**

**M0d commit trail:** Part B `d327719` · Part A `d88eb01` · Part C-t2i `3aac9ac` · Part C-i2i
`16874e4`. ⚠ Visual sign-off owed on the running app (all four levers — sampling pull-down,
advanced-prompting toggle, dev t2i JSON tree, dev i2i JSON tree on the postproc step).

**M0d fix — dev false-warning + JSON in Stage-B expansion (2026-06-20 17:45, user-found).** Two
bugs when selecting flux2-identity Stage-B with the **Dev / JSON** sampling preset: (1) the
distilled-guidance ⚠ guard fired on **flux.2-dev** (wrong — dev IS guidance-distilled, but its
guidance default 4.0 is a *real adjustable knob*; only the **klein** variants pin it, worker
`fixed_params={guidance,num_steps}`). The guard keyed on the catalog `distilled` flag (True for dev
AND klein). Fix: added an accurate **`guidance_fixed`** flag per flux2 variant (True only for
klein-4b/9b/9b-kv; False for -base + dev), served on the catalog; the FE guard now keys on it
(`flux2GuidanceFixed`, was `flux2IsDistilled`) → no false warn on dev/base. (2) **JSON prompting
didn't reach Stage-B** (Part C's tree was t2i/i2i only) — the author expected "Dev / JSON" to JSON-
prompt expansion. Fix: `flux2_prompt.build_cell_prompt(as_json=True)` + `build_cell_json` emit each
cell's directive set as a compact JSON object (subject/pose/shot/expression/background/style, empty
dropped, non-ASCII kept); `recipe.build_recipe(json_prompt=…)` threads it; the Stage-B endpoint sets
`json_prompt = advanced_prompt and is_flux2 and eff_model=="flux.2-dev"` (eff model = params-channel
override > top-level, matching the existing precedence) and echoes it in the dry-run. FE: picking the
**Dev / JSON** Stage-B preset now auto-ticks **advanced prompting** (so dev actually emits JSON in one
pick) + a "→ structured JSON (dev)" hint. klein/base keep the labeled directive string. **+5 tests**
(flux2_prompt JSON ×3, catalog guidance_fixed, endpoint dev-JSON dry-run); **276 backend**, green;
`tsc` + `vite build` clean. No `src/pipeline/` → no re-vendor. **✅ PUSHED `2f2d07f`.**

**M0d fix — JSON-tree "apply JSON" wiped the form (2026-06-20 18:08, user-found, FE-only).** The
raw-JSON textarea buffer was **snapshotted once on open** (`openRaw`) and never re-synced, so after
opening raw the textarea went stale vs later form edits; "apply JSON" then parsed the stale snapshot
— e.g. set camera → open raw (buffer `{"camera":…}`) → fill scene/subjects → apply → everything but
camera wiped. Fix: a `useEffect` keeps the textarea synced to the **live** `serializeFlux2PromptTree`
while the panel is open (deps `[json, rawOpen]`), so apply round-trips the current form; typing/
pasting doesn't change `json`, so a manual/pasted edit survives until applied (external-JSON import
still works). Removed `openRaw`; the toggle just flips `rawOpen`. Also fixed the header layout
(subtitle under the title, `d6e5b07`). `tsc` + `vite build` clean. **✅ PUSHED `cb17833`.**

**M0d fix — flux.2-dev crashed loading the Mistral processor (2026-06-20 18:24, user-found, worker
lib).** First real dev run died: `HFValidationError: Repo id must be 'namespace/repo_name':
'model/unsloth/Mistral-Small-3.2-24B-Instruct-2506-unsloth-bnb-4bit'`. Root cause in the **vendored
BFL flux2 lib** (`flux2/src/flux2/text_encoder.py` `Mistral3SmallEmbedder.__init__`): the upstream
`model_spec_processor` default is a BFL-infra **local path** `"model/unsloth/…"` whose stray
`model/` prefix is an invalid HF repo id (3 segments). The dev MODEL loads from
`mistralai/Mistral-Small-3.2-24B-Instruct-2506`, but its **processor** must come from the **unsloth
bnb-4bit** repo — that repo ships the HF-transformers `processor_config.json`/`tokenizer.json`, while
the official mistralai repo has only `tekken.json` (no `AutoProcessor`). Fix: strip the `model/`
prefix → `unsloth/Mistral-Small-3.2-24B-Instruct-2506-unsloth-bnb-4bit` (both repos already cached in
`F:\HF_HOME`). **loom patch to a vendored third-party lib** — applied to BOTH the monorepo source
`flux2/src/flux2/text_encoder.py` and the vendored `loom/.../pipelines/multistack/flux2/src/flux2/`
(byte-identical, md5 `fdc29d8…`), commented as a deviation so a future re-vendor won't silently
revert it. Verified offline: `validate_repo_id` passes + `AutoProcessor.from_pretrained` loads from
cache (PixtralProcessor) + yes/no token encode works. 276 backend tests green. **✅ PUSHED `2188f7a`.**

### M0e — flux.2 low-res-first + creative upscale (spec §12 "M0e"; WBS P2-M0e)

started: 2026-06-21 07:43
finished: 2026-06-21 08:15 (Parts A + B + C all built same day; 284 backend tests; visual sign-off owed)

Author request (2026-06-21) — the **final course-correction before the M1 trainer gate**. `flux.2-dev`
(the gated Mistral-VLM variant) runs **far faster at low resolution** on the 16 GB ROCm rig (512² ≈ 1 k
image tokens vs ~4 k at 1360×768 — `kb-flux2.md` "denoising stall analysis"), so the efficient workflow
is **author small with dev, then i2i-upscale**. Design (spec §12 "M0e solution design", committed with
this entry) has three **additive** parts — **no new worker capability**, catalog + orchestrator +
frontend only, reusing the M0c postprocess-stack contract:
- **a** — default `flux.2-dev` image size to **512²** (per-variant catalog default + model-aware
  `/generate` resolution + model-aware drawer placeholder; display==reality, M0c discipline).
- **b** — an **output size** (scale-factor quick pick **and** explicit W×H override) on the M0c i2i
  postproc steps so a `Clean`/`Refine` over **zimage/sd35** re-diffuses larger = i2i creative upscale
  (not flux2 — flux2 i2i re-poses at source dims).
- **c** — a dedicated **`Upscale ✨`** preset = single-run `sd35` **cn-inpaint + SD3.5 Tile ControlNet**
  (`InstantX/SD3-Controlnet-Tile`, already registered in the worker) at the target size + tile-CN
  weight gate/fetch. Postproc-only (not on `/generate`).

Built part-by-part (build → push) like M0d. **No `src/pipeline/` worker code changes expected → no
re-vendor** (the sd35 worker already registers the tile CN + supports cn-inpaint; the flux2/zimage
workers already honour width/height — M0e is orchestrator + frontend surface).

**Part A — `flux.2-dev` defaults to 512² (finished 2026-06-21 07:57).** ⚙ Backend: the `flux.2-dev`
catalog variant's `defaults` now carries `width:512, height:512` (model_catalog.py); a new
`model_size_default(pipeline, model_name) -> (w|None, h|None)` reads the per-variant override (else
(None,None) → caller falls back to `param_default`). The `/generate` single-pipeline unset-size block
(the M6-review "display==reality" fix) now resolves the **effective model** first (`base.get("model_name")`
— params-channel override > top-level > default, matching the weight pre-flight's precedence) and uses
its `model_size_default` before the pipeline default, so an unset dev cast emits `--width 512 --height
512`; non-dev flux2 keeps 1360×768; explicit dims (top-level or params) still win. Frontend: `ModelVariant.defaults`
typed (`orchestrator.ts`); `ParamControls`/`renderParamControl` gained a `sizeDefaults` prop that overrides
**only the width/height placeholder** (values untouched); App computes it via `modelSizeDefaults(pipeline,
modelId)` from the catalog variant `defaults` and passes it to both the cast (A) and Stage-B drawers, so
selecting flux.2-dev shows a 512 placeholder (never a 1360 that lies about what renders). **Tests:**
`test_model_catalog.py` +1 (`model_size_default` dev=512²/others None + served on the variant);
`test_multi_params.py` +1 (`test_flux2_dev_unset_size_defaults_to_512` — dry-run argv 512² on dev top-level
+ params-channel; non-dev 1360×768; explicit dims win). **278 backend tests** (276→+2), green; `tsc
--noEmit` clean. No `src/pipeline/` → no re-vendor.

**Part B — output size on the M0c i2i postproc steps (finished 2026-06-21 08:04).** The i2i upscale.
⚙ Backend (`main.py`): two module helpers — `_round16(x)` (snap to /16, clamp [256,2048]) and
`_postproc_target_dims(src_dims, params_in)` (explicit `width`+`height` win → else a `scale` factor
over the source → else source dims unchanged). `add_postproc_step` now adds `width`/`height`/`scale` to
the **i2i allowed set for zimage/sd35 only** (flux2 i2i re-poses at source dims — excluded); a new
`_validate_postproc_size` enforces width/height = /16 ints in [256,2048] set as a pair, scale a number
in [1.0,4.0] (422 otherwise). The queue endpoint's img2img branch (refactored to `if is_flux2 / elif
is_io / else`) computes the batch job's width/height via `_postproc_target_dims` instead of the hard
`_image_dims` source dims; restore (io) + flux2 i2i keep source dims. diffusers resizes the init image
to the requested H×W → init=source + larger target IS the upscale (no worker change). Frontend
(`App.tsx`/`styles.css`): the `PostprocPanel` add-form gained a `.pp-size` row (scale select ×1.5/×2/×4
+ explicit out-W/out-H inputs, mutually exclusive) shown when `sizeable = isI2i && !isFlux2`; `submit`
sends `width`+`height` (both typed) else `scale`; the step-attrs line shows `×N` / `W×H`. **Tests:**
`test_postproc_stack.py` (**+2** — scale ×2 → 2048², explicit W×H wins, no-override = source dims;
validation 422s for not-÷16 / unpaired / out-of-range scale / below-min, and flux2 rejecting size).
**280 backend tests** (278→+2), green; `tsc --noEmit` clean. No `src/pipeline/` → no re-vendor.
- **Refinement (2026-06-21, user-found, ✅ PUSHED `4705a90`):** the scale select offered only ENLARGE
  (×1.5/×2/×4); added **reduce** presets ×0.5 / ×0.75 (dropdown now ×0.5 · ×0.75 · size: source ·
  ×1.5 · ×2 · ×4). `_validate_postproc_size` floor `1.0 → 0.25` (reductions pass; `_round16` still
  clamps to 256); `_postproc_target_dims` already handled `<1.0`. +1 reduce test + a below-floor 422.

**Part C — dedicated `Upscale ✨` preset (SD3.5 Tile ControlNet) (finished 2026-06-21 08:15).** The
structure-preserving high-ratio upscale the i2i resize can't match. ⭐ Key finding: the sd35 worker
**already registers** the tile CN (`stage1_load_pipeline._CN_REPOS["tile"]="InstantX/SD3-Controlnet-Tile"`)
and supports `cn-inpaint`, and its batch `run_jobs` is t2i/img2img/inpaint **only** ("CN modes … stay
single-run") — so this is a **single-run** sd35 job exactly like M0c's flux2-dev i2i, **no
`src/pipeline/` worker change → no re-vendor**. ⚙ **Adapter** (`sd35.py`): `WIRED_PARAMS` += `controlnet`/
`control_image`/`cn_scale` (single-run `build_argv` already routes through `emit_argv`, which gates them
to `modes=["cn-inpaint"]`); `cn-inpaint` **kept OUT of `WIRED_MODES`** (postproc-only — it's reachable
solely via the queue endpoint, which never consults `WIRED_MODES`, so `/generate` still can't request a
mode that needs a per-item control image). **Backend** (`main.py`): new `_PP_PRESETS["upscale"]`
(`backend:sd35`, `mode:cn-inpaint`, `params:{controlnet:"tile", cn_scale:"0.6", scale:2}`);
`AddPostprocStepRequest.preset` Literal += `upscale`; `add_postproc_step` handles `is_upscale`
(sd35-fixed backend; allowed `{prompt,model_name,cn_scale,width,height,scale}`; a **medium-only guard** —
the InstantX tile CN is SD3-medium, so a non-`sd3.5-medium` model 422s); the queue endpoint gained an
`is_upscale` branch building a single-run `{prompt, control_image=source, controlnet:"tile", cn_scale,
width/height}` job at the Part B target dims (no `init_image` — the tile CN is the conditioner; diffusers
resizes it to the target H×W = the upscale), with the prompt resolved like clean/refine (typed > source
prompt) and a **tile-CN weight 412 pre-flight** (`postproc_weights_status("sd35_tile_cn")`, separate from
the sd3.5-medium base check) offering `POST /components/fetch?postproc=sd35_tile_cn`. **models.json**:
new `postproc.sd35_tile_cn` weight entry (`InstantX/SD3-Controlnet-Tile`, probe `config.json`, snapshot
fetch — a CN repo has no `model_index.json`, so the generic postproc gate/fetch handles it). **Schema**:
`postproc_store` preset enum += `upscale`. **Frontend** (`App.tsx`/`orchestrator.ts`): `PostprocStep.preset`
type += `upscale`; the PostprocPanel add-form gained an **`Upscale ✨ (tile)`** option — no backend picker
(sd35-fixed), a `cn_scale` field + optional prompt + the shared (factored) `sizeRow` (scale + explicit
W×H, default ×2). **Tests:** `test_postproc_stack.py` (**+3** — single-run cn-inpaint with tile control
image at ×2 / inherited prompt / cn_scale; sd35-fixed + medium-only + explicit-size override; tile-CN
412 pre-flight via monkeypatch); `test_sd35_adapter.py` (**+1** — cn-inpaint argv emits
`--controlnet/--control-image/--cn-scale`, no `--init-image`, WIRED_PARAMS advertises the CN params).
**284 backend tests** (280→+4), green; `tsc --noEmit` + `vite build` clean. **No `src/pipeline/` worker
code touched → no re-vendor.**

**✅ M0e COMPLETE (Parts A + B + C). PUSHED `1ce1540`** (single commit — A+B+C + spec/journal + tests).
**Tile-CN weight verified present on the rig (2026-06-21):** the author fetched
`InstantX/SD3-Controlnet-Tile` into `F:\HF_HOME\hub` (snapshot `48005f2…`, blobs ~1.19 GB incl. the real
`diffusion_pytorch_model.safetensors`); `components.postproc_weights_status("sd35_tile_cn")` → `True` and
the paired `sd3.5-medium` base `image_model_present` → `True`, so the `Upscale ✨` 412 pre-flight passes.
⚠ Visual sign-off still owed on the running app (the dev 512² default in the cast drawer; the i2i
output-size row on Clean/Refine; the `Upscale ✨` tile-CN preset). ⚠ Rig run owed to confirm a real
flux.2-dev 512² → tile-CN upscale loop end-to-end on ROCm. ⏭ Next: **M1** training spike — the **P2-0
ROCm go/no-go front-gate** (does ai-toolkit train on RX 9070 XT / ROCm at all?).

---

## Pre-M1 codebase + plan review — 2026-06-21 10:49

**Scope:** reread the application description/roadmap, P0/P1/P2 specs and journals, and the P3–P6
forward dependencies; mapped the current code with the fresh local Graphify graph; then inspected the
workspace/record, queue/recovery, adapter, lineage, component/weight, postprocess, Tauri, and React
surfaces against the P2 contract. The working tree started clean at `ec2519c`, tracking
`origin/main`; `core.hooksPath=.githooks` and the pre-push Graphify refresh are installed.

**Verdict: ✅ ON PLAN for P2/M1.** No architectural deviation or missing P0/P1 contract requires a
redesign before training. The intended next milestone is **P2/M1** (the request's `P1/M1` is treated
as a phase-number typo): P1 is functionally closed, M0a–M0e are landed, and both the P2 spec and this
journal name the ai-toolkit ROCm gate/spike as next. The current spine is the planned one: files are
the source of truth; orchestrator-owned atomic writes + JSON schemas; one durable workspace-bound GPU
queue; normalized subprocess adapters; per-output lineage/provenance; Saved-unfinalized profile
versions carrying self-contained curated refs + frozen coverage cells.

**Evidence:**
- backend: **284/284 tests passed** via `Invoke-RtkPytest.ps1` / RTK;
- frontend: `tsc && vite build` clean (33 modules, production bundle emitted);
- desktop shell: `cargo check --locked` clean;
- Graphify: current code-only graph = **2,216 nodes / 3,780 edges**, benchmarked at **14.6×** less
  context per representative query; exact-source checks matched its ownership/call-flow hints;
- hardware baseline: shared venv reports **torch 2.9.1+rocm7.2.1**, HIP **7.2.53211**, one
  **AMD Radeon RX 9070 XT**.

**Findings / guardrails before and during P2:**
1. **M1 remains a genuine red front-gate, not a paper exercise.** Current upstream ai-toolkit
   (`ostris/ai-toolkit` commit `548a286`, MIT) now explicitly supports `Tongyi-MAI/Z-Image`, but its
   official installation requirement still says **NVIDIA GPU** and documents CUDA wheels only. It
   has no claimed Windows-ROCm path. The shared ROCm venv also lacks ai-toolkit's key training deps
   (`optimum-quanto`, `peft`, `lycoris-lora`, `torchao`, `bitsandbytes`), while the upstream full
   requirements resolver would try to install ordinary Windows **torch 2.12.1/CUDA-oriented**
   packages and replace several versions used by working inference. **Guardrail:** test from the
   pinned shallow research clone with an isolated dependency overlay first; do not mutate the known-
   good shared ROCm stack or vendor 42 MB of trainer code until the can-run gate is green.
2. **`resumable=true` is only a recovery marker today.** The queue correctly changes an interrupted
   resumable job back to queued, but it has no checkpoint discovery/`--resume` handoff yet. This is
   **not a deviation**: P2-10 explicitly belongs to M2. M2 must add a trainer-specific submission
   shape (rather than merely flipping the boolean), checkpoint cadence, latest-valid-checkpoint
   discovery, and a restart test.
3. **Training-context provenance has one early watch item.** Curated refs durably retain coverage,
   source job/output, pipeline, method, and seed, but not the selected L1 `style_id`; deleting the
   source queue job can therefore erase the exact style selection needed by P2-13's
   `training_context.json`. Preserve the resolved style id/snapshot at curation time before M3/M6
   writes graph-ready training facts. This is a small additive schema/provenance correction, not a
   blocker for the M1 fixed-dataset spike.
4. **Complexity pressure, not a contract failure:** `App.tsx` is 3,378 lines and `main.py` is 2,439.
   M1 is no-UI and should not refactor them. Starting with M2, put training records/services/endpoints
   and Train-panel UI in dedicated modules/components instead of adding another feature family to
   either monolith.
5. **Owed rig/visual checks remain explicit and non-blocking:** formal P1 A–H rig acceptance plus
   M0d/M0e visual/upscale checks are still open. This matches the recorded author decision to move
   into P2 in parallel; it is not hidden acceptance debt. Run them before a later milestone depends
   on their visual quality, not as a prerequisite for the trainer can-run probe.

**Review close:** proceed with **P2-0 first** (minimum ai-toolkit import/model-load/backward/optimizer
probe on ROCm), and only on GO continue P2-1 (fixed P1 ref set → short Z-Image LoRA → inference
load/reproduction). If any ROCm-only patch is required, keep it minimal, comment it as a pinned
upstream deviation, and record it before vendoring.

---

## M1 — ai-toolkit ROCm gate + fixed-dataset training spike (P2-0/P2-1)

started: 2026-06-21 10:50
finished: 2026-06-21 22:45

### P2-0 — ROCm can-run gate (2026-06-21 11:05–12:08; **✅ GO**)

**Pinned input/runtime:** `ostris/ai-toolkit` commit
`548a286992261fbef40c380e82495d21fd3bca86` (2026-06-19, MIT), exercised from an ignored clone +
isolated dependency overlay. The known-good shared runtime remained unchanged:
`torch 2.9.1+rocm7.2.1`, HIP 7.2, RX 9070 XT, cached `Tongyi-MAI/Z-Image` weights.

**Probe fixture:** one real P1 `stubz001/char01/v1_base` curated ref, deterministic `char01_lw`
caption, rank/alpha 4/4, one step, batch 1, 256 bucket, bf16, gradient checkpointing, qfloat8 Quanto,
low-VRAM, plain AdamW, sampling disabled. It loaded + quantized Z-Image, attached **240 LoRA
modules**, cached the ref, completed forward/backward/optimizer (`loss=.4859` on the clean rerun),
saved a loadable-shape **21.3 MB / 480-tensor** adapter, cleaned up, and exited **0 in 37 s**.
SHA-256: `3C8446F94DC6AC0769227E6CAD3DE71DE53285FF612A7C78DB10DEDA407299C9`.

**Minimal Windows-ROCm compatibility patch (now vendor-recorded):** NumPy 1.26.4 beside upstream
SciPy 1.12; TorchAO optional (qfloat8 remains optimum-quanto); bitsandbytes absent + AdamW; pinned
Diffusers FSDP imports optional for single-GPU; `AI_TOOLKIT_MINIMAL_ZIMAGE=1` registers only
`sd_trainer` + `ZImageModel`; missing `torch.distributed.is_initialized` cleanup predicate supplied.
These are eager-import/cleanup seams, not changes to the training algorithm. **Gate verdict: GO.**

### P2-1 — fixed 17-ref training + inference bridge (2026-06-21 12:10–22:45; **✅ GO**)

**Full fixed-set training passed.** Copied all **17** finalized P1 refs + deterministic captions to
the ignored fixture; trained Z-Image at 512 px, rank/alpha 16/16, 100 steps, batch 1, bf16,
gradient-checkpointed qfloat8/Quanto, low-VRAM, plain AdamW 1e-4, no sampling. The real run exited 0
after **1,600.7 s (~26.7 min)** and produced an **85,094,880-byte** adapter (plus the step-50
checkpoint), SHA-256
`BD29BCD70C389E3CA110B0F28D02E12C5982D39F1CC4A2EA9C4D888D49B96E91`. This was a valid can-run
artifact but its fixed-seed inference did **not** reproduce the subject (younger/different face;
ArcFace centroid similarity `-0.067`, base control `-0.016`). **100 steps is not the default.**

**Preset-finding continuation (same run, real resume):** raised only the total-step target; ai-toolkit
twice discovered the latest final adapter, read step metadata (**100→300→500**), restored
`optimizer.pt`, kept the exact dataset/network/LR, and exited 0. Aggregate training time was
**8,097.5 s (~135.0 min)**. The accepted final is **500 steps**, 85,094,896 bytes, SHA-256
`B84DA64D6E642D18F62950BB522405AC560B101ADA6B4C2A89E46A3CAEB1EA1C`. This also validates the
upstream checkpoint/optimizer mechanism M2 will wrap (but does not replace M2's queued resume tests).

**Vendored after the GO (R162):** the proven source snapshot landed first at
`src/trainer/ai-toolkit/`, then byte-identically at app `trainers/ai-toolkit/` (**0 drift across 381
non-cache/non-weight files**). `LOOM_VENDOR.md` pins upstream/license, every compatibility seam,
dependency-overlay constraints and artifact evidence; the exercised 500-step preset
shape is `config/loom_zimage_rocm.example.yaml`. Trainer outputs/state/weights stay ignored, and
`trainers/` is excluded from the Loom Graphify graph so third-party internals do not swamp the
application architecture.

**Inference bridge wired monorepo-first + byte-identical:** Z-Image now accepts
`--lora-path/--lora-name/--lora-weight` in single and batch modes. Full-file paths are normalized to
Diffusers directory + `weight_name`, adapters are explicitly named/scaled, missing files fail before
the base model loads, and the resolved path/name/weight + SHA-256 are written into stage provenance.
Catalog/adapter capability + argv wiring added. Drift guard: stage1 MD5
`C1E8A3CE273B131D404930BAE38A0BF0`; runner MD5 `F8D20C7FC287BD8863E5FB5B073B5F48`
across monorepo + both app copies. Verification: Python compile clean; focused LoRA/catalog/adapter
contracts **32 passed**; milestone-close full backend **294 passed**.

**Real inference + reproduction acceptance:** the first exact-worker load exposed one integration
dependency honestly: the shared inference venv lacks PEFT (`ValueError: PEFT backend is required`).
Per the environment guardrail, it was **not mutated**; the already-isolated overlay (`peft==0.18.1`
and pinned Diffusers) loaded the adapter successfully. M2 must make that overlay a declared runtime
dependency before exposing queued LoRA jobs.

The accepted worker run used Z-Image Base, 512², 30 steps, guidance 4, seed `424242`, LoRA weight
**1.0**, and only the deterministic caption `char01_lw, front view, full body, neutral expression`—
no explicit age/hair/costume/background/style hints. It exited 0 in **134.49 s**; its manifest records
the adapter name/weight/path + exact SHA. The output (338,392-byte PNG, SHA-256
`6BA1CC4D6A9017C6956AE14391F1529BED7DC73D14AE535F53CA0FF22F242E92`) visibly reproduces the older
silver-haired subject, olive trench coat, stern expression, fluorescent room, and vintage treatment.

**Identity honesty:** InsightFace detected 16/17 refs; their own mean pairwise similarity is `0.537`
(p10 `0.405`). The final test rises materially over the base control (`centroid -0.016 → 0.263`,
best-ref `0.044 → 0.300`) but remains below the curated set's cross-view band. Verdict: the LoRA
reproduces the **whole character concept** and retires the M1 training risk, but it is not a face-lock
replacement; Loom's existing identity pass remains appropriate where exact facial identity matters.

**✅ M1 COMPLETE.** P2-0 ROCm training GO + P2-1 adapter load/reproduction GO. Default Z-Image
spike preset frozen at **500 steps / rank-alpha 16/16 / 512 px / bf16 / qfloat8 Quanto / AdamW
1e-4 / LoRA weight 1.0**. **✅ PUSHED `3a391d8`**; pre-push Graphify re-extracted 110/110 code
files and correctly reported no application-graph delta (`trainers/` is intentionally excluded).
⏭ Next: **M2 — staged queued trainer skeleton**, beginning with the isolated PEFT/runtime contract.

---

## M2 — staged queued Z-Image trainer skeleton (2026-06-25)

**Status: backend contract slice complete.** This pass moves P2 past the M1 spike into the durable
trainer path without spending GPU automatically:

- Added a distinct queue pipeline, **`zimage_trainer`**, rather than pretending trainer work is
  normal `zimage` inference. The runner now accepts `resumable=True` per submit; ordinary generation
  remains non-resumable by default. The trainer VRAM estimate is registered separately.
- Added **`jobs/staged.json`** via `orchestrator/training.py`. Staging a run writes a durable staged
  record but does **not** write `queue.json`; the explicit `/training/staged/{id}/queue` transition is
  the first moment the GPU queue can see the job.
- Added deterministic P2 records during staging:
  - `captions.jsonl` from the frozen P1 coverage-cell template (`coverage.build_caption`);
  - `caption_policy.json` with template id/source fields/trigger rule;
  - `training_context.json` with graph-ready asset/version/ref facts and context digest;
  - temp dataset copy + `.txt` captions + `dataset_manifest.json`;
  - generated ai-toolkit `train.yaml` using the M1 accepted default preset shape
    (**500 steps / rank-alpha 16/16 / 512 px / bf16 / qfloat8 / AdamW 1e-4 / low_vram**).
- Added the **isolated runtime contract** to staged params: `runtime_overlay`, `requires_peft`,
  `do_not_mutate_shared_inference_venv`, and `AI_TOOLKIT_MINIMAL_ZIMAGE=1`. The wrapper honors an
  overlay through `PYTHONPATH`; it does not install or mutate the shared inference environment.
- Added `trainers/loom_zimage_lora.py`, a thin ai-toolkit wrapper that writes a trainer manifest and
  performs **real checkpoint/artifact discovery** before launch (`optimizer.pt`, sqlite, existing
  `.safetensors`, latest artifact hash). This makes queue recovery auditable instead of relying only
  on the queue's `resumable=true` marker.
- Added API endpoints:
  - `GET /training/staged`
  - `POST /assets/{asset_id}/lora/zimage/stage`
  - `POST /training/staged/{staged_id}/queue`
  - `DELETE /training/staged/{staged_id}`
- Added no-GPU regression coverage in `orchestrator/tests/test_p2_training.py`: stage writes captions
  + context + staged record, staged→queued creates a resumable `zimage_trainer` job, adapter manifest
  parsing works, and wrapper resume discovery records real files.

**Verification:** Python compile clean for the new/changed orchestrator + trainer files. Focused
pytest for `test_p2_training.py`: **4 passed**. RTK full orchestrator suite: **299 passed**.

**Push note:** the worktree was cleaned back to the P2 trainer files before push, so the M2 commit is
scoped to staged trainer records, the `zimage_trainer` queue adapter, the ai-toolkit wrapper, tests,
and this journal update.

⏭ Next: promote-on-success + `lora.manifest.json` writeback and the Train panel UI, then a real queued
short-run resume smoke once the isolated PEFT overlay path is declared on the target machine.

---

## M2.5 — quantized `flux.2-dev` swap + gated-repo elimination (spec §12 "M2.5"; WBS P2-2.5)

**Status (2026-06-26 09:51): backend implementation COMPLETE — no-GPU close criteria met; on-rig dev
smoke + FE advanced-foldout owed (visual sign-off, M0e pattern).** Author added M2.5 as an interim
runtime/model-fit migration: the full BFL `flux.2-dev` stack never fit the 16 GB ROCm rig, so route
the logical `flux.2-dev` id to the Comfy-Org quantized split files proven in the old-project spike
`src/pipeline/flux2_q8`. Reviewed the spec against the live code + spike; resolved three scope
questions and a dependency-elimination opportunity with the author, then built it.

**Decisions (now written into [`kb-loom-p2.md`](kb-loom-p2.md) §12 "M2.5 solution design"):**

- **Scope = single-run dev only.** Quantized dev serves **t2i** (JSON authoring) + **i2i** (M0c/M0d
  postproc, M0e upscale). The **batch `ref` Stage-B sweep stays Klein-only** — loom's
  `pipeline.flux2.run_pipeline.run_jobs` (which calls full-weight `flux2.util.load_flow_model`) is
  **not** rerouted; the spike has no batch loop. Klein remains the §11/R147 identity-preserving
  workhorse. ⚠ Guard so dev can't be silently submitted to a coverage sweep (would hit the OOM path).
- **Integration = fold, not port.** Branch on `model_name == "flux.2-dev"` inside the **existing**
  vendored `run_pipeline.run()` (+ `stage1_load_models` dev branch), reusing the spike's `scaled_fp8`
  loaders and the existing `stage2/3/4`. Keeps one CLI/adapter contract; ⚙ loom's `--cpu-offload` is
  opt-in (default off) — keep it, don't adopt the spike's `--no-cpu-offload` default-on. Dev-only
  knobs `--text-encoder` (`fp8` default / `bf16`) + `--fp8-matmul` (`auto`/`native`/`dequant`), gated
  to dev.
- **Acceptance bar.** Closes on wiring + dry-run argv + no-GPU tests green; the real **on-rig dev
  smoke is owed** (visual sign-off, M0e pattern) — full dev never ran here, so there's no baseline.

**Gated-repo elimination (author confirmed — cut the large repos entirely).** Cache audit
(`F:\HF_HOME`, 2026-06-26):

- The quantized dev path reads only **~17 MB** from the **166 GB** `black-forest-labs/FLUX.2-dev`
  repo: `text_encoder/config.json` (4 KB) + `tokenizer/` (~17 MB) — used by the spike's
  `load_comfy_mistral_text_encoder` (`AutoConfig`/`AutoProcessor.from_pretrained(subfolder=…)`). The
  168 GB of safetensors are unused. → **Vendor** the config+tokenizer into the pipeline tree
  (suggested `pipelines/multistack/flux2/assets/mistral_te/`), load via local path.
- The **90 GB** `mistralai/Mistral-Small-3.2-24B` repo is referenced **nowhere** by the quantized
  path (weights come from Comfy's `mistral_3_small_flux2_fp8`) → free to drop.
- Klein's only remaining BFL file is the VAE (`ae.safetensors`, 321 MB) → **re-point to Comfy's
  identical `flux2-vae.safetensors`** (weight-source change only; Klein runtime/Stage-B unchanged).
- ⚙ Needed Comfy footprint ≈ **51 GB**, public/ungated: `flux2_dev_fp8mixed.safetensors` (34 GB,
  transformer) + `mistral_3_small_flux2_fp8.safetensors` (17 GB, TE) + `flux2-vae.safetensors`
  (321 MB). Skip the fp4 TE (NVFP4 — research), the bf16 TE (34 GB, only for a quality-compare), and
  the Turbo LoRAs. ⇒ after M2.5 **neither gated repo is referenced by any runtime path / `models.json`
  entry** (incl. the `flux2-dev-ae` `multi_presets` rows → Comfy VAE).

**VAE value spot-check (✅ PASS — done 2026-06-26 09:14, gates the Klein re-point).** Compared the two
321 MB files tensor-by-tensor with the spike's `map_comfy_vae_key` remap applied (numpy+safetensors,
no torch; script in session scratchpad):

- Remap is a clean **251 → 251 bijection** (0 missing, 0 shape mismatch, 0 dtype mismatch, 0 uncovered
  BFL keys). The Diffusers→BFL key renames + the q/k/v/proj **2D→4D `maybe_unsqueeze`** all resolve.
- **250/251 tensors bit-identical.** The sole difference is `bn.num_batches_tracked` (BFL=`400000`,
  Comfy=`0`) — a BatchNorm *training* counter never read at inference. The operative BN stats
  `bn.running_mean` / `bn.running_var` are **exact-equal**, as is every conv/attention weight. ⇒
  decode output is identical; Klein VAE re-point is safe.

⚠ **R162 vendoring (build constraint for the fold):** the quantized loader code must land in the
monorepo `src/pipeline/` first, then be copied byte-identically into
`loom/loom-loreweave-studio/pipelines/multistack/src/pipeline/flux2/` (MD5 drift-guard). The spike
already lives at `src/pipeline/flux2_q8`; the fold consolidates its `scaled_fp8` + dev loader branch
into `pipeline.flux2` on both sides.

**Implementation (built 2026-06-26 09:14–09:51, monorepo-first then synced to loom; 319 orchestrator
tests green incl. 12 new).**

- **Vendored Mistral config+tokenizer** → `src/pipeline/flux2/assets/mistral_te/` (`text_encoder/
  config.json` + `tokenizer/*`, ~17 MB + `PROVENANCE.md`). Verified `AutoConfig`→`Mistral3Config`,
  `AutoProcessor`→`PixtralProcessor`, and `apply_chat_template` all load from the **local path** (no
  BFL repo). ⚙ transformers 5.4.0.
- **Folded the quantized dev loader** into `pipeline.flux2` (NOT a separate module): new
  `scaled_fp8.py` (ported from the spike; `config_repo`/`processor_repo` default to the vendored
  dir), a `model_name=="flux.2-dev"` branch in `stage1_load_models.run()` (`_load_dev_quantized` +
  `ComfyMistralEmbedder`, reusing the existing `stage2/3/4`), and `run_pipeline.run()` threading
  `fp8_matmul`/`text_encoder_variant`/`dtype`/`local_files_only` (+ `--text-encoder`/`--fp8-matmul`
  CLI, dev-only). ⚙ Kept loom's opt-in `--cpu-offload` (NOT the spike's default-on). The batch
  `run_jobs` is **guarded to refuse `flux.2-dev`** (single-run only; never reaches the full-weight
  `load_flow_model`).
- **Manifest** gained a `quantized` dict (`backend_variant:"comfy-q8"`, hf_repo, transformer/TE/VAE
  files, te variant, fp8_matmul, dtype, cpu_offload); `{}` for Klein. Set in `run_pipeline` from the
  stage-1 result.
- **Catalog + adapter:** dev variant `repo_id`/`ae_repo_id`→`Comfy-Org/flux2-dev`, `gated:False`,
  `text_encoder` de-mistral'd; two dev-only advanced params (`text_encoder` fp8/bf16, `fp8_matmul`)
  with a `models:["flux.2-dev"]` gate; the M0e **512² default preserved**. `emit_argv` now honors a
  param `models` gate so Klein never emits the dev knobs even with a stale params dict. Adapter
  `WIRED_PARAMS` advertises them.
- **Dropped BOTH gated repos.** Klein's VAE re-points to the public Comfy `flux2-vae.safetensors`:
  `flux2.util.load_ae` now detects the Comfy/Diffusers layout and remaps onto the BFL AutoEncoder
  (`map_comfy_vae_key`, canonical copy in `flux2.util`; `scaled_fp8` keeps a copy, equivalence
  asserted in tests). FLUX2_MODEL_INFO klein/dev `ae_repo_id`/`filename_ae`→Comfy; `load_flow_model`
  + `load_text_encoder` **guard `flux.2-dev`** (raise → the quantized path). `models.json`
  `flux2-dev-ae` rows → Comfy VAE (id kept, `gated:false`). After M2.5 **no active data structure
  (models.json / catalog / FLUX2_MODEL_INFO) references `black-forest-labs/FLUX.2-dev` or
  `mistralai/Mistral-Small`** (only doc/comment strings + dead BFL-lib `text_encoder.py` default
  remain; no download path reaches them).
- **VAE re-point validated no-GPU:** the real Comfy VAE remaps key-for-key onto the BFL AutoEncoder
  (251/251) and `load_state_dict(strict=True)` succeeds — the durable test
  `test_comfy_vae_remaps_onto_bfl_autoencoder_strict` (skips if the file isn't cached). Value
  equivalence was the one-time spot-check above.
- **Tests:** `orchestrator/tests/test_flux2_dev_quantized.py` (12) — dev argv/size, structured
  elimination invariant (models.json + catalog + FLUX2_MODEL_INFO), dev loader guards, manifest
  field, batch-guard, key-map equivalence, Comfy-VAE strict-load. Full suite **319 passed**. Dev
  single-run `build_argv` emits `… --model-name flux.2-dev --text-encoder fp8 --fp8-matmul auto
  --cpu-offload`.

⚙ Disk win realised: dev's runtime download drops from ~150 GB gated dev weights + 90 GB Mistral to
~51 GB public Comfy split files (fp8 transformer 34 + fp8 TE 17 + VAE 0.3); Klein keeps its klein
flow/Qwen3 repos + the 321 MB Comfy VAE.

**Fix (2026-06-26, user-reported false-negative gate).** The standalone `/generate` + img2img-cast +
postproc weight pre-flights probe presence via `components.image_model_present(repo_id)` =
`model_index.json` — which Klein/sd35/zimage/ltxv repos HAVE but the Comfy split-files repo does NOT,
so a fully-cached dev run was wrongly 412'd ("flux2 model 'flux.2-dev' not in cache"). Added
`components.variant_weights_present(variant)`: a variant may declare `probe_files`, then ALL must be
cached; else falls back to `model_index.json`. The dev catalog variant now carries `probe_files`
(the 3 `split_files/…` paths); the 5 gate call sites (`main.py` 777/947/1673/1892/2284) use the new
helper. Klein/sd35/zimage/ltxv unchanged (no `probe_files` → same model_index.json probe). +3 tests
(15 M2.5 / 322 suite green). These are loom-orchestrator files (not vendored) → no R162 sync.
⚠ Targeted fetch of the 3 dev split files on a fresh rig (vs a whole-repo snapshot that would also
pull the 34 GB bf16 TE / fp4 / Turbo LoRAs) is still owed.

**Follow-up (2026-06-26, user-requested): FE dev-knob exposure + defaults audit.**
- **FE dev knobs.** `ParamControls` filtered out `advanced` params and ignored the new `models`
  gate, so `text_encoder`/`fp8_matmul` never rendered. Added a `models?: string[]` field to the
  `ParamSpec` TS type and a **model-scoped advanced foldout**: a `<details class="p-advanced">`
  rendering advanced params whose `modes` AND `models` match the current `model_name`. So dev shows
  an "advanced (2)" foldout (text_encoder fp8/bf16, fp8_matmul auto/native/dequant); Klein shows
  nothing. Full path verified: FE params channel → `validate_params` (accepts the knobs, rejects bad
  enums) → `emit_argv` (model-gated) → worker. `tsc` + `vite build` clean.
- **Defaults audit.** Size is correct in code — the dev variant `defaults` carry **512²** and an
  unset cast resolves to 512² (`model_size_default`); the **768 the user saw is the drawer
  *placeholder*** leaking the flux2 pipeline height default, shown only when the FE can't resolve the
  model override (a stale/un-refreshed `/models`). Guidance was inconsistent (variant 4.0 vs preset
  4.5) — now both **4.0** (author set the variant + preset to **8 steps / 4.0** for fast 512² dev
  drafts). ⚠ dev is NOT step-distilled — 8 steps without the Comfy Flux2-Turbo LoRA (not wired) will
  be under-denoised; the spike used 50. Left as the author's deliberate choice.

**Batch dev in expansion (2026-06-26, scope amended — author's real driver).** The original M2.5
call ("batch ref stays Klein-only") is **reversed**: dev's advanced structured-JSON prompting is
wanted in the **expansion/curation screen** (the Stage-B coverage sweep), so `run_jobs` now routes
`flux.2-dev` to the quantized loaders.
- `run_pipeline.run_jobs`: removed the dev refusal; branched **Phase 1** (encode-all) to the Comfy
  Mistral TE (`ComfyMistralEmbedder`) and **Phase 2** to the fp8 transformer + Comfy VAE
  (`scaled_fp8.*`), Klein path unchanged. The existing encode-all → free-TE → load-flow structure is
  exactly dev's memory profile (17 GB TE and 34 GB transformer never co-reside), and the slow load is
  paid ONCE per sweep. Batch summary carries `backend_variant:"comfy-q8"`.
- Adapter `_SHARED_KEYS` += `text_encoder`/`fp8_matmul` so a dev sweep applies the knobs once for the
  whole batch.
- **The Stage-B endpoint + FE were already dev-aware** (built in M0d/M0e): per-cell JSON prompts for
  dev (`json_prompt = advanced_prompt and eff_model=="flux.2-dev"`), `model_size_default`→512²,
  the `variant_weights_present` gate, and the "Dev / JSON" sampling preset that selects dev +
  enables advanced prompting. My generic `ParamControls` foldout already surfaces the dev knobs on
  the Stage-B params bar. So only the worker guard + `_SHARED_KEYS` were missing.
- Tests: `test_batch_run_jobs_routes_dev_to_quantized` (intercepts the first quantized call →
  proves dev is routed there, not refused, and the summary records `comfy-q8`) +
  `test_batch_shared_block_carries_dev_knobs`. Full suite **325 passed**.
- ⚠ Speed: dev is guidance- but NOT step-distilled — a sweep at the author's 8-step default will
  under-denoise without the Comfy **Flux2-Turbo LoRA** (not wired; LoRA-on-scaled-FP8 is the next
  pass). Run dev sweeps at ~50 steps until the Turbo LoRA lands. On-rig sweep sign-off owed.

**Fix (2026-06-26, first on-rig sweep — VAE dtype).** First real dev sweep failed at
`encode_image_refs`: `RuntimeError: Input type (float) and bias type (c10::BFloat16) should be the
same`. Cause: the dev path loaded the **VAE in bf16** (mirroring the spike, which only ever ran t2i =
decode-only). Klein's `load_ae` loads the same Comfy VAE in **float32** (the file's weights are F32,
preserved by `assign=True`), and the ref/i2i path feeds the VAE a **float32** image — so a bf16 VAE
mismatches on `conv_in`. Decode tolerated bf16 only because `AutoEncoder.inv_normalize` (float32 `bn`
buffers) promotes the bf16 latent to float32 anyway. **Fix:** load the dev VAE in **float32** (Klein
parity) in both dev loaders (`stage1._load_dev_quantized` + `run_jobs` Phase 2); transformer + TE stay
bf16/fp8. Regression test `test_dev_loaders_use_float32_vae_bf16_transformer` (mocks the three Comfy
loaders, asserts vae=float32 / tr=te=bf16). Suite **326**. ⚠ The spike `src/pipeline/flux2_q8` has
the same bf16-VAE bug (its `ref`/`img2img` modes were never exercised) — do not port its VAE dtype.
(Benign: 24× `Kwargs passed to processor.__call__ …` deprecation warnings from the TE's
`apply_chat_template` on transformers 5.4 — noise, encoding succeeds.)

**Fix (2026-06-26, user-reported black t2i output on rig).** Test project `loom/stubz001`,
`job_5ed46dc6/flux2_20260626_082100_s1444149108.png`, was not a prompt/color failure: the PNG was
all black because stage 3 produced **NaN latents** (`x_min/x_max/x_mean = NaN`) and stage 4 decoded
them without complaint. The queue payload had explicitly sent `flux.2-dev` with **50 steps** and
guidance `4.0` from the pre-quantized Dev/JSON/full-dev profile. That is outside the locally proven
Comfy q8 operating point (8 steps with native scaled-FP8 matmul); the run took ~686 s and still
ended non-finite.

Fixes:

- M2.5 `flux.2-dev` defaults now use the quantized-safe profile: **8 steps / guidance 4.0 / 512²**
  in both the live catalog variant and the Dev/JSON sampling preset. The vendored worker registry
  (`FLUX2_MODEL_INFO["flux.2-dev"].defaults`) matches, so unset CLI runs also fall back to 8 steps.
- `stage3_denoise` now computes finite-aware latent stats and raises `FloatingPointError` if denoise
  returns NaN/Inf, refusing to decode a likely-black image. The manifest debug path now reports
  `x_finite`, finite counts, finite ratio, and finite-only min/max/mean.
- Klein base defaults stayed at 50 steps; only `flux.2-dev` moved to 8. Added no-GPU regression
  coverage for the dev default and the non-finite guard.

Verification: `test_flux2_dev_quantized.py` **17 passed**, `test_model_catalog.py` **22 passed**,
targeted `test_flux2_adapter` **2 passed**, direct `/generate` dev-size regression **1 passed**.
⚠ Restart/reload Loom so the frontend/server sees the updated `/models` catalog before rerunning the
rig smoke; rerun t2i JSON at the 8-step default, then i2i/postproc.

⚠ **Owed (not gating the no-GPU close):** (1) on-rig dev smoke — real quantized t2i JSON + i2i
postproc generate on the RX 9070 XT using the **8-step q8 default**, manifest shows `comfy-q8`; (2) a
Klein `ref` sweep still decodes correctly on the Comfy VAE; (3) FE advanced foldout to surface the
`text_encoder`/`fp8_matmul` dev knobs (catalog already serves them). (4) confirm
`Comfy-Org/flux2-dev` is ungated at fetch.

**Follow-ups (2026-06-27): dev knobs on every surface + per-image timing.**
- **Dev knobs are now inline, not in a foldout.** The `text_encoder`/`fp8_matmul` catalog params
  dropped `advanced: True` (kept the `models:["flux.2-dev"]` gate), so they render directly in the
  params panel when dev is selected (supersedes the earlier "advanced foldout" note above).
- **Stage-B knob visibility fix.** The knobs showed on the t2i cast bar but not the expansion
  screen: `ParamControls` gated on `values.model_name`, but Stage-B holds its model in a SEPARATE
  `stageBModel` state (not `advParamsB.model_name`). Added an explicit `modelName` prop to
  `ParamControls` (falls back to `values.model_name`) and passed `stageBModel` from the Stage-B
  panel. Backend already accepted them (`validate_params("flux2","ref",…)` + the `channel` →
  `params` path); it was purely a FE visibility gate. (Corrects the batch-dev note's "FE already
  wired" claim — the worker guard + `_SHARED_KEYS` were done, but the Stage-B FE gate was missing.)
- **Per-image generation time in the inspector** (user request — the batch sweep's per-cell cost
  was invisible; only the batch total showed). The data already existed: Stage-B records per-cell
  `duration_s` (`run_jobs`), multi records per-candidate `duration_s` (stage_runner /
  arch_compose_character). Propagated it to `output_meta`: `_batch.parse_batch_result` copies the
  item `duration_s`; `multi.parse_result` now builds `outputs_meta` (parallel to outputs) with
  per-candidate `duration_s`+seed+pipeline. The runner already keys `outputs_meta` → `output_meta`
  by filename. Inspector now shows **`batch`** (whole-batch compute) **+ `image`** (the selected
  image's own time) for batch jobs, and the existing **`duration`** for single jobs. Tests extended
  (`test_batch_worker`, `test_multi_adapter`), `OutputMeta` TS type gained `duration_s`/`pipeline`/
  `pass`. Suite **326**; `tsc` + `vite build` green. (Confirmed the dev-sweep cost is inherent:
  single dev 512²/8-step denoise ≈ 87 s — 34 GB transformer paged over PCIe on 16 GB — so a 31-cell
  sweep ≈ 45 min; Klein is the fast sweep workhorse, dev for single key shots.)

⏭ Push at milestone close (carried convention) once the author signs off the on-rig smoke.

---

## M2.6 — Turbo LoRA (low-step `flux.2-dev` sweeps) (spec §12 "M2.6"; 2026-06-27)

**Status: backend built + no-GPU-verified; on-rig quality/speed sign-off OWED.** Goal: make dev
coverage sweeps practical. A single dev 512²/8-step denoise ≈ 87 s (34 GB fp8 transformer paged over
PCIe on 16 GB) → a 31-cell sweep ≈ 45 min, and 8 raw steps under-denoise (dev isn't step-distilled).
The Comfy **Flux2-Turbo LoRA** is a step-distillation adapter that should give good quality at ~4–6
steps. Author asked: does JSON prompting survive, and can it be an optional dev param? **Yes to both.**

- **JSON prompting persists** — orthogonal: the LoRA adapts only the **transformer** (denoise
  convergence); the Mistral TE that parses the JSON is untouched. (Distillation softens *guidance
  adherence* a bit; the structured-prompt content stays.)
- **LoRA structure** (`Flux2TurboComfyv2.safetensors`): 170 rank-256 BF16 pairs (`B(A·x)`, no alpha ⇒
  scale = strength, default 1.0). Targets attention + `single_blocks` + embeddings + modulation; NOT
  the double-block MLPs.
- **The crux — Diffusers→BFL key map + qkv fusion** (`scaled_fp8.map_comfy_lora_key`). The file mixes
  two namespaces: `diffusion_model.*` (103 mods) map 1:1 to BFL; `transformer.*` (67, Diffusers) remap
  — embeddings (`context_embedder`→`txt_in`, `x_embedder`→`img_in`, `proj_out`→`final_layer.linear`),
  `to_out.0`/`to_add_out`→`*_attn.proj`, and the **qkv fusion**: BFL fuses q,k,v into one `*_attn.qkv`
  Linear, so the LoRA's separate `to_q/k/v` (+ `add_q/k/v`) apply to **output-row slices**
  `[0:6144]/[6144:12288]/[12288:18432]`. ✅ Verified: **170/170 map onto real BFL Linears**, in-features
  match, all 16 fused qkv tiled by exactly the 3 slices; the only uncovered BFL Linears are the
  (unadapted) double-block MLPs.
- **Application — forward hooks** (`load_flux2_turbo_lora` / `apply_turbo_lora`). Rather than
  special-casing `ScaledFP8Linear` vs `nn.Linear`, a `register_forward_hook` adds
  `strength·F.linear(F.linear(x, A), B)` to each target's output (each at its row offset; one hook per
  fused qkv carries the 3 q/k/v). A/B resident bf16; `.to(x.device, x.dtype)` in the hook is
  offload-safe (no-op when matched). The base fp8 matmul is unchanged. ⚙ Attached on `torch_device`
  (compute device) — hooks fire at denoise, after run() moves the flow model to GPU.
- **Wiring**: `stage1._load_dev_quantized` + `run_jobs` Phase 2 apply the LoRA when `turbo`; threaded
  through `run()`/`--turbo`; rides the batch `_SHARED_KEYS`. Catalog adds a **dev-gated `turbo` flag**
  (inline, like the other dev knobs). Manifest `quantized.turbo` + `quant_stats.turbo_lora`. No forced
  step change — turbo just *arms* the LoRA; the user picks the (now-viable) low step count.
- **Tests** (`test_flux2_dev_quantized.py`): keymap unit (diffusers→BFL + qkv offsets), **full
  170-module coverage** vs the real LoRA + BFL model (gated on the file), forward-hook math (plain +
  qkv slices + strength), `--turbo` dev-gated argv + catalog shape. Full suite **330**. Monorepo→loom
  synced (3 files SAME).

⚠ **Owed (on-rig — can't run 34 GB+2.6 GB on 16 GB from CI):** (1) a dev image at 4–6 steps with turbo
looks good vs the under-denoised no-turbo 8-step, and a sweep is meaningfully faster (the **+2.6 GB**
bf16 LoRA adds paging — the step cut should still net a win, but that's the empirical question); (2)
confirm BFL qkv order is q,k,v (standard, but verify the slices land right); (3) tune the step count /
whether turbo wants lower guidance; (4) a proper 412 weight-gate + fetch for the LoRA file (worker
currently resolves it from cache, fails clearly if absent).

---

## M2.7 — warm-worker batch queue (spec §12 "M2.7"; 2026-06-27)

**Problem (user).** A batch (Cast/Expansion) is ONE queue job whose worker loops the cells; on pause
the runner `_discard_partial`s it and re-queues from scratch — every finished image disappears, and a
sweep is a single opaque queue entry. **Goal:** each image = an individual queue entry that persists
the moment it's done, serviced by a persistent warm worker (model loads once). Built in phases
(design: kb-loom-p2.md §12 "M2.7 solution design").

**Phase 1 — flux2 Expansion (DONE no-GPU; on-rig owed).** Built from the safe end inward so the proven
cold path is never touched (337 tests green throughout):

- **1b — flux2 `--serve` worker** (`run_pipeline.run_serve` + `_ServeGenerator`, `--serve` CLI). Reads
  one JSON job per stdin line, loads the model ONCE (`stage1.run`, flow+AE resident), writes one image
  into the job's own `output_dir`, emits a `[serve-result] <json>` line. `{"cmd":"shutdown"}`/EOF
  exits. **Purely additive** — the runner still dispatched normally, zero queue risk. (commit
  `2c3802c`.) ⚙ The TE is (re)loaded per cell — the dev 17 GB / Klein Qwen3 TE can't co-reside with
  the flow model on 16 GB; an encode-ahead buffer to recover that is a later phase.
- **1c — runner warm dispatch.** A resident `--serve` process lives on `self._warm_proc`/`_warm_group`
  across `_execute` calls. New `_execute_warm` (a SEPARATE path; cold `_execute` untouched): group
  match → feed the resident worker via stdin + read its `[serve-result]`; else `_spawn_warm`
  (evicting any other — one model on 16 GB). `_record_warm` marks the cell done/failed/canceled
  from its serve-result (output_name + per-cell `output_meta` with coverage_cell/seed/duration +
  lineage edge). Evict (`_evict_warm`: close stdin → frees VRAM) on **group change**, **end of group**
  (no more queued same-group cells), **idle/pause** (in `_run_loop`), and **shutdown**. Cancel kills
  the warm proc via `_procs[running_cell]`; a worker that EOFs without a result fails just that cell.
  `submit()` gained `warm_group`; the FE/queue render each cell-job naturally.
- **1d — Stage-B emits N cell-jobs.** The flux2 branch of `/assets/{id}/stage-b` now `submit()`s one
  `ref`-mode job per coverage cell (carrying `coverage_cell` + a shared `warm_group` =
  pipeline+model+size+hero+turbo/te/mm) instead of one `batch_items` job. zimage/sd35 stay on the
  batch path (Phase 2). ⚠ **post-passes (identity/clean/polish) are NOT chained onto warm cells in
  Phase 1** (per-cell chaining = Phase 2) — raw ref cells stream as individual persistent tiles.
- **Tests** (`test_warm_worker.py`): the serve protocol (round-trip / shutdown / bad-json + per-job
  failure isolation, via an injected fake generator) **and** the runner dispatch (reuse one worker
  across a group, group-change eviction, worker-dies-without-result → cell fails, via a fake serve
  proc — no GPU/subprocess). Stage-B test updated to assert N individual warm cell-jobs. Suite **337**.

⚠ **Owed (on-rig — can't run the model from CI):** a real klein Expansion sweep streams individual
tiles that **persist across a pause** and resume with the model loaded once. **Next:** Phase 2 (multi
Cast + sd35/zimage `--serve`) and post-pass chaining on warm cells; Phase 3 cross-batch warmth + the
dev encode-ahead buffer.

**Fix (2026-06-27, first on-rig warm sweep — dev OOM).** Klein Expansion streamed warm cells fine, but
a `flux.2-dev` warm cell crashed at **load** time:

```
stage1_load_models._load_dev_quantized -> scaled_fp8.load_comfy_flux2_transformer
  -> load_safetensors_into_model -> _assign_tensor(model, mapped, tensor.to(device))
torch.OutOfMemoryError: HIP out of memory. Tried to allocate 144.00 MiB ... 63.81 GiB allocated by PyTorch
```

Cause: `_ServeGenerator._load` hard-coded `cpu_offload=False`, so the **34 GB fp8 transformer loaded
straight onto the 16 GB GPU** while the Mistral TE was also resident — the exact co-residence the
cold `run()`/single-run path avoids by forcing `--cpu-offload`. The warm worker's whole premise
(everything resident across cells) is right for **klein** (fits, and the user confirmed it works) but
wrong for **dev**, which the journal already flagged as a co-residence problem.

Fix — make `_ServeGenerator` **offload-aware**, gated on the model:
- `_load`: `self.cpu_offload = job.cpu_offload or model_name == QUANTIZED_DEV_MODEL`. Under offload the
  flow model loads on **CPU**; refs are encoded with the GPU-resident AE (never the flow model), then
  the TE is **parked on CPU**. Both flow + TE stay in **CPU RAM** between cells, so the 34 GB disk
  read + fp8 dequant happens **once per sweep** — the warm win is preserved; only the PCIe shuttle is
  per-cell (the same cost the cold path already pays, ~87 s/img).
- `generate` (offload branch only): TE→GPU → encode text → TE→CPU + `empty_cache()` → flow→GPU
  (ROCm host-pages the 34 GB) → denoise → flow→CPU + `empty_cache()` → decode (AE stayed GPU). The
  flow + TE never co-reside on the GPU — this is the cold `run()` swap, applied per warm image.
- **Klein path unchanged**: `cpu_offload=False`, everything resident, the fast warm path.

`ComfyMistralEmbedder` is an `nn.Module` that reads its device dynamically (`forward` looks at
`embed_tokens.weight.device`), so `.cpu()`/`.to(device)` shuttling is safe. Both `run_pipeline.py`
copies (loom mirror + monorepo) updated byte-identical; 7/7 warm-worker tests green (the offload swap
is GPU-path code → **on-rig dev Expansion sweep still owed**, alongside the klein pause/resume sweep).

**Fix #2 (2026-06-27, first on-rig dev warm sweep — per-cell flow thrash).** The OOM was gone and the
dev sweep produced correct images, but each cell was **wildly slow and getting slower**. Hard numbers
from `loom/stubz001` (17-cell dev/ref sweep, 8 steps, 512², turbo): cold dev **t2i** single-run =
**~185 s** (load + denoise + decode); warm dev **ref** cell 0 = **676 s**; cell 1 ran **>22 min with
no image** — and cell 1 bears **no model load**. A no-load cell slower than the load-bearing cell 0 is
the tell: Fix #1's per-cell swap moved the **34 GB flow model CPU<->GPU on every cell**, and ROCm's
HMM managed memory accumulated/thrashed so each round-trip got worse than the last.

The cold **batch** path (`run_jobs` Phase-1) never had this — it loads the flow model to the GPU
**once** and denoises all cells with it resident (~87 s/cell; HMM pages only *during* denoise). Fix #2
makes the warm worker do the same:
- `_load` (offload): load flow on CPU, encode refs, free the TE, then **`flow.to(GPU)` ONCE** — the
  flow model is now resident for the whole group.
- `generate` (offload): only the **small TE** is shuttled GPU<->CPU for the text encode; the flow
  model is **never migrated per cell**. (The TE is fp8 → can't encode on CPU via `_scaled_mm`, so it
  rides onto the GPU briefly; HMM absorbs the transient flow+TE pressure for the seconds-long encode.)

Net: cell 0 ≈ load + one denoise (~the cold t2i ~185 s + ref encode); cells 1..N ≈ TE encode + denoise
+ decode (≈ cold-batch ~87–185 s/cell), with no per-cell 34 GB migration. 7/7 warm tests green; both
`run_pipeline.py` copies synced byte-identical. **The stuck in-flight sweep must be cancelled and
re-run on the fixed worker — the running process still has Fix #1's code.** On-rig confirmation that
cells 1..N hold steady (no per-cell creep) still owed.

**Fix #3 (2026-06-27, sweep seed + inspector pose hint — user, after the dev warm path ran clean).**
Two UX gaps the first good dev sweep surfaced:
- **Seed was incremental** (`base_seed + index` → 0,1,2,…), and an unset seed defaulted to a fixed 0.
  The user wants ONE seed for the whole sweep (random, or entered in the params section) so pose/angle
  /expression are the only per-cell variation (and the flux2 ref path keeps identity steady). Fix:
  `recipe.build_recipe` gains `shared_seed` (every cell gets `base_seed`); `stage_b` resolves the seed
  **once** — `req.base_seed` if given, else a fresh `random.randrange(2**31)` — and passes
  `shared_seed=True`. The UI already had the seed control (catalog `seed` → `base_seed`), so this is
  backend-only. Default `shared_seed=False` preserves the legacy per-cell draw for the recipe API.
- **No readable pose hint in the inspector.** The full per-cell prompt (incl. the pose) lives in a
  collapsed `<details>`; the only always-visible cell line showed raw frozen keys (`three_quarter_left`)
  in muted text. Fix: the inspector's coverage line now leads with `🎭 pose: <angle>` (un-muted,
  humanized — `three_quarter_left` → "three quarter left"), shown for queued AND completed cells from
  `job.coverage_cell` / `output_meta[…].coverage_cell`. (The `¾`/`°` in the dev JSON pose directive
  were a false alarm — stored correctly as `¾`; only my cp1252 console mangled them.)

Tests: `test_shared_seed_gives_every_cell_the_same_seed` (recipe) + a one-seed assertion on
`test_stage_b_flux2_cells_are_individual_warm_jobs`. 338 backend green; frontend `tsc --noEmit` clean.

### Phase 2a — sd35/zimage Expansion warm cells (2026-06-27; on-rig owed)

Extends the proven flux2 warm path to the img2img coverage sweep (user pick — the direct, lowest-risk
Phase 2 increment; `multi`/Cast needs a different mechanism, see §12 phasing). The runner side is
already pipeline-agnostic (`_execute_warm` gates on `hasattr(adapter, "serve_argv")`), so 2a is
worker + adapter + the Stage-B dispatch:

- **Workers** (`sd35`, `zimage` `run_pipeline.py`): a `--serve` loop mirroring flux2's (one JSON job
  per stdin line → one image + `[serve-result]`; `{"cmd":"shutdown"}`/EOF exits). The per-image
  generate+save body was **extracted from `run_jobs` into a shared `_generate_item`** so the
  `--jobs-file` batch loop and the `--serve` warm loop **can't drift** on the (subtle) img2img/inpaint
  path; `run_jobs` now just calls it. The serve `_ServeGenerator` loads the pipeline ONCE (the model
  is the load-bound part of the warm_group) and reuses it per cell. It honors the catalog's INVERTED
  flags directly (`no_cpu_offload`, sd35 `no_skip_layer_guidance`) since the warm spec doesn't pass
  through `build_batch_argv`'s inversion. **No offload dance** (unlike flux2-dev): these fit 16 GB and
  `enable_model_cpu_offload`'s per-call hooks persist across warm jobs.
- **Adapters** (`sd35.py`, `zimage.py`): `serve_argv` (file-path invocation + `--serve --device
  --output-dir`, NOT `-m`) + `SERVE_RESULT_PREFIX`.
- **Stage-B** (`main.py` else-branch): emits **N img2img cell-jobs per realization group**, each with
  a mode+strength-bound `warm_group` (so a group's cells share one resident worker; img2img and
  inpaint groups run back-to-back). **Gated**: `warm_cells = is_flux2 or (no post_passes and realize
  != "mixed" and serve-capable)`. Post-pass sweeps + `mixed` keep the **cold batch job** (post-passes
  chain there) until 2b — so identity/clean/polish are never silently dropped. The dry-run
  `planned_jobs` now reports the real job count (`len(cells)` for warm) — this also corrected flux2's
  Phase-1 dry-run, which still said `1`.
- **Vendor sync (R162)**: `_generate_item`/serve landed in all copies byte-identical — sd35 ×2
  (monorepo + multistack), **zimage ×3** (monorepo + `pipelines/zimage` + multistack). The drift
  guard (`test_vendored_workers_match_monorepo_source`) is green.
- **Tests**: `test_sd35_zimage_serve_argv` (adapter argv), `test_stage_b_zimage_cells_are_individual_
  warm_jobs` (N cell-jobs + shared warm_group), `test_stage_b_with_post_passes_keeps_the_cold_batch_
  job` (the gate), updated dry-run/flux2 shape assertions. The serve **protocol loop** is byte-
  identical to flux2's (already round-trip-tested); the heavy workers can't be imported in-process
  (bare-import name collision between sd35/zimage `stage1_load_pipeline`), so it's covered by the
  adapter argv + the pipeline-agnostic runner dispatch (`test_warm_worker`) + the byte-identical sync.
  **340 backend green.**

⚠ **Owed (on-rig — can't run the model from CI):** a real sd35/zimage Expansion sweep streams
individual img2img tiles that **persist across a pause**, model loaded once. **Next:** 2b (post-passes
on warm cells → drop the cold-batch fallback), then 2c (multi/Cast individual jobs).

### Phase 2a follow-ups (2026-06-27, user on-rig) — klein offload + keep-warm-across-pause

Two issues from the user's sd35 + flux2-klein on-rig run (sd35 ✓; klein showed the dev symptom):

- **klein warm cells slowed each image** (2nd > 1st) — the **same per-cell HMM thrash** dev had.
  Phase 1's "klein fits resident, stays resident" was **wrong**: klein-4b (~8 GB flow) + Qwen3 TE
  (~8 GB) = 16 GB with **no room for the latents**, so keeping both resident thrashes ROCm HMM and
  worsens per cell (the single-run klein path forces `--cpu-offload` for exactly this reason). Fix:
  **every** flux2 model offloads in warm mode — `_ServeGenerator.cpu_offload = bool(job.get(
  "cpu_offload", True))` (was dev-only). Flow loads on GPU **once** (resident for the group), the TE
  is shuttled GPU↔CPU per cell; nothing is migrated per cell. Synced to both run_pipeline.py copies.

- **"serialize the up-front encoding so a pause doesn't redo it?"** — the honest answer: the warm
  worker doesn't pre-encode all jobs up front (that's the Phase 3 encode-ahead buffer); what it does
  ONCE is **load the model** (the real cost — and it can't be usefully serialized: it's weights→VRAM,
  and the weights are already the HF cache) + encode the hero ref (cheap, seconds). The reload was
  happening because the idle-evict freed the worker **on pause**. Better fix than serializing the
  cheap part: **keep the worker warm across a brief pause.** `_run_loop` now evicts only for HARD
  reasons (no project / shutdown / disk hard-stop / sweep drained or group-changed); a pause WITH
  queued same-group cells keeps the model resident for `WARM_IDLE_GRACE_S` (default **180 s**,
  `LOOM_WARM_IDLE_GRACE_S`) — a quick pause→inspect→resume reuses the loaded model (no reload, no ref
  re-encode), and a longer pause still frees the GPU. Decision factored into
  `_warm_evict_reason_locked` ('free' / 'drained' / 'grace' / None) + unit-tested
  (`test_warm_kept_across_a_brief_pause_then_evicted_after_grace`). 341 backend green.

⚠ Owed on-rig: confirm klein warm cells now hold steady (flat per-cell), and that a brief pause→resume
skips the reload. The dev **encode-ahead buffer** (pre-encode all prompts + persist them, killing the
per-cell TE shuttle and surviving a full eviction) remains the Phase 3 option if the per-cell shuttle
proves to matter.

**On the "serialize the encoding to survive an app restart" ask (user clarified: restart, not a brief
pause).** The keep-warm grace only covers an in-session pause — across a process restart the worker
dies and the model reloads regardless. But there's little to serialize: the **done tiles already
persist** (each warm cell is its own queue entry), so only the remaining cells re-run; the **model
load** (the real cost) can't be serialized faster than the HF cache already is (weights→VRAM); and the
hero-ref **encode is cheap** (seconds). Conclusion: not worth persisting the encoding for the restart
case. The Phase 3 encode-ahead buffer (pre-encode all prompts + persist) is the only thing that would
save real work across a restart, and even then the model reload dominates — deferred unless it bites.

### Phase 2b — post-passes on warm cells (2026-06-27; on-rig owed)

Lifts the Phase 1/2a deferral: warm Expansion cells can now run post-passes (identity/clean/polish),
so a sweep with them no longer falls back to the cold batch.

- **`runner._record_warm`**: after a warm cell is marked done + lineage, it chains its post-passes via
  the **same `_submit_chained`** the cold `_finalize` uses — each cell is a 1-output parent, so the
  chained pass is a 1-item job over THAT cell's image (carrying its `coverage_cell` so curation
  survives). Best-effort (a chain failure leaves the cell done).
- **`main.py` Stage-B**: both warm branches (flux2 + sd35/zimage) now pass `post_passes=post_passes`
  to each cell submit, and the `not post_passes` gate is gone — `warm_cells = is_flux2 or (realize !=
  "mixed" and serve-capable)`. `mixed` is the lone remaining cold-batch case.
- **No warm-worker thrash**: the pass jobs are created as cells finish (later `created_at`), so FIFO
  runs ALL the sweep's warm cells first (the resident worker is never evicted mid-sweep for a pass),
  then the passes. Each pass tile is its own pause-safe job — consistent with the "everything
  individual" goal. (Trade-off: diffusion passes (clean/polish) load their backend per cell; identity
  — the common Stage-B pass, inswapper onnx/CPU — is cheap. Warming the pass backends is a later
  optimization if clean/polish-heavy sweeps need it.)
- **Tests**: `test_warm_cell_chains_its_post_passes_on_completion` (a warm cell chains a clean pass
  over its output, coverage_cell carried), `test_stage_b_post_passes_now_ride_warm_cells` (post-passes
  → warm, not cold). `mixed`-stays-cold is still covered by the birefnet two-batch-jobs test. 342 green.

⚠ Owed on-rig: a real Stage-B sweep with identity (anchor) confirms each cell streams + gets an
identity-locked pass tile, all pause-safe. **Next:** 2c (multi/Cast individual jobs).

### Phase 2c — multi (Cast) individual queue jobs (2026-06-27; on-rig owed) — Phase 2 COMPLETE

The last batch-generation surface. A Cast no longer submits one opaque `multi` job that fans out
*inside* a subprocess; the **orchestrator** fans it out into `num_candidates × |lineup|` INDIVIDUAL
t2i candidate jobs (one per pipeline × candidate seed). This is sound because the casting **ideate**
stage was already *independent t2i per pipeline+seed* (verified in `arch_compose_character`), and
clean/polish already chain as post-passes — so bypassing the `multi` worker for casting loses nothing
in use.

- **`model_catalog`**: `IDEATION_LINEUP` + `ideation_lineup(preset)` — the (pipeline, model) trio per
  preset (fast: klein-4b/sd3.5-large-turbo/zimage-turbo; refined: klein-9b/sd3.5-large/zimage-base),
  mirroring the vendored worker's `IDEATION_PRESETS` (R162 — can't import the worker; a validity test
  guards that every entry is a real catalog variant).
- **`main.py` `/generate`**: the `is_multi` branch now loops the lineup × `num_candidates`, submitting
  a `t2i` job per candidate with `model_name` + a per-candidate `seed` (`base+c`, the SAME seed across
  the 3 pipelines, matching the worker). Each candidate carries the sweep's `post_passes` (so clean/
  polish/identity chain per candidate, Phase 2b) and a **per-(pipeline,model) `warm_group`** — so the
  3 pipelines run back-to-back with each model resident across ITS candidates (Cast gets warmth, not
  just pause-persistence). The dry-run previews the first candidate's real t2i argv + the `lineup`.
- **No warm thrash, by construction**: a cast submits all of pipeline-A's candidates, then B's, then
  C's (contiguous), and FIFO + the same-group keep-warm logic services each pipeline's candidates on
  one resident worker before evicting for the next.
- **Frontend**: unchanged. The grid is derived per-job (a job's outputs → tiles) and the inspector
  falls back to `job.pipeline`, so N 1-output candidate jobs render as N tiles with the right pipeline
  label — no single-`multi`-job assumption anywhere. `tsc --noEmit` clean.
- **Tests**: `test_ideation_lineup_models_are_valid`, `test_cast_fans_out_into_individual_warm_t2i_
  candidates` (6 jobs for 2×3, 3 warm groups × 2, seed shared across pipelines); updated the two
  done-line/dry-run assertions that pinned the old `multi`/`ideate` argv. **344 backend green;
  frontend tsc clean.**

⚠ Owed on-rig: a real Cast streams its candidates as individual tiles that persist across a
pause/cancel, with each pipeline's model loaded once. **Phase 2 (warm-worker batch queue) COMPLETE** —
flux2 + sd35/zimage Expansion warm cells (2a), post-passes on warm cells (2b), Cast fan-out (2c); the
remaining cold-batch case is `realize="mixed"` Expansion (a later phase if wanted).

**Fix (2026-06-28, user on-rig — zimage-base 17-min "inference").** A refined-preset Cast's
`zimage-base` candidate (`job_0d82f0d2`) sat `running` for **17 min**. NOT the flux2-dev thrash and
NOT slow compute: the manifest showed `generate` = **1026.87 s** while the visible **denoise was ~16 s**
(50 steps; first step 15 s was a one-time AOTriton attention compile). So ~1011 s was OUTSIDE the
denoise — `enable_model_cpu_offload` shuffling the big zimage-base components (text-encoder /
transformer / VAE) CPU↔GPU on 16 GB ROCm. The transformer denoised fast *resident*, so the GPU compute
is fine; offload was pure overhead. (Not a regression: the old `multi` worker's `invoke_zimage`
defaulted `cpu_offload=True` too, so the pre-2c refined cast was equally slow — fast/turbo casts
avoided it.) Fix: the zimage **warm** `_ServeGenerator._load` now defaults **resident**
(`cpu_offload = bool(job.get("cpu_offload", False)) and not job.get("no_cpu_offload", False)`) — zimage
fits 16 GB at the casting/expansion sizes (~11 GB est). Offload is still honored if a job explicitly
asks. Synced to all 3 zimage copies; warm/batch tests green (the offload default is GPU-path, not
unit-tested). ⚠ On-rig: confirm zimage-base resident is fast (≈30 s) and doesn't OOM at 1024²; if it
OOMs we gate it back to offload.

**Probe + root cause + the missing single/batch fix (2026-06-28, user on-rig).** The warm fix above
only covered the `--serve` path. The user then ran a **single** `zimage-base` t2i (`job_b4ae9136`,
non-warm) that was *still* ~15 min, so I added a `[zimage-probe]` (`callback_on_step_end` splitting the
diffusers call into encode+setup / denoise / decode+post; commit `4bbb968`). It printed the smoking
gun: **`encode+setup=11.5s denoise=1.0s decode+post=894.1s total=906.7s`** at 1024²/50 steps, with
`[stage1] Pipeline loaded … (cpu_offload)`. So the cost is **entirely the VAE decode under
`enable_model_cpu_offload`** — the transformer denoises fast on-GPU during offload (the hook keeps it
resident for the loop), so *only the final VAE decode* pays the offload tax and lands on a ~15-min
path; resident keeps the VAE on the GPU where decode is seconds. The single + batch paths were never
told to go resident: `emit_argv` and `_batch.build_batch_shared` only act on params **present** in the
request, and the catalog `no_cpu_offload` **default is never injected** (`emit_argv` skips absent
params; `build_batch_shared` only inverts if the flag is truthy) — so single ran `cpu_offload = not
args.no_cpu_offload = True` and batch ran `shared.get("cpu_offload", True)`. **Fix:** the **zimage
adapter** `build_argv` now `p.setdefault("no_cpu_offload", True)` — the single seam both single + batch
flow through — so all three dispatch routes (warm/serve, single, batch) default resident and agree. A
request may still force offload with an explicit `no_cpu_offload=False` (OOM escape hatch). **Loom-only
orchestrator change** (no vendored/drift-guarded worker touched → no monorepo sync needed, per the
author's "loom is the only repo that matters"). +3 tests (single emits `--no-cpu-offload`; batch
`shared.cpu_offload=False` with no explicit flag; explicit `False` still offloads). **348 orchestrator
tests green.** ⚠ Still owed on-rig: confirm the resident decode drops `decode+post` from 894 s to
seconds and doesn't OOM at 1024². **✅ PUSHED `6e637fe`.**

**RESOLVED — it was never offload OR placement; it's MIOpen on Windows ROCm (2026-06-28).** The
resident fix above did NOT help: a resident `zimage-base` single (`job_39303cd7`) still showed
`[zimage-probe] encode+setup=2.6s denoise=1.0s decode+post=888.8s` at 1024². The user rightly
challenged the story (zimage-**turbo** is fast — yet it shares a **byte-identical** VAE, so the VAE
decode can't be the differentiator). Web research (see `.github/copilot/kb-zimage.md` new chapter
"VAE Decode Catastrophically Slow on Windows ROCm") found the documented root cause: on native
Windows PyTorch-ROCm, MIOpen falls back to the **naive direct-conv solver** (`ConvDirectNaiveConvFwd`,
`workspace=0`) for the **convolution-heavy FLUX VAE decoder** under the default `MIOPEN_FIND_MODE` —
while the conv-free transformer (matmul/attention via rocBLAS/AOTriton) is unaffected (ROCm/TheRock
#3077; ROCm #4742 "VAE decode slow… 9070 XT"; ROCm #4729; ComfyUI #10460). **Fix (on-rig
confirmed):** `MIOPEN_FIND_MODE=2` (FAST) → VAE-only probe decode **888 s → 1.9 s cold / 0.4 s warm**
(≈470×; CPU fallback 13.9 s, GPU+tiling 0.3 s). Wired in `orchestrator/main.py` startup:
`os.environ.setdefault("MIOPEN_FIND_MODE", "2")` BEFORE any worker spawns (the spawns build
`{**os.environ, …}` and stage_runner copies `os.environ`, so the whole subprocess tree inherits it;
real env wins; MIOpen-only var = harmless no-op off ROCm). **Fixes every conv worker** (sd35/zimage
VAE + postproc), not just zimage. 348 orchestrator tests green. The earlier resident default
(`6e637fe`) stays — it's still correct (avoids the offloaded-VAE path + thrash) and now the decode is
fast on top of it. **✅ PUSHED `7bb4961`.**

---

## P2-era fixes (non-milestone)

### Vendoring-completeness audit + guard (2026-06-28, user-asked)

**User:** "I've checked the local pipeline code in `loom/loom-loreweave-studio/pipelines/` — there's
only krea2, multistack and zimage. Are we not still running pipelines from code in `src/pipeline`?
Make sure all code that runs in loom is copied in `loom/loom-loreweave-studio/`."

**Finding — all present, nothing escapes to the monorepo.** The three top-level entries are the whole
set: `krea2` + `zimage` are flat (file-path-invoked standalone), and **everything else is bundled
inside `multistack/src/pipeline/`** — flux2, sd35, multi, ltxv, and all four postproc passes
(birefnet, identity, face_restore, frame_harvest), plus the BFL flux2 lib at `multistack/flux2/src`.
Simulated the real resolver (`config._resolve_pipeline_roots`) with the monorepo `src/pipeline/`
**fallback removed**: all 10 inference adapters **and** the zimage trainer (`trainers/loom_zimage_lora.py`)
still resolve in-repo. `multi` self-locates (`stage_runner` `REPO_ROOT`/`FLUX2_LIB_SRC`) entirely
inside `multistack/`. The only monorepo references left in the orchestrator are **model weights**
(`village_ai/models`, R160 — one shared ~330 GB set, intentionally never vendored). So `src/pipeline/`
is a pure dev convenience that is **never reached at runtime** because every pipeline is vendored.

**Side finding — 3 vendored files are intentionally *ahead* of the stale monorepo copy** (whole-tree
md5 scan: 75 sync, 0 missing, **3 drift**), all loom-only features edited directly in the loom repo and
never back-ported: `krea2/stage1_load_pipeline.py` (`40e5265` "Add Krea2 Turbo" — turbo-only @768²),
`flux2/stage3_denoise.py` + `flux2/util.py` (`7579cbd` "Route flux2 dev through quantized Comfy
backend" — non-finite-latent black-image guard + dev→8-step default). Direction is loom-newer, so
nothing loom runs is stale; the monorepo R162 mirror is the stale side. **No code changed** — left for
the author to decide (back-port to monorepo to restore the single-source invariant, or accept
loom-authoritative). The recent warm-worker edits (flux2/sd35/zimage `run_pipeline.py`,
`zimage/stage2_generate.py`, `stage1_load_pipeline.py`) are all **in sync**.

**Guard added** (`test_batch_worker.py::test_every_adapter_resolves_without_the_monorepo_fallback`):
drops the monorepo fallback root and asserts every adapter (+ trainer) still resolves a worker under
the app repo — so a future pipeline/adapter added without vendoring its worker **fails CI** instead of
only working on a checkout that happens to sit beside the monorepo. Presence-only by design (the R162
byte-match guard is separate; the 3 loom-ahead files must not trip a completeness check). 27/27
`test_batch_worker.py` green. **✅ PUSHED `a0c40f0`.**

### Krea2 Turbo vendored as a Loom T2I generator (2026-06-25, user-requested)

Author direction: keep **Krea 2 Turbo** as the practical target after the local smoke produced a
768² image in under 5 minutes, and do not chase Raw/full-fp16 fit. The prior `src/pipeline/krea2/`
spike followed `.github/copilot/kb-krea2.md`'s Proposed Pipeline layout; this pass vendors the Turbo
path into Loom.

- **Vendored worker:** copied the Krea2 source pipeline into `pipelines/krea2/` (R162 vendoring) and
  narrowed the Loom vendored model registry to **`krea2-turbo` only**. Defaults are **768×768 / 8
  steps / guidance_scale 0.0 / CPU offload** for the 16 GB ROCm target. Raw remains intentionally
  absent from Loom.
- **Adapter + backend wiring:** added `orchestrator/adapters/krea2.py` (file-path invocation, same
  manifest-as-truth parse shape as zimage/sd35), registered it in the runner adapter table,
  `/capabilities`, and `/generate`, with a **16 GB VRAM estimate**. `model_catalog.CATALOG_VERSION`
  bumped to 2 and now serves `krea2-turbo` with advanced optional Quanto knobs
  (`quant_backend`, `quant_dtype`, `quant_skip_modules`) left **unset by default** after the fp8 color
  smoke showed artifacts.
- **Mode contract:** local Diffusers exposes **`Krea2Pipeline` only** (`diffusers/pipelines/__init__`
  imports `["Krea2Pipeline"]`), not a `Krea2Img2ImgPipeline`; Loom therefore advertises **T2I only**
  (`modes=["t2i"]`). Stage-B/hero expansion stays on zimage/sd35/flux2.
- **Frontend:** Stage-A/Sandbox pipeline selector now includes **`krea2 turbo`** and the existing
  catalog-driven parameter drawer exposes Krea2's tunables. No Stage-B or postprocess backend option
  was added because those are image-conditioned paths.
- **Tests / verification:** Krea worker `--help` imports clean and shows `--model-name {krea2-turbo}`;
  focused no-GPU tests `pytest -p no:cacheprovider orchestrator/tests/test_krea2_adapter.py
  orchestrator/tests/test_model_catalog.py` = **28 passed**; `npm run build` clean. ⚠ The existing
  `.pytest_cache` directory under the Loom repo has an ACL that denies traversal/removal; pytest was
  rerun with the cache provider disabled so it did not affect verification.

**✅ PUSHED `40e5265`** (Krea2 Turbo vendoring + adapter/catalog/UI/tests + this journal entry);
follow-up journal-only commit records the hash after Git assigned it.

### flux.2-dev Stage-B size + silent batch failure (2026-06-22, user-found in `loom/stubz001`)

Two issues from a real `npc_lite` flux2-dev ref-mode Stage-B run. 295 backend tests; build clean.

- **The 512² default didn't reach Stage-B (✅ PUSHED `b56a6fd`).** M0e Part A wired the model-aware
  size default into `/generate` only; Stage-B has its own endpoint and `StageBRequest.width/height`
  default to **1024**, so a `flux.2-dev` sweep ran at 1024² (~4k latent tokens — the slow regime). The
  `stage_b` endpoint now resolves the per-cell size model-awarely (mirrors `/generate`): UNSET →
  `model_size_default` (dev → 512²); non-dev keeps 1024²; explicit dims (top-level or params channel)
  win. The drawer's model-aware placeholder is now honest for Stage-B too. +1 `test_flux2_adapter` test.
- **The batch worker failed silently (✅ PUSHED `4fb76bc`).** A pre-loop bail (text-encode / model-load /
  reference-encode) printed only `[batch-done] 0 ok / 0 failed / 17 skipped` and wrote the exception
  ONLY into the batch manifest (pruned with the job). New `_fail_preload()` in the flux2 batch
  `run_jobs` prints `[batch-error] <reason>: <exc>` + a full traceback to stderr (→ job log + console)
  and folds the reason into the manifest error. Vendored byte-identical (R162; md5 match). ⚠ The root
  cause of the user's dev-batch skip-all is still unknown (the manifest/log were pruned) — the next run
  will now surface it. Guidance: for a many-cell coverage sweep use the **Balanced** (klein-base) preset
  (fits 16 GB); reserve **Dev/JSON** for a few t2i hero shots → then upscale (dev's 60 GB flow model
  paged across 17 cells × 50 steps is impractical on the 16 GB rig).

### Styles add-to-top · individual image deletion · remove-from-curation (2026-06-21, user-found)

Three UX bugs/inconveniences the author reported after M0e. Fixed as **Pass 1** of two (the bigger
**Visual-Styles tiles + enlarge + per-style preview-generation** redesign is **Pass 2 — DEFERRED**,
author's call 2026-06-21: quick fixes first; the persistent per-style **sample image** model is agreed).
286 backend tests (284→+2); `tsc` + `vite build` clean. **✅ PUSHED `ede3903`** (code only — this
journal entry rides the next journal commit; the parallel LoRA/trainer workstream is not in it).

- **(A) New visual style lands at the TOP** (`bible.add_style` `append`→`insert(0, …)`). The author
  wanted a fresh style front-and-centre to edit immediately, not buried at the bottom; the add does NOT
  change the active default. +1 test (`test_styles.test_new_style_lands_at_the_top`).
- **(B) Image deletion is now strictly INDIVIDUAL.** Root cause: the grid 🗑 called `deleteJob(c.job.id)`
  → `RUNNER.delete` → `shutil.rmtree(out/<job>)`, so deleting any tile of a **multi-cast pool** or a
  **Stage-B batch** physically nuked the *whole* batch. New `RUNNER.delete_output(job_id, output)` prunes
  ONE image (file + `.json` sidecar) and its `result.output_names`/`output_meta`/`partial_outputs`,
  persisting; it falls through to a whole-job `delete()` only when that was the last/only output. New
  endpoint `DELETE /jobs/{id}/output?output=<rel>` (409 unknown/not-terminal, 404 not-an-output) + client
  `deleteOutput`. FE: the old `onDelete(id)` became `onDeleteCell(job, output, key)` — a multi-output tile
  deletes just that image ("the rest of the batch is kept"), a single-output tile deletes the whole gen.
  +1 test (`test_queue_feedback.test_delete_single_output_keeps_the_rest_then_whole_job`).
- **(C) Curated images are now removable in the Curation screen.** A kept image is a **durable copy** in
  `refs/` (survives source-job deletion by design), so after deleting its source generation it lingered in
  Stage C as a `durableRefCells` tile whose only removal control was the ✓ toggle (read as "keep", not
  discoverable as un-keep). Added an explicit **🗑 "remove from the curated set"** on every kept tile in
  Stage C (durable refs *and* kept job tiles) → culls via `remove_ref` (drops from `ref_set` + deletes the
  `refs/` copy); the whole-job 🗑 is suppressed on kept curated tiles so a curated tile's 🗑 means
  remove-from-curation, never delete-the-source-job. The ✓ toggle stays. FE-only (cull endpoint unchanged).

### Visual Styles redesign — tiles + expand-to-edit + per-style preview generation (Pass 2 of 2, 2026-06-21)

The deferred half of the styles work (Pass 1 = add-to-top above). Replaces the stacked full-editor list
with a **tile grid + an expand-to-edit detail panel that can generate a persistent sample image** per
style. 292 backend tests (286→+3 + parallel-workstream tests); `tsc` + `vite build` clean. **✅ PUSHED
`d002b6a`** (code only — this journal entry rides the next journal commit; the parallel LoRA/trainer
workstream is not in it). ⚠ Visual sign-off owed on the running app.

- **Persistent per-style sample (backend, like a face anchor).** `StyleEntry` gains an optional `sample`
  {file, source_output, job_id, prompt, model, set_at}; `story.schema.json` documents it. `bible.py`:
  `set_style_sample` copies an out/-relative output into **`<project>/bible/styles/<style_id>.<ext>`**
  (durable — survives source-job deletion + out/ pruning), `clear_style_sample`, `style_sample_path`;
  `remove_style` now also deletes the copy. Endpoints `POST /bible/styles/{id}/sample {output,prompt,model}`,
  `DELETE …/sample`, `GET …/sample/file` (served unauth, mutations token-gated). +3 tests
  (set/serve/clear + survives source delete; unknown-style/bad-output/traversal 404; delete-style removes copy).
- **Tile grid + detail (frontend).** Visual Styles is now a `StyleTile` grid (title + sample thumbnail or 🎨
  placeholder; ★ = the project default; click to select/expand). The selected tile opens **`StyleDetail`** —
  the name + **Style prompt** + **Negative prompt** editors (the old StyleRow controls) **plus a PREVIEW
  section**. Adding a style auto-selects it (it lands at the top, Pass 1) so the author edits immediately.
- **Per-style preview generation.** The preview section = a prompt input + a **sd3.5-medium / zimage-turbo**
  model selector + **✨ generate sample**. Generate fires a **project-scoped t2i with THIS style applied**
  (`generate({pipeline, mode:"t2i", model_name, prompt, apply_style:true, style_id})` — sd35 for medium,
  zimage for turbo) on the shared GPU queue; a self-contained poll (`getJob` every 1.5 s) pins the output as
  the style's sample on completion (`setStyleSample`). The preview image shows **🔍 magnify** (lightbox via a
  new `onView` prop → the App `viewer`) + **🗑 delete** (clear sample); while generating it shows queued/
  running status + **✕ cancel** (`cancelJob`). New client fns: `setStyleSample`/`clearStyleSample`/
  `styleSampleUrl` (cache-busted by `sample.set_at`) + `getJob`. CSS: `.style-tiles`/`.style-tile`/
  `.style-thumb`/`.style-detail`/`.style-preview*`. No `src/pipeline/` touched → no re-vendor.
  - **Refinement (2026-06-21, user-found, ✅ PUSHED `9ff7366`):** tiles **enlarged on click** — `.style-tiles`
    used `minmax(116px, 1fr)`, so opening the detail panel toggled the `.world` scrollbar → stole width →
    `auto-fill` dropped a column → the remaining `1fr` columns each grew. Fix: **fixed 108px tile columns**
    (no `1fr`) so they stay small + never resize, + `scrollbar-gutter: stable` on `.world` so the scrollbar
    can't reflow the grid. CSS-only.

### Unlock a finalized version + delete a character (2026-06-21, user-found in `loom/stubz001`)

Two more author-reported gaps. 294 backend tests (292→+2); `tsc` + `vite build` clean. **✅ PUSHED
`00dfa33`** (code only — this journal entry rides the next journal commit; the parallel LoRA/trainer
workstream is not in it).

- **Curation "can't be cleaned up" → UNFINALIZE/unlock.** Root cause: the project's `char01` active
  version was **`finalized: True`** (17 refs, 34 rejected), and the Stage-C grid gates every curation
  control on `curable = … && !locked` — a finalized version shows NO keep/cull/remove. Finalize was a
  one-way door (`finalize_version`, no inverse). Added `assets.unfinalize_version` (sets `finalized=False`,
  re-stamps `saved_at`, idempotent) + `POST /assets/{id}/versions/{vid}/unfinalize` + client
  `unfinalizeVersion`; the version-bar's static "🔒 finalized" badge became an **"🔒 finalized — unlock 🔓"**
  button (`onUnfinalizeVersion`, confirm-guarded). The finalize lock is a *declaration of intent*, not a
  hard guarantee — this is the explicit author escape hatch (vs. deep-duplicate-to-a-new-version).
  +1 test (`test_unfinalize_unlocks_a_finalized_version`).
- **Delete a character (L2 asset).** No delete-asset capability existed (only spine-character delete, a
  different thing). Added `assets.delete_asset` (rmtree the profile dir `assets/<class>/<slug>/` + all
  versions — refs/casting/faces/anchors; out/ generations + lineage left, project-level + rebuildable) +
  `DELETE /assets/{id}` (404 unknown) + client `deleteAsset`. FE: a hover-revealed **🗑** on each character
  row in the L2 rail (`.asset-row-wrap`/`.asset-del`), confirm-guarded `onDeleteAsset` → falls back to the
  Sandbox if the deleted character was active + refreshes the list. +1 test
  (`test_delete_asset_removes_profile_and_all_versions`). No `src/pipeline/` touched.

### flux2-klein-9b crashed encoding a non-ASCII stdout char (Windows cp1252) — 2026-06-18 18:56

**User:** running **flux2-klein-9b** died right after stage-2 text-encode with
`UnicodeEncodeError: 'charmap' codec can't encode character '→'` — the worker's offload log
line `"[offload] Moving text encoder → CPU, flow model → GPU"`.

**Cause (orchestrator `runner.py`, affects ALL pipelines):** the worker spawn used
`Popen(..., text=True)` with **no explicit encoding**, so on Windows the worker ENCODES its stdout
as the console default **cp1252**, which can't represent `→` (or `★`, `≤`, …). Any worker that prints
such a char crashes mid-run — flux2's two-phase offload line just happens to hit it on the klein-9b
path. (The earlier `≤`→`<=` argparse-help fix only patched one string; this is the general cause.)

**Fix:** at the spawn point — **`PYTHONIOENCODING=utf-8`** in the worker env (worker encodes stdout
UTF-8 → represents all Unicode) **+ `encoding="utf-8", errors="replace"`** on the `Popen` (runner
decodes the pipe as UTF-8, and a stray byte never fells the read loop). Both sides now agree; fixes
**every** pipeline worker at once. **Orchestrator-only — no `src/pipeline/` worker code touched (no
re-vendor).** 252 backend tests green. ⚠ A *manual* `python -m pipeline.flux2.run_pipeline` run
outside the orchestrator would still need `PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8` in the shell. **✅
PUSHED `4a9961e`.**

### flux2/multi: ideate marked all candidates failed — cp1252 stderr decode (regression of the above) — 2026-06-19

**User:** a multi **ideate** job marked **all 6 candidates failed** even though every sub-run saved
its image + printed `[done] Pipeline completed`. Persisted errors:
`'charmap' codec can't decode byte 0x8f … <undefined>` (a `UnicodeDecodeError`).

**Cause (regression of `4a9961e`):** that fix sets `PYTHONIOENCODING=utf-8` on workers, which the
multi worker's **sub-runs inherit** (`stage_runner._build_env_for` = `os.environ.copy()`), so they
now emit **UTF-8 stderr**. But `stage_runner._run_subprocess` captured that stderr with
`subprocess.run(text=True)` and **no encoding** → cp1252 on Windows → raised on a UTF-8 byte `0x8f`.
The exception propagated out of `invoke_*`, so `candidates.py` recorded every candidate **failed**
(rc/output were actually fine; the image existed). The orchestrator↔worker pipe was already
UTF-8-safe; the **worker↔sub-run** pipe wasn't.

**Fix:** decode the captured stderr as `encoding="utf-8", errors="replace"` (matches the sub-runs'
output + the orchestrator's own decode). `_run_subprocess` is the **only** subprocess capture in the
multi pipeline → covers all candidates + clean/polish stages. **Monorepo-first (R162), re-vendored
byte-identical** (drift test green). 257 backend tests. **✅ PUSHED `dec372b`.** ⚠ Rig re-run owed to
confirm a real ideate now reports ok candidates.

### identity: undetectable (stylized) anchor failed the whole expansion — 2026-06-19

**User:** char02 Stage-B expansion hard-failed: `[batch-error] no face (det >= 0.5) found in the
ANCHOR`. Inspected the anchor (512² in `loom/stubz001`): a **clear face**, but a stylized cyberpunk
character (chrome/neon/3-quarter/dramatic light). Probed insightface directly — **SCRFD detects ZERO
faces even at det 0.2** (it's a photoreal detector). Hard-failing the anchor killed the entire
17-cell identity pass. ⭐ Limitation: inswapper/SCRFD can't anchor a heavily-stylized character
(PuLID-class identity is the real answer, deferred to P5 Track B).

**Fix (worker monorepo-first + re-vendored; observer/UI follow):**
- Detector floor lowered to `_ANCHOR_DET_FLOOR=0.2` + a **lenient anchor retry** (catches borderline
  0.2–0.5 stylized faces; per-TARGET detection still uses `min_det`, so no false swaps).
- **Graceful degradation:** when the anchor face is still undetectable, **pass every image through
  unchanged** (identity SKIPPED, clear warning, `meta.identity="anchor_no_face_passthrough"`) instead
  of failing — the expansion dataset stays complete. (The 17 img2img cells already existed in the
  parent job; only the identity overlay is skipped.)
- **Verification guard:** a passthrough run locks nothing, so it must NOT verify the anchor (else
  default-on identity arms a permanent no-op). The observer + the stage-b history scan now require
  ≥1 output with `identity=="locked"` (shared `_identity_job_locked`); the frontend `anchorVerified`
  matches. +1 test (passthrough doesn't verify). **258 backend tests**, build clean. **✅ PUSHED
  `21f9b8e`.** ⚠ For a stylized character, identity-lock will passthrough (no-op) — use a more
  photoreal anchor, or wait for P5 PuLID-class identity.

---

## Carried-forward P1 UI fixes (during P2) — the "UI-rewire pass"

Bugs found in the P1 UI after P1 closed; fixed here since the journal is closed. (Not P2 milestones.)

### Stale Stage-C ref tiles leak onto a new/imported character — 2026-06-14

**User:** in `loom/stubz001/`, working on char001 in **Stage C (Curation)** then clicking
**+Character** → char002 showed char001's ref tiles as broken-image placeholders; they cleared only
after clicking through states/characters.

**Cause (frontend-only, App.tsx):** `onCreateAsset` (and `onImportFile`) did a **partial** per-asset
reset — set the new `activeAsset` + cleared `casting`/`selected`, but **never called
`refreshCasting`** and **never reset `stage`**. So the previous asset's `refSet` (+ `rejected`,
`promptTemplate`, `anchorInfo`, `versionList`) stayed in state and `stage` stayed `"C"`. Stage C
synthesizes `durableRefCells` from `refSet`, each rendered via `refUrl(activeAsset.id,
refItem.file)` — i.e. char001's filename under **char002's** id → 404 → placeholder. The exact
"leak class the asset switch had" the code already names (App.tsx ~L651); `onSelectAsset` /
`_switchToVersion` were fixed earlier, but the **create + import** paths were missed.

**Fix:** route both create and import through the existing `onSelectAsset(newAsset)`, which does the
full reset (stage→A, selection/bulk/filters/Stage-B controls cleared, `refreshCasting` rescopes
casting/refSet/rejected/anchor/versions to the new id). Resetting `stage`→A alone already stops
`durableRefCells` from rendering; `refreshCasting` then clears the stale set. Removed the now-unused
thin `onSelectAssetReset` helper. **Frontend-only**, no backend/`src/pipeline/` touched; `tsc` +
`vite build` clean (243 backend tests unaffected). **✅ PUSHED `a507c1f`.**

### Single-pipeline cast ignored the advertised size default (1024² → 1280×720) — 2026-06-14

**User:** in `loom/stubz001/`, A-Cast with **sd35** at the drawer's default **1024×1024** produced
**1280×720** images. The displayed default must match what's generated (per-model defaults are fine,
as long as they're marked accurately).

**Cause (backend, `main.py` `/generate`):** `GenerateRequest.width/height` default to **1280×720**
(the P0/Wan project default). The **multi** branch already strips that when unset so the catalog's
native default applies (M6 review #2, `1c0ac06`), but the **single-pipeline `else` branch never
did** — so an sd35/zimage cast that didn't explicitly set dims kept 1280×720 even though the drawer
advertises the per-pipeline catalog default (sd35/zimage **1024²**, flux2 **1360×768**). The drawer
was always accurate (it renders `param_default(pipeline,·)` as the field placeholder, per-pipeline,
App.tsx ~L1452/L2674); only the backend ignored it. Note this is display-vs-reality, *not* a UI
default change — the user explicitly accepts different per-model defaults.

**Fix:** in the single-pipeline branch, mirror the multi fix — when `width`/`height` are unset
(neither in `req.model_fields_set` nor the params channel), set them to
`model_catalog.param_default(req.pipeline, dim)`. Explicit values (top-level or params channel)
still win. **Backend-only**, no worker/`src/pipeline/` touched. +1 regression test
(`test_single_pipeline_unset_size_uses_catalog_default_not_project_default`: sd35/zimage→1024²,
flux2→1360×768; explicit top-level + params-channel dims still win). **244 backend tests** (243→+1),
green. **✅ PUSHED `8d8dd95`.**

---

## zimage-base "15 min/image" — root-caused to the denoise floor; 768² default + torch.compile opt-in — 2026-06-29 22:15 CEDT

Not a P2 trainer milestone — a performance/workflow fix on the casting side, run during P2.

**The chase.** A `zimage-base` t2i at 1024² took many minutes on the 16 GB ROCm rig (gfx1201 / RX
9070 XT, torch 2.9.1+rocm7.2.1). I spent hours blaming the **VAE decode** (shipped `MIOPEN_FIND_MODE=2`,
GPU-freed decode, full-frame, a `--cpu-vae` path) — all wrong. **The user caught it:** *"I don't think
it's the VAE… I think it is the denoising, the 8.23 it/s is misleading."* Correct.

**Root cause = the transformer denoise, and it's a hardware floor.** HIP kernels enqueue
**asynchronously**; tqdm bars and `callback_on_step_end` fire on host-enqueue, not GPU-compute. The
probe timed phases **without `torch.cuda.synchronize()`**, so ~700 s of denoise compute drained at the
next forced sync (inside `vae.decode`) and was misread as "decode = 888 s". One `synchronize()` per
timestamp fixed the measurement. Synced truth @1024²: **base encode+denoise = 705 s, decode = 2.5 s**;
turbo 38 s / 2.2 s. The VAE is innocent. The base↔turbo gap is purely denoise (50 steps × CFG ≈ 100
forwards vs turbo's 9, no CFG).

**Every kernel lever is exhausted** (on-rig sweeps, `scratchpad/zimage_denoise_sweep.py`):
- Not GTT paging — peak alloc ~12.6 GB, ~15.5 GB free at every resolution.
- **AOTriton SDPA (the diffusers default) is the FASTEST attention backend** @1024²: default **10.3
  s/step**; `_native_efficient` 11.9, `_native_flash` 14.5, `native` 14.9, `_native_math` 43.4 (21 GB),
  `flex` 78.8 (24.7 GB). No swap helps.
- **`aiter` is DEAD on Windows** — the pip pkg (`0.13.*`) is a pure-python **stub**: no compiled
  kernels, no `flash_attn_func`; diffusers falls back to native. (User installed it; confirmed hollow.)
- **`torch.compile` ≈ 10% only** (`compile_repeated_blocks`: 10.3 → 9.1 s/step), capped because
  ZImage's 3D RoPE uses **complex arithmetic TorchInductor can't codegen** (graph-breaks to eager).
- ~10 s/step ≈ **27 % MFU** for a 6B CFG forward — a typical Windows-ROCm ceiling, not a config bug.

So the real win is **workflow**: spend fewer tokens (resolution) / fewer steps. 768² ≈ 3× faster/step
than 1024² (~165 s vs ~500 s for 50 steps); 512² ≈ 7×.

**KB corrected.** `.github/copilot/kb-zimage.md`'s chapter previously claimed *"the VAE decode is the
bottleneck"* — rewrote it ("Z-Image-Base Slow at 1024² — the Transformer Denoise Floor") with the
synced measurements, the async-timing lesson, the backend sweep, aiter-dead, compile-marginal, and the
resolution table. Demoted `MIOPEN_FIND_MODE=2` from "the fix" to "kept (helps the conv workers + cold
MIOpen find), but it did NOT fix zimage-base." Also fixed the now-contradicted "use native_flash"
advice earlier in the file (AOTriton-default is fastest; leave it unset). *(Monorepo doc — per the
user, loom is authoritative, but this is their working zimage KB so the correction belongs there.)*

**Wire #1 — zimage-BASE defaults to 768²** (the user's call). Added `width/height: 768` to the
`zimage-base` variant `defaults` in `model_catalog.py` — the same `model_size_default()` mechanism
M0e gave `flux.2-dev` (512²). An UNSET base cast now resolves to 768² (`/generate` main.py:942 +
drawer placeholder main.py:1593 both read it); explicit dims still win; **turbo keeps 1024²**.
Extended `test_model_size_default_dev_is_512_others_none` to assert base→(768,768).

**Wire #2 — optional `torch.compile`** (ROCm-gated opt-in). New catalog `compile` flag → adapter
`WIRED_PARAMS` → worker `--compile`. `stage1_load_pipeline._compile_transformer()` runs AFTER offload
(they coexist), prefers `compile_repeated_blocks(fullgraph=False)`, sets a **persistent
`TORCHINDUCTOR_CACHE_DIR`** (derived from `HF_HOME`'s parent → on F:) so the ~60 s compile amortises
across worker processes, and degrades to eager if inductor/triton is missing (never raises). Threaded
through `run()` (single), `run_jobs` (batch, added to `_BATCH_SHARED_ONLY` — load-bound), and the
`--serve` warm path; provenance lands in the load-stage manifest. Best for **fixed-size batches**
(recompiles per shape).

**Vendoring + tests.** Both loom zimage copies (flat + `multistack/`) edited identically (synced via
copy, verified byte-identical); the `run_pipeline.py` drift-guard target in the monorepo
(`src/pipeline/zimage/`) re-vendored so `test_vendored_workers_match_monorepo_source` stays green
(stage1 is not guarded but synced too, so the monorepo worker stays runnable). **351 orchestrator
tests green**; all edited files `py_compile`-clean; `emit_argv`/size-default round-trip verified.
**✅ PUSHED `11fa6d8`** (loom main, `1c8e1f5..11fa6d8`). *(Monorepo working tree carries the matching
KB-correction + re-vendored worker, left uncommitted per "ignore the monorepo".)*

**Known wrinkle (not changed):** the `--serve` `_load` comment (`run_pipeline.py` ~L506) still cites
"denoise was ~16 s" as the reason zimage runs resident in the warm path — that figure is an
async-blind-era misreading. The resident-default decision may still be right (offload adds real
shuffle overhead), but the rationale's number is stale; revisit if the warm-path cost is ever
re-profiled.
