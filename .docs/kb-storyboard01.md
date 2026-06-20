# Story Generation Platform — Design Plan 01

Created: 2026-05-31
Working title: **Loreweave Studio** (codename `loom`)

Scope: a desktop authoring application that unifies the repo's seven generative pipelines —
three image (`flux2`, `sd35`, `zimage`), three video (`wan2`, `hunyuan`, `ltxv`), one 3D
(`trellis2`) — plus the continuity SLM (`kb-slm.md`) into a single articy:draft-style
story-generation tool: define a world and style, build reusable asset profiles, train
per-asset LoRAs, compose scenes and shots, animate them with a budget-sketch → driving-video
loop, and wire it all into a non-linear narrative graph of actions and outcomes.

This document focuses on **how the UI works and looks**. It builds directly on
[`kb-pipelines01.md`](kb-pipelines01.md), which already specifies the cross-pipeline
cooperation model, manifests, asset bible, LoRA primer, layered-asset strategy, the
keyframe → sketch-i2v → wan-animate workflow, and the TRELLIS depth-proxy route. Where this
doc says "the orchestrator does X", the mechanics are in that KB.

### Product intent (clarified 2026-05-31)

The author has scoped this down from an earlier ambition (a full computer-game generation
pipeline). That remains a long-term aspiration but is out of reach today given the manual
3D/rigging/skinning/animation skill it still demands. The realistic, motivating goal is:

> **A non-linear storyboard tool (an articy:draft *replacement*, not an integration) whose
> headline deliverable is the ability to visualize the main storyline as a ~30-minute video
> "episode"** — usable to pitch the idea or distribute on a streaming site — **while keeping
> the door open to export assets and story into Blender and Unreal Engine 5.7+** (both
> installed on the author's PC) so the same story could later become a game once the author's
> game-dev skills grow.

Three consequences for this design:

1. **The Episode is the north star, not a game build.** The narrative graph stays non-linear,
   but the platform must be able to mark and render a **main-storyline path** as one
   continuous video with dialogue. "Game rules" (variables/conditions) become *optional*
   structure for branching, not a hard requirement.
2. **Replacement, not interop, for articy.** No articy round-trip in the critical path.
   Engine/DCC export is a *later* goal, mirroring how articy exports to Unreal/Blender.
3. **Start at the asset layer.** The first thing the author wants working is the
   character-consistency loop (multi-batch casting → replace/insert chosen character into new
   shots → curate → train a LoRA). That is now specified as a first-class workflow in §4.1.

---

## 1. Did the preliminary work help?

Yes — `kb-pipelines01.md` is ~80% of the engine design for this platform. Concretely, it
already gives us:

| Platform need | Already solved in `kb-pipelines01.md` |
| --- | --- |
| Pipeline roles / when to use which | Capability matrix + "Practical Defaults" table |
| Cross-pipeline handoff contract | "Shared Handoff Rules" (PNG/MP4/latent + JSON manifest, no co-loading) |
| Animation strategy | "Fast Still To Video" extended long-take form; "Multi-Keyframe Movie Build" |
| LoRA feasibility for assets | "LoRA Primer For This Scenario" + "LoRA Versus Layering" |
| Consistent characters/props | "Layered Asset Strategy" + "Generating Consistent Keyframes" |
| Camera/parallax control | "TRELLIS Proxy Miniature To Depth-Controlled Video" |
| Take/segment data shape | The `take_001` orchestrator JSON + GUI sketch bullet list |
| Continuity automation | SLM as "continuity director" maintaining shot-state JSON |
| Build backlog | "Current Implementation Gaps Worth Building" (8 items) |

This platform is the **product layer** that turns those manual/manifest workflows into a
visual, stateful, non-linear authoring tool. The big new design surface is the **UI**, the
**narrative graph**, and the **project/asset data model** that ties generations to story
nodes.

The one architectural commitment to carry forward verbatim: **file + manifest handoff, one
heavy model on the GPU at a time, subprocess isolation.** The UI never co-loads models; it
queues jobs that shell out to the existing `src/pipeline/*/run_pipeline.py` workers.

> **Codex comment - understanding:** This is a sound boundary for the first version. The
> desktop app is not a new inference runtime; it is a durable project editor and job
> scheduler in front of CLI workers. That keeps pipeline failures isolated and lets the UI
> recover by reading manifests even after a worker exits.
>
> **Concern / decision:** Treat "~80% of the engine design" as workflow coverage, not
> implementation readiness. Before building the shell, inventory the actual CLI commands,
> their machine-readable progress output, cancellation behavior, resume behavior, and
> manifest consistency. The product layer depends on those operational contracts.

---

## 2. Mental model

Loreweave has four conceptual layers. The UI is organized around them.

```
┌──────────────────────────────────────────────────────────────────────┐
│  L4  NARRATIVE GRAPH   nodes, branches, conditions, outcomes (articy)  │
│         ▲ references assets + shots, drives playthrough/export         │
├──────────────────────────────────────────────────────────────────────┤
│  L3  SHOTS & TAKES     scenes → shots → keyframes → animation segments │
│         ▲ composed from assets, produced by the pipelines              │
├──────────────────────────────────────────────────────────────────────┤
│  L2  ASSET LIBRARY     character / prop / scene profiles + LoRAs + GLBs│
│         ▲ generated under the style, reused everywhere                 │
├──────────────────────────────────────────────────────────────────────┤
│  L1  STORY BIBLE       world, visual style, asset classes, naming,     │
│         story spine, game rules — the constraints everything inherits  │
└──────────────────────────────────────────────────────────────────────┘
        Cross-cutting: JOB QUEUE (VRAM-aware) · SLM DIRECTOR · MANIFESTS
```

Articy:draft analogy: L1 is the *Global / Template* tab, L2 is the *Asset/Entity library*,
L4 is the *Flow* editor (the node graph). L3 (shots/takes/animation) is the piece articy
doesn't have — it's where the generative pipelines live.

> **Codex comment - understanding:** The layers form a dependency direction: L1 supplies
> defaults, L2 supplies reusable identities and plates, L3 creates visual beats, and L4
> decides when those beats are played. Cross-cutting services observe all four layers
> without becoming a fifth authoring layer.
>
> **Concern / decision:** Keep references flowing by stable IDs, not by paths or display
> names. A renamed character or moved shot should not break graph nodes. Also decide whether
> an L4 node owns a shot or merely references a reusable shot; both are useful, but they have
> different editing and export semantics.

---

## 3. Data model — the "Story Bundle"

A project is a single folder ("Story Bundle") that is self-contained, portable, and
manifest-driven. Everything the UI shows is a projection of files on disk. **A Story Bundle is
user data, not part of the loom source repo** — see §3.3 for the ComfyUI-style scope split.

> **Codex comment - understanding:** This makes the filesystem the persistence API. The UI
> should be able to close, reopen, and reconstruct its state from the bundle without a
> hidden database. That is a strong fit for scripting, backup, and inspecting changes in
> git.
>
> **Concern / decision:** "Self-contained" and "git-friendly" need separate definitions.
> JSON, Markdown, prompts, and small reference images fit in git; generated videos, GLBs,
> model weights, logs, and caches can become very large. Specify what is committed, what is
> optional, and what can live in a project-local ignored cache or external artifact store.

### 3.1 Folder layout (authored once in L1, enforced everywhere)

```
my-story/
├── story.json                 # L1 Story Bible (world, style, asset classes, spine, rules)
├── naming.json                # naming convention + folder template (drives all asset paths)
├── bible/
│   ├── world.md               # long-form world doc (markdown, editable in-app)
│   ├── style/                 # style refs: palette, lighting, lens, negative constraints
│   │   ├── style.json
│   │   └── moodboard/*.png
│   └── spine.json             # premise, arc, factions, NPCs, player goals
├── assets/
│   ├── characters/<name>/     # one folder per character profile
│   │   ├── profile.json        # identity + version list + active version (§3.4)
│   │   └── versions/
│   │       ├── v1_black-hair/  # one folder per profile VERSION (a distinct state/look)
│   │       │   ├── version.json # prompt template, refs, lora ref, status, derived_from
│   │       │   ├── refs/*.png    # curated reference set for THIS version (LoRA corpus)
│   │       │   ├── lora/*.safetensors
│   │       │   ├── sheets/*.png
│   │       │   └── glb/*.glb     # optional TRELLIS proxy
│   │       └── v2_red-hair/     # e.g. character later dyes hair red
│   │   # …                       # props (intact / broken), scenes (day / night), etc.
│   ├── props/<name>/           # same shape (versions/: intact, torn-in-two, …)
│   ├── scenes/<name>/          # same shape (versions/: day, night, ruined, …)
│   └── _class_defs.json        # asset-class definitions (landscape, foliage, ruins, ...)
├── shots/
│   └── <scene>/<shot>/         # L3 — see take_001 schema in kb-pipelines01.md
│       ├── shot.json
│       ├── keyframes/*.png
│       ├── drafts/*.mp4         # budget i2v sketches
│       ├── clip_frames/*.png    # LOSSLESS frame-seq master, per version, never pruned (R161)
│       ├── clip_proxy.mp4       # cheap proxy for UI playback, per version (R161)
│       └── proxy/               # depth/silhouette/rgb passes (TRELLIS route, P6)
├── flow/
│   └── graph.json              # L4 narrative graph (nodes + edges + conditions)
├── context/
│   ├── project_context.json     # compact text digest for Muse/VLM (P4)
│   ├── project_facts.jsonl      # rebuildable typed facts for future GraphRAG (P4)
│   └── graph_index/             # deferred GraphRAG/vector index (post-v1/P6)
├── jobs/
│   └── queue.json + logs/      # job queue state, per-job manifest + stdout
├── renders/
│   └── <render>/              # L5 — one per render config (multiple allowed, §6.7)
│       ├── render.json         # hand-built clip order (R148), per-junction transitions (R157), node-voice only (v1, R95), settings
│       ├── render.manifest.json# settings snapshot + provenance for THIS render
│       └── out.mp4             # the rendered episode (+ EDL/FCPXML + voice for Resolve; stems post-v1, R95)
└── exports/
    └── handoff/                # engine/DCC asset bundles (GLB + textures + graph)
```

The `naming.json` template makes asset paths deterministic, e.g.
`{class}/{name}/{name}_{variant}_{seed}.png`. The UI computes paths from it so the user
never hand-types a path, and so a renamed convention can re-flow the whole library.

> **Codex comment - understanding:** The layout is readable and maps cleanly onto the UI.
> I would interpret `profile.json`, `shot.json`, and `graph.json` as authored records, while
> images, clips, proxies, and manifests are immutable generated artifacts referenced by
> those records.
>
> **Concern / decision:** Do not silently "re-flow the whole library" when the naming
> convention changes. Moving generated files can be expensive and can invalidate external
> references. Use immutable internal IDs for storage, treat names as presentation metadata,
> and expose an explicit migration command with a dry-run report if physical paths must
> change. The queue also needs a crash-recovery format: `queue.json` should distinguish
> persisted jobs from ephemeral worker process state.

### 3.2 The core record types

| Record | Lives in | Key fields | Produced/edited in |
| --- | --- | --- | --- |
| **StoryBible** | `story.json` | world summary, style ref, asset classes, naming, spine, rules, **project format** (aspect/res/fps/audio) | L1 World workspace |
| **AssetProfile** | `assets/.../profile.json` | id, class, **version list + active version**, lineage; versions (`versions/<vN>/version.json`) hold prompt template / refs / lora / glb / voice / `finalized` (§3.4) | L2 Asset Studio |
| **Shot** | `shots/.../shot.json` | scene, asset@version refs, keyframes, segments[], camera, **clip versions + which is `final`** (the `take_001` shape); **every version's clip stored as a lossless PNG-seq master + mp4 proxy, never pruned (R161)** | L3 Shot/Take editor |
| **FlowNode/Edge** | `flow/graph.json` | node type, title, body, conditions, variable mutations, **pinned `asset@version`**, owned shot/clip | L4 Narrative graph |
| **ProjectContext** | `context/project_context.json` + `context/project_facts.jsonl` | compact digest for SLM/VLM plus rebuildable typed facts/relations for future GraphRAG | P4 Muse/context assembler |
| **Render** | `renders/<render>/render.json` | hand-built clip order (R148), per-junction transitions (R157), **node-voice only — no episode bed/mixer in v1 (R95)**, format settings, **own manifest** (multiple per project, §6.7) | L5 Episode/Export |
| **Job** | `jobs/queue.json` | pipeline, mode, params, vram estimate, status, output manifest path | Job Queue (cross-cutting) |

Every generated file carries the per-pipeline JSON manifest the workers already emit
(`kb-trellis2.md`, `kb-pipelines01.md`). The platform adds one **lineage edge** per
generation: `{asset|shot|node} → job → output_file → manifest`, so any image/clip can be
traced back to its prompt, seed, model, LoRA, and the story node that requested it.

**GraphRAG posture (research update 2026-06-14):** the right long-term retrieval architecture is
not a generic "stuff everything into a vector DB" RAG, but a **typed project knowledge graph plus
vector retrieval**. Microsoft GraphRAG's core idea — extract structured entities/relations, cluster
or summarize communities, and answer both local and global questions over that graph — matches
Loreweave's shape: worlds, styles, assets, versions, LoRAs, shots, Flow nodes, variables, and renders
are already graph-like. For v1, do **not** build the expensive persistent GraphRAG index. Instead,
every phase should write stable IDs, lineage, manifests, and small rebuildable fact files so P6 can
add GraphRAG without scraping prose or reverse-engineering filenames.

> **Codex comment - understanding:** These records are enough for a first vertical slice.
> Lineage is particularly valuable because it turns a variant gallery into a reproducible
> experiment history rather than a folder of loosely related outputs.
>
> **Concern / decision:** Add schema versions and stable IDs from day one. Use atomic file
> replacement for writes and validate records on load, because file watching plus external
> edits can otherwise expose partially-written JSON. Consider one lineage index file or a
> small rebuildable index so the UI does not need to scan every manifest on each startup.

### 3.3 Persistence, IDs, and the git scope split (ComfyUI model)

**Critical scope clarification (author, 2026-05-31):** git versioning covers **only the loom
application we build** — the Tauri/React shell + Python orchestrator source, shared publicly
on GitHub. **Story Bundles (user projects) are *not* tracked by that repo at all**, exactly
like ComfyUI: the *software* lives on GitHub, but every project/output a user makes with it
lives outside git's scope. The §3.1 example bundle (`my-story/`) is user data on disk, not a
folder in the loom repo.

| Thing | Git treatment |
| --- | --- |
| **loom application** (shell, orchestrator, adapters, UI) | **the git repo**, shared on GitHub |
| **Story Bundles** (`story.json`, assets, versions, shots, flow, renders, LoRAs, temp…) | **outside git** — user data, living on the **work disk** (default `F:\_tmp`, §4.2) |
| **The work disk** (the whole project workspace, incl. LoRA temp, §4.2) | user data, **never in the app repo** |

So there is **no media in git** (your answer 8) — trivially, because the app repo contains no
project media in the first place. A user *may* version their own bundle in their own repo, but
the loom app never assumes or requires it, and ships **no** `.gitattributes`/LFS for bundles.
`loom init` (P0) creates a **plain project folder**, not a git repo.

The data-hygiene practices below still apply to the bundle *format* (they make a robust,
portable on-disk format — not a git concern) and resolve the Codex issues:

- **Stable internal IDs, names are presentation only.** Every record gets an immutable `id`
  (e.g., `chr_7f3a`, `shot_91c2`, `node_44de`) on creation. Flow nodes, shots, and lineage
  reference IDs, never display names or paths. Renaming an asset or re-flowing the naming
  convention (§3.1) is then pure metadata — it never breaks a graph edge or a lineage trail.
  The §3.1 "re-flow the whole library" idea is downgraded to an **explicit, dry-run migration
  command**, not an automatic side effect (per Codex).
- **`schema_version` on every record**, with a documented upgrade path. The loader validates
  on read and refuses partially-written JSON.
- **Orchestrator-owned, atomic writes.** Only the orchestrator writes bundle records (the UI
  requests changes through the typed API); writes are temp-file + atomic rename; the
  orchestrator emits change events the UI consumes. This removes the file-watch write race
  Codex flagged. External edits (hand-editing JSON) are still picked up read-only.

### 3.4 Asset Profile versioning — asset state over story time (author, round-5 answer 6)

Stories change their assets: a character **dyes black hair red** or **grows a mustache**, a
prop **breaks into two parts**, a scene has a **day and a night** version. So an AssetProfile
is **not one look — it's a list of versions**, each a distinct *state* of the asset, and a node
records **exactly which version it used**. This is the mechanism that keeps the storyboard
honest as the world evolves.

Model (refined per round-6 answers 1, 2, 4, 8):

- **AssetProfile = identity + ordered list of versions** (`v1`, `v2`, …). The profile holds the
  stable identity (who/what this is, the asset class) and points at versions.
- **A version is the unit that drives generation.** Each version owns its **prompt-template
  snippet, curated reference set, optional LoRA, canonical image(s), GLB, voice (for characters),
  and status**. Training (P2) is **per version**.
- **Versions are for *small* changes; significant/independent changes get a *new profile*
  (round-6 answers 1, 2).** A new version is a **light edit**, not a fresh start. **The author
  decides** version-vs-new-profile — loom **never auto-suggests** (round-7 Q3).
- **Copy-on-create = a *full independent duplicate* (round-7 answers 1, 4).** Creating a new
  version **deep-copies everything** from a chosen parent — refs, prompt snippet, **its own copy
  of the LoRA**, **voice profile** (the cloning sample + settings), GLB. It is a **separate
  entity with its own resources**; the parent stays **completely unchanged**.
- **LoRA (re)training base is a choice — default *from base model* (round-8 answer 2).** When you
  retrain a version's LoRA, loom offers two starting points: **"train from base model"
  (default)** or **"seed from parent LoRA"** (continue/fine-tune the copied adapter). From-base
  is the safer default — seeding from the parent can entrench the old look and make a larger
  change (black→red hair) harder to learn. The copied LoRA still lets the version *work*
  immediately (inherited look) until you retrain.
- **Base a new version on *any* prior version, not just the latest (round-7).** The "[+ new
  version]" picker lets you choose **which existing version to copy from** as the baseline.
- **Three version states — *Saved* ≠ *Finalized* (clarified round-16; supersedes the looser R69
  wording).** A profile version is in exactly one of:
  1. **Unsaved (draft, in-memory):** edits since the last Save. **Lost on exit**, with a warning
     popup. loom does **not** autosave drafts — this is the "no interim state" rule (R69).
  2. **Saved (committed, *unfinalized*):** an explicit **Save** persists the version to disk. It
     **survives exit** and is **still editable and trainable** — P1 "Save AssetProfile" lands here,
     and P2 **trains on a Saved-but-unfinalized version**. *Finalization is about **locking**, not
     about persistence* — so a Saved version persists whether or not it is finalized.
  3. **Finalized (locked):** **Finalize** makes the version **immutable**; any further change
     requires a **new version** (or a new profile for big changes — §3.4 above).
  So "lost on exit" applies to **unsaved** edits, **not** to saved-unfinalized versions. The
  earlier shorthand "unfinalized edits are lost" should read "**unsaved** edits are lost".
  **The job queue also persists** (§6.6) — queued work survives shutdown and resumes; only
  *partial outputs* and *unsaved edits* are discarded.
