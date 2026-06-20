# Loreweave Studio — P1 build spec (Bible + Assets + Casting)

Created: 2026-06-01
Status: in implementation — **M1–M3 ✅ (M3 ACCEPTED 2026-06-11, user GPU sign-off) · M3.5 ✅
(BiRefNet matting + mixed realization) · M4 ✅ (identity anchor, spike-validated inswapper) ·
P1-12 ✅ (curation throughput) · M5 ✅ (versioning: copy-on-create + finalize lock) · M6/M6.1 ✅
(face-restore pass via GFPGAN onnx + face-portrait anchor derivation; masking/upscale deferred) ·
M7 ✅ (video-sketch harvest: cell-targeted `ltxv` i2v + chained frame extraction) · M8 ✅ (full
L1 World: world prose + style global-negative + story spine → stub profiles) · M9 ✅
(profile export/import — portable bundle, new-profile-on-import, rename on collision) · M10 🟡
(MVP acceptance: done-line locked as an executable no-GPU test + adapter contract-gaps recorded
— **awaiting the user's RX 9070 XT rig sign-off** to declare P1 ACCEPTED)**. Journal
`kb-loom-p1-imp.md`. *(Amended 2026-06-11/12: spec synced to
shipped reality — see the inline "Amended" notes; the drift list is the journal's POST-MVP
TRACKER PM-1…PM-10.)*
Parent: [`kb-storyboard01.md`](kb-storyboard01.md) (overview + decision record R1–R114)
Predecessor: [`kb-loom-p0.md`](kb-loom-p0.md) (the spine P1 builds on)
Engine spec: [`kb-pipelines01.md`](kb-pipelines01.md) · 3D: [`kb-trellis2.md`](kb-trellis2.md) · postproc: [`kb-postproc-img.md`](kb-postproc-img.md)

P1 is the **first real creative layer**, and together **P0 + P1 = the MVP** (R40): define a
world/style, **cast a character, curate it, and save an AssetProfile** — *no training, no shots,
no episode*. It promotes P0's batch grid (M2) into the **character-bootstrap loop Stages A–C**,
adds **L1 World** and **L2 Asset Studio with profile versioning**, the **image postproc toolkit**,
the **identity anchor**, and the **Flux2 multi-reference research spike**. Every decision traces
to a resolved item (`Rnn`) in `kb-storyboard01.md` §10.0.

---

## 1. Purpose & the P1 / MVP done-line

**Purpose:** turn the proven P0 spine into a usable asset pipeline — the author can bootstrap a
consistent character and keep it as a reusable, versioned AssetProfile.

**P1 done-line (= the MVP "this proves the product" moment, R40):**

> Define a **style** (L1 — a *minimal* style fragment, seeded at M1; full World authoring is M8) →
> **cast** a character with a multi-batch run shown in the selectable
> grid → **pick the hero ★** → **expand** that hero into varied poses/scenes (Stage B) → **curate**
> the on-model results (Stage C) → **save an AssetProfile** (name + prompt-template snippet +
> curated reference set + chosen face anchor), as **version `v1`**. **No LoRA training** (that's
> P2); the curated refs are the future training corpus.

If a fresh user can go style → cast → expand → curate → saved AssetProfile and reopen the project
to find that profile intact and versioned, P1 is done.

---

## 2. Scope: in vs. out

**In P1:**

- **L1 World** (Story Bible subset): visual **style** (prompt fragment + later style-LoRA target),
  **asset classes**, **naming**, **story spine** → **stub AssetProfiles** (prompt-template
  snippets; manual re-sync, no auto-update — R55). Project **format** already exists from P0.
- **L2 Asset Studio**: library tree, **version selector**, variant gallery, generate bar, the
  **bootstrap strip (Stages A–C)**, curation grid, **export/import** of profiles.
- **AssetProfile versioning** (§3.4): full-duplicate copy-on-create from *any* prior version →
  edit → **finalize = pure-intent lock**; many versions with grouping; new-profile for big
  changes (manual, no hints).
- **Character-bootstrap Stages A–C** (R7/R40): **A casting** via `multi` (`--num-candidates ≤ 5`,
  ≤ ~200 img/process configurable, simple scrollable selectable grid — R38/R44); **B expansion**
  via img2img/inpaint + **video-sketch harvest** (Flux2 multi-ref is the spike, §11);
  **C curation** (manual keep/cull → curated ref set). **No training** (P2).
- **Image postproc toolkit** (§8.3): matting, masking (SAM2/Grounded-SAM), **identity (PuLID)**,
  face-restore — as queueable per-image actions. *(Matting pulled forward to **M3.5** for the
  Stage-B background mask, 2026-06-11; the rest stays M6.)*
- **Identity anchor + face-anchor stage** (R82/R93/R94): generate face portraits → pick a detailed
  face; **per version**; anchor on by default, opt-out; usable at Stage-B and inference.
- **Flux2 multi-reference research spike** (§8.1 item 2) — parallel R&D, may not pan out.

**Deferred out of P1 (round-13 answer 5): all 3D.** `trellis2` / `→3D` props & scenery move to
**P6** (R128 supersedes R108 — originally P3, deferred again once P3 lost its depth-proxy driver) —
no 3D in the MVP.

**Out of P1** (later phases):

- **LoRA training** + **proxy readiness meter** → **P2**; **VLM-assisted captioning/scoring** → **P4**.
- Shots/continuity/audio/voice/lip-sync (**P3**). Flow + Muse agent (**P4**). Episode/Render (**P5**).
- **Muse/SLM-authored** Stage-B prompt lists: P1's expansion prompts are **manual/templated**
  (the SLM tenant manager is stubbed in P0); SLM authoring is layered in when Muse lands (P3+).
- **VLM curation**: P1 curation is **manual** (human keep/cull); Qwen3-VL scoring is P4.

---

## 3. What P1 adds to the P0 spine

P1 reuses everything from P0 (persistent queue, adapter contract, workspace I/O, disk guard,
component manifest) and adds:

```
new ADAPTERS (onboarded via the P0 contract + 1-page check, kb-loom-p0 §15):
  multi  (Stage-A casting: flux2+sd35+zimage; already has session lineage)
    # amended 2026-06-11: clean/polish shipped as ORCHESTRATOR POST-PASSES on any run,
    # not inside multi — loom invokes `multi ideate` only (PM-4)
  flux2  ·  sd35  ·  _img2img  (Stage-B expansion: img2img / inpaint / polish)
    # amended 2026-06-11: _img2img shipped as a shared lib inside the zimage/sd35
    # workers (img2img/inpaint modes on those adapters), not a standalone adapter (PM-4)
  ltxv   (Stage-B video-sketch harvest: i2v → extract frames)
  # trellis2 / all 3D is deferred to P6 (R128, supersedes R108)
  postproc: birefnet (matting) · sam2/grounded-sam (masking) · pulid (identity) · face-restore

new RECORDS (inherit P0's IDs/schema/atomic-write rules, §3.3):
  StoryBible (story.json)          — world, style, asset classes, naming, spine
  AssetProfile (profile.json)      — identity + version list + active version
  ProfileVersion (versions/<vN>/)  — prompt snippet, refs, face anchor, (lora later), voice (later)

new WORKSPACES (UI):
  L1 World   (form + inheritance preview)
  L2 Asset Studio  (library + version selector + bootstrap strip A–C + curation grid + export/import)
```

The Asset Studio's **casting/curation grid is the same component** P0 built as the batch-grid
smoke target (kb-loom-p0 §12 M2) — P1 promotes it from "Sandbox" into L2 with star/cull, version
scoping, and "add to ref set".

---

## 4. Data model in P1 (§3.1, §3.2, §3.4)

The bundle subtrees P1 brings to life (P0 created only `project.json`, `jobs/`, `lineage/`):

```
<project>/
├── story.json                 # ← P1: StoryBible (world, style, asset classes, naming, spine)
├── bible/
│   ├── world.md  ·  style/style.json  ·  style/moodboard/*.png  ·  spine.json
├── assets/
│   ├── characters/<name>/
│   │   ├── profile.json        # id, class, version list, active version
│   │   └── versions/v1_base/
│   │       ├── version.json     # prompt snippet, status(finalized?), derived_from, anchor ref
│   │       ├── casting/*.png     # Stage-A candidates (hero starred)
│   │       ├── refs/*.png        # Stage-C curated set (future LoRA corpus)
│   │       ├── faces/*.png        # face-anchor candidates; chosen → anchor.png
│   │       └── (lora/ — P2)  (voice/ — P3)
│   ├── props/<name>/  ·  scenes/<name>/    # same shape (versions/)
│   └── _class_defs.json
└── (jobs/ · lineage/ · _temp/ · out/ from P0)
```

**ProfileVersion lifecycle (R49–R51, R58–R61, R119) — three states:**

1. **Unsaved (draft):** in-memory edits since the last Save → **lost on exit** (warning popup);
   no autosave.
2. **Saved (committed, unfinalized):** **"Save AssetProfile"** persists the version to disk —
   it **survives exit** and is **still editable**. *This is where the P1 done-line lands*: the v1
   AssetProfile is **Saved**, not Finalized. (P2 then trains on a Saved-unfinalized version.)
3. **Finalized (locked):** **Finalize** = pure-intent lock → immutable; changes need a new version
   (or a new profile for big changes — author decides, no hints).

So **Save ≠ Finalize** (R119): Save persists, Finalize locks. "Lost on exit" applies only to
**unsaved** edits (the queue persists separately, R78). create = **full deep-duplicate** of a
chosen parent (or blank for v1). Records: `id`, `derived_from`, `saved_at`, `finalized: bool`,
`prompt_template`, `anchor_ref`, `ref_set[]`, `casting[]`.

**Lineage (R98):** every generated image records `{ profile_version_id, job_id, output, manifest,
stage:A|B|C }` so you can trace which casting/expansion run produced each candidate.

---

## 5. New pipeline adapters (onboard via the P0 contract)

Each adapter follows the P0 envelope (`build_argv` / `parse_result` → normalized completion /
`capabilities`+presence / coarse progress / cancel) and gets a **1-page contract check** at
onboarding (kb-loom-p0 §15). They all share the `PipelineManifest` convention already present.

| Adapter | Used for | CLI shape (existing) | P1 note |
| --- | --- | --- | --- |
| `multi` | **Stage A casting** | `python -m src.pipeline.multi.run_pipeline …` | already has `multi/sessions.py` lineage; honor `--num-candidates ≤ 5`, total ≤ ~200 (R38) |
| `flux2` | casting member, Stage-B expansion, **multi-ref spike** | `…flux2.run_pipeline t2i\|img2img …` | multi-ref needs `--ref-image` wiring (§11) |
| `sd35` | casting member, **inpaint** expansion | `…sd35.run_pipeline inpaint\|cn-inpaint-mc …` | medium variant for public CNs |
| `_img2img` | Stage-B **img2img sweep**, polish | shared img2img backend | low/mid `strength` variants |
| `zimage` | casting member (from P0), inpaint | `…zimage.run_pipeline t2i\|img2img\|inpaint …` | already onboarded in P0 |
| `ltxv` | Stage-B **video-sketch harvest** | `…ltxv.run_pipeline i2v …` + frame extract | cheap motion → harvest frames as refs |
| postproc `*` | matting/masking/identity/restore | `src/pipeline/postproc/<tool>/run_pipeline.py` | HandRefiner exists; others per `kb-postproc-img.md` |

> **Amended 2026-06-11 (as shipped, M2/M3 — PM-4):** `_img2img` landed as a **shared library
> inside the `zimage`/`sd35` workers** (img2img/inpaint modes on those adapters), not a
> standalone adapter — R121's intent is satisfied by `multi` + `sd35` + `zimage`(+modes).
> **clean/polish** landed as **orchestrator-chained post-passes on ANY run** (zimage/sd35/multi/
> Stage-B), not inside `multi`. The R38 "~200 img/process" cap is guidance, not enforced (PM-6).

**Exact P1 done-line minimum (R121):** the MVP done-line requires **`multi` + `_img2img` + `sd35`**
(plus **`zimage`** from P0) — `multi` for Stage-A casting, `_img2img` for Stage-B img2img
expansion, `sd35` for Stage-B inpaint expansion. **`ltxv` (video-sketch), `flux2` (multi-ref
spike), and all postproc are NOT required for the done-line** — they're Phase-B thickening.
**`zimage`-only is *only* the M1 step** that scaffolds the L2 library/grid by reusing the P0
adapter; it does **not** reach the done-line. Onboarding order: M1 zimage-only scaffold → M2 add
`multi` (casting) → M3 add `_img2img` + `sd35` (expansion) ⇒ **done-line** → then `ltxv`/postproc.
(`trellis2`/3D is deferred to P6 — R128, supersedes R108.)

---

## 6. L1 World workspace (build)

Form-plus-document with a live **inheritance preview** (what every downstream generation
inherits). Writes to `story.json` / `bible/`.

- **World** — markdown editor (`world.md`); the long-form world summary.
- **Visual Style** — palette, lighting/lens, global negatives, moodboard drop → a **`style_id`**
  + a **style prompt fragment** that is **auto-applied to every image generation — APPENDED after
  the character/cell prompt** *(amended 2026-06-10/11, PM-1: was "fixed prepend"; front tokens
  dominate, so the character prompt leads — amends R104's wording)* — with a **per-generation
  override checkbox** to drop it for an intentionally off-style shot (R104).
  (Style-LoRA target is declared but trained in P2.)
- **Asset Classes** — cards (characters, props, scenes, landscape, foliage, ruins…): default
  pipeline, default TRELLIS preset, default consistency mechanism, prompt scaffolding.
- **Naming & Folder Layout** — `{class}/{name}/{version}/…` template + live path preview (§3.1).
- **Story Spine** — premise/arc/factions/NPCs/goals; NPCs **auto-create stub AssetProfiles**
  (name + prompt-template snippet, no images/LoRA). **No auto-update** — editing a spine NPC
  never rewrites the profile; a manual **"re-sync from spine"** action only (R55).
- **Project Settings** (from P0) — aspect/resolution/fps/audio master, size cap, sample text.

Prompt-snippet **injection is structured/explicit, never automatic** (R47): a picker that inserts
`asset@version`'s snippet, or an explicit `@asset@version` token.

Build note (walking-skeleton): L1 can start as **just the style fragment + asset classes** (enough
to feed the bootstrap), with world/spine prose added after the bootstrap works.

---

## 7. L2 Asset Studio + the bootstrap loop (Stages A–C)

Left = library tree (by class, status dots); center = **version selector + variant gallery +
generate bar + bootstrap strip**; right = inspector (prompt template, refs, anchor, consistency).

```
┌ Mara · v1_base ▾ (active) 🔓 ─── CHARACTER BOOTSTRAP ──────────────────────────────┐
│ A Casting              B Expansion                C Curation                        │
│ multi ×8/3 [Cast ▶]    method[img2img▾][+sketch]  keep ✓ / cull ✕                    │
│ grid ▦▦▦▦ pick ★       prompt-list (manual) [Gen] grid ▦▦▦  → ref set: 23            │
│ hero: ▣ → face step    [+ Flux2 multi-ref (spike)]                                   │
│ FACE: ▣▣▣ pick anchor ⛓                                          [Save AssetProfile] │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**Stage A — Casting (R38/R44, R105).** Fire the `multi` batch into the **simple scrollable
selectable grid** (the P0 grid component), `--num-candidates ≤ 5`, total ≤ ~200 (guidance — not
enforced, PM-6). **The pipeline mix is selectable per cast (R105):** tick which of
`flux2`/`sd35`/`zimage` participate (e.g. zimage-only for a fast cast), and **clean/polish are
independently selectable** toggles. *(Status 2026-06-11, PM-3: clean/polish shipped — better than
spec'd, as post-passes on ANY run; the per-pipeline mix-ticker is **deferred** pending a monorepo
`multi` CLI extension — the `fast`/`refined` ideation presets are the v1 mix control.)* Star the
**hero ★**. The style fragment from L1 is auto-**appended** (amended 2026-06-10, PM-1; per-gen
override checkbox, R104). Stage A is the **manual-experimentation** stage — free exploration to
find the look.

**Face-anchor sub-stage (R94).** Because the hero may be a full-body shot, generate a small set of
**detailed face portraits** and **pick one as the anchor** (`anchor.png`). Stored per version. The
**identity anchor is on by default** (opt-out, R93) and feeds Stage B.

**Stage B — Expansion = building the LoRA dataset (R11, §4.1; author round-13 answer 4).** Stage B
exists **strictly to produce LoRA-training material**, so it is **coverage-driven, not freeform**.
Rather than hand-typing prompts, the user picks a **dataset recipe** (a coverage matrix) and loom
**auto-generates the prompt list** to fill it — see §7.1. Methods that realize a recipe cell:

1. **img2img sweep** from the hero (`_img2img`, strength sweep) — fast bulk pose/expression variation.
2. **inpaint the hero into recipe-prescribed scenes** (`sd35`/`zimage` inpaint) — stable subject,
   varied background (the research's "background diversity" axis).
3. **video-sketch harvest** (`ltxv i2v` → extract frames) — multi-angle/pose coverage without 3D (R11).
4. **Flux2 multi-reference** — the investment; **research spike** (§11), may not be ready in P1.
- The **identity anchor is on by *default* once available** (R93) — when present, Stage-B images are
  **postprocessed to one face** (R86) *(as shipped in M4, 2026-06-11: an **inswapper face-swap to
  the anchor**, not PuLID — the M4 spike found PuLID-class tools target the wrong backbones; see
  §10 amendment)*, making the curated set face-consistent (exactly what
  makes a clean LoRA in P2). **But it is *not* required for the MVP done-line** (§12 hard guardrail):
  the done-line must be reachable with **no anchor and no postproc**; the anchor lands at M4, after
  the M3 done-line. So: *default-when-available, optional-for-MVP.*

### 7.1 Stage-B dataset recipe (grounded in current character-LoRA practice)

A **dataset recipe** is a coverage matrix loom fills automatically. Defaults below follow current
2025–26 character-LoRA guidance (incl. a Z-Image-specific guide — our first training base, R6),
targeting **~25–40 kept refs** (R107) — quality over quantity, 25 good beats 75 inconsistent.

| Axis | Values (default) | Why (per research) |
| --- | --- | --- |
| **Shot size** | face close-up · portrait (head+shoulders) · waist-up/medium · full-body | identity from close-ups; proportions/outfit from full-body |
| **Angle (yaw)** | front · ¾ left · ¾ right · profile left · profile right · back | fixes the "front-portraits-only fail on profile/back" problem (`kb-pipelines01.md`) |
| **Expression** | neutral · smile · serious/angry · sad · surprised (mainly close/portrait) | adaptability across scenarios |
| **Background** | varied per image (not one fixed set) ⚠ *deferred — see note below* | teaches the model to isolate the subject, not memorize a backdrop |
| *(hold constant)* | identity (anchor lock — *as shipped: inswapper swap, not PuLID*), outfit, L1 style fragment | the LoRA learns the character, not the noise |

> ⚠ **Background axis deferred (amended 2026-06-11, PM-2):** M3 realizes every cell via
> **img2img from the hero with `background:""`** (background terms fight the base image — user
> decision 2026-06-10), so a v1 Stage-B dataset carries ~one background. The axis returns with
> **inpaint-realized cells** (`realize="mixed"` in `recipe.py`, not yet exposed via `/stage-b`),
> which need a subject mask → **BiRefNet matting pulled forward as M3.5** (§13, ratified
> 2026-06-11). The P1→P2 contract already handles it: `build_caption` omits an empty background,
> and P2's readiness meter must treat the bg axis as **advisory** until M3.5 lands.

Rough default distribution for a ~30-image set: **~8 face close-ups** (front + ¾ L/R + a couple
expressions, for identity) · **~8 portraits** (expression variety) · **~9 medium/waist-up** (angle
variety incl. profile) · **~5 full-body** (front/¾/back), spread across varied backgrounds.

**Recipe presets (R111) — matched to how much detail the asset needs:**

| Preset | Generated candidates | Kept (after curation) | For |
| --- | --- | --- | --- |
| **Comprehensive** | **~100** (every matrix cell, multiple seeds) | a hero may keep ~40–60 if quality holds | **main characters** (need detail) |
| **Full-coverage** | ~40 | ~25–40 | default main/supporting |
| **Portrait-heavy** | ~30 | ~25 | dialogue-led characters (close/portrait) |
| **Full-body / outfit** | ~45 | ~30–35 | costume/proportion-critical |
| **NPC-lite** | ~20 | ~15–20 | NPCs (don't need much — author) |

The **Comprehensive** preset generates the full matrix exhaustively (~100) so there's plenty to
curate from; curation still **prunes to quality** (a hero keeps the strongest ~40–60, not all 100
— over-large/inconsistent sets *hurt* training, per the research). NPCs use the lite tier. The user
picks the preset and can tune target count + per-axis emphasis.

*(Shipped counts (M3, `recipe.py`, PM-4): comprehensive **78** / full_coverage **31** /
portrait_heavy **30** / full_body **45** / npc_lite **17** generated cells — within the "~"
targets except Comprehensive (78 vs ~100); tune the matrix toward ~100 or accept when the first
real Comprehensive sweep is curated.)*

**My recommendation on "no manual prompts in Stage B" (you asked):** I agree Stage B should have
**no freeform per-image prompt typing** — but replace it with a **structured recipe, not nothing**.
loom generates each prompt deterministically as `<recipe cell: shot-size, angle, expression[,
background]>, <character clause>, <L1 style>` *(amended 2026-06-10/11, PM-1: the cell fragment
LEADS and the style trails — front tokens dominate; was spec'd style-first)*. The **character clause defaults to the asset's stub
prompt-template snippet** (from L1/the spine — R112), keeping L1→L2 consistent; you can edit it
once. The user **picks the recipe + target + emphasis** and curates. This is *better* for LoRA
quality (systematic coverage, fixed identity clause) and far less tedious than hand-typing prompts.
When Muse lands (P3+), it enriches each cell's wording; the **matrix stays the contract**.

**Method per cell — auto-picked, but exposed for manual finalization (R113, quality-first).** A
recipe cell (e.g. "profile-left, waist-up") can be realized by img2img / inpaint / video-sketch;
loom **auto-selects** the best method per cell, then **shows it for the user to override/finalize**
before generating — so you can force, say, video-sketch for tricky angle coverage. Quality of the
LoRA material wins over convenience.

**Coverage-cell metadata → P2 captioning.** Each Stage-B image records its recipe cell
(shot-size/angle/expression/background) as structured metadata. P2's structured template captioning
step (no auto-tagging, no VLM in v1) consumes these tags directly,
so Stage B and P2 dovetail.

**Stage C — Curation (manual in P1).** Review the expansion grid against the recipe; **keep ✓ /
cull ✕** into the **curated ref set** (the future LoRA corpus), aiming for **~25–40 kept** (R107).
P1 curation is **human-only** (no readiness meter until P2; VLM scoring is P4). Cull near-dupes;
the recipe coverage view shows which cells are thin so you can re-roll just those.

**Save AssetProfile** → writes `profile.json` + `versions/v1_base/version.json` with the prompt
snippet, the curated `ref_set`, the `anchor.png`, and `casting[]` provenance. **This is the MVP
done-line.** (Training the LoRA from this set is P2.)

---

## 8. Profile versioning (implementation, §3.4)

- **Version selector** at the top of the stage: lists versions with the **active** starred and a
  **lock badge** when finalized; everything below is **scoped to the selected version** and
  **read-only once finalized**.
- **[+ new version]** → pick **any** prior version as parent → **full deep-duplicate** (refs,
  prompt snippet, face anchor; LoRA/voice later) → edit only what differs → **finalize** (pure
  intent → lock). Big change → **[+ new profile]** instead (manual choice, no hints).
- **Many versions expected** → the selector supports **grouping/search/naming**, not a flat list
  (R57).
- **Per-version face anchor** (R94): a version can re-pick its anchor (scar/tattoo).
- **Export / Import** (R66/R67): export a profile **with all versions** as a portable bundle;
  **import = always a new profile**, **rename on collision** (no merge).

Build note: versioning can ship **after** a single-version bootstrap works (walking-skeleton) —
v1-only first, then the copy-on-create/finalize machinery.

---

## 9. Image postproc toolkit (§8.3, `kb-postproc-img.md`)

Queueable per-image actions, each a postproc adapter (subprocess-isolated, one heavy model at a
time). P1 ships the image-side tools; lip-sync/TTS/video postproc are **P3**.

| Tool | Model (license in `kb-postproc-img.md`) | P1 use |
| --- | --- | --- |
| Matting / cutout | **BiRefNet** (MIT) | character/prop alpha for layering & clean refs *(pulled forward to **M3.5** for the Stage-B bg mask, 2026-06-11)* |
| Masking | **SAM 2** / **Grounded-SAM-2** (text→mask) | region edits / inpaint targets |
| Identity | **PuLID** (Apache 2.0) *(amended 2026-06-11: shipped as **inswapper_128** ReActor-class swap — InsightFace research/non-commercial; PuLID-class targets SDXL/FLUX.1 only → re-assess at P5 Track B)* | the **identity anchor** lock (Stage-B + inference) |
| Anatomy fix | **HandRefiner** (implemented) | clean hero/expansion frames |
| Face restore | CodeFormer / GFPGAN | tidy faces on promoted frames |
| Upscale | Real-ESRGAN | promote a candidate to a hi-res ref |

Prefer permissive options by default; keep the existing `_license_gate.py` ack for
research/non-commercial weights. Record each in the **component manifest** with its **phase**
(P0 §11 three-state model). **None of these are MVP-blocking** — matting/PuLID are
**default-when-available** (they improve the result) but **optional for the M3 done-line**, not
launch-essential; SUPIR-class is plainly optional. This matches the §12 guardrail (the MVP works
with no anchor and no postproc).

---

## 10. Identity anchor + face-anchor (R82/R86/R93/R94)

- **Anchor = a detailed face image + a PuLID/IP-Adapter lock.** Produced by the **face-anchor
  sub-stage** (§7); **per version**. *(Amended 2026-06-11, as shipped in M4: the lock is an
  **inswapper_128 ReActor-class face swap** to the anchor — post-hoc, model-agnostic, CPU; the
  spike found PuLID = SDXL/FLUX.1-dev only, InstantID = SDXL-only, no fit for
  Z-Image/SD3.5/klein. Diffusion-coupled identity re-assessed at the P5 Track-B/multi-ref era.)*
- **On by default, opt-out** per character/version (R93). Framed as a **polish** safeguard — the
  expectation is **LoRA (P2) + prompt-snippet injection** carry most preservation; the anchor is
  the extra precaution (R86).
- **Where it applies (all optional toggles):** during **Stage-B expansion** (postprocess images to
  one face — preferred, makes the curated set consistent so P2's LoRA trains clean), at
  **inference** (PuLID on any shot), and **across versions** (only the intended attribute changes).
  *(Constrained-training use is deferred beyond P2 v1 unless explicitly added to the trainer scope.)*
- **Anchor strength (R114).** Too strong flattens variety (every image the same face/angle); too
  weak lets identity drift. **v1 uses a single conservative-mid default**, but the value is
  **adjustable per output image** (so a hard-to-lock shot can be pushed). The natural later
  enhancement: **the SLM sets per-image strength from the prompt** (e.g. lower it for a back/profile
  shot) — deferred to when Muse lands (P3+). *(Amended 2026-06-11, as shipped in M4: inswapper is
  **binary** (swap/no-swap) — there is no strength knob; the shipped per-image control is
  `min_det_score` (skip-threshold) + no-face passthrough. A blend-alpha param is the future
  "strength" if ever needed — R114's per-image-strength semantics are deferred to that.)*

---

## 11. Flux2 multi-reference research spike (§8.1 item 2; R24)

> **✅ RESOLVED — GO + WIRED INTO P1 (2026-06-13, P1-11).** Spike: the native reference pathway
> already exists in `flux2/src/flux2/sampling.py` (`encode_image_refs` + `denoise(img_cond_seq=…)`)
> — the loom worker just never called it; the probe carried the hero's identity into a new
> full-body scene (the expansion img2img can't do). Per R147 (a "go" *becomes Stage-B's preferred
> method*) the user pulled it in: flux2 is now a **Stage-B `ref` mode** — the hero ★ rides as an
> in-context reference, each coverage cell driving pose/angle/scene while identity holds. Shipped:
> a flux2 `--jobs-file` batch worker (two-phase offload, module-invoked), `adapters/flux2.py`,
> `/assets/{id}/stage-b` `pipeline=flux2`, UI option. 231 no-GPU tests + a **GPU batch smoke**
> (2 cells, identity carried into a tavern waist-up + a night-rampart full-body profile). Detail:
> journal "P1-11 (wired)". Diffusion-coupled identity (PuLID-class) stays deferred — flux2
> multi-ref is the proven better-fit.

Run **in parallel** with the buildable bootstrap; **may not pan out** on ROCm Flux2.

- **Goal:** wire `--ref-image` + structured prompt into the local `flux2` CLI so the hero can be
  conditioned into new scenes directly (the best "insert this character anywhere" path).
- **Risk:** `kb-pipelines01.md`/`kb-flux2.md` describe multi-ref as *conceptual / not yet wired
  locally* — the spike is genuine R&D and might fail.
- **Fallback:** the img2img/inpaint/video-sketch ladder (§7) is the buildable path; if the spike
  succeeds it becomes Stage-B's preferred method, if not it's deferred to **P5 Track B (R147)** — not
  P6 (P6 must not reclaim it, `kb-loom-p6.md` §1).
- **Output of the spike:** a go/no-go note + (if go) a `flux2` adapter capability flag for
  multi-ref. Do **not** block the MVP done-line on it.

---

## 12. Risks & guardrails (author reflections — agreed)

Four real risks the author raised; each gets an explicit guardrail baked into the plan.

1. **P0's invisible engineering is substantial — don't underestimate "foundation."** Even with the
   walking skeleton, durable queueing, atomic writes, schema validation, lineage, disk guard,
   launch checks, and adapter hardening are real work. **Guardrail:** treat P0 as **the longest
   phase** and track each foundation item as a discrete, demoable deliverable (kb-loom-p0 §12
   M0–M8). The skeleton **retires integration risk early but does not shrink the foundation** —
   plan the schedule accordingly. (The author already flagged P0+P1 as heavy-investment.)

2. **P1 hinges on adapter-onboarding quality — avoid "adapter-debugging instead of asset-building."**
   `multi`/`flux2`/`sd35`/`_img2img`/`ltxv`/postproc each may have quirks. **Guardrails:** (a) the
   shared `PipelineManifest` convention + zimage's save-then-raise mean we *normalize* an existing
   contract, not invent one (kb-loom-p0 §15); (b) **a wrapper quirk is fixed in the wrapper to meet
   the contract, not special-cased in the adapter** (keeps the adapter layer thin); (c) **the first
   skeleton path starts `zimage`-only** (already onboarded in P0), so early payoff is reachable
   before the full adapter set; new adapters are added incrementally as milestones need them
   (R105's selectable casting subset makes this natural).

3. **Identity-anchor + postproc could steal focus from the done-line.** PuLID/matting/masking/
   restore/upscale/HandRefiner/face-anchor are useful but expandable. **Guardrail (hard rule):**
   the **MVP-proof line — a saved curated AssetProfile — must be reachable with NO anchor and NO
   postproc.** The milestone order enforces this: **M3 hits the MVP-proof line** with a single version,
   manual curation, and **zero** anchor/postproc; anchor (M4), versioning depth, and postproc (M6) are
   **Phase-B thickening toward *full P1 acceptance*, strictly after** the MVP-proof line. *(Distinguish:
   **MVP proof** = M3 steel-thread (one saved profile); **full P1 acceptance** = + anchor + versioning +
   postproc + the full adapter set.)* Keep the P1 postproc set minimal (matting + PuLID
   essential; restore/upscale "as needed", SUPIR-class deferred).

4. **Flux2 multi-ref stays truly non-blocking.** **Guardrail:** the spike (§11) runs in **parallel**
   and **never gates** any milestone; the **fallback ladder (img2img + inpaint + video-sketch) must
   be good enough that the MVP succeeds without it.** If the spike is not "go" by the time Stage-B
   is built, Stage-B ships on the ladder and multi-ref is deferred to **P5 Track B (R147)** — no MVP impact.

## 13. P1 milestones — walking skeleton first (build order)

Lead with the **shortest path to a saved character**, then thicken (mirrors P0 §12).

### Phase A — Bootstrap skeleton (a character, end to end, minimal)

1. ✅ **M1 — asset library + profile record + minimal style fragment (zimage-only scaffold).** L2 tree;
   create an AssetProfile; write `profile.json` + a single `v1_base` version (no versioning machinery
   yet); promote the P0 grid into L2 reusing the **`zimage` adapter only**. **Seed a minimal default
   L1 style fragment** — one editable prompt fragment — so Stage A/B (M2/M3) have something to
   auto-prepend *before* the full L1 World UI exists. *(Scaffold — not the done-line; full World
   authoring is M8.)*
2. ✅ **M2 — Stage A casting (`multi`).** Onboard the **`multi`** adapter (1-page check); [Cast ▶] →
   selectable grid → star hero ★ → save into `v1_base`. *(First visible character payoff.)*
3. ✅ **M3 — Stage B + C → done-line.** Onboard **`_img2img` + `sd35`** (img2img + inpaint); run the
   **coverage-matrix dataset recipe** (§7.1, auto-generated prompts) → grid; **keep/cull** →
   curated `ref_set`; **Save AssetProfile** (state = **Saved, not Finalized**, R119). *(MVP
   done-line — single version, no anchor/postproc/Flux2-spike, R110.)* *(Done 2026-06-09/11 —
   realized **img2img-only**; inpaint cells deferred with the bg axis, §7.1 note. **✅ ACCEPTED
   2026-06-11** — user GPU sign-off incl. chained clean/polish, journal PM-10.)*

**M3.5 — background-diversity realization (added 2026-06-11; matting pulled forward from M6,
PM-2).** Onboard **BiRefNet matting** (the first postproc-class adapter; monorepo-first per
R162-as-practiced) → hero **subject matte** → inverted **bg mask**; expose **`realize="mixed"`**
through `/stage-b` so inpaint-method cells **repaint the background around the held subject**
(identity-safe — subject pixels preserved; restores the §7.1 background axis; R113's per-cell
method becomes real). **Scope box:** matting + bg-mask + mixed realization ONLY — the rest of
the postproc toolkit stays M6. *(Why before M4: inpaint cells need no anchor; the first big
Comprehensive curation should happen on a bg-diverse set — that's the P2 corpus; matting
pioneers the models.json P1-weight entry + postproc plumbing so the M4 spike tests only model
feasibility.)*

### Phase B — Thicken

4. ✅ **M4 — face-anchor + identity anchor.** Face sub-stage (generate portraits → pick `anchor.png`);
   PuLID adapter; on-by-default Stage-B face lock (opt-out). *(Done 2026-06-11, spike-first — as
   shipped: **inswapper_128 swap**, not PuLID (spike rung C: wrong backbones); anchor = any owned
   output picked via `POST /assets/{id}/anchor`; default-on gated on a **verified** anchor;
   journal M4. E2E rig verify pending.)*
5. ✅ **M5 — profile versioning.** Copy-on-create from any parent, finalize/lock, version selector
   (grouping/search), per-version anchor, new-profile path. *(Done 2026-06-11 — full
   deep-duplicate incl. anchor verification, R60 lock on every mutator, parent-picker modal,
   read-only finalized UI; **R57 grouping/search deferred** — flat select v1, revisit past ~8
   versions; journal M5.)*
6. ✅ **M6 — image postproc toolkit.** Matting/masking/restore/upscale as queueable actions.
   *(Matting pulled forward to **M3.5**, 2026-06-11 — M6 keeps masking/restore/upscale; re-size
   when reached: each tool ≈ a pass backend on the post-pass chaining shipped in M3, PM-9.)*
   *(Done 2026-06-12 **as the re-size predicted**: M6 v1 = the **face-restore pass** (GFPGAN
   1.4 ONNX — the basicsr pip stack is broken on modern torchvision; chain order clean →
   polish → identity → restore, fixing the M4 swap softness). **Masking (SAM2) + upscale
   (Real-ESRGAN-class) + HandRefiner onboarding deferred "as needed"** per the §12 minimal-set
   guardrail; journal M6.)*
7. ✅ **M7 — video-sketch harvest.** `ltxv` adapter + frame extract as a Stage-B method.
   *(Done 2026-06-12: cell-targeted i2v + chained `frame_harvest`; rig E2E owed.)*
8. ✅ **M8 — full L1 World authoring.** Promote M1's minimal style fragment into the full **L1 World UI**
   — editable style fragment(s), asset classes, naming, spine → stub profiles + manual re-sync,
   structured/explicit injection. *(Done 2026-06-13 — shipped the system-connecting core: world
   prose + style **global negative** (auto-applied to every gen surface — /generate, Stage-B,
   sketch) + the **story spine → stub AssetProfile** connector (R112 snippet inheritance, R55
   manual re-sync). Deferred as descriptive-only: configurable asset-classes, `@asset@version`
   injection picker, moodboard. Journal M8.)*
9. ✅ **M9 — export/import profiles.** Bundle a profile with all versions; import = new profile,
   rename on collision. *(Done 2026-06-13 — zip bundle + loom_bundle.json; import mints fresh
   ids, remaps derived_from, renames on collision; cross-project round-trip tested. Journal M9.)*
10. **Spike (parallel) — Flux2 multi-ref** (§11); go/no-go.

### Done-line

11. 🟡 **M10 — MVP acceptance.** Style → cast → expand → curate → **saved, reopenable AssetProfile**
    (§1). Record contract gaps from the new adapters. *(2026-06-13 — the done-line is locked as an
    **executable no-GPU test** (`test_acceptance.py`: style→cast→hero→expand→curate→save→**reopen**,
    asserting the saved v1 + ref_set/coverage + rejected + hero + style all survive the disk
    round-trip) and the **adapter contract-gaps** are recorded (journal M10). **Remaining = the
    user's GPU rig pass** A–H (done-line + chained passes + mixed + identity + restore + video +
    curation + export/import round-trip) to declare P1 ACCEPTED. Journal M10.)*

---

## 14. Out of scope (defer to P2+)

- **LoRA training** + **proxy readiness meter** → **P2** (the curated `ref_set` from P1 is its input;
  new-version training **seeds from base by default**, R68). **VLM-assisted captioning/scoring** → **P4**.
- **All 3D** (`trellis2`/→3D props & scenery, depth proxies) → **P6** (R128, supersedes R108).
- Shots/continuity/audio/voice/lip-sync → **P3**. Flow + Muse agent → **P4**. Episode → **P5**.
- **Muse-authored** Stage-B prompt lists and **Qwen3-VL** curation → layered in when the relevant
  Muse/VLM phases land (P3/P4); P1 is manual.

---

## 15. Resolved (round 13) & still-open (round 14)

**Resolved (R104–R110, recorded in `kb-storyboard01.md` §10.0):**

| # | Decision |
| --- | --- |
| R104 | Style = **fixed prepend + per-gen override checkbox**. *(Amended 2026-06-10: **appended**, not prepended — the character/cell prompt leads.)* |
| R105 | Casting pipeline mix is **selectable per cast**; **clean/polish independently selectable**. *(Status 2026-06-11: clean/polish shipped as post-passes on ANY run; mix-ticker deferred — needs a monorepo `multi` CLI extension; fast/refined presets = v1 control.)* |
| R106 | Curated-set target ≈ **25–40 kept refs**. |
| R107 | **Stage B = coverage-matrix dataset recipe** (auto-generated prompts, no freeform typing); user picks recipe + curates (§7.1). |
| R108 | **All 3D deferred** (no `trellis2`/→3D in P1). *(Superseded by R128: 3D now → P6, not P3.)* |
| R109 | Stage A = manual experimentation; **Stage B = structured LoRA-dataset building**. |
| R110 | P1 risk guardrails accepted (§12): done-line independent of anchor/postproc/Flux2-spike; zimage-first; fix-wrapper-not-adapter. |

**Resolved (round 14, R111–R114, in `kb-storyboard01.md` §10.0):**

| # | Decision |
| --- | --- |
| R111 | Ship recipe presets **Comprehensive (~100, main chars)**, Full-coverage, Portrait-heavy, Full-body/outfit, **NPC-lite**; main chars need detail, NPCs don't (§7.1). |
| R112 | Character clause **defaults to the stub prompt-template snippet** (L1→L2 consistency), editable once (§7.1). |
| R113 | Per-cell method is **auto-picked but exposed for manual finalization** — quality first (§7.1). |
| R114 | Anchor strength: **single conservative default v1, adjustable per output image; SLM-driven per-image later** (§10). |

No P1 questions remain open.

## 16. Work-package breakdown (WBS) — what P1 actually contains

*Rough solo-dev sizing: **S** ≤1 d · **M** 2–4 d · **L** 1–2 wk · **XL** 3 wk+. Risk: 🟢 routine ·
🟡 unknowns · 🔴 R&D. Maps to the §13 milestones.*

| WP | Work package | Maps to | Size | Risk |
| --- | --- | --- | --- | --- |
| P1-1 | L2 asset library tree + `AssetProfile` record (zimage scaffold) | M1 | M | 🟢 |
| P1-2 | **Stage A casting** — onboard **`multi`** adapter (1-page check) + casting grid → pick → snippet → Saved | M2 | M | 🟡 adapter onboarding |
| P1-3 | **Stage B + C** — onboard **`_img2img` + `sd35`** (img2img + inpaint); dataset generation + curation grid | M3 | L | 🟡 |
| P1-4 | Coverage-matrix dataset recipe (presets: Comprehensive ~100 / Full-coverage / Portrait / Full-body / NPC-lite) + **per-cell metadata** | M3 | M | 🟡 *(P2 depends on this schema)* |
| P1-5 | **Face-anchor sub-stage** (portraits → `anchor.png`) + **identity anchor** (PuLID post-process, on-by-default, per-output strength) *(shipped 2026-06-11 as **inswapper swap**, binary, verified-anchor-gated default-on — see §10 amendments)* | M4 | L | 🟡 anchor scope |
| P1-6 | **Profile versioning** (full-duplicate copy-on-create from any parent; three states Unsaved/Saved/Finalized; finalize/lock) | M5 | L | 🟢 logic-heavy |
| P1-7 | Image postproc toolkit (masking/restore/upscale as queueable actions; *matting split out → P1-17/M3.5, 2026-06-11*) | M6 | M | 🟢 |
| P1-8 | Video-sketch harvest (`ltxv` adapter + frame extract as a Stage-B method) | M7 | M | ✅ *(done 2026-06-12: cell-targeted i2v + chained `frame_harvest`; rig E2E owed)* |
| P1-9 | L1 World (style fragment auto-apply, asset classes, naming, spine → stub profiles) | M8 | M | ✅ *(done 2026-06-13: world prose + style global-negative + spine→stub w/ R55 re-sync; asset-classes/injection-picker deferred)* |
| P1-10 | Export/import profiles (bundle profile + all versions; import = new profile, rename) | M9 | M | ✅ *(done 2026-06-13: zip bundle + fresh-id import + collision rename; cross-project round-trip tested)* |
| P1-11 | Flux2 multi-reference research spike (non-blocking) | §11 | S | ✅ **GO + WIRED** (2026-06-13): Stage-B `ref` mode (hero as in-context reference) — identity-preserving pose/scene expansion; GPU batch smoke verified |
| P1-12 | **Curation grid throughput** — bulk select/reject, keyboard nav, filter-by-coverage-cell; the *reject* workflow for culling ~100→~30 | ~~M3~~ → **post-M4, pre-P2** *(re-homed 2026-06-11, PM-5)* **✅ shipped 2026-06-11** *(persistent `rejected[]` + bulk + k/x/space keys + coverage filters; journal P1-12)* | M | 🟡 *folded from gap* |
| P1-13 | **Stub-profile prompt-snippet editor** — author the injectable snippets the casting-stub stores (P2 reads them) | M8 | S | 🟢 *folded from gap* |
| P1-14 | **Anchor-strength control surface** — where per-output strength lives in the gen flow (single default v1) | M4 | S | 🟢 *folded from gap* *(2026-06-11: inswapper is binary — shipped control = `min_det_score`; folds into the future blend-param decision, §10)* |
| P1-15 | **Create-flow branching: main-character vs NPC-lite** — preset routing (dataset size/coverage) at profile creation | ~~M1~~ *folded 2026-06-11 (PM-5): satisfied-by-design by the Stage-B preset picker — no per-profile routing needed* | S | 🟢 *folded from gap* |
| P1-16 | **Freeze the coverage-cell metadata schema (P1→P2 contract)** — the structured fields P2's template captioner (P2-3) consumes | M3 | S | 🟡 *folded from gap* |
| P1-17 | **M3.5 background-diversity realization** — BiRefNet matting adapter (first postproc-class) + hero bg-mask + `realize="mixed"` via `/stage-b` (img2img + inpaint cell split) | M3.5 | M | 🟢 *(split from P1-7, added 2026-06-11)* |

**Rollup:** ~16 WP; **P1's heaviest WPs are P1-3/P1-5/P1-6** (dataset+curation, anchor, versioning) —
note anchor (P1-5) + versioning (P1-6) belong to **full P1 acceptance**, *not* the **MVP-proof** line
(M3 = dataset+curation, single version, no anchor/postproc).
Three new adapters onboard here (`multi`/`_img2img`/`sd35`, R121) — onboarding quality is the
recurring 🟡. **P1-12–P1-16 were surfaced by the WBS gap-scan and are now planned** — P1-12
(curation throughput) and P1-16 (the P1→P2 metadata freeze) are the load-bearing ones.

**Design note:** **P1-16 is a hard contract, not a nicety** — P2's template captioner (no VLM, R116)
turns coverage-cell metadata directly into captions, so the cell schema must be *frozen* in P1 and
versioned; treat any later change as a breaking change to P2.

## Source / traceability

- Decisions: `kb-storyboard01.md` §10.0 (R1–R114); bodies §3.1/§3.2/§3.4 (data + versioning),
  §4.1 (bootstrap), §6.2 (World), §6.3 (Asset Studio), §8.3 (postproc).
- Spine reused: `kb-loom-p0.md` (queue, adapter contract, workspace I/O, disk guard, components).
- Worker contracts/CLIs: `kb-pipelines01.md`, `kb-postproc-img.md`, and
  `src/pipeline/{multi,flux2,sd35,zimage,_img2img,ltxv,postproc}`.
