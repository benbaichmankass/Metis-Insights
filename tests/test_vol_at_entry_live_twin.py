"""M21 E-2 round 4 — live vol-at-entry twin (donchian + pullback units).

Contract (mirrors the harness lever, tests/test_vol_at_entry_lever.py):
  * Undeclared (both pctl params 0.0/absent) ⇒ ``order_package`` behaviour
    byte-unchanged, and no ``vol_at_entry_pctl`` in meta.
  * Declared ⇒ a trigger bar whose trailing ATR percentile is in the
    gated tail is NON-actionable (standard ValueError path); a trigger
    with a mid-range percentile is unaffected and stamps
    ``meta["vol_at_entry_pctl"]`` for audit.
  * Fail-permissive: a window larger than the df NEVER skips (the
    percentile is undefined — a data hiccup must never strand a leg).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.units.strategies import htf_pullback_trend_2h as hp
from src.units.strategies import trend_donchian as td


def _df(rows):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return pd.DataFrame([{"timestamp": (start + timedelta(hours=i)).isoformat(),
                          "open": o, "high": h, "low": lo, "close": c,
                          "volume": 1.0}
                         for i, (o, h, lo, c) in enumerate(rows)])


# ---------------------------------------------------------------- donchian --

TD_CFG = {"symbol": "BTCUSDT", "donchian": 10, "atr_period": 5,
          "atr_stop_mult": 2.0, "trail_mult": 3.0, "min_confidence": 0.0,
          "timeframe": "1h"}


def _td_tape(spike=False):
    """Flat tape then a breakout on the latest bar. ``spike`` adds huge
    DOWNSIDE-range bars right before the breakout (close unchanged, so the
    upper Donchian edge holds) — the trigger bar's trailing ATR percentile
    lands in the hot tail."""
    rows = [(100.0, 100.5, 99.5, 100.0)] * 30
    if spike:
        rows += [(100.0, 100.5, 85.0, 100.0)] * 3
    rows += [(100.0, 103.0, 100.0, 102.5)]
    return _df(rows)


def test_td_undeclared_unchanged():
    pkg = td.order_package(dict(TD_CFG), _td_tape(spike=True))
    assert pkg["direction"] == "long"
    assert "vol_at_entry_pctl" not in pkg["meta"]


def test_td_hot_tail_non_actionable():
    df = _td_tape(spike=True)
    with pytest.raises(ValueError, match="vol-at-entry gate"):
        td.order_package({**TD_CFG, "vol_skip_above_pctl": 0.9,
                          "vol_pctl_window": 20}, df)


def test_td_midrange_unchanged_and_stamped():
    # No spike: flat-tape ATR ties rank at the top (pct rank of ties = max),
    # so gate ABOVE at 0.9 would fire — gate BELOW instead: percentile 1.0
    # is not < 0.1, the entry is untouched and the audit stamp rides meta.
    df = _td_tape(spike=False)
    base = td.order_package(dict(TD_CFG), df)
    gated = td.order_package({**TD_CFG, "vol_skip_below_pctl": 0.1,
                              "vol_pctl_window": 20}, df)
    assert gated["direction"] == base["direction"]
    assert gated["entry"] == base["entry"]
    assert gated["meta"]["vol_at_entry_pctl"] == pytest.approx(1.0)


def test_td_window_unfilled_never_skips():
    df = _td_tape(spike=True)
    pkg = td.order_package({**TD_CFG, "vol_skip_above_pctl": 0.9,
                            "vol_pctl_window": 500}, df)
    assert pkg["direction"] == "long"      # undefined pctl ⇒ gate off
    assert "vol_at_entry_pctl" not in pkg["meta"]


# ---------------------------------------------------------------- pullback --

HP_CFG = {"symbol": "ADAUSDT", "trend_lookback": 10, "pullback_lookback": 5,
          "pullback_frac": 0.5, "atr_period": 5, "atr_stop_mult": 2.0,
          "trail_mult": 2.0, "min_confidence": 0.0, "timeframe": "2h"}

# Ramp -> 2-bar pullback -> up-close trigger on the latest bar; a single
# widened bar (idx 22) right before the trigger puts the trigger bar's
# trailing ATR percentile at 0.9 within a 10-bar window (a bigger spike
# would break the Donchian-midline uptrend filter).
_HP_CLOSES = [100 + k * 1.4 for k in range(20)] + [127.0, 124.5, 123.2, 123.8]


def _hp_tape(spike=False):
    rows = []
    for k, c in enumerate(_HP_CLOSES):
        hi, lo = c + 0.5, c - 0.5
        if spike and k == 22:
            hi, lo = c + 3.0, c - 3.0
        rows.append((c, hi, lo, c))
    return _df(rows)


def test_hp_undeclared_unchanged():
    pkg = hp.order_package(dict(HP_CFG), _hp_tape(spike=True))
    assert pkg["direction"] == "long"
    assert "vol_at_entry_pctl" not in pkg["meta"]


def test_hp_hot_tail_non_actionable():
    with pytest.raises(ValueError, match="vol-at-entry gate"):
        hp.order_package({**HP_CFG, "vol_skip_above_pctl": 0.85,
                          "vol_pctl_window": 10}, _hp_tape(spike=True))


def test_hp_midrange_unchanged_and_stamped():
    df = _hp_tape(spike=False)
    base = hp.order_package(dict(HP_CFG), df)
    gated = hp.order_package({**HP_CFG, "vol_skip_below_pctl": 0.1,
                              "vol_pctl_window": 10}, df)
    assert gated["direction"] == base["direction"]
    assert gated["entry"] == base["entry"]
    assert "vol_at_entry_pctl" in gated["meta"]


def test_hp_window_unfilled_never_skips():
    pkg = hp.order_package({**HP_CFG, "vol_skip_above_pctl": 0.85,
                            "vol_pctl_window": 500}, _hp_tape(spike=True))
    assert pkg["direction"] == "long"
    assert "vol_at_entry_pctl" not in pkg["meta"]
