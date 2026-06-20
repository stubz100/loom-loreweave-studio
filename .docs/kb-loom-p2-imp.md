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
`tsc` + `vite build` clean. No `src/pipeline/` → no re-vendor. **✅ PUSHED `<pending>`.**

---

## P2-era fixes (non-milestone)

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
