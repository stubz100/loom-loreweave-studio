# Loreweave Studio — P1 implementation journal (`kb-loom-p1-imp`)

started: 2026-06-05 19:00:36
finished: 2026-06-14 21:33:40

Running log of what was **actually built** for Phase 1 (Bible + Assets + Casting — the MVP creative
layer), milestone by milestone. Brief points + any parameters/settings worth remembering later.
Spec: [`kb-loom-p1.md`](kb-loom-p1.md); decisions: [`kb-storyboard01.md`](kb-storyboard01.md) §10.0;
predecessor spine: [`kb-loom-p0.md`](kb-loom-p0.md) (P0 done & accepted — `kb-loom-p0-imp.md`).

Convention: each milestone records **start + finish** timestamps (elapsed time, like a normal
journal); ⚙ marks a setting/param later code depends on; ⚠ marks a known gap / deferred item.
**Push at milestone close** (added 2026-06-11 — M3.5 closed without one, user caught it): every
milestone/acceptance close ends with a commit+push of the loom repo, recorded in the journal.

P1 build order (spec §13): **M1** library+profile+style scaffold (zimage-only) → **M2** Stage-A
casting (`multi`) → **M3** Stage-B+C → **MVP done-line** → **M3.5** background-diversity
realization (added 2026-06-11, pulled forward from M6) → M4 anchor → M5 versioning → M6 postproc →
M7 video-sketch → M8 full L1 World → M9 export/import → M10 acceptance. (No M0 in P1; milestones are
numbered M1–M10.)

---

## ✅ P1 CLOSED — functionally complete (user sign-off 2026-06-14 21:33) — journal closed

**User:** *"P1 is completed — it still has some elements I would rewire on the UI, but functionally
it is set."* P1 is declared **functionally complete**; this journal is **closed** and succeeded by
[`kb-loom-p2-imp.md`](kb-loom-p2-imp.md) (LoRA training).

**What shipped (final state at close, HEAD `44cd411`):**
- **M1–M10 built**, the §1 MVP done-line (style → cast → hero → expand → curate → save → reopen)
  locked as an executable no-GPU test (`test_acceptance.py`), plus two post-M10 capability adds:
  **P1-11 Flux2 multi-ref** wired as a third Stage-B expansion family (identity-preserving `ref`
  mode) **and** as a first-class t2i caster, and **P1-12 curation throughput**. The L1 style became
  a **selectable collection** of named snippets.
- **243 backend tests green** (`python -m pytest orchestrator/tests -q`), `tsc` + `vite build` clean.
- Per-milestone detail + push hashes are in the entries below (newest-first).

**Carried forward — NOT done, deliberately (the user owns these):**
- 🎨 **UI rewire pass** — the user flagged "elements I would rewire on the UI." Functional, not
  blocking; collect into a dedicated UI-polish pass (no journal milestone yet — capture when scoped).
- 🟡 **Formal M10 rig acceptance (A–H)** — the §1 done-line + chained passes (clean/polish/identity/
  restore) + mixed/inpaint + video-sketch + curation + export/import round-trip on the **RX 9070 XT**.
  This is the *formal* "P1 ACCEPTED" gate and is the **user's to run on their hardware**; the user has
  declared P1 **functionally** set, so the build moves to P2 in parallel with that pass.
- ⚙ **Rig E2E owed** (subset of the above): a real **flux2 Stage-B sweep** on the queue (VRAM under
  the two-phase offload; identity/pose quality across a full recipe) and a **flux2 t2i cast** on GPU.
  First-cut flux2 follow-ups still open: flux2 cells use the img2img recipe (no background terms yet);
  klein-9b would need cpu_offload; clean/polish post-passes over flux2 outputs not yet exposed
  (no `POST_PARAMS` in the flux2 catalog).

---

## P1-11 (wired) — Flux2 multi-reference = Stage-B identity-preserving expansion (§11/R147)

started: 2026-06-13 17:45
finished: 2026-06-13 18:12

**Goal (user, post-M10):** the spike was GO → pull flux2 multi-ref in as a **Stage-B expansion
method** to fix the variation problem (img2img at str 0.55 only changed expression — couldn't
rotate/reframe). flux2 conditions on the hero ★ as an in-context REFERENCE, so identity carries
into genuinely new poses/scenes/angles.

