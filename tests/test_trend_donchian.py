"""Unit tests for src/units/strategies/trend_donchian.py + its runtime wiring.

Fully offline — synthetic OHLCV DataFrames only, no exchange calls, no
secrets, no network. Mirrors tests/test_s012_turtle_soup.py and
tests/test_ict_scalp_5m.py for the units-layer strategy contract, and
adds focused coverage of the live Chandelier trailing-stop monitor (the
real-money-critical piece) and the intent-layer pluggability of the new
strategy name.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.units.strategies.trend_donchian import (
    _DEFAULTS,
    monitor,
    order_package,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _flat_frame(n: int = 60, high: float = 100.0, low: float = 90.0,
                close: float = 95.0) -> pd.DataFrame:
    """Quiet sideways market — establishes a Donchian channel [low, high]."""
    rng = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": rng,
        "open": np.full(n, close),
        "high": np.full(n, high),
        "low": np.full(n, low),
        "close": np.full(n, close),
        "volume": np.ones(n),
    })


def _long_breakout_frame(n: int = 60) -> pd.DataFrame:
    """Sideways range with a final bar closing ABOVE the prior 20-bar high."""
    df = _flat_frame(n).copy()
    last = n - 1
    df.iloc[last, df.columns.get_loc("open")] = 106.0
    df.iloc[last, df.columns.get_loc("high")] = 112.0
    df.iloc[last, df.columns.get_loc("low")] = 105.0
    df.iloc[last, df.columns.get_loc("close")] = 110.0  # > channel high 100
    return df


def _short_breakout_frame(n: int = 60) -> pd.DataFrame:
    """Sideways range with a final bar closing BELOW the prior 20-bar low."""
    df = _flat_frame(n, high=110.0, low=100.0, close=105.0).copy()
    last = n - 1
    df.iloc[last, df.columns.get_loc("open")] = 92.0
    df.iloc[last, df.columns.get_loc("high")] = 92.0
    df.iloc[last, df.columns.get_loc("low")] = 85.0
    df.iloc[last, df.columns.get_loc("close")] = 88.0  # < channel low 100
    return df


# ---------------------------------------------------------------------------
# order_package — entry logic
# ---------------------------------------------------------------------------


def test_long_breakout_produces_long_package():
    pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_long_breakout_frame())
    assert pkg["symbol"] == "BTCUSDT"
    assert pkg["direction"] == "long"
    assert pkg["entry"] == pytest.approx(110.0)
    # Initial stop is atr_stop_mult below entry; risk is positive.
    assert pkg["sl"] < pkg["entry"]
    # No fixed TP — far sentinel above entry, capped at the
    # `_TP_SENTINEL_CAP_PCT` exchange tolerance (the 50R unclamped target
    # is far outside Bybit's ~10%-from-base rule for BTC-scale risk).
    assert pkg["entry"] < pkg["tp"] <= pkg["entry"] * 1.1
    assert 0.0 <= pkg["confidence"] <= 1.0
    meta = pkg["meta"]
    assert meta["atr"] > 0
    assert meta["trail_mult"] == _DEFAULTS["trail_mult"]
    assert meta["timeframe"] == "1h"
    assert meta["risk_per_unit"] == pytest.approx(pkg["entry"] - pkg["sl"])


def test_short_breakout_produces_short_package():
    pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_short_breakout_frame())
    assert pkg["direction"] == "short"
    assert pkg["entry"] == pytest.approx(88.0)
    assert pkg["sl"] > pkg["entry"]          # stop above entry for shorts
    assert pkg["tp"] < pkg["entry"]          # far sentinel below entry
    assert pkg["meta"]["atr"] > 0


def _btc_75k_short_breakout_frame(n: int = 60) -> pd.DataFrame:
    """BTC-scale short breakout where the unclamped 50R sentinel goes negative.

    Anchors the regression for the 2026-05-27 incident: entry ~$75.6k with
    risk ~$1528 makes ``entry - 50*risk`` negative AND any value far below
    entry trips Bybit's ~10%-from-base-price TP rule (ErrCode 10001). The
    cap keeps the sentinel within exchange tolerance.
    """
    rng = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    high = np.full(n, 78100.0)
    low = np.full(n, 75600.0)
    close = np.full(n, 76800.0)
    open_ = np.full(n, 76800.0)
    last = n - 1
    open_[last] = 76000.0
    high[last] = 76000.0
    low[last] = 73800.0
    close[last] = 75400.0  # < channel low (75600), short breakout
    return pd.DataFrame({
        "timestamp": rng, "open": open_, "high": high, "low": low,
        "close": close, "volume": np.ones(n),
    })


def _btc_75k_long_breakout_frame(n: int = 60) -> pd.DataFrame:
    """BTC-scale long breakout where the unclamped 50R sentinel exceeds the
    exchange's TP cap. Used to verify the long-side clamp."""
    rng = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    high = np.full(n, 76200.0)
    low = np.full(n, 73700.0)
    close = np.full(n, 74900.0)
    open_ = np.full(n, 74900.0)
    last = n - 1
    open_[last] = 76000.0
    high[last] = 78400.0
    low[last] = 76000.0
    close[last] = 78000.0  # > channel high (76200), long breakout
    return pd.DataFrame({
        "timestamp": rng, "open": open_, "high": high, "low": low,
        "close": close, "volume": np.ones(n),
    })


