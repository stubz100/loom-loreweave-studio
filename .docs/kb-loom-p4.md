# Loreweave Studio — P4 build spec (Flow graph + full Muse + VLM online)

Created: 2026-06-03
Status: spec (not yet implemented; depends on P0 + P1 + P2 + P3)
Parent: [`kb-storyboard01.md`](kb-storyboard01.md) (overview + decision record R1–R170)
Predecessors: [`kb-loom-p0.md`](kb-loom-p0.md) · [`kb-loom-p1.md`](kb-loom-p1.md) · [`kb-loom-p2.md`](kb-loom-p2.md) · [`kb-loom-p3.md`](kb-loom-p3.md)
Engine: [`kb-pipelines01.md`](kb-pipelines01.md) · SLM/VLM: [`kb-slm.md`](kb-slm.md) · VLM proven path: `src/pipeline/postproc/handrefiner/`

---

## 1. Purpose & the P4 done-line

P4 is the **L4 Flow layer** — the **non-linear narrative graph** that makes loom an
articy:draft *replacement*, plus the **full Muse assistant** (inline + chat + agent) and the
**VLM online** with a **project-wide context**. After P0–P3 the author can make reproducible,
voiced, animated **shots**; P4 is where those shots become a **branching story** you can walk,
and where the assistant stops being a narrow continuity-director (P3) and becomes the
Copilot-style collaborator the author asked for (R4).

**P4 done-line (the "this is a storyboard tool now" moment):**

> Build a small **Flow graph** (Start → a few Dialogue/Shot nodes → a Choice → a Jump) in the
> Flow workspace → **bind each visual node to an L3 shot's clip** (built in P3) and pin
> `asset@version` → set one or two **variables/conditions** → hit **▶ Play from here** and watch
> the branching story play in the reader pane → ask **Muse (chat)** to draft a line and **propose**
> a branch, approve it → run **Muse (agent)** on a small goal and approve its plan → have the
> **VLM** enrich a caption / score an image with **project context**. Reopen the project and the
> graph, bindings, variables, and Muse/VLM config are intact.

If a fresh user can author a branching, playable storyboard over the P0–P3 spine, get useful
assistant help that is always logged + undoable, and let the VLM see images with project
awareness, **P4 is done**.

---

## 2. Scope — one doc, three internal sub-phases

P4 bundles three large but separable strands. Like P3 (R125) it stays **one doc**, built in
**internal sub-phases** that each demo independently:

| Sub-phase | Builds | Risk |
| --- | --- | --- |
| **P4.1 Flow editor** | React Flow canvas, node palette, inspector, outline; node↔shot binding; variables/conditions; **play mode**; graph validation | medium — large UI, but no new model/GPU risk |
| **P4.2 Muse (full)** | inline assist + **chat dock** + **agent mode** (plan→approve→queue); tool surface; modular model registry; on-demand tenant | **high** — first agentic surface; small-model reliability |
| **P4.3 VLM + project context** | Qwen3-VL online (handrefiner pattern); **project-wide context**; VLM-assisted captioning/scoring (deferred from P2, R116) | medium-high — new tenant + context plumbing |

**In scope:** everything in §3. **Out of scope (deferred):** §18 — Episode/Render/export + the
production-hardening track (P5, R147), all 3D (P6, R128), effect plugin API + engine export (P6),
the **persistent GraphRAG/retrieval index** (the embedding *model* ships in P4 for *scoring*, R137 —
only the **stored graph/vector index + retrieval/query layer** is deferred; v1 context is
text-structured), hierarchical act/scene
grouping of Flow nodes (post-v1, R74), raising Muse autonomy above propose-and-approve (R14).

---

## 3. What P4 adds to the spine

- **L4 Flow graph (the articy layer):** a typed narrative runtime editor — nodes, edges,
  variables, conditions, play mode — **v1 = single flat graph + Jump nodes**, no act/scene
  grouping (R74). Nodes are **independent entities**, branch **or** run parallel, pin
  `asset@version`, and **own/bind a clip** (R23).
- **Full Muse (R4):** the three-mode assistant (inline → chat → agent), modular model registry,
  on-demand tenant. P3 brought only the narrow continuity-director online; P4 brings **chat +
  agent + inline** everywhere, **propose-and-approve only** (R14), every action through the
  orchestrator write API + queue (never direct), fully logged in lineage (R98).
- **Vision online — two tools (R22/R116/R137):** generative `Qwen3-VL-4B-Instruct` (handrefiner
  pattern) for **caption enrichment + critique**, and `Qwen3-VL-Embedding-8B` for **on-model
  scoring/curation**. Both run as **exclusive queued AI jobs** on the single GPU (R141).
- **Comprehensive project-wide context:** a structured, cached **project context** (world, style,
  cast, rules, synopsis) assembled during L1/L2/L4 authoring, fed to SLM **and** VLM so help is
  *project-true* ("this is Mara with red hair in the workshop"), not generic (R116).

P4 reuses the **P0** queue/adapter-contract/disk-guard/write-API, **P1** assets/versioning + the
project context seeds, **P2** LoRAs + the readiness proxies (now VLM-enrichable), and **P3** shots
+ node versioning (the clips Flow nodes bind to).

---

## 4. Data model (Flow graph + project context)

```
flow/
├── graph.json                  # INDEX only — node id list + edges + layout positions
│                               #   (rebuildable from node files; small, the merge-prone part)
├── nodes/
│   └── node_<id>.json          # ONE node per file (atomic write, corruption-resilient, P0 §6)
│                               #   type, title, body/lines, speaker=asset@version,
│                               #   bound_shot (shot id + chosen node-version), choices[],
│                               #   conditions[], on_enter mutations[], jump_target, comment
├── variables.json              # typed variable declarations (name, type, default) — from L1 rules
└── playstate/                  # transient sandbox states for play mode (not canonical)

context/
├── project_context.json        # assembled cache: world summary, style fragment, cast manifest
│                               #   (each AssetProfile: name/class/versions/trigger/anchor thumb),
│                               #   game-rule variables, flow synopsis; rebuilt on edit (dirty flag)
├── project_facts.jsonl         # rebuildable typed facts/relations for future GraphRAG (CPU only)
└── graph_index/                # persistent GraphRAG/vector index → later, not v1
```

