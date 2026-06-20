# Loreweave Studio — P2 build spec (LoRA training)

Created: 2026-06-01
Status: spec (not yet implemented; depends on P0 + P1)
Parent: [`kb-storyboard01.md`](kb-storyboard01.md) (overview + decision record R1–R170)
Predecessors: [`kb-loom-p0.md`](kb-loom-p0.md) (spine) · [`kb-loom-p1.md`](kb-loom-p1.md) (assets/casting)
Engine: [`kb-pipelines01.md`](kb-pipelines01.md) "LoRA Primer" · SLM/VLM: [`kb-slm.md`](kb-slm.md)

P2 is **the only phase where training happens** (R-roadmap). It takes a P1 character's **curated
ref set** and turns it into a **trained LoRA**, so the version becomes reproducible at generation
time. P2 adds **Stages D & E** of the bootstrap (train + lock), **template captioning** (no VLM),
and a **proxy-based readiness meter** (no VLM). The VLM (Qwen3-VL) and a project-wide VLM context are
**deferred to P4** (R116). Every decision traces to a resolved item (`Rnn`) in `kb-storyboard01.md` §10.0.

> ⚠ **P2 introduces genuinely new capability, not just wrapping.** P0/P1 wrapped existing
> *inference* CLIs; the repo has **no image-LoRA *trainer* yet**. P2's core build is a **training
> worker** for `zimage` (first, R6) then `sd35`. Treat that as the phase's main risk (§11).

> **M0 preflight added 2026-06-18:** before training work continues, pause for a **UI/workflow reset**
> over the current P0/P1 MVP. The app is usable, but the author called out layout and workflow debt
> that will become much harder to fix once P2/P3 controls pile onto the same surfaces. This M0 is a
> product-shape correction, not a trainer feature. **M0d added 2026-06-20:** pull in flux.2 **advanced
> prompting + sampling presets** (structured prompts + configurable guidance/steps) to fix loose pose
> adherence in flux2 `ref`-mode expansion — design in §12 "M0d solution design".

---

## 1. Purpose & the P2 done-line

**Purpose:** make a P1 character **reproducible** — train a LoRA from its curated, captioned ref
set so the character can be generated on-model anywhere.

**P2 done-line:**

> Open a P1 character version with a curated `ref_set` → loom **template-captions** it (from the P1
> coverage-cell metadata + trigger token, no VLM) → the **readiness meter** (proxy-scored: coverage
> + dupes + face-embedding) says "good to train" → **stage** the **Train LoRA** job and **add it to
> the queue** → it runs in workspace temp on the single-GPU queue → on success the **LoRA is
> promoted** into the version, temp left for **manual cleanup**, and a **test generation with the
> LoRA reproduces the character on-model**. Recorded in a **training manifest** (dataset hash,
> captions, base+family, trigger token, trainer settings, output hash, caption policy, and context
> digest).

If a P1-curated character can be trained and then re-generated recognizably via its LoRA, P2 is done.

---

## 2. Scope: in vs. out

**In P2:**

- **M0 UI/workflow reset before trainer work:** move project actions into a proper File menu; convert
  L1/L2 navigation (and future L3/L4) into workspace tabs with per-workspace controls; split L1 into
  Visual Styles / World / Story Spine tabs; make long text fields readable; decouple L2
  postprocessing into a stackable, independent image-postprocess workflow; and **(M0d) flux.2
  advanced prompting — structured prompts + configurable guidance/steps with a sampling-preset
  pull-down** to fix loose pose adherence in flux2 `ref`-mode expansion (§12 M0 + "M0d solution design").
- **Template captioning** of the curated `ref_set` (v1: deterministic from P1 coverage-cell metadata
  + trigger token; **no VLM**) — *not* auto-tagging (research). VLM enrichment → P4 (R116).
- **Readiness meter via cheap proxies** (v1: coverage from metadata, perceptual-hash dupes,
  face-embedding on-model; **no VLM**) → advisory signal before training. VLM scoring → P4.
- **LoRA training (Stages D & E)** per **profile-version**: a **training worker** for `zimage`
  (first), then `sd35`; **default train-from-base, optional seed-from-parent** (R68); runs in
  `<project>/_temp/lora_<runid>/` on the single-GPU queue, sharing the `.venv`; **promote** the
  LoRA into the version, **manual cleanup** of temp (R13).
- **Staged training jobs (R118):** training auto-generates a job spec but **does not auto-queue**;
  the author **explicitly adds it to the queue** when finalized (it uses GPU).
- **LoRA loading at inference** wired into `zimage`/`sd35` (§8.1 item 1) so the trained LoRA can be
  used in generation (and to verify the done-line).
- **Training manifest + lineage** (R98): which `ref_set` + captions + base + trigger + settings →
  which LoRA artifact. The manifest also records a **caption policy id/hash** and a compact
  **training context digest** so later GraphRAG/project-context tools can reason about which facts
  produced this LoRA without scraping prose or file names.

**Out of P2** (later phases):

- **The VLM / Qwen3-VL online path (R116):** v1 uses **template captions + proxy readiness, no VLM**.
  **VLM-assisted captioning/scoring + a comprehensive project-wide VLM context (built during L1/L2
  authoring) → P4.**
