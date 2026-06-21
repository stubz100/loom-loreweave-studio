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
50/4.5) attached to `CATALOG["flux2"]["sampling_presets"]` so `GET /models` serves it; `flux2_sampling_presets()`
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
1e-4 / LoRA weight 1.0**. Commit/push + Graphify pre-push refresh pending below.

---

## P2-era fixes (non-milestone)

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
