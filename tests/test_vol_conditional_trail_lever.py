"""M20-X — vol-conditional trailing-stop harness lever contract.

Contract (mirrors tests/test_vol_at_entry_lever.py):
  * Default (``trail_vol_tight_mult`` 0.0) ⇒ byte-identical run — the
    conditional trail mult is never consulted (and the percentile series
    is not computed unless the entry lever needs it).
  * When armed (a pctl bound + a positive tight mult), a managed bar whose
    trailing ATR percentile is in the gated tail TIGHTENS the effective
    trail mult — a strictly-not-looser stop, so exits can only come
    earlier, never later, than the same run at the base mult.
  * The percentile is TRAILING (causal): NaN until ``vol_pctl_window``
    bars exist ⇒ the lever is inert on those bars (fail-permissive).
  * Entries are never touched — the lever gates the EXIT trail only, so a
    vol-trail run and the baseline enter on exactly the same bars.

MIGRATED 2026-08-09 (``BL-20260808-RESEARCH-TREND-ENGINE-RETIREMENT-BLOCKED-BY-
TEST-COUPLING``) from the retired ``scripts/research/backtest_trend.py`` onto
the live-faithful ``scripts/backtest_trend.py``. **Disposition: (a) PORTED —
this is the engine-SPECIFIC file, and every expected number was re-derived.**

This is the only one of the five migrated files whose assertions read the EXIT
(``exit_time`` / ``r_multiple``), which is precisely the axis the two engines
disagree on — so a blind repoint here was the real risk the backlog row warned
about. Measured on this file's own ``_trend_runup_then_chop()`` tape (42 bars,
donchian 10 / atr_period 5 / atr_stop_mult 2.0 / trail_mult 6.0 /
timeout_bars 40 / cooldown_bars 0), the trade SET differs, not just the numbers:

===========================  =====================================  =====================================
run                          research engine (retired)              live-faithful engine (now)
===========================  =====================================  =====================================
base                         1 trade: long 06:00→10:00 r −1.0000    2 trades: long 06:00→09:00 r −0.3214,
                                                                    short 10:00→18:00 timeout r +0.4386
above=0.6 win=20 tight=2.0   2 trades: long 06:00→07:00 r +0.6786,  2 trades: long 06:00→**08:00**
                             long 07:00→09:00 r −1.0000             r **+1.6786**, short unchanged
===========================  =====================================  =====================================

The research engine has no post-exit cooldown, so its tightened trail produced a
same-bar RE-ENTRY (the second long at 07:00) that the live-faithful engine never
takes; conversely the live-faithful engine takes a short on the give-back leg
that the research engine's flip exit suppressed. The *contract* (a tightened
trail exits no later and, on a give-back tape, banks a better R) survives; the
numbers do not, and are re-derived above.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from backtest_pullback import run_backtest as pullback_backtest  # noqa: E402

from tests.trend_harness_engine import trend_trades as trend_backtest  # noqa: E402


def _df(rows):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return pd.DataFrame([{"timestamp": start + timedelta(hours=i),
                          "open": o, "high": h, "low": lo, "close": c,
                          "volume": 1.0}
                         for i, (o, h, lo, c) in enumerate(rows)])


def _trend_runup_then_chop(n_flat=30):
    """Flat tape → long breakout → a strong run-up that peaks → then a
    high-ATR chop tail. The base wide trail rides the retrace; a trail that
    tightens in the hot-vol tail cuts sooner. Enough tail bars that the
    trade closes before tape end either way."""
    rows = [(100.0, 100.5, 99.5, 100.0)] * n_flat
    rows += [(100.0, 103.0, 100.0, 102.5)]                 # breakout trigger
    rows += [(102.5, 110.0, 102.0, 109.0)]                 # strong run-up (peak)
    # hot-vol give-back tail: big ranges, price grinding back down
    rows += [(109.0, 110.0, 104.0, 105.0),
             (105.0, 106.5, 100.0, 101.0),
             (101.0, 102.0, 97.0, 98.0),
             (98.0, 99.0, 94.0, 95.0),
             (95.0, 96.0, 92.0, 93.0)]
    rows += [(93.0, 93.5, 92.5, 93.0)] * 6                 # settle tail
    return _df(rows)


# donchian / atr_period / atr_stop_mult / cooldown come from
# tests.trend_harness_engine.TAPE_DEFAULTS; the wide trail and the long timeout
# are this tape's own (the base trail must ride the give-back to be a baseline).
TREND_KW = dict(trail_mult=6.0, timeout_bars=40)


def test_trend_default_off_byte_identical():
    df = _trend_runup_then_chop()
    base = trend_backtest(df, **TREND_KW)
    off = trend_backtest(df, **TREND_KW, trail_vol_above_pctl=0.9,
                         trail_vol_below_pctl=0.1, trail_vol_tight_mult=0.0)
    assert base, "tape must produce a baseline trade (guards a vacuous [] == [])"
    assert [(t.entry_time, t.exit_time, t.r_multiple) for t in base] == \
           [(t.entry_time, t.exit_time, t.r_multiple) for t in off]


def test_trend_hot_tail_tightens_exit_not_later():
    df = _trend_runup_then_chop()
    base = trend_backtest(df, **TREND_KW)
    gated = trend_backtest(df, **TREND_KW, trail_vol_above_pctl=0.6,
                           vol_pctl_window=20, trail_vol_tight_mult=2.0)
    assert base and gated
    # Entries are untouched — the lever is exit-side only. Asserted over the
    # WHOLE list, not just [0]: on this engine the tape also produces a short
    # on the give-back leg, and the lever must not move it either.
    assert [t.entry_time for t in base] == [t.entry_time for t in gated]
    # A tightened trail can only exit at-or-before the base trail.
    assert gated[0].exit_time <= base[0].exit_time
    # And on this give-back tape the tighter stop banks a better R.
    assert gated[0].r_multiple >= base[0].r_multiple
    # NON-VACUITY. The two inequalities above are both satisfied by "the lever
    # did nothing at all", which is the failure mode that shipped as
    # BL-20260808-INERT-CONDITIONAL-SHIPPED-AS-A-BEHAVIOUR-CHANGE: a branch
    # whose output is identical either way passes its test while proving
    # nothing. Pin the STRICT change, and the re-derived values behind it
    # (population in the module docstring).
    assert gated[0].exit_time < base[0].exit_time
    assert gated[0].r_multiple > base[0].r_multiple
    assert len(base) == 2
    assert base[0].r_multiple == -0.3214 and gated[0].r_multiple == 1.6786
    # the lever fires on the long only; the trailing short is byte-unchanged
    assert base[1].__dict__ == gated[1].__dict__


def test_trend_window_unfilled_inert():
    df = _trend_runup_then_chop()
    base = trend_backtest(df, **TREND_KW)
    gated = trend_backtest(df, **TREND_KW, trail_vol_above_pctl=0.6,
                           vol_pctl_window=500, trail_vol_tight_mult=2.0)
    # 500-bar window never fills on this short tape ⇒ lever inert ⇒ identical.
    assert base, "tape must produce a baseline trade (guards a vacuous [] == [])"
    assert [(t.entry_time, t.exit_time, t.r_multiple) for t in base] == \
           [(t.entry_time, t.exit_time, t.r_multiple) for t in gated]


# ---------------------------------------------------------------- pullback --

HP_KW = dict(trend_lookback=10, pullback_lookback=5, pullback_frac=0.5,
             atr_period=5, atr_stop_mult=2.0, trail_mult=6.0,
             timeout_bars=40, cooldown_bars=1, timeframe="2h", symbol="ADAUSDT")


def _hp_runup_then_chop():
    closes = [100 + k * 1.4 for k in range(14)]          # uptrend
    closes += [118.0, 116.0, 118.5]                       # pullback → up trigger
    closes += [126.0]                                     # run-up peak
    closes += [122.0, 117.0, 112.0, 108.0, 105.0]         # give-back
    closes += [104.5] * 8                                 # settle tail
    rows = []
    for k, c in enumerate(closes):
        wide = 3.0 if 17 <= k <= 22 else 0.5              # hot-vol give-back band
        rows.append((c, c + wide, c - wide, c))
    return _df(rows)


def test_pullback_default_off_byte_identical():
    df = _hp_runup_then_chop()
    base = pullback_backtest(df, **HP_KW)
    off = pullback_backtest(df, **HP_KW, trail_vol_above_pctl=0.9,
                            trail_vol_below_pctl=0.1, trail_vol_tight_mult=0.0)
    assert base["total_trades"] == off["total_trades"]
    assert base["net_total_r"] == off["net_total_r"]


def test_pullback_window_unfilled_inert():
    df = _hp_runup_then_chop()
    base = pullback_backtest(df, **HP_KW)
    gated = pullback_backtest(df, **HP_KW, trail_vol_above_pctl=0.6,
                              vol_pctl_window=500, trail_vol_tight_mult=2.0)
    assert base["total_trades"] == gated["total_trades"]
    assert base["net_total_r"] == gated["net_total_r"]