- **Video LoRAs** (LTXV/Wan IC-LoRA) — much larger scratch; deferred until video work needs them
  (P3+). P2 is **image LoRAs**.
- **Style LoRA — declared only in P2, *not* built (R122).** L1 declares a style-LoRA target, but P2
  trains **character LoRAs only**. A trained style LoRA isn't even *usable* until **multi-LoRA
  stacking** (character+style at inference), so building it now would be a half-feature sitting
  unused. The same trainer engine will train it **when stacking makes it usable — now P5 (R147,
  moved from P6)**.
- **Muse/SLM creative authoring** (Stage-B prompts, dialogue) → P3/P4.
- **GraphRAG / retrieval index build.** P2 writes graph-ready facts and manifests, but does not build
  a vector store, graph database, or retrieval/query layer. That infrastructure is deferred
  post-v1/P6 (R170).
- Shots/audio/Flow/Episode → P3+.

---

## 3. What P2 adds to the P0/P1 spine

```
new WORKER (the core build — NEW capability, not a wrapper):
  trainer adapter   image-LoRA training; two backends — ai-toolkit (default) + diffusers-PEFT
                    (advanced); zimage first then sd35, §8/§11

NOT in P2 (deferred to P4): the VLM / Qwen3-VL online path.
  v1 captions = template (no VLM); v1 readiness = cheap proxies (no VLM). §6, §7, §9.

new RECORDS / fields (inherit P0 IDs/schema/atomic-write rules):
  caption set        versions/<vN>/captions.jsonl   (template caption + trigger token, per ref)
  caption policy     versions/<vN>/caption_policy.json  (template version + source fields)
  readiness report   versions/<vN>/readiness.json   (proxy scores: coverage, dupes, on-model)
  training context   versions/<vN>/training_context.json (asset/version/ref facts for future GraphRAG)
  lora artifact      versions/<vN>/lora/<name>.safetensors  + lora.manifest.json (training record)

new UI (extends L2 Asset Studio):
  M0 shell/workspace reset · L1 tabbed authoring · L2 postprocess stack
  caption review · readiness meter · Train-LoRA panel (STAGED jobs) · LoRA test-generate · temp cleanup
```

P2 reuses the P0 queue/adapter contract/disk guard and the P1 Asset Studio + versioning. The
**trainer is onboarded through the same job-queue + manifest envelope** as inference adapters, with
its own long-running-job semantics (progress by step/epoch, checkpoints in temp).

---

## 4. Data model additions (§3.1, §3.4)

```
assets/characters/<name>/versions/<vN>/
├── version.json            # P1; P2 adds: caption_status, readiness_status, lora ref, trigger_token
├── refs/*.png              # P1 curated set (training input)
├── captions.jsonl          # P2: one structured caption per ref (+ trigger token)
├── caption_policy.json     # P2: deterministic template version + source metadata fields
├── training_context.json   # P2: compact asset/version/ref-set facts for future GraphRAG/context
├── readiness.json          # P2: proxy scores (coverage/dupes/on-model) + gaps
├── lora/
│   ├── <name>_v<N>.safetensors   # P2: the trained LoRA (promoted from temp)
│   └── lora.manifest.json         # dataset hash, captions hash, base model+family, trigger,
│                                  #   caption_policy hash, context_digest, trainer settings,
│                                  #   steps, seed, output hash, duration
└── (faces/ anchor.png from P1)
```

Training **temp** lives at `<project>/_temp/lora_<asset>_<vN>_<runid>/` (dataset copy, captions,
trainer config, checkpoints, samples) — on the **work disk**, governed by the **disk guard** and
the **two-threshold hard stop** (R96). **Promote-then-manual-cleanup** (R13): on success, copy the
LoRA + write `lora.manifest.json`, leave temp for a one-click "delete this run's temp".

**Graph-ready, not GraphRAG-built (R170):** P2 should output facts cleanly enough that a later
GraphRAG index can ingest them directly. `training_context.json` is a compact snapshot of the
canonical inputs: `asset_id`, `version_id`, class, display name, selected style id, trigger token,
base family, ref ids + coverage cells + source generation job ids, parent version/LoRA ids when
relevant, and the readiness summary. `caption_policy.json` records the deterministic caption
template version, ordered source fields, omitted-empty-field behavior, and trigger-token rule.
These are CPU-only records written beside the dataset; no retrieval index, graph database, or VLM is
started in P2.

**Finalize interaction (R51/R60/R119 — Saved ≠ Finalized):** training runs on a **Saved
(committed) but *unfinalized*** version (R119) — the P1 done-line leaves the version **Saved**, and
training is part of continuing to *edit* it (it adds the LoRA). The version **must be Saved** for
training (its `ref_set` is persisted and the queued job survives restart); it need **not** be
Finalized. **Finalize** later **locks** the version incl. its LoRA; a retrain after that ⇒ a **new
version** (deep-duplicate + train again, R58), whose training **defaults to train-from-base** (R68).

---

## 5. The training pipeline (Stages D & E)

