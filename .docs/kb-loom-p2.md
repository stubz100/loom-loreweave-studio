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
> adherence in flux2 `ref`-mode expansion — design in §12 "M0d solution design". **Extended same day
> with Part C:** a `flux.2-dev` **structured-JSON prompt tree** in the params panel for t2i/i2i
> authoring straight from the schema. **M0e added 2026-06-21** (the final course-correction before the
> M1 trainer gate): **flux.2 low-res-first + creative upscale** — default `flux.2-dev` to 512² (it runs
> far faster at low res on 16 GB ROCm), add an **output size** to the M0c i2i postproc steps (i2i
> upscale), and a dedicated **`Upscale ✨`** preset on the already-registered **SD3.5 Tile ControlNet**
> — design in §12 "M0e solution design". **M2.5 added 2026-06-26:** replace Loom's `flux.2-dev`
> runtime with the Comfy-Org quantized Flux2-dev split checkpoint proven in the old
> `src/pipeline/flux2_q8` spike; this is **dev-only** and covers t2i/i2i **and** the batch `ref`
> Stage-B coverage sweep (the expansion/curation screen — batch routing added same day, reversing the
> initial Klein-only call). It also **deletes both gated heavyweight repos** (`black-forest-labs/FLUX.2-dev`
> 166 GB + `mistralai/Mistral-Small-3.2-24B` 90 GB): the ~17 MB dev config+tokenizer are vendored and
> Klein's VAE re-points to the identical Comfy `flux2-vae`. Klein keeps its Qwen3/Klein loader path
> (only its VAE weight source moves).

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
  postprocessing into a stackable, independent image-postprocess workflow; **(M0d) flux.2
  advanced prompting — structured prompts + configurable guidance/steps with a sampling-preset
  pull-down, plus a `flux.2-dev` structured-JSON prompt tree for t2i/i2i authoring** to fix loose pose
  adherence in flux2 `ref`-mode expansion (§12 M0 + "M0d solution design"); and **(M0e) flux.2
  low-res-first + creative upscale — `flux.2-dev` defaults to 512², an output size (scale + explicit
  W×H) on the M0c i2i postproc steps, and a dedicated `Upscale ✨` SD3.5 Tile-ControlNet preset** (§12
  "M0e solution design").
- **M2.5 `flux.2-dev` quantized replacement + gated-repo elimination:** route Loom's dev-only Flux2
  runtime/model-gating path to the Comfy-Org quantized Flux2-dev split files (`flux2_dev_fp8mixed`,
  Mistral fp8/bf16 text encoders, Flux2 VAE) by **folding** the quantized branch into the existing
  flux2 runner, preserving the **single-run** dev JSON/t2i/i2i authoring surfaces (the batch `ref`
  Stage-B sweep stays Klein-only — see §12 "M2.5 solution design" Scope). **Eliminate both gated
  heavyweight repos** (`black-forest-labs/FLUX.2-dev` 166 GB + `mistralai/Mistral-Small-3.2-24B`
  90 GB): vendor the ~17 MB dev config+tokenizer locally, and re-point **Klein's VAE** to Comfy's
  identical `flux2-vae.safetensors`. Klein's runtime path / Stage-B behavior is otherwise unchanged
  (only its VAE *weight source* moves; Qwen3 flow/TE gates stay).
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
   - **M0d — flux.2 advanced prompting + sampling presets + dev JSON prompt tree** (added 2026-06-20;
     full design below). flux2 `ref`-mode Stage-B identity expansion holds identity well but **follows
     pose loosely** (e.g. "three-quarter left" → body one way, head the other). Three levers FLUX.2
     offers but loom doesn't yet use: **structured/labeled (optionally JSON) prompting** with explicit
     camera+pose directives; **configurable guidance/steps** (the default klein variants are
     step-distilled, so adherence is capped); and — on **`flux.2-dev`** — a **structured-JSON prompt
     tree** for authoring **t2i/i2i** images straight from the schema. Add all three: a flux2
     structured-prompt builder + an individually-editable `guidance`/`num_steps` pair fronted by a
     **Sampling preset pull-down** (≥3 researched presets) + a dev-gated **JSON entry tree** (Part C).
   - **M0e — flux.2 low-res-first + creative upscale** (added 2026-06-21; full design below). The
     final course-correction before the M1 trainer gate. `flux.2-dev` (the gated Mistral-VLM variant)
     runs **exponentially faster at low resolution** on the 16 GB ROCm rig (512² ≈ 1 k image tokens vs
     ~4 k at 1360×768 — `kb-flux2.md` "denoising stall analysis"), so the efficient workflow is
     **author at 512² with dev, then i2i-upscale**. Three additive levers: **(a)** default the dev
     image size to **512²** when `flux.2-dev` is the selected model (display==reality, M0c discipline);
     **(b)** expose an **output size** (scale-factor quick pick **and** explicit W×H override) on the
     M0c i2i postprocess steps so a `Clean`/`Refine` step over **zimage/sd35** can re-diffuse larger =
     i2i creative upscale (not flux2 — flux2 i2i re-poses at source dims); **(c)** a dedicated
     **`Upscale ✨`** postprocess preset driving the already-registered **SD3.5 Tile ControlNet**
     (`InstantX/SD3-Controlnet-Tile`, §8/M6+ "planned") as a **single-run `sd35` cn-inpaint** job at the
     target size — the structure-preserving high-ratio upscale path the i2i preset can't match.

   **M0 done-line:** the app still performs the P1 MVP flow, but the shell uses File-menu project
   actions, L1/L2 are tabbed workspaces, L1 content is readable in sub-tabs, L2 can generate a
   base image then run at least one independent i2i/postprocess preset from a stack-like surface,
   **a flux2 Stage-B expansion can be fired with structured prompting + a chosen sampling preset
   (or hand-set guidance/steps), producing visibly tighter pose adherence than the distilled default,
   and — with `flux.2-dev` selected — a t2i/i2i image can be authored from the structured-JSON prompt
   tree (Part C); selecting `flux.2-dev` defaults the size to 512², and a base image can be
   creatively upscaled both via an i2i postprocess step with a chosen output size and via the
   dedicated `Upscale ✨` SD3.5 Tile-ControlNet preset (M0e).**

#### M0d solution design — flux.2 advanced prompting & sampling presets