def test_short_tp_clamped_within_exchange_cap():
    """Regression for 2026-05-27 — short TP capped at ~9.9% below entry."""
    pkg = order_package({"symbol": "BTCUSDT"},
                        candles_df=_btc_75k_short_breakout_frame())
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


def test_long_tp_clamped_within_exchange_cap():
    """Regression for 2026-05-27 — long TP capped at ~9.9% above entry."""
    pkg = order_package({"symbol": "BTCUSDT"},
                        candles_df=_btc_75k_long_breakout_frame())
    assert pkg["direction"] == "long"
    risk = pkg["entry"] - pkg["sl"]
    assert risk > 0
    unclamped = pkg["entry"] + 50.0 * risk
    assert unclamped > pkg["entry"] * 1.099, (
        f"fixture must hit the cap path: unclamped={unclamped}, "
        f"entry={pkg['entry']}, risk={risk}"
    )
    assert pkg["tp"] == pytest.approx(pkg["entry"] * 1.099)
    assert pkg["tp"] > pkg["entry"]


def test_no_breakout_is_non_actionable():
    with pytest.raises(ValueError, match="no breakout"):
        order_package({"symbol": "BTCUSDT"}, candles_df=_flat_frame())


def test_insufficient_candles_raises():
    with pytest.raises(ValueError, match="at least"):
        order_package({"symbol": "BTCUSDT"}, candles_df=_flat_frame(n=10))


def test_missing_candles_raises():
    with pytest.raises(ValueError):
        order_package({"symbol": "BTCUSDT"}, candles_df=None)


def test_trail_must_be_looser_than_stop_by_default():
    # The one interpretable robustness sensitivity: trail > entry stop.
    assert _DEFAULTS["trail_mult"] > _DEFAULTS["atr_stop_mult"]


# ---------------------------------------------------------------------------
# monitor — live Chandelier trailing stop (real-money-critical)
# ---------------------------------------------------------------------------


def _long_pkg(entry: float = 110.0, sl: float = 83.75, atr: float = 10.5,
              tp: float = 1500.0) -> dict:
    return {
        "order_package_id": "pkg-long",
        "direction": "long",
        "entry": entry,
        "sl": sl,
        "tp": tp,
        # entry_time omitted → _since_entry uses the full candle window;
        # the trail tests control the extreme via the frame highs/lows.
        "meta": {"atr": atr, "trail_mult": 3.0, "atr_period": 14},
    }


def _short_pkg(entry: float = 88.0, sl: float = 114.75, atr: float = 10.7,
               tp: float = 1.0) -> dict:
    return {
        "order_package_id": "pkg-short",
        "direction": "short",
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "meta": {"atr": atr, "trail_mult": 3.0, "atr_period": 14},
    }


def _price_frame(highs, lows, closes) -> pd.DataFrame:
    n = len(closes)
    rng = pd.date_range("2026-01-02", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": rng,
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": np.ones(n),
    })


def test_monitor_long_sl_cross_closes():
    # Current price has fallen to/through the stop.
    frame = _price_frame([90, 88], [82, 80], [88, 80.0])
    verdict = monitor({}, frame, _long_pkg())
    assert verdict == {"action": "close", "reason": "sl_cross", "exit_price": 80.0}


def test_monitor_short_sl_cross_closes():
    frame = _price_frame([100, 120], [95, 110], [98, 120.0])
    verdict = monitor({}, frame, _short_pkg())
    assert verdict["action"] == "close"
    assert verdict["reason"] == "sl_cross"


def test_monitor_long_trail_ratchets_up():
    # Price ran up to 150; current price 140. ext=150, atr=10.5, trail=3.0
    # → candidate = 150 - 31.5 = 118.5, which beats the 83.75 stop and
    # sits below the current price → ratchet up.
    frame = _price_frame([130, 150, 145], [120, 140, 138], [128, 148, 140.0])
    verdict = monitor({}, frame, _long_pkg())
    assert verdict == {"sl": pytest.approx(118.5)}


def test_monitor_long_trail_no_change_when_candidate_below_current_sl():
    # Modest highs near the channel → candidate well below the current
    # stop → no ratchet (the stop never loosens).
    frame = _price_frame([100, 101, 100], [95, 96, 95], [99, 100, 99.0])
    verdict = monitor({}, frame, _long_pkg())
    assert verdict is None


def test_monitor_long_trail_never_above_current_price():
    # A spike to 200 in the window but current price only 120: the naive
    # candidate (200 - 31.5 = 168.5) is ABOVE the current price, which
    # would be an instant stop-out. The guard must suppress it.
    frame = _price_frame([200, 130], [150, 118], [180, 120.0])
    verdict = monitor({}, frame, _long_pkg())
    assert verdict is None


