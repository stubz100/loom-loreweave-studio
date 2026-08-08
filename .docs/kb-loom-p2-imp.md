

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
