# Loreweave Studio — P3 build spec (Shots: compositor, animation, audio, continuity)

Created: 2026-06-02
Status: spec (not yet implemented; depends on P0 + P1 + P2)
Parent: [`kb-storyboard01.md`](kb-storyboard01.md) (overview + decision record R1–R123)
Predecessors: [`kb-loom-p0.md`](kb-loom-p0.md) · [`kb-loom-p1.md`](kb-loom-p1.md) · [`kb-loom-p2.md`](kb-loom-p2.md)
Engine: [`kb-pipelines01.md`](kb-pipelines01.md) (animation loop, the L3 backend) · video: [`kb-wan2.md`](kb-wan2.md), [`kb-hunyuan.md`](kb-hunyuan.md), [`kb-ltx09.md`](kb-ltx09.md) · 3D: [`kb-trellis2.md`](kb-trellis2.md) · postproc: [`kb-postproc-img.md`](kb-postproc-img.md)

P3 is the **L3 Shots layer** — where reproducible characters (P1+P2) become **moving, voiced
clips**. It builds the **compositor**, the **take timeline** (sketch→drive animation loop), the
**frame-accurate within-node continuity** R&D, **node versioning + Finalize**, **node-level voice
audio (two TTS tiers + lip-sync)**, **video masking**, and
brings the **creative SLM (Muse) online as a continuity director**. (**3D / depth proxies are
*not* in P3** — deferred to P6, R128.) Every decision traces to a
resolved item (`Rnn`) in `kb-storyboard01.md` §10.0.

> ⚠⚠ **P3 is by far the biggest, riskiest phase.** It bundles the make-or-break **frame-accurate
> continuity R&D**, heavy **video generation on 16 GB ROCm**, **audio/voice/lip-sync** (new media
> axis + likely a new env), and the **first creative-SLM** use. **Strong recommendation: build in
> internal sub-phases P3.1–P3.3** (§2, kept one doc per R125) and **spike the continuity R&D first**
> (§7) — it can fail, and the whole take-timeline design hinges on it. (3D/depth proxies → P6, R128.)

---

## 1. Purpose & the P3 done-line

**Purpose:** turn an asset (P1 profile + P2 LoRA) into a **finished node clip** — composed,
animated past the ~5 s single-clip cap via frame-continuous chaining, voiced, and Finalized to the
project format.

**P3 done-line:**

> Pin a P2-trained character (`asset@version`) → **compose a start keyframe** from layers
> (character + prop + plate) → in the **take timeline**, generate motion via **sketch tier (`ltxv`
> i2v ×N → pick)** then **drive tier (`wan2 animate`)**, **chaining segments with frame-accurate
> continuity** to exceed 5 s → add **node-level voice** (imported or TTS) with **lip-sync** →
> **Finalize** the node clip (resolution/fps/aspect/audio-format gate). Result: **one node with a
> single finalized, voiced clip** — one playable file, internally 1..N continuous segments joined at
> Finalize (R166) — recorded with lineage.

If a P2 character can be animated into a >5 s seamless, voiced, finalized node clip, P3's core is done.

---

## 2. Scope — one doc, internal sub-phases (don't fracture, R125)

P3 stays a **single KB doc** (author, round-17 answer 1 — fractured docs invite drift). It is still
built in **internal sub-phases** (each independently demoable), just tracked here together. **3D /
depth proxies are deferred to P6** (round-17 answer 5, R128) — with no depth-proxy or engine-export
driver yet, there's nothing pulling 3D into P3, which trims the phase:

| Sub-phase | Delivers | Risk |
| --- | --- | --- |
| **P3.1 Animation core** | compositor, take timeline, sketch+drive tiers, **frame-accurate continuity + seam-welding R&D**, node versioning + Finalize | **highest** (continuity is unsolved) |
| **P3.2 Voice/audio** | node-level voice: **two TTS tiers + lip-sync (2–3 models)**, mono→master, pannable | medium (new media + env) |
| **P3.3 Muse continuity director** | creative **SLM online**; shot-state JSON; next-segment continuity prompts | medium (first creative SLM) |

*(Removed: the former P3.3 "3D / depth proxies" → **P6**, R128.)*

**In P3:**

- **L3 compositor** — build the start keyframe from asset layers (Layered Asset Strategy, §5).
- **Take timeline** — sketch tier (`ltxv` i2v) → drive tier (`wan2 animate`); the `take_001` shape
  from `kb-pipelines01.md`.
- **⚠ Frame-accurate within-node continuity + seam-welding** (R17/R26/R35/R124) — lossless
  last-frame chaining, or **welding (interpolation/optical-flow/v2v)** over the seam; the **only**
  place pixel-identical is even attempted; episode joins (P5) use transitions.
