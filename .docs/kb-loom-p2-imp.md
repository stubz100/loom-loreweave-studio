

## ⭐ Pass 2 — the grouped operation/derivation tree (author obs. 2) (2026-08-08 20:10–20:55 CEDT)

**Author:** *"multi-candidate generations, batch generations, post-proc and expansions are
increasing the image libraries significantly… it would be wise to introduce another view, where
images could be grouped together based on the operation they have been created… The current
view should still exist… but we should also have a toggle switch, that enables these grouped
views, working in a collapsable tree structure. This should also enable group operations —
like group delete."*

### Shape: operation groups at the top level, derivation nested inside

New **dedicated module** `app/src/GroupedGrid.tsx` (M2.8 monolith policy — new feature families
stay out of `App.tsx`). Top level = the operation, keyed on **`batch_id`**; inside a group,
postprocessed images nest under the job they were derived from via **`chained_from`**:

```
▾ ▦ Expansion sweep · flux2 · 24 jobs · 24 images · 12:04     🗑 group
   [cell tile]
      └ clean · zimage · job_5f6bc34e
         [clean tile]
            └ resize · job_63eb6a88
               [resized tile]
▸ 🎭 Cast · zimage · 6 jobs · 11:12
```

Group labels come from the submit-site batch prefixes (`prv_` preview · `trn_` training ·
`rdn_` readiness · `poses_` icons · `bat_` + stage → Cast/Expansion/Batch); a **solo postproc
job carries no batch id**, so it is keyed individually rather than collapsing every unrelated
pass into one "no batch" bucket.

**Nesting is by JOB, not by output — deliberately.** A derivation is not always 1→1: the manual
postproc surface is (one image → one pass), but an **auto-chained pass is 1→N** — a single job
over every output of its parent. Hanging that off one tile would be a lie, so children attach
to the parent job and read correctly in both cases. A job whose parent is outside the current
scope (deleted, or filtered out by the stage) becomes a root rather than vanishing.

### The flat view is untouched — and cannot drift

The classic grid stays exactly as it was; a `▦ flat / 🌳 grouped` toggle swaps the tree in over
the **same scope**. Crucially the tile JSX was **extracted into one shared `renderTile`** that
both views call, so selection, curation, star/keep/cull, delete, bulk-select and the lightbox
behave identically — they are literally the same function, not two implementations. The toggle
is hidden in **Stage C**, where curation is a flat triage pass and a tree would only get in the
way.

### Group delete

`🗑 group` loops the **audited per-job `DELETE /jobs/{id}`** rather than inventing bulk
semantics: each call is atomic and orchestrator-owned (out dir, manifest, log, queue entry,
lineage edge — R80), and a running/queued job **409s instead of half-deleting**. The confirm
states the job count, warns how many are still running, and says out loud that the group
**includes anything postprocessed from it** (the tree collects descendants). The tally reports
what could not be removed instead of failing silently.

### Why this needed the styles pass first

The tree is only as good as `chained_from`, which the M2.12 spike measured at **0 of 661 real
jobs** — the manual postproc surface never set it. That was fixed in the styles pass, which is
why the sequencing mattered: nesting now reads a real edge instead of reconstructing one from a
`[X postproc of Y]` prompt string.

**Tests +2 → 432 green** — no JS runner in this repo, so the established pattern: a
**behavioural** check that one operation really shares one `batch_id` and a postproc job really
points at its source (the two keys the tree nests by, asserted on live submissions), plus a
**source contract** for the FE (dedicated module · groups by batch_id · nests by chained_from ·
the flat view survives behind the toggle · ONE shared tile renderer · group delete loops the
audited single delete and reports skips). `tsc` + `vite build` clean.