*Status: **✅ IMPLEMENTED 2026-06-20** — Parts A (structured prompting) + B (sampling presets) +
C-t2i (dev JSON tree) + C-i2i (flux2-img2img on the M0c postproc step). See `kb-loom-p2-imp.md`
"### M0d" (commits `d327719`/`d88eb01`/`3aac9ac`/`16874e4`). Original design below; web research
sourced at the end.* Pull the flux.2 "complex prompting" capability into M0 (author request). Goal: make
flux2 `ref`-mode expansion (and flux2 t2i casting) **obey pose/composition reliably**, by (A) richer
structured prompting, (B) exposing + presetting the sampling knobs, and (C) — when **`flux.2-dev`** is
the selected model — a **structured-JSON prompt tree** in the params panel for authoring **t2i/i2i**
images directly from the schema. All three are **additive** — no change to the frozen coverage-cell
contract ([[coverage]]) or the §11 `ref`-mode wiring; the structured prompt + sampling choice + JSON
tree ride the existing catalog `params` channel + the flux2 adapter's `_SHARED_KEYS`.

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
  modal), and — on `flux.2-dev` — the **structured-JSON prompt tree** of Part C as the power-user
  override (replacing the raw free-text JSON field the first draft sketched).

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
  | **Dev / JSON** *(opt)* | flux.2-dev | 8 | 4.0 | quantized Comfy dev; Mistral-VLM true-JSON prompting; 512² then upscale |
  | **Custom** | — | *(field)* | *(field)* | the individual guidance/steps fields drive it |

  *(Researched values: distilled klein = 4 steps / CFG ≈1; **base** klein = 20–24 steps / CFG 3.5–5.0;
  full dev & general FLUX.2 = 30–50 steps / guidance ≈4.5; M2.5's quantized Comfy dev path uses the
  proven 8-step/q8 profile.)*
- **Guard:** distilled variants ignore CFG (guidance pinned ≈1), so a high guidance only bites on
  `-base`/dev. The pull-down enforces sane model↔guidance pairings; the Custom fields warn when
  guidance > ~1.5 on a distilled model (no effect). **Fast stays the default** (speed); **Balanced**
  is the recommended one-click pose fix.

**C. `flux.2-dev` structured-JSON prompt tree (t2i + i2i authoring).** *(author request 2026-06-20)*
Part A's structured prompt is loom-assembled from a coverage cell; Part C lets the author **drive the
JSON schema directly** as a first-class prompt for **stand-alone t2i and i2i** (not tied to a coverage
cell), because `flux.2-dev`'s Mistral VLM interprets true JSON precisely.
- **Trigger / gating.** The tree appears **only when the selected model is `flux.2-dev`** (the
  JSON-faithful variant). On klein (Qwen3-text) it stays hidden — klein keeps the Part A labeled
  semi-structured string (JSON there is unreliable; offering it would mislead). Selecting/clearing
  `flux.2-dev` reveals/collapses the tree without touching the plain prompt field.
- **The tree (entry form).** A **collapsible node editor** mirroring the BFL/RunDiffusion schema, each
  field optional (omitted fields are dropped from the emitted JSON, never sent empty):
  `scene` · `subjects[]` { `description`, `pose`, `position` } · `camera` { `angle`, `lens`,
  `depth_of_field` } · `lighting` · `style` · `mood` · `color_palette[]`. `subjects` and
  `color_palette` are **add/remove arrays**; `camera.angle` offers the same coverage angle→directive
  vocabulary as Part A (so the pose fix is reusable here). A **"view raw JSON"** affordance shows the
  exact serialized object and allows paste-in (parsed back into the tree; invalid JSON flagged, never
  silently sent).
- **Serialization.** The tree serializes to a compact JSON **string** that becomes the flux2 `prompt`
  on the existing catalog `params` channel — **no adapter/contract change**. (BFL accepts a JSON
  object *as* the prompt; loom sends it as the prompt string the dev VLM parses.) An empty tree ⇒
  fall back to the plain text prompt, so the field is never required.
- **t2i.** Tree → JSON prompt → standard flux2-dev t2i cast (uses the Part B Dev/JSON preset:
  50 steps / guidance ~4.5). This is general image authoring, available on the **t2i cast bar**, not
  only character coverage.
- **i2i.** Same tree, but a **reference/source image rides as `img_cond`** (the existing i2i/postproc
  channel from M0c) while the JSON describes the **target** — re-pose, restyle, or edit an existing
  image with precise structured intent. Lives on the M0c **postprocess/i2i step** when the step's
  model is `flux.2-dev`, so Part C and the M0c stack share one surface.
- **Seeding (nice-to-have).** In `ref`-mode Stage-B the deterministic Part A builder can **pre-fill**
  the tree from the coverage cell + character clause; the author then tweaks nodes before firing.
  Keeps Part A (auto) and Part C (manual) as two views of the same schema rather than parallel code.

**Constraints / risks.** klein uses the Qwen3 *text* encoder (JSON less precise than dev's Mistral
VLM) → default to the labeled semi-structured form, reserve true-JSON for dev. base is heavier
(24–50 steps, cpu-offload) on the 16 GB ROCm target; M2.5 dev uses an 8-step quantized profile →
keep Fast default; presets, not forced. M0d
improves adherence but is **not** ControlNet-precise pose control. **Part C depends on `flux.2-dev`
being runnable on the rig** — after M2.5 this means the quantized Comfy dev path, not the old gated
full-dev stack; if dev proves impractical on ROCm, Part C ships **disabled-until-available** (the tree
is dev-gated anyway, so klein/base users see no regression). The JSON tree is **authoring
UI, not validation of model output** — a well-formed tree doesn't guarantee the VLM honors every node.

**Out of scope (later).** ControlNet/OpenPose/depth pose conditioning → P3 keyframes (R128) / P6 3D;
**Muse SLM auto-authoring** of these structured prompts → P3/P4 (M0d's deterministic builder is the
precursor the SLM later enriches). Per-output **identity strength** (PuLID-class) → P5 Track B.