- **Node versioning + Finalize** (R62, R30/R36) — node edits → node versions, each its own clip;
  rendering commits a version; out-of-date warning; Finalize gates res/fps/aspect/audio-format.
- **Node-level voice audio only** (R95) — two TTS tiers (R41) + **lip-sync (2–3 models, R127)**; mono
  up-sampled to master, pannable (R77); user-supplied samples, **no recording** (R84). **No episode
  audio/SFX/music** (P5+).
- **Video masking** (SAM2-video) — per-clip region edit (§8.3).
- **Muse continuity director** — SLM online (on-demand tenant), shot-state + next-segment prompts.

**Out of P3** (later phases):

- **3D / depth proxies** (`trellis2` GLBs, proxy-miniature depth route) → **P6** (R128 — no driver
  for 3D in P3 yet).
- **Flow / Muse chat+agent** → P4. **Episode/Render** (sequence nodes, transitions, export) → P5.
- **Episode-level audio / SFX / music / mixer** → P5+ (P3 is node-level voice only, R95).
- **The VLM online + project-wide VLM context** → P4 (R116).
- **LTXV keyframes/extend** — **spiked in P3** as continuity strategy (d) (R133, §7d/§19 P3-1/P3-14);
  only its **hardening** is deferred — to **P5 Track B (R147, moved from P6; gated on the P3 spike by
  R156)**, not P6. P3's *shipping* path stays **first-frame I2V chaining + welding** (works today);
  extend becomes the default only if the M1 spike proves it on-rig.

---

## 3. What P3 adds to the spine

```
new ADAPTERS (P0 contract + 1-page check):
  ltxv (i2v)        sketch tier (cheap motion drafts) + frame extract
  wan2 (animate)    drive tier — identity-locked motion (driving clip + character image)
  hunyuan (i2v)     drive tier — faster, less identity-locked   ── BOTH user-selectable (R126)
  postproc (video): lip-sync (2–3 talking-head models, R127) · sam2-video (masking) ·
                    seam-welder (RIFE/FILM interpolation · optical-flow · v2v bridge — §7) ·
                    frame-continuity helpers (lossless last-frame extract, first-frame lock)
  tts: draft (Piper/Kokoro) · cloning (XTTS/F5)   — audio generation
  (trellis2 / 3D / depth → P6, R128)

SLM online (was stubbed since P0): creative SLM as CONTINUITY DIRECTOR (shot-state + prompts).
  (VLM stays deferred to P4, R116.)

new RECORDS / fields:
  Shot (shots/<scene>/<shot>/shot.json)   — pins asset@version; node versions; segments[]; camera
  node version                            — each owns a clip; finalized?; out-of-date flag (R62)
  segment                                 — start keyframe, drafts[], selected draft, final clip,
                                            extracted-last-frame, continuity prompt-state
  voice                                   — per node: source (import/TTS-tier), sample, lip-sync ref
                                            + timecoded dialogue cues (R132)
  (depth-proxy record → P6, R128 — not in P3)
```

P3 reuses the P0 queue/adapter contract/disk guard, P1 assets/versioning, and P2 LoRAs (the
character is generated **with its trained LoRA**, optionally the **identity anchor**).

---

## 4. Data model (the `take_001` shape, §3.1; node versioning R62)

```
shots/<scene>/<shot>/
├── shot.json              # scene, shot id, pinned asset@version refs, camera, node-version list,
│                          #   active version, finalized?, out_of_date flag
├── versions/<nv>/         # one NODE VERSION (edit state) → owns ONE clip (R62)
│   ├── node_version.json   # settings snapshot, finalized?, derived_from
│   ├── keyframes/*.png      # composed start keyframes (per segment)
│   ├── segments/seg_NN/
│   │   ├── drafts/*.mp4      # sketch-tier candidates (ltxv)
│   │   ├── selected.mp4       # picked draft
│   │   ├── last_frame.png     # exact extracted last frame → next segment's first frame
│   │   └── final.mp4          # drive-tier output (wan animate)
│   ├── voice/                # imported or TTS voice clip(s) (mono→master), lip-sync source
│   ├── clip_frames/*.png     # ⟵ LOSSLESS PNG-sequence MASTER of this version's clip (R161) —
│   │                         #   kept for EVERY node version, never auto-pruned
│   └── clip_proxy.mp4        # cheap lossy proxy beside the master — UI playback/scrubbing (R161)
└── (shot-level shared refs)            # depth-proxy passes → P6 (R128), not P3
```

