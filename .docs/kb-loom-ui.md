# Loreweave Studio — UI plan (shell + workspace arrangement)

*Status: **draft for the author, 2026-09-20** — written before any shell change, per the author's
call: "before we start chopping and changing things, I'd like to create a documented UI plan,
where we evaluate each current (and future) function's arrangement on the UI with pros/cons".
Baseline = the UI at HEAD `ce70c60` (inventory in §2). Nothing here is built. The decisions the
author still owns are collected in §7 as **D1–D10**; everything else is a recommendation.*

> **⭐ Adopted 2026-09-20 (author).** D1–D10 accepted exactly as recommended (§7). One amendment
> from the author, also adopted: **the new UI is built from scratch beside the old one, on the
> same orchestrator API**, instead of refactoring App.tsx in place. Both frontends moved into
> their own tree the same day (code: the "frontends" commit; journal: "📐 UI plan adopted"):
>
> ```
> frontends/
>   shell/        the ONE Tauri 2 shell (src-tauri) — wraps v1 by default, v2 via
>                 `npm run dev:v2` / `build:v2` (tauri.v2.conf.json overlay)
>   shared/api/   the typed orchestrator client + logger (83 functions, 52 types),
>                 imported by both frontends as `@loom/shared/api/*`
>   v1/           today's frontend — FROZEN as the reference and fallback (Vite :1420)
>   v2/           the new frontend — the FRAME is in (step 1, 2026-09-20); steps 2–7 follow (Vite :1421)
> ```
>
> Consequences for this plan: migration **step 0 (the App.tsx split) is replaced** by the move
> + seed above — v1 is never refactored; **steps 1–7 are v2's build order** (frame first); the
> §2 inventory is v2's parity checklist; the frontend contract tests re-point to v2 feature by
> feature and v1's stay until v1 is deleted. Scheduled as **M2.14 — v2 frontend**, before P3.

**Verdict in one paragraph.** The good part is the *content* model — image tiles, the grouped
operation/derivation tree, the postprocess stack, the JSON prompt tree, the readable L1 editors.
The bad part is the *frame*: the shell froze at the M0a "File menu + labelled rail" reset and
never became the Navigator · Stage · Inspector triad the storyboard spec drew (`kb-storyboard01.md`
§6.1). Two fixed 240 px columns bracket a stage column that has absorbed every control bar, so
the canvas (the grid) gets what is left and the properties pane gets 240 px for four stacked
panels. The fix is not a restyle: it is to give every function a *home by kind* — global state in
a thin top bar, navigation in a collapsible rail-plus-panel, **authoring in a composer panel**,
review on a full-width canvas with one contextual strip, selection properties in a tabbed
resizable inspector, long-running work in a bottom dock that can grow into a timeline. That is
the arrangement Photoshop, Lightroom, Canva and Figma converge on, and it is the one the specs
for L3–L5 already assume.

---

## 1. Method — how each function is judged

Every function is scored on five questions; the table in §3 records the answers.

| Question | What it decides |
| --- | --- |
| **Scope** — global · project · workspace · stage · selection | which zone *kind* may hold it (a selection-scoped control has no business in a global bar) |
| **Frequency** — per-session · per-image · per-batch · rare | how visible it must be (per-image = one click, always visible; rare = menu) |
| **Space** — a chip · a row · a column · a canvas | whether it can live in a bar at all (a JSON tree is a column, never a bar) |
| **Persistence** — sticky (stays open across actions) · transient (fires and folds) | panel vs popover vs modal |
| **Cost of being wrong** — a mis-click destroys work / burns GPU minutes / nothing | confirm dialogs, hover-only vs visible affordances, distance from the primary action |

**What the professional tools agree on** (the inspiration the author asked for):

| Tool | Frame | The lesson for loom |
| --- | --- | --- |
| **Photoshop** | icon-only tool rail left · contextual *options bar* under the menu · canvas centre · dockable, tabbed, collapsible panels right · status strip bottom | one thin contextual bar per mode, never four; properties are *tabs*, not a stack; the canvas is the biggest thing on screen |
| **Lightroom** | module strip top (Library/Develop…) · left navigator + presets · right develop panels, each collapsible · filmstrip bottom · thumbnail-size slider · Loupe/Compare/Survey views | workspaces are top tabs; the grid has a zoom slider and a loupe with prev/next + compare; panels collapse individually |
| **Canva** | 48 px icon rail left that opens a *side drawer* (elements, uploads, text…) which pushes the canvas · contextual toolbar above the canvas · properties only when relevant | the rail costs nothing when closed; authoring lives in a drawer, not in bars over the canvas |
| **Figma** | layers/assets left · always-on properties right · one toolbar | selection drives the right panel; the right panel is wide enough to *edit* in |
| **DaVinci Resolve** | page tabs bottom · timeline always visible in Edit/Fusion pages | the bottom zone grows into a timeline when the workspace needs one (L3/L5) |
| **Blender** | workspace tabs top · N-panel toggled with one key · layouts persist per workspace | keyboard toggles for panels; per-workspace layout memory |

The principles we take: **(P1)** a stable frame whose zones keep their *kind* across workspaces;
**(P2)** exactly one contextual strip, above the canvas; **(P3)** authoring in a panel/drawer with
the primary action pinned at its foot; **(P4)** tabbed + collapsible + resizable side panels with
persisted layout; **(P5)** the canvas gets the remainder and scales its content (zoom, responsive
columns); **(P6)** destructive and GPU-burning actions are visible, confirmed in real dialogs, and
never hover-only; **(P7)** global signals (queue, disk, service) always visible but thin.

---

## 2. The shell today — what the inventory shows

Zone by zone (App.tsx ≈ 4 070 lines, one component + nine inner ones; `styles.css` 792 lines; no
drag handles, no collapsible zones, no persisted layout — every zone is a fixed CSS size):

| Zone | Today | Pros | Cons |
| --- | --- | --- | --- |
| **Title bar** | app name · `File ▾` · project name · the **A·B·C·D stage switch** (L2 only) · spacer · orchestrator dot | one line; File menu is conventional | the stage switch was *evicted* here because the stage bar overflowed (code comment) — a stage-scoped control in a global bar; the bar otherwise carries nothing (no queue, disk, or workspace tabs) |
| **File menu** | New · Open folder · Close · RECENT (name + 10 px mono full path + ✕) | recents exist | **New and Open are `window.prompt()` text boxes** — three prompts for New (path, name, cap); no native folder dialog; the recent list is the only "browser" |
| **Left rail — 240 px fixed** | two labelled buttons `L2 · Assets` / `L1 · World`, then the asset list (name + dot; 🗑 hover-only) or the four L1 sub-tabs | asset list is the right thing to have on the left | 240 px for a list of ~5 names = the unused space the author sees; workspace switching and navigation share one column; nothing collapses; asset class and version count hide in a tooltip; only `+ Character` exists (props/scenes are backend-ready, UI-absent) |
| **Stage column** (centre) | L2: stage-context bar (name · version select · + version · finalize · export) → generate/recipe bars → params drawers → JSON tree → hero strip → cell picker → sketch bar → banners → **style bar (every stage)** → error line → TrainPanel (D) → view toggle → grid. The **whole column scrolls** | everything for a stage is on one screen | Stage B stacks **14 controls in one wrapping row** plus a second bar, a drawer, an inline picker and a strip *above* the grid; the grid is a **fixed 3-column** `repeat(3,1fr)` regardless of width; bars are not sticky, so scrolling the grid scrolls the controls away; the style bar renders in C and D where nothing consumes it; the single `.error` line has no dismiss and no history; L1 (`WorldWorkspace`) renders in this column *without* the error/banner surface, so its GPU actions (style sample, pose icons) can fail silently and enqueue during a pause |
| **Right inspector — 240 px fixed** | INSPECTOR head → anchor buttons → `Inspector` (image, facts `<dl>`, prompt/params foldouts, log tail) → `RerunPanel` → `PostprocPanel` (step list + a full add-form with preset/backend/style/branch/model/strength/steps readout/size row/JSON tree) | selection-driven, the right idea | four panels stacked in 240 px; the flux.2-dev JSON tree's rows have `min-width:120px`, so the camera row's four controls stack vertically; the author already had to add `flex-wrap` because "the last two fell off the right edge"; a CSS class collision (`.pp-steps`) leaks `nowrap` into the step list; 10–11 px text throughout |
| **Bottom dock** | one line: queue status · `jobs ▾` popover · done/failed/canceled · disk meter · `unpause ▶` | always visible, thin (P7) — the spec's intent | the jobs popover is the only job view; training progress is a muted string in a `TrainPanel` row; the disk reason is a tooltip |
| **Modals / dialogs** | two real modals (new version, pre-flight) + a lightbox; **5 `window.prompt` + 10 `window.confirm`** | pre-flight modal is a genuinely good pattern | prompts/confirms are un-styled, un-scoped OS dialogs; the lightbox has no prev/next and no compare although the A/B preview exists |
| **Affordances** | tile ★ / keep / reject / bulk / 🗑 / 🔍 are `opacity:0` until hover; asset 🗑 and pose ↻/🗑 likewise; **128 `title=` tooltips** carry essential semantics (identity checkbox = a four-state tooltip; readiness details; preset explanations) | uncluttered tiles | invisible until the pointer is there (P6 violated); keyboard users never see them; the identity/readiness explanations have no visible home |
| **Type** | 8 px "no sample", 9 px pose/style names, 10 px paths/status/attrs/argv/log, 11 px in every bar | dense | the density is a symptom of the fixed widths, not a choice |

**Spec drift worth naming.** `kb-storyboard01.md` §6.1 drew: a *workspace switcher* (WORLD ·
ASSETS · SHOTS · FLOW · EXPORT), a **Navigator** whose content changes per workspace, a **Stage**,
an **Inspector**, and an always-visible **Job Queue** strip. M0a (`kb-loom-p2.md` §12 M0) then
asked for "true workspace tabs" and a 20 % wider Asset panel — and what shipped merged switcher
and navigator into one 240 px rail, left the inspector un-tabbed, and let every control bar
land in the stage. The plan below is, in effect, finishing §6.1 with what we learned since.

---

## 3. Function-by-function evaluation

Legend for **Scope**: G global · P project · W workspace · S stage · Sel selection. **Freq**:
session · batch · image · rare. Every row states the *why* (workflow) and the *downside* of the
proposed home, as asked. Zones are the proposed ones from §4 (Top bar · Rail · Panel · Strip ·
Canvas · Inspector · Dock).

### 3.1 Global and project

| Function | Today | Scope / freq | Proposed home | Why it belongs there | Downside / trade-off |
| --- | --- | --- | --- | --- | --- |
| Open / New project | File menu → `window.prompt` paths | P / session | **File menu → native folder dialog** (`tauri-plugin-dialog`) + a **Start screen** when no project is open (recents with a thumbnail, last-opened, size, "open folder…", "new…"); recents also under File | a project is chosen once per session; a browser is table stakes; a start screen removes the empty-shell state and makes recents *visual* | the dialog plugin adds a Tauri capability (`dialog:allow-open`) and a Rust dependency; in `npm run dev` (browser, no shell) it must fall back to the typed path |
| New project wizard (name · folder · size cap · format) | three prompts | P / rare | a real **modal** with the footprint estimate inline | one form beats three sequential prompts; the estimate belongs next to the cap field | none |
| Close / forget recent | File menu | P / rare | File menu (unchanged) | conventional | — |
| Workspace switch (L1 World · L2 Assets · L3 Shots · L4 Flow · L5 Episode) | two labelled rail buttons | G / session | **top-left tabs in the top bar** (Lightroom modules, Blender workspaces) with L3–L5 present but disabled until their phase | the frame must show the *whole* tool, not the two rooms that exist; tabs at top free the rail for navigation; the spec drew exactly this switcher | tabs cost ~50 px of top-bar width per workspace; five is the limit before it needs a menu |
| Orchestrator status · version | title bar right | G / always | top bar right, as a **status cluster**: service dot · GPU/CPU backend chip (needed once the ggml/CPU spike lands) · queue chip · disk chip | global signals belong in one place (P7); the queue chip mirrors the dock so the dock can collapse | duplicates the dock's headline — accepted, the dock carries the *detail* |
| Errors / notices | one red line inside the L2 stage column | G / event | **toast stack** (bottom-right, dismissible, last 20 kept in a log drawer) + a persistent **banner slot** under the top bar for sticky states (paused · disk hard-stop · weights missing · offline) | errors must reach every workspace — today L1 has none; sticky states are not toasts | a toast system is new code (≈150 lines) and needs a queue-of-notices contract |
| Settings (venv paths, HF token, VRAM budget, backend) | none in UI (`.env.local`) | G / rare | File → Settings modal (later); read-only "about/diagnostics" now | today the author edits `.env.local` — fine for one author, but the CPU/backend switch will want a UI toggle | out of scope of this plan; noted so the top bar reserves the gear |

### 3.2 Navigation (the left side)

| Function | Today | Scope / freq | Proposed home | Why | Downside |
| --- | --- | --- | --- | --- | --- |
| Asset library (characters · props · scenes) | 240 px rail list, `+ Character` only | W(L2) / session | **Rail icon "Library" → Panel** (280–360 px, resizable, collapsible to the 48 px rail): sections per class with counts, search, status dots, `+` per class, import; **asset thumbnail = hero ★** | the list is per-session navigation, not per-image; collapsed it costs 48 px (the author's "unused space" complaint); props and scenes are backend-ready and get their sections | when collapsed, switching assets is two clicks (open, pick) — mitigated by a keyboard palette (Ctrl+P "go to asset") |
| L1 sub-navigation (Visual styles · World · Story spine · Poses) | rail rows | W(L1) / session | the same **Panel**, whose content follows the workspace (styles list with thumbnails · spine characters · pose sets); the editor opens in the canvas | the spec's "rail content changes per workspace"; styles and spine rows *are* lists | the World prose tab has no list — the panel shows the outline of the document instead (headings), which is still useful |
| Version selector · + version · finalize · unlock · export | the L2 stage-context bar | S / batch | **stage header** at the top of the canvas (name · version pill · lock state · `+ version` · export in a `⋯` menu) **and** the version list under the asset in the Panel (grouping/search per §6.3) | the version is the scope of everything below it — it must be *seen* above the grid; the list form in the panel gives the "many small versions" navigation the spec asked for | two places show the version; the header is the truth, the panel is navigation |
| Sandbox | a rail row "▦ Sandbox (unscoped)" | W / session | first row of the Library panel, and the default when no asset is selected | it is where JSON-prompt authoring happens today; it stays one click away | none |
| Recents / project switch | File menu | P / rare | File menu + Start screen (3.1) | — | — |

### 3.3 Authoring — the generation controls (the biggest move)

Today these are *bars over the grid*: the Sandbox/A generate bar (8–10 controls), the Stage-B
recipe bar (14) + sketch bar (8) + hero strip + inline cell picker + two params drawers + the
JSON tree + the style bar. They are **column-shaped content forced into rows**.

| Function | Today | Scope / freq | Proposed home | Why | Downside |
| --- | --- | --- | --- | --- | --- |
| Prompt · JSON prompt tree (flux.2-dev) | one-line input + a drawer that opens *in the stage* | S / image | **Composer panel** (the Panel in "Compose" mode, opened from the rail's ✨ icon): a multi-line prompt, the JSON tree as a proper column form (scene · subjects · camera · lighting · palette), raw-JSON toggle, and the **Generate ▶ pinned at the panel foot** (Canva drawer / Photoshop panel) | the author says JSON prompting is "the invaluable addition" — it deserves a column, not a foldout under a bar; pinning the action at the foot keeps it reachable while the tree scrolls | the composer competes with the Library for the single left panel → **tabs at the panel top** (Library · Compose · Train); the rail icon opens the right one; layout is persisted |
| Pipeline · model · sampling preset · N · candidates · Cast/Generate | the generate bar | S / image | Composer **header**: model picker (pipeline + variant + preset in one popover, showing 🔒/⚠ gates), N/cand steppers; **Generate ▶ at the foot** | a model choice is made once per burst; a single picker replaces three selects; the disabled `mode` select disappears | the header must show the *resolved* choice (e.g. "flux.2-dev · Dev/JSON · 8 steps") or the author loses what a bar showed at a glance |
| Params drawer (catalog-driven) | `⚙ params (n)` toggles a second bar | S / batch | Composer **Advanced** section (collapsible), grouped as today | it is column content; it already folds | — |
| Style chip · apply toggle | style bar in every stage | S / batch | Composer header (one chip + toggle); shown **only** where it is consumed (Sandbox/A, B); StyleLock stays a postproc preset | today it renders in C and D and silently edits global state | the L1 → generation link becomes less visible in C/D — correct, it does nothing there |
| Preview (pre-flight modal) | ghost button | S / batch | Composer foot next to Generate (`Preview` secondary) | a dry run belongs next to the run | — |
| Stage-B recipe (recipe · cells · pipeline · model · sampling · advanced prompting · strength · realize · matte · identity · clause · params) | one 14-control bar + inline cell picker | S(B) / batch | Composer in **Expand mode**: sections *Recipe* (preset + the cell picker as a grid inside the panel, "n/m cells"), *Model*, *Identity* (matte state, anchor, the identity toggle **with its explanation as visible text**), *Character clause*, *Advanced*; **Generate dataset ▶** at the foot; the **hero ★ / anchor** strip becomes the *header image* of the composer in B | the bar is the single worst overflow in the app; every one of its controls is batch-scoped and column-shaped; the four-state identity tooltip finally gets a sentence | the composer is long in B — acceptable, it scrolls; the primary action is pinned |
| Video sketch (angle · shot · expr · motion · every · frames · Sketch ▶) | a second bar in B | S(B) / rare | Composer Expand mode, collapsed **Sketch** section | rare, and it belongs with expansion | hidden one fold deeper — acceptable for a rare action |
| Hero ★ · face anchor (set/clear/derive portrait) | hero strip in B + inspector buttons | S(B) / batch | composer header image in B (hero + anchor thumbs, with set/derive/clear) **and** on the tile (★ visible on select) | the hero is the input of expansion; it belongs at the top of the expansion form | — |

### 3.4 Review — the canvas

| Function | Today | Scope / freq | Proposed home | Why | Downside |
| --- | --- | --- | --- | --- | --- |
| Image grid (flat) | fixed 3 columns, tile aspect = job dims | S / image | **Canvas**: responsive `auto-fill, minmax(zoom, 1fr)` with a **thumbnail-size slider** in the strip (Lightroom), uniform tile boxes (letterbox inside), virtualised past ~300 tiles | the grid is the tool's canvas and must use the width it gets; zoom replaces "3 columns" | uniform boxes crop nothing but show bars around 16:9 casts — a toggle "fit/fill" covers both |
| Grouped view | toggle above the grid | S / image | the same canvas, **view switch in the strip** (Flat · Grouped · Captions · Compare) | it is a *view* of the same cells; a strip is where view switches live | — |
| Tile actions (★ keep reject bulk 🗑 🔍 ✕) | hover-only overlays | Sel / image | overlays visible **on hover AND on the selected tile**; keyboard-reachable; the *destructive* ones (🗑) never single-click without confirm; a persistent **selection bar** in the strip shows "n selected · keep · reject · delete · compare" | P6; bulk actions become discoverable instead of appearing only after a space-bar toggle | slightly busier tiles when selected — acceptable |
| Stage A·B·C·D | title-bar buttons | S / batch | the **Strip**, left end, as verbs: **Cast · Expand · Curate · Train** (letters kept as tooltips/keys 1–4) | stage-scoped, per-batch: it is the options-bar analogue and must sit over the canvas, not in the global bar | renaming touches docs/tests that say A·B·C·D — keep the letters as the internal ids |
| Curation filters (shot · angle · expr · show rejected) · counts | curate bar | S(C) / image | the Strip in Curate | filters are per-view, one row, chip-shaped — the one thing that *is* a bar | — |
| Bulk keep/reject/clear | curate bar when bulk > 0 | Sel / image | the Strip's selection bar (3.4 row 3) | — | — |
| Prompt template + Save AssetProfile | a second bar in C | S(C) / batch | the **stage header** (version pill · `Save profile 🔒`), template text in the Inspector's *Version* tab | the template is version metadata, not a per-image control | one more click to edit it — it is edited rarely |
| Keyboard | ←→ ±1, ↑↓ ±5 (wrong stride), k, x, space in C only | S / image | arrows move by *visual* row (computed from the grid's column count); `k`/`x`/`space`/`Del`/`Enter`(loupe)/`1–4`(stages)/`Tab`(hide panels)/`Ctrl+P`(go to asset) everywhere the grid has focus; a `?` overlay lists them | the stride bug is fixed by construction; Photoshop's Tab and Lightroom's loupe keys are the cheapest productivity win | shortcuts need a single dispatcher — part of the App split |
| Lightbox | full-res image + ✕ | Sel / image | **Loupe view** in the canvas: prev/next, zoom to 1:1, the facts strip, **Compare** (two tiles side by side — the existing A/B preview and any two selections), Esc back to grid | the review loop is "zoom, compare, decide"; the lightbox does only "zoom" | the loupe replaces the canvas rather than overlaying it — that is the Lightroom model and it keeps the inspector live |
| Empty states / banners | text in the stage column | S / event | the canvas empty state (unchanged copy) + the banner slot (3.1) | — | — |

### 3.5 Selection properties — the right side

| Function | Today | Scope / freq | Proposed home | Why | Downside |
| --- | --- | --- | --- | --- | --- |
| Job facts · resolved prompt · params · log/stderr · pose · identity | `Inspector` in 240 px | Sel / image | **Inspector panel, tab "Info"** (300–420 px, resizable, collapsible; Photoshop/Figma properties) with foldable sections; the log tail gets a proper monospace block | the properties pane must be wide enough to *read* a prompt and a log | screen width: on a 1920 px display, rail 48 + panel 320 + inspector 360 leaves ~1 190 px of canvas — fine; on 1440 px the panels default to collapsed |
| Re-run (seed · steps · guidance) | `RerunPanel` | Sel / image | Info tab, a "Re-run" row under the facts | it is an action on the selected job | — |
| Postprocess stack (list + add form) | `PostprocPanel` in 240 px | Sel / image | **Inspector tab "Post"**: the step tree as a vertical list with branch indentation, tombstones struck through, and the add form with room for the JSON tree and the size row | the stack is the most-used selection tool and the most squeezed; a 360 px column fits its widest row (`.pp-add-row` needs ~330 px unwrapped) | the flux.2-dev JSON tree appears in two places (composer and post) — same component, different scope; acceptable |
| Anchor actions (set as face anchor · derive portrait) | buttons above the inspector | Sel(B) / batch | Info tab actions row **and** the composer's identity section | — | — |
| Readiness meter | inside TrainPanel | S(D) / batch | **Inspector tab "Readiness"** (shown in Train), the four tiers as rows with their details *inline* (missing cells as chips, duplicate groups as tile pairs) instead of tooltips | a meter is a report; reports live on the right; its details are lists, not tooltips | — |
| Version metadata (prompt template · trigger · lora · caption status) | scattered (curate bar, TrainPanel head) | S / batch | **Inspector tab "Version"** when no tile is selected (the inspector always has something to show) | the version is the selection when nothing else is | — |

### 3.6 Train (Stage D)

| Function | Today | Scope / freq | Proposed home | Why | Downside |
| --- | --- | --- | --- | --- | --- |
| Stage form (base · init · trigger · steps · advanced) · `Stage ▶` | TrainPanel card above the D grid | S(D) / batch | **Composer in Train mode** (form column, `Stage · Train LoRA ▶` at the foot) | it is a batch-scoped form with a primary action — composer shape | — |
| Captions editor (per-ref rows) | a foldout list in TrainPanel | S(D) / image | the canvas **Captions view** (3.4 view switch): thumbnail · caption text · origin badge · ⚠ · save/reset, one row per ref, with the coverage cell as a filter | per-ref editing is canvas work — it needs width and the image next to the text | — |
| Staged runs · training jobs (progress · preview · promote · cleanup) | lists in TrainPanel | S(D) / batch | the **Dock** (they are jobs: the dock grows into a "Jobs" pane with a *Training* filter showing step/loss/ETA), with **Preview ▶ / Promote ⬆** on the job row; the pose-picker preview form opens in the composer | the trainer is the first multi-hour job and the dock is where progress belongs (the spec's "always-visible queue") | the dock must become expandable (a DaVinci-style drawer), which is also what L3/L5 timelines need |
| Promoted LoRA facts | a muted line | S(D) / rare | Inspector "Version" tab | — | — |

### 3.7 Postprocess and the coming inpaint tool

| Function | Today | Scope / freq | Proposed home | Why | Downside |
| --- | --- | --- | --- | --- | --- |
| Presets (Clean · Refine · StyleLock · Upscale · Resize · Restore) | the Post add-form | Sel / image | Post tab (3.5) | — | — |
| **Masked inpaint** (planned) | none (the step contract carries a mask; sd35/zimage have inpaint modes) | Sel / image | **Edit mode of the canvas**: the selected image fills the canvas with a **tool rail** (brush · eraser · lasso · invert · feather · from-matte) on the left edge of the canvas, the mask as an overlay, the Post tab holding the inpaint step (preset · prompt · strength · model) and `Queue ▶` | this is Photoshop's canvas literally; a mask is painted *on* the image, so the image must be the canvas, not a 240 px thumbnail | Edit mode is a new canvas surface (a `<canvas>` layer over the image) — the first Konva-class component, which L3's compositor will reuse |
| Mask sources (BiRefNet matte · manual · future SAM) | matte job only | Sel / image | Edit mode's "from" menu | — | — |

### 3.8 L1 World

| Function | Today | Proposed home | Why | Downside |
| --- | --- | --- | --- | --- |
| Visual styles (list · detail · sample) | tile grid + detail card in the stage | Panel lists styles (thumbs); the **canvas shows the style editor** (prompt · negative · sample with generate/cancel); the sample uses the same Composer model picker | list-left / editor-centre is the pattern everywhere; the style sample generation gets the error/banner surface it lacks | — |
| World prose · spine · poses | readable editors, fine | unchanged content; the canvas hosts them; the panel lists spine characters / pose sets | — | — |
| L1 errors and queue state | **none** (the L2-only error line) | the global toast/banner slot (3.1) | the one correctness gap in L1 | — |

### 3.9 The dock

| Function | Today | Proposed home | Why | Downside |
| --- | --- | --- | --- | --- |
| Queue headline · jobs popover · counts · disk · unpause | one line + popover | **Dock, collapsed = one line** (as now) · **expanded = a Jobs pane** (rows with progress, note, stop/cancel, training step/loss/ETA, filters by kind) · later the same zone hosts the L3 take timeline / L5 episode lane (P7 + Resolve) | the queue stays always visible; the expanded form replaces the popover *and* the TrainPanel job lists | the dock's expanded height competes with the canvas — a drag handle and a remembered height per workspace |

### 3.10 Future workspaces (so the frame is designed for them now)

| Workspace | Navigator (Panel) | Canvas | Inspector | Dock | Notes |
| --- | --- | --- | --- | --- | --- |
| **L3 Shots** (P3) | scene tree · shot list | **compositor canvas** (layers = L2 assets; Konva) or the **take timeline** (segment cards) via the view switch | layers · camera · continuity (Muse) · Post | take timeline can also live here (expanded dock) — the author's keyframe-first idea (flux.2 JSON keyframes → cheap i2v between them) fits: keyframes are composed/generated in the canvas, segments in the dock | Edit mode's canvas layer (3.7) is the seed of the compositor |
| **L4 Flow** (P4) | flow outline · variables | **React Flow graph** | node inspector (dialogue, pins, conditions) · Muse | play-mode reader pane can open in the dock | the triad matches `kb-loom-p4.md` §5 exactly |
| **L5 Episode** (P5) | render records · chapters | single-lane timeline (spec §5) — or the canvas shows the preview and the **dock is the timeline** | junction inspector (transition · overlap · trim/freeze) | timeline | Resolve's shape |
| **Muse** (P3 director → P4 chat/agent) | — | inline ghost-text in fields | **Inspector tab "Muse"** (chat dock sees the selection) · agent plans as a modal with the approve step | — | a tab on the right keeps it context-bound; a floating window would lose the selection link |

---

## 4. Proposed layout

Two candidate frames were considered; the recommendation is a hybrid, and the alternatives are
kept for the decision record.

**Alt A — "Canva":** 48 px icon rail → expandable side drawer (Library · Compose · Train) that
*pushes* the canvas; a contextual strip above the canvas; **no right panel by default** —
properties appear as a floating card near the selection.
*Pro:* maximal canvas; the left side costs 48 px when idle. *Con:* loom's selection properties
(facts · post stack · readiness) are dense and *sticky* — a floating card would be open all the
time and hide tiles; Canva's model works because its properties are few.

**Alt B — "Photoshop / Lightroom":** workspace tabs top; a fixed left navigator; canvas with an
options bar; **right dockable tabbed panels**; bottom filmstrip.
*Pro:* the properties pane finally has room and tabs; all panels collapsible. *Con:* Photoshop's
left side is a tool rail, not a place to author prompts — the composer would end up in the right
panels, crowding the inspector, or back in bars over the canvas.

**Recommendation — A's left side + B's right side + one strip + a growable dock:**

```
┌ Loreweave Studio ─ [World] [Assets] [Shots] [Flow] [Episode] ─ my-story ─── ● orch · ⚙GPU · ⏵2 · 💾 61% · [File▾] ┐
│ ▌sticky banner slot (paused · disk · weights · offline) — only when needed                                       │
├──┬──────────────┬──────────────────────────────────────────────────────┬────────────────────────────────────────┤
│▤ │ ◂ COMPOSE ·  │ Mara · v3 ✨ 🔒  [+ version] [⋯]        ← stage header  │ INFO │ POST │ READINESS │ VERSION │ MUSE│
│✨ │ Expand ─────  │ Cast · [Expand] · Curate · Train  ▦ 🌳 ▭ ◧  ⊡━━●━━ 🔍 │ job_9a2dad37 · done · flux2/ref     │
│⚙ │ ┌hero ★ ⚓ ┐   │ ┌────┐┌────┐┌────┐┌────┐┌────┐┌────┐  ← canvas         │ 512² · seed 4127 · 38 s              │
│  │ └──────────┘   │ │    ││    ││ ★  ││    ││    ││    │                    │ ▸ resolved prompt                    │
│  │ Recipe ▾       │ └────┘└────┘└────┘└────┘└────┘└────┘                    │ ▸ params                             │
│  │  comprehensive │ ┌────┐┌────┐┌────┐┌────┐┌────┐┌────┐                    │ ── re-run: seed [   ] steps [ ] ↻    │
│  │  ▦ 78/120 cells│ │    ││ ✓  ││    ││ ✕  ││    ││    │                    │                                      │
│  │ Model ▾        │ └────┘└────┘└────┘└────┘└────┘└────┘                    │ POST  (tab)                          │
│  │  flux.2-dev ·  │ ┌────┐┌────┐┌────┐┌────┐┌────┐┌────┐                    │  base ─ clean 0.5 ─ resize ×0.5 ✓    │
│  │  Dev/JSON · 8  │ │    ││    ││    ││    ││    ││    │                    │        └ 🗑 deleted ─ restore ✓      │
│  │ Identity ▾     │ └────┘└────┘└────┘└────┘└────┘└────┘                    │  + step: [Clean ▾][zimage ▾] str 0.5 │
│  │  ⚓ verified ·  │                                                        │    ≈4 effective steps · out 512²     │
│  │  [x] lock ident│                                                        │    [+ add] [▶ queue]                 │
│  │ Clause ______  │                                                        │                                      │
│  │ ▸ Sketch       │                                                        │                                      │
│  │ ▸ Advanced     │                                                        │                                      │
│  │ [Preview] [Generate dataset ▶]  ← pinned foot                           │                                      │
├──┴──────────────┴──────────────────────────────────────────────────────┴────────────────────────────────────────┤
│ ▲ JOB QUEUE ▶ running zimage_trainer · step 222/500 · loss 0.47 · ETA 6 min ▓▓▓▓▓░░ 44 % · 3 queued · [⏸] [jobs]  │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Zone rules:

| Zone | Size | Behaviour |
| --- | --- | --- |
| **Top bar** | 36–40 px | workspace tabs left (L3–L5 disabled until their phase); project name centre; status cluster + File right; a **banner slot** underneath only when a sticky state exists |
| **Rail** | 48 px, icon-only | Library ▤ · Compose ✨ · Train ⚙ (L2) — or the workspace's own set; clicking toggles the Panel; the active icon is lit; `Tab` hides Panel + Inspector (Photoshop) |
| **Panel** | 280–360 px, drag-resizable, collapsible | one of Library / Compose / Train (tabs at its top as well as the rail); **primary action pinned at the foot**; content scrolls independently |
| **Strip** | one 32 px row over the canvas | left: stage verbs (Cast · Expand · Curate · Train); middle: view switch (Flat · Grouped · Captions · Loupe/Compare) + filters (Curate) + selection bar (when n > 0); right: thumbnail-size slider, search |
| **Stage header** | one row above the strip | asset name · version pill · lock · `+ version` · `⋯` (export, finalize, unlock, save profile) |
| **Canvas** | the remainder, `min-width` 640 px | responsive grid / grouped tree / captions list / loupe / **edit mode** (image + tool rail + mask overlay) |
| **Inspector** | 300–420 px, drag-resizable, collapsible | tabs: **Info · Post · Readiness · Version · Muse**; sections foldable; monospace blocks readable |
| **Dock** | 28 px collapsed · drag-resizable expanded (remembered per workspace) | queue headline + progress; expanded = Jobs pane (later: timelines) |
| **Dialogs** | real modals | new project · new version · pre-flight · confirm (destructive) · settings; **no `window.prompt`**, `window.confirm` only as the fallback outside the shell |
| **Layout memory** | `localStorage` per workspace | panel widths, collapsed states, dock height, grid zoom, last stage/view |

Space check on the author's display sizes (all widths in px): at 1920 → 48 + 320 + 1 152 + 360 +
margins; at 2560 → the canvas gets ~1 750; at 1440 the Panel and Inspector default to collapsed
and the canvas keeps ≥ 1 000. Today's fixed 240 + 240 columns leave 1 400 at 1920 and 960 at
1440 — *and* spend it on bars.

---

## 5. Three workflows in the proposed frame

**W1 — JSON-authored image → refine → inpaint (the author's stated daily loop).**
Rail ✨ opens Compose in the Sandbox; model picker "flux.2-dev · Dev/JSON"; the JSON tree is a
form column; `Generate ▶` at the foot. The tile lands in the canvas; select it → Inspector Info
(facts, re-run seed) → Post tab: add Clean (zimage 0.5) → queue; the chain draws under the base.
Enter → Loupe; `c` → Compare base vs clean. Edit mode: paint a mask over the hand, Post tab shows
"Inpaint · sd35 · prompt · strength" → queue. Every step keeps the composer and the stack open;
nothing scrolls away.

**W2 — Character bootstrap (Cast → Expand → Curate → Train).**
Library panel → Mara. Strip: **Cast** — Compose in Cast mode (multi, 4 candidates, style chip on)
→ tiles stream; ★ one. **Expand** — Compose shows the hero ★ + anchor at its head, recipe +
cells grid in the panel, identity section with its explanation in plain text; `Generate dataset
▶`. **Curate** — the strip holds shot/angle/expr filters and the selection bar; keys `k`/`x`;
Captions view shows the template captions with the thumbnails. **Train** — Compose in Train mode
(base, init, trigger, steps, advanced) → `Stage ▶`; the staged run appears in the expanded dock;
`▶ Add to queue`; progress lives in the dock headline (step · loss · ETA); Readiness tab on the
right with missing cells as chips; Preview ▶ on the dock row opens the pose picker in the
composer; samples land in the canvas under Train; Promote ⬆ on the row.

**W3 — Keyframes → shot (P3, the author's animation idea).**
Shots workspace: Library panel lists scenes/shots; canvas = compositor (layers from L2 at the
adapter's trained resolution, upscaled per the P2→P3 constraint) or a flux.2 JSON keyframe
generated straight from the Compose panel (same component, now producing a keyframe); dock =
the take timeline (segment cards: start keyframe → i2v drafts → pick → last frame → next); Muse
tab writes the continuity prompt. The frame does not change between L2 and L3 — only the
panel/canvas/dock *contents* do (P1).

---

## 6. Migration plan — incremental, GPU-free, each step shippable

Ordered so every step leaves the app usable and is verifiable without the card (all UI; the FE
contract tests in `orchestrator/tests` pin the source, `tsc` + `vite build` gate every step).

| # | Step | What it delivers | Verify without GPU |
| --- | --- | --- | --- |
| **0** | ~~Split App.tsx~~ → **DONE 2026-09-20 as the move + seed** (see the Adopted note at the top): `app/` → `frontends/{shell, shared, v1}`; `frontends/v2` scaffolded on the shared client; one shell wraps either by config overlay; the orchestrator's CORS admits the v2 dev port; tests and README re-pointed | v1 frozen as reference/fallback — never refactored; v2 starts clean with a real state layer (zustand) | v1 `tsc` + `vite build` from its new home (token still absent from `dist/`), v2 `tsc` + `vite build`, `cargo check` from `frontends/shell/src-tauri`, the backend suite under the torch guard |
| **1** | **Frame** ✅ **DONE 2026-09-20** (v2; journal "🧱 M2.14 step 1"): top bar with workspace tabs (L3–L5 disabled) + status cluster + banner slot; icon rail + resizable/collapsible Panel; resizable/collapsible Inspector; dock collapsed/expanded with a handle; layout memory; **toast + banner system** (fixes the L1 blind spot); plus a read-only canvas grid with live zoom/selection and a real Info tab, so the frame shows the author's project on day one | the author's two complaints (unused rail, squeezed inspector) fixed structurally | resize/collapse/persist across reload; an L1 style-sample error now shows |
| **2** | **Project dialogs**: `tauri-plugin-dialog` folder picker for Open/New; New-project modal with the estimate inline; **Start screen** with visual recents | "painful project open" fixed | dev-build fallback to the typed path; a recents screen with thumbnails |
| **3** | **Composer panel** (Cast / Expand / Train modes; Sandbox = Cast) with the pinned foot; **stage header**; **strip** (stage verbs, view switch, filters, selection bar, zoom); the bars, drawers, inline picker, sketch bar and style bar leave the canvas column | the grid becomes the canvas | every control reachable in its new home; Stage-B recipe fires the same request (the pre-flight modal proves it byte-for-byte) |
| **4** | **Inspector tabs** (Info · Post · Readiness · Version) with the Post add-form laid out for a 360 px column; tombstones/branches drawn as a tree | the stack gets room | the JSON tree renders as a form, not a stack of stacked inputs |
| **5** | **Canvas**: responsive grid + zoom slider, uniform tiles with fit/fill, affordances visible on selection, keyboard by visual row + global shortcuts + `?` overlay, **Loupe with prev/next + Compare** | the review loop | keyboard stride correct on any width; compare two tiles |
| **6** | **Train**: form → Composer Train mode; captions → Captions canvas view; staged/jobs → expanded dock with training progress; Readiness tab inline details; preview picker in the composer | Stage D stops being a card in a column | the same endpoints, new homes; dock shows step/loss/ETA from `job.note` |
| **7** | **Edit mode** (image canvas + tool rail + mask overlay → PNG mask into `out/`) + the **Inpaint** postproc preset (sd35/zimage today; the flux2 masked-denoise branch written rig-owed) | the tool the author asked for first | mask round-trips to disk and into a step's `mask`; queue is dry-run-verified |
| **8** | Placeholders for L3–L5 tabs (empty canvas with the spec's mockup as a "coming in P3" card) and the Muse tab (disabled) | the frame shows the whole tool | — |

Sizing (solo dev, no GPU needed): 0 ≈ 2 sessions · 1 ≈ 2 · 2 ≈ 1 · 3 ≈ 3 · 4 ≈ 1 · 5 ≈ 2 · 6 ≈ 2 ·
7 ≈ 3 · 8 ≈ 0.5. The **CPU/ggml spike** can start after step 1 without conflict (it is
orchestrator + a new adapter), or after the whole plan per the author's sequencing.

**Where this sits in the phase plan.** It is an **M0-class preflight** like the 2026-06 reset:
it changes no P0–P5 scope (R165) and no backend contract, so it slots in as a P2 milestone block
(proposed **M2.14 — shell overhaul**) before P3 opens, with the same journal/README/§12 sweep
the plan-consistency rule requires when it is adopted.

---

## 7. Decisions for the author (D1–D10)

| # | Decision | Recommendation | Why it matters |
| --- | --- | --- | --- |
| **D1** | Composer on the **left** (rail + panel) vs a **bottom composer** (Midjourney/Canva-Magic-Media style prompt box) | left panel | the JSON tree and the Stage-B recipe are columns; a bottom box only fits a one-line prompt and would push the dock |
| **D2** | Inspector **open by default** vs collapsed | open on ≥ 1 600 px, collapsed below | the post stack is a daily tool |
| **D3** | Stage names: keep **A·B·C·D** vs verbs **Cast · Expand · Curate · Train** | verbs in the UI, letters as ids/keys | letters are spec shorthand, verbs are what the strip should say |
| **D4** | `tauri-plugin-dialog` (native folder picker; adds a capability) vs a custom in-app folder browser | the plugin | a custom browser is weeks; the plugin is the platform's |
| **D5** | Grid tiles **uniform boxes** (fit) vs **natural aspect** (today) | uniform with a fit/fill toggle | rows align; zoom works |
| **D6** | Expose **props · scenes** asset classes now (backend-ready) or wait for P3 | expose in the Library panel now | zero backend work; P3's compositor needs them anyway |
| **D7** | Loupe replaces the canvas (Lightroom) vs overlay lightbox (today) | replaces | keeps the inspector live while comparing |
| **D8** | Dock as the future **timeline** zone (L3/L5) vs timelines in the canvas | dock | matches Resolve and the spec's always-visible queue; the canvas stays the compositor/preview |
| **D9** | Muse as an **Inspector tab** vs a separate side sheet | tab | keeps the selection link the spec requires |
| **D10** | Schedule: adopt as **M2.14** before P3 (recommended), or run steps 0–2 now and the rest alongside P3 | M2.14, steps 0–7; step 8 with P3 | half a shell is worse than the old one; P3's compositor and timeline need the frame first |

*All ten accepted as recommended by the author on 2026-09-20, plus the from-scratch amendment (see the Adopted note at the top).*

---

## 8. Source / traceability

- The UI inventory (zone map, 20 code-visible pain points, future hooks) — read-only pass at
  HEAD `ce70c60`, 2026-09-20; line references in the journal-linked report are to that HEAD.
- Original shell intent: `kb-storyboard01.md` §6.1 (Navigator · Stage · Inspector · always-visible
  Job Queue), §6.3 ASSETS, §6.4 SHOTS (compositor + take timeline), §6.5 FLOW, §6.7 EPISODE.
- The M0a reset that produced today's shell: `kb-loom-p2.md` §12 "Phase 0 — MVP usability reset".
- Future workspaces this frame must house: `kb-loom-p3.md` §5–§6, `kb-loom-p4.md` §5–§8,
  `kb-loom-p5.md` §5; Muse plug-in points `kb-loom-p4.md` §8.
- The P2→P3 resolution constraint on character layers: `kb-loom-p3.md` §5 (⚠ block).
- Review findings that the split (step 0) also settles: journal `kb-loom-p2-imp.md`
  "🛠 Hardening pass" ledger (frontend lifecycle group: character clause reset on asset switch,
  Sandbox re-seed, poll/staleness guards, missing confirms).