- **Identity anchor — on by default, opt-out, available at every stage (round-9 answer 7;
  round-10/11 answers 3, 2, 9).** A character carries an **identity anchor**: a **detailed face
  image** + a **PuLID/IP-Adapter-style lock** (§8.3). It is **on by default** (opt-out per
  character/version), because it's cheap insurance — though the author frames it as a **polish
  improvement**, since Stage-B (§4.1) should already produce enough consistent material; the
  anchor is the extra precaution. It is usable at **every operation**, all toggleable:
  - **during Stage-B expansion** (preferred) — postprocess the expansion images to one face so
    the **LoRA trains on already-consistent images** (no special training constraint),
  - **inference-time PuLID** post-process on any shot,
  - **constrained LoRA training** (not only for faces — any attribute to pin),
  - **across versions** so only the *intended* attribute changes.
  **The anchor face is per-version (round-11 answer 3):** a character's face can legitimately
  change between versions (a scar, a face tattoo), so each version may have its own anchor face.
  Expectation: **LoRA + prompt-snippet injection do most of the preservation**; the anchor is the
  supplement.
- **Face-anchor sub-stage (new, round-11 answer 3).** Because the anchor needs a *detailed* face
  (a casting hero ★ may be a full-body shot), the bootstrap gains a small **"face" step**:
  generate a **variety of face portraits** for the version and **pick one as the anchor**. Stored
  per version alongside refs/LoRA/voice.
- **Editable until finalized; finalize is *pure intent* (round-7 answers 2, 4).** You change
  only the parts that differ (e.g. regenerate refs "+red hair", retrain the LoRA; **voice stays
  as copied unless you deliberately change it**). A version is mutable until you **finalize** it —
  which is a **pure declaration of "this look is locked"** (no concrete bar like "must have a
  LoRA"). **Once finalized it is immutable**; any further change means a **new** version (or a
  new profile if big).
- **Nodes pin a specific version.** An L4/L3 node references `asset@version` (e.g.
  `chr_7f3a@v2_red-hair`). Change a character mid-story → later nodes pin the newer version;
  earlier nodes keep the older one. **Re-pinning is an explicit edit**, never automatic.
- **One "active" version** is the profile's current default for a fresh node; any version stays
  selectable.
- **Expect *many* versions (answer 8).** They're used for very small, refined tweaks tested
  across the same asset, so there will be lots. The version UI therefore needs **organization —
  grouping, search, clear naming** — not just a flat dropdown (see §6.3).

**Scenes are plate/asset only — no "continuity check" (answer 3).** A scene's day/night/ruined
versions are just different **plates/assets**; loom does **not** try to verify two scene clips
"look like the same place" (cumbersome, and not really enforceable). If a location must feel
consistent across shots, that's carried by the **props and characters placed in it**, not by a
scene-matching tool.

> **Scope guard:** versioning is *metadata + folders*, not a heavy VCS. It reuses the stable-ID
> + atomic-write rules (§3.3); a version is just another record with an `id`, `derived_from`,
> `status`, and a `finalized` flag. Don't over-engineer diffs/merges — the author wants "copy,
> tweak, lock; pick an earlier or newer version," not git-for-assets.

---

## 4. Can asset images train LoRAs? (your three sub-questions)

Short answer from `kb-pipelines01.md` "LoRA Primer": **yes for characters, yes for props,
partially for scenery — but pair LoRA with layering, not instead of it.**

| Asset class | LoRA viability | Recommended platform approach |
| --- | --- | --- |
| **Character** | Strong. The canonical use case. 15–50 curated images, unique trigger token, model-family-specific. | Asset Studio collects a ref set as you generate; a "Train LoRA" action queues training when the set is large/clean enough. Best near-term wiring: `zimage` then `sd35` (`kb-pipelines01.md` "Local Pipeline Fit"). |
| **Prop** | Good for *distinctive hero props* (an artifact, a signature weapon). Weak/overkill for generic props. | Same flow as characters. For generic props, prefer an **isolated prop sheet + composite/inpaint** (layering) over a LoRA. |
| **Scenery / environment** | Weakest as a pure LoRA — environments vary too much per shot. A **style LoRA** ("this film's look") is more useful than a per-location LoRA. | Use a **fixed background plate + style LoRA + depth/ControlNet** instead of an environment LoRA. A location is better captured as a reusable *plate + proxy geometry* than as trained weights. |

So the platform treats LoRA as **one of three consistency mechanisms**, surfaced together in
the Asset Studio:

1. **Reference set → LoRA** (identity that survives redraws),
2. **Layering** (background plate + character alpha + prop sheets — avoid redraws),
3. **ControlNet / depth / masks** (where and how to change).

Plus the SLM keeping the shot plan coherent. This is the "strongest stack" from
`kb-pipelines01.md`. The UI's job is to make accumulating a clean ref set a *byproduct of
normal work*: every time you "promote" a generated still as on-model, it's added to that
asset's training corpus with one click, and a readiness meter tells you when a LoRA is worth
training.

> **Codex comment - understanding:** The UI should treat training-set curation as a normal
> gallery operation. Promotion means "preferred production variant"; inclusion in a LoRA
> set means "appropriate training example." Those actions are related but should remain
> separate because a beautiful image can still be a poor training sample.
>
> **Concern / decision:** Image count alone is not a meaningful readiness score. Readiness
> should also flag near-duplicates, weak angle or expression coverage, inconsistent
> costumes, missing captions or trigger tokens, and base-model family compatibility.
> Generated sheets need to be split and reviewed as individual images before training.
> Training must record dataset snapshot, captions, trainer settings, base model, and output
> hash in the manifest.

### 4.1 Character bootstrap loop (the start-here workflow)

This is the workflow the author wants first, and it answers the "generate an image then
replace the character in it with a character from another image" question. The hard part is
**Stage B** ("replace/insert the chosen character into new shots") — it is not yet a defined
pipeline command, so the platform stages it as a *bootstrap* that ends in a LoRA, after
which consistency is solved for free.

```
A CASTING            B EXPANSION                C CURATION         D TRAIN        E LOCK
multi-batch t2i  →   put the CHOSEN look into →  keep on-model  →  LoRA in     →  character
(flux2/sd35/      │  many poses/expr/scenes,  │  shots; cull   │  isolated    │  reproducible
 zimage) + clean  │  same style+world held    │  near-dupes;   │  workspace   │  anywhere via
 + polish         │  constant                  │  ensure angle  │  (§4.2)      │  trigger token
      │           │                            │  & expr        │     │        │     │
   pick the    SLM writes the varied        coverage        snapshot+      feeds normal
   "hero"      prompt list (style/world      (Codex          captions+      Asset Studio,
   image ★     fixed, pose/scene varied)     readiness)      trigger        Shots, Episode
```

**Stage A — Casting.** Run the existing **multi-batch** image process (`multi` + clean/polish,
which the author already gets good results from — e.g. 8 batch runs × 3 iterations across all
three pipelines for prompt refinement) across `flux2`/`sd35`/`zimage` to produce a variation
grid. The author picks **one hero image**. Casting constraints (author, round-4 answer 6):

- **`--num-candidates` ≤ 5** (hard max per generation call).
- Many batch *instances* are expected, but **total images per casting process is capped at
  ~200 (configurable)** — enough for prompt refinement without runaway disk/time.
- loom keeps the **current multi-batch workflow as-is**, but the big GUI win is making the
  results **selectable**: a reviewable grid where you star/cull candidates (vs. digging through
  output folders). **v1 = a simple scrollable grid** (author, round-5 answer 4) — star/cull
  only; filtering/sorting/bulk-ops are deferred. This same grid serves Stage C curation.

(These batch siblings are *not* yet consistent — that is fine; they are casting candidates,
not the character.)

**Stage B — Expansion (the previously-undefined step).** Produce *N* images of **that exact
hero** in varied poses, expressions, framings, and scenes while holding the L1 style + world
constant. There is no single local command for this today, so the platform offers a tiered
method ladder (fidelity ascending), and records which was used:

| Method | What it does | Repo status | Best for |
| --- | --- | --- | --- |
| **Flux2 multi-reference** (character role) — *the investment* | condition new scenes on the hero as a reference image | **needs `--ref-image` wiring** (`kb-pipelines01.md` gap, §8.1 item 2) | **the primary "insert this character into any scene" path** — author's chosen direction (answer 4) |
| **img2img sweep** from the hero | low/mid `strength` variations | available now (`_img2img`) | pose/expression/lighting diversity, fast bulk while multi-ref is being wired |
| **inpaint the hero into SLM-proposed scenes** | mask subject region, repaint scene around a held subject (or vice-versa) | available now (`sd35`/`zimage` inpaint) | new backgrounds with a stable subject |
| **video-sketch frame harvest** | run cheap `ltxv` low-res sketches of the hero in motion, then extract good frames as multi-pose refs | available now (`ltxv i2v` + frame extract) | **multi-angle/pose coverage without 3D** — the author's preferred alternative to a TRELLIS turnaround |

> **Why not TRELLIS turnaround here (author, 2026-05-31):** a TRELLIS GLB only gives *still*
> orbit angles and can't act/pose without major manual rigging/animation. The author judges it
> cheaper to **churn out low-res video sketches and harvest frames** until the needed
> poses/angles appear, and to **invest in Flux2 multi-reference** as the real "put this
> character anywhere" mechanism. TRELLIS stays in the platform for what it's actually good at —
> generating **prop/scenery 3D assets and depth proxies for engine/DCC export** (§6.4, §6.7) —
> but it is **removed from the character-expansion ladder.**

The **SLM is central here** (your answer 4): it generates the Stage-B prompt list — dozens of
"same character, new situation" prompts that keep the style/world clause fixed and vary only
pose/expression/scene/camera. This is exactly the "generate different images but with the same
style and scenery definition" you described, and it feeds both the still methods and the
video-sketch harvest.

**Stage C — Curation.** Review the expansion grid and keep only on-model shots. The readiness
meter (per Codex) is not just a count — it scores **angle coverage, expression spread,
costume consistency, and near-duplicate penalty**, and requires captions + a trigger token.
This is where the bulk of generated images get accepted or discarded.

**Stage D — Train** in an **isolated workspace** (§4.2). Output: one `.safetensors` LoRA +
a recorded dataset snapshot.

**Stage E — Lock.** The trained LoRA becomes the character's canonical consistency mechanism.
From here the character is reproducible in the Asset Studio, the Shot compositor, and the
Episode — Stage B's struggle never has to be repeated for this character.

> **Data lifecycle (your answer 3, made explicit):** Stage B/C generate a *lot* of throwaway
> imagery. After Stage D: keep the LoRA, the curated training snapshot, and a small "bible"
> subset that documents the character's look/background; **discard the rest under a retention
> policy.** None of the scratch corpus is committed to git (§3.3 scratch tier).

**UI surface.** Stages A→C live in the Asset Studio (§6.3) as a left-to-right **bootstrap
strip** above the variant gallery:

```
┌ Mara · CHARACTER BOOTSTRAP ───────────────────────────────────────────────────┐
│ A Casting        B Expansion             C Curation         D/E                 │
│ ▦▦▦▦ pick ★ →   method[flux2 multi-ref▾][+video-sketch]  →  keep ✓ cull ✕  →   │
│ hero: ▣          SLM prompt list (24) [Generate ×24 ▶]    coverage: F◑S◑¾◔ B◔   │
│                                                            readiness ▓▓▓▓▓░ 31/40│
│                                                            [Train LoRA ▶ (isolated)]│
└────────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 The work disk ("scratch disk") = the whole project workspace (author, round-8 answer 5)

**Reframe (round-8 answer 5):** the **scratch disk is the entire project workspace**, not just
LoRA temp. It holds **everything** — the Story Bundle (profiles, versions, assets, shots, flow,
**renders**), plus all transient/temp (training runs, draft videos, extracted frames, latents).
So the default **`F:\_tmp`** (≈140 GB free now, ~400 GB more being freed) is where projects
live, and its **size cap is a hard budget for the whole project** (see disk-guard below). This
is consistent with §3.3 — the bundle is still **user data outside the loom app's git**; it just
physically lives on this work disk.

- **One configurable work disk (answer 1).** Both **location and size cap** are settings.
  Project workspace path defaults to `F:\_tmp\<project>/`. LoRA training is **not a separate
  disk** — its temp lives under the same workspace (e.g. `<project>/_temp/lora_<runid>/`).
- **Two-threshold disk guard, policed continuously (round-8 answer 5; round-11 answers 4, 10 —
  this *adds* running-total policing).** loom watches **two** measures: **(a) project-folder size
  vs. the per-project cap**, and **(b) the work disk's actual free space**. For *either*: at
  **<5% headroom remaining → warning**; at **<2% remaining → hard stop** of new space-consuming
  work (generation, training, render). **Raising the cap or freeing space resumes.** This is
  checked **at project creation *and* continuously during work** (not just at creation) — on an
  oversubscribed `F:\_tmp`, the disk can run dry before any single project hits its cap, so both
  conditions are live.
- **Training shares the project `.venv`** and the **single-GPU job queue** (it competes for the
  16 GB; never co-loaded with an inference worker). A run writes its dataset copy, captions,
  trainer config, checkpoints, samples to `<project>/_temp/lora_<runid>/`. Base-model weights
  are **not** in the workspace — they live in the shared HF cache, read-only.
- **Rough size intuition** (for the cap): **finished render outputs are small** (a 5 s Hunyuan
  clip ≈ a few hundred KB — author). The real consumers are now the **P3 frame-sequence node
  masters** (R161 — lossless PNG, ~30–50× an mp4, **one per node version, never pruned**), plus
  **LoRA training temp** (image runs **~1–5 GB** each; a **video-LoRA** run **~20–80+ GB**), **draft
  videos**, and **per-version reference sets**. So size the work disk for **node masters + training
  temp + drafts**, not just final episodes. The default **250 GB cap** (R164, raised from 100 GB)
  holds a moderate project; a long multi-version episode or **video-LoRA training** can demand more
  (no max — raise per project), and the creation-time **footprint estimator** (R164) projects the
  masters' size up front.
- **Promote, then easy manual cleanup.** On training success the orchestrator copies the LoRA →
  the asset version's `lora/`, writes the training manifest (dataset hash, captions, base model
  + family, trigger token, trainer settings, output hash — per Codex), then **leaves temp in
  place** (no auto-prune). Cleanup is a deliberate **one-click "delete this run's temp"** (and a
  "delete all completed temp" sweep).

This belongs in **P0** because it shapes the workspace path config, the project-wide size cap +
hard-stop, and the queue's disk checks before any training/render UI exists.

---

## 5. The animation model (your third bullet)

This is the extended long-take loop from `kb-pipelines01.md`, wrapped in a timeline UI:

```
keyframe(start) ──► budget i2v sketch (ltxv)  ──► pick best draft
        ▲                                                │
        │                                       extract last frame
   polish / SR ◄── promote next keyframe ◄──────────────┘
        │
        └─► (optional) use chosen draft as wan2-animate DRIVING VIDEO
                      + canonical character image → identity-locked clip
```

Two animation tiers exposed in the UI, matching your bullet exactly:

- **Sketch tier** (your "budget, low-res i2v"): `ltxv i2v` over N seeds → a draft grid. Cheap,
  many attempts, used purely to find the motion/camera/timing.
- **Drive tier** (your "use sketches as wan2-animate driving video"): feed the chosen sketch
  as the driving clip + the asset's canonical image to `wan2 animate` for identity-locked
  final motion. `--retarget` when proportions differ.

The Take timeline (L3, §6.4) is literally the `take_001` JSON rendered as segment cards.

> **Codex comment - understanding:** The sketch tier searches motion cheaply; the drive
> tier spends compute only after motion has been selected. Chaining the extracted last frame
> into the next segment provides a practical authoring loop for longer sequences.
>
> **Concern / decision:** Verify the exact `wan2 animate` input contract before promising
> identity locking: driving-video retargeting, reference-image conditioning, supported frame
> counts, aspect ratios, and FPS must match the installed wrapper. Last-frame chaining also
> accumulates visual drift. The timeline should allow a segment to restart from a curated
> anchor keyframe, retain handles for overlap, and record frame extraction and crop settings.
> The cross-reference above should point to L3 `§6.4`, not `§6.5`.

---

## 6. UI design

> **Codex comment - understanding:** The UI is best treated as a set of editors over the
> same bundle rather than as separate applications. Selection, lineage, job status, and
> "open source record" navigation should behave consistently across workspaces.
>
> **Concern / decision:** Define the first user journey before filling every workspace with
> controls. The MVP should optimize one repeatable path: create project, define one style,
> generate one asset, compose one keyframe, create one sketch clip, and inspect lineage.

### 6.0 Design language

- **Three-pane desktop shell**: left rail (navigator/library), center stage (the active
  workspace), right inspector (properties of the selected thing). This is the articy:draft /
  DAW / game-editor convention; users already know it.
- **Dark, neutral, content-forward.** The generated imagery is the color in the room, so the
  chrome is desaturated graphite (`#1b1d22` panels, `#0f1115` stage, one accent — warm amber
  `#e0a062` — for primary actions and "on-model/approved" states).
- **Status as color, everywhere.** Each asset/shot/job carries a state dot:
  `idle ○ grey` · `queued ◔ blue` · `running ◑ amber pulse` · `done ● green` ·
  `needs-review ◆ violet` · `failed ✕ red`. Consistent across every workspace.
- **Everything is non-destructive.** Generations are variants; nothing overwrites. A
  "promote" action just marks a variant canonical and adds it to lineage.
- **The Job Queue is always visible** as a docked bottom strip (VRAM-aware, single active
  worker) — because on 16 GB you are always GPU-bound and need to see the one thing running.

> **Codex comment - understanding:** The consistent shell and always-visible queue are good
> choices for long-running local work. Non-destructive promotion fits the manifest-driven
> model and makes experimentation understandable.
>
> **Concern / decision:** Color cannot carry status alone; keep text or icon labels for
> accessibility. Separate lifecycle status (`queued`, `running`, `failed`) from editorial
> status (`draft`, `needs-review`, `approved`) so a completed generation is not confused
> with an approved asset.

### 6.1 Application shell

