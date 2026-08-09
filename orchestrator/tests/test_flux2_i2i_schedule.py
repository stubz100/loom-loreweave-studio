"""post-M2.11 — the flux2 img2img timestep schedule (`img2img_schedule`).

Author-reported, 2026-08-09: a flux2 "Clean" postprocess returned its input essentially
unchanged — first at strength 0.5 (`job_4064f0f9`), then again at 0.85 with a deliberately
RE-STYLED prompt (`job_b5f67d87`, style `sty_b9512f` → `sty_8b1312`), which ruled out "the
model is just being faithful to the source's own prompt".

The cause was the schedule's SHAPE. `get_schedule` runs a linear ramp through
`generalized_time_snr_shift`, whose resolution-dependent `mu` bunches timesteps near t=1.
Two earlier attempts inherited that bunching:

  * slicing the full 1→0 schedule below `strength` left ONE interval at 1024² no matter how
    many steps were requested (`num_timesteps: 2`);
  * scaling the full schedule by `strength` kept `num_steps` intervals but also kept the
    bunching, so the final interval swallowed the range — at 0.85/4 steps
    `[0.85, 0.822, 0.772, 0.652, 0.0]`, i.e. three ≤0.12 steps then a 0.652 leap.

That last leap is the bug: one Euler step to t=0 is `x_t - t·v`, and with the model's
velocity ≈ the true `noise - z0` that evaluates to **exactly z0** — the source image. So the
pass reconstructed its input by construction, whatever the prompt said.

The fix inverts the shift (invertible at sigma=1) to place `num_steps` intervals across
`[strength, 0]` with the model's OWN spacing. These tests lock the properties that make a
partial-strength run behave like a real traversal; the visible result is rig-owed.

Run from the loom root: `python -m pytest orchestrator/tests/test_flux2_i2i_schedule.py -q`.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# The workers are normally subprocess-invoked, so nothing puts them on sys.path (same
# preamble as test_flux2_dev_quantized).
_LOOM = Path(__file__).resolve().parents[2]
_MULTISTACK = _LOOM / "pipelines" / "multistack"
for _p in (_MULTISTACK / "src", _MULTISTACK / "flux2" / "src"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

pytest.importorskip("torch", reason="flux2 worker lib needs torch")
pytest.importorskip("einops", reason="flux2 sampling needs einops")

from pipeline.flux2.stage3_denoise import img2img_schedule  # noqa: E402
from flux2.sampling import get_schedule  # noqa: E402

# 1024² and 1536² in flux2 tokens (h/16 × w/16), plus a small one — `mu` is seq-len driven,
# and the old bug only showed at the larger sequence lengths.
SEQ = (1024, 4096, 9216)
STRENGTHS = (0.2, 0.3, 0.5, 0.6, 0.85)
STEPS = (1, 2, 4, 8, 16, 50)


@pytest.mark.parametrize("seq", SEQ)
@pytest.mark.parametrize("strength", STRENGTHS)
@pytest.mark.parametrize("num_steps", STEPS)
def test_num_steps_means_num_steps_with_exact_endpoints(seq, strength, num_steps):
    """The regression that started all of this: `num_steps` intervals, always.

    The head must be EXACTLY `strength` (that is where the latent was mixed —
    `x = (1-strength)·z0 + strength·noise`; starting anywhere else is a silent t-mismatch)
    and the tail exactly 0.0 (a schedule that stops short leaves residual noise)."""
    ts = img2img_schedule(num_steps, seq, strength)
    assert len(ts) == num_steps + 1
    assert ts[0] == pytest.approx(strength, abs=1e-9)
    assert ts[-1] == 0.0
    assert all(ts[i] > ts[i + 1] for i in range(len(ts) - 1)), f"not monotonic: {ts}"
    assert all(math.isfinite(t) for t in ts)


@pytest.mark.parametrize("seq", SEQ)
@pytest.mark.parametrize("strength", STRENGTHS)
def test_no_single_step_swallows_the_range(seq, strength):
    """No interval may dominate — the defect that made a clean pass reconstruct its input.

    A step covering most of `[strength, 0]` is a near-direct projection onto the model's z0
    estimate, which for a barely-moved latent IS the source image. Re-interpretation comes
    from curvature accumulated over several moderate steps.

    The bar is deliberately loose (the model's own spacing is not uniform, and it legitimately
    takes its largest step last) but it fails hard on the old schedule: at 0.85/4 the scaled
    version put 76% of the range in one step, and at 0.5/8 it put 58%."""
    num_steps = 8
    ts = img2img_schedule(num_steps, seq, strength)
    dts = [ts[i] - ts[i + 1] for i in range(num_steps)]
    assert max(dts) / strength < 0.45, f"one step covers {max(dts)/strength:.0%} of the range"
    # ...and the spread stays sane rather than three slivers plus a cliff.
    assert max(dts) / min(dts) < 15


@pytest.mark.parametrize("seq", SEQ)
@pytest.mark.parametrize("num_steps", (1, 4, 8, 50))
def test_full_strength_reduces_to_the_stock_schedule(seq, num_steps):
    """strength=1.0 is the whole trajectory, so it must be byte-for-byte `get_schedule` —
    the sub-range construction is a generalisation of the stock schedule, not a replacement.
    This is what keeps t2i (which still calls `get_schedule`) and a full-strength i2i on the
    same footing."""
    got = img2img_schedule(num_steps, seq, 1.0)
    want = get_schedule(num_steps, seq)
    assert got == pytest.approx(want, abs=1e-6)


def test_the_scaled_schedule_is_gone_from_the_worker():
    """Source guard. The previous fix multiplied the full schedule by `strength`; it read as
    correct (num_steps intervals, right endpoints) and shipped, so a plain behavioural test
    would not have caught it. Pin the removal so a revert is loud."""
    src = (_MULTISTACK / "src" / "pipeline" / "flux2" / "stage3_denoise.py").read_text(
        encoding="utf-8")
    assert "img2img_schedule(num_steps, x.shape[1], strength)" in src
    assert "t * strength for t in" not in src, "the scaled-schedule bug is back"


def test_a_partial_run_actually_moves_further_than_the_old_shape():
    """The practical claim, as a number: at klein's default 4 steps — which `i2i_step_budget`
    leaves alone, since 4 already meets the floor — the new schedule covers a third of the
    range in two steps where the old one had barely moved and had to jump the rest.
    Guards against a future 'simplification' back toward uniform-in-shifted-space."""
    new = img2img_schedule(4, 4096, 0.5)
    old = [t * 0.5 for t in get_schedule(4, 4096)]          # the shipped-and-wrong version
    # after two of the four steps, how much of [strength, 0] has been traversed:
    assert (0.5 - new[2]) / 0.5 > 0.3
    assert (0.5 - old[2]) / 0.5 < 0.12
