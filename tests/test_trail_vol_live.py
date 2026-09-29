"""M20-X — LIVE vol-conditional trail lever contract
(``src/runtime/trail_vol.py::resolve_vol_trail_mult``).

Parity with the harness lever (tests/test_vol_conditional_trail_lever.py):
  * Default / undeclared ⇒ base mult returned unchanged (no config read past
    the declared-gate; byte-identical monitor behaviour).
  * Declared + the CURRENT closed bar's trailing-``vol_pctl_window`` ATR
    percentile in the gated tail ⇒ ``min(base_mult, tight_mult)`` (a
    strictly-not-looser mult).
  * Window unfilled (fewer than ``vol_pctl_window`` bars) ⇒ base mult
    (fail-permissive), matching the harness ``min_periods=window`` NaN.
  * The percentile uses the SAME SMA-of-TR ATR the live unit computes, so a
    cold-tail bar fires ``below`` and a hot-tail bar fires ``above`` only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from src.runtime.trail_vol import resolve_vol_trail_mult

WIN = 200


def _df(ranges):
    """Bars with a constant close=100 so True Range == the per-bar `range`
    exactly (high=100+r/2, low=100-r/2, close=100 ⇒ TR=r), giving an ATR that
    is the rolling mean of `ranges`. Lets a test place the LAST bar's ATR
    precisely in the cold or hot tail of the trailing window."""
    rows = [{"high": 100 + r / 2, "low": 100 - r / 2, "close": 100.0}
            for r in ranges]
    return pd.DataFrame(rows)


# A long calm tail after a high-vol body ⇒ the last bar's ATR sits in the
# bottom decile of the trailing 200-bar window.
_COLD_LAST = _df([12.0] * 230 + [0.5] * 30)
# The mirror: a high-vol tail after a calm body ⇒ last ATR in the top decile.
_HOT_LAST = _df([0.5] * 230 + [12.0] * 30)

_COLD_CFG = {"trail_vol_below_pctl": 0.10, "trail_vol_tight_mult": 2.5,
             "vol_pctl_window": WIN, "atr_period": 14}
_HOT_CFG = {"trail_vol_above_pctl": 0.90, "trail_vol_tight_mult": 2.5,
            "vol_pctl_window": WIN, "atr_period": 14}


def test_undeclared_returns_base_unchanged():
    # No tight_mult ⇒ lever never engages, base returned byte-identical.
    assert resolve_vol_trail_mult({}, {}, _COLD_LAST, 5.0, "long") == 5.0
    # tight set but no pctl bound ⇒ still undeclared.
    assert resolve_vol_trail_mult(
        {}, {"trail_vol_tight_mult": 2.5}, _COLD_LAST, 5.0, "long") == 5.0


def test_cold_tail_fires_and_tightens():
    out = resolve_vol_trail_mult({}, _COLD_CFG, _COLD_LAST, 5.0, "long")
    assert out == 2.5  # min(5.0, 2.5)


def test_cold_config_does_not_fire_on_hot_bar():
    # below-only config, last bar in the HOT tail ⇒ no fire ⇒ base.
    out = resolve_vol_trail_mult({}, _COLD_CFG, _HOT_LAST, 5.0, "long")
    assert out == 5.0


def test_hot_tail_fires_for_above_config():
    out = resolve_vol_trail_mult({}, _HOT_CFG, _HOT_LAST, 5.0, "short")
    assert out == 2.5


def test_window_unfilled_is_inert():
    short = _df([12.0] * 50 + [0.5] * 30)  # < 200 bars
    assert resolve_vol_trail_mult({}, _COLD_CFG, short, 5.0, "long") == 5.0


def test_tight_never_loosens_a_smaller_base():
    # If the (decay-composed) base is already tighter than tight_mult, the
    # min() keeps the tighter base — the lever never loosens a stop.
    out = resolve_vol_trail_mult({}, _COLD_CFG, _COLD_LAST, 2.0, "long")
    assert out == 2.0


def test_meta_overrides_cfg():
    # meta wins over cfg when both present (the order_package thread path).
    out = resolve_vol_trail_mult(
        {"trail_vol_below_pctl": 0.10, "trail_vol_tight_mult": 2.5,
         "vol_pctl_window": WIN, "atr_period": 14},
        {"trail_vol_tight_mult": 0.0}, _COLD_LAST, 5.0, "long")
    assert out == 2.5


def _df_with_timestamps(ranges, *, tf: str, forming_last: bool):
    """Like ``_df`` above but with a real ``timestamp`` column so
    ``drop_forming_bar`` can evaluate closedness. The bars step back from
    NOW by one bar period each; the last bar's open is placed a few seconds
    ago (still forming) or safely more than one bar period ago (closed)."""
    tf_s = {"1h": 3600, "2h": 7200}[tf]
    now = datetime.now(timezone.utc)
    last_open = now - timedelta(seconds=5) if forming_last else now - timedelta(seconds=tf_s * 2)
    opens = [last_open - timedelta(seconds=tf_s * (len(ranges) - 1 - i))
             for i in range(len(ranges))]
    rows = [{"timestamp": o, "high": 100 + r / 2, "low": 100 - r / 2, "close": 100.0}
            for o, r in zip(opens, ranges)]
    return pd.DataFrame(rows)


def test_forming_last_bar_is_dropped_before_scoring():
    # 200 baseline (mid-range) closed bars, then the TRUE last-closed bar
    # (also mid-range — nowhere near the hot tail), then a still-forming bar
    # with an extreme range that WOULD rank in the top decile if it were
    # scored. If the forming bar leaked into the percentile calc (the bug
    # this test guards), the lever fires; correctly dropped, it must not.
    ranges = [5.0] * 200 + [5.0] + [50.0]  # closed(-2)=mid, forming(-1)=extreme
    df = _df_with_timestamps(ranges, tf="2h", forming_last=True)
    cfg = {"trail_vol_above_pctl": 0.90, "trail_vol_tight_mult": 2.5,
           "vol_pctl_window": WIN, "atr_period": 14, "timeframe": "2h"}
    out = resolve_vol_trail_mult({}, cfg, df, 5.0, "long")
    assert out == 5.0  # forming bar dropped -> scores the mid-range closed bar -> no fire


def test_closed_last_bar_is_not_dropped():
    # Sibling control: an ALREADY-closed last bar with the same extreme
    # range DOES fire — proves the guard trims only a genuinely forming bar,
    # not the last row unconditionally.
    ranges = [5.0] * 200 + [50.0]
    df = _df_with_timestamps(ranges, tf="2h", forming_last=False)
    cfg = {"trail_vol_above_pctl": 0.90, "trail_vol_tight_mult": 2.5,
           "vol_pctl_window": WIN, "atr_period": 14, "timeframe": "2h"}
    out = resolve_vol_trail_mult({}, cfg, df, 5.0, "long")
    assert out == 2.5
