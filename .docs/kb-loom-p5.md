# Loreweave Studio — P5 build spec (Episode / Export — the headline deliverable)

Created: 2026-06-03
Status: spec (not yet implemented; **depends on P0 + P1 + P2 + P3 + P4** — render records reference `flow_node_id` from the P4 Flow graph, so P4 is a hard dependency, not just a source)
Parent: [`kb-storyboard01.md`](kb-storyboard01.md) (overview + decision record R1–R158)
Predecessors: [`kb-loom-p0.md`](kb-loom-p0.md) · [`kb-loom-p1.md`](kb-loom-p1.md) · [`kb-loom-p2.md`](kb-loom-p2.md) · [`kb-loom-p3.md`](kb-loom-p3.md) · [`kb-loom-p4.md`](kb-loom-p4.md)
Bodies: `kb-storyboard01.md` §6.7 (Episode/Export workspace), §3.2 (Render record)

---

## 1. Purpose & the P5 done-line

P5 is the **L5 Episode/Export layer** — **where the platform earns its keep** (the headline goal from
day one: *visualize your main storyline as a ~30-minute episode for pitching/streaming*). It turns
the P3 finalized node clips + the P4 Flow graph into a **watchable episode video** and **file-based
exports for DaVinci Resolve**. Crucially, **assembly is select-and-stitch, never generation** (R9) —
loom stays a video/story tool, **not** an after-effects tool (R18); heavy picture finishing belongs
in Resolve.

**P5 done-line (the "now it's a real episode" moment):**

