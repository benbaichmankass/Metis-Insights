"""The ONE definition of the chandelier trail-lever firing rule.

The sibling of `src/runtime/execution_costs.py` ("the ONE shared cost model") for
the *exit-lever* side, and it exists for the same reason that one does: this rule
had already been written **twice**.

MEASURED 2026-08-17, before this module existed:

* `scripts/backtest_trend.py::_effective_trail_mult` — the full rule (trail-decay
  and vol-conditional, composing by minimum), reused by
  `scripts/research/m20_trail_attribution.py` via a dynamic module load;
* `scripts/backtest_pullback.py` — its **own inline** vol-tail test
  (`fired = ((trail_vol_above_pctl > 0.0 and float(vp) > trail_vol_above_pctl) ...)`).

Two derivations of one bound is the defect `tests/test_fold_reachability_is_derived.py`
exists to pin, and the M20 coverage roll-up hit the same class on 2026-08-17 (a
re-derived fold bound beside the one `fold_reachability` computes). A THIRD copy
was about to be added for `scripts/backtest_squeeze.py`, which is what prompted
this move: the squeeze harness had `--trail-mult` but no `--trail-vol-*` at all,
so `vol_trail` was unreachable there and every such matrix cell read
`blocked:no_harness_levers`.

**This is a verbatim MOVE, not a rewrite.** The body is byte-for-byte the
function that produced the recorded corpus, verified against three armed
baselines on `data/backtest_candles.csv` (levers-off, vol-armed, decay-armed —
137 / 153 / 144 trades, three distinct md5s, so the check can actually detect a
change). `backtest_trend.py` keeps a module-level alias so callers that resolve
`_effective_trail_mult` by name — `m20_trail_attribution.py` does exactly that —
keep working.

Do NOT re-inline this rule into a harness. A lever whose firing condition is
stated in two places is free to drift, and a drifted exit lever silently
re-grades every cell measured against it.
"""
from __future__ import annotations

import pandas as pd


def effective_trail_mult(base: float, peak_r: float, bars_since_peak: int,
                         decay_on: bool, decay_arm_r: float,
                         decay_stall_bars: int, decay_tight_mult: float,
                         vol_on: bool, atr_pctl, j: int,
                         vol_above_pctl: float, vol_below_pctl: float,
                         vol_tight_mult: float,
                         adx_on: bool = False, adx=None,
                         adx_below: float = 0.0, adx_falling_bars: int = 0,
                         adx_tight_mult: float = 0.0) -> float:
    """The chandelier trail mult in force on ONE managed bar.

    Two independent tighteners, both **no-ops at their defaults** so the caller's
    arithmetic is byte-identical to the pre-lever engine when neither is declared:

    * **M20 P4.1 trail-decay** — tighten once the move shows exhaustion, either
      R-armed (``peak_r >= decay_arm_r``) or stall-armed (``decay_stall_bars`` or
      more bars since the last new favourable extreme). A new peak re-loosens the
      MULT; the caller's price ratchet never loosens the STOP.
    * **M20-X vol-conditional trail** — tighten on a bar whose trailing ATR
      percentile sits in a gated tail. Conditional, not a ratchet; an undefined
      percentile (window unfilled) leaves it inert.

    * **Momentum trail** (``adx_on``; MOMENTUM-TRAIL-LEVER, memo
      docs/research/cross-signal-exits-2026-10-07.md § 4.1) — tighten on a bar
      whose ADX reads fading momentum (`momentum_trail_fired`). Keyed to
      MOMENTUM, not to price path.

    When more than one fires the TIGHTEST wins, matching the research harness
    this was ported from — the levers compose by minimum, never by sum. The
    momentum arguments default to off, so a caller that does not pass them gets
    the pre-lever arithmetic exactly.
    """
    tm = base
    if decay_on:
        armed = ((decay_arm_r > 0.0 and peak_r >= decay_arm_r)
                 or (decay_stall_bars > 0 and bars_since_peak >= decay_stall_bars))
        if armed:
            tm = decay_tight_mult
    if vol_on and atr_pctl is not None:
        vp = atr_pctl.iloc[j]
        if not pd.isna(vp):
            if ((vol_above_pctl > 0.0 and float(vp) > vol_above_pctl)
                    or (vol_below_pctl > 0.0 and float(vp) < vol_below_pctl)):
                tm = min(tm, vol_tight_mult)
    if adx_on and momentum_trail_fired(adx, j, adx_below, adx_falling_bars):
        tm = min(tm, adx_tight_mult)
    return tm


