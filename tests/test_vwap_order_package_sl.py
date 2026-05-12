"""Regression tests for order_package() SL/TP correctness.

Prior to this fix order_package() computed:

    risk = entry - tp   (long)   # always negative: entry < vwap for valid long
    sl   = entry + risk if risk > 0 else entry * 0.98  # always 2% fallback

so the std-dev SL from build_vwap_signal() was silently ignored.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.units.strategies.vwap import (
    SL_STD_MULT_DEFAULT,
    build_vwap_signal,
    order_package,
)


def _candles_below_vwap(n: int = 100, entry_price: float = 83000.0) -> pd.DataFrame:
    """Return candles where the last close is well below VWAP.

    The first n-1 candles cluster around a higher mean so the session
    VWAP is above ``entry_price``. The final candle closes at
    ``entry_price``, triggering a buy signal.
    """
    rng = np.random.default_rng(42)
    high_mean = entry_price + 600.0
    closes = rng.normal(high_mean, 50, n - 1).tolist() + [entry_price]
    highs = [c + 30 for c in closes]
    lows = [c - 30 for c in closes]
    volumes = [1.0] * n
    return pd.DataFrame({"high": highs, "low": lows, "close": closes, "volume": volumes})


def _candles_above_vwap(n: int = 100, entry_price: float = 85000.0) -> pd.DataFrame:
    """Return candles where the last close is well above VWAP.

    Mirrors ``_candles_below_vwap`` — session VWAP below entry so the
    final bar triggers a sell signal.
    """
    rng = np.random.default_rng(99)
    low_mean = entry_price - 600.0
    closes = rng.normal(low_mean, 50, n - 1).tolist() + [entry_price]
    highs = [c + 30 for c in closes]
    lows = [c - 30 for c in closes]
    volumes = [1.0] * n
    return pd.DataFrame({"high": highs, "low": lows, "close": closes, "volume": volumes})


def _cfg() -> dict:
    return {"symbol": "BTCUSDT", "_shadow_predictors": []}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_order_package_long_sl_is_below_entry(monkeypatch) -> None:
    """For a buy signal, sl must be strictly below entry.

    Regression: the prior formula always produced sl = entry * 0.98
    (2% below) regardless of std_dev. Now it must equal build_vwap_signal
    stop_loss = entry - SL_STD_MULT * std_dev.
    """
    monkeypatch.setattr(
        "src.units.strategies.vwap._has_open_vwap_package", lambda: False
    )
    df = _candles_below_vwap()
    pkg = order_package(_cfg(), candles_df=df)

    assert pkg["direction"] == "long"
    assert pkg["sl"] < pkg["entry"], (
        f"long SL {pkg['sl']} must be below entry {pkg['entry']}"
    )
    # SL must NOT be the 2% fallback (entry * 0.98)
    fallback_sl = round(pkg["entry"] * 0.98, 8)
    assert pkg["sl"] != fallback_sl, (
        f"long SL is still the 2% fallback ({fallback_sl}); fix did not apply"
    )


def test_order_package_short_sl_is_above_entry(monkeypatch) -> None:
    """For a sell signal, sl must be strictly above entry."""
    monkeypatch.setattr(
        "src.units.strategies.vwap._has_open_vwap_package", lambda: False
    )
    df = _candles_above_vwap()
    pkg = order_package(_cfg(), candles_df=df)

    assert pkg["direction"] == "short"
    assert pkg["sl"] > pkg["entry"], (
        f"short SL {pkg['sl']} must be above entry {pkg['entry']}"
    )
    fallback_sl = round(pkg["entry"] * 1.02, 8)
    assert pkg["sl"] != fallback_sl, (
        f"short SL is still the 2% fallback ({fallback_sl}); fix did not apply"
    )


def test_order_package_sl_matches_build_vwap_signal(monkeypatch) -> None:
    """order_package() sl must equal build_vwap_signal() stop_loss.

    Ensures the two code paths stay in lock-step: if SL_STD_MULT or the
    std-dev window ever changes in build_vwap_signal, order_package picks
    it up automatically.
    """
    monkeypatch.setattr(
        "src.units.strategies.vwap._has_open_vwap_package", lambda: False
    )
    df = _candles_below_vwap()
    signal = build_vwap_signal(df, symbol="BTCUSDT")
    assert signal["side"] == "buy", "fixture must produce a buy signal"

    pkg = order_package(_cfg(), candles_df=df)
    assert pkg["sl"] == round(signal["stop_loss"], 8), (
        f"order_package sl {pkg['sl']} does not match "
        f"build_vwap_signal stop_loss {signal['stop_loss']}"
    )
    assert pkg["tp"] == round(signal["take_profit"], 8), (
        f"order_package tp {pkg['tp']} does not match "
        f"build_vwap_signal take_profit {signal['take_profit']}"
    )