> **Hand-build a render list** on a single-lane timeline — add finalized node clips in your chosen
> order (it can differ from the story, R148; pick *which shot version*'s clip, older OK) → set a
> **transition at each junction** (default hard cut; overlap zone for cross-fade/dissolve, R149/R157)
> → **▶ Render preview** → **render** to a single `out.mp4` at the project format, with each clip's
> baked **node voice** → **Export → Resolve** as plain files (EDL+FCPXML + per-node media + voice +
> chapter markers) and **subtitles**. Reopen → the render record(s) are intact.

If a fresh user can **compose** a playable, exportable episode video over the P0–P4 spine, **P5 is
done.**

---

## 2. Scope — two tracks (Episode/Export + Production hardening, R147)

P5 carries **two tracks**. **Track A (Episode/Export)** is the **MVP headline** — it ships first as
the walking skeleton and is the done-line (§1). **Track B (Production hardening)** is the set of
quality/capability items **moved from P6** (R147) to rebalance the roadmap: deeper Flux2 multi-ref,
LTXV-extend hardening, multi-LoRA stacking + usable style LoRA, and a postproc expansion.

> **Honest note on the rebalance (R147).** Track A is pure assembly/export — no models, low risk.
> **Track B is the opposite** — model-/generation-heavy, ROCm-dependent, several 🟡/🔴. So P5 is **no
> longer "the lightest phase"**; it now owns real hardening risk. The mitigation: **Track A is
> sequenced first and is independently shippable** (the episode doesn't depend on any Track B item),
> and Track B is **additive** — each item upgrades quality on the L2/L3 surfaces without touching the
> Episode workspace. Several Track B items reinforce each other (the postproc **upscaler** is the
> missing joint in the R133 LTXV-extend stack; deeper Flux2 → better assets → better LoRA stacking).

| Track | Sub-phase | Builds | Risk |
| --- | --- | --- | --- |
| **A** | **P5.1 Episode assembly** | single-lane timeline (R157), hand-built composition (R148), overlap-zone transitions, version pick, out-of-date flags | 🟡 timeline UI |
| **A** | **P5.2 Render** | stitch ordered clips + transitions → one `out.mp4` (FFmpeg encode, not a model) | 🟢 encode |
| **A** | **P5.3 Export** | file-based Resolve handoff (timeline + media + voice), subtitles, Muse pitch one-pager | 🟢 file ops |
| **B** | **P5.4 Flux2 multi-ref (deep)** | promote the P1 spike to a production multi-reference asset path | 🟡 |
| **B** | **P5.5 LTXV-extend hardening** | productionize the P3 R133 spike → native long-form as a real continuity path | 🔴 |
| **B** | **P5.6 Multi-LoRA stacking + style LoRA** | stack character + style LoRAs; make **style-LoRA training usable** (R122) | 🔴 |
| **B** | **P5.7 Postproc expansion** | the heavier L2/L3 postproc tools (upscale/relight/detail/video) — §11 | 🟡 |

**In scope:** §3 (both tracks). **Out of scope (deferred → P6):** the **effect plugin API** (R37),
**engine/DCC export to Unreal 5.7+/Blender** (R128/§6.7 — *separate* from the Resolve episode
export), **all 3D / `trellis2` / proxy-depth** (R128). Still deferred from P5 itself: **episode-level
audio / mixer / SFX / music** (post-v1, R42/R95 — done in Resolve), parallel-thread *composition*
(R29 — one clip per point, no split-screen).

---

## 3. What P5 adds to the spine

- **Episode workspace (L5):** order a **chosen clip per node** into a linear sequence with a
  **transition at every join** (R26/R29) — trim, reorder, cut, cross-fade, dissolve, fade-to-black.
  No compositing/keying/grading/multi-track VFX (R18).
- **Episode composition (R148):** the episode is a **hand-built linear sequence** the author composes
  on a single-lane timeline — **decoupled** from the graph (it can differ from the narrative), **not**
  auto-derived from it. Still one clip per point — parallel threads are alternatives you choose
  *between*, never composed together (R29).
- **Render records (multiple per project):** each `renders/<name>/` saves node order + per-node
  clip-version + per-join transitions + its own manifest; all share the **one project format**;
  **kept, never auto-deleted** (R-keep). A 9:16 short is a *separate project*, not another render.
- **Render = stitch, not generate (R9):** sequence the finalized clips, apply transitions, encode one
  `out.mp4`. **No video regeneration** in L5.
- **File-based Resolve export (R19/R28):** standard files (timeline + per-node media + the **voice**
  track) the author **imports manually** into **free Resolve** — no API, no generated Resolve project.
- **v1 audio = node-level voice only (R42/R95):** each clip carries its baked voice; the episode
  plays them in sequence. **No episode bed, mixer, SFX, or music** (those → Resolve / post-v1).
- **Production hardening (Track B, moved from P6, R147):** **deeper Flux2 multi-ref** (P1 spike →
  production), **LTXV-extend hardening** (P3 R133 spike → real long-form path), **multi-LoRA stacking
  + usable style LoRA** (R122), and a **postproc expansion** (heavier L2/L3 tools). These plug into
  the **L2/L3** surfaces (Asset Studio, Shot timeline) — **not** the Episode workspace, which stays
  stitch-only (R18). Detail in §11.

P5 reuses **P0** (queue, disk guard, workspace I/O, lineage), **P3** finalized node clips + node
versioning (the things it stitches), and **P4** the Flow graph (the path source) + Muse (synopsis /
subtitles / pitch one-pager). Track B also builds on **P1** (Flux2 spike, postproc toolkit) and
**P2** (LoRA training).

---

## 4. Data model (Render record, §3.2/§6.7)

```
renders/
└── <render>/                  # one per render config — MULTIPLE allowed, kept (never auto-deleted)
    ├── render.json            # timeline: clip order[] (hand-built, R148); per-clip ref =
    │                          #   {flow_node_id, shot_id, shot_version_id} (lean, not a copy —
    │                          #   Flow nodes aren't versioned; versioning is on the L3 shot,
    │                          #   R62/R146); per-junction transition[] (type · overlap-N ·
    │                          #   trim|freeze, R149/R157); chapter markers[] (R158); format
    │                          #   snapshot; title
    ├── manifest.json          # this render's own PipelineManifest (status/timing)
    ├── out.mp4                # the rendered episode at the project format
    ├── subtitles.srt          # (optional) from dialogue nodes (R152)
    └── export/                # file-based Resolve handoff (EDL+FCPXML + per-node media + voice + markers)
```

| Record | Where | Holds |
| --- | --- | --- |
| **Render** | `renders/<render>/render.json` | **hand-built clip order** (R148), **per-clip reference = `{flow_node_id, shot_id, shot_version_id}`** (lean, R62/R146 — Flow nodes aren't versioned; the version is the L3 shot's), **per-junction transitions** (type · overlap-N · trim/freeze, R149/R157), **chapter markers** (R158), format snapshot, title; **own manifest**; **many per project**, all share the project format |

- **References, not copies (R62):** a render **points at** a specific **shot version** (via
  `{flow_node_id, shot_id, shot_version_id}`); it doesn't freeze a copy. If that node's shot has a
  newer version than the one referenced, the list entry shows a light **"may be out of date"** flag —
  but the referenced version's clip stays usable (no block).
- **All renders share the project format** (aspect/resolution/fps/audio, set at creation) — renders
  differ only in **which nodes/versions are included and the transitions** (e.g. a tight pitch cut vs.
  an extended cut). A different aspect (9:16) is a **separate project**.
- **Lineage (R98):** the render references each clip by `{flow_node_id, shot_id, shot_version_id}`;
  the shot version already pins `asset@version` + LoRA — so an episode is fully traceable from the
  story node, through the exact shot version, down to the assets that made it.

---

## 5. Episode workspace — a single-lane timeline (R157)

The episode editor (`kb-storyboard01.md` §6.7) is a **single-video-lane timeline**, *not* a
multi-lane NLE. **Why single-lane:** R29 forbids two clips playing at once (no split-screen), so
there is **no use for multiple video lanes** — and R18 says loom is not an after-effects tool. So:
clips sit **end-to-end on one video lane**, you **drag to reorder**, and **each junction holds a
transition rendered as a draggable overlap zone** (its width = the overlap/duration). The **voice
lane** (baked, read-only) sits below.

```
┌ STAGE: EPISODE · "Act 1" — one video lane · drag clips to reorder · transition = overlap zone ─┐
│ VIDEO  ┌Arrival──┐┌The-Map───┐▟▙┌take─────┐▟▙┌End───┐    ◀ drag a clip left/right to reorder    │
│        │■ final  ││■ final   │××│■ final  │××│■ fin │    ▟▙×× = transition OVERLAP zone         │
│ VOICE  │▓ Mara ▓ ││          ││ │▓ Mara ▓ ││ │      │    (node voice, baked, read-only, v1)     │
│ junction ▸  [hard cut]     [x-fade 0.50s · trim▾]     [hard cut]   ◀ per-junction inspector      │
│              (no overlap)   (overlap: trim-content │ hold-freeze)                                 │
│ ░ post-v1: episode music bed + mixer (gain/volume/pan) — NOT in v1 (greyed) ░                    │
│ [Render preview ▶]  [Export → Resolve]  [Pitch one-pager (Muse) ▶]  [Chapters ▾]                 │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **A hard cut = zero overlap** (clips abut, default R149). A **cross-fade/dissolve = a non-zero
  overlap zone** you drag to set its duration (R149); fade-to/from-black needs no second clip.
- **Per-transition trim-vs-freeze toggle (R157, answers F1 "both"):** an overlap can either
  **trim into content** (clips slide together, ~N frames consumed — the default) **or hold/freeze**
  the boundary frame to fill the overlap (no content lost, a brief freeze). Chosen per junction.
- **Per-clip: pick *which shot version*** (R62/R146 — the node owns one shot, the shot has versions) —
  including an older one; **out-of-date flag** if that shot has a newer version (advisory, never
  blocking, R153).
- **Basic editing only (R18):** reorder + per-junction transition (type · overlap · trim/freeze).
  Nothing else — no compositing, keying, grading, multi-track. (A full NLE would violate R29/R18.)
- **The mixer row is post-v1, shown greyed** (R42/R95) — v1 has voice only.

---

## 6. Episode composition — a hand-built render list, decoupled from the graph (R148/R29)

**The episode is *not* mechanically derived from the Flow graph.** Per the author (R148), **the
finished episode can differ substantially from the narrative structure** — so the render list is
**hand-composed**: the author adds node clips in **whatever order** they choose, which may or may
not follow the graph's edges.

- **Decoupled from L4 (R148):** the Flow graph is the *narrative/branching* structure; the episode
  is a *curated linear cut*. loom does **not** auto-linearize the graph — the author **builds the
  render list manually** (add a node's clip → place it → order it). v1 has **no play-mode-capture /
  graph-derived path** (an optional "seed from a play-mode walk" is **deferred** — R148/F2, not v1).
- **One clip per point, no split-screen (R29):** still holds — the episode is a single linear
  sequence; parallel graph threads are alternatives you pick between, never composed.
- **Validation (R153):** a listed node with **no** rendered clip **blocks** the render until it has
  one; a **stale** clip (newer unrendered edits) is allowed with a **warning** (advisory, not a block).

---

## 7. Transitions — every join is a transition (R26/R29)

Pixel-identical seams live **only within a node** (P3 §7); the **episode list always uses a
transition** between adjacent clips (R26). v1 set:

| Transition | Overlap | Use |
| --- | --- | --- |
| **Cut** (default, R149) | **zero** | hard join — the cleanest; no frames consumed |
| **Cross-fade / dissolve** | **needs an overlap window** | blend over N frames (e.g. 0.5 s) of *both* clips |
| **Fade-to-black / from-black** | from/to black (no second clip) | scene breaks, episode open/close |

- **Cut is the default (R149).** Non-cut transitions (cross-fade/dissolve) **require an overlap
  window** — the author's point: to blend, the encoder needs N frames from clip A's tail *and* N
  from clip B's head playing together. So a cross-fade **consumes ~N frames of content from each
  adjacent clip's boundary** (loom clips are exact-length/finalized — there are no spare "handle"
  frames). A hard cut consumes nothing — hence the default.
- **The overlap is set on the timeline** (§5 — drag the junction's overlap zone) and offers **both**
  ways to take it (R157, F1): **trim-into-content** (default — lose ~N frames of action) **or
  hold/freeze** the boundary frame (no loss, brief freeze), per junction.
- Each join stores **type + overlap-N + trim|freeze** in `render.json`. No other VFX (R18).
- Transitions are rendered at encode time (FFmpeg `xfade`-class); they are **not** model generation.

---

## 8. Render — stitch, not generate (R9)

- **Inputs:** the ordered finalized clips (each with its baked node voice, P3 Finalize) + per-join
  transitions. **Outputs:** one `out.mp4` at the project format + an optional `subtitles.srt`.
- **It's an encode, not a model:** FFmpeg-class concat + transition rendering + audio mux. **No VRAM
  tenant** — so it is *not* subject to the exclusive-AI rule, but it **still runs as a queue job**
  (progress/cancel/serialization, §12). On this single-GPU rig, prefer CPU or light HW-encode; it
  won't contend with model VRAM (render-job wiring, §12).
- **Render preview** = a fast, possibly lower-res pass to check pacing/transitions before the full
  encode.
- **The render `out.mp4` is modest** — an encoded episode runs **tens to a few hundred MB for a
  30-min cut** (not a few MB, but **tiny next to the PNG masters' hundreds of GB**), so **renders
  barely move the disk budget**; **keep all renders** (R-keep), manual delete only. **But the project's disk budget is
  now dominated by the P3 frame-sequence node masters (R161 — lossless PNG, ~30–50× an mp4, one per
  version, never pruned), not by renders/drafts/temp** — so the cap pressure lives in P3, not here
  (see `kb-loom-p3.md` §14).

---

## 9. Export — file-based to free Resolve (R19/R28)

loom **exports standard files** the author imports manually into **free Resolve** — **no API, no
generated Resolve project** (R19):

- **The watchable `out.mp4`** (flattened episode).
- **An editorial timeline — `EDL` + `FCPXML` (R151)** **+ per-node media + the voice track**, so the
  author can **re-edit in Resolve** rather than re-render in loom. **FCPXML is the richer/primary**
  format (carries the timeline + transitions + markers); **EDL is the simple, universal fallback**.
- **Per-clip media = author's choice, lossy only at this final step (R161).** Each clip's source is
  its **lossless PNG-seq master** (P3, R161). At export the author picks per the handoff need:
  **(a) the image-sequence directly** (max fidelity / archival — Resolve imports PNG sequences
  natively), or **(b) a visually-lossless encode** (ProRes/DNxHR or CRF-low — one tidy file per clip
  for editorial). Either way the **master stays lossless**; encoding is the only lossy step and it's
  opt-in. *(The flattened `out.mp4` above remains a convenience preview, separate from the editorial
  media.)*
- **⚠ Free-Resolve import compatibility is a build-time validation gate (R151, author's concern).**
  The exported FCPXML/EDL + collected media must **import cleanly into the latest *free* Resolve**
  (nothing Studio-only, R28) — this is **tested during the build**, not assumed; relative/collected
  media paths so the import doesn't break on move (§18 gap). **FCPXML is primary, EDL the fallback
  (R151, ratified F3).**
- **Subtitles** (`.srt`, R152) generated from the **dialogue nodes' authored text** — cheap, since
  the text already lives in the Flow nodes.
- **Chapter markers (R158, F4):** Muse's chapter markers (§10) export **into the FCPXML timeline**
  (Resolve reads markers) **and as a sidecar list** — so the act/scene structure survives the handoff.
- **v1 = one voice/dialogue track only.** When SFX/music exist (post-v1), export them as **separate
  stems** (dialogue/SFX/music, R-stems); v1 has just the voice (R95).

---

## 10. Muse in L5 (R141/R142)

Muse's EPISODE/EXPORT uses (`kb-storyboard01.md` §7.1), all under the AI-execution rules:

- **Synopsis / logline / chapter markers**, **subtitle text** from dialogue nodes, and a **pitch
  one-pager** from the bible + the composed episode.
- **Execution (R141/R142):** these are **interactive (idle-only)** when lightweight, or a **queued AI
  job** for a batch (e.g. one-pager generation) — **never concurrent with a render or any GPU job**.
  The render itself is **not** an AI job (it's an encode), but the queue still serializes them.

---

## 11. Track B — production hardening (moved from P6, R147)

Four quality/capability items moved from P6 to rebalance the roadmap. **All plug into the existing
L2 (Asset Studio) / L3 (Shot timeline) surfaces — never the Episode/L5 workspace, which stays
stitch-only (R18).** All are **model jobs → queued (R141)**, one heavy model at a time, **ROCm-gated**
(like P2-0), license-gated (`_license_gate.py`). They're **additive**: Track A (episode) ships
without any of them.

### 11.1 Deeper Flux2 multi-reference (P1 spike → production)

- P1 ran a **Flux2 multi-ref research spike** (non-blocking, `kb-storyboard01.md` §8.1 item 2). P5
  promotes it to a **production multi-reference asset path**: insert/regenerate a character
  consistently from **several** reference images — a stronger complement to PuLID + img2img in the
  Stage-B loop, and cleaner character insertion in the L3 compositor.
- **Plugs into:** L2 Stage-B expansion (better on-model datasets → better LoRAs) and L3 compositing.
- **Risk 🟡:** productionizing a spike = reliability + 16 GB VRAM headroom; ROCm-gated.

### 11.2 LTXV-extend hardening (P3 R133 spike → real long-form path)

- P3 **spiked** LTXV-extend as continuity strategy **(d)** (R133) — native long video conditioned on
  prior frames, **no seam to weld**. P5 **hardens** it into a production path **only if the P3 spike
  proved out on-rig (gated, R156)** — else this is skipped and **P3 chain+weld stays the default**.
  When hardened: the **LTXV-extend spine → optional wan2-animate quality/consistency drive pass →
  upscaler** stack.
- **This is where the R133 stack's two unproven joints (P3-14) get productionized:** the **upscaler**
  (from §11.4) and **wan2-animate driving from a full LTXV clip**.
- **Plugs into:** L3 take timeline — becomes the **default** continuity path if it proved out on-rig
  (else P3's chain+weld remains default; this is purely additive).
- **Risk 🔴:** depends on the P3 spike outcome; VRAM-heavy long-form gen on 16 GB.

### 11.3 Multi-LoRA stacking + usable style LoRA (R122 → P5)

- P2 trains **character** LoRAs; **style** LoRA was **declared-only** (R122), waiting for multi-LoRA
  stacking to make it usable. P5 builds **stacking**: load **multiple LoRAs at inference** (character
  + style) with per-LoRA weights — which **makes style-LoRA training usable** (amends R122: now P5).
- **Plugs into:** L2/L3 generation (apply a character LoRA *and* a style LoRA together); P2's
  style-LoRA **training** path becomes a real deliverable here.
- **Risk 🔴:** stacking interactions (identity vs style bleed), VRAM with 2+ adapters, training a
  second LoRA type.

### 11.4 Postproc expansion (the heavier L2/L3 tools — *detailed*)

P1 shipped **image** postproc (matting/masking/identity/anatomy/face-restore/Real-ESRGAN upscale);
P3 shipped **video** postproc (lip-sync/video-masking/continuity-helpers). P5 adds the **heavier /
deferred** tools cataloged in `kb-postproc-img.md` (§8.3) but not yet built:

| Tool | Model(s) | Domain | loom use |
| --- | --- | --- | --- |
| **Heavy image upscale** | **SUPIR** (heavy) | image | promote a final keyframe/still to hi-res — the quality tier beyond Real-ESRGAN |
| **Video upscale** | Real-ESRGAN-video / video-SR | video | upscale finished clips **and the LTXV-extend draft — the R133 stack joint (§11.2)** ← *highest-value link* |
| **Relight / color-match** | **IC-Light** · `color-matcher` | image | match a composited layer to its plate (cataloged in P1, not built) |
| **Anatomy / face auto-detail** | **ADetailer** | image | automatic face/hand detailing beyond the manual HandRefiner |
| **Video face-restore** | CodeFormer / GFPGAN over frames | video | clean generated-video faces across a clip |
| *(optional)* **Video denoise / artifact cleanup** | video denoiser | video | tidy generated-video artifacts before Finalize |

- **How it fits the tool — same shape as every other postproc (no new pattern):**
  - Each is a **subprocess-isolated** `src/pipeline/postproc/<tool>/run_pipeline.py` adapter, **one
    heavy model at a time**, **license-gated** (`_license_gate.py`; SUPIR/IC-Light are
    research/non-commercial — prefer permissive where possible).
  - Surfaced as **queueable per-asset (L2) / per-clip (L3) actions** — exactly like the P1/P3
    postproc, on the *existing* Asset Studio + Shot timeline surfaces. **Not** added to the Episode
    workspace (R18 — loom stays a stitch tool, not an AE tool).
  - Every tool runs as a **queue job (R141)** — GPU model, exclusive; **ROCm can-run check** per tool;
    some may need their **own env** (component manifest, R31).
- **Priority (R155):** the **upscaler (image SUPIR + video-SR) first** — it's the missing joint in the
  R133 LTXV-extend stack (§11.2). Relight/ADetailer/video-face-restore follow; denoise is optional.

---

## 12. Build dependencies (`kb-storyboard01.md` §8.1)

- **FFmpeg-class encode/stitch** with transition rendering (`xfade`, fades) + audio mux — the render
  engine. Likely a small `src/pipeline/`-style tool or an orchestrator helper, wrapped as a **render
  job** on the P0 queue.
- **Editorial export** writer (**EDL + FCPXML** + media collection + voice + **chapter markers**,
  R151/R158) — plain file ops; validated against free Resolve.
- **Subtitle writer** (`.srt` from dialogue-node text + the clip durations).
- **Single-lane timeline editor** (R157): drag-reorder clips, **overlap-zone transitions** (type ·
  overlap · **trim/freeze**), per-clip version pick, out-of-date flags, manual composition (R148).
- **Track B (§11):** Flux2 multi-ref production wiring (§8.1 item 2); LTXV-extend hardening (§8.1
  item 3); a **multi-LoRA stacking** loader (2+ adapters + weights) + the **style-LoRA training**
  path (extends P2); new **postproc adapters** (`src/pipeline/postproc/<tool>/run_pipeline.py`:
  SUPIR, video-SR, IC-Light, ADetailer, video face-restore) — each ROCm/license-gated.

---

## 13. Disk & VRAM

- **Disk:** Track A is the lightest — `out.mp4` + exports are **small** (R83). **Track B is heavier**
  — Flux2/LTXV-extend/LoRA-training/upscale produce drafts + temp (like P1/P2/P3). The two-threshold
  guard (R96) + per-project cap apply throughout.
- **VRAM:** Track A render is **encode, not a model** — no VRAM tenant. **Track B is VRAM-heavy** and
  obeys the single-GPU rule — every Track B model job is a **queued, exclusive AI/GPU job** (R141),
  one heavy model at a time, ROCm-gated. Muse uses obey the on-demand/idle rules (R141/R142).

---

## 14. P5 milestones — walking skeleton first

### P5.1 — Episode assembly

1. **M1 — single-lane timeline skeleton (walking skeleton).** Lay **2–3 finalized P3 clips** on one
   video lane (drag-reorder) → **hard-cut** joins → `render.json` (lean version refs) → persist/reopen.
   *(First payoff: a saved episode timeline, R157.)*
2. **M2 — render + preview → done-line.** Stitch the ordered clips (cuts only) → one `out.mp4` at the
   project format with baked voice; a fast **preview** pass. *(P5 walking-skeleton done-line: a
   watchable episode from the spine.)*
3. **M3 — overlap-zone transitions + version pick + flags.** Per-junction cut/cross-fade/dissolve/fade
   as a **draggable overlap zone** with the **trim/freeze toggle** (R157/R149); pick which shot version
   clip; advisory stale flags.
4. **M4 — episode composition (hand-built, decoupled).** Manually add/order node clips into the
   render list — **not** graph-derived (R148); block-missing / warn-stale validation (R153).

### P5.2 / P5.3 — Export

5. **M5 — Resolve export + subtitles + markers.** File-based editorial export (**EDL + FCPXML** +
   per-node media + voice + **chapter markers**, R151/R158) + `subtitles.srt`; validated against free
   Resolve.
6. **M6 — Muse pitch one-pager + synopsis/logline** (idle-only/queued, R141/R142).

### Track A done-line

7. **M7 — episode acceptance (the MVP headline).** Hand-built render list (R148) → overlap-zone
   transitions → `out.mp4` (project format, voiced) → Resolve export (EDL+FCPXML + media + voice +
   markers) + subtitles, with lineage to `{flow_node_id, shot_version_id}`/`asset@version`. Multiple render records
   coexist; all kept on reopen (§1).
   **M7 is the v1 ALPHA GATE (R167)** — when it's green the product is feature-complete for v1.
   **Track B below is post-alpha hardening that lives in P5 but does NOT gate v1.**

### Track B — production hardening (post-alpha; each additive; does NOT gate v1 — do after the episode ships, R167)

8. **M8 — postproc upscaler first** (§11.4) — image **SUPIR** + **video-SR** as queued postproc
   adapters; this unblocks the R133 stack joint. *(Highest-value Track B item; ROCm-gated.)*
9. **M9 — LTXV-extend hardening** (§11.2) — **gated on the P3 spike (R133) proving out on-rig
   (R156)**; if so, productionize → native long-form path (LTXV spine → wan2-animate drive pass →
   upscaler) as the default continuity path; **if not, skip — P3 chain+weld stays default**.
10. **M10 — multi-LoRA stacking + style LoRA** (§11.3) — 2+ adapter loader + weights; the style-LoRA
    **training** path (extends P2) → character+style applied together.
11. **M11 — deeper Flux2 multi-ref** (§11.4/§11.1) — production multi-reference asset path; remaining
    postproc tools (IC-Light/ADetailer/video face-restore).

---

## 15. Risks & guardrails

1. **Scope creep into an editor.** **Guardrail:** **basic editing only** (R18) — trim/reorder + the
   transition set; heavy finishing is explicitly Resolve's job. Resist compositing/grading requests.
2. **Transition rendering quality.** **Guardrail:** lean on FFmpeg `xfade`/fades (proven); the
   pixel-identical concern is a P3 *within-node* matter, not here (R26).
3. **Render references go stale.** **Guardrail:** renders **reference** shot versions (lean); the
   out-of-date flag surfaces staleness; a missing clip blocks only that render until rendered (R153).
4. **Don't regress P0–P4.** **Guardrail:** P5 is additive; a project with no render record is fully
   valid; the Flow/assets/shots are untouched.
5. **Episode-audio temptation.** **Guardrail:** v1 is **voice-only** (R42/R95) — music/SFX/mixer stay
   greyed/post-v1; a spanning score is done in Resolve.
6. **⚠ Track B raises P5's risk profile (R147).** Track B is model-/ROCm-heavy and dilutes the clean
   "lightest phase" identity. **Guardrails:** (a) **Track A ships first and standalone** — the
   episode never depends on a Track B item; (b) every Track B model job obeys the **single-GPU queue
   rule** (R141); (c) each Track B item has a **ROCm can-run gate** (like P2-0) and is **additive** —
   if one (e.g. LTXV-extend) doesn't pan out on-rig, the prior path (P3 chain+weld) still stands.
7. **Postproc stays on L2/L3, never L5 (R18).** **Guardrail:** the postproc expansion adds tools to
   the Asset Studio / Shot timeline only; the **Episode workspace remains stitch-only** — no
   grading/compositing creeps into render.

---

## 16. Out of scope (defer)

- **Episode-level audio / mixer / SFX / music / stems** → post-v1 / Resolve (R42/R95; mixer mockup is
  greyed). When it lands, gain/volume/pan only (R34), separate stems (R-stems).
- **Effect plugin API (Python/JS/TS)** → P6 (R37).
- **Engine/DCC export — assets *and* graph to Unreal 5.7+/Blender** → P6 (this is *separate* from the
  Resolve episode export, which is in P5).
- **All 3D / depth proxies** → P6 (R128).
- **Parallel-thread composition / split-screen / intercut** → not planned (R29 — one clip per point).

---

## 17. Resolved (round 21 → R148–R156; round 22 → R157–R158)

**Resolved (round 21):**

| # | Decision |
| --- | --- |
| R148 | **Episode = hand-built render list, *decoupled* from the Flow graph** — the author composes the order manually; the finished episode can differ substantially from the narrative; **no graph-derived/play-mode-capture path in v1** (§6). |
| R149 | **Default transition = hard cut** (zero-overlap). Non-cut transitions (cross-fade/dissolve) **require an overlap window** that consumes ~N frames from each adjacent clip's boundary (loom clips are exact-length, no handles) (§7). |
| R150 | **Render runs as a queue job** (encode type — progress/cancel/serialization — not an AI tenant) (§8/§12). |
| R151 | **Editorial export = EDL + FCPXML + per-node media + voice**; **FCPXML primary, EDL fallback**; **free-Resolve import compatibility is a build-time validation gate** (author's concern) (§9). |
| R152 | **Subtitles `.srt`** from dialogue-node text, v1 (§9). |
| R153 | **Render gate: block on a node with *no* clip; warn on a stale clip** (advisory) (§6). |
| R154 | **Pitch one-pager = text + a few key stills** from the chosen clips (§10). |
| R155 | **Postproc priority = upscaler first** (image SUPIR + video-SR — the R133-stack joint), then IC-Light/ADetailer/video-face-restore; denoise optional (§11.4). |
| R156 | **LTXV-extend hardening is gated on the P3 spike (R133) proving out on-rig**; else skip and P3 chain+weld stays default (§11.2). *(Track A ships before Track B — ratifies R147.)* |

**Resolved (round 22):**

| # | Decision |
| --- | --- |
| R157 | **Episode editor = a single-video-lane timeline** (F1) — clips end-to-end, **drag to reorder**; **each junction = a transition rendered as a draggable overlap zone** (width = duration); **per-transition toggle: trim-into-content (default) *or* hold/freeze** (F1 "both"). **Not a multi-lane NLE** — R29 (one clip per point) makes extra lanes pointless, R18 keeps it basic. Voice lane below (baked, read-only) (§5/§7). |
| R158 | **Chapter markers exported** (F4) — Muse's chapter markers (§10) go **into the FCPXML timeline** (Resolve reads them) **+ a sidecar list** (§9). |

*Round-22 ratifications:* **F2** — render list is **purely manual in v1**, optional "seed from a
play-mode walk" deferred (confirms R148). **F3** — **FCPXML primary + EDL fallback**, validated
against free Resolve at build (confirms R151).

**Open:** none for P5. R148–R158 settle rounds 21–22; the §18 WBS reflects them.

---

## 18. Work-package breakdown (WBS) — what P5 actually contains

*Rough solo-dev sizing: **S** ≤1 d · **M** 2–4 d · **L** 1–2 wk · **XL** 3 wk+. Risk: 🟢 routine ·
🟡 unknowns · 🔴 R&D. Maps to the §14 milestones.*

| WP | Work package | Maps to | Size | Risk |
| --- | --- | --- | --- | --- |
| P5-1 | **Single-lane timeline editor (R157)** — one video lane, drag-reorder, per-clip version pick + out-of-date flag, voice lane (read-only) | M1/M3 | L | 🟡 |
| P5-2 | **Render record** (`render.json` + manifest; lean version refs; multiple per project; kept) | M1 | S | 🟢 |
| P5-3 | **Stitch/encode engine** — FFmpeg-class concat + audio mux → `out.mp4` at project format | M2 | M | 🟡 |
| P5-4 | **Render preview** (fast/low-res pacing pass) | M2 | S | 🟢 |
| P5-5 | **Overlap-zone transitions (R149/R157)** — cut/cross-fade/dissolve/fade as a draggable overlap + **trim/freeze toggle**, rendered at encode (`xfade`) | M3 | M | 🟡 |
| P5-6 | **Episode composition** — manual add/order node clips into the render list, **decoupled from the graph** (R148); block-missing/warn-stale (R153) | M4 | M | 🟡 |
| P5-7 | **Render-as-queue-job** wiring (progress/cancel; encode job type, not an AI tenant) | M2 | S | 🟢 |
| P5-8 | **Editorial export** (**EDL + FCPXML** + per-node media + voice + **chapter markers**, R151/R158; file-based, **free-Resolve compat gate**) | M5 | M | 🟡 |
| P5-9 | **Subtitles** (`.srt` from dialogue-node text + clip timings) | M5 | S | 🟢 |
| P5-10 | **Muse L5 uses** — synopsis/logline/**chapter markers** + **pitch one-pager (text+stills, R154)** (idle-only/queued) | M6 | M | 🟡 |
| P5-11 | **Track A acceptance** (§1 / M7) — the episode MVP done-line | M7 | S | 🟢 |
| | **— Track B: production hardening (moved from P6, R147) —** | | | |
| P5-12 | **Postproc: image upscale (SUPIR)** adapter — queued, ROCm/license-gated | M8 | M | 🟡 |
| P5-13 | **Postproc: video upscale (video-SR)** adapter — the R133-stack joint (§11.2) | M8 | M | 🟡 |
| P5-14 | **LTXV-extend hardening** — productionize the P3 R133 spike → native long-form path | M9 | L | 🔴 |
| P5-15 | **Multi-LoRA stacking** loader (2+ adapters + per-LoRA weights, char + style) | M10 | L | 🔴 |
| P5-16 | **Style-LoRA training path** (extends P2; usable now that stacking exists, R122) | M10 | M | 🔴 |
| P5-17 | **Deeper Flux2 multi-ref** — P1 spike → production multi-reference asset path | M11 | M | 🟡 |
| P5-18 | **Postproc: relight/color-match (IC-Light), ADetailer, video face-restore** adapters | M11 | M | 🟡 |

**Rollup:** **~18 WP across two tracks.** **Track A (P5-1…P5-11, ~11 WP)** is the MVP headline — the
**single-lane timeline editor (P5-1, now the largest Track A item, R157)** + FFmpeg encode +
plain-file export, mostly 🟢/🟡. **Track B (P5-12…P5-18, ~7 WP)** is the moved-in
hardening — **model-/ROCm-heavy, the real risk (3× 🔴)**: LTXV-extend hardening (P5-14) and multi-LoRA
+ style (P5-15/16). The upscaler (P5-12/13) is the highest-value Track B item (unblocks the R133
stack). **Track A is independently shippable; Track B is additive.**

**⚠ Gaps to watch:**
- **Transition + voice timing across a join** — a cross-fade overlaps two clips; confirm each clip's baked voice doesn't clip awkwardly across the blend (part of P5-5).
- **Editorial export media paths** — the exported timeline must reference media with paths that survive the move to Resolve (relative/collected media), or the import breaks (part of P5-8).
- **Render-list ↔ Flow drift** — if Flow nodes are deleted/re-bound after a render is authored, the render's references need a clear "missing node" state (ties to P5-1 out-of-date flags + R146 ownership).

---

## Source / traceability

- Decisions: `kb-storyboard01.md` §10.0 — **R9** (episode = stitch many clips, not a monolith),
  **R18** (basic editing only), **R19/R28** (file-based free-Resolve, no API), **R26/R35** (episode
  joins always transition; pixel-identical is within-node only), **R29** (one clip per point, no
  split-screen), **R42/R95** (v1 audio = node voice only; episode audio deferred), **R62** (node
  versioning — pick which version), **R83** (renders small), **R96** (disk hard stop), **R98**
  (lineage), **R141/R142** (AI execution), **R146** (shot ownership). **R37** (effect plugin API) +
  **R128** (3D) + engine export are **P6**, not P5.
- Track B decisions (§11): **R147** (rebalance — Flux2-deep / LTXV-extend-harden / multi-LoRA+style /
  postproc-expansion moved P6 → P5); **R122** (style LoRA — *amended: now P5, was P6*); **R133**
  (LTXV-extend — spike P3, *hardening amended: now P5, was P6*); **R37** plugin API + engine export
  stay P6; `kb-storyboard01.md` §8.1 items 2 (Flux2) & 3 (LTXV extend) now land in P5.
- Bodies: `kb-storyboard01.md` **§6.7** (Episode/Export workspace + mockup), **§3.2** (Render record),
  **§8.3** (postproc catalog), `kb-postproc-img.md` (postproc models/licenses).
- Spine reused: `kb-loom-p0.md` (queue, disk guard, workspace I/O, lineage), `kb-loom-p1.md` (Flux2
  spike, postproc toolkit), `kb-loom-p2.md` (LoRA training), `kb-loom-p3.md` (finalized node clips +
  shot/take versioning + the LTXV-extend spike R133), `kb-loom-p4.md` (Flow graph = the path source; Muse).
- Engine: FFmpeg (encode/transitions/mux); `kb-pipelines01.md`; `src/pipeline/postproc/` (adapter shape).
