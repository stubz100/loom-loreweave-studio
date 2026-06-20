# Loreweave Studio — P6 build spec (Polish — post-v1, no essential deliverables)

Created: 2026-06-04
Status: spec (post-v1; **not required for alpha** — see §1; depends on a shipped P0–P5)
Parent: [`kb-storyboard01.md`](kb-storyboard01.md) (overview + decision record R1–R170)
Predecessors: [`kb-loom-p0.md`](kb-loom-p0.md) · [`kb-loom-p1.md`](kb-loom-p1.md) · [`kb-loom-p2.md`](kb-loom-p2.md) · [`kb-loom-p3.md`](kb-loom-p3.md) · [`kb-loom-p4.md`](kb-loom-p4.md) · [`kb-loom-p5.md`](kb-loom-p5.md)
Bodies: `kb-storyboard01.md` §6.7 (engine/DCC export + plugin API), §8.3 (3D/`trellis2`), R37/R128/R138/R170

---

## 1. Purpose & the P6 framing — **P5 is the alpha gate, P6 is overflow (R165)**

> **⚠ Read this first — the governing rule of the whole project (R165):**
>
> **P5 is the final phase for alpha (v1).** Full core functionality — World → Assets →
> LoRA → Shots → Flow → Episode/Export — **ships in P5**, **without changing the scope defined in
> the P0–P5 phase documents.** Precisely (R167): the **alpha/v1 gate is P5 Track A's M7
> episode-acceptance** — when M7 is green the product is **feature-complete for v1**. **P5 Track B
> (M8–M10) is post-alpha hardening** that lives in P5 but **does not gate v1**.
>
> **P6 has NO essential deliverables for the core functions of the application.** Nothing in P6
> gates v1. P6 is two things and only two things:
> 1. a **parking lot for deliberately deferred polish** (all 3D, the effect plugin API, engine/DCC
>    export, and a couple of nice-to-haves), and
> 2. a **flexible overflow bucket**: if an *unforeseen* development point surfaces during P0–P5
>    (a spike fails, a contract gap appears, a "we actually need X" emerges), **it goes here** —
>    so the P0–P5 scope stays fixed and the alpha gate stays meaningful.
>
> Because of (2), **P6's scope is intentionally open-ended and will grow.** That is by design.
> Do not let P6 work block, expand, or redefine P0–P5. **Keep this rule in mind throughout the
> entire development process.**

**Why this matters operationally.** The temptation across P0–P5 will be to "just add one more
thing" to a phase to make it feel complete. R165 says: don't. Land the phase as specced, and
**push the extra into P6.** This keeps each phase shippable, keeps the alpha date real, and gives
us a single honest list (P6) of "everything we chose not to do for v1." P6 is therefore **the
project's release valve**, not a planned body of work with a fixed end.

### 1.1 The P6 "done-line" — there isn't a hard one (and that's intentional)

Unlike P0–P5, **P6 has no single acceptance test that gates anything.** Its items are independent
and individually optional; each ships when (and if) it's worth doing post-v1. The closest thing to
a milestone is per-feature: *"3D props can be generated and used as L2 assets,"* *"a plugin can
register an effect,"* *"a project exports to Unreal/Blender."* None blocks the others; none blocks
v1.

---

## 2. Scope — what lives in P6

| Bucket | Item | Origin | Notes |
| --- | --- | --- | --- |
| **Deferred polish** | All **3D** — `trellis2` props/scenery GLBs + the **proxy-miniature depth-ControlNet** route | R128 (moved out of P3) | §3 |
| | **Effect plugin API** (Python / JS / TS) | R37 | §4 |
| | **Engine / DCC export** — assets *and* the Flow graph → **Unreal 5.7+ / Blender** | §6.7 | §5 |
| | **GraphRAG retrieval index** (typed project graph + same `Qwen3-VL-Embedding` model) | R138/R170 | §6 — deferred from P4 |
| | **Video LoRAs** (LTXV / Wan motion) | P2/P4 deferrals | §6 — needs video work + larger scratch |
| **Flexible overflow** | **Any unforeseen P0–P5 development point** | R165 | §7 — the bucket grows here |

