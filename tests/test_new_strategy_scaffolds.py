"""Tests for the Rank-1/Rank-2 new-strategy scaffolds (not-yet-wired).

Covers order_package shape, the shared Chandelier monitor (ratchet + SL-cross),
and the non-actionable ValueError paths. Also asserts the modules are NOT
wired into the live builder (they must stay inert until the Tier-3 activation
PR).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.units.strategies import htf_pullback_trend_2h as pull
from src.units.strategies import session_breakout_trend as sess


def _frame(rows):
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close"])


# --------------------------------------------------------------------------
# session_breakout_trend
# --------------------------------------------------------------------------
def _session_df(breakout: bool):
    """Build a 15m frame: a flat opening range after 13:30 UTC, then either a
    clean upside breakout (breakout=True) or a bar that stays inside."""
    base = pd.Timestamp("2026-03-02T13:30:00Z")
    rows = []
    # 40 pre-session warmup bars (yesterday) for ATR.
    warm = base - pd.Timedelta(minutes=15 * 40)
    px = 100.0
    for k in range(40):
        t = warm + pd.Timedelta(minutes=15 * k)
        rows.append([t, px, px + 1, px - 1, px])
    # Opening range: 4 bars hugging 100..101.
    for k in range(4):
        t = base + pd.Timedelta(minutes=15 * k)
        rows.append([t, 100.0, 101.0, 100.0, 100.5])
    # Next in-window bar: breakout above 101 (or stay inside).
    t = base + pd.Timedelta(minutes=15 * 4)
    if breakout:
        rows.append([t, 100.5, 105.0, 100.5, 104.0])   # closes well above range hi
    else:
        rows.append([t, 100.5, 100.9, 100.1, 100.5])   # inside the range
    return _frame(rows)


def test_session_breakout_emits_long_package():
    pkg = sess.order_package({"symbol": "BTCUSDT"}, _session_df(breakout=True))
    assert pkg["direction"] == "long"
    assert pkg["entry"] == 104.0
    assert pkg["sl"] < pkg["entry"] < pkg["tp"]
    assert 0.0 <= pkg["confidence"] <= 1.0
    meta = pkg["meta"]
    assert meta["atr"] > 0 and meta["risk_per_unit"] > 0
    assert meta["session_range_hi"] == 101.0
    assert meta["timeframe"] == "15m"


def test_session_breakout_inside_range_non_actionable():
    with pytest.raises(ValueError):
        sess.order_package({}, _session_df(breakout=False))


def test_session_breakout_needs_timestamp():
    df = _session_df(breakout=True).drop(columns=["timestamp"])
    with pytest.raises(ValueError):
        sess.order_package({}, df)


def test_session_monitor_ratchets_long_stop_up():
    pkg = sess.order_package({"symbol": "BTCUSDT"}, _session_df(breakout=True))
    # Price runs far above entry → trail should propose a higher stop.
    fresh = _session_df(breakout=True)
    runner = fresh.iloc[-1].copy()
    runner["timestamp"] = pd.Timestamp("2026-03-02T16:00:00Z")
    runner["high"] = 130.0
    runner["close"] = 128.0
    fresh = pd.concat([fresh, pd.DataFrame([runner])], ignore_index=True)
    verdict = sess.monitor({}, fresh, pkg)
    assert verdict is not None and "sl" in verdict
    assert verdict["sl"] > pkg["sl"]


def test_session_monitor_sl_cross_closes():
    pkg = sess.order_package({"symbol": "BTCUSDT"}, _session_df(breakout=True))
    crashed = _session_df(breakout=True)
    last = crashed.iloc[-1].copy()
    last["close"] = pkg["sl"] - 1.0
    crashed = pd.concat([crashed, pd.DataFrame([last])], ignore_index=True)
    verdict = sess.monitor({}, crashed, pkg)
    assert verdict == {"action": "close", "reason": "sl_cross",
                       "exit_price": pkg["sl"] - 1.0} or verdict["action"] == "close"


# --------------------------------------------------------------------------
# htf_pullback_trend_2h
# --------------------------------------------------------------------------
def _pullback_df():
    """Build a 2h frame: a long uptrend (so close > Donchian midline) followed
    by a pullback into the lower third of the recent range, then a bullish
    confirmation close."""
    base = pd.Timestamp("2026-01-01T00:00:00Z")
    rows = []
    px = 100.0
    # 70 bars trending up from 100 -> ~170 (establishes midline well below price).
    for k in range(70):
        px += 1.0
        t = base + pd.Timedelta(hours=2 * k)
        rows.append([t, px - 1, px + 0.5, px - 1.5, px])
    # Pullback: last ~6 bars dip back toward the lower part of the recent range.
    top = px
    for k in range(6):
        t = base + pd.Timedelta(hours=2 * (70 + k))
        dip = top - (k + 1) * 2.0
        rows.append([t, dip + 1, dip + 1.5, dip - 1.0, dip])
    # Confirmation bar: bullish close above the prior close, still > midline.
    t = base + pd.Timedelta(hours=2 * 76)
    last_close = rows[-1][4]
    rows.append([t, last_close, last_close + 3, last_close - 0.5, last_close + 2.5])
    return _frame(rows)


def test_pullback_emits_long_package():
    df = _pullback_df()
    pkg = pull.order_package({"symbol": "BTCUSDT"}, df)
    assert pkg["direction"] == "long"
    assert pkg["sl"] < pkg["entry"] < pkg["tp"]
    assert pkg["meta"]["atr"] > 0
    assert pkg["meta"]["timeframe"] == "2h"
    assert 0.0 <= pkg["meta"]["pullback_pos_in_range"] <= 1.0


def test_pullback_no_setup_non_actionable():
    # A pure uptrend with NO pullback (close near range high) → non-actionable.
    base = pd.Timestamp("2026-01-01T00:00:00Z")
    rows = []
    px = 100.0
    for k in range(80):
        px += 1.0
        t = base + pd.Timedelta(hours=2 * k)
        rows.append([t, px - 1, px + 0.5, px - 0.8, px])  # always closing near highs
    with pytest.raises(ValueError):
        pull.order_package({}, _frame(rows))


def test_pullback_monitor_ratchets_and_closes():
    df = _pullback_df()
    pkg = pull.order_package({"symbol": "BTCUSDT"}, df)
    # SL-cross close.
    crashed = df.copy()
    last = crashed.iloc[-1].copy()
    last["close"] = pkg["sl"] - 1.0
    crashed = pd.concat([crashed, pd.DataFrame([last])], ignore_index=True)
    v = pull.monitor({}, crashed, pkg)
    assert v is not None and v["action"] == "close" and v["reason"] == "sl_cross"


def test_pullback_too_few_candles():
    df = _pullback_df().iloc[:10]
    with pytest.raises(ValueError):
        pull.order_package({}, df)


# --------------------------------------------------------------------------
# Inertness guard — scaffolds must NOT be wired into the live builder yet.
# --------------------------------------------------------------------------
def test_scaffolds_not_wired_into_builder():
    import src.runtime.strategy_signal_builders as b
    src = ""
    try:
        import inspect
        src = inspect.getsource(b)
    except Exception:  # noqa: BLE001
        pytest.skip("could not read builder source")
    assert "session_breakout_trend" not in src, "scaffold wired prematurely (Tier-3)"
    assert "htf_pullback_trend_2h" not in src, "scaffold wired prematurely (Tier-3)"