**Worker (monorepo-first, R162; vendored byte-identical):**
- `flux2/run_pipeline.py`: new **`run_jobs(--jobs-file)`** batch worker — the loom Stage-B shape
  (jobs.json `{shared, items}`, per-item `Image:` lines, STOP file, `flux2_batch_<ts>.json`
  jobs_batch summary). ⚙ **two-phase offload** (16 GB): encode ALL cell prompts → free the 8 GB
  Qwen3 encoder → load the 8 GB klein flow model + AE → encode the SHARED hero reference ONCE →
  loop cells (`denoise(img_cond_seq=ref_tokens)`); the encoder + flow model never co-reside. New
  `--ref-image` (repeatable) + `mode="ref"` on the single-run path too (`stage3_denoise.run`
  gained `ae=`/`ref_images=`). ⚙ **self-bootstraps `<repo>/flux2/src`** onto sys.path (the worker
  uses package-relative imports + is **MODULE-invoked** `-m pipeline.flux2.run_pipeline`, like
  `multi` — not run by bare path; multi's stage_runner used to set this via PYTHONPATH).
- ⭐ The BFL `flux2` lib needed **zero changes** — `sampling.encode_image_refs` +
  `denoise(img_cond_seq=…)` already existed (the spike's finding). We only wired the loom worker.

**Orchestrator:**
- `adapters/flux2.py` (new): module-invoked, `capabilities` carries `multi_ref:{max_refs:4,
  via:encode_image_refs}` + `ref` worker mode; batch argv writes the SHARED `ref_images` (encoded
  once) + per-cell items; `parse_result` = batch-summary-as-truth. Registered in runner `ADAPTERS`
  + `VRAM_ESTIMATES["flux2"]=13`.
- `model_catalog`: flux2 gains the `ref` mode; `ref_images` rides the jobs-file shared block (not
  a per-cell catalog flag).
- `/assets/{id}/stage-b`: `pipeline` now `zimage|sd35|flux2`. flux2 → ONE **`ref` group** (hero as
  `ref_images`, no init_image/strength); `realize="mixed"` + flux2 → **422** (flux2 changes the
  scene via the prompt, not an inpaint mask); global negative **skipped** for flux2 (distilled, no
  CFG); the **inswapper identity pass is NOT auto-armed** (the reference IS the identity mechanism;
  `identity=true` still adds it). dry-run previews the real `-m … --ref-image` argv.

**UI:** Stage-B pipeline selector gains **"flux2 — identity ✨"**; selecting it hides strength +
realize (no img2img/mixed axis) and shows a "✨ reference-conditioned (identity-preserving)" note;
forces realize=img2img so the request never trips the mixed-guard.

**Tests: 231 backend** (225 → +6, `test_flux2_adapter.py`): adapter present + caps (multi_ref,
ref mode, runner-registered); batch argv writes shared ref_images + items + module-invokes;
single-run emits `--ref-image`; **`-m pipeline.flux2.run_pipeline --help` imports clean** (guards
the whole graph incl. the self-bootstrapped lib path); Stage-B flux2 dry-run → `ref` split +
identity off; flux2+mixed → 422. `tsc`+`vite` clean.

⚠ **Rig E2E owed (user):** a real flux2 Stage-B sweep on the queue — VRAM under the two-phase
offload, identity/pose quality across a full recipe, curation of the ref-conditioned cells.
First-cut scope (follow-ups noted): flux2 cells use the img2img recipe (no background terms yet —
pose/angle/expression coverage); klein-9b would need cpu_offload; clean/polish post-passes over
flux2 outputs not yet exposed (flux2 has no POST_PARAMS in its catalog). **✅ PUSHED `e404522`.**

**GPU batch smoke (2026-06-13, `F:\_tmp\spike_m6\batch_out`):** ran the real `run_jobs` path via
`-m pipeline.flux2.run_pipeline --jobs-file` — 2 cells, **status completed, 2/2 ok**, ~19 s after
load, summary manifest + per-cell PNG/sidecar with `coverage_cell` meta. Both cells carried the
hero's identity (red braids + green cloak) into NEW poses/scenes — a tavern waist-up 3q-left and a
**full-body profile on a night castle rampart** (the full-body profile img2img can't reach).

### Flux2 wiring — review fixes (2026-06-13)

Five findings (1 High, 2 Medium, 2 Low) — all addressed (234 tests, +3; build clean):

- **High — cancel/tree-kill robustness (GPU safety before P2):** the win32 `_kill_tree` ran
  `taskkill /T /F` but **ignored its return code/output** and the fallback only `proc.kill()`d the
  direct child (no re-wait) — a canceled multi/flux2 job could leave a grandchild holding the GPU.
  ⚠ The dedicated test PASSES in our env (taskkill /T fells the tree here), so the red wasn't
  reproducible — but the code gaps are real. Fix: **per-job Windows Job Object** — each worker is
  assigned to a fresh KILL_ON_JOB_CLOSE job at spawn (nested under the process-wide `_KILL_JOB`);
  **cancel = `TerminateJobObject`** which reaps the worker + ALL descendants ATOMICALLY (no
  tree-walk, independent of taskkill). Handle closed at finalization. `taskkill /T` kept as the
  fallback but now **rc-checked + retried once + logged**, and the proc is **re-`wait()`ed** after
  an escalated kill. New helpers `_create_kill_job/_assign_to_job/_terminate_job/_close_job`;
  existing orchestrator-death reaping untouched. +test `test_cancel_job_object_fells_tree`
  (descendant spawned AFTER the worker joins the job, as in the real flow → TerminateJobObject
  takes it). (The genuine fix for the reviewer's host: TerminateJobObject doesn't rely on taskkill.)
- **Medium — flux2 ref provenance:** submitted cell `meta.method` recorded the recipe's PREFERRED
  `c["method"]` (img2img/inpaint) even for flux2, so curated `ref_set.method` could mislabel a
  flux2 ref as img2img — P2 is metadata-driven. Now the `ref` group records `method="ref"`
  (zimage/sd35 unchanged). +test.
- **Medium — flux2 identity UI lied:** the ⚓ checkbox rendered CHECKED by default when a verified
  anchor existed, but flux2 defaults the inswapper pass OFF and the request omits identity unless
  toggled — so the UI claimed a lock the backend skipped. For flux2 the checkbox now defaults
  **visually OFF**, is relabeled **"⚓ identity (extra)"**, and the tooltip says the reference
  already carries identity (tick to ALSO run inswapper).
- **Low — `/capabilities` omitted flux2** (registered in the runner, caps exposed) → added the
  import + entry. +test `test_capabilities_includes_flux2`.
- **Low — stale comments:** `StageBRequest` docstring ("one img2img job per cell / Flux2-spike
  later") + `model_catalog` "flux2 via multi only" refreshed to the wired-flux2 reality.

No `src/pipeline/` worker code touched → no re-vendor. **✅ PUSHED `b0895d4`.**

### flux2 t2i casting + full-res image viewer (2026-06-13, user requests)

Two asks after the flux2 wiring:

- **"Why can't I select flux2 as a t2i in Casting?"** — because it was wired only for the
  Stage-B `ref` mode (casting is `multi`'s job). Made flux2 a **first-class t2i generator**:
  `GenerateRequest.pipeline` += `flux2`, flux2 adapter `WIRED_MODES` += `t2i`, UI casting
  selector gains **"flux2 🔒"** (gated klein weights). ⚙ The flux2 adapter's single-run
  build_argv now **forces `--cpu-offload`** — a single flux2 run loads the 8 GB klein flow model
  AND the 8 GB Qwen3 encoder, which don't co-fit 16 GB without the CPU↔GPU swap (the batch `ref`
  sweep does its own two-phase offload in `run_jobs`, so this only affects single t2i / the ref
  dry-run preview). Casting fires `count` single jobs (same as zimage/sd35; each reloads the
  model — fine for low-count exploration). +2 tests (t2i dry-run module-invokes with
  `--cpu-offload`; single-argv adds it exactly once).
- **Full-resolution image viewer.** Added a **🔍 (bottom-right) tile button** on every tile that
  has an image (cast / expansion / curation) → a **lightbox modal** (`viewer` state) showing the
  PNG at full res (`max 96vw/94vh`, object-fit contain); click backdrop / ✕ / **Esc** to close.
  Frontend-only (App.tsx `GridCell.onView` + the modal + `.view`/`.viewer-*` CSS).

**Tests: 236 backend** (234 → +2). `tsc`+`vite` clean. No `src/pipeline/` touched. **✅ PUSHED
`cb21885`.**

### L1 styles → a COLLECTION (multiple named styles, selectable per generation) (2026-06-13, user)

User: the storyboard defined Visual Style as a *collection* of snippets, not one fragment —
implement multiple styles + make the L1 style selectable at each generation. (Future-phase
plans only cover style *LoRA* — P2 declares the target, P5 Track B makes it usable, R122/R147 —
not a prompt-snippet collection, so this is net-new but aligned with the storyboard's `style_id`.)

**Data model (`bible.py` + `story.schema.json`):** canonical store is now `styles[]`
(`{id, name, fragment, global_negative}`) + `active_style_id` + a story-level on/off gate
`style_enabled_default`. ⚙ The legacy single `style` object is KEPT as a **mirror of the ACTIVE
style** (synced on every save) so the schema's `required: style` + all M8 readers/tests keep
working, and a pre-existing project **migrates transparently on load** (`_normalize`). ⚙ The
seeded default's style id is **fixed `sty_000000`** (not random) — an UNPERSISTED default story
reads back the same id each call, else GET-then-DELETE/set-active would 404 on a regenerated id
(caught by a test, would've hit the UI too). CRUD: `add_style/update_style/remove_style`
(refuses the last) `/set_active_style`; `resolve_l1(ws, apply, style_id)` picks the requested
style (→ active fallback, lenient).

**API:** `GET /bible/styles` (collection) + `POST` (add) + `PUT /bible/styles/{id}` (edit) +
`DELETE` + `POST /bible/styles/active`. `PUT /bible/style` now edits a specific style via
`style_id` (active by default). **`style_id` added to GenerateRequest + StageBRequest +
SketchRequest** and threaded through `resolve_l1` at all 3 generation surfaces.

**UI:** the L2 **style-bar** gains a **style `<select>`** (★ marks the default) — picks WHICH
style applies to this generation (Cast/Expand/Sandbox; the bar's fragment editor + Save now
target the selected style). **L1·World** "Visual style" → a **collection manager**: per-style
name + fragment + global-negative editors, **set-default**, delete (refuses the last), **+ add
style** (new `StyleRow` component). `style_id` rides every cast/stage-b request.

**Tests: 242 backend** (236 → +6, `test_styles.py`): default collection; add/edit/delete/
set-active; can't-delete-last; unknown→404 / empty-name→400; **per-gen `style_id` selects the
fragment + global negative** (active fallback on unknown). test_l1_world (the M8 single-style
behavior, via the mirror) still green. `tsc`+`vite` clean. No `src/pipeline/` touched. **✅
PUSHED `6205498`.**

#### L1 styles — review fixes (2026-06-13)

Three findings — all addressed (243 tests, +1; build clean):
- **Medium — `set_style` was lenient on an unknown `style_id`** (`_find_style(...) or _active(...)`),
  so a stale client could silently overwrite the active default. Now **STRICT**: an unknown id
  RAISES → the handler 404s. Generation's `resolve_l1` stays lenient (a bad id never errors a
  render). +test (404 + active untouched + gen still renders).
- **Medium/Low — TS request types missed `style_id`** (it only rode through spread objects). Added
  `style_id?` to `GenerateRequest`, `StageBRequest`, and the `sketchHero` body type (+`apply_style`).
- **Low — stale copy:** the `stage_b` HANDLER docstring still said "one img2img job per cell"
  (→ "one batch job per realization group; flux2 via reference-conditioning"); the apply-style
  tooltip said "prepend" (→ "appended after the character prompt").

**✅ PUSHED `44cd411`.**

---

## P1-11 — Flux2 multi-reference spike (§11, R24/R147) — ✅ **GO**

started: 2026-06-13 17:30
finished: 2026-06-13 17:36

**Question (§11):** can the LOCAL flux2 worker condition generation on a reference (hero) image
so the character is carried into a NEW scene/pose — the "insert this character anywhere" path
img2img can't do? **Verdict: GO** (empirically validated on the RX 9070 XT).

**Finding 1 — the native pathway already EXISTS in the vendored lib (the §11 "not yet wired
locally" premise was stale).** `flux2/src/flux2/sampling.py` has `encode_image_refs(ae, imgs)`
(AE-encodes a list of refs → reference tokens with distinct t-offset position ids, ≤4 Klein/≤6
dev) and `denoise(..., img_cond_seq, img_cond_seq_ids)` / `denoise_cfg(...)` already accept the
ref tokens, concat them into the sequence, run `model.forward`, and slice the prediction back to
the generated tokens; there's even a `denoise_cached` + `model.forward_kv_extract/forward_kv_cached`
KV path. `kb-flux2.md` (research 2026-04-25) documents this and pre-specced the `--ref-image` flag.
The functions were just **never called** by the loom worker (`run_pipeline.py` does t2i + img2img
only). Ref conditioning is **encoder-agnostic** — it rides the AE/latent path, so it works on the
Qwen3-encoder Klein variants, not only the Mistral-VLM `dev`.

**Finding 2 — it RUNS and WORKS on the rig (empirical probe `F:\_tmp\spike_m6\`):** probe =
hero t2i → new-scene prompt rendered twice at the SAME seed, once NO-ref (control) and once with
the hero as a reference (`encode_image_refs` → `denoise(img_cond_seq=…)`), klein-4b / 4 steps /
768². Result: **ran clean, ~245 s for all 3 passes** (4-step denoise <1 s each; the AE decode is
the cost — ~60-80 s, first-decode ROCm warmup); hero → **2304 ref tokens**; **control-vs-withref
mean pixel diff = 14.6%** (ref materially consumed, not ignored). **Visual:** control = a generic
brown-haired warrior in steel plate; **with-ref = the SAME snowy-forest full-body scene but with
the hero's red braids + green cloak + freckles carried in.** Identity transferred into a new
pose/framing — exactly what img2img at strength 0.55 could not do (the M10 expansion complaint).
Artifacts: `out/{hero,scene_control,scene_withref}.png` + `result.json`.

**So this is the identity-preserving Stage-B expansion method** (R147: a "go" spike *becomes
Stage-B's preferred method*). Capability flag (if wired): flux2 adapter `capabilities()` gains
`multi_ref: {max_refs: 4 (klein) / 6 (dev), via: "encode_image_refs"}` + a `ref` worker mode.

**Wiring plan (NOT built yet — scope decision for the user; ~½–1 day):**
1. **Monorepo-first (R162):** `src/pipeline/flux2/stage3_denoise.py` gains a `ref_images=[…]`
   param → `encode_image_refs(ae, refs)` → `denoise(img_cond_seq=…)`; `run_pipeline.py` adds
   `--ref-image` (repeatable, cap 4 klein/6 dev) + manifest `ref_image_paths`; vendor byte-identical.
2. **Batch mode:** flux2 has no `--jobs-file` worker yet (only zimage/sd35) — Stage-B fires batch
   jobs, so either add flux2 batch support (load once, loop cells) or run per-cell (slower). This
   is the real lift.
3. **Orchestrator:** add `flux2` to the Stage-B pipeline set + a standalone flux2 Stage-B adapter
   (today flux2 is only a `multi` casting member) with a `ref` mode; the **hero rides as the
   reference** (Stage-B already resolves the hero). model_catalog flux2 variants already exist;
   weight gate already covers klein (multi preset). VRAM proven (klein-4b + Qwen3-4B fit 16 GB).
4. **Decision (R147 + R167):** GO ⇒ it *should* become Stage-B's identity-preserving method.
   Pull into P1 now, or take it as the **first P5 Track B** item — user's call (P1 is at M10
   acceptance; this is net-new Stage-B work). Either way the spike's blocking question is closed:
   **viable.** PuLID-class diffusion-coupled identity stays deferred (M4 spike: wrong backbones) —
   flux2 multi-ref is the better-fit answer and it's now proven.

---

## M10 — MVP / P1 acceptance

started: 2026-06-13 14:00
finished: 2026-06-13 14:15

**Goal (spec §1 + §13 M10):** prove the **P1 done-line** — *style → cast → pick hero → expand
→ curate → **saved, reopenable AssetProfile** (version v1, Saved-not-Finalized)* — and **record
the contract gaps from the new adapters**. M10 is a **verification** milestone: the executable /
no-GPU layer lives here; the GPU pixel-quality sign-off is the user's rig pass (checklist below).

### Deliverable 1 — the done-line as an executable test (`test_acceptance.py`, +2 tests, 223 total)

`test_done_line_end_to_end` walks the WHOLE §1 arc through the **HTTP API** the way a real
session does (GPU generation simulated by injecting `done` job results into the paused runner —
the established no-GPU stand-in; pixels are the rig's job, the **data-model contract** is locked
here so the done-line can't silently regress):
1. **STYLE** `PUT /bible/style` (fragment + default-on).
2. **create** the AssetProfile (v1 active).
3. **CAST** (Stage A) → `POST …/casting/star` picks the hero ★ from a candidate pool (asserts
   exactly one starred hero).
4. **EXPAND** (Stage B) — `POST …/stage-b dry_run` proves the hero resolves + the per-cell prompt
   weaves **clause + the L1 style fragment** (R104 append: asserts both "ranger" and "watercolor"
   land in `first_cell.prompt`); then a realized 3-cell coverage dataset is injected.
5. **CURATE** (Stage C) — keep 2 / reject 1 via `…/refs/keep` + `…/refs/reject`.
6. **SAVE** `…/save` → `finalized:false` (R119 Saved-not-Finalized).
7. **REOPEN** — switch to a fresh project (unbinds A, asserts B is empty), re-`POST /project/open`
   the original, then `GET /assets/{id}` asserts the **whole done-line survived the disk
   round-trip**: name + class, **single version v1** = active, `prompt_template`, `finalized:false`,
   **ref_set == 2 each with a coverage_cell** (the P1→P2 corpus), `rejected == [the culled name]`,
   one starred hero — and `GET /bible/style` still carries the fragment + default-on.
`test_save_is_reopenable_without_curation` locks the *minimum* done-line (bare style→save with no
refs reopens as a valid editable v1 — not lost, not finalized).

### Deliverable 2 — adapter contract-gaps record (the new P1 adapters)

Logged for per-pipeline onboarding / P2+ (extends the P0/M8 worker-contract gap list). **Common
to every P1 adapter** (carried from P0): `cancellable:True` = **whole-subprocess tree-kill**, not
graceful mid-step checkpoint; **`vram_estimate_gb: None` from every worker** → admission uses the
runner-side static `VRAM_ESTIMATES`, and **no `peak_vram_gb` is ever reported back** (a worker
telemetry gap for P2 budgeting); progress is **coarse stage-markers** for the diffusion/matting
workers (multi/sd35/ltxv/birefnet), **per-item** for the batch CPU postproc (identity/face_restore/
frame_harvest). Per adapter:
- **multi** — `ideate`-only; the in-worker `batch` subcommand (clean/polish) is **unused** (loom
  runs clean/polish as orchestrator post-passes, PM-4). Pipeline mix = preset (fast|refined, both
  the full flux2+sd35+zimage lineup); **per-cast subset + per-member model/steps NOT exposable**
  without a monorepo `multi` CLI extension (R105 mix-ticker deferred, PM-3).
- **sd35 / zimage** — `_img2img` shipped as a **shared lib** inside both workers (img2img + inpaint
  modes), not a standalone adapter (PM-4); the only **post-pass backends are zimage|sd35** —
  flux2-img2img as a pass needs the §11 spike.
- **birefnet** — matte/cutout/**bgmask** roles in `output_meta`; bgmask provenance is enforced
  orchestrator-side (role=="bgmask" of a done job for *this* version), not by the worker.
- **identity (inswapper-128)** — CPU onnxruntime (no GPU residency); **research / non-commercial
  license**; **no per-output identity strength** (diffusion-coupled identity = PuLID/InstantID →
  P5 Track B); the detector/embedder pack (buffalo_l) is a **filesystem-probe** gate, not an HF
  single-file; `meta.anchor_cos` is a free P2-readiness signal.
- **face_restore (GFPGAN 1.4 ONNX)** — **BLIND** restoration (subtle/no-op on already-sharp faces —
  user-observed); CPU; `portrait` mode = aligned 512² crop of the largest face; upscale / SAM2 /
  HandRefiner deferred.
- **ltxv** — **i2v only (no t2v)**; T5-XXL forces `offload=model` on 16 GB (static VRAM est 12);
  coarse stage markers; harvested via the chained frame_harvest pass.
- **frame_harvest** — OpenCV CPU, **no model at all**; one manifest item **per frame**; stills
  inherit the sketch's **TARGET** coverage cell (aimed, not classified — the frozen P1→P2 contract).

### Deliverable 3 — RIG acceptance checklist (the user's GPU sign-off; ⚠ OWED)

The single consolidated GPU pass that closes P1 (folds in every owed E2E check accrued since M3.5):
- **A. Done-line proper (§1):** style → cast (`multi` 3-pipeline + `zimage`) → star hero → Stage-B
  recipe sweep → keep/cull → Save → **quit & relaunch** → profile intact + versioned.
- **B. Chained passes:** clean → polish over a real sweep (tiles stream; cells stay curatable);
  ⏹-stop mid-chain leaves pre-pass outputs (no chained pass); ✕ tree-kill frees VRAM (PM-10).
- **C. M3.5 mixed:** matte hero → `realize="mixed"` sweep (bgmask dilate-12, inpaint @0.95,
  curate the background-diverse cells).
- **D. M4 identity:** verified anchor → identity-locked Stage-B close-ups (`anchor_cos` in meta,
  back-view passthrough); tight-portrait anchor recommended.
- **E. M6/M6.1:** identity→restore on close-ups (blend sanity); `…/anchor/derive` face-portrait.
- **F. M7 video:** the **first ltxv job on the queue** — VRAM under `offload=model` (unmeasured!),
  motion-prompt cell-reach, frame-harvest quality.
- **G. P1-12 curation:** keyboard k/x/space + bulk keep/reject sweep (~100→~30).
- **H. M9 round-trip:** UI **⤓ Export** a saved profile → **⤒ Import** it back → rename + casting/
  ref/anchor images survive the trip.

**Status:** no-GPU acceptance layer ✅ (223 backend tests, build clean). **P1 is NOT yet declared
ACCEPTED** — that is the user's call after the rig pass (A–H) on the RX 9070 XT. No `src/pipeline/`
touched → no re-vendor. **✅ PUSHED `13c2c61`** (2026-06-13).

### M10 review — fixes (2026-06-13)

Four findings (1 Medium, 1 Medium/Low, 2 Low) — all addressed:

- **Medium — M10 locked persistence but not the actual P1 adapter path** (§11.1/R121: the done-line
  MINIMUM is `multi` casting + `_img2img`/`sd35` Stage-B; the persistence test stands in zimage, so
  a regression in the multi/sd35 *wiring* could still pass M10): added
  `test_done_line_uses_the_p1_adapter_path` (no-GPU dry-runs) — asset-scoped **`multi` cast**
  (`profile_version_id`==version, argv carries `ideate` + `multi`), **`sd35` Stage-B img2img**
  (split has img2img, first_argv built by the sd35 worker), and **`realize="mixed"` routing**
  (split has BOTH img2img + inpaint groups — the M3.5 two-batch-job split).
- **Medium/Low — import buffered an oversized body when Content-Length was absent/lying:** the
  pre-read cap only fired with a present header; otherwise the body was buffered THEN checked.
  Replaced `await request.body()` with a **streamed read under a running byte cap** (`request.stream()`
  + abort the instant the running total exceeds `MAX_BUNDLE_BYTES`) → memory is bounded regardless
  of the header (chunked / lying client). +test: a generator (chunked, no Content-Length) body
  over the cap → 413.
- **Low — `/version.token_required` stale:** added `GET /assets/{id}/export` (token-gated since the
  M9 review) to the advertised list.
- **Low — journal M9 export-auth text contradictory:** struck through the original "unauthenticated"
  export sentence + the "unauth GET" UI note with **[SUPERSEDED]** pointers to the M9 review (which
  token-gated it).

**Tests: 225 passed** (223 → +2). Build clean. No `src/pipeline/` touched → no re-vendor.
**✅ PUSHED `82ee0f0`** (2026-06-13).

### M10 rig pass — curation UI fixes (2026-06-13, user findings)

Two findings while curating on the rig — both **UI-only** (the backend already supported the
behavior; `test_identity_anchor.py` locks the `allow_unlocked` escape):

- **Every output is curatable, not just the end-of-chain.** Pressing `k` on a pre-pass image
  (a Stage-B parent whose chained clean/polish/identity/restore passes are pending/un-run) hit
  the M4 terminal-output guard → `409`. ⭐ **User decision (amends the M4-review High):** keeping
  is a deliberate human action, so **all outputs are selectable**. The UI `keepRef` now defaults
  `allow_unlocked=true` (App `onKeep` + bulk-keep ride that default) and the Tile's `curable` no
  longer excludes pre-pass tiles. The backend guard stays as the **API-caller default** (the
  escape is opt-in there). The pre-pass tile keeps its informational pass marker (no longer
  worded "curate the pass outputs instead").
- **Curate icons re-laid-out + pass markers relocated.** The keep/select/cull icons were
  top-left and only on un-marked tiles; now they line up in the **TOP-RIGHT** corner like Cast's
  star+delete — **keep ✓ · select □ · cull/reject ✕ · delete 🗑 (delete last/rightmost)** — on
  every done tile. The **clean/polish/identity/restore pass markers moved from top-left to
  BOTTOM-LEFT** on all three panels (Casting / Expansion / Curation), clear of the icon lineup.

`tsc` + `vite build` clean (App.tsx + orchestrator.ts + styles.css). **✅ PUSHED `1ebea83`.**

### M10 rig pass — Inspector shows the PER-CELL prompt (2026-06-13, user finding)

User ran an `npc_lite` expansion and saw the Inspector "resolved prompt (as run)" show only the
job label `[dataset npc_lite · 17 img2img cells] <clause>` — no coverage description — and asked
whether the per-cell pose prompts even reach the model. **They do** (verified end-to-end): stage_b
writes each cell's real prompt into `batch_items[i].prompt` (`<angle>, <shot-size>, <expression>,
<clause>, <style>` from `recipe._cell_prompt_fragment`); the batch worker's `run_jobs` does
`merged = {**shared, **item}` and generates with `merged["prompt"]` — so **every image uses its own
cell prompt**. The job-level `p.prompt` is ONLY the `[dataset …]` summary label. The real per-cell
prompt was already recorded (`_batch.parse_batch_result` → `output_meta[out].prompt`), just not
surfaced. **Fix:** `OutputMeta.prompt` added (orchestrator.ts) + the Inspector now prefers
`ometa.prompt` over `p.prompt` for a per-output Stage-B tile. `tsc`+`vite` clean. **✅ PUSHED
`70f6c97`.**

⚙ **Not a bug — img2img limitation (explained to user):** identical-except-expression output at
strength 0.55 is expected — img2img is anchored to the hero's structure, so local facial changes
(expression) land but a front hero won't rotate to profile/back or reframe from a text prompt
alone. True angle/pose coverage = raise strength (≈0.65–0.8, trades identity drift) or the
**video-sketch harvest (M7)** / **Flux2 multi-ref (§11 spike, deferred)** — by design. ⚙ **flux2
is NOT a Stage-B pipeline** — it ships only inside `multi` casting; standalone flux2-img2img is the
deferred §11 spike (P5 Track B fallback), never added in M10 (M10 = acceptance, not a new adapter).

---

## M9 — export / import profiles

started: 2026-06-13 13:30
finished: 2026-06-13 13:39

**Goal (spec §8/§13 M9, P1-10; R66/R67):** export a profile **with all versions** as a
portable bundle; **import = always a NEW profile, rename on collision, no merge**.

**Bundle format:** a zip of the whole `assets/<class>/<slug>/` tree (profile.json + every
version's records + casting/refs/faces files) + a top-level **`loom_bundle.json`** manifest
(kind/bundle_version/asset_class/source id+name). Self-describing + portable across projects.

**Backend (`assets.py`):**
- `export_profile(ws, asset_id)` → writes the zip under `ws.temp_dir`, returns the path
  (recursive `rglob` of the asset dir, files under `asset/…`).
- `import_profile(ws, zip_path)` → **always a new profile** (R67): traversal-guarded
  extraction (rejects absolute / `..` members), reads the manifest (validates kind +
  asset_class), stages to a temp dir, then the **id remap** — ⚙ **fresh `ast_` + every
  `ver_` id** (the runner/lineage key on the version id as requester_id; a re-import into
  the SAME project with the old ids would cross-link), `derived_from` remapped within the
  bundle (a dangling parent → cleared), `active_version`/`versions[]` remapped;
  **rename-on-collision** (`_free_name`: "(imported)", "(imported 2)", … until the slug dir
  is free) — finalized versions + the anchor verification stamp ride through unchanged;
  `shutil.move` staging → the target asset dir. BadZipFile → WorkspaceError.
- ⚙ `create_asset` already gained `prompt_template=` at M8 — not needed here (import writes
  records directly), noted for symmetry.

**API:** `GET /assets/{id}/export` → FileResponse(.zip) ⚠ **[SUPERSEDED by the M9 review below —
export is now TOKEN-GATED, not unauthenticated]** ~~unauthenticated (mirrors the other file
serves: /outputs, casting, anchor)~~. `POST /assets/import` → the zip is the **raw request body**
(⚙ deliberately NOT multipart — `python-multipart` isn't installed and the minimal-deps posture
says don't add it; FastAPI reads the body — see M10 review: now a **streamed running-cap read**),
token-gated, writes a temp zip → import_profile → returns `{profile, renamed_from}`. Route
registered before `/assets/{asset_id}` (distinct methods, but ordered defensively). Token-listed.

**UI:** rail ASSETS head **⤒ Import** (hidden file input → `arrayBuffer()` → POST; switches
to the imported asset, re-sync re-list); stage-ctx bar **⤓ Export** ⚠ **[SUPERSEDED — now a
token-authenticated fetch → Blob → object-URL download, see M9 review]** (~~an `a.ghost` href to
the unauth GET~~). `a.ghost` link styling added.

**Tests: 219 passed** (213 → +6, `test_profile_io.py`): export bundles profile + BOTH
versions + the ref file; unknown-asset 404; import → new profile w/ fresh disjoint ids +
both coexist + collision rename + content (prompt_template) carried; `derived_from` remap +
finalized preserved; **cross-project round-trip** (export from A → import into a fresh B,
no rename, name kept); non-bundle zip + non-zip body → 400. `tsc` + `vite build` clean.

⚠ **Owed (rig/user):** a real export→import round trip through the UI (download a bundle,
re-import it, confirm the rename + that casting/ref/anchor images survive the trip).
**✅ PUSHED `38def3a`** (2026-06-13 13:40 — M9 close per the push-at-milestone-close rule).

### M9 review — fixes (2026-06-13)

Four findings (2 Medium, 2 Low) — all addressed:

- **Medium — export was an unauthenticated bulk read:** `GET /assets/{id}/export` is now
  **token-gated** (`Depends(require_token)`). It packages every version + every file into one
  portable archive — more sensitive than the per-image serves — so it's gated like import. ⚙
  The UI export changed from a plain `<a href>` (which couldn't carry the token) to a
  **fetch + `X-Loom-Token` → Blob → object-URL** download: client `exportProfileUrl()` →
  `exportProfile(assetId): Promise<Blob>`; App.tsx `onExport()` builds a transient `<a download>`
  and revokes the object URL. The unknown-asset path still 404s.
- **Medium — import had no size guard (could bypass the disk guard, R96):** added bounds
  **before any extraction**. API: a **Content-Length** pre-read gate + a post-read byte check,
  both against `assets.MAX_BUNDLE_BYTES` (2 GB) → 413. Importer: per-member
  `ZipInfo.file_size` ≤ `MAX_MEMBER_BYTES` (1 GB), total uncompressed ≤
  `MAX_BUNDLE_UNCOMPRESSED` (4 GB), member count ≤ `MAX_BUNDLE_MEMBERS` (5000), a
  **zip-bomb compression-ratio tripwire** (`MAX_COMPRESSION_RATIO` 200), and
  `_require_import_headroom(ws, total_unc)` — a **disk-free + project-cap headroom check**
  (reuses `diskguard._dir_size_bytes` + `ws.load_project().size_cap_gb`) so a huge bundle is
  refused before it can fill the work disk.
- **Low — bundle_version was written but not enforced:** import now rejects any manifest whose
  `bundle_version != BUNDLE_VERSION` (was checking `kind` only) → no partial reshaping of a
  future/incompatible bundle while the format is young.
- **Low — README stale at the entry point:** README "Next: M9" → an M9 sentence (export/import,
  token-gated + size-guarded) and "Next: M10 (MVP/P1 acceptance)".

**Tests: 221 passed** (219 → +2, `test_profile_io.py`): export-without-token → 401;
unsupported `bundle_version` (repackaged bundle w/ version 999) → 400. `tsc` + `vite build`
clean. No `src/pipeline/` touched → no re-vendor. **✅ PUSHED `0c0a846`** (2026-06-13).

---

## M8 — full L1 World authoring

started: 2026-06-13 09:20
finished: 2026-06-13 09:30

**Goal (spec §6/§13 M8, P1-9; R47/R55/R112):** promote M1's lone style fragment into the full
L1 World — world prose, richer style, the **story spine** whose characters seed **stub
AssetProfiles** (the L1→L2 connector). §12: M8 is Phase-B thickening, not done-line.

**Scope decision:** built the three **load-bearing, system-connecting** pieces; deferred the
descriptive-only ones (journaled):
- **World prose** (`story.world`, markdown) — authoring context (+ future Muse); NOT injected
  into generation (it's a summary, not a prompt).
- **Style global negative** (`story.style.global_negative`) — the one new
  *generation-affecting* field: a negative prompt **auto-applied under the same `apply_style`
  gate as the fragment**, appended to the request's negative_prompt. Wired in `/generate`
  (skipped for multi — ideate takes no negative arg; the worker warns harmlessly where a
  variant ignores negatives).
- **Story spine** (`story.spine` = premise + characters `{id spc_, name, snippet,
  linked_asset_id}`) → **stub AssetProfile** (`create_asset` gained `prompt_template=` so the
  stub's v1_base inherits the snippet, R112) + **manual re-sync** (R55 — the snippet→profile
  push is the ONLY thing that writes it; editing the spine snippet never clobbers a
  hand-edited profile; re-sync overwrites the linked profile's active-version
  prompt_template, refuses if unlinked/finalized).
- ⏭ **Deferred (journaled):** configurable asset-classes with per-class pipeline/scaffold
  defaults (the fixed `characters`/`props`/`scenes` suffice for P1 — no near-term consumer);
  the `@asset@version` structured snippet-injection picker (R47 — the spine snippet already
  flows L1→L2; the picker is a Shots/Flow-era convenience); moodboard images.

**Backend:** `story.schema.json` +world/spine/global_negative; `bible.py` set_world,
set_premise, upsert/remove/link spine character, spine_character; 8 `/bible/*` endpoints
(GET /bible + the style/world/premise/spine writes incl. **stub** + **resync**), token-listed.

**UI:** rail **L1·World / L2·Assets view toggle** → a **WorldWorkspace**: style fragment +
global-negative editors (Save style), world prose (Save world), and the **spine editor**
(premise + per-character rows with save / **+ stub profile** / **⟳ re-sync** / remove, and a
linked ● badge). Stub-create refreshes the L2 asset rail.

**Tests: 212 passed** (205 → +7, `test_l1_world.py`): world roundtrip; global negative
persists + applies to a zimage gen (appended) + opt-out + multi-skip; spine premise/char CRUD
+ empty-name 400; stub creates a profile carrying the snippet + links + double-create 409;
**re-sync is manual** (spine edit doesn't clobber a hand-edited profile; explicit resync
overwrites); unknown-character 404. `tsc` + `vite build` clean.

⚠ **Owed (rig/user):** end-to-end author pass (set world+style+negative → spine character →
stub → cast it → re-sync after a snippet tweak); confirm the global negative visibly helps.
**✅ PUSHED `d0f4366`** (2026-06-13 09:31 — M8 close per the push-at-milestone-close rule).

### M8 review fixes (user findings; 2026-06-13 13:27)
- **Med — the global negative wasn't actually global.** Only `/generate` applied it; Stage-B
  and sketch wove in the style FRAGMENT but never the negative. → new single-source-of-truth
  gate **`bible.resolve_l1(ws, apply_style_req) → (apply, fragment, global_negative)`** +
  `bible.join_negative`; all THREE surfaces now resolve through it. Stage-B folds the negative
  into the shared `extra` (reaches the dry-run preview + every realization group's batch
  params → every cell); sketch folds it into the i2v params; /generate refactored onto the
  helper. Still skipped for multi (ideate takes no negative). Test: global negative present in
  Stage-B `first_argv` + sketch argv + opt-out drops it.
- **Med — split style state could overwrite a World edit.** WorldWorkspace saved via setStyle
  but the parent's L2 toolbar `styleDraft`/`applyStyle` went stale → a later Assets-bar save
  wrote the old fragment back. → World's save now calls an **`onStyleSaved(s)`** callback that
  syncs the parent style state (style/draft/applyStyle), so the two editors can't diverge.
- **Low — stale copy/docs:** README "Next: M8"→M9; kb-loom-p1.md header + §13 M8 + WBS P1-9
  marked ✅; the L2 Assets style-bar placeholder "auto-prepended" → "auto-applied — appended;
  full editor in L1·World".

**Tests: 213 passed** (+1 global-negative-everywhere). `tsc` + `vite build` clean.
**✅ PUSHED `bfc5b42`** (2026-06-13 13:28).

---

## M7 — video-sketch harvest (`ltxv` + frame extraction)

started: 2026-06-12 21:25
finished: 2026-06-12 21:52 (core; ⚠ rig E2E owed — the first VIDEO pipeline on the queue)

**Goal (spec §13 M7 / P1-8; R11/§4.1):** cheap low-res `ltxv` i2v motion sketches from the
hero ★ → harvest stills — multi-angle/pose coverage img2img can't reach, without 3D.

**Design (the load-bearing decision):** a sketch is **CELL-TARGETED** — the user picks ONE
target coverage cell (angle/shot/expr; bg "" — i2v inherits the hero's setting) and the
motion is prompted toward it. The cell rides the ltxv job as the **first-class
`coverage_cell` field**, and the chained **`frame_harvest` pass** stamps it onto every
extracted frame (new `_submit_chained` fallback: no per-output meta → inherit the parent's
job-level cell) — so harvested stills stream into the Stage-B grid and **curate exactly like
recipe cells** (keep ✓/reject/filters/P2 captions, zero new curation machinery). This also
answers the M3-era question of how video frames get contract-valid cells: they're *aimed*,
not classified.

- **`ltxv` adapter + catalog entry** (5th pipeline): 4 diffusers variants
  (`Lightricks/LTX-Video-*`; default **2b_0.9.7_distilled** — CFG off, 8 steps, 704×480
  native, ⚙ **needs offload=model on 16 GB — T5-XXL is ~11 GB**, the variant default
  handles it); params incl. num_frames (121 ≈ 5 s @ 24), fps, steps, guidance,
  offload; ⚙ the worker calls the variant flag **`--variant`**, mapped via the catalog's
  model_name spec. Manifest-as-truth parse (PipelineManifest family, mp4 output), coarse
  stage progress, `  Video:` streamed. Weight gate = the standard variant-aware
  `image_model_present` probe (diffusers repos). VRAM estimate 12 GB.
- **`frame_harvest` worker** (monorepo postproc/frame_harvest, vendored): pure OpenCV CPU,
  every-k-th frame (cap max_frames) → PNGs; ⚙ **one manifest item PER FRAME** (1 video →
  N outputs — the jobs_batch manifest is the truth, the inputs file is just the work
  order); STOP between frames; meta = the carried cell + `frame` number. **Run END-TO-END
  in the no-GPU suite** (synthetic 30-frame mp4 → 4 stills, meta verified).
- **`POST /assets/{id}/stage-b/sketch`** {cell axes, motion_prompt, every, max_frames,
  params channel}: hero-seeded i2v, prompt = **cell fragment → clause → motion → style**
  (the M3 order), contract-validated cell, variant-aware 412, VRAM/disk admission,
  post_passes=[harvest]. Videos are **not keepable** (422 "curate the harvested frames");
  the grid renders mp4 outputs as a 🎬 placeholder tile (no curation controls).
- **UI:** Stage-B **🎬 sketch bar** — target angle/shot/expr selects (frozen vocab), motion
  prompt, every/frames, [🎬 Sketch ▶].

**Tests: 205 passed** (193 → +12: ltxv catalog/argv (--variant!)/manifest-as-truth/progress;
the REAL harvest run; chained-harvest job-cell fallback; sketch dry-run prompt order +
chain spec; submit with first-class cell; bad-cell 422; mp4-keep 422; catalog = FIVE
pipelines + ltxv variant drift guard; vendor-sync +3 files). `tsc` + `vite build` clean.

⚠ **Owed (rig):** the FIRST VIDEO JOB through the queue — a real sketch (~5 s @ 704×480,
2B distilled, offload=model) + harvest streaming + curation of a harvested profile/back
frame; VRAM behavior under offload (the 12 GB estimate is a guess until measured); sketch
motion-prompt quality (does "turns to profile left" actually reach the cell?). ⏭ Follow-ups
(journaled): identity/restore chaining AFTER harvest (one-line spec append — frames will
drift; pair with a verified anchor), per-sketch t2v mode (no loom surface yet), proxy mp4
playback in the UI (tile is a placeholder; the file serves via /outputs).
**✅ PUSHED `ef67165`** (2026-06-12 21:53 — M7 core close per the push-at-milestone-close rule).

### M7 review fixes (user findings; 2026-06-13 09:18)
- **Med — hero ★ star collided with the top-left pass markers** (⏳/clean·polish·restore). →
  moved `.cell .star` to **top-RIGHT, `right:28px`** (just left of the 🗑 delete at `right:4px`).
- **Med — sketch jobs didn't persist the LTXV default dims** → the chained harvest fell back to
  the runner's 1024² display metadata, so 704×480 harvested stills rendered square in the grid.
  → `/stage-b/sketch` now resolves `model_catalog.param_default("ltxv", dim)` into the job
  params when width/height are unset, so the harvest pass inherits **704×480**. Test: submit
  asserts persisted 704/480 AND the chained harvest job inherits them.
- **Low — entry-point doc drift** (header/README "next M7", WBS P1-8 in-progress) → all three now
  read M7 ✅ / next M8.

**Tests: 205 passed** (the sketch-submit test extended). `tsc` + `vite build` clean.
**✅ PUSHED `1fe16f8`** (2026-06-13 09:19).

---

## M6 — image postproc toolkit (re-sized: pass backends)

started: 2026-06-11 22:55
finished: 2026-06-12 13:44 (core; ⚠ rig E2E owed)

**Goal (spec §9/§13 M6, P1-7; re-sized per PM-9):** queueable per-image postproc actions —
each tool ≈ a **pass backend** on the chained-pass machinery. §12 guardrail: keep the set
minimal — matting ✓ (M3.5), identity ✓ (M4); **M6 v1 = face restore** (the observed need:
the identity swap's 128px softness on close-ups — the M4 pairing). Upscale stays
"as needed"; SAM2 masking + HandRefiner onboarding deferred (journaled below).

### M6 spike (≈10 min) — VERDICT: GO via GFPGAN 1.4 ONNX
The gfpgan/basicsr pip stack is known-broken on modern torchvision (functional_tensor
import removed) — so the spike tested **ONNX mirrors** instead: **`facefusion/models-3.0.0
/ gfpgan_1.4.onnx`** downloads + runs on onnxruntime CPU, **0.32 s per 512² face**, visibly
clean restoration on a real casting image (detect via the buffalo_l pack already on the
rig → `face_align` 512 crop → onnx → output). Zero new python deps.
spike artifacts: `F:\_tmp\spike_m6\`.

**Worker (R162 monorepo-FIRST):** `src/pipeline/postproc/face_restore/run_pipeline.py` —
batch-shaped (jobs_batch manifest `face_restore_batch_<ts>.json`, STOP, `  Image:` per
item). Per item **every** face ≥ `--min-det-score` is restored: `estimate_norm` 512 align →
GFPGAN onnx ([-1,1] NCHW) → **`--blend`** (default 0.8) with the original crop → **feathered
inverse-affine paste-back** (16px border zeroed + 31px gaussian mask — no seam); no-face
images pass through (`meta.restore="no_face_passthrough"`); restored items carry
`meta.faces`. **On-rig verified** (single-shot: restored, 1 face, 0.69 s, seamless full-frame
result). Vendored byte-identically (drift guard +1).

**Orchestrator:** `adapters/face_restore.py` (modes `("restore",)`, inputs.json, shared
`_batch` parse/progress) — ADAPTERS + `/capabilities` + VRAM 1 GB (onnx CPU). models.json
`postproc.face_restore` = gfpgan-1.4 (facefusion mirror; upstream GFPGAN Apache-2.0) +
the shared buffalo-l filesystem-probe entry (self-contained gate). **Catalog:** `restore` +
`restore_blend` joined the POST_PARAMS literal (post:True → served on zimage/sd35/multi +
stage-b; the P1-12 drawer grouping auto-files them under "other postproc" — zero UI work).

**Pass integration:** `_extract_post_passes` grew the restore branch (footgun 422 on
orphan `restore_blend`, tool-scoped weight 412 `?postproc=face_restore`), appended after
clean/polish; **stage_b inserts identity BEFORE restore** (lock first, then GFPGAN fixes
the swap softness — restore is the chain's final word). `_submit_chained`'s identity branch
generalized to the **io-pass branch** (identity | restore): items `{"input": …}`, per-pass
params (anchor vs blend), coverage_cell/seed meta carried — curation survives.

**Tests: 189 passed** (179 → +10, `test_face_restore_pass.py`: params on every surface;
dry-run spec + no worker flag (⚙ gotcha: the conftest per-test out dir contains the test's
own name — flag assertions must use `startswith("--…")`, not substring); footgun 422;
weight 412; **order [polish, identity, restore]**; chained io-job (input vocabulary, blend,
meta carry); adapter inputs.json + manifest parse incl. restore/faces meta; vendored
resolve; drift guard). `tsc` + `vite build` clean.

⚠ **Owed (rig):** a real chained sweep ending in restore (identity → restore on close-ups —
the M4 softness check), blend default sanity (0.8 too strong on stylized faces?). ⏭ M6
follow-ups (journaled, not started): **upscale** pass (Real-ESRGAN-class onnx — same
io-pass slot, "as needed" per §12), SAM2 masking (when inpaint-targeted edits need it),
HandRefiner onboarding (worker exists in the monorepo; license-gated), cutout reuse from
M3.5.
**✅ PUSHED `bc9a38f`** (2026-06-12 13:45 — M6 core close per the push-at-milestone-close rule).

### M6 review fixes (user findings; started 2026-06-12 ~13:50, finished 2026-06-12 14:00)

- **Medium — the fetch could download the whole mirror.** The worker loads exactly
  `gfpgan_1.4.onnx`, but `fetch_postproc` used an **unrestricted `snapshot_download`** for
  non-pack entries — on `facefusion/models-3.0.0` (dozens of models) that's a multi-GB disk
  surprise, the exact thing the P0/M6 disk guardrails exist to prevent. → postproc entries
  gained an optional **`filename`** key (set on gfpgan-1.4 AND inswapper-128; ⚙ `probe` =
  presence check, `filename` = fetch restriction — both set for single-file entries);
  `fetch_postproc` branches to **`hf_hub_download(repo_id, filename)`** when present
  (snapshot stays for genuinely-multi-file repos like BiRefNet). Regression test:
  `fetch_postproc("face_restore")` requests exactly `("facefusion/models-3.0.0",
  "gfpgan_1.4.onnx")` and never calls snapshot_download.
- **Low — plan docs lagged M6.** kb-loom-p1.md header now reads M5 ✅ + M6 ✅ (re-sized:
  face restore; masking/upscale deferred), **next: M7**; the §13 M5/M6 bullets carry
  as-shipped notes (M5 incl. the R57 flat-select deferral; M6 names the GFPGAN-onnx
  decision + the deferred set).

**Tests: 190 passed** (+1 fetch regression). **✅ PUSHED `31e5839`** (2026-06-12 14:01).

### M5/M6 UI + multi-size fixes (user findings; started 2026-06-12 ~19:30, finished 2026-06-12 19:43)

- **multi cast size: drawer said 1024², images came out 1280×720.** Root cause: the param
  drawer advertises the CATALOG default (`MULTI_PARAMS` width/height = 1024, the member
  models' native square), but an unset cast request falls back to **GenerateRequest's
  top-level defaults (1280×720 — the P0/Wan project default)**, which ride into `base` via
  model_dump for every pipeline. Display ≠ reality. → the `/generate` multi branch now
  resolves an UNSET width/height (not in `req.model_fields_set`, not in the params channel)
  to **`model_catalog.param_default("multi", dim)`** — the advertised default is the
  effective one; explicit values (top-level or params channel) still win. ⚙ New mc helper
  `param_default(pipeline, name)`. Regression test pins all three resolutions (unset→1024,
  top-level 1280×720, params-channel 1536).
- **Version controls didn't fit the design system** (native white selects, odd sizes — the
  element theming was scoped to `.generate-bar` only, so the M5 stage-ctx controls + the
  NEW VERSION modal + the inspector/hero-strip ghost buttons all leaked native widgets). →
  **`.ghost` is now a global class** (one definition, bar duplication kept for specificity);
  `.stage-ctx select` + `.modal select/input` themed to the panel system (panel-2 background,
  line borders, 11–12px sizing consistent with `.proj-btn`/bars).

**Tests: 191 passed** (+1 multi-size regression); `tsc` + `vite build` clean.
**✅ PUSHED `1c0ac06`** (2026-06-12 19:44).

### M6.1 — face-portrait anchor derivation + pass legibility (user findings/idea; started 2026-06-12 ~20:45, finished 2026-06-12 21:13)

The user's first real restore run surfaced two fixes and one feature idea (theirs):

- **16:9 tile on a 1024² restore output.** The io-pass jobs carried no width/height, so the
  grid's tile-aspect fell back to 1280×720. → `_submit_chained` io-pass params now carry the
  parent's dims (display-only — the io workers ignore them); the derive endpoint sets 512².
- **"I don't see anything different" — pass legibility.** Two truths: (a) GFPGAN is **blind
  restoration** (no reference face — it detects each face and re-synthesizes detail from its
  prior; unlike identity, which uses the anchor), and on an already-sharp 1024² face the
  effect IS subtle — its value shows on small/soft faces (the post-swap close-up case);
  (b) the user had no way to even SEE what the pass did. → the **Inspector now shows the
  pass meta** per output: `identity: locked (anchor cos 0.87)`, `restore: restored
  (2 faces)`, passthroughs, portrait crops (OutputMeta typed).
- **⭐ User idea, shipped as M6.1: derive the ANCHOR from a restored face portrait.** A face
  inside a full-body shot is a poor anchor source (few pixels). New **`portrait` mode** on
  the face_restore worker (`"portrait": true` in inputs.json / `--portrait`): output = the
  **restored ALIGNED 512² crop of the LARGEST face** (no paste-back; no-face = item error,
  not passthrough; `meta.restore="portrait_crop"`). **On-rig verified**: a medium shot where
  the face was ~120px → a detailed 512² portrait (0.59 s). **`POST
  /assets/{id}/anchor/derive`** {job_id, output} — ownership-guarded, weight-412,
  disk-gated; queues ONE portrait job (stage A, requester = version) whose tile lands in
  the Stage-A grid → the existing **"⚓ set as face anchor"** picks it up (no new anchor
  plumbing). Inspector gains the **"✨ face portrait"** button next to ⚓. R94's "generate
  detailed face portraits → pick one" is now a one-click flow.

**Tests: 193 passed** (+2: portrait flag in inputs.json (both modes); derive endpoint queues
the portrait job (input/dims/stage/requester) + foreign-asset 409; chained-restore test also
asserts the dims carry). `tsc` + `vite build` clean. Worker re-vendored (MD5 match).
**✅ PUSHED `8f6dbc3`** (2026-06-12 21:14).

### Doc-sync fixes (user findings; 2026-06-12 21:40)
- README "Next: M5" → now records M5 + M6/M6.1 as-shipped and **Next: M7** (the entry-point
  drift class again — README lags the journal; caught by the user twice now, worth watching
  at every milestone close).
- `fetch_postproc` docstring still described the pre-review snapshot-only behavior — now
  documents the three entry kinds (insightface_pack / single-file `filename` / snapshot).
**193 passed** unchanged. **✅ PUSHED `eea71f8`** (2026-06-12 21:41).

---

## M5 — profile versioning

started: 2026-06-11 22:16
finished: 2026-06-11 22:30 (core; ⚠ E2E verify + R57 selector depth owed)

**Goal (spec §8/§13 M5, P1-6; R49–R51, R58–R61):** copy-on-create from ANY parent →
edit-what-differs → **finalize = pure-intent lock**; version selector; per-version anchor
(already true since M4); new-profile path (= the existing `create_asset`, R61: author's call,
no hints).

**Backend (`assets.py`):**
- **`create_version(asset_id, parent_version_id=None, name=None)`** — full **deep-duplicate of
  any prior version** (default: active): `shutil.copytree` of the whole version dir
  (casting/ + refs/ + faces/), records carried as-is, fresh `ver_` id, `derived_from` = parent,
  **unlocked** (R51), `saved_at` restamped; **the new version becomes active**. ⚙ The anchor's
  durable **verification stamp carries** (the copied file is byte-identical — the proof holds).
  Dir naming: `v{n}_{slug(name)}` (or the name itself when it's already vN-shaped); collision →
  clear error.
- **`finalize_version`** — sets `finalized: true` (idempotent re-finalize). **Lock enforcement
  (R60): `_require_unlocked` now guards EVERY mutator** — star_candidate, set_hero, set_anchor,
  clear_anchor, keep_ref, reject_output, remove_ref (+ save_profile's existing refusal), and
  even the observer's `mark_anchor_verified` returns without writing on a locked version.
  Generation itself is NOT gated (jobs live in out/, not the record) — curating results INTO a
  locked version is what's refused.
- **`set_active_version`** — validates membership + loadability, flips `profile.active_version`.

**API:** `POST /assets/{id}/versions` {parent_version_id?, name?} ·
`POST /assets/{id}/versions/{vid}/finalize` · `POST /assets/{id}/versions/activate` — all
token-gated, in the /version token list.

**UI:** the stage-ctx bar's hardcoded "v1_base" is now the real **version selector** (name +
🔒 on finalized), **[+ version]** (prompt for a name → copy-on-create → switches), and
**finalize 🔒** (confirm dialog) / a "🔒 finalized" badge when locked. Switching versions
resets the per-version Stage-B controls (same leak class as the asset-switch fix) and
re-scopes casting/refs/anchor/rejected via the existing refresh path. Server errors (mutating
a locked version) surface in the error bar.

**Tests: 179 passed** (175 → +4, `test_versioning.py`): full-deep-duplicate (records + files
+ anchor verification carried, active switched, derived_from); create **from any parent**
(v3 from v1 while v2 is active, R59); finalize locks every mutator (×6 raises + save refusal
+ stamp-writes-nothing) while copy-on-create FROM the locked version stays editable;
activate roundtrip + unknown-version 400. `tsc` + `vite build` clean.

⚠ **Owed:** E2E on the rig (create v2 of a real character → re-anchor/re-curate → finalize →
verify the lock in the UI); **R57 selector depth** (grouping/search/naming for MANY versions —
v1 ships a flat select; revisit when a real profile passes ~8 versions, journaled as the M5
follow-up); per-version voice/lora dirs stay later-phase (P2/P3).
**✅ PUSHED `94545a4`** (2026-06-11 22:31 — M5 core close per the push-at-milestone-close rule).

### M5 review fixes (user findings; started 2026-06-11 ~22:35, finished 2026-06-11 22:47)

Three findings on the M5 core, all fixed — they sharpen the milestone's actual promise:

- **Copied refs weren't inspectable/cullable.** The Stage-C grid derived ONLY from job
  history scoped to the active version — a fresh copy has durable `ref_set` records + `refs/`
  files but no jobs, so its inherited refs counted as "kept" while being invisible
  ("copy parent, then edit what differs" was hollow). → **Durable ref tiles**: Stage C now
  synthesizes a cell for every `ref_set` entry whose source output isn't already on the grid,
  served from the version's `refs/` via `refUrl` (the durable copy — works after job deletion
  too, not just version copies), rendered kept (✓ → **cull works through the ref id**),
  coverage filters apply (`covOf` reads `refItem.coverage_cell` first). Reject/bulk don't
  apply to durable tiles (they're already kept; cull is the one edit).
- **"Create from any prior parent" wasn't reachable from the UI.** The prompt-only flow could
  only copy the active version. → A small **NEW VERSION modal**: name + **parent select over
  every version** (default = active, finalized parents allowed — R59/R51), Enter-to-create.
- **Finalized felt error-prone, not read-only.** The server locked everything but the UI
  still offered the buttons. → `activeVersionLocked` now drives the whole surface: Save
  AssetProfile disabled (+ 🔒 label), star/keep/reject/bulk-select hidden on tiles, the
  Inspector's "⚓ set as face anchor" and the hero-strip anchor ✕ hidden, bulk bar replaced
  with a **"🔒 read-only (finalized)"** note, keyboard k/x no-op (arrows still navigate).
  Generation stays available (jobs aren't version mutations — matching the backend stance).

**Tests: 179 passed** (UI-only changes — backend untouched); `tsc` + `vite build` clean.
**✅ PUSHED `33a1bbc`** (2026-06-11 22:48).

---

## P1-12 — curation throughput (+ params-drawer grouping)

started: 2026-06-11 21:30
finished: 2026-06-11 21:57

**Goal (P1-12, re-homed post-M4/pre-P2 in the doc sweep; user kickoff "we almost forgot one of
the action points"):** the *reject* workflow for culling ~100→~30 — bulk select/reject, keyboard
nav, filter-by-coverage-cell. Plus the user's second ask: the ⚙ params drawer mixed everything —
group it into blocks.

**Backend — persistent `rejected[]` record:**
- `version.schema.json` gains optional **`rejected: [string]`** (out/-relative output names) —
  lightweight + persistent (the reject sweep survives reloads), **no image copy** (unlike
  keep, rejection is just a view mark). Mutually exclusive with ref_set membership:
  **rejecting a KEPT output → 409** ("cull first"); **keeping a rejected output un-rejects
  it** (keep wins — `assets.keep_ref` clears the mark).
- `assets.reject_output(..., rejected=True|False)` — idempotent both ways, traversal-guarded.
- **`POST /assets/{id}/refs/reject`** {job_id, output, rejected} — ownership scope guard
  (`_require_job_owned_by`, never cross-asset) + output-membership check; `/version` token
  list updated.

**UI — Stage-C curation toolbar + tile controls + keyboard:**
- **Filter bar** (Stage C): shot / angle / expr selects over the frozen coverage vocab (⚙
  UI-side `COV_*` consts mirror `coverage.py` — keep in lockstep), **show-rejected** toggle
  (rejected tiles hidden by default; shown = dimmed/grayscale with ↩ un-reject), live counts
  `kept · rejected · showing/total`.
- **Tile controls**: ✕ reject (bottom-right, persistent+reversible; hidden on kept tiles) and
  □/■ **bulk-select** (bottom-left); bulk bar appears with `✓ keep N` / `✕ reject N` / clear —
  per-item isolation (one 409, e.g. a pre-lock tile, doesn't abort the sweep; errors counted,
  authoritative state refreshed once at the end).
- **Keyboard curation**: the grid is focusable — ←→↑↓ move the selection (↑↓ = ±5), **k** =
  keep, **x** = toggle reject, **space** = toggle bulk-select; key legend in the toolbar.
- Coverage filtering reads per-output `output_meta.coverage_cell` (batch jobs) with the
  legacy job-level field as fallback.

**Params drawer grouped (user ask #2):** `ParamControls` now renders **blocks** — *model /
generation* first, then *clean pass*, *polish pass*, and *other postproc* (future families
land automatically) — grouped by the catalog's `post` marker + name-prefix family, each with
a small uppercase label + left rule. The flat mix had become unreadable once POST_PARAMS
joined every pipeline. (`ParamSpec.post` added to the frontend type.)

**Tests: 172 passed** (169 → +3, `test_curation_throughput.py`: reject/unreject roundtrip
persists on disk + idempotent; kept-output 409 + keep-clears-reject; ownership/membership
guards). `tsc` + `vite build` clean.

⚠ E2E owed (rig): a real ~30-cell curation sweep with the keyboard + a bulk reject; filter
usefulness check (is per-axis enough, or is a thin-cells coverage view needed? — deferred
idea, noted). ⏭ PM-3's multi CLI extension stays opportunistic (user confirmed).
**✅ PUSHED `7301474`** (2026-06-11 21:58 — P1-12 close per the push-at-milestone-close rule).

### P1-12/M4 review fixes (user findings; started 2026-06-11 ~22:05, finished 2026-06-11 22:15)

Four findings, all fixed (pre-M5 hardening):

- **Medium — Stage-B controls leaked across assets.** `realize`/`identityOn` were global UI
  state: switching from a matted asset could send `realize="mixed"` with no bg_mask (422), and
  an explicit identity override shadowed the server's verified-anchor auto behavior on the next
  asset. → `onSelectAsset` resets both; plus an effect **auto-downgrades mixed→img2img whenever
  the bg mask disappears** (matte job deleted, asset switched).
- **Medium — anchor verification wasn't durable.** The computed check read RUNNER job history —
  deleting/pruning the verifying identity job silently un-verified a good anchor and turned
  default-on identity off (poison for M5's Saved/portable versions). → **Durable stamp on
  `version.anchor`** (`verified_at`/`verified_by_job`, schema'd): the runner gained a generic
  **completion observer** (injected like the disk gate — runner stays asset-agnostic) which
  `main` wires to `assets.mark_anchor_verified` (looks the version up by id, confirms the
  verified file is STILL the current anchor — a mid-run re-pick gets no credit, idempotent).
  stage_b reads the **stamp first**, job-history as fallback, and **lazily promotes** a
  legacy history-only verification to the stamp. UI mirrors (verified_at first, live scan as
  interim feedback). Re-pick still invalidates naturally (fresh anchor dict has no stamp).
- **Low — /refs/reject was looser than its Stage-C contract.** Now mirrors keep: job must be
  **done** (409) and the output must carry a **coverage_cell** (422) — API callers can no
  longer dirty `rejected[]` with non-dataset outputs.
- **Low — stale entry-point docs**: kb-loom-p1.md header (was "M1–M3, M4+ pending") + README
  (was "Next: M4") → both now M1–M4 + M3.5 + P1-12 ✅, next M5.

**Tests: 175 passed** (172 → +3: durable-stamp survives job pruning (observer wired by
lifespan); lazy promotion writes the stamp from a history-only verification; reject 409-not-done
+ 422-no-coverage_cell). `tsc` + `vite build` clean.

---

## M4 — face-anchor + identity anchor

started: 2026-06-11 16:58
finished:

**Goal (spec §13 M4 / P1-5, P1-14; R82/R86/R93/R94/R114):** face-anchor sub-stage (pick
`anchor.png` per version) + an identity lock applied to Stage-B output (on by default once an
anchor exists, opt-out) — **spike-first** (plan decision 2026-06-11: PuLID-on-our-bases was
unverified; the §12 guardrail means a failed spike degrades scope, blocks nothing).

### Identity spike (started 2026-06-11 16:58, finished 2026-06-11 17:04) — VERDICT: GO via inswapper

Run ON the rig (CPU), against the user's real casting candidates (`loom/stubz001`,
photoreal char001 set):
- **Rung A — detection+embedding: PASS.** `insightface` **1.0.1 now ships a pure-python wheel**
  (the historical Windows-build pain is GONE; installed into the shared .venv, R103);
  `onnxruntime` 1.24.4 CPU was already present. buffalo_l pack auto-downloaded to
  **`F:\HF_HOME\insightface`** (⚙ worker root). All 4 candidates detected (det_score
  0.78–0.87 on medium shots), **~0.08 s/image CPU** after warmup. Bonus: the candidate
  cross-cosines (0.10–0.32) QUANTIFY the bootstrap problem (same-person ≥ ~0.5) — exactly the
  metric P2's readiness meter (R120/D2) needs; rung A doubles as its feasibility proof.
- **Rung B — inswapper_128 swap: PASS, decisively.** HF mirror `ezioruan/inswapper_128.onnx`
  (⚠ InsightFace research/non-commercial — `_license_gate.py` posture, licensing deferred per
  project memory). Swap hero→most-dissimilar candidate: **cos 0.105 → 0.870** re-embedded,
  **0.19 s/image CPU**, visually clean paste-back (lighting/angle adapted). Known caveat:
  128px swap region = softness on extreme close-ups → face-restore pairing is the M6 hook.
- **Rung C — diffusion-coupled identity: NO-GO for M4.** PuLID = SDXL/FLUX.1-dev only;
  InstantID = SDXL-only; InstantX SD3.5 IP-Adapter = general image conditioning (identity
  strength unproven, sd35-only, worker surgery). None target Z-Image (the LoRA base), klein,
  or sd3.5 with face specialization. Defer to the multi-ref-spike era (P5 Track B); revisit
  then.

**Chosen M4 design:** identity = a **post-hoc ReActor-class pass** (detect → swap to the
anchor face → paste back), CPU-cheap, **model-agnostic** (locks zimage/sd35/flux2/multi
outputs alike), riding the **chained-pass machinery** as a batch-shaped job (streams per
item, ⏹ works, coverage_cell meta carried → curation survives). Anchor = a per-version
chosen face image (R94), set from any owned output. No-face images (back views) pass through
unchanged — correct, the face isn't visible. R114 strength: inswapper is binary (swap/no-swap);
per-image "strength" deferred to a blend param if ever needed.

spike artifacts: `F:\_tmp\spike_m4\` (rung scripts + swapped sample).

### M4 implementation (started 2026-06-11 17:04, finished 2026-06-11 17:17; ⚠ GPU/E2E verify owed)

**Worker (R162 monorepo-FIRST):** `src/pipeline/postproc/identity/run_pipeline.py` —
**batch-shaped like the zimage/sd35 `--jobs-file` workers** (one face-stack load, loops the
items, STOP file = graceful ⏹, `identity_batch_<ts>.json` in the SAME jobs_batch shape, `  Image:`
per item) so the orchestrator's entire batch machinery applies unchanged. Per item: largest face
≥ `--min-det-score` (0.5) → inswapper swap to the anchor → paste back; **no-face images pass
through unchanged** (`meta.identity="no_face_passthrough"` — correct for back views, dataset
stays complete); locked items echo `meta.anchor_cos` (re-embedded ArcFace cosine to the anchor —
**P2's readiness meter gets its on-model number for free**). Anchor with no detectable face →
clear fatal error. ⚙ insightface root: `LOOM_INSIGHTFACE_ROOT` > `<HF_HOME>/insightface`;
inswapper via plain `hf_hub_download` (hub cache → the HF-cache probe + fetch flow work
naturally). **Verified end-to-end ON the rig** (single-shot mode): locked, cos 0.87, exit 0.
Vendored byte-identically (drift-guard parametrize +1).

**Orchestrator:** `adapters/identity.py` (modes `("lock",)`; build_argv writes
`<out>/inputs.json` = anchor + items + tunables; parse_result = the SHARED
`_batch.parse_batch_result`; per-item progress) — registered in ADAPTERS + `/capabilities`;
VRAM estimate 1 GB (onnxruntime CPU). **`_submit_chained` grew an identity branch**: pass spec
`{"pass": "identity", "backend": "identity", "anchor": <abs>, "min_det_score"}` → ONE batch
`lock` job over the parent's outputs, items `{"input": …}` (identity vocabulary — no
prompt/init_image), coverage_cell/seed meta carried (curation survives the lock). models.json
`postproc.identity` = inswapper-128 (⚠ research/non-commercial note; buffalo_l auto-downloads,
not gated).

**Face anchor (R94):** `version.schema.json` gains optional **`anchor`** object
{file, source_output, job_id, set_at} (legacy `anchor_ref` untouched); `assets.set_anchor`
(copies the picked output into the version's **`faces/anchor.png`** — self-contained, R94
re-pickable) / `clear_anchor` / `anchor_file_path`. **`POST /assets/{id}/anchor`** (set via
job_id+output with the `_require_job_owned_by` scope guard — never cross-asset; output must be
one of the job's outputs; `job_id=null` clears) + **`GET /assets/{id}/anchor/file`** (unauth
read, mirrors /outputs); `/version` token list updated.

**Stage-B identity pass (R86/R93):** StageBRequest gains `identity: bool|None` (None = **ON when
the version has an anchor** — default-when-available; false = opt-out; true without an anchor →
422) + `identity_min_det_score`. Appended **LAST** after clean/polish (the lock is the final
word — a later polish would re-diffuse the swapped face). Weight gate: tool-scoped 412
(`?postproc=identity` fetch hint), skipped on dry_run.

**UI:** Inspector gains **"⚓ set as face anchor"** on any selected done output (works for
casting candidates, Stage-B cells, sandbox images); Stage-B hero strip shows the **anchor thumb**
(+ ✕ clear); Stage-B bar gains the **⚓ identity checkbox** (auto-checked when an anchor exists,
disabled until then); `AnchorInfo` type + setAnchor/clearAnchor/anchorUrl client fns.

**Tests: 165 passed** (155 → +9 +1 drift: `test_identity_anchor.py` — adapter inputs.json +
batch-manifest parse (anchor_cos + passthrough meta) + no-manifest honesty; `_submit_chained`
identity-branch unit (vocabulary, meta/seed carry, inheritance); anchor set/serve/clear
roundtrip + foreign-job 409 + unknown-output 422; stage-b default-on + appended-last +
opt-out/require-422 + weight-412). `tsc` + `vite build` clean.

**✅ PUSHED `ede1b66`** (2026-06-11 ~17:25, one commit covering **M3.5 + its review fixes + M4**
— the two milestones interleave in main.py/runner.py/models.json, so splitting would have made a
broken intermediate tree; 21 files). ⚠ Process note: M3.5 closed WITHOUT a push (user caught it)
— the **push-at-milestone-close** rule is now in the journal preamble conventions.

### M4 review fixes (user findings; started 2026-06-11 ~17:30, finished 2026-06-11 17:44)

Four findings on the M4 build, all fixed:

- **High — pre-lock outputs were curatable.** The parent batch's outputs carry coverage_cell, so
  with identity "on" a user could still keep ✓ the UN-locked originals into the ref_set (the
  exact corpus-poisoning the lock exists to prevent — same class as the M3.5 bg_mask hole, from
  the curation side). **API enforcement:** `refs/keep` → **409** when the job has PENDING
  `post_passes` (its outputs aren't the end of the chain; message names the pending passes),
  with an explicit **`allow_unlocked: true`** escape for the legitimate case (a ⏹-stopped chain
  curated deliberately). **UI:** pre-pass tiles get a **⏳ badge** naming the pending passes and
  lose the keep ✓ button — the terminal pass job's tiles are the curatable ones.
- **Medium — a faceless anchor only failed at run time.** Default-on could arm a bad anchor
  (landscape/back view) that dies inside the worker mid-sweep. Now the anchor is **VERIFIED
  lazily, with zero stored state**: verified ⇔ a done+ok identity job for this version used
  this anchor file AND started after it was (re-)picked (`created_at >= anchor.set_at` — a
  re-pick auto-invalidates; the worker hard-fails on a faceless anchor, so a successful run IS
  the proof). **Default-on requires verified**; an unverified anchor defaults identity OFF with
  an explanatory `identity_note` in the response; **explicit `identity: true` is allowed
  unverified — that run is the verification**, after which default-on engages. UI mirrors the
  rule (⚓ "?" marker + checkbox unchecked until verified). *(A dedicated detect-only verify job
  was considered and deferred — the lazy rule needs no runner→assets coupling and no new state.)*
- **Medium — buffalo_l wasn't in the gate/fetch story.** The detector/embedder pack
  auto-hydrated at run time → a fresh/offline rig could clear `/components/fetch?postproc=identity`
  and still die mid-job. models.json `postproc.identity` now carries a **`buffalo-l` entry with
  a FILESYSTEM probe** (`insightface_pack` entry type — it's a github-release zip, not an HF
  repo) at the worker's insightface root (⚙ `components._insightface_root()` is a faithful
  mirror of the worker's — keep in lockstep); `fetch_postproc` hydrates pack entries via the
  insightface auto-downloader (explicit fetch only, R163). The stage-b identity 412 now covers
  both weights.
- **Low — stale PuLID/per-output-strength wording in kb-loom-p1.md** (§7 R86 bullet, §7.1
  hold-constant row, §9 table, §10 anchor+R114 paragraphs, §13 M4 ✅, WBS P1-5/P1-14): amended
  as-shipped — inswapper swap (binary; `min_det_score` is the shipped per-image control; a
  blend-alpha param is the future "strength"), PuLID-class re-assessed at P5 Track B.

**Tests: 169 passed** (165 → +4 net: keep-409 + allow_unlocked escape; unverified-defaults-off +
explicit-true-allowed; verified→default-on (the old default-on test now verifies first);
re-pick invalidates verification; buffalo filesystem gate; the 412 test now uses explicit
identity:true — correct under the new default). `tsc` + `vite build` clean.
**✅ PUSHED `d8c350b`** (2026-06-11 17:45 — M4 re-close per the push-at-milestone-close rule).

⚠ **E2E verify owed (M4, user's GPU/rig session):** a real Stage-B sweep with the anchor set —
chained identity job streams locked tiles, `anchor_cos` visible in meta, back-view passthrough,
curation of locked cells into ref_set; anchor picked from a REAL face close-up (the spike used a
medium shot — a tight portrait anchor should lock even better); ⏹ mid-identity-pass. ⏭ Deferred
(journaled): face-restore pairing for extreme close-ups (M6 — inswapper is 128px), per-image
anchor strength (R114 — inswapper is binary; a blend param if ever needed), diffusion-coupled
identity (PuLID-class) re-assessed at the P5 Track-B / multi-ref era, P1-14 anchor-strength
control surface folds into the blend-param decision.

started: 2026-06-11 12:35
finished: 2026-06-11 12:57 (implementation; ⚠ GPU verify owed)

**Goal (P1-17, added 2026-06-11; spec §13 M3.5 / §7.1 bg-axis note):** restore the §7.1
**background-diversity axis** that M3's img2img-only realization deferred — inpaint-method cells
**repaint the background around the held subject** (identity-safe: subject pixels preserved, so
no anchor needed → why M3.5 sits before M4). Scope box held: matting + bg-mask + mixed
realization ONLY.

**Worker (R162 monorepo-FIRST):** new `src/pipeline/postproc/birefnet/run_pipeline.py` — the
first postproc-class worker, following the `postproc/_common.PostprocManifest` convention
(save-then-raise, run_id/artifacts) + the zimage print markers (`[stage1] Pipeline loaded…`,
`  Image:`, `  Manifest:`, `[done]`). One image → THREE artifacts: `*_matte.png` (soft subject
matte), `*_cutout.png` (RGBA), `*_bgmask.png` (**white = repaint**, subject binarized at
`--threshold` 0.5 then **dilated `--dilate-px` 12** so inpaint never eats edges; optional
outward-only `--feather-px`, hard-zero inside the protected core). Variants
`birefnet`/`birefnet-hr` (HF `ZhengPeng7/BiRefNet[_HR]`, MIT, ungated, ~0.9 GB). Heavy imports
lazy (`-h` cheap). ⚙ Loaded via `transformers AutoModelForImageSegmentation`
(**trust_remote_code** — upstream model code ships in the HF repo, not vendored); deps already
in the shared .venv (torch+ROCm/torchvision/transformers 5.4/timm/kornia; scipy for
dilate/feather). **Vendored byte-identically** to
`pipelines/multistack/src/pipeline/postproc/{__init__,_common}.py + birefnet/*` (MD5-verified;
drift-guard parametrize extended; both copies `-h`-smoke-tested).

**Orchestrator:**
- `adapters/birefnet.py` — modes `("matte",)`; resolve `<root>/postproc/birefnet/run_pipeline.py`;
  argv `--input` + `--output-dir` + catalog flags; **manifest-as-truth** parse with the 3
  artifacts as outputs and their **`role` in `outputs_meta`** (UI/Stage-B pick `bgmask` without
  filename heuristics); coarse progress (load 0.6 / matted 0.9); streams via `collect_output`.
- `model_catalog.py` — 4th pipeline entry (variants + matte params: resolution/threshold/
  dilate_px/feather_px/dtype); POST_PARAMS deliberately NOT extended to it; model_name choices
  auto-fill covers it.
- **Tool-scoped weight gate** (models.json `postproc` block + `components.postproc_weights_status/
  fetch_postproc`): probed at the matte endpoint (412 + `?postproc=birefnet` fetch hint), **fails
  closed** on an unconfigured tool. ⚙ DELIBERATELY NOT a phase `models` entry — that would fold
  it into `weights_ok` and gate ALL of /generate on a matting model (the multi-preset over-gating
  lesson). Probe = `config.json` (transformers repo — no model_index.json).
  `POST /components/fetch?postproc=<tool>` added. VRAM estimate 4 GB. Registered in ADAPTERS +
  `/capabilities`.
- **`POST /assets/{id}/stage-b/matte`** — resolves the hero ★ (the version's `casting/cand_*.png`
  copy), catalog-validates params, weight 412 / VRAM / disk admission, submits ONE birefnet job
  (stage B, requester = version → lands in the Stage-B grid; dry_run → argv).
- **`/stage-b` `realize="img2img"|"mixed"`** (+ `bg_mask` out/-relative + traversal-guarded,
  `inpaint_strength` default **0.95**): mixed splits the recipe cells by their R113 method into
  **up to TWO batch jobs sharing one batch_id** — img2img sweep cells (bg-less, hero's setting,
  `strength` 0.55) + **inpaint cells** (mask_image = the hero's bgmask; per-cell background
  prompts restored by `recipe.build_recipe(realize="mixed")` — which existed since round 2,
  finally exposed). Params channel must validate for BOTH modes; post-passes ride on both jobs;
  `mixed` without `bg_mask` → 422 with the matte hint. Response/dry-run gain
  `realize`/`split`/`planned_jobs`. (npc_lite splits 9 img2img + 8 inpaint.)

**UI:** Stage-B bar gains **realize** select (mixed disabled until a mask exists, labeled "matte
first") + **[Matte hero]** button (→ "Matte ✓" once a `*_bgmask.png` exists); `bgMask` derived
from the newest done birefnet job scoped to the active version; stage-b body carries
realize/bg_mask; preview modal shows the split ("9 img2img + 8 inpaint"); matte tiles stream into
the Stage-B grid like any job (roles in output_meta).

**Tests: 151 passed** (135 → +16: new `test_birefnet_matting.py` — adapter argv/manifest-roles/
failed-stage/progress, catalog matte params, fails-closed weights, matte dry-run argv +
weight-412 + submit, mixed 422/dry-run-split/two-jobs with per-mode strengths + bg prompts;
catalog tests now 4 pipelines + birefnet drift guard; vendor-sync parametrize +2 files).
`tsc` + `vite build` clean.

⚠ **GPU verify owed (M3.5):** first real matte on the rig (BiRefNet remote code under
**transformers 5.4** is the one unproven seam — if it breaks, pin the arch or vendor the model
code), bgmask quality (dilate 12 enough? halo?), a real `mixed` npc_lite sweep (inpaint cells:
does sd3.5/zimage inpaint respect the mask at strength 0.95?), curation of inpaint cells into
ref_set (cells carry real backgrounds again). ⏭ Follow-ups: cutout reuse for refs/layering (M6),
`realize` exposure in the recipe bar presets per-cell override (R113 manual finalization — still
auto-only), matte caching/invalidation when the hero changes (re-matte manually for now).

### M3.5 review fixes (user findings; started 2026-06-11 ~16:35, finished 2026-06-11 16:52)

**✅ User confirmed BiRefNet WORKS on the rig** (matte → hero cutout + mask) — the
transformers-5.4 seam held. Four findings from their review, all fixed:

- **High — `bg_mask` accepted ANY file under out/.** The check was traversal + existence only;
  an API caller could pass a stale/wrong-asset mask (or any PNG) and silently poison the
  Stage-B/P2 corpus. Now stage_b resolves **provenance**: the name must appear in
  `output_names` of a **completed `birefnet` job whose `profile_version_id` == the resolved
  version**, AND carry **`output_meta.role == "bgmask"`** (the matte/cutout siblings are real
  outputs but inpainting against the soft matte would repaint the SUBJECT) — then the
  path/existence guard as before. dry_run still echoes unchecked (no-GPU preview). The old
  test that hand-dropped a mask file was reworked into a `_matte_job` provenance helper; +3
  negative tests (unprovenanced file / wrong role / other version's mask → 422).
- **Medium — `birefnet-hr` could clear the gate and die mid-worker.** Same class as the
  morning's params.model_name finding: the catalog serves both variants but the gate checked
  only the tool default. `postproc_weights_status`/`fetch_postproc` are now **variant-aware**
  (filter the models.json entries by catalog variant id; fails closed on an unconfigured
  variant), models.json gained the **`birefnet-hr` → ZhengPeng7/BiRefNet_HR** entry (⚙ entry
  ids mirror catalog variant ids — keep in lockstep), the matte endpoint gates on the
  **chosen** variant (`extra.model_name or default`), and the 412 hint + fetch carry
  `&postproc_variant=` (asking for the default never pulls the HR model).
- **Low — the UI ignored the role metadata it introduced.** `OutputMeta.role` added to the
  frontend types; `bgMask` is now selected by **`output_meta.role === "bgmask"`**, not the
  `_bgmask.png` filename suffix — the journal's "no filename heuristics" claim is now true on
  both sides.
- **Low — `/version` self-description drift:** `POST /assets/{id}/stage-b/matte` added to the
  `token_required` list.

**Tests: 155 passed** (151 → +4 net: variant-aware gate incl. HR-412-naming-the-HR-repo +
default-passes, 3 provenance negatives; 2 monkeypatched gate lambdas updated to the 2-arg
signature). `tsc` + `vite build` clean. ⚠ GPU items unchanged (real `mixed` sweep still owed —
now safe to run).

---

## M3 — Stage B + C → MVP done-line

started: 2026-06-06 10:03:31
finished: 2026-06-11 12:33 (✅ ACCEPTED — user GPU sign-off; see the acceptance entry below)

**Goal (spec §13.3 / P1-3, P1-4, P1-12, P1-16):** onboard `_img2img` + `sd35` (img2img +
inpaint) → run the **coverage-matrix dataset recipe** (auto-generated prompts) → grid →
**keep/cull** → curated `ref_set` → **Save AssetProfile** (Saved, not Finalized, R119). This is
the **MVP done-line** (single version, no anchor/postproc/Flux2-spike). Per the M2 review's locked
ordering: **contract-first** — freeze the coverage-cell metadata schema *before* any generation,
because P2's template captioner consumes it as a hard data contract (P1-16).

### Step 1 — freeze the coverage-cell metadata contract (P1-16) — finished 2026-06-06 10:15
The structured per-image fields P2's template captioner (kb-loom-p2 §6, **no VLM**) turns into a
deterministic caption — pinned up front, schema + tests, so it can't be evolved mid-milestone.
- **`coverage.py`** — the single source of truth: controlled vocabularies (the §7.1 axes)
  `SHOT_SIZES` {face_closeup, portrait, waist_up, full_body}, `ANGLES` {front, ¾ L/R, profile L/R,
  back}, `EXPRESSIONS` {neutral, smile, serious, sad, surprised}; `background` = free short
  descriptor ("varied per image"). Each value carries its **canonical caption phrase** so P1
  (metadata) and P2 (captions) can't drift. `build_caption(cell, trigger)` emits the **frozen**
  P2 caption shape `"<trigger>, <angle>, <shot-size>, <expression>[, <bg> background]"` (e.g.
  `mara_lw, left profile view, waist-up, neutral expression, market background`) — provided in P1
  so the contract is executable + drift-tested; P2-3 imports it verbatim. `CONTRACT_VERSION=1`:
  a key/phrase/output-shape change is a **breaking change to P2** → bump it.
- **`coverage_cell.schema.json`** — record-validation schema mirroring the vocab (`additionalProperties:
  false`); a test asserts its enums == `coverage.py` (lockstep).
- **`version.schema.json`** — `ref_set[]` promoted **string → object** (mirrors casting[]'s M2
  promotion): `{id ref_, file, coverage_cell (required), source_output, job_id, pipeline, method,
  seed, added_at}`. Legacy empty `ref_set`s unaffected; nothing populated it before M3.
- **Launch gate** — `coverage_cell.schema.json` added to `_P1_SCHEMAS` + a sample validated in
  `_check_p1_records`, so a broken contract **blocks P1 startup** (phase-scoped, like the other P1
  records).
- **Verified:** **+9 no-GPU tests** (`test_coverage_contract.py`: frozen vocab sets; caption shape
  matches P2 + omits empty bg + rejects bad cells; **schema enums == coverage.py**; schema validates/
  rejects; ref_set accepts a curated ref w/ cell + rejects one without). **50 backend tests pass.**
- ✅ **Contract signed off by the user** (2026-06-06): all four design decisions agreed
  (`build_caption` lives in P1; precise L/R phrasing; free-string background; cells on `ref_set[]`).

### Step 2 — Stage-B dataset recipe engine (P1-4, §7.1) — finished 2026-06-06 10:40
Generation-side + **mutable** (vs the frozen `coverage.py`): the coverage matrix loom auto-fills so
Stage B is coverage-driven, not freeform (R107/R109). `orchestrator/recipe.py`:
- **5 presets (R111)** as buckets `(shot_size, angles, expressions)` → matrix sizes verified close
  to the headline targets: **comprehensive 78** (keep 40–60, main chars) · **full_coverage 31**
  (keep 25–40, default) · **portrait_heavy 30** (keep 20–25) · **full_body 45** (keep 30–35) ·
  **npc_lite 17** (keep 15–20). Distributions follow §7.1 (identity from close-ups, proportions from
  full-body, angle variety incl. **profile + back** — fixes the front-only failure).
- `build_recipe(preset, *, character_clause, style_fragment, base_seed, backgrounds)` → **deterministic**
  ordered cells, each `{index, coverage_cell (validated vs the frozen contract), prompt, method, seed}`.
  **Prompt = `<style>, <clause>, <cell fragment>`** (no freeform typing, R107); the cell fragment reuses
  `coverage.py`'s canonical phrases **minus the trigger** (trigger is a P2-caption concept, not an image
  prompt). `character_clause` defaults to the asset's stub snippet (R112), held fixed across the set.
- **Per-cell method auto-pick (R113):** close-ups/portraits → `img2img` (pose/expression sweep from the
  hero); waist-up/full-body → `inpaint` (subject into varied scenes); overridable downstream.
  **Backgrounds** cycle `DEFAULT_BACKGROUNDS` (subject isolation, §7.1).
- **Verified:** **+8 no-GPU tests** (`test_recipe.py`: presets+metadata; cells valid+structured+seeded;
  method↔shot-size; varied backgrounds; determinism; counts in R111 ballpark; profile/back coverage
  present; unknown-preset/empty-clause raise). **58 backend tests pass.**
- ⏭ Next: onboard `_img2img`+`sd35` (img2img+inpaint) → Stage-B `/generate` wiring (recipe cell → job
  carrying its `coverage_cell`) → Stage-C curation (keep/cull → `ref_set`) → Save AssetProfile.

### Step 3 — onboard the Stage-B expansion adapters (sd35 + zimage img2img/inpaint) — finished 2026-06-06 11:20
**Finding that shaped the work:** `_img2img` is a **shared backend library** (`backends.py`/
`autodetect.py`), **not a CLI** — and neither `zimage` nor `sd35` imports it (their img2img/inpaint
modes are self-contained diffusers pipelines). So R121's "`_img2img`" = the img2img *capability*,
realized through the per-pipeline modes — **no new adapter, no extra vendoring** (the `multi`-vendored
`sd35` + the P0-vendored `zimage` already carry everything, incl. self-bootstrapping imports).
- **`adapters/sd35.py`** (1-page contract check) — a near-clone of `zimage`: **file-path invocation**
  (sd35 self-bootstraps `sys.path.insert(0, parents[1])` for `_artifact_id` + script-dir for bare
  stage imports, so the runner's default `cwd=parents[2]` + no PYTHONPATH suffices). Wired modes
  **img2img** (`--init-image` + `--strength`) + **inpaint** (`--init-image`+`--mask-image`); worker
  also supports t2i/cn-inpaint(-mc) (informational, not wired — casting is `multi`'s, CN is M6+).
  **Manifest-status-as-truth** parse_result + coarse `[stageN]` progress, mirroring zimage.
- **zimage img2img/inpaint wired:** `WIRED_MODES` `("t2i",)` → `("t2i","img2img","inpaint")` (build_argv
  already handled init/mask/strength since P0 — just advertised now).
- **Runner:** `sd35` registered in `ADAPTERS` + `VRAM_ESTIMATES["sd35"]=13` (cpu_offload+T5 peak).
- **`/generate`:** `mode` now honors `t2i|img2img|inpaint` for non-multi (multi stays `ideate`),
  **validated against the adapter's `WIRED_MODES`** (unwired mode → 400; `ideate` on non-multi → 400);
  img2img requires `init_image`, inpaint requires `init_image`+`mask_image` (→ 422); init/mask are
  **out/-relative names resolved + traversal-guarded** to absolute paths (dry_run echoes raw). New
  request fields `init_image`/`mask_image`/`strength`. `/capabilities` now lists zimage+multi+sd35.
- **Verified (no-GPU):** **+8 adapter tests** (`test_sd35_adapter.py`: img2img/inpaint argv, capabilities
  honesty, progress, parse_result completed/failed/no-manifest; zimage now advertises the Stage-B modes)
  → **66 backend tests pass**; **TestClient `/generate` smoke** (sd35 img2img/inpaint dry-run 200 w/
  correct argv; missing init/mask → 422; sd35 t2i unwired → 400; zimage img2img 200; `ideate` on
  zimage → 400; capabilities lists all three). `tsc`/`vite` untouched (no UI yet).
- ⚠ **Real-GPU verification owed** (like M2 step 2): an actual img2img/inpaint generation hasn't run on
  hardware yet. ⚠ **sd35 default model is `sd3.5-medium`** (ungated) — if a Stage-B sd35 cell requests a
  gated large variant, no pre-flight covers standalone sd35 yet (the preset gate is multi-only); fold a
  Stage-B weight check into Step 4 (gen wiring) or pin sd35 Stage-B to a cached model.
- ⏭ Next (Step 4): Stage-B generation wiring — `build_recipe` → N jobs, each carrying its `coverage_cell`
  + method→mode (img2img/inpaint) + the hero as `init_image` (resolved from the version's `casting/`).

### Step 4a — full model catalog (all variants + all adjustable params) — finished 2026-06-06 11:55
**User request:** a prototyping tool needs the *full* model surface, not our defaults — add
`sd3.5-medium` (downloaded, **ungated**, unique abilities) and expose **all versions** of flux2/sd35/
zimage with **all adjustable parameters** (even the ones earlier adapters hardcoded).
- **`model_catalog.py`** — the single source the UI/`/generate` read: per pipeline, every variant
  (id, repo_id(s), `gated`, per-model defaults + capabilities) + every tunable CLI param (name, flag,
  type, default, range/choices, applicable modes, note). Tally: **flux2 6 variants / 9 params**
  (klein 4b/9b/9b-kv + base 4b/9b + dev; Qwen3 text encoder, Mistral only on dev), **sd35 3 / 25**
  (medium **ungated** + large + large-turbo; incl. prompt-3, max-seq-len, SLG scale/start/stop, drop-t5,
  dtype, cpu-offload, the cn-inpaint(-mc) ControlNet params marked `advanced`/M6+), **zimage 2 / 15**
  (turbo + base, both **ungated/Apache**; incl. cfg-normalization, cfg-truncation, attention-backend, dtype).
- **`GET /models`** serves the catalog (catalog_version + per-pipeline variants/params/modes/loom_access).
  flux2's `loom_access` notes it's **multi-driven** today (standalone flux2 = the §11 spike, deferred).
- **Verified:** **+8 no-GPU tests** (`test_model_catalog.py`: 3 pipelines; variants/params well-formed;
  `sd3.5-medium` present + ungated; zimage ungated; **drift guard** — catalog variant ids == the vendored
  `*_MODEL_INFO` keys, regex-extracted from source, no heavy import, per pipeline). **74 backend tests pass.**
### Step 4b — catalog-validated params channel + per-model weight pre-flight — finished 2026-06-06 12:30
**User decision (AskUserQuestion):** a single catalog-validated `params` dict (not ~40 typed fields).
- **`model_catalog.validate_params(pipeline, mode, raw)`** — every key must be a known catalog param,
  right type, in range, applicable to the mode (else `CatalogError`→422); structural only (per-MODEL
  applicability left to the worker, which warns+ignores — keeps prototyping frictionless).
  **`model_catalog.emit_argv(pipeline, params, mode)`** — the **single flag-mapping source**: emits
  `--flag value` (or bare for flags) for every catalog param present, mode-gated, None skipped.
- **Adapters refactored:** `zimage`/`sd35` `build_argv` = fixed args (prompt/mode/output-dir/device) +
  `emit_argv(...)` — so adding a tunable = a catalog entry, not adapter edits (behavior-preserving;
  the M3 adapter tests still pass). `multi` stays bespoke (preset-driven).
- **`/generate`:** new `params: {}` field; for zimage/sd35 it's validated + merged into the flat param
  set (overrides top-level on clash); **rejected for `multi`** (400 — casting is preset-driven, no
  silent drop). Added a **per-model weight pre-flight** (`components.image_model_present` probes the
  chosen variant's repo) → 412 if the standalone image model isn't cached (**closes the gap the
  multi-only preset gate left** — e.g. a Stage-B sd3.5-medium that isn't downloaded).
- **Verified:** **+4 catalog tests** (validate accepts/drops-None, rejects unknown/type/range/mode;
  emit maps flags + respects mode + skips false flags) → **78 backend tests pass**; **TestClient smoke**
  (sd35 img2img with `drop_t5`/`dtype`/`skip_layer_guidance_scale` → correct argv; zimage `cfg_normalization`
  /`attention_backend`; unknown param + wrong-mode param → 422; **multi+params → 400**). README swept.
- ⏭ Next (the remaining Step-4 piece): the **recipe→Stage-B batch** — see the design fork below.

### Step 4c — recipe→Stage-B batch (`POST /assets/{id}/stage-b`) — finished 2026-06-06 13:10
**User decision (AskUserQuestion):** **img2img-only for M3** — realize every recipe cell via img2img
from the hero; inpaint background-diversity + true angle coverage need masking (M6)/video-sketch (M7)/
the Flux2 spike (later). Honest to the §12 done-line-without-postproc guardrail.
- **`assets.resolve_hero(ws, asset_id, version_id)`** → `(version, hero_entry, hero_abs_path)`; raises
  if no hero is starred (Stage B can't start without one) — the Stage-A ★ pick seeds Stage B.
- **`POST /assets/{id}/stage-b`** (`StageBRequest`): pick a `preset` + (optional) `character_clause`
  (defaults to the version's `prompt_template`, R112) + pipeline (zimage|sd35) + strength/width/height
  + advanced `params`. Builds the recipe (style auto-prepended into each cell prompt, R104), then fires
  **one img2img job per cell** from the hero — each job carries its frozen **`coverage_cell`** (new
  first-class job field → `submit()` + `job.schema.json`, alongside profile_version_id/stage) + `stage="B"`.
  Per-model weight pre-flight (412), VRAM/disk admission, `dry_run` previews the recipe + first-cell argv.
- **Verified (no-GPU):** **+2 tests** (`resolve_hero` returns the starred ★ + its casting/ path; raises
  without a star) → **80 backend tests pass**; **TestClient smoke** (npc_lite dry-run → 17 img2img jobs,
  hero resolved from `casting/`, first cell's coverage_cell correct, argv mode=img2img with `--init-image`;
  no-hero → 409; no-clause/template → 422).
- ⚠ **Real-GPU verification owed** (Stage B hasn't generated on hardware): an actual img2img batch +
  the curated-set quality is the M3 acceptance check, pending the user's GPU. ⚠ img2img from a single
  front hero gives limited angle change (expected — profile/back coverage is the deferred-methods' job).
- ✅ **Step 4 COMPLETE** (model catalog + params channel + Stage-B generation). ⏭ Step 5: Stage-C
  curation (keep/cull → `ref_set` w/ coverage_cell) + Save AssetProfile (Saved, not Finalized) = done-line.

### Step-4 review follow-ups (2026-06-06, 2 findings, both valid — pre-Step-5)
- **Med — unknown `model_name` bypassed the standalone pre-flight.** `model_name` was an `enum`
  param with **no `choices`**, so `validate_params` couldn't reject it, and the cache pre-flight only
  fired `if find_variant() returns a variant` — a bogus model returned `None`, skipped the check, and
  reached the worker (argparse-fails the subprocess after a spawn). **Fix:** (a) `model_name.choices`
  is now auto-filled from each pipeline's variants (so the enum check rejects it + `/models` advertises
  the set); (b) new `model_catalog.validate_model()` raises on a set-but-unknown model; `/generate` +
  `/stage-b` call it up front → **422** (even on dry_run). *Verified: bogus model top-level + via params
  → 422; valid → 200. +2 tests → 82 backend pass.*
- **Low/Med — frontend API types were pre-M3.** `client GenerateRequest` only allowed `zimage|multi`
  and lacked mode/sd35/model_name/init_image/mask_image/strength/params; `ProfileVersion.ref_set` was
  still `string[]` though the schema now requires curated **ref objects with `coverage_cell`** (would
  trip Step 5's curation UI). **Fix:** `orchestrator.ts` GenerateRequest extended (pipeline+`sd35`, mode,
  model_name, num_steps, guidance_scale, negative_prompt, init/mask/strength, `params`); added
  `CoverageCell` + `RefItem` types; `ref_set: RefItem[]`. `tsc` + `vite build` clean.

### Step 5 — Stage-C curation + Save AssetProfile (the MVP done-line) — finished 2026-06-06 14:00
The done-line payload: keep/cull Stage-B candidates into the curated `ref_set` (the future LoRA
corpus, R107), then Save. **The backend MVP done-line is now reachable** (cast → expand → curate →
save a reopenable AssetProfile), single version, no anchor/postproc (§12 guardrail).
- **`assets.keep_ref`** — keep a Stage-B candidate into `ref_set`: validates its `coverage_cell`
  (frozen contract), **copies the image (+ sidecar) into the version's `refs/`** (self-contained,
  survives job deletion / out/ pruning — mirrors casting/), records `{id ref_, file, coverage_cell,
  source_output, job_id, pipeline, method, seed, added_at}`. Idempotent on `source_output`.
  **`remove_ref`** (cull) drops the entry + deletes the refs/ copy. **`save_profile`** persists the
  editable identity clause (`prompt_template`) + re-stamps `saved_at` — **Saved, not Finalized**
  (R119; refuses if finalized). **`ref_file_path`** = traversal-guarded serving.
- **Endpoints** (token-gated): `POST /assets/{id}/refs/keep` (reads the job → its coverage_cell +
  provenance; **422 if the job has no coverage_cell** — only Stage-B outputs are curatable),
  `POST /assets/{id}/refs/cull`, `POST /assets/{id}/save`, + `GET /assets/{id}/refs/{file}` serving.
- **Verified:** **+5 no-GPU tests** (`test_curation.py`: keep records ref w/ cell + copies image +
  survives source deletion; idempotent; rejects invalid cell; cull removes entry+file; **Save persists
  the clause + the curated set survives a fresh `Workspace.open` reload, finalized False**) → **87
  backend tests pass**; **TestClient smoke** (keep 200 w/ cell → serve ref 200 image/png → save 200
  Saved-not-Finalized → cull 200 → keep-non-Stage-B 422). README swept.
- ✅ **Backend done-line COMPLETE.** ⏭ Step 6: the **UI** (bootstrap strip B/C — recipe/model/param
  controls + keep/cull curation grid + Save) + **real-GPU acceptance** (the actual cast→expand→curate
  →save run on hardware = the M3 acceptance, still owed). Then M3 closes.

### Step 6 — bootstrap-strip UI (Stages A·B·C) — finished 2026-06-06 14:40
The visible half of the done-line. `App.tsx` + `orchestrator.ts` + `styles.css`:
- **Stage switcher** (A·Casting / B·Expansion / C·Curation) in the asset stage header; the grid is
  **stage-scoped** (A → casting jobs `stage=A`; B/C → the Stage-B dataset `stage=B`).
- **Stage B — recipe bar:** preset picker (the 5 R111 presets), img2img pipeline (zimage|sd35),
  strength, an editable **character clause** (defaults to the saved prompt template), **[Generate
  Dataset ▶]** → `POST /stage-b` (disabled w/ a hint until a hero ★ is starred; jumps to Stage C).
- **Stage C — curation:** each done candidate gets a **keep ✓ / cull ✕** toggle (green ring when
  kept; mirrors the Stage-A ★); a **Save bar** shows `kept N` + the identity-clause input +
  **[Save AssetProfile]** → `POST /save`. Client fns `stageB`/`keepRef`/`cullRef`/`saveProfile`
  (+ the kept-set derived from `version.ref_set` by `source_output`).
- **Verified:** `tsc` + `vite build` clean; **87 backend tests still pass**. Frontend types already
  M3-current (the earlier review fix). ⚠ **Real-GPU acceptance is the remaining check** — the user
  will do a thorough end-to-end run (cast → star → expand → keep/cull → Save → reopen) on hardware;
  the no-GPU layers (unit + TestClient smokes for every endpoint) are all green.
- ✅ **M3 implementation COMPLETE** (steps 1–6); **acceptance = the user's real-GPU pass.**

### Step-6 review follow-ups (2026-06-06, 3 findings)
- **Med — Stage-C keep/star weren't API-scoped to the producing asset/version (FIXED).** `refs/keep`
  (and, same bug, `casting/star`) only checked done/coverage_cell/output, then wrote into whatever
  `asset_id`/`version_id` the caller passed — a stale/manual call could keep Asset A's Stage-B output
  into Asset B's `ref_set`. **Fix:** shared `_require_job_owned_by(ws, asset_id, version_id, job)` —
  resolves the target version + asserts `job.profile_version_id == it` (else **409**); both endpoints
  now pass the resolved vid. **+2 TestClient tests** (`test_curation_scoping.py`: keep A's job into B →
  409, into A → 200; star cross-asset → 409). The UI was already safe (grid filters to the active
  version), but the backend now enforces it. **89 backend tests pass.**
- **Low/Med — Stage-B model selector added to the UI (FIXED).** `StageBRequest` carried `model_name`
  but the UI sent only preset/pipeline/strength/clause. Added a **model-variant selector** (fetches
  `GET /models`; lists the chosen pipeline's variants, gated ones marked 🔒, "default" = worker's);
  resets when the pipeline family changes; sends `model_name`. (The full long-tail `params` controls —
  SLG/cfg-norm/dtype/… — remain API-only for now; a per-param control surface is later UI polish.)
- **Med — P1-12 curation throughput is NOT delivered; kept OPEN (per reviewer).** M3's Stage-C is a
  valid steel thread (per-tile keep/cull, kept count, Save) but **not** the spec's P1-12 "bulk
  select/reject, keyboard nav, filter-by-coverage-cell" (kb-loom-p1 §16) — which matters at the
  Comprehensive (~78) scale. **MVP done-line steel-thread = complete; P1-12 (curation throughput) +
  the advanced-params UI remain open P1 follow-ups**, to land before/with the larger-recipe workflows.

### Acceptance-run bug — vendored `multi` missing `_img2img` (FIXED 2026-06-09)
First real GUI multi cast failed instantly: `failed … (multi/ideate): no multi manifest produced`
(rc=1, ~0.1 s). Root cause: **the M2 vendoring (`multistack`) copied `multi/flux2/sd35/zimage` but
not `_img2img`** — and `multi/arch_batch.py` does `from .._img2img.autodetect import …`, so every cast
died with `ModuleNotFoundError: No module named 'pipeline._img2img'`. (zimage casts were fine —
they don't import it; my M2 "neither zimage nor sd35 imports `_img2img`" note was true but missed that
**`multi` does**.) The no-GPU adapter tests checked `build_argv`/`parse_result` but never *imported*
the vendored package, so it slipped to runtime. **Fix:** vendored `_img2img` into
`pipelines/multistack/src/pipeline/_img2img/` (self-contained — stdlib + its own submodules) +
**a new import-graph guard** (`test_vendored_multi_imports_as_module`: subprocess `-m
pipeline.multi.run_pipeline -h` from the vendored src, asserts rc 0 — exactly the runner's invocation).
**90 backend tests pass.** *(`fast` cast now imports clean; the user re-runs on GPU to confirm.)*

### Acceptance-run UX fixes (2026-06-09) — multi looked "hung", "no tile on the grid"
With multi finally running, two visibility issues surfaced (GPU at 100%, but no progress + no tile):
- **Worker stdout was block-buffered → no progress/log for minutes.** Python block-buffers stdout to
  a pipe, so a running multi cast (which only prints at coarse stage markers anyway) showed nothing —
  empty per-job log, frozen progress bar. **Fix:** the runner now spawns workers with
  `PYTHONUNBUFFERED=1` (inherited by multi's stage_runner sub-subprocesses too) → live streaming.
- **The grid is derived from `jobs`, which was only fetched by the cast-triggered fast poll** — never
  by the steady 2 s health probe. So selecting an asset (or any timing slip) left `jobs` stale/empty →
  **empty grid even with jobs running server-side**. **Fix:** the health probe now also `listJobs()`
  every 2 s (sets jobs/counts/paused/vram), so the grid always reflects reality regardless of the fast
  poll. `tsc`/`vite` clean.
- ⚠ **Separately, flux2 on 16 GB ROCm is very heavy** (klein-4b flow + FLUX.2-dev AE + a 4B Qwen3 text
  encoder): ~12 min for one candidate, likely memory-thrashing — multi (flux2-inclusive) is the slow
  path on this rig. **The done-line is fully exercisable via `zimage` casting (~40 s/img, proven);**
  multi/flux2 perf tuning (offload) is a later, non-blocking item.

### Codebase review (2026-06-10 08:56, Claude Code) — pipeline-feedback + parameter-surface findings
Full review of `orchestrator/` + `app/src/` + the vendored `pipelines/` stack, prompted by two user
complaints: (1) *"queue shows paused but nothing is running"* and (2) *"runs are templated — I want to
see/change all parameters before generating"*. Findings (fixes follow in the next entry):

**Issue 1 — pause/feedback (three stacking causes, all confirmed in code):**
- **Resume-paused (R88) fires on every launch/open and its queued work is often invisible.** The grid
  is scoped (active asset + stage, or the sandbox's last batch), so leftover queued jobs from another
  session/asset/stage exist only as a dock count — "⏸ paused (N queued)" with zero tiles. Exactly the
  reported symptom. There is **no queue list view** anywhere in the UI.
- **`paused` is sticky with an empty queue**: cancel/delete of the last queued job never re-evaluates
  it (`runner.cancel`/`delete`) → "⏸ paused (0 queued)" forever. The UI has **no pause button** —
  every "paused" the user ever sees is the resume-paused machinery.
- **Submitting into a paused queue is silent**: `/generate` enqueues happily; tiles sit at "queued…"
  with no hint that an unpause is needed.
- Supporting feedback gaps: job `note` ("OOM — auto-retry 1/1", "re-queued after graceful shutdown")
  is **never rendered**; `log_tail` only refreshes on coarse stage markers (a multi cast sits at 20%
  with a frozen tail for minutes); the Inspector shows the tail only for failed/canceled; the dock
  claims "▶ running (N)" off `queued+running` (true even when the disk gate holds dispatch); the
  VRAM meter is hardcoded `0.0/16G`.
- **Efficiency (the structural half):** every image pays a **full model load** — each job is a fresh
  one-shot subprocess (zimage/sd35 CLIs are one-prompt-one-image). Stage-B `full_coverage` = **31 ×
  (pipeline load + generate)**, i.e. 60–80% of wall time is reloading the same model; a multi cast
  loops **seed-outer × pipeline-inner** (`candidates.py`) = max interleave, `N×3` loads. Highest-
  leverage fix: a **batch mode in the per-pipeline workers** (`--jobs-file`: load once, loop cells)
  → Stage-B becomes one *dataset job* per model. ⚠ R162: vendored pipeline code stays unedited — the
  batch mode lands in the parent monorepo `src/pipeline/` first, then re-vendor. Deferred (own
  milestone-sized item), journaled as the agreed direction.

**Issue 2 — parameter surface: the backend is ahead of the UI.** `GET /models` (full catalog with
type/min/max/choices per param), the catalog-validated `params` channel, and `dry_run` (exact argv)
all exist — **none surfaced**: Stage-A casting exposes only prompt/count/preset (no width/height/
seed/steps/guidance/negative/variant, though `GenerateRequest` accepts them all); `multi` hard-rejects
`params` (preset-only, members hardcoded); Stage B hides width/height/base_seed/params and the ~31
generated prompts are never shown before committing the GPU; the style fragment silently rewrites the
prompt server-side and the Inspector never shows resolved `params`. Plan: **parameter drawer** driven
by `GET /models` + a **dry-run pre-flight review** (resolved prompt, model, planned jobs, argv) +
Inspector provenance. (Matches the open "advanced-params UI" follow-up from the Step-6 review.)

**Bugs found along the way:**
- **`main.py` `refs/keep` error path raises `NameError`** — `except (…, coverage.CoverageError)` but
  `coverage` is never imported in main.py → a keep failure that should 400 returns a 500.
- **Cancel doesn't free the GPU for multi casts**: `proc.terminate()` kills the direct worker but
  `stage_runner`'s **grandchild** (the process actually holding the model) survives on Windows and
  keeps generating. The process-wide Job Object only reaps on *orchestrator* death, not per-job cancel.
- `_canceled` set never pruned on finalization (slow leak).
- UI double-polls `/jobs` (2 s health probe + a second 1.2 s loop that never stops while paused).

### Review fixes (started 2026-06-10 08:56, finished 2026-06-10 09:17, Claude Code) — feedback + parameter surface
Implemented the fixable findings above (the batch-mode worker + the curation-throughput UI stay
open as their own work items). **99 backend tests pass (90 → 99, +9 `test_queue_feedback.py`);
`tsc` + `vite build` clean.**

**Backend (`runner.py`, `main.py`, `adapters/multi.py`, `queue.schema.json`):**
- **`pause_reason`** (`"resume"` resume-paused load · `"user"` explicit pause · null) — tracked in
  the runner, persisted in the `queue.json` envelope (schema gains the optional enum), surfaced via
  `state()` → `/jobs` + `/queue/pause|unpause`. ⚙ The UI now distinguishes "resumed from last
  session" from a deliberate pause.
- **Sticky-pause auto-clear** — `_clear_pause_if_empty_locked()`: canceling/deleting the **last
  queued** job drops the paused state (it exists to hold queued work for review, R88 — with nothing
  held it just reads as a stuck pipeline). Wired into `cancel()` + `delete()`.
- **Cancel = kill the worker *tree*** — `_kill_tree()`: Windows `taskkill /PID … /T /F` (POSIX
  terminate→kill). Fixes the multi-cancel hole: the per-pipeline **grandchild** kept generating on
  the GPU after `terminate()` of the direct child (the process-wide Job Object only reaps on
  orchestrator death). + a real tree-kill regression test (parent+grandchild both dead).
- **Live `log_tail`** — updated on **every** worker line (0.5 s throttle), not only at coarse stage
  markers; a multi cast no longer shows a frozen tail for minutes.
- **`make_progress(params)` adapter hook** — optional stateful progress factory; the runner prefers
  it over the stateless `progress(line)`. `multi` implements it: counts per-candidate completions
  (`[done] Pipeline completed`) against `num_candidates × 3` for a real fraction; a failed candidate
  still advances on the next `[stage_runner] $` spawn banner. (Found en route: the old
  `"minted session"` marker **never matched** — the real print is "minted **new** session".)
- **`_canceled` pruned at every terminal transition** (cancel-queued, cancel-raced, done/failed).
- **`coverage` imported in `main.py`** — `refs/keep` failures now 400 as intended instead of a
  NameError 500 (+ regression test via a monkeypatched `keep_ref`; also added the missing `recipe`
  import to the direct-run fallback branch, same latent crash).

**UI (`App.tsx`, `orchestrator.ts`, `styles.css`):**
- **Single poller** — the 2 s health probe is now THE `/jobs` poll (`applyJobs` is the one place
  queue state lands); actions trigger a one-shot `refreshJobs()` for instant transitions. The
  second 1.2 s loop (which never stopped while paused) is gone.
- **Honest dock** — `▶ running N · M queued` only when something IS running; `⛔ held (disk)` when
  the guard holds dispatch; `⏸ paused (resumed last session) — N queued` with the reason; the
  hardcoded `VRAM 0.0/16G` meter removed (was pure noise).
- **Queue panel** — `jobs ▾` in the dock lists queued/running jobs (id, pipeline/mode/stage,
  batch i/N, live %, the runner's `note` — OOM retry etc. was previously never rendered) with
  per-row cancel. The "paused but where are the jobs?" mystery is now one click.
- **Paused banner** — whenever the queue is paused, the stage shows "⏸ Queue paused (reason —
  N queued); new jobs will wait — [unpause ▶]". Submitting into a paused queue is no longer silent.
- **Inspector provenance** — model, mode/stage, `note`, `coverage_cell`, collapsible **resolved
  prompt** + **params (as run)** (style prepend finally visible), and the **live log tail while
  running** (was failed/canceled only).
- **⚙ params drawer** (issue 2, first slice) — on the Stage-A bar **and** the Stage-B bar: every
  non-advanced tunable for the pipeline+mode rendered from `GET /models` (int/float bounded inputs,
  enums incl. the model-variant picker, flags, strings; unset = model default, nothing sent).
  width/height/seed/steps/guidance/negative go top-level (API-validated), long-tail via the
  catalog `params` channel; multi gets its wired width/height/seed. Stage-B maps the seed control
  to `base_seed`.
- **Preview = dry-run pre-flight** (issue 2) — a [Preview] button beside Cast/Generate and
  Generate Dataset: shows the **resolved prompt**, planned job/candidate count, output dir, and
  the **exact worker argv** (Stage B: planned_jobs + kept target + hero + first cell + argv) in a
  modal with **[Run ▶]** firing the identical request. New client fns `generatePreview` /
  `stageBPreview` (+ `StageBPreview`/`GeneratePreview`/`ParamSpec`/`PauseReason` types).
- README cancel contract line updated (kill → **tree** kill).

⚠ **Real-GPU verification owed** for: tree-kill canceling an actual multi cast mid-candidate
(unit test proves the mechanism, not the GPU release), the per-candidate progress fractions on a
real cast, and the params drawer end-to-end (a dry-run argv check is covered by TestClient).
⏭ **Open follow-ups from the review (deliberately not done here):** the **batch-mode worker**
(`--jobs-file`: load once, loop cells — kills the 31× model reload in Stage B; change lands in the
monorepo `src/pipeline/` first per R162, then re-vendor), per-member multi params, P1-12 curation
throughput, slim `/jobs` list + on-demand detail.

### User-feedback round 2 (started 2026-06-10 ~12:30, finished 2026-06-10 13:50, Claude Code)
Four user findings on the previous round + a test-location request. **109 backend tests pass
(99 → 109, +10 `test_multi_params.py` + reworked `test_recipe.py`); `tsc` + `vite build` clean.**
*(Clarified for the journal: "interim multi results" ≠ the open "batch-mode worker" item — the
latter is Stage-B model-reload efficiency; interim results are visibility into a running cast.)*

**1 — multi param drawer was just width/height/seed → now the full batch surface.**
- **`model_catalog.MULTI_PARAMS`** (+ `MULTI_ENTRY`, `catalog_for_api()`): all 18 tunables the
  `multi` batch CLI accepts — width/height/seed + **`clean`/`polish` opt-in toggles** with their
  full sub-param sets (backend zimage/sd35/flux2-img2img, model override, prompt, strength,
  negative, cfg-norm, polish seed) + `img2img_batching`. Served as `GET /models` → `multi`;
  `params()`/`validate_params()` honor the pseudo-pipeline.
- **`/generate`** now ACCEPTS the multi params channel (was a hard 400): catalog-validated, plus
  the worker's opt-in **footgun guard pre-spawn** (clean_*/polish_* without the master toggle →
  422, mirroring the CLI's loud failure — never a silent ideate-only no-op after a GPU spawn).
- **`adapters/multi.py` build_argv**: picks the **`batch` subcommand when clean/polish is on**
  (else `ideate`, unchanged); all tunables flow through `emit_argv("multi", …)` (single flag map).
  `parse_result` collects **`cleaned[]`/`polished[]` outputs into the pool** alongside ideate
  (each pass = its own starrable tile). `make_progress` denominator now = `N×3 × passes`.
- ⚠ **NOT exposable without a worker CLI change (vendored, R162): per-member ideate model/steps/
  guidance** — the `fast|refined` preset fixes those inside `pipeline/multi`. Needs a monorepo
  CLI extension + re-vendor; noted in `MULTI_PARAMS`' header for when that lands.

**2 — interim results + scrolling + Inspector + prompt order.**
- **Interim cast results:** new adapter hook **`collect_output(line)`** — the runner resolves each
  announced image (ideate children stream `  Image: <path>` live; the piped clean/polish passes
  surface via `[batch] clean|polish OK … -> <path>`), serve-guards it under `out/`, and appends to
  the job's new **`partial_outputs[]`** (schema'd; reset on every re-queue path). The grid expands
  a running cast into **one tile per landed candidate + a placeholder tile** carrying the
  progress bar/cancel — images appear as they finish, not all-at-once at the end.
- **Scroll containment:** `.stage` gets `overflow-y:auto; min-height:0` — the grid now scrolls
  inside its pane instead of scrolling the whole page (titlebar/rails/dock stayed put before only
  until the grid outgrew the window).
- **Inspector follows the selection:** the selected cell's `output` is passed in — preview shows
  THAT candidate (was: always the pool's first), with per-candidate pipeline/seed parsed from its
  `…/ideate/<pipeline>/seed_<n>/…` path (clean/polish outputs labeled as their pass).
- **Prompt order (user decision, amends R104's wording):** the style fragment is now **appended**
  — `/generate` builds `<user/character prompt>, <style>` (front tokens dominate; style mostly
  restates the look). README + UI labels swept.

**3 — squashed B/C tiles + hero visibility.** Tiles were a fixed 16:9 box (`aspect-ratio` CSS) —
right for 1280×720 casts, cropping 1024×1024 Stage-B images. **Per-tile aspect now follows the
job's own width/height params.** Stage B also shows a **base-image strip**: the starred Stage-A
hero ★ thumbnail(s) (served from the version's `casting/`) with an explainer that every cell
img2img's from it.

**4 — Stage-B prompts fought the base image.** Two recipe changes (generation-side, the frozen
coverage contract untouched): (a) **cell fragment now LEADS** — prompt = `<cell fragment>,
<clause>, <style>` (the coverage terms kept losing weight at the tail of a long clause);
(b) **no background terms for img2img-realized cells** — the hero already fixes the setting, a
"plain studio background" clause only fights it. Cells carry `background=""` (contract-legal;
P2's caption simply omits it); the cycled `DEFAULT_BACKGROUNDS` pool survives behind
`build_recipe(realize="mixed")` for the M6+ inpaint path (subject isolation, §7.1), drift-tested
both ways.

**5 — tests now run in `loom/testing/`** (user request; their live project is `loom/test/`):
`tests/conftest.py` **overrides pytest's `tmp_path`** to `<monorepo>/loom/testing/<test-name>/`,
wiped at session START so the latest run's projects/queues/outputs stay inspectable (no more
AppData temp archaeology). Applies to every existing fixture automatically.

⚠ **Real-GPU verification owed:** a clean/polish cast end-to-end (argv + parse are unit-proven),
interim tiles streaming on a live cast, and the recipe-prompt quality change on a real Stage-B
sweep. ⏭ Open: per-member ideate params (monorepo CLI), the batch-mode worker, P1-12.

### User-feedback round 3 (started 2026-06-10 ~14:30, finished 2026-06-10 16:24, Claude Code)
Three asks: **build the batch-mode worker** (#1), **closeable projects** (#2), **sandbox as an
experimentation surface** (#3). **121 backend tests pass (109 → 121, +13 `test_batch_worker.py`,
1 reworked); `tsc` + `vite build` clean.**

**1 — THE BATCH-MODE WORKER (the 31× model-reload fix) — implemented end to end.**
- **Worker side (R162 honored: landed in the monorepo `src/pipeline/` FIRST, then re-vendored
  byte-identically** to `pipelines/zimage/` + both `pipelines/multistack/src/pipeline/{zimage,sd35}/`
  mirrors — a new **vendor-sync drift-guard test** hashes vendored copies against the monorepo
  source so a missed re-vendor fails CI): `run_pipeline.py --jobs-file jobs.json` on **zimage AND
  sd35** — JSON `{shared:{…run() kwargs…}, items:[{prompt, seed, meta, …overrides}]}`; the pipeline
  **loads once** and loops generate+save per item. Per-item failures record + continue; load-bound
  keys (mode/model/dtype/offload/…) are shared-only; per-item PNG + sidecar manifests
  (`<pipe>_<ts>_i<idx>_s<seed>.png`) + a **`<pipe>_batch_<ts>.json` summary manifest** (statuses,
  durations, echoed `meta`). A **`STOP` file** in the out dir (checked between items) = graceful
  stop: finish the current image, skip the rest, completed items stay valid (exit 0).
- **Orchestrator side:** a job whose params carry **`batch_items`** becomes ONE `--jobs-file`
  invocation — shared `adapters/_batch.py` (jobs.json writer with catalog-flag→kwarg inversions
  e.g. `no_cpu_offload`→`cpu_offload:false`; batch-manifest-as-truth parse; per-item progress;
  `Image:` interim collection). `CompletionRecord` gains **`outputs_meta`** (parallel to outputs)
  → runner surfaces **`result.output_meta[output]`** = each image's `{coverage_cell, seed, method}`.
- **Stage B now fires ONE batch job** (was: one job per cell — 17–78 spawns × full model loads):
  each recipe cell rides as an item with its frozen `coverage_cell` in `meta`; **Stage-C keep
  resolves the cell per-OUTPUT** (`output_meta`, with the legacy per-job `coverage_cell` still
  honored). Interim tiles stream in per cell (round-2 plumbing reused verbatim). **`POST
  /jobs/{id}/stop`** + a **⏹ button** in the queue panel = graceful mid-dataset stop (✕ cancel
  still kills + discards). Dry-run/preview shows "1 batch job · N cells (model loads once)".
- ⚙ The worker prints `[item i/N]` + per-item `  Image:` lines → real per-item progress + the
  grid streaming; `[batch-done] ok/failed/skipped`.

**2 — Closeable projects (easy, done).** `RUNNER.unbind()` (refuses while running, like bind) +
`projects.close_project()` (clears the pointer's `active_project` so a relaunch does NOT
auto-reopen — an explicit close is a user decision; the project stays in the recent list) +
token-gated **`POST /project/close`** + a **Close** button in the titlebar. Non-destructive: the
queue/outputs stay on disk; reopening resumes paused (R88). Every project-scoped surface already
gated on `ws is None` (409s / disabled buttons), so the app degrades exactly as it did pre-open.
⏭ **Deferred with the user's blessing:** the native folder picker (Browse is still a text prompt)
— a Tauri dialog-plugin convenience item, noted in the README.

**3 — Sandbox = the experimentation surface.** The sandbox was the P0 smoke grid (locked to
zimage t2i); no further spec plans existed for it. Reframed per the user: **all three pipelines
are now selectable unscoped** — `multi` casting (works without an asset; backend always allowed
it), `zimage` t2i, and **`sd35` t2i (newly wired** — the worker always supported it; the adapter
fenced it to Stage-B img2img/inpaint). The ⚙ params drawer follows the selected pipeline (its
full catalog surface) and resets on switch; prompts/styles/seeds/all tunables can be explored
without touching an AssetProfile. Sandbox jobs stay unscoped (requester = project, no stage) —
nothing pollutes casting/curation.

**Tests:** +13 (`test_batch_worker.py`): vendor-sync drift guard (×2), batch argv + jobs.json
(incl. flag inversions ×2), batch-manifest parse (outputs+meta / stopped-keeps-completed),
batch progress/collect, **stage-b = 1 batch job of 17 items w/ cell-first prompts**, stage-b
dry-run shape, **keep-from-output_meta (200) + meta-less output (422)**, stop-file write (+409
when not running), **close→409s→reopen roundtrip**; sd35 capabilities test updated for t2i.

⚠ **Real-GPU verification owed (the next acceptance pass):** a real Stage-B batch sweep (load-once
timing + interim streaming + ⏹ stop mid-run), sd35 t2i from the sandbox, and the round-2 items
(clean/polish cast, recipe prompt quality). ⏭ Open: per-member multi ideate params (needs a
monorepo `multi` CLI extension), P1-12 curation throughput, native folder picker, slim `/jobs`.

### Round-3 review follow-ups (2026-06-11 06:20, Claude Code — 3 findings, all valid, all fixed)
External review of the batch-mode work. **123 backend tests pass (121 → 123); `tsc`+`vite` clean.**
- **Med — a partial/stopped batch could read as a fully-green done.** `parse_batch_result`
  required only ≥1 output and masked `stopped` as `completed`; the worker's ok/failed/skipped
  counts never left the manifest — 1 ok / 77 failed looked successful. **Fix:**
  `CompletionRecord.batch` = `{count, ok, failed, skipped, status}` → `result.batch`;
  `manifest_status` now carries the REAL status (`stopped` stays visible); the runner stamps the
  job **note** `partial dataset: ok/count cells (F failed, S skipped[ — stopped early])`
  (`_batch.partial_note`, also in the queue panel + Inspector); the UI shows a **⚠ partial-dataset
  banner in Stages B and C** ("coverage matrix is incomplete — re-run Stage B or curate what's
  there"). Per-item sidecar `manifest_path` now also rides `outputs_meta`. *(`ok` semantics
  unchanged by design: usable images stay a done job — the partiality is now loud, not failing.)*
- **Med — lineage was one edge per JOB, not per output.** The Stage-B batching (and multi pools
  before it) yield N outputs, but `record_output` wrote only `result.output_name` — the other N−1
  images lost provenance despite R98 "every generated image traceable". **Fix:** `make_edges` —
  **one edge per `output_names[]` entry** (keyed `job_id` + `output_file`), each preferring its
  per-item sidecar manifest from `output_meta` (job-level manifest as fallback); replace-by-job_id
  stays retry-idempotent; `remove_edge` (delete flow) drops ALL of a job's edges.
- **Low/Med — Stage-B params leaked across pipeline switches.** The drawer's one `advParamsB` bag
  survived a zimage↔sd35 family change (only the model reset), so a stale zimage-only key (e.g.
  `cfg_normalization`) 422'd confusingly on sd35. **Fix:** the family switch clears the drawer
  (same rule as the Stage-A bar).
- **Tests:** +2/-0 reworked: batch counts surfaced + `stopped` preserved + partial_note shapes
  (incl. None on full success); **lineage one-edge-per-output** (per-item manifest, job-level
  fallback, retry replace-not-duplicate, delete removes all).

### User-feedback round 4 (started 2026-06-11 ~06:40, finished 2026-06-11 07:40, Claude Code)
Three connected asks: clean/polish should be a **post-process on ANY run** (not multi-only), the
pass **model should be a dropdown** (was freetext), and pass tiles should **stream one-by-one**
(they only appeared when the whole pass finished). One architecture change resolves all three:
**clean/polish moved OUT of the multi worker and became orchestrator-chained post-passes.**
**131 backend tests pass (123 → 131, +8 `test_post_passes.py`, 3 reworked); `tsc`+`vite` clean.**

- **Why chaining:** the in-worker passes (multi `batch` subcommand) ran their img2img children
  **piped** (`backends.py` captures stdout) — that's exactly why their tiles never streamed — and
  they were structurally locked to multi. As chained jobs they're ordinary **batch img2img jobs**
  (round-3 machinery reused whole): model loads once per pass, `Image:` lines stream per item →
  **interim tiles, real progress, ⏹ stop, partial-dataset honesty all apply for free**.
- **`model_catalog.POST_PARAMS`** — ONE shared spec (marked `post: True`), appended to the
  **zimage + sd35 catalogs and `MULTI_PARAMS`**: `clean`/`polish` toggles + per-pass backend
  (zimage|sd35), **model (enum over both families' variants — the dropdown ask)**, strength
  (defaults 0.5 / 0.22), prompt (default: each image's own), negative, polish_seed. `emit_argv` +
  `build_batch_shared` skip post params — they are **never worker CLI flags**. ⚠ Dropped from the
  old multi surface: `img2img_batching` (meaningless now) + `flux2-img2img` as a pass backend
  (needs the §11 standalone-flux2 spike; zimage/sd35 are the wired backends).
- **`/generate` + `/stage-b`**: shared `_extract_post_passes` pops the post params and builds
  pass specs — opt-in footgun guard (sub-params w/o toggle → 422, now on EVERY pipeline),
  **backend-family check** (a zimage model on an sd35 backend → 422), per-pass weight pre-flight
  (412) + VRAM admission (422). Dry runs return `post_passes` (the preview modal shows
  "clean: zimage @ 0.5 → polish: sd35/sd3.5-medium @ 0.22").
- **Runner chaining**: jobs carry `post_passes` / `chained_from` / `pass` (schema'd). On a
  successful finalize, `_submit_chained` fires the next pass as ONE batch img2img job over the
  parent's outputs — per-output prompt/seed/**coverage_cell carried from `output_meta`** (a
  polished Stage-B cell stays curatable), requester/version/stage inherited (lands in the same
  grid), remaining passes ride along (polish chains off clean's outputs). Works after **any**
  parent: zimage/sd35 singles, multi pools, Stage-B datasets.
- **multi adapter**: always invokes **`ideate`** again (the `batch` subcommand + its in-worker
  passes stay a monorepo CLI capability, unused by loom). Round-3's multi clean/polish argv
  plumbing superseded.
- **UI**: pass jobs show `clean⤴`/`polish⤴` in the queue panel + Inspector (with the parent id);
  the **sandbox grid follows chains** (a pass job joins its parent's batch view, nested chains
  included); the param drawers got the model dropdowns for free (enum choices). Asset grids
  needed nothing — chained jobs inherit requester/stage.
- **Tests:** +8 `test_post_passes.py` (emit skips post; zimage+clean dry-run spec; family
  mismatch 422; footgun 422 on a non-multi pipeline; submitted job carries stripped passes;
  **`_submit_chained` builds the batch pass job** — items/meta/seeds/inheritance/rest-passes;
  stage-b + polish dry-run); 3 reworked (multi argv "post params never reach the worker",
  multi dry-run → post_passes, catalog well-formed allows flag-less post params; + the new
  model-dropdown test).
⚠ **Real-GPU verification owed:** a chained clean→polish run end-to-end (chain firing is
unit-proven, not GPU-proven), incl. a pass over a multi pool and a polished Stage-B cell being
kept into the ref_set. ⏭ Open (unchanged): per-member multi ideate params, P1-12, native folder
picker, slim `/jobs`; new: flux2-img2img as a pass backend rides on the §11 flux2 spike.

### Round-4 review follow-ups (started 2026-06-11 ~11:10, finished 2026-06-11 11:25, Claude Code)

External review of the round-4 work surfaced two Medium findings; both confirmed + fixed.

**Finding 1 — `params.model_name` bypassed the weight pre-flight (Medium).** `model_name` is
also a catalog param (enum, served to the drawer), and the params channel overrides the
top-level field on merge — but the cache gate in `/generate` and `stage_b` only checked the
explicit `req.model_name` or the pipeline default. So `params: {model_name: "sd3.5-large"}`
passed sd3.5-medium's cache check and then loaded sd3.5-large in the worker (late subprocess
failure instead of a fast 412).
- **`/generate`** (`main.py`): the pre-flight now resolves the EFFECTIVE model from the
  **merged** param set (`base.get("model_name") or default`) — `validate_model` still rejects
  unknown top-level names up front, and the params-channel value is already enum-validated.
- **`stage_b`** (`main.py`): one resolution point — `model_name = extra.pop("model_name") or
  req.model_name` — feeds the dry-run preview, the pre-flight, and the worker params. This also
  fixed two latent precedence inconsistencies: the real run used to let `req.model_name` beat
  the params channel (opposite of `/generate`), and the dry-run preview the reverse of THAT
  (`**extra` clobbered the field). Now params-channel wins everywhere, and what passed the gate
  is exactly what the worker gets.

**Finding 2 — graceful stop still chained the post-passes (Medium).** A ⏹-stopped batch is
deliberately `ok` (≥1 output, `manifest_status: "stopped"`), but the chain hook fired for ANY
ok parent with `post_passes` — so stopping a sweep could immediately enqueue a clean/polish
batch over the partial outputs, undoing the stop.
- **`runner.py`**: `_submit_chained` now returns early when `result.manifest_status ==
  "stopped"` (guard lives in the method → unit-testable; covers nested chains too — a stopped
  pass job won't fire ITS remaining passes either). Finalize appends the skip to the job note:
  `"partial dataset: …— stopped early; clean → polish pass(es) not chained"`, so the queue
  panel/Inspector say why nothing followed. Re-running the pass from the drawer stays available.

**Tests:** +4 → **135 passed** (3.79s). `test_batch_worker.py`: generate + stage-b 412s name the
params-channel model (`image_model_present` monkeypatched to "only sd3.5-medium cached" — the
old code passed the gate), and the params-channel model is what reaches the worker params;
`test_post_passes.py`: `test_stopped_batch_does_not_chain` (stopped parent → `_submit_chained`
is a no-op). No UI change needed (the note string already renders in the queue panel/banner).

### MVP push + plan sanity/consistency review (started 2026-06-11 ~11:30, finished 2026-06-11 12:10, Claude Code)

**User call: "MVP is in good shape" → push, then sanity/consistency-check the plan before continuing.**

**✅ PUSHED `228fe7c`** (`main`, first push since `452f89c`): the entire local arc — M2 hardening
(3 passes, vendored `multistack`), M3 steps 1–6 (Stage B+C MVP done-line), acceptance `_img2img`
vendor fix, review rounds 1–4 + round-4 follow-ups. 84 files, +15,858/−229. Pre-commit hygiene
verified: `.env.local`/`.loom_state`/`__pycache__` ignored, multistack = 0.8 MB source, no weights.

**Plan review (kb-loom-p1.md + kb-storyboard01.md §9.2/§10.0 R1–R169 + §4.1 + kb-loom-p2.md
contract surface). Verdict: structurally sound** — M4→M10 ordering, MVP-proof vs full-acceptance
guardrails (§12), and the P1→P2 contract all hold; nothing built contradicts the architecture.
But several M3-era **user decisions never flowed back into the spec docs**, and three forward
sanity risks exist. All tracked below.

#### POST-MVP TRACKER (consistency + sanity findings, 2026-06-11 review)

*Consistency (spec ↔ shipped reality) — fix = a one-commit doc sweep unless noted:*
- **PM-1 · R104 stale ×3.** Style is **appended** (user 2026-06-10) but kb-loom-p1.md §6 + §15
  and the kb-storyboard01 R104 row still say "fixed prepend". Same for Stage-B order: §7.1 says
  `style + clause + cell`; shipped = `cell, clause, style`. → amend-in-place w/ amendment notes
  (the R87/R108 "superseded" pattern).
- **PM-2 · Background-diversity axis unfulfilled (substantive).** §7.1 says backgrounds "varied
  per image"; shipped img2img cells are `background:""` (user round-2) → a Stage-B dataset has
  ~one background. Contract side safe (`build_caption` omits empty bg; P2 meter advisory R120).
  **Dependency the plan doesn't surface: inpaint-realized cells need a subject MASK → masks come
  from matting (BiRefNet) → matting is M6.** → see mitigation sequencing below.
- **PM-3 · R105 half-done.** clean/polish selectable = shipped (better than spec — any run).
  **Pipeline-mix-per-cast NOT shipped**, blocked on a monorepo `multi` CLI extension (same change
  unlocks per-member ideate params). → amend R105 ("fast/refined presets = v1 mix control");
  fold both needs into ONE monorepo work item, opportunistic not blocking.
- **PM-4 · Wording drifts (doc sweep):** `_img2img` listed as standalone *adapter* (§5/R121) →
  shipped as shared lib in zimage/sd35 workers; §3/§5 place clean/polish *inside* multi → now
  orchestrator post-passes (multi = ideate-only); preset counts spec ~100/40/30/45/20 vs shipped
  78/31/30/45/17 (annotate or tune Comprehensive); kb-loom-p1.md header still "Status: spec (not
  yet implemented)"; R162 "referenced not vendored" superseded in practice (vendored multistack,
  monorepo-first + drift guards).
- **PM-5 · Un-homed WPs.** **P1-12 curation throughput** maps to M3 in the WBS but M3 closed
  without it (spec calls it load-bearing; bites on first Comprehensive run). → re-home after M4,
  **before P2**. **P1-15** (main-vs-NPC routing at profile creation) → recommend: fold into the
  Stage-B preset picker (satisfied-by-design), drop from M1.
- **PM-6 · R38 ~200-img cast cap never enforced.** → recommend amending R38 to "guidance, not
  enforced — superseded by disk guard + queue visibility" (or a trivial soft-warn if preferred).

*Sanity (road ahead):*
- **PM-7 · M4 identity anchor = riskiest milestone; "PuLID" assumption unverified.** PuLID
  targets SDXL/FLUX-dev; Z-Image/SD3.5 support unproven; InsightFace/onnxruntime on Win+ROCm is
  a known pain. → **spike-first** (see mitigation). Note: round-4 chained-pass architecture IS
  the face-lock slot (per-pass strength already carried; per-output strength rides output_meta,
  R114-ready) — M4's orchestrator side is largely pre-built; risk concentrates in worker/model.
- **PM-8 · models.json declares no P1 weights.** M4/M6 add postproc models → manifest + gating
  entries needed (reuse the `multi_presets` preset-scoped fetch pattern). Mechanical.
- **PM-9 · M6 got cheaper** — "queueable per-image actions" ≡ chained passes; matting/restore/
  upscale each become a pass backend. Re-size M6 when reached; consider splitting matting out
  (ties to PM-2).
- **PM-10 · M3 acceptance not formally recorded.** Owed real-GPU list: chained clean→polish
  end-to-end (incl. multi pool + polished Stage-B cell kept to ref_set), batch-sweep
  timing/streaming/⏹ (now incl. ⏹-with-passes → "not chained" note), sd35 sandbox t2i, recipe
  prompt quality, tree-kill on live cast, params drawer e2e. → run, then write an **M3 ACCEPTED**
  entry (M10 requires it).

#### Mitigation sequencing (agreed shape: docs → acceptance → spike → thicken)

1. **Doc sweep** (~0.5 d, one commit): PM-1, PM-3 amendment, PM-4, PM-5 re-homing, PM-6 — all
   mechanical; this tracker enumerates every edit. Amendment notes, never silent rewrites.
   *Process fix going forward: any implementation-time user decision that contradicts a spec gets
   a journal entry AND a same-day spec amendment (the round-2 style decision proved the gap).*
2. **M3 GPU acceptance** (user, PM-10) → M3 ACCEPTED entry; fix anything it surfaces.
3. **M4 spike-first** (PM-7, time-boxed 1–2 d): on-rig feasibility of (a) face embedding
   (InsightFace/antelopev2 — CPU is acceptable, per-image), (b) identity application vs the
   ACTUAL bases: PuLID-FLUX (klein unproven) / IP-Adapter-FaceID-class for SD3.5 / **ReActor-class
   inswapper face-swap as the model-agnostic fallback** (image→image, no diffusion backbone —
   fits the chained-pass slot perfectly, highest lands-on-ROCm probability). Ship whichever
   works; §12 guardrail means a failed spike degrades M4 to "anchor record/UI + best-effort
   pass", blocks nothing.
4. **Pull BiRefNet matting forward** (with/right after M4, PM-2/PM-9): masks unlock
   inpaint-realized cells (expose `realize="mixed"` through /stage-b) → closes the
   background-diversity gap **before P2 trains**. Also: P2's first LoRA off a bg-uniform set is
   itself the cheap experiment — train one, check backdrop memorization, let evidence set the
   priority.
5. **P1-12 curation throughput** after M4, before P2 (PM-5).
6. **multi CLI extension** (PM-3) opportunistic, monorepo-first per R162-as-practiced.

**✅ Mitigation step 1 — DOC SWEEP DONE (2026-06-11 12:10).** All amendment-note style (no silent
rewrites), tagged with PM-n references:
- **kb-loom-p1.md:** header Status → "in implementation, M1–M3 ✅"; §3 adapter block notes
  (clean/polish = post-passes, `_img2img` = shared lib); §5 as-shipped blockquote before the R121
  paragraph; §6 style **appended** (PM-1); §7 Stage A — append + R105 status + R38 guidance
  (PM-1/3/6); §7.1 — prompt order cell-first (PM-1), Background axis row ⚠ + deferral blockquote
  (PM-2, names the matting dependency + `realize="mixed"`), shipped preset counts note (PM-4);
  §13 M1–M3 ✅ marks + M3 img2img-only/acceptance-pending note; §15 R104/R105 row amendments;
  §16 WBS — P1-12 re-homed **post-M4 pre-P2**, P1-15 folded into the Stage-B preset picker (PM-5).
- **kb-storyboard01.md §10.0:** R38 (cap = guidance, PM-6), R104 (appended, PM-1), R105 (status,
  PM-3), R121 (`_img2img` as-shipped, PM-4), R162 (vendoring practice update — monorepo-first +
  byte-identical re-vendor + hash guards, PM-4).
- **kb-loom-p2.md §5 D2:** P1 contract note — `coverage_cell.background` may be empty,
  `build_caption` omits it, bg axis advisory-only until inpaint realization (PM-2).
PM-1/3/4/5/6 doc-side **closed**; PM-2 doc-side closed (engineering rides mitigation step 4);
PM-7…PM-10 unchanged (next: PM-10 GPU acceptance → M4 spike).

**PM-10 update (2026-06-11 ~12:30):** user ran **extensive GPU image-generation jobs — generation
paths signed off** (GPU utilization good). Acceptance held open on THREE control-path checks that
generation volume doesn't exercise: (1) **chained clean→polish on hardware** incl. a polished
Stage-B cell kept into `ref_set`; (2) **⏹ mid-batch with passes queued** ("not chained" note, no
pass spawned); (3) **✕ on a running multi cast** (tree-kill → VRAM actually freed). If the user's
runs covered these → write **M3 ACCEPTED**; else ~10 min targeted check.

## ✅ M3 ACCEPTED (signed off by the user 2026-06-11 12:33) — PM-10 CLOSED

**User sign-off:** extensive GPU image-generation runs (generation paths, GPU utilization good)
**+ chained clean/polish confirmed GPU-efficient on hardware**. M3 = Stage B+C MVP done-line is
**ACCEPTED**: cast → star hero ★ → expand (coverage recipe, one batch job) → keep/cull →
`ref_set` → Save AssetProfile, plus the review-round hardening (queue UX, params drawer, batch
worker, post-passes, partial-honesty, per-output lineage). *(Not explicitly re-run at sign-off,
noted as residual low-risk: ⏹-with-passes-queued and ✕-tree-kill — both unit-covered; first
real-world use will confirm.)* **Next: M3.5** (BiRefNet matting + `realize="mixed"` — decision
ratified by the user 2026-06-11), then M4 spike-first.

**Sequencing decision support (2026-06-11): recommend BiRefNet + `realize="mixed"` as M3.5,
BEFORE M4.** Key insight: **inpaint cells are identity-SAFE by construction** (subject pixels
held, only background repainted) → they don't need the M4 anchor; img2img cells are the ones that
drift and benefit from the face lock later — the milestones are independent. Plus: curation is
the expensive non-redoable resource (first big Comprehensive curation should happen on a
bg-diverse set — that's what P2 trains on), and matting pioneers the postproc plumbing
(models.json P1 weights PM-8, vendor step, pass-backend registration) with the simplest tool, so
the M4 spike then tests ONLY model feasibility. **M3.5 scope box:** BiRefNet worker
(monorepo-first, R162) → hero matte → inverted bg mask → expose `realize="mixed"` via `/stage-b`
(= two batch jobs: img2img cells + inpaint cells; mask is hero-derived + batch-shared, batch
worker untouched; R113 method auto-pick becomes real). NOT in scope: rest of M6
(restore/upscale/SAM2).

---

## M2 — Stage A casting (`multi`)

started: 2026-06-05 20:12:38
finished: 2026-06-05 20:57:03

**Goal (spec §13.2 / P1-2):** [Cast ▶] → selectable grid → **star the hero ★** → save into
`v1_base`, and **onboard the `multi` adapter** (1-page contract check). Per the review-locked
ordering: **Step 1** nails the casting *data model* with the known-good `zimage` adapter; **Step 2**
onboards `multi` — so adapter debugging can't mask data-model bugs.

### Step 1 — persist `casting[]` + hero-star into `version.json` (zimage) — finished 2026-06-05 20:23:54
- **Schema:** `version.schema.json` `casting[]` items promoted **string → object**
  `{id cand_, job_id, file, source_output, pipeline, seed, starred, added_at}` (≤1 `starred` = hero).
- **`assets.py`:** `star_candidate` (idempotent on `job_id`; **copies the candidate image + sidecar
  manifest into the version's `casting/` dir** so a Saved version is self-contained / survives job
  deletion or `out/` pruning; sets the sole hero), `set_hero` (set/clear ★ among recorded
  candidates), `casting_file_path` (traversal-guarded serving resolver); refactored
  `_find_version`/`_load_version_strict` to share lookup.
- **`main.py`:** `POST /assets/{id}/casting/star` (resolves the job → output_name/seed/pipeline,
  done-only → 409 otherwise), `POST /assets/{id}/casting/hero`, `GET /assets/{id}/casting/{file}`
  (serves the casting copy, mirrors `/outputs`). token_required list updated.
- **UI:** ★/☆ toggle on **done** tiles when an asset is active (top-left, clear of 🗑/✕); the saved
  hero gets an amber **glow ring**; `getAsset`→casting drives `starredJobs`; `starCandidate` persists.
  Client: `CastingCandidate`/`ProfileVersion`/`AssetDetail` types + `getAsset`/`starCandidate`/`castingUrl`.
- **Verified:** **9 no-GPU tests** (`test_casting.py`: record+copy, single-hero invariant, idempotent,
  unstar toggle, explicit/clear hero, missing/unsafe-output reject, serving guard, **survives reload**)
  + **real-GPU end-to-end** (asset-scoped gen 92.6 s → star → `cand_…png` copied, `GET casting` 200/901 KB,
  persisted + reopened with hero intact). `tsc`+`vite` clean. **24 backend tests pass.**
- ⚠ Carried to Step 2 / later: candidates still come from the **`zimage`** adapter (multi = Step 2);
  the grid shows live/persisted *jobs* (a deleted job's casting copy survives on disk but isn't shown
  — the full browse-orphaned-candidates grid is M3 curation). `--num-candidates`/pipeline-mix selector = Step 2.

### Step 2 — onboard the `multi` adapter (built + unit-verified; **real-GPU run pending HF setup**)
**Decisions (user, via AskUserQuestion):** (1) **full 3-pipeline cast** (flux2+sd35+zimage) — not a
cached subset; (2) **per-candidate tiles + output-keyed casting** (one job → N candidate tiles).
**Blocker surfaced:** flux2 (`FLUX.2-dev` AE) + sd3.5-large(-turbo) are HF-**gated** and there's **no
HF token** — so the GPU run waits on the user creating `HF_TOKEN` (→ `.env.local`) + accepting the
licenses (fast preset: `FLUX.2-dev` + `sd3.5-large-turbo`).
- **`adapters/multi.py`** (1-page contract check): **module-invoked** `python -m
  pipeline.multi.run_pipeline ideate …` (relative imports) — the runner's existing `cwd=script.
  parents[2]` lands on `…/src`, the correct cwd; `multi`'s `stage_runner` self-locates flux2/sd35/
  zimage + PYTHONPATH and runs each as an isolated subprocess (VRAM isolation), so no env-replication
  here. `parse_result` reads the `multi_*.json` manifest's **ideate** stage → `candidates[].output_path`
  for every `ok` candidate ⇒ **N outputs from one job**. Default preset **`fast`** (klein-4b/turbo).
- **Runner:** registered `multi` in `ADAPTERS` + `VRAM_ESTIMATES["multi"]=14` (peak = one pipeline,
  not the sum — subprocess isolation). Result now surfaces **`output_names[]`** (paths **relative to
  `out/`**, not basenames — serves nested candidate trees) plus `output_name` (primary, back-compat).
- **`/generate`:** accepts `pipeline:multi` (mode coerced to `ideate`); a multi cast = **one job**
  carrying `num_candidates`(≤5)+`ideation_mode`; admission/disk-gate/style-prepend/lineage all shared.
  **HF token propagated** from config into `os.environ` at startup so child subprocesses inherit it.
- **Casting → output-keyed:** `star_candidate` dedups on **`source_output`** (not `job_id`), so each
  of a multi job's N candidates is an independent starrable entry; the star API takes an explicit
  `output` (+ derives per-candidate pipeline/seed from the `…/ideate/<pipeline>/seed_<n>/…` path).
- **UI:** the grid **flattens jobs → candidate cells** (a multi job expands into one tile per
  `output_names[]` entry; selection/star/cancel/delete keyed per cell); cast bar gains a **pipeline
  selector** (multi|zimage), **candidates** (1–5) + **preset** (fast|refined) when multi. Client:
  `output_names`, `pipeline/num_candidates/ideation_mode` on the request, `starCandidate(output)`.
- **Verified (no-GPU):** **29 backend tests** (multi `build_argv` module-invoke + `parse_result`
  pool/failed/no-manifest; output-keyed multi-pool casting); **multi `/generate` dry-run** (one job,
  3 candidates, cwd=`src`, module argv, requester=version, style prepended); `tsc`+`vite` clean.
- ✅ **Real-GPU verified (2026-06-05 20:57):** user set `HF_TOKEN` + accepted the licenses; a full
  **3-pipeline cast** ran end-to-end — `num_candidates=1` → **one job → a pool of 3 candidates**
  (flux2 + sd35 + zimage, ~213 s total, ~60 s/pipeline sequential). The starred flux2 candidate
  persisted into `version.json` (pipeline correctly parsed from the `…/ideate/flux2/seed_…/` path),
  the casting copy served **200 / 1.78 MB**, and survived reopen. **M2 done.**
- ⚠ Carried (deliberate, not done-line): lineage writes **one edge/job** even for a multi pool (the
  per-candidate `casting/` copies are the durable record); per-candidate lineage = later refinement.
  clean/polish (`batch` toggles) + R105 pipeline-subset selector also deferred (full 3-pipeline now).

### Review follow-ups (2026-06-06 09:26 — 4 findings, all valid; user-chosen fixes)
Post-acceptance hardening from an external review of M2 (no new milestone — M2's finish stands).
Two had a design fork → resolved with the user via AskUserQuestion (recorded below).

- **Med — weight gate was blind to `multi`'s gated deps.** `weights_ok()` (the `/generate`
  precondition) only checked the phase `models` (zimage P0 + P3/P4 Qwen); a cast on missing/
  unauthorized **flux2/sd35** weights died *inside* the GPU subprocess instead of failing fast.
  **Chosen fix (user): a preset-aware pre-flight, NOT a phase-gate entry** — the casting weights
  are **preset-dependent** (`fast` = klein-4b/sd3.5-large-turbo/zimage-turbo; `refined` =
  klein-9b/sd3.5-large/zimage-base, mirror of `pipeline/multi` `IDEATION_PRESETS`), so folding
  them into the phase gate would force *all* presets' gated checkpoints onto a 16 GB rig
  (over-gating). Added `models.json` → **`multi_presets`** (separate from `models`, so the launch
  gate ignores it), `components.multi_weights_status(preset)` (HF cache-probe per repo → missing[]
  with `repo_id`+`gated`), and a `/generate` check that **412s with the exact missing repos** for
  the *selected* `ideation_mode` (skipped for `dry_run`). `/components/fetch?multi_preset=…`
  snapshot-fetches a preset's set (gated repos still need a one-time license-accept + `HF_TOKEN` —
  a 401/403 surfaces per-repo). ⚙ Cache probe respects `HF_HOME` (set at orchestrator startup);
  the preset→weights table incl. the per-variant text encoder (Mistral-Small-24B / Qwen3-8B-FP8).
  *Verified: empty `HF_HOME` → all 5 fast entries reported missing w/ gated flags; populated → ok.*
- **Med — `multi` was an adapter without vendored pipeline code.** Only `zimage` was vendored;
  `multi` resolved via `LOOM_PIPELINES_DIR`/the monorepo fallback → M2 wasn't clone-runnable.
  **Chosen fix (user): vendor it now.** `multi`'s `stage_runner` self-locates flux2/sd35/zimage +
  the `flux2` lib by paths relative to its own file (`SRC_DIR = parents[2]`, `REPO_ROOT/flux2/src`)
  and is **module-invoked** (`-m pipeline.multi.run_pipeline`, cwd = `parents[2]`). So a flat copy
  into `pipelines/multi/` would break: instead vendored as **`pipelines/multistack/`** — a faithful
  mirror of the monorepo's `src/pipeline/{multi,flux2,sd35,zimage}` (+ `_artifact_id.py`) and the
  sibling `flux2/src/flux2` lib. Registered the inner `…/multistack/src/pipeline` as a pipeline root
  **ahead of the monorepo fallback** (`config._resolve_pipeline_roots`). **Zero edits to the
  pipeline code** (faithful copy → no logic drift, the stated vendoring risk). ⚙ The root *must* be
  the inner `…/src/pipeline` so `parents[2]` == `…/src` (the cwd that makes both the module import
  and the self-location resolve). *Verified: `multi` resolves into `multistack` (not the monorepo);
  vendored `stage_runner` `SRC_DIR/REPO_ROOT/FLUX2_*` all point into the tree + exist; `flux2.util`
  lib present; `zimage` still resolves to its flat copy. ~0.9 MB vendored.*
- **Low — `/capabilities` only exposed `zimage`.** It drives the UI yet omitted `multi` (which has a
  proper `capabilities()`), leaving the contract stale before M3 adds adapters. **Fix:** the endpoint
  now returns both `zimage` + `multi`.
- **Low — job Inspector mislabeled `duration_s` as "pipeline".** `App.tsx` showed seconds under a
  "pipeline" label. **Fix:** added a real **pipeline** row (`job.pipeline`) + relabeled the seconds
  row **duration**.

Tests: `test_multi_weights_vendor.py` — preset sets load from manifest, status ok/missing+gated
flags, **phase gate stays blind to presets**, unknown-preset fetch is safe; vendored root registered
+ precedes monorepo, `multi` resolves into the vendored tree w/ correct path math.

#### Second review pass (2026-06-06 09:39 — 3 findings on the above, all valid)
- **Med — `fast` preset listed the wrong text encoder.** I'd put `Mistral-Small-3.2-24B` for klein-4b,
  but Klein checkpoints load **Qwen3** (`load_qwen3_embedder` variant 4B/8B; Mistral is only
  `flux.2-dev`, which the presets use for the **AE only**). Worse, on the **Windows ROCm target (RX
  9070 XT)** `_load_text_encoder_safe` falls back to the **non-FP8** `Qwen/Qwen3-{4B,8B}`, so an
  FP8-only entry would miss on the actual rig. **Fix:** TE entries are now `Qwen/Qwen3-4B`(fast)/
  `Qwen/Qwen3-8B`(refined) with the FP8 repo as `alt_repo_ids`; new `_entry_present` treats an entry
  as satisfied if **either** variant is cached (covers CUDA *and* ROCm without importing torch).
- **Med/Low — `multi_weights_status` failed OPEN on a missing preset.** An unknown/dropped preset →
  `multi_preset_weights` `[]` → `(True, [])`, silently disabling the 412. **Fix:** empty set now
  **fails closed** (`(False, [explanatory])`). *(`ideation_mode` is Literal-constrained today, so this
  is a manifest-edit guard.)*
- **Low — stale `components.py` header** said "P1 declares no weight / `/generate` stays ungated until
  M2." **Fix:** rewritten to describe preset-scoped (not phase-scoped) multi gating.

Tests (TE handling; status fails closed on unknown preset). `tsc` + `vite build` clean.

#### Third review pass (2026-06-06 — 2 findings, both valid)
- **Bug — `fetch_multi_preset` `NameError` on the download success path.** When I switched the
  "already cached" check to `_entry_present(e)` I dropped the local `probe`, but the post-download
  line still read `_hf_cache_probe(repo_id, probe)` → `name 'probe' is not defined`, so the exact
  recovery path the 412 hint points at falsely reported failure after a successful download. **Fix:**
  re-check via `_entry_present(e)` (the canonical presence fn) — no bare `probe`. Regression test
  with a fake `snapshot_download` confirms `fetched=True` and no per-repo error.
- **Refinement (user request) — gate the EXACT Qwen repo, not "either variant".** Replaced the
  `alt_repo_ids` "any-of" probe with platform-exact resolution: `components._needs_fp8_workaround()`
  faithfully mirrors `flux2/stage1_load_models` (`os.name=="nt"` + `torch.version.hip`, lazy-imported
  + lru-cached), and `_entry_resolve_repo` picks `repo_id` (non-FP8) on Windows ROCm / `fp8_repo_id`
  elsewhere. The gate + fetch + the 412's `missing[].repo_id` now name the precise repo that will
  load. *Verified on the target box: `_needs_fp8_workaround()` → True, TE resolves to `Qwen/Qwen3-4B`.*

Tests **+3 / −1** (TE resolves to exact platform repo; missing-report names the platform repo on
ROCm; fetch success path has no NameError) **+ a drift guard** (user request): `_needs_fp8_workaround`
in `components` and the vendored flux2 live in separate trees, so a test AST-extracts the vendored
function's source and runs it (just `os`+`torch`, no heavy `flux2.util` import — fast) and asserts it
agrees with ours; a logic change in the vendored copy flips it. **41 backend tests pass** (≈1.6 s);
`tsc` + `vite build` clean. No milestone change — M2's finish stands.

---

## M1 — asset library + AssetProfile record + minimal style fragment (zimage scaffold)

started: 2026-06-05 19:00:36
finished: 2026-06-05 19:25:31

**Goal (spec §13.1 / P1-1, P1-15):** stand up **L2 Asset Studio's** skeleton — the bundle records
+ a library tree + create an AssetProfile (`profile.json` + a single `v1_base` version, **no
versioning machinery yet**), seed a **minimal editable L1 style fragment** (`story.json`) that
auto-prepends to generation, and **promote the P0 batch grid into L2 scoped to an asset**, reusing
the **`zimage` adapter only** (no new adapters — those are M2/M3). *Scaffold, not the MVP done-line.*

### Done (backend)
- **New records + schemas** (inherit P0 IDs/atomic/validate): `story.schema.json` (StoryBible —
  M1 = style only: `{id sto_, style:{id sty_, fragment, enabled_default}}`), `profile.schema.json`
  (AssetProfile `chr_`: name, `asset_class` ∈ characters/props/scenes, slug, `active_version`,
  `versions[]`), `version.schema.json` (ProfileVersion `ver_`: name, `finalized` (M1=false=**Saved
  not Finalized**, R119), `derived_from`, `prompt_template`, `anchor_ref`, `ref_set[]`, `casting[]`).
  Lineage schema gains `profile_version_id` + `stage`.
- **`workspace.py`**: `story_json`/`bible_dir`/`assets_dir`/`asset_dir(class,slug)` lazy subtrees
  (§4) + `slugify(name)` (folder is a slug; refs use the stable id).
- **`bible.py`** (L1): `DEFAULT_STYLE_FRAGMENT` ("cinematic, dramatic lighting…"); `load_style` /
  `set_style` (writes `story.json`; in-memory default until first edit).
- **`assets.py`** (L2): `create_asset` (profile + `v1_base` version + `casting/refs/faces/` dirs;
  slug-collision → 400), `list_assets`, `get_asset`, `resolve_version` (active by default).
- **`main.py`**: `GET/PUT /bible/style`, `GET/POST /assets`, `GET /assets/{id}` (POST token-gated).
  **`/generate` extended**: `asset_id`/`version_id`/`stage`/`apply_style`. When `asset_id` set →
  `requester_id = the version id` (lineage scope) + job carries `profile_version_id`+`stage`; the
  **L1 style fragment auto-prepends** to the prompt unless `apply_style:false` (R104 override).
  `runner.submit` stores `profile_version_id`/`stage`; lineage edge records them.
- **Logging**: asset-create + scoped-generate (`for ver_… stage=A`) flow through the M0-logging layer.

### Done (UI — minimal L2)
- **Navigator rail → ASSETS**: a **Sandbox (unscoped)** row + the character list + **"+ Character"**
  (prompt-create). Selecting an asset scopes the stage.
- **Stage**: a casting-context header (`<name> · v1_base · Stage A · Casting`) when an asset is
  active; **Generate → "Cast ▶"**; an **L1 style bar** (editable fragment + Save + an **apply**
  checkbox = the per-gen override). The grid is **derived from the asset's jobs** (requester =
  active version) — no stale-closure batch tracking; Sandbox keeps the P0 batch-id grid.
- Client fns `getStyle`/`setStyle`/`listAssets`/`createAsset`; `Job` gains `requester_id`/`stage`.

### Verified
- **Backend (TestClient + real GPU)**: default style + edit + persist (`story.json`); create asset
  (profile + `v1_base`, dirs), list, get, dup-name **400**, no-token **401**; **dry-run scoping** —
  style prepend (`noir comic, ink wash, a lone ranger`), `requester_id`=version, `profile_version_id`
  set, `apply_style:false` → no prepend, bad asset **404**; **real GPU gen scoped to the asset →
  done (75 s), job `requester=ver_… stage=A`, lineage edge carries `profile_version_id`+`stage:A`**.
- `tsc` + `vite build` clean.

### ⚠ Carried forward (intentional — later P1 milestones)
- **No versioning machinery** (copy-on-create/finalize/selector) — M5. **No hero-star save into
  `casting/`** yet — M2 (`multi`). **No Stage B/C** (dataset recipe + curation) — M3 (the MVP
  done-line). The grid promotion + style + records are the scaffold those build on.
- Style auto-prepend currently applies to **any** scoped gen with `apply_style` on; the full
  per-class style scaffolding + L1 World UI is M8.

### Review follow-ups (2026-06-05 19:50 — 5 findings, all valid)
- **High — corrupt version could still be dispatched.** `resolve_version` only checked that the id
  was in `profile.json`'s `versions[]`; a corrupt `versions/*/version.json` was silently dropped by
  the list path yet still enqueued. **Fix:** `resolve_version` now strictly loads + **validates the
  target version record on disk** (`_load_version_strict`) and raises `WorkspaceError` (→ 404) on a
  corrupt-but-registered version — never enqueue Stage A/B/C against an unloadable ProfileVersion.
  The list/skip paths (`_iter_profiles`/`_load_versions`) now **log a warning** instead of disappearing
  corruption silently. *Verified: corrupted `version.json` → resolve raises, list still renders.*
- **Med — AssetProfile id was character-shaped for every class.** `new_id("chr")` + `^chr_` schema
  even though the API accepts `props`/`scenes`. **Fix:** neutral **`ast_`** id (`profile.schema.json`
  pattern `^ast_…`); `asset_class` carries the kind. *Verified: a `props` asset gets an `ast_` id.*
- **Med — launch gate ignored P1 schemas.** The orchestrator could start cleanly with broken
  story/profile/version support and fail later at `/assets`. **Fix:** added a **`p1_records`** code
  component (loads the 3 P1 schemas + validates a sample of each), **phase-scoped to P1** — reported
  as `installed-but-unavailable` (not blocking) while `LOOM_ACTIVE_PHASES=P0`, blocking once P1 is
  activated. *Verified: `code_ok` stays True under P0, component present.*
- **Low — `style.enabled_default` was stored but never honored.** `apply_style` defaulted hard to
  `True`. **Fix:** `apply_style` is now **tri-state** (`bool | None`); omitted ⇒ fall back to the saved
  `enabled_default`. The UI's **Save** now persists the apply toggle as the default (`setStyle(frag,
  applyStyle)`), and Save enables when either fragment **or** the toggle changed. *Verified via dry-run:
  default-True omitted prepends, default-False omitted skips, explicit overrides either way.*
- **Low — README P1 status linked to P0 spec/journal.** Repointed Spec→`kb-loom-p1.md`,
  journal→`kb-loom-p1-imp.md` (P0 spine kept as a secondary link).

`tsc` + `vite build` clean. No new milestone — M1's finish time stands; this is post-acceptance hardening.

### M1.5 — record hardening (2026-06-05 20:00, review-driven, pre-`multi`)
A small hardening step inserted **before M2's `multi`** (review: keep adapter debugging from
hiding data-model bugs).
- **Gate now activates P1** (review: P1 ships in the app, so a broken P1 schema should *block*
  startup, not just be reported). `active_phases()` default `{"P0"}` → **`{"P0","P1"}`**; `.env`
  pins `LOOM_ACTIVE_PHASES=P0,P1`; README table updated. ⚙ Safe because **P1 declares no weight**
  in `models.json` (only P0/P3/P4 do) — so `/generate` stays ungated until `multi`'s weight lands at
  M2. *Verified: active=[P0,P1], code_ok+weights_ok+launch_ok all True, `p1_records` phase-essential.*
- **`LOOM_ACTIVE_PHASES` now routed through the central config loader** (review follow-up): it
  previously read the **process env only** (`os.environ.get`), so edits to the committed `.env`
  silently had no effect. `config.Config.active_phases_raw` reads it via `_get` (real env >
  `.env.local` > `.env`), and `components.CONFIG_active_phases_env()` delegates there. Precedence
  preserved (a real env var still wins); `None` ⇒ the gate's built-in `{P0,P1}` default.
- **No-GPU invariant tests** (`orchestrator/tests/test_p1_records.py`, **16 passing**): story default
  +persist+schema-reject; profile neutral-`ast_` id, props/scenes ids, bad-input + dup-name guards,
  legacy-`chr_` rejection; **version resolution** active/explicit/unknown + **the corrupt-registered-
  version-raises regression guard**; launch-gate `p1_records` presence + phase-scoping + a monkeypatched
  broken-P1-schema that blocks only when P1 is active. Run: `python -m pytest orchestrator/tests -q`.

### ⏭ Forward sequencing locked in (from review — apply at M2/M3)
- **M2 ordering:** first make **`zimage` casting persist `casting[]` + hero-star into `version.json`**,
  and *only then* onboard the **`multi`** adapter — so data-model bugs surface before adapter
  debugging can mask them.
- **M3 contract-first:** **freeze the coverage-cell metadata schema before** any M3 generation work.
  P2 captioning consumes those tags — it's a **data contract**, not UI decoration, so it gets pinned
  (schema + test) up front, not evolved mid-milestone.