def vol_trail_armed(trail_vol_tight_mult: float,
                    trail_vol_above_pctl: float,
                    trail_vol_below_pctl: float) -> bool:
    """Is the vol-conditional trail declared at all?

    Hoisted beside the rule it gates because the two harnesses that already had
    this lever ALSO each re-stated this predicate. A tight mult with no tail, or
    a tail with no tight mult, is not a declaration — it is a half-configured
    lever, and reading it as armed would tighten on a bound nobody set.
    """
    return (trail_vol_tight_mult > 0.0
            and (trail_vol_above_pctl > 0.0 or trail_vol_below_pctl > 0.0))


def momentum_trail_armed(adx_below: float, adx_falling_bars: int,
                         adx_tight_mult: float) -> bool:
    """Is the ADX-conditioned trail declared at all?

    A tight mult with no condition, or a condition with no tight mult, is a
    half-configured lever and reads as NOT armed (same contract as
    `vol_trail_armed`) — so a bare ``--trail-adx-tight-mult`` can never tighten
    every bar unannounced.
    """
    return (adx_tight_mult > 0.0
            and (adx_below > 0.0 or adx_falling_bars > 0))


def momentum_trail_fired(adx, j: int, adx_below: float,
                         adx_falling_bars: int) -> bool:
    """Does the momentum-trail condition hold on managed bar ``j``?

    ``adx`` is a per-bar ADX series (positional). Both declared conditions must
    hold: ``ADX_j < adx_below`` (when ``adx_below > 0``) AND
    ``ADX_j < ADX_{j-k}`` (when ``adx_falling_bars = k > 0``) — memo § 4.1, so
    ``adx20_k3`` reads "ADX under 20 *and* lower than 3 bars ago". With only one
    declared, that one alone decides. NaN anywhere it is read (warm-up) is inert
    — fail-permissive, as `vol_trail`.
    """
    if adx is None:
        return False
    a = adx.iloc[j]
    if pd.isna(a):
        return False
    if adx_below > 0.0 and not float(a) < adx_below:
        return False
    if adx_falling_bars > 0:
        if j - adx_falling_bars < 0:
            return False
        ref = adx.iloc[j - adx_falling_bars]
        if pd.isna(ref) or not float(a) < float(ref):
            return False
    return adx_below > 0.0 or adx_falling_bars > 0


def momentum_stale_armed(stale_bars: int, stale_adx_below: float) -> bool:
    """Is the momentum-keyed time stop declared? Needs BOTH knobs (a bar count
    with no ADX bound, or the reverse, is half-configured and reads off)."""
    return stale_bars > 0 and stale_adx_below > 0.0


def momentum_stale_fires(adx, j: int, entry_i: int, stale_bars: int,
                         stale_adx_below: float) -> bool:
    """Should the momentum-keyed time stop close the trade at bar ``j``'s CLOSE?

    ``(j - entry_i) >= N`` AND ``ADX_j < ADX_entry`` AND ``ADX_j < A`` (memo
    § 4.2). Open R is deliberately NOT consulted — that is the whole difference
    from `stale_stop`. NaN at either point is inert.
    """
    if adx is None or (j - entry_i) < stale_bars:
        return False
    a, a0 = adx.iloc[j], adx.iloc[entry_i]
    if pd.isna(a) or pd.isna(a0):
        return False
    return float(a) < float(a0) and float(a) < stale_adx_below


def momentum_flag_errors(trail_mult: float, adx_below: float,
                         adx_falling_bars: int, adx_tight_mult: float,
                         stale_bars: int, stale_adx_below: float) -> list:
    """Refusals for half-configured / inert momentum-lever combinations.

    A lever that is declared but cannot act reports a zero delta that is NOT a
    measurement of it (the `rr_floor` precedent), so the CLIs refuse rather than
    run it. Returns human-readable messages; empty = fine.
    """
    errs = []
    cond = adx_below > 0.0 or adx_falling_bars > 0
    if (adx_tight_mult > 0.0) != cond:
        errs.append("--trail-adx-tight-mult needs --trail-adx-below and/or "
                    "--trail-adx-falling-bars (and vice versa): a half-declared "
                    "momentum_trail is inert")
    if adx_tight_mult > 0.0 and adx_tight_mult >= trail_mult:
        errs.append(f"--trail-adx-tight-mult ({adx_tight_mult}) must be < "
                    f"--trail-mult ({trail_mult}): the lever can only tighten")
    if (stale_bars > 0) != (stale_adx_below > 0.0):
        errs.append("--momentum-stale-bars and --momentum-stale-adx-below must "
                    "be declared together: a half-declared momentum_stale is inert")
    return errs