**Not in P6** (already pulled *forward* to P5 by R147, do not re-list here): deeper Flux2 multi-ref,
LTXV-extend hardening, multi-LoRA stacking + usable style LoRA, postproc expansion. Those are **P5
Track B** — P6 must not duplicate or reclaim them.

---

## 3. All 3D — `trellis2` + the proxy-miniature depth route (R128)

3D was deferred wholesale from P3 to here (R128) because it has **no impact on the core
storyboard→episode loop**: a v1 episode is made entirely from 2D image/video pipelines.

**3a. `trellis2` props & scenery as L2 assets.** `trellis2` already has the
manifest/preset/CLI shape the platform assumes (`kb-trellis2.md`), so it onboards through the
**same pipeline-adapter contract** as every other worker (P0 §8). A generated **GLB** becomes a new
**asset class** in L2 Asset Studio (props, scenery), versioned like any other AssetProfile (§3.4),
with its GLB stored under the asset's `glb/` (the layout already reserves `assets/.../` for this).

**3b. The proxy-miniature depth-ControlNet route.** The keyframe compositor (P3 §5) already records
**camera intent**; this route lets a 3D proxy *drive* that intent for hard-to-keep geometry:
- build a rough **proxy miniature** of the scene (placed `trellis2` GLBs + simple stand-ins),
- render **depth / silhouette / RGB passes** from the intended camera — the shots layout already
  reserves `shots/<scene>/<shot>/proxy/` for exactly these passes,
- feed the **depth pass into a depth-ControlNet** at keyframe generation so the 2D image inherits
  consistent geometry/parallax across a move.

This is a **quality/consistency aid for the existing 2D keyframe path**, not a 3D renderer — loom
never renders the episode in 3D. It plugs into the P3 compositor as an optional pre-step.

> **Risk:** ROCm-gated like every heavy worker; `trellis2` VRAM fit on the RX 9070 XT (16 GB) must
> be checked (same can-it-run front-gate pattern as P2-0). One heavy model at a time (R21) — a
> `trellis2` job is an ordinary single-GPU queue job.

---

## 4. Effect plugin API (R37)

A **third-party extension surface** so effects/filters can be added without forking loom.

- **Languages:** Python (in-process with the orchestrator's env) and JS/TS (in the UI / a sandboxed
  worker), per R37.
- **Shape:** an effect plugin **registers** into the existing surfaces rather than inventing new
  ones — most naturally as a **postproc adapter** (the `src/pipeline/postproc/<tool>/` pattern,
  subprocess-isolated, license-gated `_license_gate.py`, ROCm-gated) on the **L2/L3** surfaces, *not*
  L5 (R18 keeps the episode layer assembly-only). *(Cross-ref 2026-06-11: the **first**
  postproc-class adapter — BiRefNet matting — was pulled forward to **P1/M3.5** (`kb-loom-p1.md`
  §13), so the adapter+manifest pattern this plugin API formalizes has a concrete reference
  implementation from P1 onward.)*
- **Contract:** a plugin declares its **inputs/outputs, params, VRAM estimate, and presence/
  capability** through the same component-manifest + adapter-contract machinery P0 hardened — so a
  plugin job is just another **queue job** under the single-GPU rule (R141), and a missing/declined
  plugin is reported, never silently run.
- **Safety:** plugins are **declared, presence-checked, and explicitly enabled**; they obey the
  no-auto-trigger / no-concurrent-GPU rules. Untrusted JS runs sandboxed.

This is genuinely post-v1: v1 ships a **fixed, curated** postproc/effect set (P1 toolkit + P5 Track
B expansion); the plugin API only *opens* that set to outside code.

---

## 5. Engine / DCC export — assets *and* graph → Unreal 5.7+ / Blender (§6.7)

The lowest-priority, most forward-looking item: hand a project's **assets and its Flow graph** to a
real engine/DCC for downstream production.

- **Targets:** **Unreal Engine 5.7+** and **Blender 5.1.1** (the locked engine/DCC decision;
  BlenderMCP is reachable on `:9876` as the DCC hub).