New RECORDS / fields:

| Record | Where | Holds |
| --- | --- | --- |
| **FlowNode** | `flow/nodes/node_<id>.json` | type, title, body/lines, `speaker = asset@version`, **`bound_shot` (shot id + node-version)**, choices/edges, conditions, on-enter mutations, jump target |
| **FlowGraph index** | `flow/graph.json` | node-id list, edges, canvas layout — **rebuildable**, the only git-merge-prone file (kept thin per Codex) |
| **Variables** | `flow/variables.json` | typed variable declarations (bool/enum/int) + defaults |
| **ProjectContext** | `context/project_context.json` | the assembled, cached project-wide context for SLM/VLM |
| **ProjectFacts** | `context/project_facts.jsonl` | rebuildable typed facts/relations (`asset_version_has_lora`, `flow_node_uses_asset_version`, `shot_generated_by_job`, etc.) for later GraphRAG |
| **MuseAction (lineage)** | existing lineage (R98) | every Muse edit/job attributed to `"Muse (agent)"` vs human |

**Node ↔ shot binding (the key relationship — R134):** a Flow node carries **narrative role**
(graph position, dialogue, choices, conditions, variable mutations); an **L3 shot (P3)** carries
the **visual** (the rendered clip + its versions). A visual Flow node (`Dialogue`/`Shot`) holds a
**`bound_shot`** pointer to a shot id + the chosen shot version, plus its `asset@version` pins.
The graph is authorable **before** any shot exists (story-first), and a node can **spawn a new L3
shot** (handoff to the Shots workspace) or **bind an existing one**.

> **Ownership rule — binding is sole ownership, never a shared reference (R146, reconciles R23+R134).**
> Every L3 shot is owned by **exactly one** Flow node (`owner_node` field). "**Bind existing**"
> attaches an **unbound** shot (one built in the Shots workspace but not yet owned) — or re-selects a
> *version* of this node's own shot — and **transfers sole ownership** to the node. A shot that's
> already owned **cannot** be bound to a second node; to use a look elsewhere you **duplicate** it
> into a new node-owned shot (this is the "no reusable shots / revisiting = a new shot" rule, R23).
> So there is never a shared/reusable shot reference — the ownership graph stays 1:1 and unambiguous,
> which keeps undo, lineage, and the P5 render-picker simple. (Deleting a node releases or deletes
> its shot.)

> **Terminology — "node" is overloaded; here's the resolution.** P3 calls a *shot* a "node" and
> versions it (R62). P4 calls a *Flow graph node* a "node." To keep them straight: **versioning
> lives on the L3 shot (R62)**; a **Flow node is *not* separately versioned in v1** — its dialogue/
> choice edits are plain edits. A Flow node simply **binds a shot and picks which shot version's
> clip** to show (this is how storyboard §6.5's "a node has versions" is *realized* — at the shot
> layer). L5 (P5) then picks which bound clip enters the episode.

---

## 5. The Flow editor (L4 canvas)

A **React Flow** node-graph canvas (`kb-storyboard01.md` §6.5, §8): left = flow outline +
variable list; center = the graph; right = the selected-node inspector.

**Node palette (articy parity + generative hook):**