**Node versioning (R62):** editing a node's settings creates a **new node version**; **rendering a
quality clip commits** that version; the episode list (P5) picks a node version's clip; a node with
**unrendered edits** shows an **"out of date"** warning (not a block). Each node version owns **one
clip** (a chain of segments). **Finalize** locks per R30/R36/R119 (Saved vs Finalized applies to
nodes too — a node version is Saved when its clip is rendered, Finalized when locked).

**Clip storage = lossless frame-sequence master, not mp4 (R161).** A video pipeline decodes
latents → an **RGB frame tensor** → *then* encodes mp4; loom intercepts one step earlier and writes
the **decoded frames as a lossless PNG sequence** (`clip_frames/`) — the canonical, frame-accurate
master. PNG-8 sRGB is lossless against the model's 8-bit output (EXR/16-bit is post-v1, no benefit
now). This also **unifies with the §7 continuity work**: seam-welding / optical-flow / v2v and any
**batch i2i re-pass read/write the frame master directly**, with no lossy codec round-trip per pass.
The master + a small **`clip_proxy.mp4`** (for smooth UI playback — the master is for
editing/i2i/export, the proxy is for scrubbing) are kept for **every node version, never
auto-pruned** (author call: masters/proxies are costly to recreate, so recreation cost outweighs
disk cost). A PNG sequence is **~30–50× larger than mp4**, so keeping all versions means the
**project-cap + disk-guard (R96)** and **manual file management (R80)** carry the consequence —
expect to **raise the per-project cap** on a full episode; loom will warn/hard-stop at the cap
thresholds rather than silently delete a master. At **L5 export the master is read directly**
and the author **picks image-sequence *or* a visually-lossless encode** for Resolve (lossy only at
that final, deliberate step — §9 / `kb-loom-p5.md`).

---

## 5. The compositor (Layered Asset Strategy → start keyframe)

Build the segment's **start keyframe** by compositing reusable layers (`kb-pipelines01.md`
"Layered Asset Strategy"):

- **Character** — generated **with its P2 LoRA** (+ optional anchor, R82) at the pinned
  `asset@version`; cut out via **BiRefNet matting** (P1 postproc) for an alpha layer.
- **Props** — isolated prop sheets (P1) composited in.
- **Background plate** — a scene asset (P1).
- **Compose → flatten → optional inpaint/polish** on seams → the segment's start keyframe.
- **Camera intent** recorded (med/dolly/etc.) for the continuity prompt. *(A depth proxy could
  consume it later, but that route is P6, R128.)*

The compositor needs explicit **transforms/masks/blend/z-order/source-variant IDs/output
resolution** in `node_version.json` (Codex), not just a flattened image — so a keyframe is
reproducible and editable.

---

## 6. Take timeline + animation loop (`kb-pipelines01.md` engine)

Two tiers, per the engine spec:

- **Sketch tier (cheap, search motion):** `ltxv i2v` over **N seeds** from the start keyframe → a
  **draft grid**; pick the draft whose motion/camera/timing is closest. (Optional **motion-prompt
  trial**, §8.1 item 5: run N prompts/seeds → recipe for the drive tier.)
- **Drive tier (final) — user picks the model (R126):** both available, the author chooses per
  segment —
  - **`wan2 animate`** — identity-locked motion from the selected draft (driving) + the character's
    canonical image; `--retarget` for proportions; **higher fidelity, slow (≈2 h/720p)**;
  - **`hunyuan i2v`** — **faster, less identity-locked** quality tier.
- **Chain segments** (§7) — pixel-identical or **welded** — to exceed the ~5 s single-clip cap.