- **Assets:** export GLBs (§3) + textures + the relevant 2D plates into an engine-friendly bundle —
  the layout already reserves `exports/handoff/` for "engine/DCC asset bundles (GLB + textures +
  graph)".
- **Graph:** export the **L4 Flow graph** (nodes + edges + conditions + variable mutations) into a
  form a Sequencer/timeline tool or a custom importer can consume, so the *narrative structure*
  travels, not just the media.
- **Boundary:** this is a **handoff**, not a live integration (mirrors the R19/R28 Resolve stance —
  file-based, no API coupling). loom produces files; the engine imports them.

> Explicitly **low-priority** (§6.7) and the least-defined P6 item — it may stay a stub for a long
> time. It does not gate anything.

---

## 6. Other deferred candidates

- **GraphRAG retrieval index (R138/R170).** P4 shipped *text* project-context (R145) plus a
  rebuildable typed fact sidecar (`context/project_facts.jsonl`) and deferred the persistent
  retrieval layer. P6 can build the real GraphRAG stack: ingest typed project facts into a graph,
  attach embeddings from the **same `Qwen3-VL-Embedding-8B` model already on disk**, index finalized
  stills/clips/captions/summaries, and expose retrieval to Muse for relational/global questions and
  asset reuse. Example queries: "which shots use Mara before v2?", "which LoRA was trained from this
  ref set?", "which props appear in this branch but not the render list?" Embedding/index jobs run
  exclusively (vision tenant, R137) like all AI work — queued, never concurrent (R141).
- **Video LoRAs (LTXV / Wan).** Motion LoRAs were parked (need video training work + a larger
  scratch budget, ~20–80 GB runs). A P6 item, not a v1 capability.

Both are **opt-in quality plays**, not core-loop features.

---

## 7. The overflow bucket — how unforeseen P0–P5 items land here (R165)

This is the mechanism that keeps the alpha gate honest.

- **During P0–P5**, when a spike under-delivers, a worker contract has a gap, or a "we actually need
  X to be good" realization appears that is **not already in that phase's spec**, the default is:
  **do not expand the current phase** — **log it as a P6 item** and ship the phase as specced.
- Each overflow item should be recorded here with: **what it is, which phase surfaced it, why it was
  deferred, and whether it's quality (nice-to-have) or a genuine gap to revisit.**
- **Exception — true v1 blockers:** if an item is genuinely *essential* for the core loop to work at
  all (not just to be better), it is **not** overflow — it belongs in its phase, and deferring it
  would mean v1 is broken. That judgment call is explicit and rare; the bias is **toward P6**.

**Overflow log (append as P0–P5 development proceeds):**

| # | Item | Surfaced in | Why deferred | Type |
| --- | --- | --- | --- | --- |
| — | *(none yet — populated during P0–P5)* | — | — | — |

---

## 8. Build dependencies

- **A shipped P0–P5.** P6 builds only on a feature-complete alpha; it adds nothing P0–P5 needs.
- **3D:** `trellis2` worker (present in repo, manifest/CLI shape ready, `kb-trellis2.md`); ROCm fit
  check on the RX 9070 XT.
- **Plugin API:** the P0 adapter-contract + component-manifest machinery; a JS sandbox for UI-side
  plugins.
- **Engine export:** Unreal 5.7+ / Blender 5.1.1 (+ BlenderMCP `:9876`); a graph-export serializer.
- **GraphRAG:** `Qwen3-VL-Embedding-8B` (on disk); a graph store and vector index over
  `context/project_facts.jsonl`, captions, summaries, and visual embeddings.

---

## 9. Disk & VRAM

- **VRAM (16 GB):** unchanged rules — one heavy model at a time (R21), every model-loading task is a
  single-GPU **queue job** (R141), AI never concurrent with gen/train. `trellis2` and any video-LoRA
  run get the same can-it-run front-gate as P2-0.
- **Disk:** 3D GLBs + proxy passes + a visual index add to the per-project footprint; the same
  cap + two-threshold disk-guard (R96, default 250 GB / R164) apply. Nothing here changes the
  R161 master-retention rule.

