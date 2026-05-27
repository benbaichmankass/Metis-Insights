"""Unit tests for src/units/strategies/fade_breakout_4h.py.

Fully offline — synthetic OHLCV DataFrames only, no exchange calls, no
secrets, no network. Mirrors tests/test_trend_donchian.py (the mirror
strategy) for the units-layer contract: failed-breakout entry detection,
the ADX chop-gate, and the live Chandelier trailing-stop monitor (shared
verbatim with trend_donchian, re-tested here for safety).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.units.strategies.fade_breakout_4h import (
    _DEFAULTS,
    _adx,
    monitor,
    order_package,
)

# Gate-disabled cfg for the entry-logic tests — a perfectly flat range has
# an undefined (NaN) ADX, which the chop-gate would reject before the entry
# logic runs. Disabling the gate (adx_max=None) isolates the failed-breakout
# detection; the gate itself is covered by the dedicated ADX tests below.
_NO_GATE = {"symbol": "BTCUSDT", "adx_max": None}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _flat_frame(n: int = 60, high: float = 100.0, low: float = 90.0,
                close: float = 95.0) -> pd.DataFrame:
    """Quiet sideways market — establishes a Donchian channel [low, high]."""
    rng = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({
        "timestamp": rng,
        "open": np.full(n, close),
        "high": np.full(n, high),
        "low": np.full(n, low),
        "close": np.full(n, close),
        "volume": np.ones(n),
    })


def _failed_upside_frame(n: int = 60) -> pd.DataFrame:
    """Range [90,100]; final bar pierces ABOVE the channel high then closes
    back inside → a failed upside breakout → SHORT fade."""
    df = _flat_frame(n).copy()
    last = n - 1
    df.iloc[last, df.columns.get_loc("open")] = 100.0
    df.iloc[last, df.columns.get_loc("high")] = 112.0   # pierces dc_hi=100
    df.iloc[last, df.columns.get_loc("low")] = 94.0
    df.iloc[last, df.columns.get_loc("close")] = 96.0   # closes back < 100
    return df


def _failed_downside_frame(n: int = 60) -> pd.DataFrame:
    """Range [100,110]; final bar pierces BELOW the channel low then closes
    back inside → a failed downside breakout → LONG fade."""
    df = _flat_frame(n, high=110.0, low=100.0, close=105.0).copy()
    last = n - 1
    df.iloc[last, df.columns.get_loc("open")] = 100.0
    df.iloc[last, df.columns.get_loc("high")] = 106.0
    df.iloc[last, df.columns.get_loc("low")] = 88.0     # pierces dc_lo=100
    df.iloc[last, df.columns.get_loc("close")] = 104.0  # closes back > 100
    return df


def _inside_frame(n: int = 60) -> pd.DataFrame:
    """Range [90,100]; final bar stays fully inside the channel → no fade."""
    df = _flat_frame(n).copy()
    last = n - 1
    df.iloc[last, df.columns.get_loc("high")] = 98.0
    df.iloc[last, df.columns.get_loc("low")] = 92.0
    df.iloc[last, df.columns.get_loc("close")] = 95.0
    return df


def _trending_frame(n: int = 60) -> pd.DataFrame:
    """Strong monotonic uptrend → high ADX (the chop-gate should reject)."""
    rng = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    close = 100.0 + 5.0 * np.arange(n)
    return pd.DataFrame({
        "timestamp": rng,
        "open": close,
        "high": close + 2.0,
        "low": close - 2.0,
        "close": close,
        "volume": np.ones(n),
    })


def _chop_failed_upside_frame(n: int = 60) -> pd.DataFrame:
    """Rangebound oscillation (low ADX) + a final failed upside breakout —
    the full happy path WITH the default chop-gate active."""
    highs = np.array([99.0 if i % 2 == 0 else 100.0 for i in range(n)])
    lows = np.array([90.0 if i % 2 == 0 else 91.0 for i in range(n)])
    closes = np.full(n, 95.0)
    highs[-1], lows[-1], closes[-1] = 112.0, 94.0, 96.0  # failed upside grab
    rng = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({
        "timestamp": rng, "open": closes, "high": highs,
        "low": lows, "close": closes, "volume": np.ones(n),
    })


# ---------------------------------------------------------------------------
# order_package — entry logic (gate disabled)
# ---------------------------------------------------------------------------


def test_failed_upside_produces_short():
    pkg = order_package(_NO_GATE, candles_df=_failed_upside_frame())
    assert pkg["symbol"] == "BTCUSDT"
    assert pkg["direction"] == "short"
    assert pkg["entry"] == pytest.approx(96.0)
    assert pkg["sl"] > pkg["entry"]              # stop above the rejection wick
    assert pkg["tp"] < pkg["entry"]              # far sentinel below entry
    assert 0.0 <= pkg["confidence"] <= 1.0
    meta = pkg["meta"]
    assert meta["atr"] > 0
    assert meta["trail_mult"] == _DEFAULTS["trail_mult"]
    assert meta["timeframe"] == "4h"
    assert meta["risk_per_unit"] == pytest.approx(pkg["sl"] - pkg["entry"])


def test_failed_downside_produces_long():
    pkg = order_package(_NO_GATE, candles_df=_failed_downside_frame())
    assert pkg["direction"] == "long"
    assert pkg["entry"] == pytest.approx(104.0)
    assert pkg["sl"] < pkg["entry"]              # stop below the rejection wick
    assert pkg["tp"] > pkg["entry"]              # far sentinel above entry
    assert pkg["meta"]["atr"] > 0


def _btc_75k_failed_upside_frame(n: int = 60) -> pd.DataFrame:
    """BTC-scale failed upside breakout where unclamped 50R TP goes negative.

    Mirrors the trend_donchian regression — fade/squeeze share the same
    short TP formula and the same pre-flight tp>0 guard, so the clamp must
    keep TP positive here too.
    """
    rng = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    high = np.full(n, 77800.0)
    low = np.full(n, 75300.0)
    close = np.full(n, 76500.0)
    open_ = np.full(n, 76500.0)
    last = n - 1
    open_[last] = 77800.0
    high[last] = 80200.0   # pierces channel hi 77800
    low[last] = 75400.0
    close[last] = 76200.0  # closes back inside → SHORT fade
    return pd.DataFrame({
        "timestamp": rng, "open": open_, "high": high, "low": low,
        "close": close, "volume": np.ones(n),
    })


def test_short_tp_clamped_within_exchange_cap():
    """Regression for 2026-05-27 — short TP capped at ~9.9% below entry."""
    pkg = order_package(_NO_GATE, candles_df=_btc_75k_failed_upside_frame())
    assert pkg["direction"] == "short"
    risk = pkg["sl"] - pkg["entry"]
    assert risk > 0
    unclamped = pkg["entry"] - 50.0 * risk
    assert unclamped < pkg["entry"] * 0.901, (
        f"fixture must hit the cap path: unclamped={unclamped}, "
        f"entry={pkg['entry']}, risk={risk}"
    )
    assert pkg["tp"] == pytest.approx(pkg["entry"] * 0.901)
    assert pkg["tp"] < pkg["entry"]


def test_inside_bar_is_non_actionable():
    with pytest.raises(ValueError, match="no failed breakout"):
        order_package(_NO_GATE, candles_df=_inside_frame())


def test_insufficient_candles_raises():
    with pytest.raises(ValueError, match="at least"):
        order_package(_NO_GATE, candles_df=_flat_frame(n=10))


def test_missing_candles_raises():
    with pytest.raises(ValueError):
        order_package(_NO_GATE, candles_df=None)


# ---------------------------------------------------------------------------
# ADX chop-gate
# ---------------------------------------------------------------------------


def test_adx_gate_rejects_trend():
    # Default gate (adx_max=20). A strong uptrend has high ADX → rejected
    # as "not chop" before any entry logic runs.
    with pytest.raises(ValueError, match="regime not chop"):
        order_package({"symbol": "BTCUSDT"}, candles_df=_trending_frame())


def test_chop_passes_gate_and_fades():
    # Default gate active; a low-ADX rangebound frame with a failed upside
    # breakout produces a SHORT fade.
    pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_chop_failed_upside_frame())
    assert pkg["direction"] == "short"
    assert pkg["meta"]["adx"] is not None
    assert pkg["meta"]["adx"] < _DEFAULTS["adx_max"]


# ---------------------------------------------------------------------------
# Regression: flat / zero-movement bars must not crash _adx
# (2026-05-25 live incident — fade_breakout_4h raised "No numeric types to
# aggregate" every tick on calm 4h bars, producing zero shadow data. Cause:
# the divide-by-zero guard used pd.NA, which upcast the float Series to
# object dtype so the downstream ewm().mean() raised. A flat frame makes
# plus_dm/minus_dm both 0 -> plus_di+minus_di == 0 everywhere, the exact
# trigger. These tests exercise _adx, which the gate-disabled entry tests
# above never did.)
# ---------------------------------------------------------------------------


def test_adx_flat_frame_stays_numeric_and_does_not_raise():
    # A perfectly flat market has undefined directional movement; _adx must
    # return a NUMERIC (NaN-filled) Series, never raise / never go object.
    s = _adx(_flat_frame(n=60), 14)
    assert str(s.dtype) != "object"  # would be object before the fix
    assert s.isna().all()            # ADX undefined for zero-movement bars


def test_flat_frame_with_gate_rejects_cleanly_not_aggregate_error():
    # With the DEFAULT chop-gate active, a flat frame's ADX is undefined, so
    # order_package must reject it as "regime not chop" — NOT crash with the
    # pandas "No numeric types to aggregate" TypeError (the live bug).
    with pytest.raises(ValueError, match="regime not chop"):
        order_package({"symbol": "BTCUSDT"}, candles_df=_flat_frame())


# ---------------------------------------------------------------------------
# Defaults match the validated (nested walk-forward) config
# ---------------------------------------------------------------------------


def test_defaults_match_validated_config():
    assert _DEFAULTS["donchian"] == 20
    assert _DEFAULTS["trail_mult"] == 3.5
    assert _DEFAULTS["adx_max"] == 20.0
    assert _DEFAULTS["atr_stop_buffer"] == 0.5
    assert _DEFAULTS["timeframe"] == "4h"


# ---------------------------------------------------------------------------
# monitor — live Chandelier trailing stop (shared with trend_donchian)
# ---------------------------------------------------------------------------


def _long_pkg(entry: float = 110.0, sl: float = 83.75, atr: float = 10.5,
              tp: float = 1500.0) -> dict:
    return {
        "order_package_id": "pkg-long",
        "direction": "long",
        "entry": entry, "sl": sl, "tp": tp,
        "meta": {"atr": atr, "trail_mult": 3.0, "atr_period": 14},
    }


def _short_pkg(entry: float = 88.0, sl: float = 114.75, atr: float = 10.7,
               tp: float = 1.0) -> dict:
    return {
        "order_package_id": "pkg-short",
        "direction": "short",
        "entry": entry, "sl": sl, "tp": tp,
        "meta": {"atr": atr, "trail_mult": 3.0, "atr_period": 14},
    }


def _price_frame(highs, lows, closes) -> pd.DataFrame:
    n = len(closes)
    rng = pd.date_range("2026-01-02", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({
        "timestamp": rng, "open": closes, "high": highs,
        "low": lows, "close": closes, "volume": np.ones(n),
    })


def test_monitor_long_sl_cross_closes():
    frame = _price_frame([90, 88], [82, 80], [88, 80.0])
    verdict = monitor({}, frame, _long_pkg())
    assert verdict == {"action": "close", "reason": "sl_cross", "exit_price": 80.0}


def test_monitor_short_sl_cross_closes():
    frame = _price_frame([100, 120], [95, 110], [98, 120.0])
    verdict = monitor({}, frame, _short_pkg())
    assert verdict["action"] == "close"
    assert verdict["reason"] == "sl_cross"


def test_monitor_long_trail_ratchets_up():
    # ext=150, atr=10.5, trail=3.0 → candidate = 150 - 31.5 = 118.5.
    frame = _price_frame([130, 150, 145], [120, 140, 138], [128, 148, 140.0])
    verdict = monitor({}, frame, _long_pkg())
    assert verdict == {"sl": pytest.approx(118.5)}


def test_monitor_long_trail_never_above_current_price():
    # Spike to 200 in-window but current price 120: naive candidate 168.5 is
    # above price (instant stop-out) → suppressed.
    frame = _price_frame([200, 130], [150, 118], [180, 120.0])
    verdict = monitor({}, frame, _long_pkg())
    assert verdict is None


def test_monitor_short_trail_ratchets_down():
    # ext(min low)=45, atr=10.7, trail=3.0 → candidate = 45 + 32.1 = 77.1.
    frame = _price_frame([70, 62, 65], [55, 45, 48], [65, 52, 60.0])
    verdict = monitor({}, frame, _short_pkg())
    assert verdict == {"sl": pytest.approx(77.1)}


def test_monitor_handles_json_string_meta():
    import json
    pkg = _long_pkg()
    pkg["meta"] = json.dumps(pkg["meta"])
    frame = _price_frame([130, 150, 145], [120, 140, 138], [128, 148, 140.0])
    verdict = monitor({}, frame, pkg)
    assert verdict == {"sl": pytest.approx(118.5)}


def test_monitor_no_candles_returns_none():
    assert monitor({}, None, _long_pkg()) is None