```
P1 curated ref_set ─► D1 CAPTION (template) ─► D2 READINESS (proxies) ─► D3 TRAIN (worker, staged) ─►
                       coverage-cell + trigger   coverage/dupes/on-model   isolated temp, queue
                       (no VLM)                  → meter (no VLM)            default from-base
   ─► E PROMOTE  ─►  LoRA → versions/<vN>/lora/ + manifest  ─►  test-generate (verify on-model)
       manual temp cleanup
```

- **D1 Caption (template, v1 — no VLM)** — loom builds a **deterministic structured caption per
  ref** from the P1 **coverage-cell metadata** (shot-size/angle/expression/background) + a **unique
  trigger token** (e.g. `mara_lw`). Identity stays *implicit*; captions describe **what varies**,
  not the constant identity (research). **Reviewable/editable** before training. The caption template
  and source-field contract are recorded in `caption_policy.json`. (VLM enrichment → P4.)
- **D2 Readiness (proxies, v1 — no VLM)** — coverage from the recipe metadata, **perceptual-hash**
  dupes, **face-embedding** on-model vs. the anchor, caption presence → a **readiness meter**; below
  threshold → suggests "re-roll these cells in Stage B" (back to P1), **advisory, not a hard block**.
  - *(P1 contract note, 2026-06-11: `coverage_cell.background` **may be empty** — P1/M3 realizes
    cells via img2img with `background:""` (the bg-diversity axis is deferred to inpaint
    realization, `kb-loom-p1.md` §7.1 note). D1's `build_caption` already **omits an empty
    background** (contract-frozen, CONTRACT_VERSION=1); D2 must score the background axis as
    **advisory-only** until inpaint-realized cells exist.)*