**Owed:** the author's visual sign-off on the tree — and it wants a real library to be judged on
(char02's 661-job project is the honest test, not a fixture).

**✅ PUSHED `dc6ce45`.**

### Pass 2 design revision — cards, covers, and the one-column bug (author feedback, same day)

*"I like the grouping logic, but the design I'm not happy with"* — three complaints, three
fixes. The grouping/nesting logic is unchanged; this is layout only.

**#3 was a real BUG, not a preference.** *"when opening a collection, you can see a list of
tiles in one column"* — each root job rendered **its own** `.tree-tiles` grid, and since a cell
job produces one image, a 24-cell expansion sweep drew **24 stacked single-tile grids**. The
`auto-fill` track sizing was right all along; it just never had more than one tile to work
with. Now every **childless** root pools its tiles into **ONE grid** per group, and only a root
that genuinely has derived children keeps its own nested block (which is what carries the
`└ clean → └ resize` chain). Groups of independent images fill the width; chains still read as
chains.

**#1 — the bars ate vertical space.** Collapsed groups are now **cards in a multi-column grid**
(`auto-fill, minmax(210px, 1fr)` — two-plus columns at any usable width). An **open** group
takes `grid-column: 1 / -1`, so it spans every column and its tiles get the full width. One
layout serves both jobs: scanning many operations, and studying one.

**#2 — a bar told you nothing about its contents.** Each card now leads with a **cover image**
— the first tile in the group that actually has one, walked depth-first through the derivation
children so a postproc-only group still shows something — with the label and facts
(pipeline · job count · image count · time) underneath, plus a `×N` badge when the group holds
more than one root. Queued/failed/video tiles resolve to no cover and fall back to a
placeholder rather than a broken image.

The group `🗑` moved into the expanded header (it needs the deliberate act of opening a group
first, which is the right friction for a destructive bulk action).

**Tests +1 → 433 green** — a layout contract pinning all three fixes: the card grid + the
full-width open group, the cover resolver, and specifically the childless-root pooling that was
the one-column bug. `tsc` + `vite build` clean.

**✅ PUSHED `fcd1eb7`.**

### Pass 2 follow-ups — Stage C, horizontal chains, and BRANCHING postproc stacks (author, same day)

Three questions, and the third turned out to be a real capability gap rather than a view issue.

**1 — Stage C was omitted deliberately, and the reason was wrong.** Curation is a flat triage
pass, so a tree looked like clutter. But the author asked, and the real objection was narrower
than "no": Stage C shows **durable curated refs** — a version copied from a parent keeps its
`refs/` files and has **no jobs behind them** — and a job-derived tree cannot hold those, so
switching views would have silently dropped part of the set. Fixed properly instead of
excluded: the toggle is available in Stage C, job-less tiles are surfaced as their own
**📌 Curated refs** group, and the tree now reads the **same filtered `stageCells`** the flat
grid does, so the coverage filters and hide-rejected apply identically in both views (they did
not before — the tree was reading the unfiltered `cells`).

**2a — chains now read LEFT→RIGHT.** *"stacking image tiles in a tree structure will result in
a lot of space wasted"* — correct. A descent is a row (`source → clean → upscale`) that scrolls
sideways rather than squeezing tiles; the **only** thing that costs vertical space is a real
**fan-out**, which is exactly when the shape carries information.

**2b — ⭐ postproc stacks BRANCH now.** *"we don't allow anything else, but the stack… if
someone wants to test different strengths, or types of post processing, the same base image
cannot be used for that."* Exactly right, and it was a one-line assumption in `add_step`:
`source = steps[-1]["output"]`. The stored shape already carried a **per-step `source`**, so
the data model was a tree all along — only the writer was a chain.

- `add_step(..., source=...)` names the branch point: the base, or any **finished** step's
  output. Omitted ⇒ the old continue-the-chain behaviour, so nothing existing changes.
- Branching from an unfinished step is refused — a source must be a real image on disk.
- `remove_step` was *"only the LAST step"*, which is the same rule while a stack is linear.
  It is now **"any LEAF"**: what actually matters is never orphaning steps sourced from the
  one being removed, and a branched-from step is refused with that reason.
- `GET /postproc/sources?base=` lists the legal branch points, and the panel offers them
  (`↳ continue the chain` · `⌂ from the base image` · `⑂ from #N <preset>`).

⚠ **A decorator moved when it should not have.** Inserting `stack_sources` directly above
`add_step` left `@_mutates_store` attached to the **new read-only function**, silently
unlocking the mutator — caught immediately by the M2.8 #3 thread-safety test, which is exactly
what it exists for. Restored onto `add_step`.

**3 — how styles behave in postproc (the author's question), answered precisely.** The stored
prompt of a generated image **already contains** the style fragment (R104 appends it at
generation), so a postproc step inheriting that prompt carries the original style **as text,
merged** — nothing re-applies it. That is the fix from the previous pass and it is correct.
**But restyling was only reachable via the StyleLock preset**: `apply_style` existed in the API
after that pass and had **no UI control**, so on a Clean/Refine/Upscale pass there was no way to
ask for a different style. Now there is — a **`restyle`** tick on every i2i preset, which
applies the chosen L1 style and **strips the inherited fragment first** (using the source job's
stamped `style_id`) so the prompt never describes two looks. Off by default: the source's style
is baked into its pixels, and text cannot un-bake it.

**Tests +4 → 437 green** (branching: two first-level passes off one base, an unfinished source
refused, the branch-point list · leaf-only removal with a named refusal · chains horizontal +
Stage C included + orphan refs surfaced + both views on the same filtered cells · the branch
picker and restyle tick reachable in the panel). `tsc` + `vite build` clean.

**✅ PUSHED `b2e0d27`.**

### Pass 2 follow-up — the branch picker's gate, and per-lineage cards (author, same day)

**1 — the branch picker was gated on a FINISHED step, which blocked its own use case.** It
rendered only when `stack.steps.some(st => st.output)`, so with one pass still queued there was
no picker at all — and *"test different strengths"* is precisely the case where you configure
the second variant while the first is still running. Worse, that is the one moment the old
backend refused outright ("queue and finish the previous step before adding another"), so the
combination left the feature unreachable exactly when wanted. It now appears as soon as the
stack has **any** step: `↳ continue the chain` · `⌂ branch from the base image` · `⑂ branch
from #N <preset>`, with only *finished* outputs listed as branch points (an unfinished step is
not an image yet — the backend enforces this, and the tooltip says why).

**2 — each lineage is now its own collapsible card inside the group.** An open operation
holding several postproc lines got long, and folding one away should not cost the whole
operation. Every chain root renders as a nested card with its own toggle, keyed
`${group}::${job}` so two groups can never collide. Collapsed, it still carries meaning: the
lineage **shape** (`clean → resize`, or `clean +2 branches` once it fans out), the **pass
count**, and a **thumbnail** — so a folded lineage is still identifiable at a glance.

**Tests +1 → 438 green** (the widened gate + the old condition asserted GONE; per-lineage cards
with a namespaced collapse key and a collapsed state that still describes itself). `tsc` +
`vite build` clean.

**✅ PUSHED `4f166f0`.**

### Pass 2 follow-up — control rows must WRAP (author, same day)

*"there are multiple options to the right that don't fit the inspector panel — after the model
picker there are 2 more pull-downs, that can only be seen when the panel is scrolled right"*.

**Self-inflicted and exactly diagnosable.** `.pp-add-row` is a **non-wrapping** flex row. It was
built for three controls (preset · backend · style); the branching work added two more (branch
point · restyle), and in the narrow inspector the last two left the viewport entirely —
reachable only by scrolling sideways, which nothing signposted. `flex: 1` on the selects made it
worse: they shared the overflowing width instead of forcing a wrap.

**Fixed:** the row wraps, and its selects carry `flex: 1 1 118px; min-width: 108px` — a readable
floor is what turns "squeeze five selects into slivers" into "wrap onto a second line". Two side
by side when there is room, stacked when there isn't.

**Swept the rows added in this session for the same bug** rather than fixing only the reported
one. `prev-opts`, `prev-pose-row` and `tree-head` already wrapped. Two did not and hold
**variable-length text**, so they were the same bug waiting: **`.style-bar`** (a style NAME, up
to 22ch) and **`.chain-card-head`** (a lineage description like `clean → resize → upscale
+2 branches`) — both now wrap, and the lineage toggle can break a long label instead of pushing
the thumbnail off the edge. `view-toggle` and `tree-bar` hold fixed short content and were left
alone.

**Tests +1 → 439 green** — a layout contract asserting the wrap on all three at-risk rows plus
the select floor, so the next control added to that row cannot silently reintroduce it.
`tsc` + `vite build` clean.

**✅ PUSHED `7725ac6`.**

### Pass 2 follow-up — a folded lineage is a TILE, not a bar (author, same day)

*"when we collapse a sub-card, can it not take a shape of a bar, rather the same image tile size
as the rest of the images inside the card — the mechanics should work the same way as with the
main card collapse"*.

Right, and it makes the whole view consistent: **one visual language for "a collection folded
away", applied at both levels.** A collapsed lineage now renders with the **same `.tree-card`
shape a collapsed GROUP uses** — cover image, name, facts underneath — sitting in the grid as
one more tile beside the group's plain images. Expanding it takes `grid-column: 1 / -1` and
spans the row, exactly like opening a group card one level up. The old bar (a 34 px thumbnail
shoved to the right) is gone.

The structural change that makes it work: **`.tree-body` IS the tile grid now**, holding the
plain tiles and the lineage cards as siblings. Previously it was a flex column wrapping a
separate `.tree-tiles` grid, which is why a folded lineage could only ever be a full-width row.

⚠ That reshuffle re-armed the **one-column bug** in a second place: the orphan (Stage-C curated
refs) group still nested a `.tree-tiles` grid *inside* the now-grid body, which would have
squeezed it into a single column — the exact failure fixed one level up an hour earlier. Caught
and flattened; `.tree-tiles` is now unreferenced and its rule deleted, so the shape cannot come
back. The badge on a folded lineage reads `⑂N` (branch count) rather than the group card's
`×N`, so the two levels stay distinguishable at a glance.

**439 tests green** (contract extended: the body is the grid, an open lineage spans the row, the
collapsed card reuses `.tree-card`, and `tree-tiles` is asserted GONE from both the module and
the stylesheet). `tsc` + `vite build` clean.

**✅ PUSHED `db9fd6f`.**

## ⭐ Rig finding — the i2i step budget: a "Clean" that did almost nothing (2026-08-09)

**Author:** *"job_724798a6 is a flux clean job, but it is exactly the same as the input image.
Did this job silently fail?"*

**No — and that is the interesting part.** Exit 0, `manifest_status: completed`, all four
stages completed, a real 930 KB output written, **93 % of pixels changed**. Nothing failed. It
did exactly what it was told, and what it was told was almost nothing.

**The denoise stage is the whole story:** `num_steps: 4`, **`num_timesteps: 2`**,
**`timesteps: [0.6, 0.0]`**. Strength 0.6 WAS applied — denoising started at t=0.6 — but
`num_steps` is the **FULL schedule** and an i2i run walks only the last `strength × num_steps`
of it. `flux.2-klein-4b` defaults to **4 steps** (it is distilled for 4-step t2i), so
0.6 × 4 = **2 timesteps**, and the schedule hops 0.6 → 0.0 in ONE move. Mean pixel delta
4.4/255: the collar and cuffs got marginally crisper and nothing else moved. 38 s of load time
to produce a wash.

**Who else is affected** — the distilled/turbo variants, and Refine is worse than Clean:

| model | preset steps | effective @0.5 | @0.25 |
| --- | --- | --- | --- |
| **flux.2-klein-4b / 9b** | 4 | **2** | **1** |
| **sd3.5-large-turbo** | 4 | **2** | **1** |
| zimage-turbo | 9 | 4 | 2 |
| flux.2-dev | 8 | 4 | 2 |
| sd3.5-medium | 40 | 20 | 10 |
| zimage-base / klein-base | 50 | 25 | 12 |

### Fix 1 — a FLOOR on effective steps, not a blanket rescale

`model_catalog.i2i_step_budget()` returns `(num_steps_to_request, effective_steps)`. When the
model's own preset already clears `MIN_EFFECTIVE_I2I_STEPS` (4) it returns `None` — send
nothing, keep the worker's default. Only below the floor does it raise the request to
`ceil(4 / strength)`, capped at `MAX_I2I_STEPS` (60) so a 0.05 strength cannot explode into a
400-step run.

⚠ **This deliberately differs from the "scale by 1/strength" I first proposed.** Rescaling
*everything* to its full budget would take sd3.5-medium from 40 to **80** requested steps —
doubling the cost to buy nothing, since 20 effective steps was already ample. The floor moves
only the degenerate cases: klein 0.6 → request 7 (4 real), klein 0.25 → request 16 (4 real),
sd35-large-turbo likewise; sd35-medium, zimage-base, zimage-turbo and dev are untouched.

Wired into **both** i2i job builders — flux2 (single-run) and zimage/sd35 (batch_items) — at
queue time, reading the step's stored params (the preset's strength is merged in at add time,
so it is always present). An UNSET model resolves the pipeline's default variant, because that
is what the worker will actually run.

### Fix 2 — the panel says what will actually happen

Beside the strength field: **"≈N effective steps"**, and **"(asking N)"** in amber when loom had
to raise the request. Display == reality — the FE mirrors the same arithmetic, and a test
asserts the two floor constants are literally equal, so the readout cannot quietly start lying.
A degenerate strength/model pairing is now visible *before* queueing instead of after a
forensic dig through the manifest.

**Tests +3 → 442 green** (the budget rescues both distilled families at 0.6 and 0.25, leaves
the four already-adequate models untouched, caps a pathological strength, and predicts from the
default variant when the model is unset · the corrected `num_steps` reaches the job on both
the flux2 single-run and the sd35 batch path · the readout exists and its floor matches the
backend's). `tsc` + `vite build` clean.

**✅ PUSHED `2a40d07`.**