def test_monitor_short_trail_ratchets_down():
    # Price fell to 50; current 60. ext(min low)=45, atr=10.7, trail=3.0
    # → candidate = 45 + 32.1 = 77.1, below the 114.75 stop and above the
    # current price → ratchet the stop down.
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


def test_monitor_recomputes_atr_when_meta_missing():
    # Legacy package without a frozen entry-time ATR: the monitor falls
    # back to a rolling ATR from the candles rather than crashing.
    pkg = _long_pkg()
    pkg["meta"] = {}  # no atr
    frame = _price_frame([130, 150, 145], [120, 140, 138], [128, 148, 140.0])
    verdict = monitor({}, frame, pkg)
    # Either ratchets or no-ops, but never raises and never returns a bad shape.
    assert verdict is None or "sl" in verdict


# ---------------------------------------------------------------------------
# Runtime signal builder (offline via monkeypatch)
# ---------------------------------------------------------------------------


def test_signal_builder_emits_actionable_signal(monkeypatch):
    import src.runtime.strategy_signal_builders as ssb
    import src.runtime.market_data as md
    import src.units.strategies as strat_pkg

    monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda settings: object())
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **k: _long_breakout_frame())
    monkeypatch.setattr(
        strat_pkg, "load_strategy_config",
        lambda: {"trend_donchian": {"enabled": True, "timeframe": "1h"}},
    )

    sig = ssb.trend_donchian_signal_builder({"SYMBOL": "BTCUSDT"})
    assert sig["side"] == "buy"
    assert sig["meta"]["strategy_name"] == "trend_donchian"
    assert sig["stop_loss"] < sig["entry_price"]
    # Trailing params propagate into the signal meta for the monitor.
    assert sig["meta"]["atr"] > 0
    assert sig["meta"]["trail_mult"] == _DEFAULTS["trail_mult"]
    # The per-strategy risk multiplier was removed 2026-06-29 — a strategy
    # carries NO risk level; the builder must not emit one. Sizing is the
    # RiskManager's account-level job.
    assert "strategy_risk_pct" not in sig["meta"]


def test_signal_builder_emits_no_strategy_risk_pct_via_multiplexer(monkeypatch):
    # The multiplexer must NOT inject a per-strategy risk level (removed
    # 2026-06-29). Sizing is account-level only.
    import src.runtime.pipeline as pipeline
    import src.runtime.strategy_signal_builders as ssb
    import src.runtime.market_data as md
    import src.units.strategies as strat_pkg

    monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda settings: object())
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **k: _long_breakout_frame())
    monkeypatch.setattr(
        strat_pkg, "load_strategy_config",
        lambda: {"trend_donchian": {"enabled": True, "timeframe": "1h"}},
    )
    monkeypatch.setattr(pipeline, "STRATEGIES", ["trend_donchian"])
    monkeypatch.setattr(pipeline, "is_strategy_paused", lambda name: False)

    sig = pipeline.multiplexed_signal_builder({"SYMBOL": "BTCUSDT"})
    assert sig["side"] == "buy"
    assert "strategy_risk_pct" not in sig["meta"]


def test_signal_builder_disabled_returns_none(monkeypatch):
    import src.runtime.strategy_signal_builders as ssb
    import src.units.strategies as strat_pkg

    monkeypatch.setattr(
        strat_pkg, "load_strategy_config",
        lambda: {"trend_donchian": {"enabled": False}},
    )
    sig = ssb.trend_donchian_signal_builder({"SYMBOL": "BTCUSDT"})
    assert sig["side"] == "none"
    assert sig["meta"]["reason"] == "disabled_in_yaml"


# ---------------------------------------------------------------------------
# Intent-layer pluggability — name + priority
# ---------------------------------------------------------------------------


def test_trend_donchian_priority_registered():
    from src.runtime.intents import DEFAULT_PRIORITIES
    assert DEFAULT_PRIORITIES["trend_donchian"] == 20
    # Lowest on the roster — cannot override the established strategies.
    assert DEFAULT_PRIORITIES["trend_donchian"] < DEFAULT_PRIORITIES["ict_scalp_5m"]


def test_intent_multiplexer_accepts_trend_donchian_via_injected_builder():
    from src.runtime.intent_multiplexer import multiplexed_intent_signal_builder

    def _fake_builder(settings):
        return {
            "symbol": "BTCUSDT",
            "side": "buy",
            "price": 110.0,
            "entry_price": 110.0,
            "stop_loss": 83.75,
            "take_profit": 1500.0,
            "meta": {
                "strategy_name": "trend_donchian",
                "confidence": 0.9,
                "strategy_risk_pct": 0.3,
            },
        }

    sig = multiplexed_intent_signal_builder(
        {"SYMBOL": "BTCUSDT"},
        builders={"trend_donchian": _fake_builder},
        strategies=["trend_donchian"],
    )
    assert sig["side"] == "buy"
    assert sig["meta"]["strategy_name"] == "trend_donchian"
    # The conservative risk multiplier survives intent aggregation.
    assert sig["meta"]["strategy_risk_pct"] == pytest.approx(0.3)