```
┌─ Loreweave Studio ── my-story ───────────────────────[ ⚙ ]─[ _ ▢ ✕ ]┐
│ ┌──────────┐                                                          │
│ │ ◆ WORLD  │   ← top-level workspace switcher (L1..L4 + Export)       │
│ │ ◆ ASSETS │                                                          │
│ │ ◆ SHOTS  │                                                          │
│ │ ◆ FLOW   │                                                          │
│ │ ◆ EXPORT │                                                          │
│ └──────────┘                                                          │
│ ┌────────────┬───────────────────────────────────┬─────────────────┐ │
│ │ NAVIGATOR  │            STAGE                    │   INSPECTOR     │ │
│ │ (library / │   (the active workspace)           │  (properties of │ │
│ │  tree /    │                                     │   the selection)│ │
│ │  graph     │                                     │                 │ │
│ │  outline)  │                                     │                 │ │
│ │            │                                     │                 │ │
│ └────────────┴───────────────────────────────────┴─────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ JOB QUEUE  ▸ running: wan2 animate seg_003  ▓▓▓▓▓░░ 62%  VRAM 14.1G│ │
│ │ queued(3): ltxv i2v ·  sd35 inpaint ·  trellis2 npc_character      │ │
│ └──────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

The five workspace tabs map 1:1 to the four layers + Export. The left rail's content changes
per workspace (file tree in Assets, graph outline in Flow, etc.); the shell stays put.

> **Codex comment - understanding:** The shell gives the user a stable spatial model while
> the stage changes. I would also use the inspector for lineage drill-down and a compact
> "open in originating workspace" action.
>
> **Concern / decision:** Add project-level signals that are not tied to a selected record:
> unsaved or externally changed files, invalid references, active bundle path, disk usage,
> and whether the orchestrator or SLM sidecar is connected. The app needs a clear degraded
> state when a service is unavailable.

### 6.2 WORLD workspace (L1 — Story Bible)

A form-plus-document hybrid. Left = section outline; center = the section editor; right =
a live "inheritance preview" showing what every downstream generation will inherit.

```
┌ NAVIGATOR ─────┐┌ STAGE: World ───────────────────┐┌ INSPECTOR ──────────┐
│ ▸ World        ││ ┌ Visual Style ───────────────┐  ││ Style propagates to:│
│ ▸ Visual Style ││ │ Palette  [■■■■■] + add        │  ││ • all image gens    │
│ ▸ Asset Classes││ │ Lighting [warm lamplight   ▾]│  ││   (prepended prompt)│
│ ▸ Naming/Folder││ │ Lens     [35mm, shallow DOF ]│  ││ • style LoRA target │
│ ▸ Story Spine  ││ │ Negative [no text, no logos ]│  ││                     │
│ ▸ Game Rules   ││ │ Moodboard [+drop refs]  ▦▦▦  │  ││ Preview prompt:     │
│                ││ └──────────────────────────────┘  ││ "warm lamplight,    │
│ [+ new section]││ Style ID: style_lantern_v3         ││  35mm shallow DOF,  │
│                ││ Applied by: 0 assets (new)         ││  <subject>, …"      │
└────────────────┘└───────────────────────────────────┘└─────────────────────┘
```

Sections (each a card the user fills in, all writing to `story.json` / `bible/`):

- **World** — long-form markdown editor (the world summary the SLM gets as system context).
- **Visual Style** — palette swatches, lighting/lens dropdowns, global negative constraints,
  a moodboard drop zone. Produces a `style_id` and a **style prompt fragment** prepended to
  every image generation, plus an optional **style-LoRA** target.
- **Asset Classes** — your list (landscape, foliage, buildings, ruins, roads, rivers,
  characters, props…). Each class is a card defining: default pipeline, default TRELLIS preset
  (`background_prop`/`interactive_prop`/`npc_character`/… from `kb-trellis2.md`), default
  consistency mechanism (LoRA vs plate vs sheet), and prompt scaffolding.
- **Project Settings** — set **at project creation, no overrides** (round-5 answer 5):
  **aspect ratio `[X]:[Y]` and resolution `[W]×[H]` with the aspect→resolution scale *locked*
  together** (change one, the other stays consistent), plus **fps** and **audio format**.
  **Defaults track Wan2.2's native output** (author, round-6 answer 7), since Wan is the final
  video pipeline — i.e. **1280×720 (16:9) 720P landscape**, fps **24 or 30** (Wan Animate is
  30fps; TI2V-5B is 24fps and 1280×704). Presets include **832×480 (480P)** for low-VRAM drafts
  and **upscaled targets** (Real-ESRGAN 4× → e.g. 5120×2880) when the postproc upscaler (§8.3)
  is in the chain. **Audio format default = a high-fidelity master, uncompressed WAV/AIFF,
  48 or 44.1 kHz, 16/24-bit, stereo** (author, round-7 answer 6 — they're a musician and want a
  proper master for finishing elsewhere), **subject to what the lip-sync module can emit** (many
  output 16 kHz mono, so loom up-samples/normalizes voice clips to the project master format).
  These gate clip Finalize (§6.4) and bucket every generation's width/height.
- **Project format is global — applies to *all* nodes and episodes, no per-item overrides
  (author, round-8 answer 6).** You cannot have one node 16:9 and another 9:16 in the same
  project; **a different aspect ratio means a *separate project*.** Two exceptions: **(1) image
  assets** are **cropped to the master size** when used (they can be authored at any size), and
  **(2) draft videos** keep the project **aspect ratio** but run at the **model's cheapest
  supported bucket** for that aspect (round-9 answer 6 — drafts are for motion search, not final
  quality). Finals always match the master.
- **Voice "sample text"** (round-9/10/11) — **one built-in default English passage for v1**
  (round-11 answer 1; custom/per-character passages are post-v1). Every character records the
  **same passage** (better for clone consistency); the user reads it **elsewhere** and **imports**
  the audio (loom does not record, §8.3). The recorded **sample is per character**; validation is
  minimal.
- **Work-disk size cap, set at project creation (round-9 answers 4, 5).** Every project gets a
  **per-project workspace size cap** — **default 250 GB (R164, raised from 100 GB to fit R161
  PNG-seq masters), minimum 50 GB, no maximum**, with a **creation-time footprint estimator** (R164). At creation
  loom **validates that the work disk has at least the cap in free space** and **requires an
  empty destination folder** (warns if not). It then **polices both the project size and the disk
  free space continuously** — **warn at <5% headroom, hard stop at <2%** (round-11 answers 4, 10;
  §4.2). File management is otherwise **manual** — no project manager in v1.
- **Naming & Folder Layout** — a template builder (`{class}/{name}/{version}/...`) with a live
  preview of resulting paths. Drives §3.1.
- **Story Spine** — premise, main arc, factions, key NPCs, player goals. NPCs here
  auto-create **stub AssetProfiles** in L2 and are selectable as speakers in L4. A *stub* is
  just a name + a **reusable prompt-template snippet** on its first version (text), no images
  and no LoRA yet (training is strictly P2). **No auto-update (author, round-6 answer 6):** once
  a stub exists, editing the spine NPC **never silently rewrites the profile** — instead loom
  offers a **manual "re-sync from spine"** action, so hand-edits are never clobbered. That
  snippet is **injectable into later prompts**, **version-aware** (§3.4), via a **structured or
  explicit** mechanism — never automatic (round-5 answer 7): a **picker/dropdown** that inserts
  `asset@version`'s snippet, or an **explicit token** like `@Mara@v2` you type and expand.
  Selecting a node's speaker does *not* silently prepend anything.
- **Game Rules** (optional) — variables, flags, win/lose conditions; these become the
  condition vocabulary the Flow editor (L4) can branch on.

Design intent: filling in L1 is *cheap and front-loaded*, and it visibly constrains
everything downstream (the right pane always shows "what this changes"). This is what makes
the later generation steps consistent instead of ad-hoc.

> **Codex comment - understanding:** WORLD edits define inherited defaults, while assets
> and shots should be able to override them deliberately. The inheritance preview is the
> important part: it makes an otherwise hidden prompt-building process visible.
>
> **Concern / decision:** Version inherited values. A global style change should not mutate
> the reproducibility of an existing asset or shot; it should mark descendants as based on
> an older style revision and offer regeneration. NPC stub creation also needs merge rules
> so editing a spine entry does not overwrite a richer AssetProfile.

### 6.3 ASSETS workspace (L2 — Asset Studio)

The heart of the tool. Left = library tree grouped by class with status dots; center = the
selected asset's **profile + variant gallery + generation bar**; right = inspector with the
prompt/refs/LoRA/consistency controls.

```
┌ LIBRARY ───────┐┌ STAGE: Asset · "Mara" (character) ●on-model ┐┌ INSPECTOR ─────────┐
│ characters     ││ ┌ Variant gallery ─────────────────────────┐ ││ Prompt template    │
│  ● Mara        ││ │ ▣canon ▣  ▣  ▣  ▣   [pose▾][expr▾]        │ ││ "<style>, Mara, a  │
│  ◑ Joren       ││ │  ★      ☆  ☆  ★  ☆   ← ★=in LoRA set      │ ││  weathered cartog…"│
│  ○ Innkeeper   ││ └──────────────────────────────────────────┘ ││ Trigger: mara_lw   │
│ props          ││ ┌ Generate ────────────────────────────────┐ ││                    │
│  ● Brass map   ││ │ pipeline[zimage▾] mode[t2i▾] seed[rand]   │ ││ Reference set: 23  │
│  ◑ Lantern     ││ │ refs:[Mara/refs ▾]  count[4]  [Generate ⏎]│ ││ LoRA readiness:    │
│ scenes         ││ └──────────────────────────────────────────┘ ││  ▓▓▓▓▓▓▓░ 23/30    │
│  ● Workshop    ││ Actions: [Promote★][Add to LoRA set]         ││  [Train LoRA ▶]    │
│  ○ Market      ││          [Inpaint][Polish][→3D][→Shot]       ││ LoRA: mara_lw.safe │
│ [+ asset]      ││                                              ││  weight[0.8] ●ready│
└────────────────┘└──────────────────────────────────────────────┘└────────────────────┘
```

Key interactions:

- **Generate bar** picks pipeline/mode/seed/count and fires a job. Style fragment from L1 is
  auto-prepended (shown greyed in the prompt). Results stream into the variant gallery as the
  job completes.
- **Promote ★** marks a variant canonical. **Add to LoRA set** tags it for training (the star
  badges in the gallery). The **LoRA readiness** meter fills as the curated set grows;
  **Train LoRA** unlocks past a threshold and queues training (`zimage`/`sd35` per
  `kb-pipelines01.md`). This is the "build enough images → train a LoRA" answer made literal.
- **Per-variant actions** map straight to pipeline modes: Inpaint (`sd35`/`zimage`), Polish
  (source-matched img2img `strength≈0.22`), **→3D** (queues `trellis2` with the class's preset →
  GLB in `assets/.../glb/`), **→Shot** (drops the asset into the Shot composer as a layer).

> **Note — `→3D` / `trellis2` are P6 (R128).** All 3D was deferred to P6, so the `→3D` button and any
> `trellis2` job shown in the queue mockups below are **disabled/greyed until P6**. They're drawn
> here to show the eventual shape, not a v1/MVP feature.
- **Version selector** (top of the stage, §3.4): the profile's versions with the **active** one
  starred and each showing a **lock badge when finalized**. Everything below — gallery, ref set,
  LoRA, prompt snippet, voice — is **scoped to the selected version**, and is **read-only once
  that version is finalized** (edit → make a new version). **[+ new version]** **copies
  everything** from the chosen parent (`derived_from`) as a frozen baseline, then you tweak only
  what differs — "same character, now with a mustache" in a couple of clicks. Because there will
  be **many** versions (small tweaks), the selector supports **grouping/search/naming**, not a
  flat list. Significant, independent changes → **[+ new profile]** instead.
- **Consistency selector** (inspector) per version: `LoRA · Plate · Sheet · ControlNet`,
  defaulting from the asset class. Scenery defaults to *Plate + style-LoRA*; characters to
  *Reference→LoRA*.
- A **Sheets** sub-tab generates turnaround / expression / pose sheets to seed the ref set —
  the fastest way to reach LoRA-readiness for a character version.
- **Export / Import an AssetProfile (author, round-7 answer 8).** A profile can be **exported
  with all its versions included** (refs, LoRAs, voice, GLBs, manifests) as a portable bundle,
  and **imported into another story** — so you can reuse a character/prop across projects without
  rebuilding it. Preferred over heavyweight project templates (too space-hungry to insist on):
  portability lives at the **asset** granularity. **Import always creates a *new* profile — no
  merging (author, round-8 answer 1);** if a same-named asset already exists, the import
  **must be renamed** (loom prompts for a new name on collision).

> **Codex comment - understanding:** This workspace is the main production loop: generate,
> compare, curate, derive, and promote. Per-variant actions should create new jobs whose
> manifests point back to the selected source variant.
>
> **Concern / decision:** "Results stream into the gallery" should mean job records appear
> immediately and artifacts appear only after an atomic completion step. Define whether an
> asset has one canonical image or canonical images by role, such as portrait, full-body,
> side view, expression, and proxy source. A single canonical image is unlikely to serve
> LoRA training, compositing, and animation equally well.

### 6.4 SHOTS workspace (L3 — Scene / Shot composer + Take timeline)

Two linked views: a **compositor** (build the start keyframe from asset layers) and a **take
timeline** (the animation loop). Toggle with a tab; they share the right inspector.

Compositor view:

```
┌ SCENE TREE ────┐┌ STAGE: Shot s2_03 · compositor ─────────────┐┌ INSPECTOR ─────────┐
│ Workshop       ││  ┌ canvas (16:9) ───────────────────────┐   ││ Layers (top→bottom)│
│  ▸ s2_01       ││  │   [ background plate: Workshop ]      │   ││ ▤ Mara (char,LoRA) │
│  ▸ s2_02       ││  │        ╭─Mara─╮     ▢ Brass map       │   ││ ▤ Brass map (prop) │
│  ● s2_03  ◀     ││  │        │      │                       │   ││ ▤ Plate: Workshop  │
│  ▸ s2_04       ││  │        ╰──────╯                       │   ││ Camera: med, dolly │
│ Market         ││  └───────────────────────────────────────┘   ││  left                │
│  ▸ m1_01       ││  [Compose keyframe ▶]  [Depth proxy…]         ││ Continuity (SLM):  │
│                ││  derived: depth ▦  canny ▦  silhouette ▦      ││  end_pose, props…  │
└────────────────┘└───────────────────────────────────────────────┘└────────────────────┘
```

- Layers come from L2 (character alpha, prop sheets, background plate) — the **Layered Asset
  Strategy**. Compose → flatten → optional inpaint/polish on seams → that's the shot's
  start keyframe.
- **Depth proxy…** opens the TRELLIS proxy-miniature flow (`kb-pipelines01.md`): drop GLBs +
  primitives, author a camera path, render the depth/silhouette/canny/RGB pass bundle for
  ControlNet-locked keyframes or future LTXV control.

Take timeline view (the animation loop, = `take_001` JSON as cards):

```
┌ STAGE: Shot s2_03 · take timeline ───────────────────────────────────────────┐
│ seg_001                seg_002                seg_003                          │
│ ┌───────────┐  ▶clip   ┌───────────┐  ▶clip   ┌───────────┐                   │
│ │start KF ▣ │────────► │start KF ▣ │────────► │start KF ▣ │   [+ segment]     │
│ │drafts ▦▦▦▦│ pick ★   │drafts ▦▦▦ │ pick ★   │drafts …   │                   │
│ │ ↳last frame│ promote  │ ↳last frame│ promote │           │                   │
│ │final ▶ wan │          │final ▶ wan │         │           │                   │
│ └───────────┘          └───────────┘          └───────────┘                   │
│ Draft tier: [ltxv i2v ×4 ▶]   Drive tier: [wan2 animate (driving=★draft) ▶]   │
└───────────────────────────────────────────────────────────────────────────────┘
```

Each segment card shows start keyframe → draft grid (sketch tier) → selected draft →
extracted last frame → promoted next keyframe → final clip (drive tier). "Pick ★" selects the
draft whose motion you want; "promote" pushes its last frame to the next segment's start
keyframe — the chained long-take. The SLM director (inspector) maintains the shot-state JSON
and writes the next segment's continuity prompt (end pose, camera, props) automatically.

> **Frame-accurate continuity is a within-node generation concern (author, round-4 answer 3).**
> A single I2V clip caps around ~5 s, too short for real beats, so a node's longer clip is built
> by **chaining segments: the exact last frame of one segment becomes the first frame of the
> next generation** (`image_cond_noise_scale=0` first-frame lock, exact last-frame extraction,
> optional short overlap/optical-flow morph, per-join continuity check). The expectation here is
> a **pixel-identical handoff** — one unbroken shot. This is the make-or-break R&D of L3 (P3).
>
> Crucially, **this is the *only* place pixel-identical continuity is expected.** It applies
> *within* a node's clip, between generated segments. It does **not** apply between two finished
> clips on the episode render list — those always get a deliberate **video transition** (§6.7).
> If a within-node pixel-identical join can't be achieved for some segment pair, the fallback is
> to **split it into two separate clips and join them with an episode-level transition**, not to
> fudge a seam inside one continuous shot.
>
> **Finalize clip (+ voice audio).** When the chain looks right, a **Finalize** step bakes the
> node's clip plus its **node-level voice track** (imported/cloned voice, lip-synced, §8.3).
> **v1 has no SFX, music, or episode mix** (round-11 answer 5) — voice is the only audio.
> **Finalize gate (author, rounds 3–4):** the only hard checks
> are that the clip matches the **project-level settings — resolution, frame rate, aspect ratio,
> and audio format** (defined once at project level, **no per-scene overrides**); everything
> else is **eyeball approval**. After Finalize there is no more *video* generation for that node.
>
> **Node versioning — parallels Asset Profile versioning (author, round-7 answer 5).** Editing a
> node's settings doesn't overwrite its clip — it produces a **new node version**, and **each
> node version has its own clip(s)**. The act of **generating a quality clip from a node's
> changes is what finalizes those changes into a new version** (so an unrendered edit is a
> *pending* change, not yet a version). All versions are **retained unless explicitly marked for
> deletion** (no auto-pruning). The **episode render list picks a specific node version's clip**
> (§6.7), so you may deliberately use an **older** version's clip if you prefer it. If a node has
> **unrendered changes** (edited since its last clip), loom shows a **light "may be out of date"
> warning** on that node and on any episode entry using it — **not a hard block**: the
> already-rendered version on the list stays usable. (Within a node version, the clip itself is
> still built by the frame-accurate chain above and gated by the project format.)

> **Codex comment - understanding:** The compositor creates controlled anchor images; the
> take timeline turns anchors into clips while preserving the decision history for each
> segment. This division is clear and should be maintained in the data model.
>
> **Concern / decision:** A canvas compositor needs explicit transforms, masks, blend
> settings, z-order, source-variant IDs, and output resolution in `shot.json`, not only a
> flattened image. Clarify whether the SLM proposes continuity fields for approval or edits
> them automatically. For production use, author-approved values should win and automated
> suggestions should be auditable.

### 6.5 FLOW workspace (L4 — Narrative graph, the articy:draft layer)

A node-graph canvas (React Flow-style). Left = flow outline + variable list; center = the
graph; right = selected node inspector. This is what makes it "work like articy:draft."

```
┌ FLOW OUTLINE ──┐┌ STAGE: Flow · "Act 1 — The Workshop" ───────────┐┌ NODE INSPECTOR ────┐
│ ▸ Act 1        ││                                                  ││ Type: Dialogue     │
│   • Arrival    ││   ┌─Hub─┐    ┌──────────┐  yes  ┌──────────┐     ││ Speaker: Mara ▾    │
│   • The Map    ││   │Start│──► │Dialogue  │──────►│Condition │     ││ Shot: s2_03 ▾ ▣    │
│   • Choice     ││   └─────┘    │ Mara:"…" │       │ trust>2? │     ││ Lines:             │
│ ▸ Act 2        ││              │ [shot ▣] │       └────┬─────┘     ││  "You found it."   │
│ Variables      ││              └──────────┘         no │           ││ Choices:           │
│  trust = 0     ││                   │ choice            ▼           ││  [+ choice → node] │
│  has_map=false ││                   ▼              ┌──────────┐     ││ On enter:          │
│ [+ variable]   ││             ┌──────────┐         │ Outcome  │     ││  set has_map=true  │
│                ││             │ Choice×2 │         │ flee     │     ││                    │
└────────────────┘└──────────────────────────────────────────────────┘└────────────────────┘
```

Node palette (articy parity + generative twist):

| Node | Purpose | Generative hook |
| --- | --- | --- |
| **Start / Hub** | entry / branching junction | — |
| **Dialogue** | a line/exchange | speaker = AssetProfile; **owns its clip** (the visual for this beat) |
| **Choice** | player options (1..n outgoing) | each choice edge can require/set variables |
| **Condition** | branch on variables/flags from Game Rules | reads L1 rule vocabulary |
| **Action/Instruction** | mutate variables, grant items, move time | — |
| **Shot/Scene** | pure visual beat | **owns its clip** (1..N frame-continuous shots); previews inline |
| **Jump** | non-linear link to any node | enables non-linear timelines |
| **Comment** | author notes | — |

- **Every node OWNS its clip — no reusable shots (author, answer 7).** A node is the single
  owner of its visual; there is no shared/reference shot pool. This keeps "this exact beat"
  editing simple and the ownership graph unambiguous. (Trade-off accepted: revisiting a
  location means a new node with its own clip, not reuse.)
- **A node may own *multiple* shots chained with frame-accurate continuity (answers 7 & 2).**
  Because a single I2V clip caps around ~5 s, a node's "clip" can be a **frame-continuous chain
  of shots** built in the L3 take timeline (§6.4) — last-frame-exact handoff between shots — so
  one beat can run well beyond 5 s as one seamless clip.
- **Nodes can branch *or run in parallel* (answer 8).** The graph supports parallel branches,
  not only either/or choices — concurrent threads the author later **picks between when
  hand-composing the episode** (L5, R148 — the episode is a curated cut, not an auto-linearization).
- **A node pins specific asset *versions* (§3.4).** Its speaker/props/scene are referenced as
  `asset@version` (e.g. `chr_7f3a@v2_red-hair`), so the beat records exactly which look was used.
  Re-pinning to a newer version is an explicit edit; earlier nodes keep their older pins — this
  is how the storyboard stays correct as the world changes over time.
- **A node has *versions*, each with its own clip (§6.4, round-7 answer 5).** Editing a node
  creates a new version; rendering a quality clip is what commits that version. L5 picks **which
  node version's clip** to use (an older one is fine); a node with unrendered edits shows a light
  **"may be out of date" warning** but its rendered version stays usable. The chosen clip carries
  its flattened clip-level audio, so L5 assembly is stitching, never generation (§6.7).
- **Variables & conditions** come from L1 Game Rules; the inspector offers them as typed
  dropdowns, so branches stay valid.
- **Play mode**: a "▶ Play from here" walks the graph, resolving conditions against a sandbox
  variable state, showing each node's clip/**authored** dialogue in a reader pane. **No ad-hoc AI
  voicing (R143)** — a node shows authored dialogue or nothing; the SLM is never auto-invoked at
  play time (it shares the single GPU, R141).
- **Jump nodes + multiple outgoing Choice edges (+ parallel threads)** are what give you the
  non-linear, branching timeline articy is known for.
- **v1 is a single flat graph (author, round-8 answer 7).** Keep it simple: one flat node graph
  with **Jump nodes** for structure — **no act/scene/chapter grouping** (folders of nodes) in
  v1. Hierarchical grouping is a post-v1 nicety; until then the Flow outline lists nodes flat,
  and Jumps stand in for chapter links. (The "Act 1 / Act 2" outline above is illustrative of a
  *later* grouping feature, not v1.)

> **Codex comment - understanding:** FLOW is a typed narrative runtime editor, not merely a
> diagram. The author defines state transitions; play mode interprets the same records that
> export will consume. Binding shots to nodes makes the graph useful as a storyboard.
>
> **Concern / decision:** Specify graph validation early: exactly one entry point per
> playable flow, reachable-node checks, missing targets, cycles, choice ordering, variable
> types, and mutation semantics. A monolithic `graph.json` will merge poorly in git; consider
> one file per flow, act, or node plus a rebuildable layout/index file. Live SLM dialogue
> should be opt-in and clearly separated from authored canonical dialogue.

### 6.6 Job Queue (cross-cutting dock)

Always-docked bottom strip; expandable to a full panel. This is the single most important
operational surface on a 16 GB GPU.

```
┌ JOB QUEUE ── 1 running · 3 queued · VRAM 14.1 / 16.0 GB ──────────[ ▴ expand ]┐
│ ▶ RUNNING  wan2 animate · s2_03/seg_003  ▓▓▓▓▓▓░░ 62%  06:120 frames  [logs][✕]│
│ ◔ QUEUED   ltxv i2v · s2_03/seg_004 ×4                          est 2m  [▲][✕] │
│ ◔ QUEUED   sd35 inpaint · Mara variant 5                        est 40s [▲][✕] │
│ ◔ QUEUED   trellis2 npc_character · Joren                       est 3m  [▲][✕] │
│ recent: ● zimage t2i Mara ×4 (28s) · ✕ hunyuan i2v (OOM→retry group-offload)  │
└────────────────────────────────────────────────────────────────────────────────┘
```

- **Single active GPU worker** (the subprocess isolation rule). Queue is reorderable;
  CPU-only steps (extract last frame, compose layers, EDL export) can run alongside.
- **VRAM estimate per job** drives admission: a job that won't fit triggers a suggested
  offload mode (`group`/`sequential`) rather than OOM-failing — and the recent-list shows the
  auto-retry, mirroring the Hunyuan/Wan offload notes in `kb-pipelines01.md`.
- Each job links to its **manifest + stdout log** and the record (asset/shot/node) that
  requested it — the lineage edge.
- **The queue is persistent — quit anytime, resume where you left off (author, round-9
  answer 3).** Queued tasks are **durable**: they survive shutdown and are still there on
  relaunch. A job that was **mid-run when you quit** has its **incomplete output discarded** (an
  in-progress generation is not "complete"), but the **task stays queued** and re-runs on
  return. So "lose anything not complete" applies to *partial outputs and uncommitted edits*
  (R69), **not** to the queue itself — the plan of work persists.
- **On relaunch the queue resumes *paused* (author, round-10 answer 5).** loom does **not**
  immediately start spending GPU on reopen. It shows the pending tasks and lets you
  **review/cancel** first; you **unpause** when ready. This avoids a surprise batch firing the
  moment you open a project.

> **Codex comment - understanding:** The queue is both scheduler and operational history.
> GPU-heavy jobs run serially, while bounded CPU tasks may run concurrently. Each adapter
> translates a typed job into one CLI invocation and converts its result into a normalized
> completion record.
>
> **Concern / decision:** VRAM estimates are heuristic, so persist observed peaks per
> pipeline configuration and improve estimates over time. Define cancellation, timeout,
> stale-running-job recovery after restart, disk-space checks, log retention, retry limits,
> and whether CPU jobs also have concurrency limits. Auto-retry should be visible and capped.

### 6.7 EPISODE / EXPORT workspace (L5) — the headline deliverable

This is where the platform earns its keep — **video assembly is select-and-stitch, not
generation** (answer 8). Each node owns one-or-more **versioned clips** with **clip-level audio
flattened in** (§6.4). The render phase **sequences a chosen clip per node** with simple
transitions. No *video* regeneration happens here. loom stays a **video/story tool, not an
after-effects tool** — heavy picture finishing belongs in DaVinci Resolve.

- **Pick *which version* of each node's clip (round-7 answer 5).** The render list lets you
  select a specific node version's clip — including an **older** one if you prefer it. A node
  with **unrendered edits** shows a light **"may be out of date"** flag on its list entry, but
  the listed clip is **still usable** (no block). A render thus **references** specific clip
  versions (lean, not a frozen copy); the per-node version warning is how staleness surfaces.
- **Episode length is arbitrary — driven by the render list, hard-bounded by disk (round-7
  answer 7; round-8 answer 5).** The ~30 min was only an example; an episode runs from **~1 s
  up**, as long as the node sequence. The **per-project work-disk cap is a hard stop** (§4.2,
  default 250 GB, R164): as the **whole project** approaches the cap, loom **blocks** further
  space-consuming work; **raising the cap or freeing space resumes.** Note the **final render
  output is *small*** (author: a 5 s Hunyuan render ≈ a few hundred KB), but the disk budget is
  **now dominated by the P3 frame-sequence node masters** (R161 — lossless PNG, kept per version),
  alongside draft videos, per-version reference sets, and LoRA training temp — **not** the finished
  episodes themselves.
- **Keep all render outputs unless deleted (round-9 answer 8).** Each render's `out.mp4` (+
  stems/EDL) is **retained** — they're cheap. loom never auto-deletes them; you remove renders
  manually.

> **v1 audio scope — drastically simplified (author, round-11 answer 5).** No mixing in loom for
> v1. **Episode-level audio is *dropped*** (no music bed, no mixer); **SFX and music are out of
> v1 entirely.** The **only** audio loom handles in v1 is **node-level voice** — the
> generated/imported voice that drives lip-sync (§8.3). So a clip carries one **voice** track and
> nothing else; the episode is just those voice-bearing clips in sequence. Music/SFX/mixing are
> done later in Resolve/a DAW, or in a post-v1 loom. (The mixer mockup below is retained as a
> *post-v1* reference, greyed out for v1.)

- **Select the main storyline = pick a linear node sequence (author, round-3 answer 7).** In
  the graph, nodes are **independent entities** — many can exist "at the same time" (parallel
  threads), articy-style. The episode render sequence is where you **explicitly order which
  clip plays after which**, and **exactly one clip occupies each point in the sequence — no
  split-screen, no multiple simultaneous projections.** Parallel graph threads are alternatives
  you choose *between*, not compose together.
- **A render is a saved record — and you can have many (author, round-6 answer 5).** Each render
  (`renders/<name>/`, §3.2) stores its **node order, chosen per-node clip version, and per-join
  transitions**, plus **its own manifest**. Renders **all share the one project format**
  (aspect/resolution/fps/audio — round-8 answer 6); they differ in **which nodes/clip-versions
  are included and the transitions** — e.g. a tight pitch cut vs. an extended cut. (A different
  aspect ratio, like a 9:16 short, is a **separate project**, not another render. Episode-level
  audio/score is out of v1 — round-11 answer 5.) The render list is *lightly authored* state.
- **Basic video editing only — and every join is a transition (answers 2, 8; round-4 answer 3).**
  A *deliberately small* toolset ties clips together: **reorder, hard-cut, cross-fade, dissolve,
  fade-to-black**. The episode list **always expects some form of transition between adjacent clips**
  — it never assumes a pixel-identical seam (that lives only *within* a node's clip, §6.4). Nothing
  more — no compositing, keying, grading, multi-track VFX.

> **Refined for v1 by R148/R157 (round 21–22).** The episode is a **hand-built render list,
> *decoupled* from the graph** (R148 — it can differ substantially from the narrative). The editor
> is a **single-video-lane timeline** (R157): drag clips to reorder; **each junction is a transition
> rendered as a draggable overlap zone** (default **hard cut**, R149; non-cut consumes an overlap
> window with a per-transition **trim-into-content / hold-freeze** toggle). It is **not** a multi-lane
> NLE — R29 (one clip per point) makes extra lanes pointless. The simple "ordered strip" mockup below
> is the earlier sketch; see `kb-loom-p5.md` §5 for the timeline. Export adds **chapter markers**
> (R158).
- **Audio (v1) = node-level voice only.** Each clip carries its **voice** track (from imported
  recording or cloning TTS, lip-synced, §8.3); the episode plays those in sequence. **No episode
  bed, no mixer, no SFX, no music** in v1 (round-11 answer 5).

```
┌ STAGE: EPISODE · "Act 1" ── order clips (one at a time) · transitions ──────────────┐
│ order:  ┌Arrival─┐╱┌The-Map──┐╱┌take────┐│┌End──┐    every join = a transition        │
│ VIDEO   │■ final ││ │■ final  ││ │■ final ││■ fin │   per-join:[cut▾][x-fade 0.5s]     │
│ VOICE   │▓ Mara ▓│ │         │ │▓ Mara ▓│        │   (node-level voice only, v1)       │
│ ░ post-v1: episode music bed + mixer (gain/volume/pan) — not in v1 ░                  │
│ [Render preview ▶]   [Export → Resolve (file-based)]   [Pitch one-pager (Muse) ▶]     │
└────────────────────────────────────────────────────────────────────────────────────┘
```

- **DaVinci Resolve handoff — simple file-based, latest *free* Resolve as baseline (author,
  round-3 answer 3).** loom neither drives Resolve's Python API nor generates a Resolve project
  to script it. It just **exports standard files** (EDL/XML/AAF + per-node media + the
  **voice audio**) that the author **imports manually** into Resolve; **import/export stay plain
  file operations.** (Free Resolve is the target — nothing relies on Studio-only features.)
  *(Post-v1, when SFX/music exist, export them as separate stems — dialogue/SFX/music — per
  round-10 answer 7; for v1 there is only the voice/dialogue track.)*
- **Extensibility = an effect plugin API, not Lua (corrected per round-4 answer 5).** Letting
  power users add their own video/audio effects beyond the built-ins is a **P6+ nicety** via a
  small **effect plugin API in Python or JS/TS** (the author agrees a real plugin API beats
  Resolve-style Lua). Not core; parked until the basics are solid.
- **Engine/DCC export = both assets *and* graph, but explicitly not the main goal (author,
  round-5 answer 8).** Long-term loom should export **GLBs + textures** to **Unreal 5.7+ /
  Blender** *and* the **story graph** (a data table / dialogue tree the engine can read). But
  the author is "drifting away from this being the main goal," so it stays **low-priority/
  long-term (P6+)**. The only thing this asks of us *now* is to keep `flow/graph.json`
  reasonably **engine-neutral** (stable IDs, typed nodes/edges) so a future exporter is
  feasible — not to build the exporter. No articy round-trip (loom is a replacement).

> **Codex comment - understanding:** EXPORT materializes a selected view of the bundle for
> another consumer: an editorial render, a DCC or engine asset handoff, or a narrative
> runtime package.
>
> **Concern / decision:** Export targets need explicit versioned profiles. An MP4 path is
> deterministic only after choosing a flow path and variable state. Engine export needs a
> documented coordinate system, units, texture packing, naming policy, and overwrite rules.
> Treat articy round-trip as a separate compatibility project until its supported subset is
> defined.

---

## 7. SLM integration — the assistant layer ("Muse")

The author wants the SLM used **everywhere, like Copilot in VS Code** (answer 4): inside
generation processes, supporting the user through the UI, and — eventually — acting
agentically to carry out tasks. This section explores that the way §6 explored the UI. The
assistant is branded **Muse**; it runs as the `llama-server` sidecar from `kb-slm.md`
(Qwen3-1.7B for fast tool-calling/dispatch, SmolLM3-3B for creative/dialogue).

### 7.0 Philosophy: one assistant, three modes

Muse is a single context-aware assistant exposed in three escalating modes — the same mental
model VS Code Copilot uses (completion → chat → agent):

| Mode | Analogy | What it does | Autonomy |
| --- | --- | --- | --- |
| **Inline assist** | Copilot ghost-text | suggestions *inside fields*: finish a world paragraph, propose a prompt clause, draft a line of dialogue, name an asset | zero — author accepts/rejects a suggestion |
| **Chat dock** | Copilot Chat | a side panel that can *see the current selection + bible* and answer/brainstorm/rewrite, and **propose** actions | low — proposes, author runs |
| **Agent mode** | Copilot agent | given a goal ("draft Act 1 from the spine", "bootstrap Mara to LoRA-ready"), it produces a **plan of jobs + edits**, the author approves, then it drives the orchestrator | gated — plan → approve → execute → audit |

Hard rule (and a direct answer to a Codex concern): **Muse never writes bundle records or
launches GPU work directly.** Every agent action goes through the *same typed, atomic,
orchestrator-owned write API and the same job queue* as a human click, so everything Muse does
is logged in lineage and is undoable. Agent mode is **propose-and-approve by default**.

### 7.1 Where Muse plugs into each workspace

| Workspace | Muse uses (generation) | Muse uses (authoring support) |
| --- | --- | --- |
| **WORLD (L1)** | derive style prompt-fragments from the moodboard prose; suggest asset classes; extract game-rule variables from the spine text | expand/critique the world doc; check the spine for plot holes |
| **ASSETS (L2)** | **write the Stage-B expansion prompt list** (§4.1); caption images for LoRA; propose negative prompts; suggest a trigger token | name assets; summarize a character into a bible blurb |
| **SHOTS (L3)** | **continuity director** — maintain shot-state JSON, emit each next-segment prompt (already specced in `kb-pipelines01.md`); break a scene into a shot list; suggest camera moves | describe a shot in words → seed the compositor |
| **FLOW (L4)** | draft dialogue for a node; propose branch/choice options and outcomes *(no ad-hoc play-mode voicing — R143)* | validate the graph (dead ends, unreachable nodes, unused variables) |
| **EPISODE / EXPORT (L5)** | write the episode synopsis, logline, chapter markers; draft subtitle text from dialogue nodes | generate a pitch one-pager from the bible + main path |

The two highest-value uses today are the **Stage-B expansion prompt list** (makes the
character-bootstrap loop usable) and the **continuity director** (makes the animation loop
usable). Both are text-only and already within the SLM's wheelhouse.

### 7.2 Agent mode — how a goal becomes work

Agent mode is tool-calling over the orchestrator's API. `kb-slm.md` notes Qwen3/SmolLM3
support function-calling + GBNF/JSON-schema-constrained output, so Muse emits a **structured
plan** the UI renders for approval before anything runs:

```
┌ MUSE · agent ──────────────────────────────────────────────[ approve all ▸ ]┐
│ goal: "Bootstrap 'Mara' to LoRA-ready"                                        │
│ plan (7 steps, 4 GPU jobs · est 11m · ~1.8 GB scratch):                       │
│  1 ✎ create AssetProfile chr_? "Mara" (class: character)        [edit][skip]  │
│  2 ⚙ multi t2i casting ×8  (flux2+zimage+sd35, +clean/polish)   [edit][skip]  │
│  3 ⏸ WAIT for you to pick the hero ★                            (gate)        │
│  4 ✎ SLM expansion prompt list ×24 (style/world fixed)          [edit][skip]  │
│  5 ⚙ img2img+inpaint expansion ×24  →  6 ⚙ video-sketch harvest [edit][skip]  │
│  7 ✎ open Curation; train when readiness ≥ threshold (isolated) (gate)        │
└───────────────────────────────────────────────────────────────────────────────┘
```

- Steps marked **⚙** enqueue jobs (queue-governed, single GPU worker); **✎** are typed edits;
  **⏸/gate** pauses for a human decision (pick the hero, approve the curated set).
- The author can edit/skip any step before approving — agent mode is a *planner*, not an
  autopilot. This keeps the author in the director's chair while removing busywork.
- **No auto-execute for now (author, answer 7).** The explicit worry is an agent quietly
  spending GPU/compute on a big batch. Two mitigations: (1) **propose-and-approve is the only
  mode** until experience says otherwise — raising the autonomy ceiling is a later, separate
  decision; (2) even an approved plan runs **through the single-GPU job queue one job at a
  time**, and the plan shows an up-front **cost estimate** (job count · est time · scratch GB),
  so compute is always bounded and visible, never a runaway.

### 7.3 Tool surface (what the orchestrator exposes to Muse)

A small, typed function set mirrors human capabilities exactly (no privileged backdoor):

`create_asset` · `update_record` · `queue_generation(pipeline, mode, params)` ·
`queue_training(asset)` · `add_flow_node` / `connect_nodes` · `set_variable` ·
`read_record` / `list_records` / `read_manifest`. Outputs are JSON-schema-constrained (GBNF)
so a malformed call can't reach the orchestrator. Every call is attributed to "Muse (agent)"
in lineage so an audit can separate human and assistant authorship.

### 7.4 Modular model registry, budget, and the two real limits

**Muse is modular — different models for different jobs** (author, answer 5). The orchestrator
keeps a **model registry** mapping *roles → models*, each a swappable `llama-server` GGUF, **already
on disk** in `src/village_ai/models/`.

> **⚠ Superseded for v1 by R144 (round-19+).** The exploratory 3-model split below is **collapsed to
> TWO models for v1**: one generative VLM — **`Qwen3-VL-4B-Instruct` Q8** — serves *all* generative
> roles (dispatch + creative + vision-describe), plus **`Qwen3-VL-Embedding-8B` Q8** for
> retrieval/scoring (Reranker optional). The registry stays modular so a heavier creative model can
> be slotted later. See `kb-loom-p4.md` §11. Below is kept for the role taxonomy + rationale.

> **GraphRAG research update (2026-06-14):** use P4's compact `project_context.json` as the v1 memory,
> but shape it so it can scale into **GraphRAG** later: canonical records emit typed facts
> (`asset_version_has_lora`, `flow_node_uses_asset_version`, `shot_generated_by_job`, etc.) with
> stable IDs, and P6 can build the persistent graph/vector index over those facts. This is a better
> fit than plain vector RAG because the app's hardest context questions are relational and global
> ("which old shots use Mara before the red-hair version?", "which LoRA is stale relative to this
> ref set?", "what does this branch imply about props and rules?").

| Role | Exploratory default *(superseded → R144)* | Why |
| --- | --- | --- |
| **Dispatch / tool-calling** (inline, agent planning) | `SmolLM3-Q4_K_M.gguf` (or Qwen3.5-9B if VRAM allows) | fast, function-calling/JSON; smallest resident footprint |
| **Creative / dialogue** (NPC lines, prose, prompts) | `Qwen3.5-9B` / `SmolLM3` | best role-play/creative; bigger Qwen3.5-27B available for batch jobs |
| **Vision (VLM)** — on-model judging, curation, captioning | **`Qwen3-VL-4B-Instruct` + `qwen3-4b-instruct-mmproj-F16`** (30B-A3B variant also present) | **can actually *see* images** — and is **already the VLM handrefiner uses** for grounded region detection |

The registry makes models hot-swappable per role and is the natural home for future game/story
fine-tunes (`kb-slm.md` Approach 2). Also present and usable: Phi-4-mini/reasoning, Trinity,
helcyon-grok — the author can audition any of them per role.

- **The VLM is first-class — and already proven here.** `src/pipeline/postproc/handrefiner/`
  already starts **Qwen3-VL** as an on-demand `llama-server` for VLM-grounded region detection
  (`--detect-vlm-regions`, with `--vlm-ctx/-ngl/-ready-timeout`). loom reuses exactly that
  pattern for curation: score "on-model"-ness, drive readiness (§4.1 Stage C), auto-caption
  LoRA images, and answer "what's wrong with this shot?".
- **On-demand only (author, round-3 answer 6).** No model stays resident during generation.
  The tenant manager: (1) loads a model only for active assist/planning; (2) before a heavy
  job, **persists KV-cache/slot state to disk and unloads** so the full 16 GB goes to
  generation; (3) reloads + restores context afterward. Net: **zero SLM/VLM VRAM cost during
  batch generation, context preserved.** The repo already has the bones — `llama.cpp` KV-cache
  save/restore behind `src/village_ai/models/kv_cache/`.
- **Cross-model context exchange is explicitly *not* a focus (author, round-3 answer 6).** The
  author wants to build **loom, not an LLM**. Sharing one KV-cache *between different models*
  is hard (tokenizer/architecture mismatch) — so the default is **per-model context** (each
  role keeps its own saved state). If a clean cross-model handoff falls out cheaply, great; if
  not, **that's fine** — we don't invest in it. A pragmatic fallback when switching models is a
  short **text summary** of the prior context rather than a shared KV blob.
- **Agentic Muse vs. an unloaded LLM — the real tension (author, round-3 answer 6).** If the
  LLM is unloaded during generation, an agent can't "think" mid-run. Resolution: **agent mode
  plans fully up-front while loaded**, the author approves a concrete step list (§7.2), and
  **execution is then queue-driven and needs no live LLM** — each ⚙ step is a deterministic job
  the queue runs. The LLM only reloads if a step genuinely needs fresh reasoning on a result,
  and then it **waits for a GPU gap** (between jobs) rather than competing with a running one.
  So agentic workflows degrade to "plan → approve → unattended queue execution," which fits the
  on-demand constraint and the propose-and-approve rule (§7.2) cleanly.
- **Limit — small models hallucinate structure.** Hence GBNF/JSON-schema-constrained tool
  calls, plan-before-execute, and human gates. Treat Muse as a fast junior assistant, not an
  oracle.

---

## 8. Architecture

```
┌──────────────── Desktop app (Tauri or Electron) ────────────────┐
│  React + TypeScript UI                                           │
│   • React Flow  → L4 narrative graph                            │
│   • Konva/Canvas → L3 compositor + take timeline               │
│   • Zustand store, projection of Story Bundle files on disk     │
└───────────────▲───────────────────────────────┬────────────────┘
                │ local HTTP / IPC               │ file watch