`prepare-first-frame` util (§8.1 item 4: resize/crop to project aspect + manifest) feeds both tiers
and the chaining. The whole loop is the **keyframe/movie orchestrator** (§8.1 item 7 — "literally
the L3 backend").

---

## 7. ⚠ Frame-accurate within-node continuity — the make-or-break R&D (R17/R26/R35)

A single I2V clip caps ~5 s, so a node's longer clip is a **chain of segments** joined at the
seam. We **strive for pixel-identical** handoffs (R124), but it is **the only place pixel-identical
is even attempted** (episode joins in P5 always use transitions, R26).

**Why the handoff isn't naturally bit-exact (author asked, round-17):** three compounding causes —
(1) the conditioning frame goes through a **VAE encode→decode roundtrip + the i2v generation**, so
even with `image_cond_noise_scale=0` the first generated frame is *close but not identical* to the
input; (2) **lossy video encoding** — pulling the "last frame" from an h264 `.mp4` already drifts,
so the handoff must use a **lossless intermediate** (PNG/lossless frames); (3) the **last frame is
itself generated** and may carry artifacts the next segment locks onto. So a bit-exact seam is hard
to guarantee — which is why we offer **three strategies**, all available (R124):

| Strategy | How | When |
| --- | --- | --- |
| **(a) Pixel-identical handoff** (strive for this) | lossless last-frame extract → `image_cond_noise_scale=0` first-frame lock → per-join check | the goal; when it converges, one truly unbroken shot |
| **(b) Seam-welding** ← *author's idea (round-17)* | a short **v2v / interpolation pass over the join**: **frame interpolation** (RIFE/FILM-class) to synthesize 2–4 bridging frames, **optical-flow morph**, or a brief **v2v harmonization** of an overlap window — welds two clips even without a bit-exact handoff | the practical middle path — far more achievable than bit-exact, near-invisible |
| **(c) Dissolve / transition fallback** (R26) | split into two clips joined by an **episode-level transition** (P5) | last resort, when (a)/(b) can't hide the seam |
| **(d) Native long-form** ← *author's idea (round-18), **ratified** R133* | **LTXV-extend**: generate natively longer video conditioned on prior segments (`ltxv` wrapper already stubs this as "Phase 3+", `image_cond_noise_scale=0.0` = hard conditioning) — **no seam exists to weld**; optionally a **wan2-animate quality/consistency drive pass + upscaler** over the LTXV draft | *if it holds on this 16 GB ROCm rig, this is the preferred path* and demotes (b) to fallback |

So a node yields **one seamless clip**. The realistic default to chase is **(b) seam-welding**, not
bit-exactness — unless **(d) native long-form** proves out on this rig, in which case (d) is
preferred (no seam at all) and (b) becomes the fallback. Only if all fail does it degrade to several
clips + a P5 transition.

> **Spike all of this FIRST (before the take-timeline UI):** on this ROCm rig validate, in one
> experiment, **(a)** the lossless-handoff path, **(b)** a **frame-interpolation welder** (RIFE/FILM)
> over the seam, **and (d)** **LTXV-extend native long-form** (backlog item 3, pulled forward from
> P6 as a *spike only* — R133). Outcome decides the default: if (d) works it wins; else (b) is the
> workhorse. The take-timeline design assumes a node becomes **one** clip — and it always does: a
> node/shot-version **exposes exactly one finalized clip** (R166). If nothing suffices for some pair,
> P3 still **concatenates the segments into one continuous file at Finalize** (the seam may be visible
> for that pair, but the **join stays in P3 and is never deferred to P5**); downstream (P4/P5) always
> bind/pick that single finalized clip per `shot_version`.

> **Acceptance bar (R129):** "**invisible at play speed**" is the v1 bar for (b)/(d) — tiny artifacts
> are OK if unnoticeable in motion; a clean frame-by-frame scrub is **not** required for v1. Welder
> models ship **both RIFE and FILM** (R130), modular/user-selectable; the heavier **v2v-bridge**
> variant is the fallback when interpolation alone leaves a ghost.

---

## 8. Node versioning + Finalize gate (R62, R30/R36, R119)

- **Node version per edit; rendering commits it** (R62). Episode (P5) picks a version's clip.
- **Out-of-date warning** when settings changed since the last render (advisory, R62).
- **Finalize gate (R30/R36):** the only hard checks are **resolution, fps, aspect ratio, audio
  format** matching the **project settings**; everything else is **eyeball**. Finalize **bakes the
  node-level voice** into the clip (§9) and locks the version (R119: Saved when rendered, Finalized
  when locked).

---

## 9. Audio — node-level voice only (R95)

P3 audio is deliberately minimal: **only the node-level voice** that drives lip-sync. **No episode
bed, no SFX, no music, no mixer** (those are P5+, R95).

- **Two TTS tiers (R41):**
  - **Draft TTS** (Piper/Kokoro, fixed voices) — fast placeholder/scratch lines.
  - **Cloning TTS** (XTTS/F5) — consistent per-character voice from a **user-supplied** sample
    (read the master English sample text; **loom does not record** — R84/R85). Voice is part of the
    AssetProfile version (P1/§3.4), copied across versions.
- **Or import** a recorded voice file directly.
- **Voice shape (R77):** **mono, up-sampled to the project audio master**, placed as a **pannable
  mono source in the stereo field** (up-sampling format-matches, doesn't add fidelity).
- **Lip-sync — 2–3 models, modular, user-selectable (R127):** audio-driven talking-head takes the
  node clip + the voice → a lip-synced clip. The author has no preference yet, so loom ships **at
  least 2 (preferably 3)** from the field (e.g. **Wav2Lip** — fast/classic, **SadTalker**,
  **LatentSync**, **Hallo**/**MuseTalk**) behind a common adapter, like the model registry — pick
  per node. **Likely each needs its own env** (component manifest, R31) — the isolated-venv
  exception. Optional per node. (Final model set chosen during build after a ROCm/quality check.)

### 9.1 Voice ↔ clip timing — clip-first with timecoded cues (R132)

The author (round-18) chose **clip-first**: you control the clip, then place **dialogue cues** on its
take timeline rather than stretching the clip to fit a voice line.

- **Dialogue cue = text (or imported audio) anchored at a timecode** on the node's take timeline.
  Multiple cues per node. You drag a cue to retime it.
- **Timecoding is automatic, not hand-entered:** TTS engines emit **word-level timestamps** natively;
  for **imported** audio, **forced alignment** (whisperX / aeneas / Montreal Forced Aligner) recovers
  word/phoneme timings. So loom knows exactly when each word lands → **lip-sync runs only over each
  cue's spoken span**, and the mouth animates under author-controlled timing.
- **Clip is the master (clip ≥ the cue's span):** a cue must fit inside the clip's spoken window. A
  line that **overflows** the clip is **pushed to the next consecutive node** (an authoring action,
  matches the "single clip per sequence point" model, R-articy).
- **"Fit clip to line" is a later convenience, not v1 default:** once **(d) LTXV-extend** lands
  (§7/R133), a button can extend the clip to a long line's duration. Until then, clip-first keeps
  generation off the long-video wall.
- **Finalize** bakes each cue's voice + lip-sync over its timecoded span into the node clip (§8).

---

## 10. Video masking (§8.3)

**SAM2-video** (propagated masks) for **per-clip region edit** — fix or swap a region across a clip
without re-rolling the whole generation. A queueable postproc action on a node clip.

---

## 11. 3D / depth proxies — deferred to P6 (R128)

Originally deferred from P1 → P3 (R108), now **deferred again to P6** (author, round-17 answer 5):
with no depth-proxy *use* and no engine export yet, nothing pulls 3D into P3, and it "has no
meaningful impact at this moment." When it lands in P6 it brings the `trellis2` path online —
props/scenery GLBs and the **proxy-miniature → depth-ControlNet keyframe** route from
`kb-pipelines01.md` (authored parallax/occlusion). **Not built in P3.**

---

## 12. Muse continuity director (creative SLM online)

P3 brings the **creative SLM online for the first time**, in a **focused, non-chat role** (full
Muse chat/agent is P4):

- **Shot-state JSON** — the SLM maintains a structured state (end pose, camera direction, actor
  state, props, background continuity) across a node's segments.
- **Next-segment continuity prompt** — from the prior segment's end-state, it writes the next
  segment's prompt (continue-the-turn wording + exact end-state), per `kb-pipelines01.md`.
- **On-demand tenant (R21), run as a queue step (R141):** the SLM's next-segment-prompt
  call is a **queued AI job interleaved between video gen steps** — SLM step → gen step → SLM step,
  **never concurrent** on the single GPU. It loads on demand and unloads (KV-cache start/stop). The
  model is the **single unified generative VLM** (`Qwen3-VL-4B-Instruct` Q8) that P4 standardises on
  (R144) — P3 may scaffold against a `kb-slm.md` default, but converges on the one model. **P3 builds
  the *minimal* AI-job + single-tenant mutual-exclusion this director needs (R168); P4 M5 *generalizes*
  it** into the full modular tenant (registry, KV prefix-cache save/restore, idle-gating, chat) —
  the same rule (R141), built incrementally (like the adapter contract). *(P4's §11.1 formalises
  "all AI = queue jobs"; P3 ships the first, minimal instance.)*
- **Author-approved, auditable:** the SLM **proposes** continuity fields; author-approved values win
  (Codex). This is the continuity-director role from `kb-pipelines01.md`, not chat/agent.

---

## 13. Build dependencies (`kb-storyboard01.md` §8.1)

P3 needs these pipeline-CLI additions (the engine backlog):

- **Item 4 — prepare-first-frame util** (resize/crop to project aspect + manifest) — feeds sketch,
  drive, and chaining.
- **Item 5 — motion-prompt-trial** (LTXV over N seeds/prompts → recipe for Hunyuan/Wan) — sketch tier.
- **Item 7 — keyframe/movie orchestrator** (asset bible, shot-state JSON, compositing, segment gen,
  last-frame continuation, stitch) — **this *is* the L3 backend**.
- **Item 3 — LTXV keyframes/extend** is the *ideal* continuity tool. Per R133 it is **pulled forward
  as a P3 spike** (the M1 experiment) — if native long-form holds on this rig it becomes the default
  continuity path (§7d); **hardening → P5 (R147, moved from P6 — `kb-loom-p5.md` §11.2)**. P3's
  *shipping* fallback is first-frame I2V chaining
  + seam-welding (works today). The wrapper already stubs extend ("Phase 3+", `run_pipeline.py`).

---

## 14. Disk & VRAM

- **Disk (⚠ R161 changed this picture):** P3 is the first **video-heavy** phase, and the old
  "final clips are small" (R83) **no longer holds** — each node's finalized clip is now a **lossless
  PNG-sequence master (~30–50× an mp4)**, and we **keep one per node version, never auto-pruned**
  (R161). So the **node masters dominate the disk budget**, not the drafts/temp. Rough order: a
  720p ~4 s master ≈ **100–240 MB**, so a 30-min episode of many versioned nodes can run to
  **hundreds of GB** — which is why the **default cap is now 250 GB (R164, raised from 100 GB)**, with a **creation-time footprint estimator (R164)**. The **two-threshold hard stop**
  (R96) + per-project cap apply, and the disk-guard **warns/hard-stops rather than deleting a
  master**; **size the work disk and set the project cap for the full set of PNG-seq masters**
  (drafts/proxies are comparatively cheap).
- **VRAM (16 GB, tight):** one heavy thing at a time. `wan2 animate` is the heaviest (≈2 h/video at
  720p per `kb-wan2.md`); `hunyuan`/`ltxv` are faster. **SLM/lip-sync unload before video jobs**
  (R21). Expose offload/precision; respect each model's `4N+1`/`8N+1` frame rules and fps
  (`kb-pipelines01.md` "Settings That Travel Well").

---

## 15. Risks & guardrails

1. **Frame-accurate continuity may not work (the make-or-break).** **Guardrail:** spike it **first**
   (§7) — both the lossless-handoff *and* the **seam-welder**; accept the **split-into-clips +
   P5-transition fallback** only if both fail. The take timeline must degrade gracefully, not block.
2. **P3 is big — keep it one doc, build in sub-phases (R125).** **Guardrail:** execute as
   **P3.1–P3.3** (§2, one doc); P3.1's animation core is the only hard prerequisite for a watchable
   node — voice and Muse follow. (3D removed → P6, R128.)
3. **Video on 16 GB ROCm is slow + heavy.** **Guardrail:** lean on the **sketch tier** (cheap
   `ltxv`) for iteration; spend the drive tier (`wan`) only on the chosen motion; serialize on the
   queue; expose offload. Set expectations: a finalized node clip may take **tens of minutes to
   hours** of GPU.
4. **Lip-sync likely needs its own env.** **Guardrail:** treat it as the **one isolated-venv
   exception** (R31), recorded in the component manifest; keep loom's core in the shared `.venv`.
5. **First creative-SLM use.** **Guardrail:** keep it the **narrow continuity-director role**
   (shot-state + next-segment prompts), **propose-and-approve**; don't build chat/agent here (P4).
6. **Don't regress P0–P2.** **Guardrail:** P3 is additive; an un-animated character (P1+P2) stays
   fully valid. A node with no voice is still a valid node.

---

## 16. P3 milestones — walking skeleton, continuity spike first

### P3.1 — Animation core (highest risk)

1. **M1 — continuity + welder + extend spike (no UI).** On this ROCm rig with `ltxv`/`wan`, validate
   in one experiment (a) lossless last-frame→first-frame chaining, (b) a **frame-interpolation welder**
   (RIFE/FILM) over the seam, **and (d) LTXV-extend native long-form** (R133 spike). **Go/no-go** with
   a default: if (d) holds it wins (no seam); else (b) is the workhorse. Bar = **invisible at play
   speed** (R129); confirm the split+transition fallback. *(De-risks the whole phase, §7.)*
2. **M2 — compositor.** Layer compositing (character@LoRA + prop + plate via BiRefNet) → start
   keyframe; transforms/masks/z-order in `node_version.json`.
3. **M3 — take timeline (sketch+drive).** `ltxv` sketch grid → pick → drive tier (**`wan2 animate`
   or `hunyuan i2v`, user-selectable**) → one segment clip; onboard the adapters (1-page checks).
4. **M4 — chaining + node versioning + Finalize.** Chain segments (pixel-identical or **welded**, per
   M1) into one node clip; node versions + out-of-date warning; Finalize gate. *(P3 core done-line:
   a >5 s finalized node clip — silent.)*

### P3.2 — Voice/audio

5. **M5 — voice + lip-sync.** Import voice / **draft TTS** + **cloning TTS**; mono→master, pannable;
   **lip-sync (2–3 models, own env)** bakes into the node clip at Finalize. *(Done-line: voiced node
   clip.)*

### P3.3 — Muse continuity director

6. **M6 — SLM continuity director.** SLM online (on-demand tenant); shot-state JSON; next-segment
   continuity prompts (propose-and-approve); video masking (SAM2-video) alongside.

### Done-line

7. **M7 — acceptance.** P2 character → composed → sketch→drive → **frame-continuous (chained or
   welded) >5 s clip** → voiced + lip-synced → **Finalized**, with lineage (§1).

---

## 17. Out of scope (defer)

- **3D / depth proxies** (`trellis2`, proxy-miniature depth route) → **P6** (R128).
- **Flow / Muse chat+agent** → P4. **Episode/Render + transitions + export** → P5.
- **Episode-level audio / SFX / music / mixer / stems** → P5+ (P3 = node voice only, R95).
- **VLM online + project-wide context** → P4 (R116). **LTXV keyframes/extend *hardening*** → **P5**
  (R147, moved from P6; the *spike* is here in P3, R133 — see §7d, not out-of-scope here).
- **Video LoRAs** (motion/style) → later.

---

## 18. Resolved (round 17) & still-open

**Resolved (R124–R128, in `kb-storyboard01.md` §10.0):**

| # | Decision |
| --- | --- |
| R124 | Continuity: **strive for pixel-identical**; **all three strategies available** — pixel-identical, **seam-welding** (interpolation/optical-flow/v2v), dissolve fallback (§7). |
| R125 | **Keep P3 as one doc** (fractured docs drift); build in internal sub-phases P3.1–P3.3 (§2). |
| R126 | **Both drive tiers available** (`wan2 animate` + `hunyuan i2v`), **user-selectable** per segment (§6). |
| R127 | **Lip-sync: 2–3 models**, modular/user-selectable; no preference yet (§9). |
| R128 | **3D / depth proxies deferred to P6** (no meaningful impact now); removed from P3 (§2, §11). |

**Resolved (round 18 → R129–R133):**

| # | Decision |
| --- | --- |
| R129 | **Welder acceptance bar = "invisible at play speed" for v1** — tiny artifacts OK if unnoticeable in motion; frame-scrub-clean not required for v1 (§7). |
| R130 | **Welder models: ship both RIFE and FILM** (frame interpolation), modular/user-selectable; v2v-bridge is the heavier fallback (§7). |
| R131 | **Default drive tier = `wan2 animate`** (fidelity priority); both `wan2`/`hunyuan` available (R126, §6). |
| R132 | **Voice ↔ clip = clip-first with timecoded dialogue cues** (forced alignment: TTS native + whisperX/aeneas/MFA for imports); clip ≥ cue span; overflow pushed to next node; "fit clip to line" is a later convenience (§9.1). |
| R133 | **LTXV-extend native long-form pulled forward as a P3 *spike*** (backlog item 3; **hardening → P5**, R147, moved from P6) — a 4th continuity strategy that deletes the seam; if it holds on-rig it's the default, else seam-welding is. Stack: LTXV-extend spine → optional wan2-animate drive pass + upscaler (§7d, §13). **Ratified by author** — default-safe (shipping path stays chain+weld); stack-validation work folded into P3-1/P3-14 (§19). |

**Still open:** none for P3 — R133 ratified; all four round-18 answers are settled and the surfaced
gaps are folded into the §19 WBS as planned work packages.

## 19. Work-package breakdown (WBS) — what P3 actually contains

*Rough solo-dev sizing: **S** ≤1 d · **M** 2–4 d · **L** 1–2 wk · **XL** 3 wk+. Risk: 🟢 routine ·
🟡 unknowns · 🔴 R&D / make-or-break. Maps to the §16 milestones.*

| WP | Work package | Maps to | Size | Risk |
| --- | --- | --- | --- | --- |
| P3-1 | **Continuity + welder + extend spike (no UI)** — chaining (a) + **RIFE/FILM welder** (b) + **LTXV-extend** (d); go/no-go + default | M1 | L | 🔴 **make-or-break (§7)** |
| P3-2 | **Compositor** — layer character@LoRA + prop + plate (BiRefNet) → start keyframe; transforms/masks/z-order in `node_version.json` | M2 | L | 🟡 |
| P3-3 | **Take timeline + sketch→drive** — `ltxv` sketch grid → pick → drive tier (**wan2 default / hunyuan**, R131) → segment clip; onboard adapters | M3 | L | 🟡 |
| P3-4 | **Chaining + node versioning + Finalize** — chain/weld into one node clip; versions + out-of-date warning; Finalize gate (res/fps/aspect/audio) | M4 | L | 🟡 |
| P3-5 | **Voice** — import / draft TTS / cloning TTS; mono→master pannable | M5 | M | 🟡 |
| P3-6 | **Timecoded dialogue cues + forced alignment** (TTS-native + whisperX/aeneas/MFA; cue timeline; overflow→next node) — **R132, new** | M5 | M | 🟡 |
| P3-7 | **Lip-sync 2–3 models** (own env each, R31), bakes at Finalize over each cue span | M5 | L | 🟡 env juggling |
| P3-8 | **Video masking** (SAM2-video propagated masks; per-clip region edit) | M6 | M | 🟡 |
| P3-9 | **Muse continuity director** — SLM online (on-demand tenant); shot-state JSON; next-segment prompts (propose-approve) | M6 | L | 🔴 first creative SLM |
| P3-10 | Acceptance: P2 char → composed → sketch→drive → continuous >5 s → voiced + lip-synced → Finalized + lineage | M7 | S | 🟢 |
| P3-11 | **Multi-hour video queue ETA + visibility** — `wan2` ≈ 2 h/clip; a node = several segments → up to a **full day** of serial GPU; honest estimates + progress (shares the P2-12 ETA work) | M3/M4 | M | 🟡 *folded from gap* |
| P3-12 | **Forced-alignment own-env + component-manifest entry** — whisperX/aeneas/MFA has its own ROCm/deps; the isolated-venv exception (R31), like lip-sync | M5 | S | 🟡 *folded from gap* |
| P3-13 | **Multi-cue lip-sync compositing** — several timecoded cues on one clip → lip-sync **per-span then composite** the synced windows back onto the single node clip | M5 | M | 🟡 *folded from gap* |
| P3-14 | **R133 stack validation (in the M1 spike)** — (1) choose/wire an **upscaler** for the LTXV draft (own env?) and (2) **validate wan2-animate driving from a full LTXV clip** (it normally drives from a pose/reference) | M1 | M | 🔴 *folded from gap* |
| P3-15 | **Project audio-master plumbing** — wire the creation-time audio master (P0) through to the TTS/voice up-sampling stage; confirm, don't assume | M5 | S | 🟢 *folded from gap* |
| P3-16 | **Frame-seq clip master (R161)** — write decoded frames as a lossless PNG seq pre-mux; **kept for every version (no auto-prune)** + mp4 proxy for playback; batch-i2i reads/writes the frame master; sidecar (fps/audio-ref/color) | M4 | M | 🟡 *folded — disk-heavy* |
| — | LTXV-extend **hardening** beyond the spike → **P5** (R133/R147, moved from P6); all **3D** → P6 (R128) | §11/§17 | — | — |

**Rollup:** ~16 WP — **the heaviest phase.** Risk concentrated in **P3-1 (continuity)** and **P3-9
(first creative SLM)**, with **P3-7 (lip-sync envs)** a logistics tax. New since round-18: **P3-6
timecoded cues** and the **LTXV-extend** strand inside P3-1. **P3-11–P3-15 were surfaced by the WBS
gap-scan and are now planned** — note **P3-14 folds the R133 stack-validation into the M1 spike** (so
the two unproven joints get tested before anything is built on them).

**Design notes for the folded-in WPs:**
- **P3-13 multi-cue compositing** is the non-obvious one: a node with three lines means three lip-synced spans on one clip. v1 runs lip-sync per cue-span and composites the mouth region (the masking from §10 can scope each composite), rather than one pass over the whole clip.
- **P3-14** keeps the R133 risk honest — the spike must prove wan2-animate accepts a *full clip* as drive input *before* the LTXV-extend→drive-pass stack is treated as the preferred continuity path.

---

## Source / traceability

- Decisions: `kb-storyboard01.md` §10.0 (R17/R26/R35 continuity, R30/R36 finalize, R41/R70/R77/R84
  voice, R62 node versioning, R95 v1 audio, R108 3D-in-P3, R114 anchor, R116 VLM-deferred, R119
  Saved/Finalized).
- Engine: `kb-pipelines01.md` (animation loop, layered assets, continuity, TRELLIS proxy route, the
  L3-backend gap list §8.1 items 3/4/5/7); `kb-wan2.md`/`kb-hunyuan.md`/`kb-ltx09.md` (video models);
  `kb-trellis2.md` (3D); `kb-postproc-img.md` + `kb-slm.md` (lip-sync/TTS/SLM); `src/pipeline/*`.
