

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

## ⭐ flux2 i2i schedule — the SHAPE was wrong too + the tombstone/reconcile blind spot (2026-08-09)

### Why "clean" still returned its input after the last fix

The author re-ran the pass with **restyle on** (`job_7efb40c7` style `sty_b9512f` "Dim Glow" →
`job_b5f67d87` style `sty_8b1312` ink-blot — a genuinely different prompt) at **strength 0.85**,
and it still came back nearly unchanged. That killed the "the model is being faithful to the
source's own prompt" reading and pointed at the schedule again.

The previous fix (`f3ac8d9`) delivered the right *count* — `num_timesteps: 5` on the rig,
confirmed — but kept the wrong *shape*:

```
job_b5f67d87  [0.85, 0.8223, 0.7719, 0.6521, 0.0]
        dt      0.0277  0.0504  0.1198  0.6521   ← one leap = 77% of the range
```

`get_schedule` runs a linear ramp through `generalized_time_snr_shift`, whose resolution-driven
`mu` bunches timesteps near t=1. Slicing inherited the bunching (1 interval); **scaling
inherited it too** — three slivers, then a cliff.

**That cliff is the bug.** A single Euler step to t=0 is `x_t - t·v`, and with the model's
velocity ≈ the true `noise - z0` it evaluates to **exactly z0** — the source image. The pass
reconstructed its input by construction, whatever the prompt said. Re-interpretation lives in
curvature accumulated over several moderate steps; one big step short-circuits it.

**Fix — `img2img_schedule`.** The shift is invertible at sigma=1
(`s = e^mu/(e^mu + 1/t - 1)` → `t = s/(s + e^mu·(1-s))`), so map `strength` back to LINEAR time,
ramp linearly from there to 0, and shift each point. `num_steps` intervals across
`[strength, 0]` **with the model's own spacing**:

| strength 0.5, 4 steps | schedule | max:min interval |
| --- | --- | --- |
| scaled (was) | `[0.5, 0.4837, 0.4541, 0.3836, 0.0]` | 23.5 |
| native (now) | `[0.5, 0.4225, 0.3225, 0.1886, 0.0]` | **2.4** |

At `strength=1.0` it reduces byte-for-byte to `get_schedule`, so it generalises the stock
schedule rather than replacing it (t2i untouched). Re-vendored to the monorepo (R162).

⚠ Correction to the last entry: `fixed_params: ["guidance", "num_steps"]` is **manifest
metadata, not enforcement** — `run_pipeline` honours an explicitly requested `num_steps` on
klein. The budget leaves klein at its default 4 because 4 already meets the floor; now that
steps genuinely buy intervals, raising that floor is the next quality knob (rig-owed, not
guessed here). klein remains distilled at guidance 1.0 — for a *re-interpreting* clean,
`flux.2-dev` (8 steps, adjustable guidance 3.0) or the preset's own zimage default is the
better backend.

### The deletion that didn't reach the stack

Author: *"when I deleted the 2nd level image on the stack (zimage/str:0.6), the stack didn't
reflect this change and I can still see the image in the stack, but it doesn't exist as a tile
on the stack card."*

Both halves of yesterday's work were individually correct and did not compose. In the live
project, `pps_7f9d3d` **was** tombstoned properly, while `pps_087ecd` (the author's zimage/0.6)
sat untouched — which localised it to two gaps:

1. **`_job_state` reported a job gone only when its RECORD was gone** — and a tombstone keeps
   the record. So reconcile was blind to exactly the deletes the tombstone rule handles: the
   step held `done` + an `output` naming a file removed with the job. A tombstoned job's images
   are as deleted as any other's → it now reads as gone and goes back through reconcile's own
   tombstone path (kept only while something branches from it).
2. **Nothing re-read the stacks after a delete.** Reconcile is authoritative but only runs on a
   READ; the staleness effect watched queued/running steps only (a delete leaves the step
   `done`), and neither delete handler refreshed postproc. Both handlers now refetch, and the
   effect also treats a done step whose job vanished as stale — self-terminating, since the
   server either drops the step or flags it `deleted`.

Also: the group-delete confirm still promised *"this includes anything postprocessed from
them"*, which the no-cascade tombstone rule had already made untrue.

**Tests +123 → 565 green** (a parametrised `img2img_schedule` matrix over seq-len × strength ×
steps: exact interval count, exact endpoints, monotonic, no step swallowing the range, and
`strength=1.0` ≡ `get_schedule`; a source guard pinning the scaled version's removal; the
tombstoned-job reconcile in both directions; the FE refetch contract). `tsc` + `vite build`
clean.

**Rig-owed:** the visible result. The schedule is provably right now, but only a render says
whether a 4-step distilled klein is a satisfying cleaner at all.

**PUSHED `ef22ab6`.**

### Removing a step deletes the image it produced (2026-08-09)

Author: *"I removed the last step on the stack, but the image is still there, it should have
been deleted."* — the mirror of the delete that never reached the stack, and the other half of
"a step and its image must agree".

`DELETE /postproc/step/{id}` dropped the step record only, so its output lived on as a library
image no stack accounted for — and with the step gone, nothing could ever account for it again.
The endpoint now deletes the producing job too.

- **Order matters.** `remove_step` refuses a step others branch from (409); the image must
  survive that refusal, so the job is deleted only *after* the removal is accepted.
- **The tombstone rule still applies** — `RUNNER.delete` keeps the record (artifacts freed,
  chain intact) when something else derives from that image, rather than stranding descendants.
- **A live job is refused** ("cancel the job first"), matching the queue endpoint; deleting a
  running job's files mid-write is what R80 exists to prevent.
- FE: the ✕ on a finished step now confirms (it destroys an image, and every other destructive
  path asks) and refreshes the jobs so the tile actually leaves the grid.

**Tests +4 → 569 green** (removal deletes the job · a refused removal keeps the image · a live
job is refused · the FE confirm/refresh contract). One of them exposed a latent trap worth
recording: `RUNNER` is a process-wide singleton, so a job left `running` by a test holds the
concurrency slot and stalls every later module's queue — 38 unrelated failures from one line.
Always hand the slot back in a `finally`.

**PUSHED `0afdabe`.**