┌───────────────┴───────────────────────────────▼────────────────┐
│  Orchestrator service  (Python FastAPI, reuses src/pipeline/*)  │
│   • Job queue (VRAM-aware, single GPU worker, reorderable)       │
│   • Pipeline adapters → shell out to run_pipeline.py subprocs    │
│   • SLM director (llama-server, kb-slm.md) for continuity/prompts│
│   • Manifest + lineage writer; Story Bundle file I/O             │
└───────────────┬─────────────────────────────────────────────────┘
                │ subprocess (one heavy model at a time)
   ┌────────────┼───────────┬───────────┬───────────┐
 flux2/sd35/  wan2/hunyuan/  trellis2    _img2img    llama-server
 zimage(t2i…) ltxv (i2v…)    (img→3D)    (polish)    (SLM, :8080)
```

Rationale:

- **Reuse, don't rewrite.** The orchestrator wraps the existing
  `python -m src.pipeline.<x>.run_pipeline …` CLIs (see `kb-trellis2.md` CLI section). The UI
  never imports torch.
- **Shell = Tauri or Electron** — the author has used neither; see the full rundown in §8.2.
  Both work because the heavy lifting lives in the Python orchestrator + `llama-server`
  sidecar, so the shell is a thin client either way.
- **React Flow** is the natural fit for the articy-style graph; **Konva** for the
  layer compositor and timeline cards.
- **Generative model as a sidecar** `llama-server` — the **single unified `Qwen3-VL-4B-Instruct`
  Q8** (R144) authors continuity prompts (L3) and assists in Flow (L4). It runs as its **own
  exclusive AI job on the single-GPU queue** (R141) — never alongside a *running* gen/video job, and
  **never auto-invoked at play time** (R143).
- **Files are the source of truth.** The UI watches the Story Bundle; external edits (hand-edits
  to JSON) reflect live. This keeps it scriptable and inspectable. (The bundle is user data,
  not in the app's git — §3.3.)
- **Single instance, one project at a time (author, round-8 answer 8).** loom does **not** run
  concurrent stories/instances. This is a deliberate simplification: one project owns the single
  GPU job queue, the one SLM/VLM tenant slot, and the one work disk (§4.2) — no cross-project
  contention to coordinate. A second launch focuses the existing window rather than opening a
  rival instance.

> **Codex comment - understanding:** The architecture has three clean processes: a desktop
> editor, a lightweight orchestrator, and isolated inference workers. FastAPI is useful if
> the service may later be controlled by scripts; IPC is simpler if it remains exclusively
> local.
>
> **Concern / decision:** Choose one ownership boundary for filesystem writes. If both the
> desktop app and orchestrator write bundle records, file watching can create races and
> feedback loops. Prefer orchestrator-owned writes through a typed API, atomic replacement,
> and emitted change events. Also verify whether the sidecar can truly co-reside in VRAM
> during heavy inference; the queue should be able to pause or unload it.

### 8.1 Build backlog this depends on (from `kb-pipelines01.md` §"Gaps Worth Building")

The platform is blocked on a handful of pipeline-CLI additions already itemized there:

1. LoRA loading flags + manifest fields on `zimage`/`sd35` (then Flux2 multi-ref/LoRA) — **L2
   LoRA feature**.
2. **Flux2 multi-reference (`--ref-image` + structured prompt)** — the author's chosen
   character-expansion mechanism (§4.1). Promoted from "later" to an **early research spike**,
   because it is the real "insert this character into any scene" path and unblocks the
   start-here bootstrap loop. (`kb-pipelines01.md` flags it as conceptual/not-yet-wired.)
3. LTXV Phase 3 `keyframes`/`extend` — improves L3 interpolation (works without it today via
   first-frame I2V).
4. Shared "prepare first frame" util (resize/crop to video aspect + manifest) — **L3 keyframe →
   draft** handoff; also feeds the §4.1 video-sketch frame-harvest.
5. "Motion prompt trial" command (LTXV over N seeds → recipe for Hunyuan/Wan) — **L3 draft
   grid**.
6. Video wrappers in `multi/` for session lineage — **the lineage edges**.
7. The keyframe/movie orchestrator (asset bible, shot-state JSON, SLM prompts, compositing,
   segment gen, last-frame continuation, stitch) — **this is literally the L3 backend.**

None of these block starting on L1/L2 + the shell + job queue. **TRELLIS is *not* on this
critical path for characters** (§4.1) — it stays available for prop/scenery 3D assets, depth
proxies, and engine/DCC export, but the character pipeline no longer depends on it.

> **Codex comment - understanding:** The dependency split is sensible: P0 and much of P1
> can exercise project I/O and adapter wiring before the richer movie orchestrator exists.
>
> **Concern / decision:** Add a short "contract hardening" task before P0: normalize command
> schemas, manifest envelopes, progress events, exit codes, cancellation, and capability
> discovery across existing workers. There is also a numbering inconsistency below: P2 says
> LoRA flags are "gap #7", while this section presents them as item 1. *(Resolved: the engine
> KB does not number its gaps; this doc's §8.1 list is the canonical numbering, and the
> roadmap now cites "§8.1 item 1".)*

### 8.2 Tauri vs Electron (you've used neither — here's the honest rundown)

The good news: **this decision is low-stakes for *this* project**, because the SLM and all
generation already live behind a Python `llama-server` + FastAPI orchestrator. The shell only
has to (a) render the React UI, (b) spawn/supervise sidecar processes, (c) make local HTTP
calls, and (d) watch files. **Both frameworks do all four.** So the choice is mostly footprint
vs. familiarity, not capability.

| Dimension | **Electron** | **Tauri** |
| --- | --- | --- |
| Backend language | Node.js (JavaScript/TS) | **Rust** (you'd write a thin Rust layer) |
| Footprint / RAM | Bundles Chromium + Node → ~120–200 MB installer, higher RAM | Uses the OS WebView2 → ~3–10 MB installer, low RAM |
| Renderer consistency | Ships its own Chromium (identical everywhere) | Uses system WebView2 on Windows (fine; you're Windows-only) |
| Spawn `llama-server` / Python | trivial (`child_process`) | supported (Tauri **sidecar** / `Command`) — slightly more config |
| File watching | mature (`chokidar`) | Rust `notify` (mature) or do it in the Python orchestrator |
| Ecosystem / examples | huge; most AI-desktop apps are Electron | smaller but growing; fewer copy-paste examples |
| Security posture | larger surface (full Node in renderer if careless) | sandboxed-by-default, explicit allowlist |
| **llama.cpp integration** | `llama-server` sidecar **or** `node-llama-cpp` (npm, prebuilt binaries, in-process) | `llama-server` sidecar **or** `llama_cpp` Rust crate (in-process) |

**What neither gives you out of the box (you build it regardless of choice):**

- The Python orchestrator + job queue (this is your code either way).
- VRAM-aware scheduling and the single-GPU-worker rule.
- The React UI, React Flow graph, Konva compositor.
- ROCm/GPU plumbing (lives entirely in the Python workers, untouched by the shell).

**llama.cpp specifically:** the easiest path in *both* is the **`llama-server` sidecar** from
`kb-slm.md` (OpenAI-compatible HTTP) — no native bindings, no per-platform compile, hot-swap
models, and it's the same transport whether you're in Electron or Tauri. Only reach for
in-process bindings (`node-llama-cpp` / `llama_cpp` crate) if you want to drop the sidecar
process; given you already run a Python orchestrator, the sidecar is the lower-friction choice.

**Recommendation:** **Tauri** as primary — the footprint and RAM headroom genuinely matter on a
16 GB GPU box where the queue, Chromium, and a 2 GB SLM all compete; the Rust layer stays tiny
(spawn sidecars + emit file-change events). **Electron** is the safe fallback if the Rust layer
or WebView2 quirks become a time sink, or if you'd rather integrate `node-llama-cpp` directly.
Because the UI is plain React + a thin transport layer, **switching shells later is cheap** —
don't over-agonize this; pick Tauri, keep the shell logic thin, and revisit only if it bites.

### 8.3 Postprocessing toolkit — significantly expanded for loom (answer 4)

The author wants loom's postprocessing capabilities **significantly extended**, including
**lip-sync** and **masking** and the other tools recorded in the `kb-postproc-*` docs. There is
already a real foundation: `src/pipeline/postproc/` (architecture in
[`kb-postproc-img.md`](kb-postproc-img.md)) with HandRefiner implemented and a full open-weights
catalogue parked for follow-on modules. loom turns that catalogue into first-class, queueable
tools surfaced as **per-variant/per-clip actions** in the Asset Studio (§6.3) and Shot timeline
(§6.4). Each is a subprocess-isolated pipeline like the rest (one heavy model at a time).

**Image postproc (mostly cataloged in `kb-postproc-img.md`, some implemented):**

| Tool | Models (license noted in `kb-postproc-img.md`) | loom use |
| --- | --- | --- |
| **Matting / cutout** | BiRefNet (MIT) / RMBG-2.0 | character & prop alpha for the **Layered Asset Strategy** (§6.4) |
| **Masking** | **SAM 2** (click/box) · **Grounded-SAM-2** (text→mask, e.g. "left hand") | drive inpaint/region edits; the masking the author called out |
| **Identity lock** | **PuLID** (Apache 2.0) > InstantID > IP-Adapter-FaceID | a *complement to Flux2 multi-ref* for Stage-B insertion (§4.1) **and the optional cross-version "identity anchor"** (§3.4) that keeps a character's face stable across versions |
| **Anatomy fix** | **HandRefiner** (implemented) · ADetailer (face/hand) | clean hero frames before they become keyframes/refs |
| **Face restore** | CodeFormer / GFPGAN | tidy faces on promoted frames |
| **Upscale** | Real-ESRGAN (general) · SUPIR (heavy) | promote a draft last-frame into a hi-res keyframe |
| **Relight / color-match** | IC-Light · `color-matcher` | match a composited layer to its plate |
| **Depth / pose / edge** | DepthAnythingV2 · DWPose · Canny/HED/lineart | ControlNet conditioning + the §6.4 depth-proxy passes |

**Video postproc (new for loom — the bigger lift):**

| Tool | Approach | loom use |
| --- | --- | --- |
| **Lip-sync** | **audio-driven talking-head** (Wav2Lip / SadTalker / LatentSync-class): face clip **+ an audio file → lip-synced clip** | the answer to "audio→lips": author records/voices speech *or* uses TTS (below), loom syncs the node's clip. Optional per node. |
| **Per-clip masking / region edit** | SAM2-video / propagated masks | fix or swap a region across a clip without re-rolling it |
| **Frame-continuity helpers** | exact last-frame extract · first-frame lock · short overlap/optical-flow morph | the §6.4 frame-accurate-continuity machinery (within-node, round-4 answer 3) |

**Audio generation (new for loom — author, round-4 answer 7):**

loom offers **two TTS tiers (author, round-5 answer 1)** — both may be needed:

| Tier | Approach | loom use |
| --- | --- | --- |
| **Draft TTS** | fast, light, fixed voices — e.g. **Piper** (MIT) or **Kokoro** (Apache); CPU-friendly, no per-character setup | quick **scratch/placeholder** voicing while drafting a scene — say the line, hear it, iterate |
| **Character TTS (voice cloning)** | clone a voice from a short **imported** sample — e.g. **XTTS-v2 / F5-TTS**-class (heavier, GPU, another on-demand tenant); **loom does not record** (round-10 answer 1) | **consistent per-character voices**; the voice (sample + settings) is **part of the AssetProfile version**, copied when a new version is made and editable until finalize like any other parameter (§3.4) |

**Voice sample capture (settled, round-10 answer 1 — loom does NOT record).** loom **displays a
standard "sample text"** (English, **per character**, §6.2) for the user to read **while
recording elsewhere**, then **imports the resulting audio file**. loom does **not** handle a
microphone — no in-app capture. Validation is **minimal** (a light "seems short/quiet?" hint at
most), not a gate.

**Voice audio shape (round-9 answer 2).** Generated/voice audio is **mono content**, but
**up-sampled to the project audio master** (§6.2) and placed as a **pannable mono source in the
stereo field** (so you can position a speaker left/right) — up-sampling format-matches, it does
not invent fidelity.

Either tier's output can **drive lip-sync** (above), and the author may still **supply recorded
audio** instead. TTS is a *generation* step (text → audio) living beside the postproc tools
because it feeds the same Finalize/lip-sync flow. Both tiers are **optional** components (§8.3
packaging note); the cloning tier is the heavier, clearly-"optional" tenant.

> **Licensing caution (from `kb-postproc-img.md`):** several of these are research/non-commercial
> (CodeFormer, HandRefiner weights, SUPIR backbone, InstantID's InsightFace, IC-Light v2). loom
> keeps the existing `_license_gate.py` acknowledgement pattern and prefers permissive options
> (BiRefNet, SAM2, PuLID, GFPGAN, Real-ESRGAN, DepthAnythingV2, DWPose) by default.

Sequencing: image postproc (matting/masking/identity/restore) lands with the Asset Studio in
**P1**; **lip-sync + video masking** are a **P3** workstream alongside the audio model. New
modules follow the established `src/pipeline/postproc/<tool>/run_pipeline.py` shape, so they
plug into the queue and lineage for free.

> **Packaging strategy (author, round-3 answer 5) — "essential vs optional" must be explicit.**
> The repo already vendors several heavy projects as sibling repos compiled into the shared
> env (e.g., `TRELLIS.2_rocm/`, `flash-attention/`). Preferred path for lip-sync: **clone +
> compile it locally into the shared `.venv`** the same way, so loom uses it without a separate
> environment. A **separate `.venv` is acceptable** if its deps genuinely conflict — but only
> as a contained exception. To keep that honest, loom maintains a **component manifest**
> classifying each pipeline/tool as **essential** (core loom: image/video/3D gen, training,
> the queue, Muse) vs **optional/external** (lip-sync, local TTS, exotic postproc), recording
> for each its repo origin, env (shared vs isolated), license gate, and whether loom degrades
> gracefully without it. This is what stops loom's footprint from sprawling uncontrolled.

---

## 9. Walkthrough & roadmap

### 9.1 End-to-end walkthrough (one beat)

1. **WORLD**: define the lantern-workshop world, a warm-lamplight 35mm style (`style_lantern_v3`),
   asset classes, naming template, and a spine with NPC "Mara". → stub AssetProfiles created.
2. **ASSETS (bootstrap)**: cast Mara with a `multi` batch → pick the hero ★ → expand via Flux2
   multi-ref + a video-sketch harvest (SLM writes the varied prompts) → curate to ~30 on-model
   shots → **Train LoRA** on the scratch-disk → `mara_lw.safetensors`. Generate the brass-map
   prop; **→3D** for a TRELLIS prop asset.
3. **SHOTS** (compositor): new shot `s2_03`, drop the Workshop plate + Mara (LoRA) + brass-map
   layers, compose the start keyframe, inpaint the seam.
4. **SHOTS** (timeline): sketch tier `ltxv i2v ×4` → pick the best turn-toward-camera draft →
   drive tier `wan2 animate` + Mara's canonical image; chain segments with **frame-accurate
   continuity** to exceed 5 s → **Finalize** (resolution/fps checked, eyeball otherwise; bakes
   clip-level lip-sync/SFX flattened to one track).
5. **FLOW**: a Dialogue node "Mara: *You found it.*", speaker = Mara, **owns** clip `s2_03`; a
   Choice ("take the map" / "refuse") sets `has_map`; a Condition branches Act 2.
6. **EPISODE/EXPORT**: **order the chosen nodes' FINAL clips, one at a time** → cut/cross-fade
   (no regen) → add the **episode music bed + mix** (gain/volume/pan) → export **standard files**
   (EDL/XML + media + audio stems) to **free Resolve**; export Mara's GLB to **Unreal 5.7+ /
   Blender**.

Throughout, the **Job Queue** shows the single running model and VRAM; the **SLM** writes each
next segment's continuity prompt; every output keeps its manifest + lineage.

> **Codex comment - understanding:** This is the right acceptance scenario for the product:
> it crosses every layer and proves that inherited style, lineage, queued work, and graph
> binding remain connected.
>
> **Concern / decision:** Split this into two executable acceptance scenarios. The MVP
> scenario should stop after the LTXV sketch clip and lineage inspection. The later full
> scenario can include LoRA training, TRELLIS, WAN driving, Flow branching, and Unreal
> export. That keeps the first proof independent of unfinished pipeline capabilities.

---

### 9.2 Phased roadmap

Reminder on git scope (§3.3): **git = the loom app**; the per-phase KB files below are
`kb-loom-p0.md`, `kb-loom-p1.md`, … (author's naming convention; this file, `kb-storyboard01.md`,
stays the overview). Story Bundles are user data, never in the app repo.

**Heavy-investment phases are P0 and P1** (author, answer 6): system architecture, the
SLM/VLM tenant manager, LoRA training, and the UI foundations all land early. Expect the bulk
of engineering effort here before the later phases get cheaper.

| Phase | KB file | Deliverable | Depends on |
| --- | --- | --- | --- |
| **P0 Foundation** | `kb-loom-p0.md` | **single-instance** app **git repo** scaffold; `loom init` creates a project workspace **on the work disk (default `F:\_tmp\<project>`, must be empty + free-space ≥ cap, §6.2)**, plain (non-git, §3.3); **global project format at creation — aspect+resolution locked, fps, audio master (WAV/AIFF 48/44.1k), Wan2.2 presets, per-project size cap (def 250 GB/min 50 GB, R164) + footprint estimator** (§6.2); **project-wide HARD STOP at cap** (§4.2); app shell; Bundle I/O with **stable IDs, `schema_version`, atomic writes; no draft state + exit-warning** (§3.4); **persistent Job Queue (resume *paused* on relaunch)** (§6.6); **model-registry + on-demand SLM/VLM tenant mgr** (§7.4); **component manifest** (§8.3); **hard-require local stack at launch** (§11); "contract hardening" over worker CLIs | existing CLIs; `village_ai/models/` (present) |
| **P1 Bible+Assets+Casting** | `kb-loom-p1.md` | L1 World (+ **stub AssetProfiles = prompt-template snippets; manual re-sync, no auto-update**, §6.2) + L2 Asset Studio with **AssetProfile versioning (full-duplicate copy-on-create from *any* prior version → edit → finalize=pure-intent lock; many versions w/ grouping; new-profile for big changes; manual choice, no hints)** (§3.4) + **profile export/import incl. all versions (import = always new profile, rename on collision)** (§6.3); **character-bootstrap Stages A–C only** (casting via `multi`, **`--num-candidates` ≤ 5**, **≤ ~200 img/process configurable**, **simple scrollable selectable grid**; expansion via img2img/inpaint + **video-sketch harvest**; curation). **No training here** — curated refs saved for P2. **Image postproc toolkit** (§8.3); **Flux2 multi-ref research spike** (§8.1 item 2) | `_img2img`/inpaint (done); `ltxv` (done); `postproc/` (foundation exists) |
| **P2 LoRA** | `kb-loom-p2.md` | **the only phase where training happens** — **template captions (no VLM)** + **proxy readiness meter** (coverage/dupes/face-embedding); **Stage D/E Train-LoRA in workspace temp** via **ai-toolkit (default)/diffusers-PEFT (advanced)**, **train-from-base default**, per-model preset, **staged jobs** (explicit add-to-queue); promote + manual cleanup; writes graph-ready `caption_policy`, `training_context`, readiness, and LoRA manifest facts for later GraphRAG, but **does not build retrieval**. **VLM captioning/scoring → P4.** | NEW image-LoRA trainer (none in repo); LoRA flags on `zimage`/`sd35` (§8.1 item 1) |
| **P3 Shots + audio + continuity** | `kb-loom-p3.md` | L3 compositor + take timeline; Muse continuity director; **⚠ within-node continuity R&D — strive pixel-identical + SEAM-WELDING (interpolation/optical-flow/v2v), dissolve fallback (R124)**; **drive tier = wan2 animate OR hunyuan i2v, user-selectable (R126)**; **shot/take versioning + out-of-date warning + Finalize gate (res/fps/aspect/audio)** (the L3 *shot/take* record — distinct from the L4 *Flow node*); **node-level VOICE only — 2 TTS tiers + lip-sync (2–3 models, R127), clip-first TIMECODED dialogue cues via forced alignment (R132), import-only sample, mono→master pannable**; welder bar "invisible at play speed" + RIFE/FILM (R129/R130); **default drive tier wan2 (R131)**; **LTXV-extend native long-form pulled forward as P3 spike (R133)**; video masking; **creative SLM online (continuity director)**; a Flow node owns **exactly one** L3 shot (R146 — sole ownership; reuse = duplicate), pins `asset@version`. **3D deferred → P6 (R128).** | §8.1 items 4,5,7; SLM tenant online |
| **P4 Flow + Muse agent** | `kb-loom-p4.md` | L4 narrative graph — **v1 single flat graph + Jump nodes (R74)**; **nodes independent, pin `asset@version`, branch + parallel** (§6.5); **node BINDS or SPAWNS an L3 shot (R134)**; **minimal variables/conditions v1, expandable (R135)**; play mode (**no ad-hoc voicing, R143**); **one-file-per-node + thin index (R139)**; Muse inline+chat+**agent (plan-up-front, propose-approve, agent STAGES gpu not auto-queue, R136)**; **TWO MODELS v1 (R144): `Qwen3-VL-4B-Instruct` Q8 for ALL generative roles (dispatch+creative+vision-describe) + `Qwen3-VL-Embedding-8B` Q8 for retrieval/scoring (Reranker optional); Q8 fits 16GB ONLY because vision runs exclusively**; **context mgmt (R145/R170): `project_context.json` digest = durable memory, `project_facts.jsonl` = rebuildable graph-ready facts, KV-cache = speed layer only; GraphRAG index later (R138/R170)**; **⚠ ALL AI = QUEUE JOBS on the single-GPU worker (R141) — never concurrent with gen/train, no auto-trigger; interactive chat/inline IDLE-ONLY (R142); NO ad-hoc play-mode NPC voicing (R143)** | game-rules vocab from L1; tool surface §7.3; handrefiner VLM path |
| **P5 Episode/Export + Production hardening** | `kb-loom-p5.md` | **TRACK A (MVP headline, ships first): Render records (multiple per project, each own manifest; all share the one project format, §3.2/§6.7)** — **episode = HAND-BUILT render list, DECOUPLED from the graph (R148)**; order clips one-at-a-time, **pick which node-version clip** (older OK), **block-missing/warn-stale (R153)**; **SINGLE-LANE TIMELINE editor (R157 — one video lane, drag-reorder, transition = draggable OVERLAP zone + trim/freeze toggle; NOT a multi-lane NLE since R29 forbids simultaneous clips)**; basic editing only (R18 — **every join a transition, default HARD CUT (R149), non-cut needs overlap**, no split-screen); **arbitrary length (1 s+), HARD-STOP disk guard**; **v1 audio = node VOICE only (no episode bed/mixer/SFX/music)**; **render = queue job (R150)**; **file-based export to free Resolve** (EDL+FCPXML + media + voice + **chapter markers (R158)**, **Resolve-compat build gate R151**; stems post-v1) + **.srt subtitles (R152)** + Muse pitch one-pager (text+stills, R154); **keep all render outputs**. **TRACK B (production hardening, MOVED FROM P6 — R147): deeper Flux2 multi-ref; LTXV-extend hardening (P3 spike R133); multi-LoRA stacking + usable style LoRA (R122); postproc expansion (SUPIR/video-SR upscale, IC-Light relight, ADetailer, video face-restore) on L2/L3 surfaces (NOT L5, R18)** | finalized node clips (P3); **P4 Flow graph (render records reference `flow_node_id` — hard dep)**; §8.1 items 2,3; Track B = model/ROCm-heavy |
| **P6 Polish — post-v1, NO essential deliverables (R165)** | `kb-loom-p6.md` | **Not required for alpha/v1 — P5 is the v1 gate (R165).** Deferred polish: **all 3D (`trellis2` props/scenery GLBs + proxy-miniature depth-ControlNet route, R128)**; **effect plugin API (Python/JS/TS)** (§6.7); **engine/DCC export — assets *and* graph to Unreal 5.7+/Blender** (§6.7); deferred **GraphRAG retrieval index** (R138/R170) / video LoRAs; **+ any unforeseen overflow from P0–P5** (flexible scope). *(Flux2-deep / LTXV-extend-harden / multi-LoRA+style / postproc-expansion MOVED → P5, R147.)* | post-v1; **nothing here gates v1** |

**MVP / first vertical slice (author, round-4 answer 8):** **P0 + P1 only**, ending at
**casting → curate → a saved AssetProfile** (name + prompt-template snippet + curated reference
set). **No training** (that's P2), no shots, no episode. This is the "this proves the
architecture works" moment: world/style defined → multi-batch casting in a selectable grid →
curate → an AssetProfile whose prompt snippet is reusable downstream. It deliberately depends on
**zero unfinished pipeline work** (just the existing `multi`/`_img2img` CLIs + the P0 shell).

> **Codex comment - understanding:** The roadmap separates the editor foundation from
> increasingly expensive generative features. The proposed MVP is narrow enough to expose
> whether the desktop architecture is pleasant to use before deeper investment.
>
> **Concern / decision:** Move schema versioning, reference validation, atomic writes,
> adapter capability discovery, and crash recovery into P0. Without them, later phases may
> build on unstable persistence. Also correct the P2 dependency label from `gap #7` to the
> canonical gap number used in the engine KB.

---

## 10. Open questions / decisions to make

### 10.0 Resolved by the author (2026-05-31) — R1–7 r1, R8–15 r2, R16–32 r3, R33–40 r4, R41–48 r5, R49–57 r6, R58–66 r7, R67–74 r8, R75–83 r9, R84–91 r10, R92–99 r11, R100–103 r12, R104–110 r13, R111–114 r14, R115–118 r15, R119–123 r16(review), R124–128 r17, R129–133 r18, R134–146 r19+r20(AI-exec/model+context/ownership), R147 r20(P5/P6 rebalance), R148–156 r21, R157–158 r22, R159–161 r23(job-lifecycle, HF model hosting, frame-seq clip masters), R162 r24(app repo = cloned loom-loreweave-studio), R163–164 r25(launch-behavior reconcile, disk-cap→250GB+footprint estimator; +stale-ref cleanup: R161 disk, P5 render-ref triple, R91 superseded), R165 r26(P5=alpha gate, P6=flexible overflow), R166–169 r27(cross-phase review: P3 fallback=one finalized clip, alpha=P5 Track A/M7, P3 owns minimal AI-tenant, P2 M2=trainer skeleton), R170 r28(GraphRAG posture)

| # | Decision | Resolution |
| --- | --- | --- |
| R1 | Product scope | **Not a game.** Non-linear storyboard tool; headline = ~30-min pitch/streaming **episode**; engine/DCC export later; games long-term. |
| R2 | articy interop | **Full replacement**, no round-trip in the critical path. |
| R3 | git scope | **git tracks the loom *app* only.** Story Bundles are user data, outside git (ComfyUI model); **no media in git/LFS** (§3.3). |
| R4 | SLM scope | **Everywhere, Copilot-style** (Muse, §7), and **modular — different models per role**, incl. a **VLM** (§7.4). |
| R5 | Shell | **Tauri** primary (Electron fallback) — low-stakes, thin client (§8.2). |
| R6 | First LoRA base family | **`zimage`** near-term. |
| R7 | Start point | **The character-bootstrap loop** (§4.1) is P1's spine. |
| **R8** | **Engine** | **Unreal Engine 5.7+ and Blender** (both installed). No Unity. Matches stored memory — conflict closed. |
| **R9** | **Episode model** | **No monolithic render.** Assemble many **production-ready ~3–5 s clips** rendered during production; final = select + stitch + audio mix (refined by R26–R30); heavy finishing in **Resolve** (§6.7). |
| **R10** | **Audio** | Author supplies **own music/SFX/some speech**; platform must **accept/align audio layers**; **lip-sync in scope but optional, audio-driven**. Detail in P3. |
| **R11** | **Stage-B method** | **Invest in Flux2 multi-reference**; use **video-sketch harvest** meanwhile. **Drop TRELLIS turnaround** for characters (§4.1). |
| **R12** | **VLM** | **Yes — add a vision model**, modular per §7.4. |
| **R13** | **LoRA training env** | **Share the project `.venv`**; isolate on a **scratch-disk** (default `F:\_tmp`; video LoRAs may need far more); cleanup **easy but manual** (§4.2). |
| **R14** | **Muse autonomy** | **Propose-and-approve only** for now (compute-cost worry); raise later if ever (§7.2). |
| **R15** | **Naming** | Keep **Loreweave Studio / `loom`**; per-phase KB files named **`kb-loom-p0.md`, `kb-loom-p1.md`, …**; this overview stays `kb-storyboard01.md`. |
| **R16** | **Scratch-disk** | **Both location and size configurable** (§4.2). |
| **R17** | **Snippet continuity** | **Frame-accurate within-node continuity is mandatory** (5 s is too short); dedicated R&D in P3 (§6.4). |
| **R18** | **In-loom editing** | loom gets **basic editing only** (trim/cut/cross-fade); heavy finishing → Resolve. loom stays a generation tool, not an AE tool (§6.7). |
| **R19** | **Resolve integration** | **No API, no loom-generated Resolve project.** Plain **file-based** export/import (EDL/XML + media + stems); **free Resolve** baseline. The `.lua` idea = optional scripting to extend **loom's own** video effects, *not* to drive Resolve (§6.7). |
| **R20** | **Postproc** | **Significantly expand** the postproc toolkit (lip-sync, masking, identity, restore…) from `kb-postproc-*`; new §8.3. |
| **R21** | **SLM/VLM efficiency** | **On-demand only**, KV-cache start/stop so SLM/VLM **frees VRAM during batch generation** but keeps context (§7.4). |
| **R22** | **VLM choice** | Use **Qwen3-VL-4B (already on disk)**, **on-demand**; 30B available; open to recommendations. |
| **R23** | **Shot ownership** | **No reusable shots — every node owns its clip(s)** with frame continuity (§6.5). Nodes are **independent entities**, branch or run parallel. |
| **R24** | **Flux2 multi-ref spike** | **OK to start in P1**; accept it may not pan out on ROCm Flux2. |
| **R25** | **Scratch default** | **`F:\_tmp`** (~140 GB free now, ~400 GB more coming). |
| **R26** | **Continuity** | **Pixel-identical handoff is *within-node only*** (end-frame → next segment's first frame). The **episode list always uses transitions** between clips — never a pixel-identical seam (§6.4, §6.7). |
| **R27** | **Audio model** | **Clip-level track flattened** at Finalize (lip-sync/SFX); **episode-level continuous bed(s)** at render; **minimal mixer: gain/volume/pan only** (no EQ, no VST3 — do those in Resolve/DAW) (§6.7). |
| **R28** | **Resolve version** | **Latest *free* Resolve** is the baseline — nothing Studio-only. |
| **R29** | **Episode linearity** | **One clip at every point — no split-screen/intercut.** Parallel graph threads are alternatives you pick between (§6.7). |
| **R30** | **Finalize gate** | **Resolution + frame rate must match project settings**; everything else **eyeball** (§6.4). |
| **R31** | **Lip-sync packaging** | Prefer **compile locally into shared `.venv`** (like other vendored repos); a **separate `.venv` is an acceptable exception**; track via the **component manifest** (essential vs optional) (§8.3). |
| **R32** | **LLM focus** | Build **loom, not an LLM.** Cross-model context exchange is **not a focus**; per-model context is the default, and multi-model is fine to drop if hard (§7.4). Agentic Muse = **plan-up-front then queue-driven execution** with the LLM unloaded during jobs (§7.2/§7.4). |

| **R33** | **VST3** | **Cut.** Fancier audio processing → Resolve/DAW; loom keeps gain/volume/pan only (§6.7). |
| **R34** | **Basic mixing in loom** | **Yes** — not everyone has a DAW/Resolve, so loom ships **gain/volume/pan** (§6.7). |
| **R35** | **Continuity scope** | Pixel-identical **only at node generation** (end-frame chaining); **episode list always has a transition** (R26). |
| **R36** | **Project settings** | **Resolution, fps, aspect ratio, audio format** are **project-level, no overrides** (§6.4). |
| **R37** | **Effects extensibility** | **P6+ nicety** via an **effect plugin API in Python or JS/TS** (not Lua) (§6.7). |
| **R38** | **Casting limits** | `--num-candidates` **≤ 5**; **≤ ~200 images/process (configurable)**; keep current `multi` flow but make results **selectable** in a grid (§4.1). *(Amended 2026-06-11: ≤5 is enforced; the ~200/process cap is **guidance, not enforced** — superseded by the disk guard (R96) + queue visibility.)* |
| **R39** | **Local TTS** | **Yes — add a local TTS pipeline** (§8.3); feeds lip-sync; author may still supply recordings. |
| **R40** | **MVP / first slice** | **casting → curate → saved AssetProfile** (prompt-template + curated refs). **No training** (P2). Proves the architecture (§9.2). |

| **R41** | **TTS tiers** | **Both** — a **fast draft TTS** (fixed voices, e.g. Piper/Kokoro) *and* **voice cloning** for consistent per-character voicing (XTTS/F5 class), pinnable to a profile version (§3.4, §8.3). |
| **R42** | **Episode audio** | **Nice-to-have / deferred.** Baseline = **node-level audio only**; an episode bed + mixer may not ship — a spanning score can be done in Resolve (§6.7). |
| **R43** | **Clip versioning** | Node keeps **many clip versions**, all retained unless marked for deletion; **light stale-render warning** when edited since last render. *(Refined by R62: full node versioning — the episode list picks any version's clip, not a single global `final`.)* |
| **R44** | **Casting grid v1** | **Simple scrollable grid** (star/cull); filtering/sorting/bulk deferred (§4.1). |
| **R45** | **Project format** | Set at creation: **aspect `[X]:[Y]` + resolution `[W]×[H]` locked together**, fps, audio format; **presets list** offered; no overrides (§6.2). |
| **R46** | **Asset Profile versioning** | **New core feature** — a profile is a **list of versions** (distinct states/looks: hair color, mustache, broken prop, day/night); each version owns its prompt/refs/LoRA; **nodes pin `asset@version`** (§3.4). |
| **R47** | **Prompt injection** | **Structured or explicit, never automatic** — a picker that inserts the snippet, or an explicit `@asset@version` token (§6.2). |
| **R48** | **Engine export** | **Both assets *and* story graph**, but **explicitly not the main goal** — P6+, low-priority; only keep `graph.json` engine-neutral now (§6.7). |

| **R49** | **New version weight** | **Light edit** — copy parent, tweak, re-train LoRA if needed. **Significant/independent changes → new profile**, not a version (§3.4). |
| **R50** | **Version seeding** | **v1 seeds v2** (copy-on-create), used only for small changes; big changes → new profile (§3.4). |
| **R51** | **Version lifecycle** | **Copy-on-create freezes a baseline → edit only the differing parts → finalize/lock.** After finalize, immutable; further change → new version (§3.4). |
| **R52** | **Scenes** | **Plate/asset only — no continuity-check tool.** Consistency of a place comes from the **props/characters** placed in it (§3.4). |
| **R53** | **Voice binding** | Voice is **copied into a new version unchanged**, editable until finalize — **same lifecycle as every parameter** (see R89) (§3.4). |
| **R54** | **Render record** | **Yes — render-level record, multiple per project, each with its own manifest** (different settings/cuts) (§3.2, §6.7). |
| **R55** | **Stub auto-update** | **No auto-update.** Manual **"re-sync from spine"** only; never clobber hand-edits (§6.2). |
| **R56** | **Default format** | **Tied to Wan2.2 native — 1280×720 (16:9) 720P, fps 24/30**; 480P + upscaled presets too (§6.2). |
| **R57** | **Version count** | **Many** expected (small refined tweaks); version UI needs **grouping/search/naming** (§3.4, §6.3). |

| **R58** | **Version copy depth** | **Full independent duplicate** — own refs + **own LoRA copy** + voice; parent untouched. *(Re-training base: see R68 — default from-base.)* (§3.4) |
| **R59** | **Version base** | A new version can be copied from **any prior version**, not just the latest (§3.4). |
| **R60** | **Finalize a version** | **Pure intent** — declare "locked"; no concrete bar. After that, immutable; change ⇒ new version (§3.4). |
| **R61** | **Version vs profile** | **Author decides; loom never hints** (§3.4). |
| **R62** | **Node versioning** | Node edits create **node versions, each with its own clip**; **rendering a quality clip commits the version**; episode list **picks a node version's clip** (older OK); unrendered edits → light "out of date" warning, still usable (§6.4, §6.7). |
| **R63** | **Render ↔ clips** | Render **references** specific node-version clips (lean, not snapshot); staleness surfaces via the per-node out-of-date flag (§6.7). |
| **R64** | **Audio master** | **High-fidelity WAV/AIFF, 48/44.1 kHz, 16/24-bit stereo**; voice/lip-sync output resampled up to it (§6.2). |
| **R65** | **Episode length** | **Arbitrary (1 s+),** driven by the node list; **disk-based length guard** (§6.7). *(R161 changed the magnitude: a 30-min episode of versioned PNG-seq masters ≈ **150–250+ GB** at 720p, not ~100 GB — default cap raised to **250 GB**, R164.)* Multiple renders (acts) are first-class (R54). |
| **R66** | **Portability** | **AssetProfile export/import (all versions)** preferred over heavy project templates (§6.3). |

| **R67** | **Import collisions** | **Always import as a new profile** — no merging; **rename on name collision** (§6.3). |
| **R68** | **LoRA training base** | **Choosable; default "train from base model"**, optional "seed from parent LoRA" (§3.4). |
| **R69** | **No draft state** | **Unsaved edits lost on exit** (no autosaved drafts); **exit warning popup**. *(Clarified by R119: Saved≠Finalized — see §3.4 three-state model.)* |
| **R70** | **Voice sample** | Per character-version, **user-supplied audio file; loom does NOT record** (confirmed final by R84 — loom shows the master sample-text, you import a file recorded elsewhere) (§8.3). |
| **R71** | **Disk guard** | **Hard stop** when the **whole project** (clips+versions+renders+temp) nears the work-disk cap; raise cap / free space to continue (§4.2, §6.7). |
| **R72** | **Work disk = whole workspace** | The "scratch disk" (default `F:\_tmp`) holds the **entire project workspace**, not just LoRA temp (§4.2). |
| **R73** | **Global format** | One aspect/resolution/fps/audio **for the whole project, no overrides**; image assets cropped to master, draft videos keep aspect at minimal res; **different aspect = separate project** (§6.2). |
| **R74** | **Flat graph + single instance** | v1 = **single flat graph + Jump nodes** (no act/scene grouping); **no concurrent projects/instances** (§6.5, §8). |

| **R75** | **Version value** | **Organizational, not a training shortcut** — versions group under one character/lineage and pin as `@asset@vN`; each still trains from base (§3.4). |
| **R76** | **Voice capture** | *(Settled by R84 — loom does NOT record. Shows master sample-text; user records elsewhere and **imports**; minimal validation.)* (§6.2, §8.3) |
| **R77** | **Voice audio shape** | Voice is **mono, up-sampled to the project master, pannable in stereo space** (§8.3). |
| **R78** | **Exit & queue** | **Quit anytime; the queue persists and resumes.** Incomplete outputs + uncommitted edits are lost, but **queued tasks survive** (§6.6). |
| **R79** | **Project size cap** | **Per-project**, set at creation — **default 250 GB (R164, raised from 100 GB), min 50 GB, no max** (§6.2, §4.2). |
| **R80** | **Project storage** | **Manual file management** (no project manager v1). New project requires an **empty work folder** (warn if not) and **validates free space ≥ cap** (§6.2). |
| **R81** | **Draft resolution** | **Model's cheapest supported bucket matching the project aspect** (§6.2). |
| **R82** | **Identity anchor** | **On by default, opt-out**, available at *every* op (Stage-B, inference PuLID, constrained training, across versions); **anchor face is per-version**; framed as polish (§3.4, §8.3). |
| **R83** | **Render retention** | **Keep all render outputs unless deleted** — they're small (§6.7). |

| **R84** | **No in-app recording** | loom **shows the sample text; you import a file recorded elsewhere**; **minimal** validation. No microphone handling (§6.2, §8.3). |
| **R85** | **Sample text** | **English only, per character** (§6.2). |
| **R86** | **Identity anchor reach** | Optional **at Stage-B expansion** (postprocess to one face → LoRA trains on consistent images, no training constraint needed); plus optional inference PuLID + optional constrained training. **LoRA + prompt injection do most preservation** (§3.4, §4.1, §8.3). |
| **R87** | **Disk policing** | *(Superseded by R96 — loom DOES police running totals: warn <5% headroom, hard-stop <2%, on both project-cap and disk-free.)* (§4.2, §6.2) |
| **R88** | **Queue on relaunch** | **Resume *paused*** — review/cancel, then unpause (§6.6). |
| **R89** | **Voice not special** | Voice behaves like every other parameter: not editable on a finalized version; **changeable only via a new version** (copy old → edit) (§3.4). |
| **R90** | **Export stems** | **Separate stems (dialogue / SFX / music)**, not one mixed master (§6.7). |
| **R91** | **Launch requirement** | **Hard-require** the local stack (venvs + GGUF models); no graceful degradation (§11). *(The "GGUF models eventually in the repo" clause is **superseded by R160**: weights **never** live in git — an HF companion repo + the `models.json` manifest hosts them, with an on-demand fetch. Launch behavior refined by R163.)* |

| **R92** | **Sample text v1** | **One built-in default English passage** every character reads (better for clone consistency); custom/per-character later (§6.2). |
| **R93** | **Anchor default** | **On by default, opt-out** per character/version (§3.4). |
| **R94** | **Face-anchor stage** | New bootstrap step: **generate face portraits → pick one detailed face as the anchor**, **per version** (face can change: scar/tattoo) (§3.4, §4.1). |
| **R95** | **v1 audio = voice only** | **No episode audio, no mixer, no SFX, no music in v1** — only **node-level voice** (lip-sync). Music/SFX/mix → Resolve or post-v1 (§6.4, §6.7). |
| **R96** | **Disk thresholds** | Police **both** project-cap headroom and disk free space, continuously: **warn <5%, hard-stop <2%** (§4.2, §6.2). *(reverses R87)* |
| **R97** | **Launch check** | **Presence-only** for v1 (not version); get it working on author's rig first, then extract+ship to GitHub, then generalize (§11). *(R162: the GitHub repo `loom-loreweave-studio` now exists up front — the "extract" step is realized early; pipelines/models still referenced from the parent monorepo until vendored.)* |
| **R98** | **Lineage depth** | Record **`asset@version` + LoRA version** per clip (§11). |
| **R99** | **Next artifact** | **`kb-loom-p0.md`** — the detailed P0 build spec (this turn). |

| **R100** | **loom repo location** | Build **in-repo under `loom/`** (R97 extraction later) (`kb-loom-p0.md` §4). *(Superseded by R162: app source now lives in its own cloned GitHub repo at `loom/loom-loreweave-studio/`.)* |
| **R101** | **Orchestrator transport** | **FastAPI on `127.0.0.1`** (`kb-loom-p0.md` §3). |
| **R102** | **P0 done-line** | The **7-step acceptance test** is approved (`kb-loom-p0.md` §1). |
| **R103** | **Env strategy** | **Reuse the current `.venv` for now**; compose a separate `requirements.txt` **inside the app repo** (R162); the orchestrator shells to `python -m src.pipeline.…` via a **configurable pipelines root** pointing at the parent monorepo's `src/` (`kb-loom-p0.md` §4). |

| **R104** | **Style application** | **Fixed prepend + per-generation override checkbox** (`kb-loom-p1.md` §6). *(Amended 2026-06-10 during M3: the fragment is **APPENDED** after the character/cell prompt — front tokens dominate, the character prompt leads. Auto-apply + override checkbox unchanged.)* |
| **R105** | **Casting mix** | Pipeline subset **selectable per cast**; **clean/polish independently selectable** (`kb-loom-p1.md` §7). *(Status 2026-06-11: clean/polish **shipped** — generalized to orchestrator post-passes on ANY run; the per-pipeline subset ticker is **deferred** pending a monorepo `multi` CLI extension (same change unlocks per-member ideate params); the `fast`/`refined` presets are the v1 mix control.)* |
| **R106** | **Curated-set target** | **~25–40 kept refs** (`kb-loom-p1.md` §7.1; matches current LoRA-dataset practice). |
| **R107** | **Stage B = dataset recipe** | Stage B builds LoRA material via a **coverage-matrix recipe** (auto-generated prompts, **no freeform typing**; user picks recipe + curates) (`kb-loom-p1.md` §7.1). |
| **R108** | **3D deferred** | All 3D (`trellis2`/→3D) out of the MVP. *(Superseded by R128: now deferred to **P6**, not P3.)* |
| **R109** | **Stage roles** | **Stage A = manual experimentation; Stage B = structured LoRA-dataset building** (`kb-loom-p1.md` §7). |
| **R110** | **P1 guardrails** | Done-line independent of anchor/postproc/Flux2-spike; **zimage-first**; fix-wrapper-not-adapter; P0 = longest phase (`kb-loom-p1.md` §12). |
| **R111** | **Recipe presets** | **Comprehensive (~100, main chars)** + Full-coverage + Portrait-heavy + Full-body/outfit + **NPC-lite**; main chars need detail, NPCs don't (`kb-loom-p1.md` §7.1). |
| **R112** | **Character clause** | Defaults to the **stub prompt-template snippet** (L1→L2 consistency), editable once (`kb-loom-p1.md` §7.1). |
| **R113** | **Per-cell method** | **Auto-picked, exposed for manual finalization** — quality first (`kb-loom-p1.md` §7.1). |
| **R114** | **Anchor strength** | **Single default v1, adjustable per output image; SLM-driven per-image later** (`kb-loom-p1.md` §10). |
| **R115** | **LoRA trainer** | **ai-toolkit default (optimized)** + **diffusers-PEFT advanced** (deep control) backend (`kb-loom-p2.md` §8). |
| **R116** | **P2 captioning/VLM** | **Template-only captions for v1, no VLM**; **template + optional VLM enrichment + comprehensive project-wide VLM context → P4** (`kb-loom-p2.md` §6, §9). |
| **R117** | **Trainer settings** | **Optimal default preset per base model; advanced knobs on demand** (`kb-loom-p2.md` §8). |
| **R118** | **Staged training jobs** | Auto-generate the training job spec but **don't auto-queue**; author **explicitly adds it to the queue** when finalized (`kb-loom-p2.md` §5). |

| **R119** | **Saved vs Finalized** | Three states: **Unsaved draft (lost on exit)** · **Saved/committed but unfinalized (persists, editable, trainable)** · **Finalized (locked)**. "Lost on exit" = *unsaved*, not unfinalized (`kb-storyboard01.md` §3.4, clarifies R69). |
| **R120** | **P2 no-anchor readiness** | On-model proxy works **with or without** a face anchor: anchor-distance if present, else **set self-consistency (centroid outliers)** — so P2 needs no required anchor; P1 guardrail intact (`kb-loom-p2.md` §7). |
| **R121** | **P1 minimum adapters** | The **P1 done-line requires `multi` + `_img2img` + `sd35`** (plus `zimage` from P0). zimage-only is just the M1 library-scaffold step (`kb-loom-p1.md` §5/§13). *(As shipped 2026-06-11: `_img2img` = a **shared lib inside the zimage/sd35 workers**, not a standalone adapter — intent satisfied by `multi` + `sd35` + `zimage` with img2img/inpaint modes.)* |
| **R122** | **Style LoRA in P2** | **Declared only** — P2 does **not** build a usable style-LoRA path. Style-LoRA training waits until **multi-LoRA stacking** makes it usable. *(Amended by R147: multi-LoRA stacking + usable style LoRA now **P5**, not P6.)* (`kb-loom-p2.md` §2/§13; `kb-loom-p5.md` §11.3). |
| **R123** | **Staged-job storage** | Staged jobs live in a **separate `jobs/staged.json`** (durable), **not** in `queue.json`; "Add to queue" promotes `staged → queued` (`kb-loom-p2.md` §5; `kb-loom-p0.md` queue). |

| **R124** | **Continuity strategies** | Strive for **pixel-identical**; **all three available** — pixel-identical, **seam-welding** (RIFE/FILM interpolation · optical-flow · v2v bridge), dissolve fallback (`kb-loom-p3.md` §7). |
| **R125** | **P3 stays one doc** | Don't fracture P3 into sub-docs (drift risk); internal sub-phases P3.1–P3.3 (`kb-loom-p3.md` §2). |
| **R126** | **Drive tiers** | **Both `wan2 animate` + `hunyuan i2v`**, user-selectable per segment (`kb-loom-p3.md` §6). |
| **R127** | **Lip-sync models** | **2–3 models**, modular/user-selectable (Wav2Lip/SadTalker/LatentSync-class); no preference yet (`kb-loom-p3.md` §9). |
| **R128** | **3D → P6** | **Defer all 3D/depth proxies to P6** (no meaningful impact now); removed from P3 (`kb-loom-p3.md` §11). *(supersedes R108's P3 placement)* |
| **R129** | **Welder bar** | Seam-welder acceptance = **"invisible at play speed"** for v1; frame-scrub-clean not required (`kb-loom-p3.md` §7). |
| **R130** | **Welder models** | Ship **both RIFE + FILM** (frame interpolation), modular; v2v-bridge = heavier fallback (`kb-loom-p3.md` §7). |
| **R131** | **Default drive tier** | **`wan2 animate`** is the default (fidelity priority); both wan2/hunyuan available (`kb-loom-p3.md` §6). |
| **R132** | **Voice timing** | **Clip-first with timecoded dialogue cues** — forced alignment (TTS-native + whisperX/aeneas/MFA) gives word-level timings; clip ≥ cue span; overflow → next node; "fit clip to line" is a later assist (`kb-loom-p3.md` §9.1). |
| **R133** | **LTXV-extend spike** *(ratified)* | **Pull LTXV-extend native long-form forward as a P3 *spike*** (backlog item 3) — 4th continuity strategy that removes the seam; if it holds on-rig it's the default, else seam-welding. Stack: LTXV spine → wan2-animate drive pass + upscaler (`kb-loom-p3.md` §7d/§13/§19 P3-14). *Default-safe: P3 ships chain+weld regardless.* *(Amended by R147: **hardening now P5**, not P6 — `kb-loom-p5.md` §11.2.)* |
| **R134** | **Flow↔shot binding** | A visual Flow node **binds an existing shot OR spawns a new one** inline (handoff to Shots); node = narrative, shot = visual, pin `asset@version` (`kb-loom-p4.md` §4/§5). |
| **R135** | **Variables minimal v1** | Flow variables/conditions = **bool/enum flags + simple conditions** for v1 (branch the storyboard, not a game engine); **expandable later** (`kb-loom-p4.md` §6). |
| **R136** | **Agent GPU = stage-only** | The Muse agent makes typed edits freely but **stages** GPU/training jobs (`jobs/staged.json`, R123); author explicitly queues; **never auto-spends GPU** (`kb-loom-p4.md` §9/§10). |
| **R137** | **Vision = two tools** | *Describe/judge* → generative **`Qwen3-VL-4B-Instruct` Q8** (~4.8 GB, handrefiner pattern): caption enrichment + critique. *Rank by similarity* → **`Qwen3-VL-Embedding-8B` Q8** (~7.5 GB) +optional **`Reranker-8B` Q8** (~8.2 GB), **all on disk**: on-model scoring + curation ranking. **Q8 8B fits 16 GB because vision runs exclusively (R141)**; f16/bf16 too big → Q8; more compact variants if needed; ROCm can-run check. v1 = caption-enrich + on-model-score (`kb-loom-p4.md` §12). |
| **R138** | **Project context text-only v1** | Project-wide context = **text-structured** for v1 (world/style/cast/rules/synopsis). The embedding **model ships in P4 for scoring** (R137); only the **persistent GraphRAG/retrieval index** is deferred — it **reuses the same model** (no new model later, just the graph/vector index build) (`kb-loom-p4.md` §13; refined by R170). |
| **R139** | **Graph storage** | Flow = **one-file-per-node + rebuildable thin `graph.json` index** (atomic, corruption-resilient, Codex-aligned) (`kb-loom-p4.md` §4). |
| **R140** | ~~**Play-mode NPC voicing**~~ | **Superseded by R143** (no ad-hoc AI in play mode). |
| **R141** | **AI = queue jobs** | All model-loading AI work (VLM caption/score, embedding/reranker, Muse agent plan+exec, P3 continuity director) is a **first-class AI job on the single-GPU queue**; the one worker guarantees **AI never runs concurrently with gen/training** and **nothing AI auto-triggers during a job** (`kb-loom-p4.md` §11.1; sharpens R21). |
| **R142** | **Interactive AI idle-only** | Chat dock + inline assist run **only when the queue is idle** (greyed during an active GPU job; live when it drains); heavy/batch AI always queues (`kb-loom-p4.md` §11.1). |
| **R143** | **No ad-hoc NPC voicing** | Play mode **never** auto-invokes the SLM (supersedes R140) — authored dialogue or nothing; desired NPC audio = **P3 voice cue (R132)** or a deliberate **queued** generation (`kb-loom-p4.md` §6). |
| **R144** | **One generative model** | `Qwen3-VL-4B-Instruct` Q8 serves **all** generative roles (dispatch + creative + vision describe/judge) — light/fast, one resident context, **dissolves cross-model-context**. Only **retrieval** (`Qwen3-VL-Embedding-8B` Q8; Reranker optional) is separate → **two models v1**. Registry stays modular (R4); simplifies §7.4's 3-model table (`kb-loom-p4.md` §11). |
| **R145** | **Context mgmt: text=memory, KV=cache** | `project_context.json` (compact digest, dirty-flag rebuilt) is the **durable system of record** (survives unload-before-every-job, R141); the **KV-cache is only a prefix-reuse speed layer**, invalidated on digest change. Scale: compact digest v1 + typed fact sidecar → GraphRAG later (= deferred index, R138/R170) (`kb-loom-p4.md` §13). |
| **R146** | **Shot ownership 1:1 (binding = sole ownership)** | Reconciles R23+R134: every L3 shot is owned by **exactly one** Flow node. **Binding** attaches an *unbound* shot (or re-selects a version of this node's own shot) and **transfers sole ownership** — **never a shared/reusable reference**. A look reused elsewhere = a **duplicated** shot in a new node. Keeps undo/lineage/P5-picker unambiguous (`kb-loom-p4.md` §4). |
| **R147** | **P5/P6 rebalance — hardening → P5** | Move **deeper Flux2 multi-ref, LTXV-extend hardening (R133), multi-LoRA stacking + usable style LoRA (R122), and the postproc expansion** from **P6 → P5** as a **"Track B" production-hardening** track. **Track A (Episode/Export) stays the MVP headline and ships first; Track B is additive** (model-/ROCm-heavy, plugs into L2/L3 not L5/R18). **P6 narrows to 3D/`trellis2` + effect plugin API + engine/DCC export** (`kb-loom-p5.md` §2/§11; amends R122/R133). |
| **R148** | **Episode decoupled from graph** | The episode = a **hand-built render list**, the author composes the order **manually** — it can differ substantially from the Flow narrative; **no graph-derived / play-mode-capture path in v1** (`kb-loom-p5.md` §6). |
| **R149** | **Default transition = hard cut** | Hard cut (zero-overlap) is the default; **cross-fade/dissolve need an overlap window** that consumes ~N frames from each adjacent clip's boundary (clips are exact-length, no handles) (`kb-loom-p5.md` §7). |
| **R150** | **Render = queue job** | The render/encode runs as a **queue job** (progress/cancel/serialization) — an encode type, not an AI tenant (`kb-loom-p5.md` §8). |
| **R151** | **Export = EDL + FCPXML** | Editorial export = **EDL + FCPXML + per-node media + voice** (FCPXML primary, EDL fallback); **free-Resolve import compatibility is a build-time validation gate** (`kb-loom-p5.md` §9). |
| **R152** | **Subtitles `.srt`** | Export `.srt` from dialogue-node text, v1 (`kb-loom-p5.md` §9). |
| **R153** | **Render gate** | **Block** the render on a node with **no** clip; **warn** (advisory) on a stale clip (`kb-loom-p5.md` §6). |
| **R154** | **Pitch one-pager** | **Text + a few key stills** from the chosen clips (`kb-loom-p5.md` §10). |
| **R155** | **Postproc priority** | **Upscaler first** (image SUPIR + video-SR — the R133-stack joint), then IC-Light/ADetailer/video-face-restore; denoise optional (`kb-loom-p5.md` §11.4). |
| **R156** | **LTXV-harden gated; Track A first** | LTXV-extend hardening (§11.2) is **gated on the P3 spike (R133) proving out on-rig** — else skip, P3 chain+weld stays default. **Track A (episode) ships before Track B** (ratifies R147) (`kb-loom-p5.md` §11.2/§14). |
| **R157** | **Episode editor = single-lane timeline** | Clips end-to-end on **one video lane**, drag to reorder; **each junction = a transition rendered as a draggable overlap zone**; **per-transition trim-into-content (default) *or* hold/freeze toggle** (answers F1 "both"). **Not a multi-lane NLE** — R29 (one clip per point) makes extra lanes pointless, R18 keeps editing basic. Voice lane below (baked, read-only) (`kb-loom-p5.md` §5/§7). |
| **R158** | **Chapter markers exported** | Muse's chapter markers export **into the FCPXML timeline** (Resolve reads them) **+ a sidecar list** (`kb-loom-p5.md` §9). |
| **R159** | **One job lifecycle (`resumable` flag)** | Every job carries **`resumable`** (default `false`; `true` only for checkpointing workers — P2 training). On restart: **graceful×non-resumable** → `queued`+partial discarded (R69/R78); **crash×non-resumable** → `failed`, user retries (P0-15); **either×resumable** → resume from last checkpoint (P2-10). Queue always returns **paused** (R88). Reconciles R69/R78/R88 (`kb-loom-p0.md` §7). |
| **R160** | **Weights out of git → HF companion repo** | Model weights **never live in git** (too large). M0 **scaffolds into the existing app-repo clone (R162)** and commits; the `.gitignore` excludes all weight caches (bulk weights live in the parent monorepo, outside the app repo); a shipped **`models.json`** lists every model → **Hugging Face companion-repo URL + sha256 + target path + phase**. Launch presence-check (R91/R97) **offers an explicit, checksum-verified on-demand fetch** of missing phase-essential weights instead of dead-ending. Project workspace stays non-git (§3.3) (`kb-loom-p0.md` §11.1). |
| **R161** | **Node clip = lossless frame-seq master, not mp4** | The node's finalized clip is stored as a **lossless PNG sequence** (`clip_frames/`, decoded pre-mux — lossless vs the model's 8-bit output; EXR post-v1), **unifying with the §7 continuity/batch-i2i work** (operate on frames, no codec round-trip), with a small **`clip_proxy.mp4`** for UI playback. **Retention: keep the master + proxy for EVERY node version, never auto-pruned** (author call — masters/proxies are costly to recreate; recreation cost > disk cost). **The disk consequence (a PNG seq is ~30–50× an mp4) is borne by the project-cap + disk-guard (R96) and manual file management (R80)** — expect to raise the cap on a full episode. **L5 export reads the master and the author picks image-sequence *or* a visually-lossless encode** — lossy only at that final, opt-in step (`kb-loom-p3.md` §4 / `kb-loom-p5.md` §9). |
| **R162** | **App repo = cloned `loom/loom-loreweave-studio/`** | The loom **app source lives in its own GitHub repo** (`github.com/stubz100/loom-loreweave-studio`), **cloned to `loom/loom-loreweave-studio/`** inside the parent monorepo — **pulling R97's "extract a public repo" forward** (the public repo exists first; dev happens in it). **Supersedes R100** (plain in-repo `loom/`). M0 scaffolds *into the existing clone* (no `git init`, R160). **Pipelines + model weights stay in the parent monorepo** (`src/pipeline/`, `src/village_ai/models/`), **referenced not vendored**; the orchestrator resolves a **configurable pipelines/models root** (extends R103 interpreter pinning), the parent `src/` during dev. Vendoring/submodule for a self-contained release stays under R97 (`kb-loom-p0.md` §4/§13). *(Practice update 2026-06-11: per-phase pipeline **code IS now vendored** under the app repo's `pipelines/` — zimage at P0-review, the `multistack` mirror at P1/M2 — under the rule **changes land in monorepo `src/pipeline/` FIRST, then re-vendor byte-identically**, guarded by vendor-sync hash tests. Weights still never vendored, R160.)* |
| **R163** | **Launch: code fails fast, weights fetch-then-fail** | Reconciles R91/R97 ("fail if missing") with R160 ("offer fetch"). A missing **phase-essential code component** (pipeline venv/CLI) → **fail fast, refuse to start** (code can't be auto-fetched). A missing **phase-essential model weight** → **offer an on-demand, checksum-verified HF fetch (R160)**; launch proceeds if it succeeds and **fails fast only if the fetch is unavailable, declined, or fails**. Non-active-phase items are reported, not fetched (`kb-loom-p0.md` §1/§11/§11.1). |
| **R164** | **Cap default 250 GB + footprint estimator** | Because **R161** keeps a lossless PNG-seq master **per node version** (a 30-min 720p episode ≈ **150–250+ GB**), the per-project cap **default rises 100 → 250 GB** (min 50, no max, amends R79). Project creation gains a **footprint estimator** — from target episode length × resolution it projects the likely PNG-master size and **suggests/warns on the cap**. The disk-guard still **warns/hard-stops rather than deleting a master** (R96) (`kb-loom-p0.md` §5 / `kb-loom-p3.md` §14). |
| **R165** | **P5 = alpha (v1) gate; P6 = flexible overflow** | **P5 is the final milestone for alpha/v1 — full core functionality ships by end of P5**, achieved **without scope changes to the P0–P5 phase docs**. **P6 has NO essential deliverables** for core function: it holds **deferred polish** (all 3D/`trellis2` + proxy-depth R128, effect plugin API R37, engine/DCC export to Unreal 5.7+/Blender) **plus any unforeseen overflow** that surfaces during P0–P5 development. P6's scope is deliberately **flexible/open-ended; nothing in P6 gates v1**. This principle holds throughout development (`kb-loom-p6.md` §1). |
| **R166** | **P3 continuity fallback = one finalized clip (join stays in P3)** | A node/shot-version always exposes **exactly one finalized clip** (a single playable file). Even in the continuity fallback (no welder/extend yields a seamless single generation for some segment pair), **P3 concatenates the segments into one continuous file at Finalize** — the **join stays in P3, never deferred to P5**. The record carries one `finalized_clip` (with an optional internal `segments[]` for provenance); **P4/P5 always bind/pick exactly one finalized clip per `shot_version`** (resolves the P3↔P4/P5 data-model conflict). A failed pair may have a more visible seam, but **"invisible at play speed" (R129)** stays the bar (`kb-loom-p3.md` §1/§7, `kb-loom-p4.md` §6). |
| **R167** | **Alpha (v1) gate = P5 Track A (M7)** | Refines R165. The v1 alpha gate is **P5 Track A's M7 episode-acceptance** done-line — when M7 is green the product is feature-complete for v1. **P5 Track B (M8–M10) is post-alpha hardening**: it lives in P5 but **does NOT gate v1** (postproc upscaler, LTXV-extend hardening, multi-LoRA/style-LoRA). "No P0–P5 scope change" (R165) still applies to Track A (`kb-loom-p5.md` §16, `kb-loom-p6.md` §1). |
| **R168** | **P3 owns the minimal AI-job/tenant; P4 generalizes** | P3 brings the creative SLM online for the continuity director, so **P3 builds the minimal AI-as-queue-job + single-tenant mutual-exclusion** it needs (load/unload one model, never concurrent with gen on the single GPU — R141). **P4 M5 generalizes** it into the full modular tenant (registry, KV prefix-cache save/restore, idle-gating, chat/inline). Same rule (R141), built incrementally — like the adapter contract (`kb-loom-p3.md` §12, `kb-loom-p4.md` §13). |
| **R169** | **P2 M2 = trainer skeleton, not the done-line** | P2's **M2** ("trainer as a queued job") is a **skeleton** — it proves a LoRA trains/loads via the queue with a default preset, but is **not** the P2 done-line. The done-line (template-caption → readiness "good to train" → stage → train, §1) is reached **after M3 (template captions) + M4 (proxy readiness)** (`kb-loom-p2.md` §1/§16). |
| **R170** | **GraphRAG posture** | Long-term retrieval should be **GraphRAG-style**: typed project facts + stable IDs + graph/vector retrieval, not plain vector-only RAG. **P2/P4 write graph-ready artifacts now** (`training_context.json`, `caption_policy.json`, `project_facts.jsonl`); **P4 ships compact text context only** for v1; **persistent GraphRAG index/build/query is deferred to P6/post-v1** so it does not block LoRA training or alpha. |

### 10.1 Previously open — round 12 (resolved by R100–R103)

Kept for traceability. These P0 setup questions were answered in `kb-loom-p0.md` and recorded as
R100–R103 above:

1. **Repo location for loom** → originally in-repo under `loom/` (R100); **now its own cloned GitHub repo at `loom/loom-loreweave-studio/` (R162)**.
2. **Orchestrator transport** → **FastAPI on `127.0.0.1`** (R101).
3. **P0 "hello-world" acceptance** → **the seven-step P0 acceptance test** (R102).
4. **Python version / env manager** → **reuse the current `.venv` for now**; split requirements
   when extracting the public repo (R103).

> **Codex comment - recommendation:** Decide the persistence and collaboration model first;
> it affects IDs, graph storage, locking, large-file policy, and migration strategy. Then
> decide the MVP shell technology and initial training family. Keep live NPC dialogue and
> articy round-trip outside the critical path until the authored workflow is proven.
>
> **Additional decisions to add:** define the committed-versus-cached artifact policy,
> stable ID and schema migration policy, whether shots are reusable across flow nodes,
> supported export profile for the first engine handoff, and whether the SLM is unloaded
> during GPU-heavy jobs.

---

## 11. Relation to existing repo

- Generation backends already exist under `src/pipeline/*` — the platform is an
  orchestration + UI layer, not new model code.
- The asset/3D path (`trellis2`) already has the manifest/preset/CLI shape this design assumes
  (`kb-trellis2.md`).
- The continuity SLM is specced in `kb-slm.md`; **GGUF models are already on disk** in
  `src/village_ai/models/` — SmolLM3, Phi-4-mini, Qwen3.5-9B/27B, and **Qwen3-VL-4B/30B (+ mmproj)**
  — plus a `kv_cache/` directory proving the **context-preserving start/stop** pattern (§7.4).
- **Hard-requires the full local stack; presence-check only for v1 (author, round-10 answer 8;
  round-11 answer 6).** loom does **not** degrade gracefully — it **requires the pipeline venvs
  and the GGUF models** (`src/village_ai/models/`) to launch, erroring clearly if missing. For
  v1 it only **checks presence, not version** (version-pinning is post-v1). **Weights never live in
  git (R160):** the app ships a **`models.json` manifest** pointing at a **Hugging Face companion
  repo**, and a missing phase-essential weight is **fetched on demand** (checksum-verified) rather
  than committed — a missing *code* component still fails fast, a missing *weight* offers the fetch
  first, failing only if that fetch is unavailable/declined/fails (**R163**). The app source already
  lives in its own GitHub repo (`loom-loreweave-studio`, **R162**); the bulk weights sit in the
  parent monorepo's `src/village_ai/models/`, outside the app repo; user Story Bundles stay outside
  git (§3.3).
- **Lineage records versions (author, round-11 answer 7).** Each clip's lineage edge records not
  just job/prompt/seed but the exact **`asset@version`** *and* **LoRA version** that produced it —
  so months later you can answer "why does this old clip look different?" Cheap metadata, high
  debugging value.
- **Postprocessing has a real foundation:** `src/pipeline/postproc/` (HandRefiner implemented;
  catalogue + architecture in `kb-postproc-img.md` and `kb-postproc-img-imp*`). It already runs
  **Qwen3-VL as an on-demand `llama-server`** for grounded region detection — the exact start/stop
  + VLM pattern loom generalizes (§7.4, §8.3).
- The cross-pipeline cooperation, animation loop, LoRA, layering, and depth-proxy mechanics are
  in `kb-pipelines01.md` and should be treated as the engine spec for L3.
- Confirmed targets: engine **Unreal Engine 5.7+**, DCC **Blender 5.1.1 + BlenderMCP (:9876)**,
  GPU **RX 9070 XT (16 GB)** — both Unreal and Blender are installed on the author's PC, and the
  single-GPU-worker queue + `low_vram`/offload defaults are designed around the 16 GB budget.
  (The earlier "Unity" mention was a slip of the tongue; no Unity. Matches stored memory.)
- **Documentation convention:** this file (`kb-storyboard01.md`) is the living overview;
  each implementation phase gets its own KB file **`kb-loom-p0.md` … `kb-loom-p6.md`** (author's
  naming). git versions the loom **application** only; Story Bundles are user data (§3.3).

> **Codex comment - understanding:** This document should remain the product/UI spec while
> the linked KB files remain the pipeline capability specs. During implementation, each UI
> action should map to a documented worker capability and record that capability version in
> its job manifest.
>
> **Concern / decision:** Some assumptions are project-memory claims rather than verified
> repo contracts, especially target versions and available CLI behavior. Mark those as
> provisional until checked against the installed toolchain and wrapper implementations.

## Source files checked

- `.github/copilot/kb-pipelines01.md` (engine spec — primary)
- `.github/copilot/kb-slm.md` (continuity director / NPC dialogue SLM)
- `.github/copilot/kb-trellis2.md` (3D asset stage, manifest/preset/CLI shape)
- `.github/copilot/kb-postproc-img.md` (postproc toolkit catalogue + `src/pipeline/postproc/` architecture)
- Microsoft GraphRAG docs + "From Local to Global: A Graph RAG Approach..." (research update
  2026-06-14: GraphRAG is best treated as a post-v1 retrieval/index layer over typed project facts,
  not as a P2 trainer dependency)
- `src/village_ai/models/` (on-disk GGUF SLMs + Qwen3-VL + `kv_cache/`)
- `src/pipeline/postproc/handrefiner/run_pipeline.py` (existing Qwen3-VL start/stop `llama-server` pattern)
- Project memory: engine-and-dcc-decision, project-posture, doc-authoring-suffix-convention

> **Codex comment - understanding:** These are the right design inputs for this proposal.
> Before implementation, turn the assumed worker behaviors into a checked capability matrix
> against the current CLI wrappers so the UI exposes only supported actions.