| Node | Purpose | Generative hook |
| --- | --- | --- |
| **Start / Hub** | entry / branching junction | — (exactly one playable entry per flow — §7) |
| **Dialogue** | a line/exchange | speaker = `asset@version`; **binds a clip** (the beat's visual) |
| **Choice** | player options (1..n outgoing) | each choice edge can **require/set** variables |
| **Condition** | branch on variables/flags | reads L1 Game-Rule vocabulary |
| **Action/Instruction** | mutate variables, grant items, move time | — |
| **Shot/Scene** | pure visual beat | **binds one finalized clip** (P3 joins its 1..N continuous segments into a single file — R166); previews inline |
| **Jump** | non-linear link to any node | enables the non-linear timeline (the v1 structural tool) |
| **Comment** | author notes | — |

- **Every visual node binds a clip — no reusable shots (R23/R146).** A node is the **sole owner** of
  its shot; binding claims an unbound shot (never a shared reference); revisiting a place = a new
  node with a **duplicated** shot, not reuse (§4 ownership rule).
- **Nodes pin `asset@version`** (§3.4) — the beat records exactly which look was used; re-pinning is
  an explicit edit, earlier nodes keep older pins, so the storyboard stays correct as the world
  changes.
- **Nodes branch *or run in parallel*** (R-parallel) — concurrent threads the L5 main-path picker
  (P5) later linearizes.
- **v1 is a single flat graph + Jump nodes (R74)** — no act/scene/chapter folders; the outline
  lists nodes flat, Jumps stand in for chapter links. Hierarchical grouping is post-v1.

---

## 6. Variables, conditions, play mode

- **Variables** come from L1 **Game Rules** (optional layer); the inspector offers them as **typed
  dropdowns** — **minimal v1: bool/enum flags + simple conditions** (R135), expandable later — so
  branches stay valid.
- **Conditions** read variables/flags; **Choice edges** can require/set them; **Action** and
  **on-enter** mutate them. Typed, explicit — never free-form.
- **Play mode (`▶ Play from here`):** walks the graph from a node, **resolving conditions against a
  sandbox variable state** (`flow/playstate/`, transient — never canonical), showing each node's
  bound clip + dialogue in a **reader pane** (with a **max-step loop guard**).
- **No ad-hoc AI in play mode (R143, supersedes R140).** Play mode **does not** invoke the SLM to
  voice unscripted lines — that would auto-fire a model unpredictably, against the single-GPU rule
  (§11.1/R141). A node either shows its **authored** dialogue, or nothing. Want NPC audio in the
  final clip? Author a **P3 timecoded voice cue** (R132). Want a line drafted? Use **chat/inline when
  idle** (R142) or a deliberate **queued** generation — never an automatic play-time trigger.
- **The episode is the north star, not a game (§2 overview):** variables/conditions exist to make
  the storyboard branch, not to ship a rules engine — so v1 logic stays deliberately light (R135).

---

## 7. Graph validation (Codex concern — specify early)

Validation runs continuously and gates play/export. v1 checks:

- **Exactly one entry point** per playable flow (one reachable Start).
- **Reachable-node check** — flag orphans/unreachable nodes.
- **Dangling edges / missing targets** (incl. Jump targets that no longer exist).
- **Choice integrity** — ordering, ≥1 outgoing per Choice, no duplicate labels.
- **Variable type checks** — conditions/mutations reference declared variables of the right type.
- **Cycle awareness** — cycles are *allowed* (Jumps make loops legitimate) but **flagged** so a
  play walk has a guard against infinite loops (max-step ceiling in play mode).

Validation results surface as a **non-blocking problem list** (like a linter) — authoring is never
hard-blocked, but Play and P5 export require a clean entry + no dangling targets.

---

## 8. Muse — the three modes (R4, R14)

Muse is **one** context-aware assistant in three escalating modes (`kb-storyboard01.md` §7.0):

| Mode | Analogy | Does | Autonomy |
| --- | --- | --- | --- |
| **Inline assist** | Copilot ghost-text | suggestions *inside fields* — finish a world paragraph, propose a prompt clause, draft a dialogue line, name an asset | zero (accept/reject) |
| **Chat dock** | Copilot Chat | side panel that sees the **current selection + project context**, brainstorms/rewrites, and **proposes** actions | low (proposes, author runs) |
| **Agent mode** | Copilot agent | given a goal, emits a **plan of jobs + edits**, author approves, then it drives the orchestrator via the queue | gated (plan → approve → execute → audit) |

**Hard rule (R14 + Codex):** Muse **never writes records or launches GPU directly.** Every action
goes through the **same typed, atomic, orchestrator-owned write API and the same job queue** as a
human click — so everything is logged in lineage (`"Muse (agent)"`) and undoable. **Propose-and-
approve only** for v1; raising autonomy is a later, separate decision.

**Where Muse plugs in (now fully online):** WORLD — derive style fragments, extract variables,
critique the spine; ASSETS — write the Stage-B prompt list, **caption images (VLM)**, suggest
trigger tokens; SHOTS — the continuity director (already P3) + describe-a-shot→seed-compositor;
**FLOW — draft node dialogue, propose branch/choice options, validate the graph** (no ad-hoc
play-mode voicing, R143); EPISODE — synopsis/logline/subtitles, pitch one-pager (P5 consumes).

---

## 9. Agent mode — goal → plan → approve → queue (§7.2)

Agent mode is **tool-calling over the orchestrator API** with **GBNF / JSON-schema-constrained**
output, so Muse emits a **structured plan the UI renders for approval before anything runs:**

```
┌ MUSE · agent ──────────────────────────────────────────────[ approve all ▸ ]┐
│ goal: "Draft Act 1 from the spine"                                            │
│ plan (6 steps, 2 GPU jobs · est 7m · ~0.6 GB scratch):                        │
│  1 ✎ add 4 Flow nodes (Dialogue×3, Choice×1) from spine beats   [edit][skip]  │
│  2 ✎ draft lines for each (speaker pins suggested)              [edit][skip]  │
│  3 ✎ declare variable trust:int=0, set on choice edges          [edit][skip]  │
│  4 ⏸ WAIT for you to bind/spawn shots for the visual nodes      (gate)        │
│  5 ⚙ STAGE keyframe+sketch jobs for bound shots (not queued)    [edit][skip]  │
│  6 ✎ run graph validation; report problems                     [edit][skip]  │
└───────────────────────────────────────────────────────────────────────────────┘
```

- **✎ = typed edits** (free), **⚙ = GPU/training jobs**, **⏸/gate = human decision**.
- **Up-front cost estimate** (job count · est time · scratch GB) so compute is bounded + visible.
- **GPU autonomy is conservative (R136):** the v1 agent **stages** GPU/training jobs (R123,
  `jobs/staged.json`) and the **author explicitly queues** — the agent never auto-spends GPU.
- **Agentic Muse vs. an unloaded LLM (R21 tension, resolved §7.4):** the agent **plans fully
  up-front while loaded**; execution is then **queue-driven and needs no live LLM** (each ⚙ is a
  deterministic job). The LLM reloads only if a step needs fresh reasoning on a result, and then
  **waits for a GPU gap** rather than competing with a running job.

---

## 10. Tool surface (what the orchestrator exposes to Muse, §7.3)

A small, **typed** function set that mirrors human capabilities exactly (no privileged backdoor):

`create_asset` · `update_record` · `queue_generation(pipeline, mode, params)` (v1: **stage**, not
auto-queue — Q3) · `queue_training(asset)` (stage) · `add_flow_node` / `connect_nodes` ·
`set_variable` · `read_record` / `list_records` / `read_manifest` · `bind_shot(node, shot@ver)`.

Outputs are **GBNF/JSON-schema-constrained** so a malformed call can't reach the orchestrator;
every call is attributed to `"Muse (agent)"` in lineage so an audit separates human vs assistant
authorship.

---

## 11. Modular model registry + on-demand tenant (§7.4)

**Muse is modular** (R4) — a **role → model** registry, each a swappable `llama-server` GGUF, all
**already on disk** in `src/village_ai/models/` (no new downloads to start):

**v1 reduces to TWO models (R144):** one generative VLM for *every* generative role, plus one
retrieval model. `Qwen3-VL-4B-Instruct` *is* a VLM, so it covers text **and** vision-describe in a
single resident context — no per-role model swaps, fast agentic responses.

| Role | Model (on disk, Q8) | Why |
| --- | --- | --- |
| **Generative — ALL of dispatch/tool-calling + creative/dialogue + vision describe/judge** | **`Qwen3-VL-4B-Instruct` Q8** (~4.8 GB) | one light model for everything text+vision-describe; fast; **one context to maintain**; GBNF grammar enforces reliable tool-call JSON even at 4B |
| **Retrieval — embed/rank (on-model scoring)** | **`Qwen3-VL-Embedding-8B` Q8** (~7.5 GB); **`Reranker-8B` Q8 optional/later** | vectors/scores, not text — a different architecture, so it stays separate; Q8 8B fits 16 GB because vision runs **exclusively** (R141) |

- **Why one generative model (R144):** lightness + speed (esp. agentic workflows), and it **dissolves
  the cross-model-context problem** — there's a *single* evolving context, not per-role states.
  Tradeoff: 4B drafts less richly than a 9B; acceptable (human-approved junior assistant), and the
  **registry stays modular (R4)** so a heavier creative model can be slotted later if drafts feel weak.

- **On-demand only (R21):** no model stays resident during generation. The tenant manager loads a
  model only for active assist/planning; **before a heavy job, persists KV-cache to disk and
  unloads** (full 16 GB → generation); reloads + restores context after. Bones already exist —
  `llama.cpp` KV-cache save/restore behind `src/village_ai/models/kv_cache/`.
- **Cross-model context problem dissolved (R144):** with a *single* generative model there's one
  evolving context, not per-role states — so the old "no shared KV across models" caveat is moot for
  the generative side. The embedding model needs no conversational context (it just embeds), so
  nothing has to be shared across the two. (Memory itself lives in the text digest, not KV — §13.)
- **Limit — small models hallucinate structure** → GBNF-constrained calls, plan-before-execute,
  human gates. Muse is a **fast junior assistant, not an oracle.**

### 11.1 AI execution model — every AI action is a queue job or idle-only (R141/R142)

**The hard rule (author): the AI (SLM/VLM/embedding/reranker) shares the *one* GPU with generation
and training, so it must NEVER run concurrently with a job, and must NEVER auto-trigger during an
active queue run.** Rather than orchestrate "unload before each job" by hand (fragile), loom enforces
this **structurally**:

- **Model-loading AI work is submitted as a first-class *AI job* to the same single-GPU queue
  (R141).** The single worker runs **one** thing at a time — gen **or** train **or** an AI job —
  so mutual exclusion is guaranteed by construction. An AI job loads its model, runs, **unloads**,
  and frees the GPU before the next job. Nothing AI fires while a job is calculating.
- **Interactive Muse (chat dock, inline assist) is available *only when the queue is idle* (R142).**
  When a GPU job is active, chat/inline are **greyed with a clear indicator** ("Muse shares the GPU —
  available when the queue is idle"); they go live the moment the queue drains and the tenant can
  load. (Waiting-in-queue would defeat interactivity, so interactive AI is gated on idle, not queued.)
- **No automated/ad-hoc AI triggering — anywhere (R141).** Nothing scores, captions, or voices
  *implicitly* after a generation. Every AI invocation is an explicit user action that either runs
  as a queued AI job (heavy/batch) or only when idle (interactive). **This is why ad-hoc play-mode
  NPC voicing is removed (R143/§6).**

**Use-case review — every Muse/VLM touchpoint classified (author's request):**

| AI use | Needs the GPU model? | How it runs |
| --- | --- | --- |
| Inline assist (ghost-text) | yes (SLM) | **interactive — idle-only** (R142) |
| Chat dock (Q&A/brainstorm/propose) | yes (SLM) | **interactive — idle-only** (R142) |
| Agent **plan generation** (goal → plan) | yes (SLM) | **queued AI job** (one-shot; runs in a GPU gap) |
| Agent **plan execution** | ✎ edits: no · ⚙ steps: yes | ✎ run instantly; ⚙ are **staged → queued** jobs (R136) |
| VLM caption enrichment | yes (gen-VLM) | **queued AI job** (batch) |
| On-model scoring / curation rank | yes (embedding/reranker) | **queued AI job** (batch) |
| Shot critique ("what's wrong?") | yes (gen-VLM) | **queued AI job** (single-shot) |
| P3 **continuity director** (next-segment prompt) | yes (SLM) | **queued AI step**, interleaved between gen steps — *never concurrent* (this also clarifies P3 §12) |
| Project-context assembly (§13) | **no** (text/JSON only, R138) | plain CPU work — **not a job**, runs anytime |
| Readiness proxies (perceptual hash, face-embed) | tiny/CPU | not a tenant job; the *VLM-enriched* score is the queued AI job above |

So the only AI that runs without queuing is **interactive (idle-gated) chat/inline** and **non-model
text assembly** — neither can collide with a GPU job. Everything model-heavy is a queue entry.

---

## 12. Vision online — two tools, two jobs (R22/R116/R137)

A VLM is **not** a reverse-generation pipeline (author, round-19). Research (Qwen3-VL-Embedding +
Reranker paper [arXiv 2601.04720]; VLM-as-judge: IQAGPT, "Compact VLMs as in-context judges",
VisCE²) splits loom's vision needs into **two jobs that need two different model types** —
**don't conflate them:**

**Job A — *describe / judge* (generative VLM).** `Qwen3-VL-4B-Instruct` (on disk) *sees an image →
writes words or a 1–10 score with an explanation.* Reuses **exactly** the proven path:
`src/pipeline/postproc/handrefiner/` starts Qwen3-VL as an on-demand `llama-server`
(`--detect-vlm-regions`, `--vlm-ctx/-ngl/-ready-timeout`).

**Job B — *rank by similarity / consistency* (embedding + reranker).** A **two-stage retrieval**
system: the **embedding** model maps images/text into one vector space → cosine similarity to the
**canonical refs** = a principled *on-model* score; the **reranker** (a cross-encoder *scorer*, not
a generator) re-scores `(query, candidate)` pairs 0–1 for fine-grained ranking. This is the right
tool for "is this generated frame actually on-model?", not "ask the generative VLM to rate it."

| Vision use | Tool (Job) | What it does | Builds on |
| --- | --- | --- | --- |
| **Caption enrichment** | generative 4B (A) | upgrade P2 **template captions** with what the VLM sees, project-context-aware | P2 §6 (R116) |
| **On-model scoring / curation ranking** | **embedding (+optional reranker) (B)** | cosine-to-canonical *on-model* score; rank ~100 candidates for **P1 Stage C** keep/cull; enrich **P2 proxy readiness** (holistic signal beyond the face-embedding) | P1 Stage-C, P2 §7 proxies |
| **Shot critique** | generative 4B (A) | "what's wrong with this shot?" — qualitative curation/compositing feedback | — |

- **VRAM (corrected — models are on disk, quantized):** `Qwen3-VL-4B-Instruct` Q8 **~4.8 GB**,
  `Qwen3-VL-Embedding-8B` Q8 **~7.5 GB**, `Qwen3-VL-Reranker-8B` Q8 **~8.2 GB** (all in
  `src/village_ai/models/`). Because **vision runs *exclusively*** (R141 — it's a queue job, nothing
  else on the GPU), Q8 8B fits 16 GB with context headroom. The **f16/bf16** copies (~16 GB) are too
  big to share; **Q8 is the pick**, and **more compact Qwen variants** can be pulled from HF if more
  headroom/faster load is wanted. The embedding/reranker still get a **ROCm can-run check** (P4-13b).
- **The embedding model *is* the future GraphRAG/vector retrieval model (§13/R138/R170)** — so
  on-model scoring and the deferred retrieval index use the **same model family**.
- **Queue-serialized, never concurrent (R141):** vision is a **queued AI job** — generative-VLM
  **or** embedding **or** trainer **or** a gen job, never two at once; the single worker enforces it.
- **v1 priorities (R137):** **caption enrichment + on-model scoring first** (they retro-improve
  the P1/P2 loops); critique/Q&A follow.

---

## 13. Project-wide context management (R116/R145/R170) — how the model stays current

What makes Muse/VLM help *project-true* not generic. The question (author): *with one resident
model, how does its project context stay up to date as the project grows and changes?* The v1 answer
is a **two-tier model — text is the memory, KV is only a cache** — with a deliberate path toward
GraphRAG once the compact digest is no longer enough:

### 13.1 Tier 1 — `project_context.json` is the system of record (R145)

- **Contents:** world summary, style fragment, **cast manifest** (each AssetProfile → name, class,
  version looks, trigger token, anchor thumbnail), game-rule variables, **flow synopsis** (graph
  beats). A **compact structured digest** — summaries + one-line cast entries, **not** raw everything.
- **It is the durable memory:** text, deterministic, model-agnostic, and — crucially — it **survives
  the unload-before-every-job rule (R141)**, which the KV-cache does not.
- **Freshness = dirty-flag triggers (cover *all* the inputs, or the context silently rots):**
  - **world / style** edit → dirty (world summary, style fragment);
  - asset **create / edit / finalize a version** (new look / trigger token) → dirty (cast manifest);
  - **LoRA** trained/promoted/changed on a version → dirty (cast manifest — the version's model);
  - **flow** node add/edit/delete, **variable** declare/edit → dirty (synopsis + rules);
  - **shot binding / re-bind / version re-select** (R146) → dirty (which look a beat shows).
  Rebuilt on edit, **not per keystroke**; on the next AI request loom feeds the fresh digest.
  Assembly is **plain CPU text work — not a GPU job** (§11.1). *(Missing a trigger = stale cast/looks
  fed to the model — so the trigger set is part of the P4-14 acceptance.)*
- **Graph-ready sidecar:** the same assembler writes `project_facts.jsonl`, a rebuildable CPU-only
  fact stream with stable IDs and typed relations. It is **not** a retrieval index; it is the clean
  source a later GraphRAG builder will ingest instead of scraping Markdown or filenames. Include
  facts for world/style versions, asset versions + LoRAs, training context digests (from P2), shot
  generation lineage, Flow node pins/bindings, variables, and render-list references.

### 13.2 Tier 2 — KV-cache is a *speed* layer, never the memory (R145)

- The digest is fed as a **cached prompt prefix**; `llama.cpp` **prefix-reuse + disk slot
  save/restore** (`kv_cache/`) avoid recomputing it each request and across unloads.
- **But KV is not memory:** it's **evicted on every job-unload (R141)**, the window is **finite**,
  and it goes **stale** when records change. So when the digest's dirty flag flips, the saved KV
  prefix is **invalidated and recomputed once**. KV optimizes; the JSON remembers.
- **A single generative model (R144) is what makes this clean** — one prefix to cache and refresh,
  not one per role.

### 13.3 Scaling past the context window — GraphRAG later (R170)

- v1 keeps the **digest compact** so it fits comfortably. When a task needs deep detail ("rewrite
  *this* scene"), loom appends only the **relevant** records on top of the digest.
- That selective pull is what a **persistent GraphRAG index** powers later instead of stuffing
  everything into the prompt. The later layer should combine:
  - the **typed fact graph** from `project_facts.jsonl` for relational/global questions;
  - the **embedding model** already online in P4 for scoring (R137) for semantic lookup over text,
    images/stills, captions, and summaries;
  - graph/community summaries for broad questions like "where does this character's red-hair version
    appear?" or "which LoRAs/refs/shots are stale relative to the current canon?"

  Only the *stored graph/vector index + retrieval/query layer* is deferred (R138/R170), and it
  reuses the same embedding model family. **v1 = compact text digest + typed fact sidecar; later =
  digest + GraphRAG detail.**

---

## 14. Build dependencies (`kb-storyboard01.md` §8.1)

- **React Flow** (graph canvas) + the inspector/outline UI — the bulk of P4.1.
- **Tenant manager** (model registry, on-demand load/unload, KV-cache save/restore) — reuses the
  `llama-server` sidecar pattern from `kb-slm.md` + `kv_cache/`.
- **Tool-surface bridge** — the typed, GBNF-constrained function set over the **existing P0 write
  API + queue** (no new privileged path).
- **Generative-VLM adapter** — the handrefiner on-demand `llama-server` path, generalized to
  captioning/critique (Job A, §12). Same model as all generative roles (R144).
- **Embedding/reranker adapter** — `Qwen3-VL-Embedding-8B` Q8 (+ optional `Reranker-8B` Q8), **both
  on disk**, for on-model scoring/ranking (Job B); runs as an **exclusive queued AI job** (R141) with
  a **ROCm can-run check**, R137.
- **AI-job queue type + idle-gating** — wire AI work as single-GPU queue entries (R141) and gate
  interactive chat/inline on queue-idle (R142).
- **Project-context assembler** — reads L1/L2/L4 records → `project_context.json` (the durable digest;
  cache + dirty flag, R145) + `project_facts.jsonl` (GraphRAG-ready facts, R170); plus KV
  prefix-cache invalidation.
- **Graph validator** — the linter in §7.

---

## 15. Disk & VRAM

- **Disk:** P4 is light vs P2/P3 — Flow records + context cache are small JSON; no new heavy media.
  The two-threshold guard (R96) + per-project cap still apply.
- **VRAM (16 GB, tight):** the recurring rule — **one heavy thing at a time.** SLM/VLM are
  **on-demand tenants that unload before any gen/training job** (R21); agent execution is
  queue-driven and needs no live LLM (§9). Expose model choice + offload/precision per role.

---

## 16. P4 milestones — walking skeleton first

### P4.1 — Flow editor (build the storyboard spine first)

1. **M1 — graph skeleton (no logic).** React Flow canvas; add/connect Start + Dialogue + Shot +
   Jump nodes; node files (`flow/nodes/*`) + rebuildable `graph.json` index; persist/reopen.
   *(First visible payoff: a graph that saves.)*
2. **M2 — node↔shot binding + pins.** Bind a visual node to an existing **P3 shot's clip**; pin
   `asset@version`; inline clip preview; "spawn shot from node" handoff. *(Now the graph shows the
   actual story visuals.)*
3. **M3 — play mode (walking skeleton done-line).** `▶ Play from here` walks Start→…→Jump,
   showing bound clips + **authored** dialogue in a reader pane against a sandbox state (max-step
   loop guard). **No ad-hoc AI voicing** (R143). *(P4.1 done-line: a playable branching storyboard
   over the P0–P3 spine.)*
4. **M4 — variables/conditions + validation.** Typed variables (L1 rules); Choice/Condition/Action;
   the §7 validator as a non-blocking problem list.

### P4.2 — Muse (full assistant)

5. **M5 — single-model tenant + AI-as-queue-job (generalizes P3's minimal tenant, R168).** One
   generative model (`Qwen3-VL-4B-Instruct` Q8, R144) behind a modular registry; load/unload with KV
   prefix-cache save/restore; **AI runs as a queue job** with idle-gating for interactive chat/inline
   (R141/R142) — mutual exclusion proven against the queue. Builds on the minimal queued-AI-job +
   single-tenant exclusion P3 introduced for the continuity director (R168).
6. **M6 — chat dock + inline assist.** Chat panel sees selection + project context, proposes;
   inline ghost-text in key fields (dialogue, prompt clauses, world prose, asset names).
7. **M7 — agent mode + revert.** GBNF-constrained plan → approval UI (cost estimate) → typed edits
   run + **GPU/training jobs *staged only*** (R123/R136); everything attributed in lineage; a
   **Muse-action history with one-click revert** (per-action / per-plan, P4-12b) closes the safety loop.

### P4.3 — Vision + project context

8. **M8 — vision tenants online (ROCm-gated).** Generative **Qwen3-VL-4B-Instruct** Q8 via the
   handrefiner `llama-server` pattern; **Qwen3-VL-Embedding-8B** Q8 (Job B, on disk) + a **can-it-run
   check**; both run as **exclusive queued AI jobs** (R141).
9. **M9 — project context (two-tier, R145/R170).** `project_context.json` digest (world/style/cast/
   rules/synopsis) + `project_facts.jsonl` typed fact sidecar + dirty-flag rebuild (the durable
   memory); KV prefix-cache + invalidation as the speed layer over it.
10. **M10 — caption enrichment + on-model scoring.** Caption enrichment via the **generative VLM**
    (retro-improves P2 datasets); **on-model scoring via the embedding model** (cosine-to-canonical;
    enriches P1 Stage C / P2 readiness) — both **project-context-aware** (R137).

### Done-line

11. **M11 — acceptance.** Author builds a branching, playable Flow over P0–P3; Muse chat proposes +
    an approved agent plan stages work; the VLM enriches a caption/score with project context;
    reopen → all intact (§1).

---

## 17. Risks & guardrails

1. **Agent mode is the make-or-break (small models hallucinate structure).** **Guardrails:**
   GBNF/JSON-schema-constrained calls; **plan→approve→execute** only (R14); **GPU staged, not
   auto-queued** (R136); everything through the human write-API + queue, logged + undoable.
2. **Flow editor is a big UI.** **Guardrail:** walking-skeleton-first (M1–M3 reach a playable
   storyboard before logic/validation thicken it).
3. **VLM/SLM VRAM contention.** **Guardrail:** strict on-demand tenant, **unload before any heavy
   job** (R21); one heavy thing at a time.
4. **`graph.json` git-merge fragility (Codex).** **Guardrail:** **one-file-per-node** + a thin,
   **rebuildable** index (R139) — matches the P0 atomic-record model.
5. **AI auto-firing / contaminating canon (Codex + author).** **Guardrail:** **all model-loading AI
   is a queue job; interactive AI is idle-only; nothing auto-triggers** (R141/R142). Play mode has
   **no ad-hoc voicing at all** (R143) — a node shows authored dialogue or nothing.
6. **Don't regress P0–P3.** **Guardrail:** P4 is additive; a project with no Flow graph, no agent
   use, and no VLM is still fully valid.

---

## 18. Out of scope (defer)

- **Episode / Render / main-path pick / transitions / export** → **P5** (Track A).
- **Production hardening — deeper Flux2 multi-ref, LTXV-extend hardening (R133), multi-LoRA stacking
  + style LoRA, postproc expansion** → **P5** (Track B, R147).
- **All 3D / depth proxies** → **P6** (R128).
- **Effect plugin API (Python/JS/TS) + engine/DCC export** (Unreal 5.7+/Blender) → **P6**.
- **Persistent GraphRAG/retrieval index** for the project context → later (v1 = text digest +
  rebuildable typed facts, §13).
- **Hierarchical act/scene grouping** of Flow nodes → post-v1 (R74).
- **Raising Muse autonomy** above propose-and-approve → a later, separate decision (R14).
- **Video LoRAs** (motion) → P6.

---

## 19. Resolved (round 19 → R134–R146, plus R170)

| # | Decision |
| --- | --- |
| R134 | **Flow node ↔ shot binding = both** — a visual node can **bind an existing** P3 shot **or spawn a new one** inline (handoff to Shots); node holds narrative, shot holds the visual, pin `asset@version` (§4/§5). |
| R135 | **Variables/conditions minimal for v1** — bool/enum flags + simple conditions (enough to branch the storyboard, "episode is the north star, not a game"); **expandable later** (the fuller articy-style variable set is post-v1) (§6). |
| R136 | **Agent GPU autonomy = stage-only** — the agent freely makes typed edits but **stages** GPU/training jobs (`jobs/staged.json`, R123); the author explicitly queues; **agent never auto-spends GPU** (§9/§10). |
| R137 | **Vision = two tools, two jobs.** *Describe/judge* → generative **`Qwen3-VL-4B-Instruct` Q8** (~4.8 GB, on disk, handrefiner pattern): caption enrichment + critique. *Rank by similarity/consistency* → **`Qwen3-VL-Embedding-8B` Q8** (~7.5 GB) + optional **`Qwen3-VL-Reranker-8B` Q8** (~8.2 GB), **both on disk**: on-model scoring + curation ranking. **Q8 8B fits 16 GB *because vision runs exclusively* (R141)**; f16/bf16 too big → Q8; more compact Qwen variants available if needed; ROCm can-run check (P4-13b). v1 = caption enrichment + on-model scoring (§12). |
| R138 | **Project context = text-structured for v1.** The embedding **model ships in P4 for scoring** (R137); only the **persistent GraphRAG/retrieval index** is deferred — it **reuses that same model** (no new model later, just the stored graph/vector index build), so the scorer is P4 and the index is later (§13). |
| R139 | **Graph storage = one-file-per-node + rebuildable thin `graph.json` index** (atomic, corruption-resilient, Codex-aligned) (§4). |
| R140 | ~~Live NPC voicing in play mode = opt-in + ephemeral~~ — **SUPERSEDED by R143** (no ad-hoc AI in play mode). |
| **R141** | **AI runs as queue jobs — single-GPU mutual exclusion.** All model-loading AI work (VLM caption/score, embedding/reranker, Muse agent plan + execution, P3 continuity director) is submitted as a **first-class AI job** to the same single-GPU queue; the one worker guarantees **AI never runs concurrently with gen/training**, and **nothing AI auto-triggers during an active queue run** (§11.1; sharpens R21). |
| **R142** | **Interactive Muse (chat/inline) = idle-only.** Available **only when the queue has no active GPU job**; greyed with a clear indicator otherwise; goes live when the queue drains. Heavy/batch AI always queues (§11.1). |
| **R143** | **No ad-hoc/live NPC voicing in play mode** (supersedes R140). Play mode never auto-invokes the SLM; a node shows authored dialogue or nothing. Desired NPC audio = **P3 voice cue (R132)** or a deliberate **queued** generation (§6). |
| **R144** | **One generative model for v1.** `Qwen3-VL-4B-Instruct` Q8 serves **all** generative roles (dispatch/tool-calling + creative/dialogue + vision describe/judge) — light, fast, one resident context; **dissolves the cross-model-context problem**. Only the **retrieval** model (`Qwen3-VL-Embedding-8B` Q8; Reranker optional/later) is separate → **two models v1**. Registry stays modular (R4) for a heavier creative model later (§11). |
| **R145** | **Context management = text is memory, KV is cache.** `project_context.json` (compact digest, dirty-flag rebuilt from canonical records) is the **durable system of record** — it survives the unload-before-every-job rule. The **KV-cache is only a prefix-reuse speed layer** (invalidated when the digest changes), never the memory. Scaling: compact digest v1 + typed fact sidecar → GraphRAG for task-relevant detail later (= the deferred index, R138/R170) (§13). |
| **R146** | **Shot ownership is 1:1; binding = sole ownership** (reconciles R23+R134). Every L3 shot is owned by **exactly one** Flow node; **binding** attaches an *unbound* shot (or re-selects a version of this node's own shot) and **transfers sole ownership** — **never a shared/reusable reference**. Reuse elsewhere = a **duplicated** node-owned shot. Keeps undo/lineage/P5-picker unambiguous (§4/§5). |
| **R170** | **GraphRAG posture.** P4 writes `project_facts.jsonl` as a rebuildable typed fact sidecar, but does **not** build the persistent graph/vector retrieval index. GraphRAG/query lives post-v1/P6; P4 only makes the future index cheap and reliable to build. |

**Still open:** none for P4. R134–R146 plus R170 settle round-19 + AI-execution + model/context,
ownership, and the GraphRAG boundary;
the §20 WBS reflects them.

---

## 20. Work-package breakdown (WBS) — what P4 actually contains

*Rough solo-dev sizing: **S** ≤1 d · **M** 2–4 d · **L** 1–2 wk · **XL** 3 wk+. Risk: 🟢 routine ·
🟡 unknowns · 🔴 R&D / make-or-break. Maps to the §16 milestones.*

| WP | Work package | Maps to | Size | Risk |
| --- | --- | --- | --- | --- |
| P4-1 | **React Flow canvas** + node palette + add/connect; node files + rebuildable `graph.json` index | M1 | L | 🟡 |
| P4-2 | Node inspector + flow outline + variable list panels | M1/M4 | M | 🟢 |
| P4-3 | **Node↔shot binding** (bind existing P3 shot + node-version) + `asset@version` pins + inline clip preview | M2 | M | 🟡 |
| P4-4 | "**Spawn shot from node**" handoff into the Shots workspace | M2 | S | 🟢 |
| P4-5 | **Play mode** — graph walk, sandbox variable state, reader pane (walking-skeleton done-line) | M3 | M | 🟡 |
| P4-6 | Typed **variables/conditions** (Choice/Condition/Action/on-enter mutations) | M4 | M | 🟡 |
| P4-7 | **Graph validator** (entry/reachability/dangling/choice/type/cycle) as a non-blocking linter | M4 | M | 🟡 |
| P4-8 | **Single-model tenant + modular registry** (one `Qwen3-VL-4B-Instruct` Q8 for all generative roles, R144; load/unload, KV prefix-cache save/restore) | M5 | M | 🔴 tenant lifecycle |
| P4-8b | **AI-as-queue-job wiring (R141/R142)** — AI job type in the single-GPU queue (load→run→unload); **idle-gating** for interactive chat/inline; no auto-trigger paths | M5 | M | 🔴 the mutual-exclusion guarantee |
| P4-9 | **Chat dock** (sees selection + project context, proposes) | M6 | M | 🟡 |
| P4-10 | **Inline assist** (ghost-text in dialogue/prompt/prose/name fields) | M6 | M | 🟡 |
| P4-11 | **Agent mode** — GBNF plan → approval UI (cost estimate) → typed edits + **staged** GPU jobs | M7 | L | 🔴 make-or-break |
| P4-12 | **Tool surface** (typed GBNF function set over the P0 write API + queue; lineage attribution) | M7 | M | 🟡 |
| P4-12b | **Muse-action history + one-click revert** — a reviewable journal of Muse-authored edits/jobs (from lineage R98); revert **per-action** and **per-approved-plan batch** (reverse the typed writes; a reverted ⚙ job's output is removed + the prior record restored) | M7 | M | 🟡 agent safety rail |
| P4-13 | **Generative-VLM tenant** online (Qwen3-VL-4B via handrefiner `llama-server` pattern; pausable) | M8 | M | 🟡 |
| P4-13b | **Embedding/reranker online (Job B)** — `Qwen3-VL-Embedding-8B` Q8 (+ optional Reranker-8B Q8), **on disk**; **ROCm can-run gate**; exclusive-tenant only (R137/R141) | M8 | M | 🔴 new model type + ROCm |
| P4-14 | **Project-context (two-tier, R145/R170)** — `project_context.json` digest + `project_facts.jsonl` fact sidecar + dirty-flag rebuild (the memory) **+ KV prefix-cache invalidation** (the speed layer) | M9 | M | 🟡 |
| P4-15 | **Caption enrichment** via generative VLM (project-context-aware; retro-improves P2 datasets) | M10 | M | 🟡 |
| P4-16 | **On-model scoring** via **embedding** (cosine-to-canonical; rank candidates; enriches P1 Stage C + P2 readiness) | M10 | M | 🟡 |
| P4-17 | Acceptance (§1 / M11) | M11 | S | 🟢 |

**Rollup:** ~20 WP — comparable weight to P3 but **UI- and assistant-heavy rather than GPU-heavy.**
Risk concentrated in **P4-8/P4-8b (tenant lifecycle + the AI-as-queue-job mutual-exclusion
guarantee)**, **P4-11 (agent mode)**, and **P4-13b (embedding/reranker + ROCm gate)**. The Flow
editor (P4-1/P4-5) is large but low-novelty; the generative-VLM strand reuses a *proven* on-disk
path (handrefiner). **P4-8b is the keystone**: getting AI onto the single-GPU queue is what makes the
"no AI during a job, no auto-trigger" rule structural rather than hopeful.

**⚠ Gaps folded in from the round-19 settle:**
- **Undo/redo for Muse actions** — **now a firm WP (P4-12b)**: a reviewable Muse-action history with one-click revert, per-action and per-approved-plan batch.
- **Project-context staleness** — the full invalidation-trigger set (world/style, asset version, **LoRA change**, flow/variable, **shot binding**) is now enumerated in §13.1 and is **part of P4-14 acceptance** — miss one and the model reads stale cast/looks.
- **Play-mode loop guard** — cycles are legal (Jumps); the **max-step ceiling** is now wired into M3/P4-5.
- **Agent plan cost-estimate accuracy** — the up-front "est time · scratch GB" must reuse the **same VRAM/time tables as the queue** (P0-7/P2-12/P3-11) or it'll mislead (part of P4-11).
- **Embedding/reranker as a separate tenant** — they're a *different* model from the generative VLM, so a vision-scoring job and a chat session can't both hold the GPU; the queue serializes them (R141) — confirm load/unload latency between an embedding job and a generative call is acceptable (part of P4-13b).

---

## Source / traceability

- Decisions: `kb-storyboard01.md` §10.0 — **R4** (SLM everywhere, modular), **R14** (propose-and-
  approve only), **R21** (on-demand tenant, unload before heavy jobs), **R22** (VLM on disk),
  **R23** (nodes independent, own clips, pin `asset@version`), **R62** (node versioning), **R74**
  (single flat graph + Jump, no grouping), **R98** (lineage), **R116** (VLM + project context → P4),
  **R123** (staged jobs), and **R134–R145** (round-19 + follow-ups — binding, minimal variables,
  agent stage-only, two-tool vision, text-context, per-node storage; **R141–R143** AI-as-queue-job /
  idle-only / no-ad-hoc-voicing; **R144** one generative model; **R145** text-is-memory/KV-is-cache);
  **R170** GraphRAG posture (typed facts now, persistent index later).
- Research (round-19, Q4): Qwen3-VL-Embedding/Reranker [arXiv 2601.04720] (two-stage retrieval);
  VLM-as-judge — IQAGPT [arXiv 2312.15663], "Compact VLMs as in-context judges" [arXiv 2507.20156],
  VisCE² [arXiv 2402.17969]. **Models on disk as Q8 (4B ~4.8 GB / Embedding-8B ~7.5 GB / Reranker-8B
  ~8.2 GB); Q8 8B fits 16 GB because vision runs *exclusively* (R141).**
- Bodies: `kb-storyboard01.md` **§6.5** (Flow workspace), **§7** (Muse — modes, agent, tools,
  registry, tenant), **§8** (architecture — React Flow/Konva, sidecar).
- Spine reused: `kb-loom-p0.md` (queue, write API, adapter contract, disk guard, components),
  `kb-loom-p1.md` (assets/versioning, context seeds), `kb-loom-p2.md` (LoRAs, readiness proxies,
  VLM handed to P4 §9), `kb-loom-p3.md` (shots + node versioning — the clips Flow binds).
- Engine: `kb-slm.md` (model roles, GBNF/function-calling, KV-cache), `kb-pipelines01.md`;
  proven VLM path `src/pipeline/postproc/handrefiner/`; on-disk models `src/village_ai/models/`.
