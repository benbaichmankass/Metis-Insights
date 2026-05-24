"""Unit tests for src/units/strategies/squeeze_breakout_4h.py.

Fully offline — synthetic OHLCV only. Covers the squeeze-release entry
detection (compression -> expansion), the no-fire cases, defaults, and
the shared Chandelier monitor.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.units.strategies.squeeze_breakout_4h import (
    _DEFAULTS,
    monitor,
    order_package,
)


def _frame(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    n = len(rows)
    rng = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({
        "timestamp": rng,
        "open": [r[2] for r in rows],
        "high": [r[0] for r in rows],
        "low": [r[1] for r in rows],
        "close": [r[2] for r in rows],
        "volume": np.ones(n),
    })


def _compressed(n: int = 39) -> list:
    # Tight range, constant close -> sd≈0 (narrow BB), ATR>0 (wider KC) ->
    # BB sits inside KC -> squeeze ON.
    return [(101.0, 99.0, 100.0)] * n


def _fire_up_frame() -> pd.DataFrame:
    rows = _compressed(39) + [(112.0, 108.0, 110.0)]  # expansion up -> long
    return _frame(rows)


def _fire_down_frame() -> pd.DataFrame:
    rows = _compressed(39) + [(92.0, 88.0, 90.0)]      # expansion down -> short
    return _frame(rows)


def _no_fire_compressed() -> pd.DataFrame:
    # Stays compressed through the final bar -> no release -> non-actionable.
    return _frame(_compressed(40))


def _never_squeezed_frame() -> pd.DataFrame:
    # Steady uptrend: wide BB, never inside KC -> squeeze never on.
    closes = [100.0 + 3.0 * i for i in range(40)]
    return _frame([(c + 1, c - 1, c) for c in closes])


# ---------------------------------------------------------------------------
# order_package — squeeze-release entry
# ---------------------------------------------------------------------------


def test_squeeze_release_up_produces_long():
    pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_fire_up_frame())
    assert pkg["direction"] == "long"
    assert pkg["entry"] == pytest.approx(110.0)
    assert pkg["sl"] < pkg["entry"]
    assert pkg["tp"] > pkg["entry"]
    assert pkg["meta"]["atr"] > 0
    assert pkg["meta"]["timeframe"] == "4h"
    assert pkg["meta"]["trail_mult"] == _DEFAULTS["trail_mult"]


def test_squeeze_release_down_produces_short():
    pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_fire_down_frame())
    assert pkg["direction"] == "short"
    assert pkg["entry"] == pytest.approx(90.0)
    assert pkg["sl"] > pkg["entry"]
    assert pkg["tp"] < pkg["entry"]


def test_still_compressed_is_non_actionable():
    with pytest.raises(ValueError, match="no squeeze release"):
        order_package({"symbol": "BTCUSDT"}, candles_df=_no_fire_compressed())


def test_never_squeezed_is_non_actionable():
    with pytest.raises(ValueError, match="no squeeze release"):
        order_package({"symbol": "BTCUSDT"}, candles_df=_never_squeezed_frame())


def test_insufficient_candles_raises():
    with pytest.raises(ValueError, match="at least"):
        order_package({"symbol": "BTCUSDT"}, candles_df=_frame(_compressed(10)))


def test_missing_candles_raises():
    with pytest.raises(ValueError):
        order_package({"symbol": "BTCUSDT"}, candles_df=None)


def test_defaults_match_validated_config():
    assert _DEFAULTS["bb_period"] == 20
    assert _DEFAULTS["bb_std"] == 2.0
    assert _DEFAULTS["kc_mult"] == 1.0
    assert _DEFAULTS["trail_mult"] == 3.5
    assert _DEFAULTS["timeframe"] == "4h"


# ---------------------------------------------------------------------------
# monitor — shared Chandelier trailing stop
# ---------------------------------------------------------------------------


def _long_pkg(entry=110.0, sl=83.75, atr=10.5, tp=1500.0) -> dict:
    return {"direction": "long", "entry": entry, "sl": sl, "tp": tp,
            "meta": {"atr": atr, "trail_mult": 3.0, "atr_period": 14}}


def _short_pkg(entry=88.0, sl=114.75, atr=10.7, tp=1.0) -> dict:
    return {"direction": "short", "entry": entry, "sl": sl, "tp": tp,
            "meta": {"atr": atr, "trail_mult": 3.0, "atr_period": 14}}


def _price_frame(highs, lows, closes) -> pd.DataFrame:
    n = len(closes)
    rng = pd.date_range("2026-02-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"timestamp": rng, "open": closes, "high": highs,
                         "low": lows, "close": closes, "volume": np.ones(n)})


def test_monitor_long_sl_cross_closes():
    frame = _price_frame([90, 88], [82, 80], [88, 80.0])
    verdict = monitor({}, frame, _long_pkg())
    assert verdict == {"action": "close", "reason": "sl_cross", "exit_price": 80.0}


def test_monitor_long_trail_ratchets_up():
    frame = _price_frame([130, 150, 145], [120, 140, 138], [128, 148, 140.0])
    verdict = monitor({}, frame, _long_pkg())
    assert verdict == {"sl": pytest.approx(118.5)}


def test_monitor_short_trail_ratchets_down():
    frame = _price_frame([70, 62, 65], [55, 45, 48], [65, 52, 60.0])
    verdict = monitor({}, frame, _short_pkg())
    assert verdict == {"sl": pytest.approx(77.1)}


def test_monitor_no_candles_returns_none():
    assert monitor({}, None, _long_pkg()) is None