- **D3 Train** — the **training worker** (§8) runs in temp on the single-GPU queue: base = the
  version's family (zimage first), **train-from-base by default / seed-from-parent optional** (R68),
  trigger token baked in. Long job: progress by step/epoch, checkpoints + sample images in temp.
  - **Staged training jobs (R118) + their storage (R123).** Configuring a training run
    **auto-generates the job spec(s) but does NOT add them to the queue**. They persist in a
    **separate `jobs/staged.json`** (durable — survives restart), **not** in `queue.json` (which
    stays strictly "things that will run", so the GPU worker can never accidentally pick up a staged
    job — it doesn't see them). The author reviews/finalizes, then **"Add to queue"** transitions
    `staged → queued` (moves the record from `staged.json` into `queue.json`). Only then does it
    consume GPU. A Comprehensive dataset / multiple versions may stage several jobs at once. This
    extends propose-and-approve (R14) to the most expensive operation and pairs with the queue's
    resume-*paused* behavior (R88): **nothing spends GPU until you say so.**
- **E Promote** — copy the final LoRA → `versions/<vN>/lora/`, write `lora.manifest.json`, set the
  version's `lora` ref + `trigger_token`; include `caption_policy_hash` and `context_digest` in the
  manifest; **manual cleanup** of temp.
- **Verify** — a **test generation** with the LoRA loaded (via the new inference LoRA flags, §8.1
  item 1) confirms the character reproduces → the done-line.

---

## 6. Structured captioning (grounded in research)

Current character-LoRA practice (Civitai/Flux/Z-Image guides, `kb-loom-p1.md` §7.1 sources) is
explicit: **use structured LLM captions, not auto-tagging** (auto-tags degrade training), caption
**similar images together** for consistency, and use a **unique trigger token**.

loom's captioning (D1) — **v1 is template-only (author, round-15 answer 2):**

- **Template captions (v1, no VLM):** loom builds each caption **deterministically from the P1
  coverage-cell metadata + trigger token** — e.g. `"mara_lw, profile view, waist-up, neutral
  expression, market background"`. This is **maximally consistent** (the research's top priority)
  and needs **no VLM**, which keeps P2's core path VLM-free and de-risked. We already have the
  coverage tags from P1, so captioning is essentially free.
- **Trigger token:** unique per character/version (suggested + editable).
- **Editable:** captions land in `captions.jsonl` for human review/edit before training.
- **Policy recorded:** the exact template version + source fields land in `caption_policy.json`, so
  a later P4/P6 context or GraphRAG tool can distinguish "caption changed" from "model/trainer
  changed."
- **Optional VLM enrichment — deferred to P4 (author, round-15 answer 2).** Later, Qwen3-VL can
  *enrich* the template caption with observed detail (lighting, incidental props). The author's
  bigger idea: build a **comprehensive, project-wide VLM context during L1/L2 authoring** (world,
  style, cast) so the VLM's captions/scoring/assistance are project-aware — flagged as a **P4
  investigation**, not P2. P2 ships template-only.

---

## 7. Readiness meter — cheap proxies in v1 (VLM scoring deferred to P4)

P1 curation was human-only; P2 adds a **readiness meter**. Because captioning is template-only and
the VLM is deferred (answer 2), **v1 readiness uses cheap, VLM-free proxies** built from signals we
already have:

- **Coverage (free):** the **P1 coverage-cell metadata** directly shows which matrix cells
  (angle/shot-size/expression) are thin → "re-roll just these" guidance (back to P1 Stage B).
- **Near-duplicates (cheap):** **perceptual hash** clustering — no VLM needed.
- **On-model (cheap) — works with *or without* an anchor (R120):** a **face-embedding** check
  (ArcFace/PuLID). **If a face anchor exists** (P1, optional), score each ref by **distance to the
  anchor**. **If no anchor** (the P1 guardrail allows none — R110), fall back to **set
  self-consistency**: embed all refs, take the **centroid**, and flag **outliers** from it. Either
  way the proxy runs — so **P2 does not require the face-anchor step**, keeping the P1 "MVP works
  with no anchor" guarantee intact. (An anchor, when present, gives a firmer reference.)
- **Captions present:** template captions exist for every ref (trivially true).
- **Readiness meter:** a single signal (coverage ✓, on-model ✓, dupes low, captions ✓) that gates
  the **Train** button as *recommended* — **advisory, never a hard lock** (R14: models assist,
  author decides).

**VLM-assisted scoring is a P4 enhancement** (with the comprehensive project context, §6): richer
on-model judgement and semantic coverage analysis. P2 v1 does **not** require the VLM.

---

## 8. LoRA training engine (the core P2 build)

The repo has **no image-LoRA trainer** today — this is new. **Two backends (author, round-15
answer 1):**

- **ai-toolkit (default, optimized)** — the Z-Image LoRA guides (`kb-loom-p1.md` sources) use it; a
  strong, fast-to-working default for our **first base, `zimage`** (R6), then `sd35`. Most users
  train through this with the per-model default preset and never see a knob.
- **diffusers-PEFT (advanced, deep control)** — a second backend for users who want **full control**
  of the training process (custom rank/targets/schedules/loss). Exposed under "advanced".

Both present as the same **trainer adapter** to the queue (same manifest envelope); the backend is a
choice on the Train panel (default = ai-toolkit). Requirements regardless of backend:

- **Per-base-family** (zimage first, then sd35) — a LoRA is base-family-specific (`kb-pipelines01.md`
  LoRA Primer). The trainer adapter declares which families it supports.
- **Runs as a queued, subprocess-isolated job** in the shared `.venv`, in `<project>/_temp/…`,
  competing for the 16 GB like any heavy job (never co-loaded with inference or the VLM — unload
  the VLM first, R21).
- **Train-from-base default; seed-from-parent optional** (R68) — a toggle exposed at Stage D.
- **Optimal default preset per base model (R117).** Each base family (zimage, sd35) ships a
  **single conservative "just works" preset** (rank/alpha/lr/steps/precision) tuned for that model;
  most users never touch it. **Advanced knobs are available on demand** (and are the natural home
  of the PEFT backend). We find/validate the per-model optimum during the training spike (M1).
- **Records a full training manifest** (Codex): dataset snapshot hash, captions hash, caption policy
  hash, compact context digest, base model + family, trigger token, trainer config
  (rank/alpha/lr/steps/precision), seed, output hash, duration.
- **ROCm/16 GB-aware:** image-LoRA training fits (kb-pipelines01: PEFT LoRA on a 3B model fits
  16 GB; image LoRAs are similar order). Expose offload/precision knobs; image-LoRA scratch is
  ~1–5 GB/run (`kb-loom-p0`/§4 estimates), well within the work-disk cap.
- **Inference LoRA loading** (paired build, §8.1 item 1): add `--lora-path/--lora-name/--lora-weight`
  to `zimage`/`sd35` so the trained LoRA is usable in generation and the **verify** step works.

---

## 9. VLM: deferred to P4 (P2 v1 is VLM-free)

Per round-15 answer 2, **P2 v1 does not bring the VLM online.** Template captions (§6) + proxy
readiness (§7) cover the core training path without Qwen3-VL, which **removes a whole subsystem from
P2** and de-risks it. The VLM tenant manager comes alive in **P4**, alongside the **comprehensive
project-context** the author wants to build during L1/L2 authoring (world/style/cast aware), which
is the right foundation for *good* VLM captioning/scoring rather than a bolt-on here.

When P4 activates it: Qwen3-VL-4B (on disk, R22) runs as an **on-demand** `llama-server` (handrefiner
pattern, `kb-storyboard01.md` §7.4), is a **pausable tenant** unloaded before heavy jobs (R21), and
enriches captions + scoring with project context. **None of that is built in P2.**

---

## 10. Disk & VRAM

- **Disk:** training temp is the main consumer in P2 — image-LoRA runs ~1–5 GB each (R-§4); the
  **two-threshold hard stop** (warn <5% / stop <2%, R96) and the per-project cap apply (default
  **250 GB**, R164). Many image-LoRA runs fit comfortably. **Video LoRAs (~20–80 GB) are out of P2.**
- **VRAM:** one heavy thing at a time — trainer **or** VLM **or** an inference job, never together.
  The queue serializes; the VLM unloads before training (R21). Expose precision/offload for the
  trainer to stay within 16 GB on the RX 9070 XT.

---

## 11. Risks & guardrails

1. **UI debt compounds quickly.** P2 adds training controls, P3 adds shots/audio, and P4 adds Flow;
   if the cramped P1 layout remains, every later feature will inherit that pressure. **Guardrail:**
   M0 is a focused interaction refactor: navigation, L1 tabbing, and postprocess separation only.
   Do not redesign the visual language or rebuild unrelated state/contracts here.
2. **The trainer is new code/vendoring, not a wrapper (the big one).** P0/P1 normalized existing
   inference CLIs; P2 must stand up training. **Guardrail:** prefer **vendoring a proven trainer**
   (ai-toolkit for zimage) over building one; onboard it through the **same job-queue + manifest
   envelope**; prove **one base (zimage)** end-to-end before sd35. Time-box a **training spike**
   first (train *any* LoRA from a fixed dataset, ignore UI) to de-risk before wiring the workflow.
3. **ROCm training reality.** Training on RX 9070 XT / ROCm may hit kernel/precision issues
   (cf. `kb-trellis2.md` ROCm workarounds). **Guardrail:** validate the trainer on ROCm in the spike;
   keep precision/offload knobs; document any ROCm-specific settings in the training manifest.
4. **Resist pulling the VLM into P2.** The roadmap originally placed VLM-assisted scoring here, but
   round-15 deferred it (R116). **Guardrail:** keep P2 **VLM-free** — template captions + proxy
   readiness only. The VLM (and its project context) is a P4 subsystem; building it here would
   re-inflate the phase we just trimmed. If on-model proxy (face-embedding) proves weak, improve the
   *proxy*, don't reach for the VLM.
5. **Resist pulling GraphRAG into P2.** GraphRAG is promising for project-wide relational retrieval,
   but it is not needed to train a character LoRA. **Guardrail:** P2 writes graph-ready facts
   (`training_context.json`, `caption_policy.json`, manifest hashes) and stops there. Retrieval
   index build/query remains P6/post-v1 (R170).
6. **Readiness must not block the author.** **Guardrail:** the readiness meter is **advisory** —
   it *recommends*, never *forbids* training (R-philosophy: assist, author decides).
7. **Don't regress the MVP.** P1's done-line (saved curated profile, no training) must keep working
   if P2 is incomplete. **Guardrail:** training is **additive** to a version; an untrained version
   is still valid and usable (prompt + anchor), per P1.

---

## 12. P2 milestones — UI reset, then walking skeleton first

### Phase 0 — MVP usability reset (before trainer risk)

0. **M0 — shell/workspace UI + postprocess workflow reset.** Tighten the current MVP before adding
   P2 training controls:
   - **File menu:** move top-bar project actions (`New`, `Open`, `Close`, and future project/file
     operations) into a conventional **File** menu. The top bar remains for status, current project,
     queue/health indicators, and compact global actions.
   - **Workspace tabs:** make L1/L2 operate as true workspace tabs, with future L3/L4 designed to
     slot into the same model. Each workspace owns its controls when selected instead of sharing a
     cramped always-visible controls strip. Make the Asset panel 20% wider.
   - **L1 sub-tabs:** split L1 into **Visual Styles**, **World**, and **Story Spine** tabs. Each tab
     shows only its relevant controls. Long-form fields, especially style/world prose and negative
     constraints, use readable multi-line editors sized for review/editing, not two-line inputs.
   - **L2 postprocessing as an independent stack:** separate base-image generation from
     postprocessing. `Clean`/`Refine` become presets over a general i2i/postprocess step (different
     strengths, same concept), not special one-off workflow buttons. A selected base image can have
     an ordered stack of postprocess steps; each step is independently configurable, queueable, and
     records its source image + params + output.
   - **Mask-ready postprocess contract:** postprocess steps may optionally require or consume masks
     (manual mask, generated mask, or future SAM/Grounded-SAM style tools). M0 only needs the UI/data
     shape and source/output lineage; later adapters can plug into the same stack without crowding
     the Asset Studio.
   - **M0d — flux.2 advanced prompting + sampling presets** (added 2026-06-20; full design below).
     flux2 `ref`-mode Stage-B identity expansion holds identity well but **follows pose loosely**
     (e.g. "three-quarter left" → body one way, head the other). Two levers FLUX.2 offers but loom
     doesn't yet use: **structured/labeled (optionally JSON) prompting** with explicit camera+pose
     directives, and **configurable guidance/steps** (the default klein variants are step-distilled,
     so adherence is capped). Add both: a flux2 structured-prompt builder + an individually-editable
     `guidance`/`num_steps` pair fronted by a **Sampling preset pull-down** (≥3 researched presets).

   **M0 done-line:** the app still performs the P1 MVP flow, but the shell uses File-menu project
   actions, L1/L2 are tabbed workspaces, L1 content is readable in sub-tabs, L2 can generate a
   base image then run at least one independent i2i/postprocess preset from a stack-like surface,
   **and a flux2 Stage-B expansion can be fired with structured prompting + a chosen sampling preset
   (or hand-set guidance/steps), producing visibly tighter pose adherence than the distilled default.**

#### M0d solution design — flux.2 advanced prompting & sampling presets

*Status: design (2026-06-20), not yet implemented. Web research sourced below.* Pull the flux.2
"complex prompting" capability into M0 (author request). Goal: make flux2 `ref`-mode expansion (and
flux2 t2i casting) **obey pose/composition reliably**, by (A) richer structured prompting and (B)
exposing + presetting the sampling knobs. Both are **additive** — no change to the frozen
coverage-cell contract ([[coverage]]) or the §11 `ref`-mode wiring; the structured prompt + sampling
choice ride the existing catalog `params` channel + the flux2 adapter's `_SHARED_KEYS`.

**Why pose is hit-and-miss (root cause).** The default `flux.2-klein-4b/9b` are **step-distilled**
(4 steps, CFG pinned ≈1.0) → they follow the text prompt *loosely* and ignore guidance, and loom asks
with a **flat** per-cell string (`<cell fragment>, <clause>, <style>`) whose "three-quarter left view"
is a weak steer the identity reference easily overrides on composition. FLUX.2 supports far more:
prompt **structure with leading-token priority**, **JSON/labeled structured prompts**, and (on
*non-distilled* variants) **configurable guidance/steps**.

**A. Advanced / structured prompting.**
- Adopt FLUX.2's structure — **Subject → Action → Style → Context, most-important-first** (FLUX
  attends most to leading tokens), medium length (~30–80 words). **No negative prompts** — describe
  positively (loom already drops the L1 global-negative for flux2; keep that).
- Build flux2 cell prompts from a **labeled, semi-structured** template — robust across the Qwen3-text
  klein variants — with an optional **true-JSON** form for the Mistral-VLM `flux.2-dev` (which
  interprets JSON precisely). Schema (BFL / RunDiffusion): `scene · subject{description, pose,
  position} · camera{angle, lens, depth_of_field} · lighting · style · mood · color_palette`.
- ⭐ **Pose is the fix:** map each coverage **angle** to an EXPLICIT camera+pose directive instead of
  the loose "…view" phrase — e.g. `front` → "facing the camera directly"; `three_quarter_left` →
  "body AND head both turned three-quarters to the viewer's left (¾ view)"; `profile_left` → "full
  left profile, looking left". This lands the head/body alignment the flat phrasing misses. A small
  angle→directive table beside [[coverage]] (the coverage *vocab* stays frozen; this is just how we
  phrase it to flux2).
- loom assembles the structured prompt **deterministically** from the frozen cell + character clause
  + L1 style (a flux2 prompt-builder alongside `recipe.py`). UX: an **"advanced prompting" toggle**
  (off ⇒ today's flat string), a **per-cell prompt preview/edit** (reuse the dry-run pre-flight
  modal), and an optional power-user **structured/JSON override** field.

**B. Guidance/steps — individually configurable + a Sampling preset pull-down.**
- Surface **`guidance`** and **`num_steps`** as first-class, individually-editable fields on the
  flux2 Stage-B + t2i-cast bars (they already validate via the catalog/params channel; M0d promotes
  them to the front and makes the model↔guidance pairing sane).
- A **"Sampling" pull-down** with ≥3 researched presets — each sets model_name + steps + guidance:

  | Preset | Model | Steps | Guidance | Use |
  | --- | --- | --- | --- | --- |
  | **Fast (draft)** | klein-4b/9b *distilled* | 4 | 1.0 | quick exploration; loose adherence — **today's default** |
  | **Balanced** ⭐ | klein-**base**-4b/9b | 24 | 4.0 | good pose/prompt adherence, moderate cost — the pose fix |
  | **Quality** | klein-**base**-9b | 40 | 4.5 | strongest adherence; slow + needs cpu-offload |
  | **Dev / JSON** *(opt)* | flux.2-dev | 50 | 4.5 | Mistral-VLM; best true-JSON prompting (gated weights) |
  | **Custom** | — | *(field)* | *(field)* | the individual guidance/steps fields drive it |

  *(Researched values: distilled klein = 4 steps / CFG ≈1; **base** klein = 20–24 steps / CFG 3.5–5.0;
  dev & general FLUX.2 = 30–50 steps / guidance ≈4.5.)*
- **Guard:** distilled variants ignore CFG (guidance pinned ≈1), so a high guidance only bites on
  `-base`/dev. The pull-down enforces sane model↔guidance pairings; the Custom fields warn when
  guidance > ~1.5 on a distilled model (no effect). **Fast stays the default** (speed); **Balanced**
  is the recommended one-click pose fix.

**Constraints / risks.** klein uses the Qwen3 *text* encoder (JSON less precise than dev's Mistral
VLM) → default to the labeled semi-structured form, reserve true-JSON for dev. base/dev are heavier
(24–50 steps, cpu-offload) on the 16 GB ROCm target → keep Fast default; presets, not forced. M0d
improves adherence but is **not** ControlNet-precise pose control.

**Out of scope (later).** ControlNet/OpenPose/depth pose conditioning → P3 keyframes (R128) / P6 3D;
**Muse SLM auto-authoring** of these structured prompts → P3/P4 (M0d's deterministic builder is the
precursor the SLM later enriches). Per-output **identity strength** (PuLID-class) → P5 Track B.

**Sources (web research 2026-06-20):**
[BFL FLUX.2 prompting guide](https://docs.bfl.ml/guides/prompting_guide_flux2) ·
[RunDiffusion — Flux 2 JSON / structured prompting](https://www.rundiffusion.com/flux-2-prompting) ·
[RunDiffusion — Klein base vs distilled (steps/CFG)](https://learn.rundiffusion.com/flux-2-klein-three-new-models/) ·
[ltx.io — Flux prompting guide](https://ltx.io/blog/flux-prompting-guide) ·
[fal — Flux 2 [klein] user guide](https://fal.ai/learn/devs/flux-2-klein-user-guide).

### Phase A — Training skeleton (prove a LoRA can be made + used on this rig)

1. **M1 — training spike (no UI).** Vendor **ai-toolkit**; train **one** `zimage` LoRA from a fixed
   P1 `ref_set` on ROCm; load it at inference and confirm it reproduces the character. Find the
   per-model default preset here. *(Retires the core risk before any workflow wiring.)*
2. **M2 — trainer *skeleton* as a (staged) queued job.** Wrap the trainer in the P0 job-queue +
   manifest envelope with **staged-job** semantics (auto-generate, explicit "add to queue", R118);
   train from the Asset Studio with a **[Train LoRA]** button → temp run → promote → verify.
   *(**Skeleton only — NOT the P2 done-line, R169:** trains from a manual/fixed ref set + default
   preset. The done-line (template-caption → readiness "good to train" → train, §1) is reached after
   **M3 captions + M4 readiness**.)*

### Phase B — Thicken (all VLM-free)

3. **M3 — template captioning.** Generate `captions.jsonl` deterministically from coverage-cell
   metadata + trigger token; write `caption_policy.json`; review/edit UI. **(No VLM.)**
4. **M4 — proxy readiness.** Coverage (from metadata) + perceptual-hash dupes + face-embedding
   on-model → `readiness.json` → advisory readiness meter. **(No VLM.)** ***P2 done-line now
   reachable*** (M2 trainer + M3 captions + M4 readiness, R169).
5. **M5 — train options + sd35 + PEFT backend.** Expose train-from-base / seed-from-parent (R68) and
   the per-model preset + advanced knobs; add the **diffusers-PEFT** advanced backend; onboard the
   **sd35** trainer; record full training manifests.
6. **M6 — promote + manual cleanup + LoRA management.** Promote into the version; one-click temp
   cleanup; version selector shows LoRA presence. **(No style-LoRA path — declared only, 0 effort in
   P2 per R122; built in P5 with multi-LoRA stacking, R147.)**

### Done-line

7. **M7 — acceptance.** A P1 character → template-captioned → proxy-readiness ✓ → **staged → added to
   queue → trained → promoted → test-gen reproduces it on-model**, all recorded in the training
   manifest (§1), with `caption_policy_hash` + `context_digest` present.

---

## 13. Out of scope (defer)

- **The VLM / Qwen3-VL online path → P4** (R116): VLM-assisted captioning/scoring + the
  **comprehensive project-wide VLM context** built during L1/L2 authoring. P2 is VLM-free.
- **GraphRAG / retrieval index → P6/post-v1** (R170): P2 emits structured facts only; it does not
  build or query a persistent retrieval index.
- **Video LoRAs** (LTXV/Wan) → later (need video work + larger scratch).
- **Style LoRA** — **declared only**, trained later (when **multi-LoRA stacking, P5** — R147, moved
  from P6 — makes it usable). P2 does not build a style-LoRA path (R122).
- **Muse/creative SLM** → P3/P4. **Multi-LoRA stacking** (character+style at inference) → **P5** (R147, moved from P6).
- Shots/audio/Flow/Episode → P3+.

---

## 14. Resolved (round 15) & still-open

**Resolved (R115–R118, in `kb-storyboard01.md` §10.0):**

| # | Decision |
| --- | --- |
| R115 | Trainer = **ai-toolkit default (optimized)** + **diffusers-PEFT advanced** (deep control) backend (§8). |
| R116 | Captioning = **template-only for v1** (no VLM); **template + optional VLM enrichment + comprehensive project-wide VLM context → P4** (§6). |
| R117 | **Optimal default preset per base model**, **advanced knobs on demand** (§8). |
| R118 | **Staged training jobs:** auto-generate the job spec but **don't auto-queue**; the author **explicitly adds it to the queue** when finalized (§5). |

No P2 questions remain open. (P4 will need to scope the **project-wide VLM context** the author
flagged — noted for `kb-loom-p4.md`.)

## 15. Work-package breakdown (WBS) — what P2 actually contains

*Rough solo-dev sizing: **S** ≤1 d · **M** 2–4 d · **L** 1–2 wk · **XL** 3 wk+. Risk: 🟢 routine ·
🟡 unknowns · 🔴 R&D / make-or-break. Maps to the §12 milestones.*

| WP | Work package | Maps to | Size | Risk |
| --- | --- | --- | --- | --- |
| P2-M0a | **Shell + workspace navigation reset** — File menu for project actions; tabbed L1/L2 workspace navigation with room for future L3/L4 controls | M0 | M | 🟢 |
| P2-M0b | **L1 tabbed authoring** — Visual Styles / World / Story Spine sub-tabs; readable multi-line editors for long style/world/spine fields | M0 | M | 🟢 |
| P2-M0c | **L2 postprocess stack surface** — base-image generation separated from postprocess; clean/refine as i2i presets; source/output lineage and mask-ready step contract | M0 | M | 🟡 |
| P2-M0d | **flux.2 advanced prompting + sampling presets** — structured/labeled (opt-JSON) flux2 prompts with explicit angle→camera/pose directives (the pose-adherence fix) + individually-editable guidance/steps fronted by a ≥3-preset Sampling pull-down (Fast/Balanced/Quality, model↔guidance pairing guard). Additive (no coverage-contract change). Design: §12 "M0d solution design" | M0 | M | 🟡 |
| P2-0 | **ai-toolkit ROCm *can-it-run-at-all* gate** — prove ai-toolkit trains on **RX 9070 XT / ROCm** before the rest of P2 is built; **hard go/no-go** (if no-go, the whole training approach changes) | M1 | M | 🔴 **make-or-break front-gate** |
| P2-1 | **Training spike (no UI)** — vendor **ai-toolkit**, train one `zimage` LoRA from a fixed dataset, load-test it | M1 | M | 🔴 **no trainer exists** |
| P2-2 | Trainer as a **staged queued job** (wrap in P0 queue + manifest; `jobs/staged.json`; auto-generate, don't auto-start) | M2 | M | 🟡 |
| P2-3 | Template captioning (`captions.jsonl` + `caption_policy.json` deterministically from coverage-cell metadata; no VLM) | M3 | S | 🟢 |
| P2-4 | Proxy readiness meter (coverage + perceptual-hash dupes + face-embedding centroid; **no-anchor fallback**) | M4 | M | 🟡 |
| P2-5 | Train options + **`sd35` base** + **PEFT backend** (train-from-base / seed-from-parent; second base model; diffusers-PEFT advanced path) | M5 | L | 🟡 two trainers, two bases |
| P2-6 | Promote + manual cleanup + LoRA management (promote into version; one-click temp cleanup; LoRA list/attach) | M6 | M | 🟢 |
| P2-7 | Acceptance: P1 char → captioned → readiness ✓ → staged → queued → trained → promoted | M7 | S | 🟢 |
| P2-9 | **Training VRAM-fit presets** — rank/resolution/batch/grad-accum/precision combos that *fit 16 GB*, pinned **per base model** (`zimage`, `sd35`) | M5 | M | 🟡 *folded from gap* |
| P2-10 | **Long-job resume-from-checkpoint** — a multi-hour training job survives queue pause/relaunch (R88) by resuming from its last checkpoint, not restarting | M2 | M | 🔴 *folded from gap* |
| P2-11 | **LoRA preview before promote** — sample-gen with the freshly trained LoRA so the author eyeballs it before promoting into the version | M6 | S | 🟢 *folded from gap* |
| P2-12 | **Training-time ETA in the queue** — an honest running estimate so a multi-hour job doesn't look hung | M2 | S | 🟢 *folded from gap* |
| P2-13 | **Graph-ready training facts** — write `training_context.json`, `caption_policy_hash`, and `context_digest` into the promoted LoRA manifest; no retrieval index | M3/M6 | S | 🟢 |
| — | Style-LoRA: **declared only, not built** (R122) — 0 effort in P2; lands with multi-LoRA stacking (**P5**, R147) | §2 | — | — |

**Rollup:** ~17 WP including the M0 UI reset (now M0a–M0d); **P2-0/P2-1 remain the phase's make-or-break training
risk** — whether **ai-toolkit even trains on RX 9070 XT / ROCm**. **P2-0 is still the hard trainer
front-gate**: if it's no-go, P2-9–P2-12 and the rest of the trainer path don't get built as-is.
M0 is deliberately separate: it should land before that gate so later trainer controls have a
better surface to inhabit. Everything else is conventional once the UI reset + trainer gate/spike
are green. **P2-9–P2-12 were surfaced by the WBS gap-scan and are now planned.**

**Design note:** **P2-10 (resume-from-checkpoint) is the first real test of the resume-*paused*
queue (R88)** — training is the first job long enough that restart-from-zero is unacceptable. The
trainer must checkpoint to workspace temp on a cadence and the job-resume path must pick the latest
checkpoint, not re-run. **A training job is the first (and, in v1, only) job that sets the
`resumable=true` flag (R159, P0 §7)** — that flag is what routes it down the "resume from last
checkpoint" column instead of the default "discard + re-queue/fail" path.

## Source / traceability

- Decisions: `kb-storyboard01.md` §10.0 (R6 zimage-first, R13 scratch/cleanup, R21 VLM unload,
  R58/R68 version/training base, R96 disk guard, R98 lineage, R114 anchor).
- Engine: `kb-pipelines01.md` "LoRA Primer" + "Local Pipeline Fit"; `kb-slm.md` (TRL/PEFT pattern);
  `kb-loom-p1.md` §7.1 (dataset recipe + sources); `src/village_ai/models/` (Qwen3-VL on disk).
- Build dependency: `kb-storyboard01.md` §8.1 item 1 (LoRA flags on zimage/sd35).
- Retrieval posture: `kb-storyboard01.md` R170 — P2 writes graph-ready facts, while persistent
  GraphRAG/index/query is deferred.
- UI preflight: author feedback 2026-06-18 — P1 MVP usable, but shell navigation, L1 density, and
  L2 postprocess workflow need correction before P2/P3 functionality piles onto them.