**Sources (web research 2026-06-20):**
[BFL FLUX.2 prompting guide](https://docs.bfl.ml/guides/prompting_guide_flux2) ·
[RunDiffusion — Flux 2 JSON / structured prompting](https://www.rundiffusion.com/flux-2-prompting) ·
[RunDiffusion — Klein base vs distilled (steps/CFG)](https://learn.rundiffusion.com/flux-2-klein-three-new-models/) ·
[ltx.io — Flux prompting guide](https://ltx.io/blog/flux-prompting-guide) ·
[fal — Flux 2 [klein] user guide](https://fal.ai/learn/devs/flux-2-klein-user-guide).

#### M0e solution design — flux.2 low-res-first + creative upscale

*Status: **✅ IMPLEMENTED 2026-06-21** — Parts A (dev 512² default) + B (i2i output size on the M0c
postproc steps) + C (the `Upscale ✨` SD3.5 Tile-ControlNet preset) all built same day (284 backend
tests; visual sign-off owed). Original design below. **No new worker capability** — flux2/sd35 workers
already did everything; M0e is catalog + orchestrator + frontend surface, reusing the M0c postprocess-
stack contract (no `src/pipeline/` change → no re-vendor). See `kb-loom-p2-imp.md` "### M0e".*

**Why low-res-first for `flux.2-dev`.** dev is the 32B Mistral-VLM variant — the JSON-faithful one
the author wants for precise prompting — but on the 16 GB ROCm rig it is the heaviest: its 60 GiB bf16
flow transformer is paged from system RAM, and cost scales with **token count², dominated by output
resolution** (`kb-flux2.md` "denoising stall analysis": 512² ≈ 1,024 image tokens vs ~4,080 at
1360×768 — the attention pair count is ~9× larger, and a 1360×768 dev run measured **3,166 s**). So the
sane dev workflow is **author small, then upscale**: generate at 512² (fast, JSON prompt honoured),
then run a creative upscale pass. M0e wires exactly that loop end-to-end.

**Part A — `flux.2-dev` defaults to 512².** The flux2 catalog size default is 1360×768 (`model_catalog`
`CATALOG["flux2"]["params"]`), pipeline-wide. Add **per-variant size defaults** — `flux.2-dev` →
`{width: 512, height: 512}` in its `defaults` block — and a `model_size_default(pipeline, model_name)`
helper that returns the variant override when present, else the pipeline param default. The `/generate`
single-pipeline unset-size resolution (the M6-review "display==reality" block) consults the **effective
model** (params-channel override > top-level > catalog default — the same precedence the per-model
weight pre-flight already uses) before falling back to the pipeline default, so an unset `flux.2-dev`
cast actually **gets** 512². The frontend `ParamControls` width/height **placeholder** becomes
model-aware (a `sizeDefaults` prop) so the drawer advertises 512² the moment dev is selected — never a
1360 placeholder that lies about what will render. Klein/base/sd35/zimage are unchanged. Explicit dims
(top-level or params channel) always win; this only moves the **unset** default.

**Part B — output size on the M0c i2i postprocess steps (the i2i upscale).** Today a `Clean`/`Refine`
step preserves the source's dims (`_image_dims(src_abs)` in the queue endpoint — the right default).
Add an **optional output size** so the same step can re-diffuse **larger** = a quick i2i creative
upscale: a **scale factor** quick pick (×1.5/×2/×4) **and** an explicit **W×H override** (author choice
2026-06-21). Resolution rule: explicit W×H wins; else `round16(source × factor)`; else (blank/×1) the
source dims (today's behaviour). The size rides the step `params` (`scale`/`width`/`height`, validated
divisible-by-16 + catalog min/max). **zimage/sd35 only** — `flux2` i2i (the M0d dev-JSON re-pose) keeps
source dims, since its purpose is edit-in-place, not enlarge. diffusers resizes the init image to the
requested H×W, so init=source + a larger target *is* the upscale; no worker change.

**Part C — dedicated `Upscale ✨` preset (SD3.5 Tile ControlNet).** The structure-preserving,
high-ratio path the plain i2i resize can't match. The sd35 worker **already registers** the InstantX
SD3 Tile ControlNet (`stage1_load_pipeline._CN_REPOS["tile"] = "InstantX/SD3-Controlnet-Tile"`) and
supports `cn-inpaint` (= `StableDiffusion3ControlNetPipeline`, t2i + CN); the spec parked it as
"postproc/M6+ … the polished creative-upscale orchestration remains planned" (§8, `sd35.py` header).
M0e pulls it in early as a **postprocess preset**, NOT on `/generate`:
- **Single-run, not batch.** The sd35 worker's batch `run_jobs` is `t2i/img2img/inpaint` only — "the
  ControlNet modes need per-item conditioning images and stay single-run" (`run_pipeline._BATCH_MODES`).
  So `Upscale` fires a **single-run** `sd35` job (`mode=cn-inpaint`, no `batch_items`) — exactly the
  shape M0c's flux2-dev i2i step already uses. The orchestrator builds
  `{prompt, mode:"cn-inpaint", controlnet:"tile", control_image:<source>, cn_scale, width, height,
  model_name}`; `emit_argv` already gates `controlnet`/`control_image`/`cn_scale` to `modes:["cn-inpaint"]`.
  The tile CN is the **conditioner** (no `init_image`); diffusers resizes the control image to the
  target H×W, so source→control + a larger target is the tile upscale. Prompt defaults to the source's
  own (the "upscale THIS image" behaviour); the dev-JSON tree is **not** offered here (tile CN is sd35,
  Qwen/T5 text — JSON-as-text only on dev).
- **Wiring.** `sd35` adapter: advertise `cn-inpaint` in capabilities + `WIRED_PARAMS`
  (`controlnet`/`control_image`/`cn_scale`) so single-run `build_argv` emits them; **not** added to
  `WIRED_MODES` (that gates `/generate`, and `Upscale` is postproc-only — it submits straight through
  the postproc queue endpoint, which never consults `WIRED_MODES`). New `_PP_PRESETS["upscale"]`
  (`backend:"sd35"`, `mode:"cn-inpaint"`, `params:{controlnet:"tile", cn_scale, scale:2}`), sd35-fixed.
  Output size = Part B's resolver (default ×2). Size control is the same factor+explicit pair.
- **Weights.** `InstantX/SD3-Controlnet-Tile` is a single `SD3ControlNetModel` (a `config.json`, **not**
  a pipeline `model_index.json`), so `image_model_present` (which probes `model_index.json`) won't see
  it — add it to `models.json` (a postproc/controlnet weight) + a `controlnet_present()` probe
  (`config.json`) + a **412 pre-flight** in the queue endpoint (offer the fetch), alongside the existing
  sd3.5-medium base check. The CN is SD3-medium-family (hidden-dim match — the worker asserts this), so
  `Upscale` defaults to / requires the **`sd3.5-medium`** base.

**Constraints / risks.** (1) On 16 GB ROCm `flux.2-dev` may still be slow even at 512² (paging) — Part A
just removes the *resolution* penalty; if dev is impractical the klein/base presets + the sd35/zimage
upscale paths stand alone (Part A is dev-only, no regression elsewhere). (2) The InstantX SD3 tile CN is
**SD3-medium**; on sd3.5-large the worker already errors on the hidden-dim mismatch — so `Upscale` is
medium-only (documented + preset-fixed). (3) Tile CN is a *creative* upscale (it re-renders detail), not
a faithful super-resolution — expect added/altered texture; that is the intended "polish" behaviour.
(4) Single-run `cn-inpaint` is heavier than a batch item but it is **one** image (a deliberate upscale of
a chosen base), so no streaming-tiles concern. **Out of scope (unchanged):** depth/pose/canny CN
conditioning and multi-CN inpaint stay `advanced`/unwired (P3 keyframes R128 / P6); a tiled-diffusion
**megapixel** upscaler (latent tiling for >2k) → later; SD3.5-large tile CN → if/when a matching CN ships.

**Sources (carried + 2026-06-21):** `kb-flux2.md` "FLUX.2-dev … denoising stall analysis" (token²/
resolution cost, 512² recommendation) · [InstantX SD3 ControlNet Tile](https://huggingface.co/InstantX/SD3-Controlnet-Tile)
· [diffusers SD3 ControlNet pipeline](https://huggingface.co/docs/diffusers/en/api/pipelines/controlnet_sd3).

#### M2.5 solution design — quantized `flux.2-dev` replacement

*Status: **planned 2026-06-26** — informed by the old-project spike in `src/pipeline/flux2_q8`.
The spike proved Comfy-Org's quantized Flux2-dev split files can run on the RX 9070 XT / ROCm rig,
including advanced JSON prompts, and that native scaled-FP8 matmul roughly halves the denoise time
versus the compatibility dequant path. This is a **dev-only migration**. Klein models remain as-is.*

**Goal.** Loom should stop trying to run the full BFL `flux.2-dev` stack and instead route the
**single-run** dev-tier operations through the quantized Comfy-Org Flux2-dev implementation:

- transformer: `Comfy-Org/flux2-dev` `split_files/diffusion_models/flux2_dev_fp8mixed.safetensors`
- text encoder: selectable Mistral `fp8` / `bf16` split file for quality comparison; `fp8` default
- VAE: `split_files/vae/flux2-vae.safetensors`
- execution: scaled-FP8 Linear wrappers with `fp8_matmul=auto` defaulting to native `_scaled_mm` on
  this ROCm build; `dequant` remains a diagnostic fallback

**Dependency elimination (the point of the swap, decided 2026-06-26).** A cache audit (`F:\HF_HOME`)
showed the gated heavyweight repos are almost entirely dead weight for the quantized path: the dev
path reads **~17 MB** from the 166 GB `black-forest-labs/FLUX.2-dev` repo (`text_encoder/config.json`
+ `tokenizer/`) and **nothing** from the 90 GB `mistralai/Mistral-Small-3.2-24B` repo. M2.5 therefore
**deletes both gated repos from every runtime path**: (1) **vendor** the ~17 MB Mistral config +
tokenizer into the pipeline tree and load via a local path; (2) **re-point Klein's VAE** (its one
remaining BFL file, `ae.safetensors` 321 MB) to Comfy's identical `flux2-vae.safetensors`. The result:
the only large dev download becomes the **~51 GB** of public Comfy split files (fp8 transformer 34 GB +
fp8 Mistral TE 17 GB + VAE 321 MB), replacing ~150 GB of gated dev weights + 90 GB Mistral. The Klein
re-point is a **weight-source change only** — it does **not** alter Klein's runtime path or its
Stage-B `ref` behavior (§11/R147).

**Scope (decided 2026-06-26; batch added 2026-06-26).** M2.5 first shipped quantized dev for the
**single-run** surfaces — **t2i** (JSON authoring / casting) and **i2i** (the M0c/M0d postprocess +
M0e upscale steps). **⭐ Amended same day:** the author's real driver for dev is its advanced
(structured-JSON) prompting **in the expansion/curation screen**, so the **batch `ref` Stage-B
coverage sweep is now ALSO routed to quantized dev** (the original "Klein-only batch" call is
reversed). `run_pipeline.run_jobs` branches its loaders to the Comfy split files for `flux.2-dev`
(TE encode-all → free → fp8 transformer + Comfy VAE — the existing batch memory structure already
keeps the 17 GB TE and 34 GB transformer from co-residing on 16 GB). The Stage-B endpoint was already
dev-aware (M0d JSON prompts per cell, M0e 512², the `variant_weights_present` gate). Klein remains a
fully-valid Stage-B workhorse; dev is now an *option* for sweeps (slower without a step LoRA — see
Risks). **Owed:** the Comfy **Flux2-Turbo LoRA** to make low-step (≈8) dev sweeps fast — a separate
pass (LoRA-on-scaled-FP8 is R&D); until then dev sweeps want ~50 steps.

**Naming stance.** Keep `flux.2-dev` as the **logical Loom model id** for now so existing projects,
postprocess steps, JSON-tree gating, and tests do not need a user-facing migration. Internally it
resolves to the quantized Comfy implementation. If we later expose both, add an explicit
`flux.2-dev-q8` variant, but P2/M2.5's intent is replacement, not a parallel model zoo.

**Surfaces to replace.** Search/replace alone is not enough; the migration must update the runtime
contract behind each existing dev touchpoint:

- **Vendored pipeline (decided: fold, not port).** Fold the quantized dev branch into the existing
  `pipeline.flux2.run_pipeline` behind `model_name == "flux.2-dev"`, reusing the spike's scaled-FP8
  loaders (`scaled_fp8` + the `stage1` Comfy loader branch) and the **existing** `stage2/3/4`. Only
  the **single-run `run()`** path (and the `stage1_load_models` dev branch) is touched — **`run_jobs`
  is left as-is** (batch ref stays Klein-only, per Scope). Folding (vs porting `flux2_q8` as a
  separate module) keeps **one** CLI/adapter contract — notably loom's `--cpu-offload` is opt-in
  (default off), whereas the spike runner defaults offload **on** via `--no-cpu-offload`; the fold
  keeps loom's flag so the adapter's appended `--cpu-offload` ([adapters/flux2.py]) still applies.
  Dev-only knobs (`--text-encoder`, `--fp8-matmul`) are added and **gated to dev** (hidden from Klein).
  The folded loader points `AutoConfig`/`AutoProcessor` at the **vendored** Mistral config+tokenizer
  directory (local path), not the gated `black-forest-labs/FLUX.2-dev` repo id — so no dev run needs
  that repo. Suggested home: `pipelines/multistack/flux2/assets/mistral_te/` (config.json + the
  tokenizer files), tiny enough to live beside the vendored `flux2` lib.
- **Adapter/orchestrator:** keep the `flux2` adapter public contract stable, but route `flux.2-dev`
  jobs to the quantized runner/module. Preserve dev max-ref caps and JSON prompt passthrough.
  Klein variants continue to invoke the existing Klein runner.
- **Model catalog:** update the `flux.2-dev` variant metadata so defaults, size override, guidance
  editability, JSON-tree gating, and sampling presets point to the quantized backend. Klein entries
  and Klein sampling presets remain unchanged. ⭐ **The catalog stays the size source of truth:** the
  M0e per-variant **512²** default (`CATALOG["flux2"].variants[flux.2-dev].defaults`) must be
  preserved — `emit_argv` keeps emitting it, overriding the spike runner's internal 1024² default, so
  the M0e "display==reality" discipline doesn't regress.
- **Weight gates (`models.json` + probes) — drop both gated heavyweight repos.** (There is no
  standalone heavyweight dev gate today to "replace" — dev was only ever gated via the `multi`
  presets.) Per the dependency-elimination decision above:
  - **Dev** gates only the **3 Comfy split files** (`flux2_dev_fp8mixed` 34 GB + `mistral_…fp8`
    17 GB + `flux2-vae` 321 MB ≈ 51 GB, `Comfy-Org/flux2-dev`, public/ungated). The Mistral
    config+tokenizer are **vendored** (not gated, no probe). Skip the fp4 TE, the bf16 TE (unless
    a quality-compare fetch is wanted), and the Turbo LoRAs.
  - **Klein** gates Comfy's `flux2-vae.safetensors` instead of `black-forest-labs/FLUX.2-dev`
    `ae.safetensors`; its flow/TE (Qwen3) gates are unchanged. A one-time **VAE value spot-check**
    (load both, compare a remapped tensor) confirms equivalence before flipping `ae_repo_id`.
  - Net invariant: after M2.5 **neither `black-forest-labs/FLUX.2-dev` nor `mistralai/…` is
    referenced by any runtime path or `models.json` entry** — both are removed (incl. the
    `flux2-dev-ae` `multi_presets` rows, re-pointed to the Comfy VAE).
- **Prompting UI:** the structured JSON tree remains dev-gated, but the gate means "logical
  `flux.2-dev` backed by quantized Comfy files." Add optional advanced controls only where useful:
  text encoder (`fp8`/`bf16`) and FP8 matmul (`auto`/`native`/`dequant`) should live in an advanced
  params foldout, not in the main creative path.
- **Manifests + lineage:** record `backend_variant:"comfy-q8"`, Comfy repo snapshot/path, transformer
  file, text-encoder variant/file, VAE file, `fp8_matmul`, and quantized loader stats. This is
  important because a future image comparison must distinguish "full dev" from "Comfy quantized
  dev" even if the UI id stayed `flux.2-dev`.
- **Tests:** update no-GPU tests around catalog variants, weight gates, adapter argv, postprocess
  flux2 i2i, and model-size defaults. Add explicit invariants that `flux.2-klein-*` paths do not
  reference the quantized dev runner and that `flux.2-dev` does.

**Acceptance for M2.5.** *(Bar decided 2026-06-26: the milestone CLOSES on wiring + dry-run +
no-GPU tests green; the real on-rig dev generation is **owed** as a separate visual sign-off — the
M0e pattern — because full dev never actually ran on the rig, so there is no working baseline to
regress against.)*

**Closes the milestone (no-GPU):**

- A dry-run `/generate` for `pipeline:"flux2", model_name:"flux.2-dev"` emits the quantized runner
  command (single-run path) and records quantized dev metadata.
- A postprocess `flux2` img2img/refine step with `model_name:"flux.2-dev"` still accepts the JSON
  prompt body and routes (dry-run) to the quantized runner.
- No-GPU tests: catalog variant metadata, the dev Comfy split-file gates + the Klein Comfy-VAE gate,
  the vendored config+tokenizer load (local path resolves), adapter argv, the dev-only
  `--text-encoder`/`--fp8-matmul` knobs, postprocess flux2 i2i, and model-size defaults (dev 512²).
- Existing Klein smoke/dry-run tests pass unchanged; no Klein catalog entry, weight gate, or adapter
  path depends on the Comfy Mistral/transformer split files, **and the batch `run_jobs` ref path is
  unchanged** (still Klein-only).
- Invariant: dev cannot be silently submitted as the model for a batch coverage sweep (guarded or
  documented), so the unported `run_jobs` full-weight path is never reached for dev.
- **Elimination invariant (grep/test):** no runtime path or `models.json` entry references
  `black-forest-labs/FLUX.2-dev` or `mistralai/Mistral-Small-3.2-24B` — the dev config/tokenizer load
  from the vendored local dir and Klein's VAE from `Comfy-Org/flux2-dev`.

**Owed (on-rig visual sign-off, does NOT gate close):**

- A real local smoke: quantized dev **t2i JSON** (and a dev **i2i** postproc step) generates on the
  RX 9070 XT using `--fp8-matmul auto` and produces a manifest with `backend_variant:"comfy-q8"`,
  `text_encoder_variant`, and the Comfy split-file paths.
- **Klein VAE value spot-check:** load `ae.safetensors` (BFL) and `flux2-vae.safetensors` (Comfy,
  remapped), confirm tensors match within tolerance, and a Klein `ref` smoke still decodes correctly
  — then the BFL `ae.safetensors` reference is removed for good.

**Risks / open edges.**

- **FP4 text encoder remains research.** Comfy also ships an fp4-mixed Mistral file, but it uses
  packed NVFP4 (`comfy_quant`/`weight_scale_2`), not the scaled-FP8 wrapper. Do not make fp4 a Loom
  default until an `NVFP4Linear` path exists and image quality is checked.
- **Native FP8 matmul is platform-sensitive.** `auto` should prefer `_scaled_mm` on the RX 9070 XT
  ROCm stack and fall back to dequant elsewhere; `native` should be a diagnostic force-fail mode.
- **Quality validation is empirical.** The quantized dev path is expected to preserve JSON adherence
  better than Klein while fitting the rig, but fp8 vs bf16 text encoder quality should be tested with
  same-prompt/same-seed grids before freezing defaults beyond "fp8 for speed/fit".
- **Name compatibility can hide implementation drift.** Because `flux.2-dev` remains the user-facing
  id, manifests must be explicit enough that old full-dev outputs and new quantized-dev outputs are
  never confused in lineage.
- **Klein VAE equivalence is assumed until the value spot-check.** The header audit (same 251
  tensors/dtypes; naming + 2D→4D diffs the existing remap handles; spike loads `strict=True` and
  decodes) strongly implies identical weights, but the flip of Klein's `ae_repo_id` is gated on the
  one-time value compare. If they ever diverged, Klein output would shift subtly — so don't remove
  the BFL `ae.safetensors` reference until that check passes.
- **Vendoring gated config/tokenizer.** The ~17 MB Mistral config+tokenizer come from the gated BFL
  repo. Committing them redistributes gated-repo files — acceptable for this private project
  ([[project-posture]]: licensing deferred), but if the repo ever goes public, move them to the
  companion weights repo (fetch as small `file` entries) rather than ship them in git.

#### M2.6 solution design — Turbo LoRA (low-step `flux.2-dev` for viable sweeps)

*Status: **planned 2026-06-27** — make `flux.2-dev` sweeps practical. Per the timing investigation
(kb-loom-p2-imp.md), a single dev 512²/8-step image denoises in ~87 s (the 34 GB fp8 transformer pages
over PCIe on 16 GB), so a 31-cell expansion sweep ≈ 45 min. The Comfy **Flux2-Turbo LoRA** is a
step-distillation adapter that should give good quality at **~4–6 steps**, roughly halving sweep time.
An **optional, dev-gated `turbo` toggle** on the params panel arms it. On-rig quality/speed validation
is OWED (can't run the 34 GB+2.6 GB on 16 GB from CI).*

**Does JSON prompting survive?** Yes — the Turbo LoRA adapts only the **transformer** (denoise
convergence); the Mistral TE that parses the structured JSON is untouched. Expect the usual
step-distillation softening of *guidance adherence*, but the structured-prompt content persists.

**LoRA structure (inspected `Flux2TurboComfyv2.safetensors`).** 170 rank-256 BF16 LoRA pairs
(`<m>.lora_A` [r,in], `<m>.lora_B` [out,r]); delta = `strength · B(A·x)` (no alpha tensor ⇒ Comfy
α=rank ⇒ scale = `strength`, default 1.0). It targets attention + `single_blocks` + embeddings +
modulation; the double-block MLPs are NOT adapted.

**Key map → BFL `flux2.model.Flux2` (the crux).** Two namespaces in the file:
- **`diffusion_model.*` (103 mods) map by name 1:1** — `double_stream_modulation_{img,txt}.lin`,
  `single_blocks.N.{linear1,linear2}`, `single_stream_modulation.lin`, `guidance_in.{in,out}_layer`,
  `time_in.{in,out}_layer`.
- **`transformer.*` (67 mods, Diffusers naming) remap** — embeddings: `context_embedder`→`txt_in`,
  `x_embedder`→`img_in`, `proj_out`→`final_layer.linear`; double-block attention (8 blocks):
  `to_out.0`→`img_attn.proj`, `to_add_out`→`txt_attn.proj`; and the **qkv fusion** — BFL fuses q,k,v
  into one `*_attn.qkv` Linear (6144→18432), so the LoRA's separate projections apply to **output-row
  slices**: `to_q/to_k/to_v` → `img_attn.qkv` rows `[0:6144]/[6144:12288]/[12288:18432]`;
  `add_q/add_k/add_v` → `txt_attn.qkv` slices. (Verify the BFL qkv order is q,k,v — standard, but
  confirm on the spike.)

**Application — forward hooks (works on fp8 AND bf16 Linears).** Rather than special-casing
`ScaledFP8Linear` vs `nn.Linear`, register a **forward hook** on each target module that adds the LoRA
delta to its output: build a `zeros_like(output)` delta, fill each `[start:start+slice]` with
`scale·F.linear(F.linear(x, A), B)`, return `output + delta`. Non-fused mods use one entry at
`start=0` (full width); fused qkv attaches three entries at the three slices. A/B stay resident bf16
(activations are bf16 ⇒ no dtype juggling). The base fp8 matmul is unchanged.

**Toggle + schedule.** A dev-gated catalog `turbo` flag (same channel as `text_encoder`/`fp8_matmul`:
single-run `--turbo` + the batch `_SHARED_KEYS` for sweeps). When on: load the Turbo LoRA after the
transformer, attach the hooks, and default `num_steps` to the Turbo count (**6**, overridable). The UI
shows it inline for `flux.2-dev` only.

**Risks / owed.** (1) **+2.6 GB** bf16 LoRA on the already-overflowing 16 GB adds paging — the 8→4/6
step cut should still net a speedup, but that's the empirical on-rig question. (2) qkv-order + exact
slice correctness — validate on the spike (one image, eyeball vs no-turbo). (3) optimal step count +
whether Turbo wants a fixed/low guidance — on-rig tuning. (4) the LoRA file weight-gate (Comfy
`split_files/loras/Flux2TurboComfyv2.safetensors`) — worker resolves local; a proper 412 fetch gate is
owed. (5) the LoRA hook compute (rank-256 over ~170 mods) is ~modest vs the base, but measure.

**Acceptance (no-GPU close; on-rig owed).** Closes on: the key-map covers every LoRA module onto a
real BFL Linear (171-module audit + qkv slices), the forward-hook delta is correct on a tiny synthetic
Linear, dry-run argv carries `--turbo`, and the `turbo` toggle is dev-gated end-to-end. **Owed
(on-rig):** a dev image at 4–6 steps with Turbo looks good vs the under-denoised no-Turbo 8-step, and a
sweep is meaningfully faster.

#### M2.7 solution design — warm-worker batch queue (individual cell-jobs + a persistent worker)

*Status: **planned 2026-06-27** (author-approved). A core-queue change, built in phases (each
journalled + pushed).* **Problem.** Today a batch (multi **Cast** + Stage-B **Expansion**) is ONE
queue job whose worker loops the cells internally. On **pause** the runner discards the partial
(`runner._discard_partial` for non-resumable jobs) and re-queues from scratch — every finished image
disappears (user-reported). And the batch is a single opaque queue entry, so individual images can't be
seen/managed. **Goal:** each image is an **individual queue entry** that persists as a tile the moment
it finishes (pause keeps it; resume continues with the rest), while a **persistent "warm" worker**
keeps the model resident across the group so we don't pay the per-image model reload. *(Chosen over the
simpler "resumable batch" — which kills the pause pain at zero cost but keeps one opaque queue entry —
because the author wants per-image queue entries AND amortized load.)*

**Architecture.**
- **Submission → N cell-jobs.** The `/assets/{id}/stage-b` + `/generate?pipeline=multi` endpoints stop
  emitting one `batch_items` job and instead `submit()` **one job per cell/candidate** — each a
  single-image spec (its prompt/seed/`coverage_cell`) tagged with the existing `batch_id` + a new
  **`warm_group`** key = hash(pipeline, model_name, mode, ref-set, size, sampling). The load-bound
  shared params (model, `ref_images`, size, `turbo`/TE/matmul) ride each cell-job (or a per-group
  record the worker reads once).
- **Persistent worker (`--serve`).** A new worker mode: load the model from the FIRST job, then read
  job specs as JSON lines on **stdin**, generate one image each, emit a `{result}` line on **stdout**,
  loop until EOF/idle/stop. The existing `run_jobs` (load-once-then-loop) is the template — just
  stdin-driven instead of a fixed `--jobs-file`. Flux2 `ref` keeps the hero encoded once for the group.
- **Runner warm dispatch.** `_execute` gains a warm path: if a pending job's `warm_group` matches the
  **resident** warm worker, **feed it** (write the spec to its stdin, read the result) instead of
  `Popen`-ing a fresh process; else spawn a new warm worker (after killing any other — still **one
  model at a time** on 16 GB, R-§7). Track `{worker, group, idle_timer}`; **evict** (close stdin →
  worker frees VRAM + exits) on group change, idle timeout, pause, or cancel-of-last. Per-cell result
  → that cell-job goes `done` with its image (a durable tile + a lineage edge) immediately.
- **FE — mostly free.** The grid/queue already key on jobs, so N one-image jobs render + persist
  individually; `batch_id` groups them visually (a "sweep" header + per-cell tiles). The inspector's
  per-image time (just shipped) is now the job's own `duration_s`.

**⚠ The dev-TE wrinkle.** The current dev *batch* worker encodes ALL prompts up front, frees the 17 GB
Mistral TE, then loads the 34 GB transformer and loops denoise — amortizing the TE load. Individual
jobs arrive one at a time, so the warm worker can't pre-encode; with the transformer resident it must
load/free the TE per image (17 + 34 GB won't co-reside on 16 GB). So **dev** individual-jobs pay a
per-image TE cost the batch avoided (still far better than cold per-image jobs; Klein's smaller TE is
fine). The queue-UX win (persist-on-pause) is identical. *(A later optimization: a small "encode-ahead"
buffer in the warm worker that pre-encodes the next K queued prompts while denoising — recovers most of
the amortization. Out of the initial phases.)*

**Phasing (each = its own journal entry + push).**
- **Phase 1 — flux2 Expansion** (the actual pain): `--serve` mode on the flux2 worker; the runner
  warm-worker dispatch + lifecycle (one resident worker, group match, evict); the Stage-B endpoint
  emits N cell-jobs. Prove a klein/dev sweep streams individual persistent tiles + survives pause.
- **Phase 2 — the rest of batch generation.** Split (the original "multi serve mode" didn't survive
  contact — a Cast spans 3 VRAM-isolated pipelines, so a single resident worker can't map to it):
  - **2a — sd35/zimage Expansion warm cells** (DONE): `--serve` on both workers (a shared
    `_generate_item` so the batch + serve paths can't drift) + adapter `serve_argv`; the Stage-B
    else-branch emits N img2img cell-jobs per realization group through the **same** runner warm
    dispatch as flux2. Gated to **no-post-pass, non-`mixed`** sweeps — those still ride the cold
    `--jobs-file` batch job until 2b (no silent loss of identity/clean/polish).
  - **2b — post-passes on warm cells** (DONE): each warm cell-job carries the sweep's `post_passes`
    and chains them over its OWN output on completion (`_record_warm` → the same `_submit_chained` the
    cold path uses); the no-post-pass gate is gone, so identity/clean/polish now ride warm sweeps (one
    pass tile per cell, each its own pause-safe job). Because the pass jobs are created as cells finish
    (later `created_at`), FIFO runs all warm cells first (worker resident, no thrash), passes after.
    `realize="mixed"` is the only remaining cold-batch Expansion case (its two-group bg-mask path).
  - **2c — multi (Cast) individual queue jobs**: orchestrator-driven candidate fan-out so each Cast
    candidate is its own pause-surviving queue entry. NOT a warm worker (Cast spans 3 models); the win
    is pause-persistence, not warmth.
- **Phase 3 — cross-batch warmth + idle-eviction tuning** (the worker survives between unrelated
  same-group jobs) + the dev encode-ahead buffer if wanted.

**Risks / guardrails.** (1) **VRAM** — the warm worker holds the GPU between jobs; idle-evict + the
one-model rule must free it for other pipelines (the existing VRAM-admission still gates). (2) **Cancel
semantics** — cancel ONE cell-job (skip/kill current item, worker lives) vs cancel the sweep (evict the
worker); the per-job kill-Job-Object is per `Popen`, so the warm path needs a soft "abandon current
item" signal. (3) **Crash recovery** — a warm-worker crash fails the in-flight cell-job; the rest stay
`queued` and the runner respawns (no `_discard_partial` of the done tiles). (4) **Protocol robustness**
— stdin/stdout JSON-line framing must survive a worker print mid-stream (reuse the utf-8/replace pipe
discipline). (5) **Determinism** — per-cell seeds already ride each item, unchanged.

**Acceptance (per phase; on-rig owed).** Phase 1 no-GPU close: the Stage-B endpoint emits N cell-jobs
with the right `warm_group`/`batch_id`/`coverage_cell`; the flux2 `--serve` argv + stdin protocol parse
+ round-trip a job→result on a stub; the runner feeds same-group jobs to one worker and evicts on group
change (unit-tested with a fake worker); pause leaves done cell-jobs `done` and the rest `queued`.
**Owed (on-rig):** a real klein sweep streams individual tiles that persist across a pause, with the
model loaded once.

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
3. **M2.5 — quantized `flux.2-dev` replacement + gated-repo elimination.** Fold the Comfy quantized
   Flux2-dev path into the existing flux2 runner behind the `flux.2-dev` logical id; preserve the
   **single-run** dev JSON/t2i/i2i surfaces (batch `ref` Stage-B sweep stays Klein-only); add precise
   Comfy split-file gates + manifest fields. **Delete both gated heavyweight repos** (BFL dev 166 GB
   + Mistral 90 GB): vendor the ~17 MB dev config+tokenizer; re-point Klein's VAE to the identical
   Comfy `flux2-vae.safetensors` (Klein runtime/Stage-B otherwise unchanged). Closes on wiring +
   dry-run + no-GPU tests; on-rig dev smoke + Klein-VAE value spot-check owed. *(Runtime/model-fit
   migration, not trainer work.)*
3b. **M2.6 — Turbo LoRA (low-step dev).** Make dev coverage sweeps viable: attach the Comfy
   Flux2-Turbo LoRA (Diffusers→BFL key map + qkv-fusion onto the fused `*_attn.qkv`, applied via
   forward hooks on the quantized transformer) behind an optional dev-gated **`turbo`** param. JSON
   prompting unaffected (TE untouched). Backend + no-GPU tests done; on-rig quality/speed sign-off
   owed (+2.6 GB LoRA paging vs the 8→4 step cut). Design: §12 "M2.6 solution design".
3c. **M2.7 — warm-worker batch queue.** Make batch (Cast + Expansion) generation submit **N
   individual cell-jobs** (each a persistent tile that survives pause/resume) serviced by a
   **persistent warm worker** that keeps the model resident across the group (no per-image reload).
   Core-queue change, built in phases (Phase 1 = flux2 Expansion), each journalled + pushed. Design:
   §12 "M2.7 solution design".

### Phase B — Thicken (all VLM-free)

4. **M3 — template captioning.** Generate `captions.jsonl` deterministically from coverage-cell
   metadata + trigger token; write `caption_policy.json`; review/edit UI. **(No VLM.)**
5. **M4 — proxy readiness.** Coverage (from metadata) + perceptual-hash dupes + face-embedding
   on-model → `readiness.json` → advisory readiness meter. **(No VLM.)** ***P2 done-line now
   reachable*** (M2 trainer + M3 captions + M4 readiness, R169).
6. **M5 — train options + sd35 + PEFT backend.** Expose train-from-base / seed-from-parent (R68) and
   the per-model preset + advanced knobs; add the **diffusers-PEFT** advanced backend; onboard the
   **sd35** trainer; record full training manifests.
7. **M6 — promote + manual cleanup + LoRA management.** Promote into the version; one-click temp
   cleanup; version selector shows LoRA presence. **(No style-LoRA path — declared only, 0 effort in
   P2 per R122; built in P5 with multi-LoRA stacking, R147.)**

### Done-line

8. **M7 — acceptance.** A P1 character → template-captioned → proxy-readiness ✓ → **staged → added to
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
| P2-M0d | **flux.2 advanced prompting + sampling presets + dev JSON tree** — (A) structured/labeled (opt-JSON) flux2 prompts with explicit angle→camera/pose directives (the pose-adherence fix); (B) individually-editable guidance/steps fronted by a ≥3-preset Sampling pull-down (Fast/Balanced/Quality, model↔guidance pairing guard); (C) a `flux.2-dev`-gated **structured-JSON prompt tree** in params for t2i/i2i authoring from the schema. Additive (no coverage-contract change). Design: §12 "M0d solution design" | M0 | M–L (FE-heavy) | 🟡 |
| P2-M0e | **flux.2 low-res-first + creative upscale** — (a) `flux.2-dev` size defaults to 512² (per-variant catalog default + model-aware generate resolution + drawer placeholder); (b) output size (scale-factor + explicit W×H) on the M0c i2i postproc steps so a zimage/sd35 Clean/Refine re-diffuses larger = i2i upscale; (c) dedicated **`Upscale ✨`** preset = single-run `sd35` cn-inpaint + **SD3.5 Tile ControlNet** (already registered) at target size + tile-CN weight gate/fetch. Additive, postproc-only (no `/generate` mode change). Design: §12 "M0e solution design" | M0 | M (FE+orch) | 🟡 |
| P2-0 | **ai-toolkit ROCm *can-it-run-at-all* gate** — prove ai-toolkit trains on **RX 9070 XT / ROCm** before the rest of P2 is built; **hard go/no-go** (if no-go, the whole training approach changes) | M1 | M | 🔴 **make-or-break front-gate** |
| P2-1 | **Training spike (no UI)** — vendor **ai-toolkit**, train one `zimage` LoRA from a fixed dataset, load-test it | M1 | M | 🔴 **no trainer exists** |
| P2-2 | Trainer as a **staged queued job** (wrap in P0 queue + manifest; `jobs/staged.json`; auto-generate, don't auto-start) | M2 | M | 🟡 |
| P2-2.5 | **Quantized `flux.2-dev` replacement + gated-repo elimination** — fold the quantized Comfy Flux2-dev split-file branch into the existing flux2 runner behind the logical dev id (single-run t2i/i2i; batch `ref` stays Klein); vendor the ~17 MB dev config+tokenizer + re-point Klein's VAE to the Comfy `flux2-vae` so **both gated repos (BFL dev 166 GB + Mistral 90 GB) are deleted**; add precise split-file weight gates, manifest fields, advanced `text_encoder`/`fp8_matmul` params, and no-GPU adapter/catalog tests; **Klein runtime unchanged (VAE source only)** | M2.5 | M | 🟡 runtime migration |
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

**Rollup:** ~19 WP including the M0 UI reset (now M0a–M0e) and the M2.5 dev-runtime replacement;
**P2-0/P2-1 remain the phase's make-or-break training
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
- Quantized dev replacement: old-project spike `src/pipeline/flux2_q8` (2026-06-26) proving
  Comfy-Org `flux2_dev_fp8mixed` + Mistral fp8/bf16 text encoders + Flux2 VAE with native scaled-FP8
  matmul on RX 9070 XT / ROCm; Loom M2.5 keeps Klein's runtime/Stage-B path unchanged (only its VAE
  weight source moves to Comfy).
- Gated-repo elimination: `F:\HF_HOME` cache audit (2026-06-26) — quantized dev reads ~17 MB
  (`text_encoder/config.json` + `tokenizer/`) from the 166 GB `black-forest-labs/FLUX.2-dev` repo and
  nothing from the 90 GB `mistralai/Mistral-Small-3.2-24B`; Comfy VAE vs BFL `ae.safetensors`
  safetensors-header compare (251 tensors, matching dtypes; naming + 2D→4D diffs handled by
  `map_comfy_vae_key`) → vendor the dev config+tokenizer + re-point Klein's VAE to Comfy, deleting
  both gated repos. Needed Comfy footprint ≈ 51 GB (fp8 transformer + fp8 TE + VAE), public/ungated.
