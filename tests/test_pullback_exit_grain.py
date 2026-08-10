"""Intrabar exit-evaluation grain — the three arms of the M20 cadence A/B.

`docs/live-exit-monitor-cadence-DESIGN.md` § 4.1. Live evaluates bot-side exit
levers roughly 21x per 1h bar; the harness evaluates them ONCE, at the bar's
close. Neither models the other, so "does more frequent evaluation help?" has
never been asked of the data. `--exit-grain leg|levers|full` asks it.

These tests pin the four properties that make the answer trustworthy:

  1. **Arm A is byte-identical.** The default path must not move at all, or
     every historical verdict silently changes meaning.
  2. **No lookahead.** `ext`/`mfe`/`trail` advance on the SUB-BAR clock. The
     obvious implementation extends them to the whole leg bar's extreme first,
     which lets a giveback rule checked at 14:05 compare against a peak set at
     14:47 — it would exit at the top with uncanny timing and MANUFACTURE an
     improvement. That is worse than not running the experiment.
  3. **Bar-counted params stay on the LEG clock.** `stale_exit_bars: 8` must
     mean 8 leg bars in every arm. Counting sub-bars would silently redefine
     every threshold by the sub-bar ratio and the cell would no longer be the
     config-exact one.
  4. **A missing/unusable finer frame REFUSES.** Degrading to bar-close
     evaluation would report the baseline under arm B's label.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, str(REPO_ROOT / relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


bp = _load("backtest_pullback_grain", "scripts/backtest_pullback.py")

# trend_lookback is deliberately WIDE (10). A pullback deep enough to put the
# close in the lower half of the recent 3-bar range also drops it below a
# 4-bar trend midline, so a narrow window admits no entry at all — and a
# fixture that takes no trades makes every assertion below vacuously true.
# `test_the_fixture_actually_trades` pins that.
BASE = dict(trend_lookback=10, pullback_lookback=3, pullback_frac=0.5,
            atr_period=3, atr_stop_mult=2.0, trail_mult=5.0, timeout_bars=50,
            cooldown_bars=0, timeframe="1h", symbol="BTCUSDT")


def _frame(rows, start="2024-01-01", freq="1h"):
    ts = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    return pd.DataFrame({
        "timestamp": ts,
        "open": [r[0] for r in rows], "high": [r[1] for r in rows],
        "low": [r[2] for r in rows], "close": [r[3] for r in rows],
    })


def _bar(c, rng=1.0):
    return (c, c + rng / 2, c - rng / 2, c)


def _entering(run_up=20, step=3.0):
    """A frame that ACTUALLY takes one long pullback entry, then runs.

    Rise 100->155 in 5s (establishes the trend and leaves the 10-bar midline
    far below), a two-bar shallow dip to 149 (puts the close in the lower half
    of the recent 3-bar range while still well above the midline), one up bar
    at 150 -> ENTRY, then a run. Verified to produce exactly one trade.
    """
    return ([_bar(100 + 5 * i) for i in range(12)]
            + [_bar(151), _bar(149), _bar(150)]
            + [_bar(150 + step * i) for i in range(1, run_up + 1)])


def _subbars(leg, per=4, spike_bar=None, spike_high=None, spike_at="last"):
    """Explode each leg bar into `per` sub-bars stamped inside it.

    When `spike_bar` is given, that leg bar's favourable extreme is placed in
    its LAST sub-bar (`spike_at="last"`) or its FIRST (`"first"`) — the two
    arrangements the lookahead probe needs.
    """
    rows, ts = [], []
    for i in range(len(leg)):
        t0 = leg["timestamp"].iloc[i]
        o, h, lo, c = (float(leg[k].iloc[i]) for k in ("open", "high", "low", "close"))
        for k in range(per):
            ts.append(t0 + pd.Timedelta(minutes=(60 // per) * k))
            if i != spike_bar:
                rows.append((o, h, lo, c))
            elif spike_at == "last":
                # The peak sub-bar CLOSES AT its own high. That is what makes
                # this a clean discriminator: a lookahead-free run only learns
                # the peak in this last sub-bar, and at that sub-bar's close it
                # has surrendered NOTHING, so no giveback can fire. A run that
                # extends `ext` to the whole leg bar's high before walking the
                # sub-bars sees the peak at sub-bar 1 — whose close is 40 below
                # it — and fires. (An earlier version closed this sub-bar at
                # `c`, which let a LEGITIMATE same-sub-bar high->close giveback
                # fire and made the probe indistinguishable from the bug.)
                rows.append((c, spike_high, c - 0.1, spike_high) if k == per - 1
                            else (c, c + 0.1, c - 0.1, c))
            else:
                rows.append((c, spike_high, c - 0.1, spike_high - 0.5) if k == 0
                            else (c, c + 0.1, c - 0.1, c))
    return pd.DataFrame({"timestamp": ts,
                         "open": [r[0] for r in rows], "high": [r[1] for r in rows],
                         "low": [r[2] for r in rows], "close": [r[3] for r in rows]})


# --------------------------------------------------------------------------
# 1. Arm A is byte-identical
# --------------------------------------------------------------------------

def test_arm_a_matches_the_grain_free_call_exactly():
    """`exit_grain='leg'` must equal the default with no sub-bar frame at all.

    The default is what every historical sweep verdict was produced at. If the
    new code path moved it even slightly, every recorded delta would silently
    be measured against a different baseline.
    """
    df = _frame(_entering())
    a = bp.run_backtest(df.copy(), **BASE, stale_exit_bars=8)
    b = bp.run_backtest(df.copy(), **BASE, stale_exit_bars=8, exit_grain="leg")
    drop = {"exit_grain", "subbar_coverage", "subbar_missing_bars"}
    assert {k: v for k, v in a.items() if k not in drop} == \
           {k: v for k, v in b.items() if k not in drop}


def test_arm_a_reports_missing_bars_as_none_not_zero():
    """`0` would claim "we looked and nothing was missing"; nothing was looked for."""
    out = bp.run_backtest(_frame(_entering()), **BASE, exit_grain="leg")
    assert out["subbar_missing_bars"] is None
    assert out["subbar_coverage"] is None


# --------------------------------------------------------------------------
# 2. NO LOOKAHEAD — the property that decides whether the experiment is real
# --------------------------------------------------------------------------

def test_the_fixture_actually_trades():
    """Every assertion below is vacuous if the frame takes no trade.

    The first version of this file used a plain rising staircase and a narrow
    trend window; it produced ZERO entries, so the lookahead probe "passed" by
    finding no giveback exits in a run with no trades at all. That is the
    unasserted-denominator failure, committed inside the very test written to
    prevent it. This pins the denominator.
    """
    out = bp.run_backtest(_frame(_entering()), **BASE)
    assert out["total_trades"] >= 1, "fixture takes no trades — nothing to grade"


def _late_peak(spike_at: str):
    """A leg bar whose favourable extreme prints LAST (or FIRST), + a giveback.

    With the peak LAST, every earlier sub-bar close sits flat and has no R to
    surrender — so a giveback exit on that bar could only come from reading a
    future extreme. With the peak FIRST, the later closes genuinely give R back
    and a correct implementation MUST fire.
    """
    leg = _frame(_entering())
    spike_i = len(leg) - 2
    high = float(leg.loc[spike_i, "close"]) + 40.0
    leg.loc[spike_i, "high"] = high
    sub = _subbars(leg, per=4, spike_bar=spike_i, spike_high=high, spike_at=spike_at)
    return bp.run_backtest(leg, **BASE, giveback_min_mfe_r=1.0, giveback_r=0.5,
                           subbar_df=sub, exit_grain="levers")


def test_levers_arm_cannot_exit_on_a_peak_that_has_not_printed_yet():
    out = _late_peak("last")
    assert out["total_trades"] >= 1
    assert out["by_outcome"].get("giveback_stop", 0) == 0, (
        "a giveback fired on a bar whose peak prints in its LAST sub-bar — "
        "the lever read a future extreme (lookahead)")


def test_the_lookahead_probe_can_actually_fire():
    """The negative above is worthless unless the same shape CAN produce one."""
    out = _late_peak("first")
    assert out["total_trades"] >= 1
    assert out["by_outcome"].get("giveback_stop", 0) >= 1, (
        "the probe never fires even with the peak FIRST — it cannot "
        "distinguish a lookahead-free implementation from a broken one")


# --------------------------------------------------------------------------
# 3. Bar-counted params stay on the LEG clock
# --------------------------------------------------------------------------

def test_stale_exit_counts_leg_bars_not_subbars():
    """With 4 sub-bars per leg bar, a sub-bar clock would fire ~4x sooner.

    Asserted on the exit INDEX distance from entry: on the leg clock the
    earliest a `stale_exit_bars=6` exit can land is 6 leg bars after entry.
    A drifting-to-sub-bars implementation would land it at 2 or less.
    """
    # A flat run after entry so the stale rule (open_r < 0) is what fires.
    leg = _frame(_entering(run_up=20, step=0.0))
    sub = _subbars(leg, per=4)
    out = bp.run_backtest(leg, **BASE, stale_exit_bars=6, stale_exit_below_r=0.0,
                          subbar_df=sub, exit_grain="levers")
    assert out["total_trades"] >= 1
    held = out.get("mean_bars_held")
    assert held is not None and held >= 6, (
        f"mean bars held {held} < stale_exit_bars=6 — the bar counter is "
        "running on the sub-bar clock, which silently redefines the threshold")


# --------------------------------------------------------------------------
# 4. An unusable finer frame REFUSES rather than degrading
# --------------------------------------------------------------------------

def test_intrabar_arms_require_a_subbar_frame():
    with pytest.raises(ValueError, match="requires --subbar-data"):
        bp.run_backtest(_frame(_entering()), **BASE, exit_grain="levers")


def test_a_frame_that_is_not_finer_is_refused():
    """Resampling a coarser frame up would fabricate sub-bars that never traded."""
    leg = _frame(_entering())
    with pytest.raises(ValueError, match="not finer"):
        bp.run_backtest(leg.copy(), **BASE, subbar_df=leg.copy(),
                        exit_grain="levers")


def test_unknown_grain_is_rejected():
    with pytest.raises(ValueError, match="unknown --exit-grain"):
        bp.run_backtest(_frame(_entering()), **BASE, exit_grain="intrabar")


def test_short_trades_run_through_every_arm():
    """The short branch must execute — it is a separate code path.

    Every other test in this file takes a LONG. The first version of the
    ratchet renamed its parameter on the long branch only, leaving `l`
    undefined on the short branch: a short trade in arm B or C would have
    raised NameError at runtime, and no test here would have noticed.
    `ruff` caught it as F821, which is luck, not coverage. This is the
    coverage.
    """
    # Mirror of `_entering()`: a DOWNtrend, a shallow bounce, then a down bar.
    rows = ([_bar(200 - 5 * i) for i in range(12)]
            + [_bar(149), _bar(151), _bar(150)]
            + [_bar(150 - 3 * i) for i in range(1, 21)])
    leg = _frame(rows)
    sub = _subbars(leg, per=4)
    outs = {}
    for grain in ("leg", "levers", "full"):
        kw = {} if grain == "leg" else {"subbar_df": sub}
        outs[grain] = bp.run_backtest(leg.copy(), **BASE, stale_exit_bars=6,
                                      exit_grain=grain, **kw)
    assert outs["leg"]["trades_short"] >= 1, (
        "fixture takes no SHORT trade — the short path is still unexercised")
    for grain in ("levers", "full"):
        assert outs[grain]["total_trades"] >= 1
