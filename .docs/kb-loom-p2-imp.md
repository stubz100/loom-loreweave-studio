

## ⭐ flux2 i2i schedule (the PROPER fix) + stack/delete consistency via tombstones (2026-08-09)

### The flux2 i2i schedule — `num_steps` now means num_steps

`job_7efb40c7` confirmed the diagnosis a third time: `num_steps=8`, strength 0.5, still
`num_timesteps: 2` → `[0.5, 0.0]`. **My previous "fix" was a no-op on flux2** — this replaces
it at the source rather than patching the number upstream of a broken model.

**Root cause.** `stage3_denoise` built the FULL 1.0→0.0 schedule and sliced the tail below
`strength`. But `get_schedule` applies a **resolution-dependent shift**
(`compute_empirical_mu`), and at 1024² (4096 tokens) that pushes nearly every timestep ABOVE
0.6 — so the tail held **one interval whether you asked for 4 steps or 16**. Measured:

| num_steps @1024², strength 0.6 | intervals below 0.6 |
| --- | --- |
| 4 | 1 |
| 7 (my old budget) | **1 — bought nothing** |
| 16 | 3 |
| 50 | 9 |

**Fix:** build the schedule ACROSS `[strength, 0]` with `num_steps` intervals —
`[t * strength for t in get_schedule(num_steps, seq)]`. Scaling the shifted schedule into the
sub-range keeps the model's own step DISTRIBUTION (where it wants to spend denoising effort)
while guaranteeing num_steps means num_steps, at any strength and any resolution. Verified
4/8/16 steps × 0.6/0.5/0.3 all produce exactly that many intervals, head at `strength`, tail
at 0.0. Re-vendored to the monorepo (R162).

⚠ **This changes every flux2 i2i path** — Clean/Refine/StyleLock and the M0e upscale now do
what they claimed. Expect visibly stronger passes; lower the strength rather than the steps.

**Consequence for the step budget:** flux2 is now `"exact"` (num_steps IS the interval count),
zimage/sd35 stay `"fraction"` (diffusers walks only `strength × num_steps`). The budget knows
the difference, so flux2 no longer over-requests — klein's 4-step preset is genuinely 4 steps.

### Stack ↔ image consistency: tombstones

**Author's rule:** *"only if this is the end of the chain, otherwise keep the job manifest (but
flagged deleted), if there are already new images generated on it, so the chain becomes
consistent."* — i.e. **no cascade**; a deleted node with descendants becomes a tombstone.

**The bug.** `RUNNER.delete()` removed the job record, its images, log and lineage edge but
**never touched `postproc_stacks.json`**; and `reconcile()` only revisited QUEUED/RUNNING
steps, so a *done* step kept a `job_id` and an `output` pointing at files that were gone. The
author's live project: **13 dangling bases · 18 dangling outputs · 19 dangling jobs across 26
stacks**, including both clean jobs from the flux2 investigation. Deleting a parent also
stranded its children — `chained_from` no longer resolved, so a postproc pass surfaced as an
unparented top-level card. **That is exactly the char01 anomaly the author suspected, from the
cause they suspected.**

**Built:**
- `RUNNER.has_descendants()` + `delete(..., tombstone=)`. A job **with** descendants keeps its
  record flagged `deleted`, with every artifact pointer cleared (`outputs`/`output_names`/
  `output_meta`/`output_name`/`partial_outputs`) — disk is freed identically, but the chain
  still resolves. A **leaf** is removed completely, as before.
- **`reconcile` is now authoritative.** It runs on every stacks read, so extending it to prune
  dead step records (job gone, nothing branching from them) and drop stacks with neither steps
  nor a base **self-heals the existing 26 stacks with no migration**. A step something branches
  from is kept as a tombstone rather than pruned. The signal is the **job**, not the file —
  `resolve()` already reports a vanished job, and keying on that avoids filesystem races.
- FE: a tombstone renders a dashed `🗑 deleted` placeholder **in its place in the chain**, so
  its children stay attached and visibly parented instead of floating to the top level.

**On the author's point 2** (*"only unparented postproc images should occur as a top level
card"*): that is already the rule `buildGroups` implements — the orphans were manufactured by
the deletion bug, not by the grouping. No grouping change was needed.

**Tests +2 → 444 green** (tombstone kept with cleared artifact pointers + chain still
resolving, leaf deleted outright · reconcile prunes a dead step and tombstones a branched-from
one · the budget's flux2 "exact" vs sd35 "fraction" semantics). `tsc` + `vite build` clean.

**✅ PUSHED `f3ac8d9`.**