---

## 10. Risks & guardrails

- **Scope creep into P6 is *expected* — scope creep *out* of P6 into P0–P5 is the real risk.**
  Guardrail: R165. P6 absorbs; it never pushes work back up the chain or delays the alpha.
- **P6 becoming an excuse to under-build P0–P5.** Guardrail: §7's "true v1 blocker" exception —
  essential-for-the-core-loop items stay in their phase; only *polish/quality/unforeseen-extra* is
  deferred.
- **ROCm/VRAM on the heavy 3D + video-LoRA items** — gated, same pattern as P2-0; may simply be
  "not feasible on this rig," which is an acceptable P6 outcome.

---

## 11. Out of scope / non-goals

- **Anything that gates v1** — by definition not P6 (R165).
- **A live engine/DCC integration** (API-coupled) — handoff files only (§5).
- **Re-listing P5 Track B** (Flux2-deep / LTXV-extend-harden / multi-LoRA+style / postproc-expand) —
  those are P5 (R147).
- **Episode-level audio mixing / SFX / music / stems** — that's a separate post-v1 Resolve concern
  (R42/R95), not P6 polish.

---

## 12. Resolved decisions feeding P6

| Decision | Meaning |
| --- | --- |
| **R165** | **P5 = alpha (v1) gate; P6 = flexible overflow with no essential deliverables.** The governing rule (§1). |
| **R128** | All 3D / depth proxies deferred to P6 (supersedes R108's P3 placement). |
| **R37** | Effect plugin API (Python / JS / TS). |
| **R138** | Persistent retrieval index deferred (same Qwen3-VL-Embedding model). |
| **R170** | GraphRAG posture: typed facts in P2/P4 now, persistent graph/vector index + retrieval/query here. |
| **R147** | Flux2-deep / LTXV-extend-harden / multi-LoRA+style / postproc-expansion moved P6 → **P5** (do not reclaim). |

---

## 13. Work-package breakdown (WBS) — intentionally light & open

*P6 is not estimated as a fixed body of work (R165). These are the **known** parked items; the
overflow bucket (§7) adds more during P0–P5. Sizing is indicative only.*

| WP | Work package | Area | Size | Risk |
| --- | --- | --- | --- | --- |
| P6-1 | `trellis2` onboarding → 3D **prop/scenery GLBs as L2 assets** (adapter + asset class + versioning) | 3D | M | 🟡 ROCm fit |
| P6-2 | **Proxy-miniature depth route** — proxy build → depth/silhouette/RGB passes → depth-ControlNet into the P3 keyframe compositor | 3D | L | 🔴 R&D |
| P6-3 | **Effect plugin API** — Python + JS/TS registration via the postproc-adapter/component-manifest contract; sandbox + safety | Extensibility | L | 🟡 |
| P6-4 | **Engine/DCC export** — asset bundle (GLB+textures+plates) + Flow-graph serializer → Unreal 5.7+/Blender handoff | Export | L | 🟢 low-priority |
| P6-5 | **GraphRAG retrieval index** (typed project graph + Qwen3-VL-Embedding vector store; Muse + asset-reuse retrieval) | Retrieval | M | 🟡 |
| P6-6 | **Video LoRAs** (LTXV/Wan motion training path) | Training | L | 🔴 VRAM/scratch |
| P6-7+ | **Overflow items** (from §7) | — | — | grows during P0–P5 |

**Rollup:** deliberately unbounded. **None of these gate v1** — P5 is the gate (R165).

---

## Source / traceability

- Decisions: `kb-storyboard01.md` §10.0 — **R165** (alpha gate), R128 (3D), R37 (plugin API),
  R138/R170 (GraphRAG), R147 (Track B moved to P5).
- 3D worker: `kb-trellis2.md` (CLI + manifest shape).
- Engine/DCC: the locked Unreal 5.7+ / Blender 5.1.1 decision; BlenderMCP `:9876`.
- The core-loop phases this one must never disturb: `kb-loom-p0.md` … `kb-loom-p5.md`.
