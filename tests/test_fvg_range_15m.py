"""Unit tests for src/units/strategies/fvg_range_15m.py.

Fully offline — synthetic OHLCV DataFrames only, no exchange calls, no
secrets, no network. Covers the units-layer contract: confirmed-range
detection (width bounds + boundary touches), the ADX chop-gate, the
location + unfilled-FVG + wick-rejection entry, the SL-beyond-boundary /
TP-at-opposite-boundary risk model, and the time-decay monitor.

The fixture builds a low-ADX oscillating range [100, 110] whose body hovers
mid-range while wicks tag the boundaries (a genuine chop range, not a
directional zig-zag — the latter scores high ADX and would be gated out),
then a bullish FVG in the lower third with a wick-rejection current bar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.units.strategies.fvg_range_15m import (
    _DEFAULTS,
    _adx,
    monitor,
    order_package,
)


# Test cfg: range_lookback shrunk to 24 + adx_max relaxed to 40 so a compact
# synthetic frame can establish the range; the chop gate itself is exercised
# by test_adx_gate_blocks_trending. All other params at their live defaults.
_CFG = {
    "symbol": "BTCUSDT", "timeframe": "15m",
    "range_lookback": 24, "atr_period": 14, "adx_period": 14, "adx_max": 40.0,
    "min_width_pct": 0.01, "max_width_pct": 0.5, "touch_tol_pct": 0.005,
    "min_touches": 3, "third_frac": 0.34, "fvg_search": 8,
    "min_fvg_size_bps": 0.5, "atr_stop_buffer": 0.25, "timeout_bars": 48,
    "min_confidence": 0.0,
}

_S, _R = 100.0, 110.0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _range_frame() -> pd.DataFrame:
    """Low-ADX oscillating range [100,110]: 28 calm bars (body ~105, wicks
    alternately tag S/R to build >=3 touches each side), then drift into the
    lower third and form a bullish FVG with a wick-rejection final bar."""
    rng = np.random.RandomState(7)
    data = []
    for k in range(28):
        op = 105 + rng.uniform(-0.6, 0.6)
        cl = 105 + rng.uniform(-0.6, 0.6)
        hi = max(op, cl) + 0.3
        lo = min(op, cl) - 0.3
        if k % 4 == 0:
            lo = _S       # tag support
        if k % 4 == 2:
            hi = _R       # tag resistance
        data.append([op, hi, lo, cl])
    # drift down into the lower third (<= 100 + 10*0.34 = 103.4)
    data.append([104.0, 104.2, 102.0, 102.3])
    data.append([102.3, 102.5, 101.5, 101.8])   # bar k-2 of the gap: high=102.5
    data.append([101.8, 102.0, 101.6, 101.7])   # bar k-1
    # current bar: bullish FVG gap=[high[k-2]=102.5, low[k]=102.6]; low wicks
    # to 102.6, closes 103.4 back above the gap with a bullish body.
    data.append([102.7, 103.6, 102.6, 103.4])
    idx = pd.date_range("2026-01-01", periods=len(data), freq="15min", tz="UTC")
    df = pd.DataFrame(data, columns=["open", "high", "low", "close"])
    df["timestamp"] = idx
    df["volume"] = 1.0
    return df


def _trending_frame(n: int = 60) -> pd.DataFrame:
    """A steady uptrend — high ADX — to exercise the chop gate."""
    rng = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    close = np.linspace(100.0, 160.0, n)
    return pd.DataFrame({
        "timestamp": rng,
        "open": close - 0.2,
        "high": close + 0.3,
        "low": close - 0.5,
        "close": close,
        "volume": np.ones(n),
    })


# ---------------------------------------------------------------------------
# Entry logic
# ---------------------------------------------------------------------------


def test_long_signal_in_confirmed_range():
    pkg = order_package(dict(_CFG), candles_df=_range_frame())
    assert pkg["direction"] == "long"
    assert pkg["symbol"] == "BTCUSDT"
    # entry = last close
    assert pkg["entry"] == pytest.approx(103.4, abs=1e-6)
    # TP = opposite (resistance) boundary; SL beyond the gap/support boundary.
    assert pkg["tp"] == pytest.approx(_R, abs=1e-6)
    assert pkg["sl"] < _S
    assert pkg["entry"] < pkg["tp"]
    assert 0.0 <= pkg["confidence"] <= 1.0
    meta = pkg["meta"]
    assert meta["strategy_name"] == "fvg_range_15m"
    assert meta["range_hi"] == pytest.approx(_R, abs=1e-6)
    assert meta["range_lo"] == pytest.approx(_S, abs=1e-6)
    # FVG geometry surfaced for the dashboard overlay.
    assert meta["fvg_low"] < meta["fvg_high"]
    assert meta["timeout_bars"] == _CFG["timeout_bars"]
    assert meta["entry_time"] is not None


def test_risk_is_positive_and_stop_below_support_for_long():
    pkg = order_package(dict(_CFG), candles_df=_range_frame())
    assert pkg["meta"]["risk_per_unit"] > 0
    # stop sits atr_stop_buffer*ATR below min(gap_low, support)
    assert pkg["sl"] < pkg["meta"]["range_lo"]


def test_adx_gate_blocks_trending():
    """A trending tape (high ADX) is non-actionable even with the gate at the
    live default — a range needs chop."""
    cfg = dict(_CFG)
    cfg["adx_max"] = 20.0
    with pytest.raises(ValueError, match="regime not chop"):
        order_package(cfg, candles_df=_trending_frame())


def test_touches_gate_rejects_unconfirmed_range():
    """Requiring more boundary touches than the frame provides → non-actionable."""
    cfg = dict(_CFG)
    cfg["min_touches"] = 50
    with pytest.raises(ValueError, match="range not confirmed"):
        order_package(cfg, candles_df=_range_frame())


def test_width_gate_rejects_too_wide():
    cfg = dict(_CFG)
    cfg["max_width_pct"] = 0.001   # 10/105 ≈ 9.5% > 0.1% → rejected
    with pytest.raises(ValueError, match="range width"):
        order_package(cfg, candles_df=_range_frame())


def test_raises_on_missing_candles():
    with pytest.raises(ValueError):
        order_package(dict(_CFG), candles_df=None)


def test_raises_on_too_few_candles():
    df = _range_frame().iloc[:5].reset_index(drop=True)
    with pytest.raises(ValueError, match="need at least"):
        order_package(dict(_CFG), candles_df=df)


def test_defaults_match_validated_config():
    """Guard the validated walk-forward config so a careless edit is caught."""
    assert _DEFAULTS["range_lookback"] == 48
    assert _DEFAULTS["min_touches"] == 4
    assert _DEFAULTS["adx_max"] == 20.0
    assert _DEFAULTS["atr_stop_buffer"] == 0.25
    assert _DEFAULTS["third_frac"] == 0.34
    assert _DEFAULTS["timeframe"] == "15m"


# ---------------------------------------------------------------------------
# monitor() — time-decay backstop
# ---------------------------------------------------------------------------


def test_monitor_closes_after_timeout():
    pkg = order_package(dict(_CFG), candles_df=_range_frame())
    entry_ts = pkg["meta"]["entry_time"]
    # Build a candle frame extending well past timeout_bars after entry.
    n = pkg["meta"]["timeout_bars"] + 5
    start = pd.Timestamp(entry_ts)
    rng = pd.date_range(start, periods=n, freq="15min")
    later = pd.DataFrame({
        "timestamp": rng,
        "open": np.full(n, 104.0), "high": np.full(n, 104.5),
        "low": np.full(n, 103.5), "close": np.full(n, 104.0),
        "volume": np.ones(n),
    })
    out = monitor({}, later, pkg)
    assert out == {"action": "close", "reason": "time_decay"}


def test_monitor_no_close_before_timeout():
    pkg = order_package(dict(_CFG), candles_df=_range_frame())
    entry_ts = pd.Timestamp(pkg["meta"]["entry_time"])
    rng = pd.date_range(entry_ts, periods=3, freq="15min")
    early = pd.DataFrame({
        "timestamp": rng,
        "open": [104.0, 104.0, 104.0], "high": [104.5, 104.5, 104.5],
        "low": [103.5, 103.5, 103.5], "close": [104.0, 104.0, 104.0],
        "volume": [1.0, 1.0, 1.0],
    })
    assert monitor({}, early, pkg) is None


def test_monitor_handles_empty_candles():
    pkg = order_package(dict(_CFG), candles_df=_range_frame())
    assert monitor({}, None, pkg) is None
    assert monitor({}, pd.DataFrame(), pkg) is None


# ---------------------------------------------------------------------------
# ADX helper parity
# ---------------------------------------------------------------------------


def test_adx_is_numeric_on_flat_bars():
    """Flat bars must not upcast the ADX Series to object dtype (the
    fade_breakout_4h crash). float('nan') guard keeps it numeric."""
    n = 40
    flat = pd.DataFrame({
        "high": np.full(n, 100.0), "low": np.full(n, 100.0),
        "close": np.full(n, 100.0),
    })
    out = _adx(flat, 14)
    assert str(out.dtype) == "float64"
