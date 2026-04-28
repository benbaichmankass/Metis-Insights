"""
Unit tests for the M7 Phase 2.4 ICT signal-builder factory.

These tests exercise ``src.runtime.strategies.ict.build_ict_signal`` in
isolation — the builder must be a pure function (no exchange, no DB, no
file IO) so we feed it synthetic OHLCV frames and assert on the returned
signal dict.

Branches covered:

* empty / missing inputs → ``side="none"`` with explanatory ``reason``.
* ``htf_trend_bias`` returns ``"neutral"`` → ``side="none"``.
* Kill-zone gate active but the latest bar is outside any kill-zone →
  ``side="none"``.
* Kill-zone gate **disabled** via settings → signal can still fire.
* Bullish trend + active kill-zone + unfilled bullish FVG →
  ``side="buy"`` with ``trigger_kind="fvg"``.
* Bearish trend + active kill-zone + unfilled bearish FVG →
  ``side="sell"`` with ``trigger_kind="fvg"``.
* No aligned FVG but a same-bias OB exists → trigger falls back to OB.
* Trend present but no aligned zone at all → ``reason="no_aligned_zone"``.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import pytest

from src.runtime.strategies.ict import build_ict_signal


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

LONDON_HOUR_UTC = 8        # inside ``london`` kill-zone (07:00–10:00 UTC)
OUT_OF_KZ_HOUR_UTC = 17    # outside all three kill-zones


def _make_index(n: int, start_hour: int = LONDON_HOUR_UTC) -> pd.DatetimeIndex:
    """Generate a UTC DatetimeIndex of length *n* starting at *start_hour*.

    We use 1-minute spacing so the entire frame stays inside a single
    kill-zone window — this gives us deterministic gating in tests.
    """
    start = pd.Timestamp("2025-01-06", tz="UTC") + pd.Timedelta(hours=start_hour)
    return pd.date_range(start=start, periods=n, freq="1min")


def _trending_frame(
    n: int,
    direction: str = "up",
    start_price: float = 100.0,
    step: float = 1.0,
    start_hour: int = LONDON_HOUR_UTC,
) -> pd.DataFrame:
    """Build a strictly monotone OHLCV frame.

    A monotone series guarantees fast-EMA(20) is unambiguously above /
    below slow-EMA(50) on the last bar, so ``htf_trend_bias`` returns
    ``"bullish"`` for ``direction="up"`` and ``"bearish"`` for
    ``direction="down"``.
    """
    sign = 1 if direction == "up" else -1
    closes = start_price + sign * step * np.arange(n, dtype=float)
    opens = closes - sign * 0.1
    highs = np.maximum(opens, closes) + 0.05
    lows = np.minimum(opens, closes) - 0.05
    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.full(n, 10.0),
        },
        index=_make_index(n, start_hour=start_hour),
    )
    return df


def _inject_bullish_fvg(df: pd.DataFrame, idx: int = -5) -> pd.DataFrame:
    """
    Carve a 3-candle bullish FVG so candle-1.high < candle-3.low.

    The detector spec (see ``src/ict_detection/fvg_detector.py``) is:
      bullish FVG if ``df[i-2].high < df[i].low``. We mutate three
      consecutive rows ending at ``idx`` so the gap is unfilled by the
      remaining bars.
    """
    df = df.copy()
    if idx < 0:
        idx = len(df) + idx
    # Candle 1 (bar idx-2): low body
    df.iloc[idx - 2, df.columns.get_loc("high")] = 100.0
    df.iloc[idx - 2, df.columns.get_loc("low")] = 99.0
    df.iloc[idx - 2, df.columns.get_loc("open")] = 99.2
    df.iloc[idx - 2, df.columns.get_loc("close")] = 99.8
    # Candle 2 (bar idx-1): impulse — we don't constrain it strictly
    df.iloc[idx - 1, df.columns.get_loc("high")] = 105.0
    df.iloc[idx - 1, df.columns.get_loc("low")] = 100.5
    df.iloc[idx - 1, df.columns.get_loc("open")] = 100.5
    df.iloc[idx - 1, df.columns.get_loc("close")] = 105.0
    # Candle 3 (bar idx): low above candle-1 high → bullish FVG
    df.iloc[idx, df.columns.get_loc("low")] = 102.0
    df.iloc[idx, df.columns.get_loc("high")] = 106.0
    df.iloc[idx, df.columns.get_loc("open")] = 102.5
    df.iloc[idx, df.columns.get_loc("close")] = 105.5
    return df


def _inject_bearish_fvg(df: pd.DataFrame, idx: int = -5) -> pd.DataFrame:
    """
    Carve a bearish FVG: ``df[i-2].low > df[i].high``.
    """
    df = df.copy()
    if idx < 0:
        idx = len(df) + idx
    # Candle 1 (idx-2): high body sitting above the eventual gap
    df.iloc[idx - 2, df.columns.get_loc("high")] = 110.0
    df.iloc[idx - 2, df.columns.get_loc("low")] = 109.0
    df.iloc[idx - 2, df.columns.get_loc("open")] = 109.8
    df.iloc[idx - 2, df.columns.get_loc("close")] = 109.2
    # Candle 2 (idx-1): impulse down
    df.iloc[idx - 1, df.columns.get_loc("high")] = 108.5
    df.iloc[idx - 1, df.columns.get_loc("low")] = 104.0
    df.iloc[idx - 1, df.columns.get_loc("open")] = 108.5
    df.iloc[idx - 1, df.columns.get_loc("close")] = 104.0
    # Candle 3 (idx): high below candle-1 low → bearish FVG
    df.iloc[idx, df.columns.get_loc("low")] = 101.0
    df.iloc[idx, df.columns.get_loc("high")] = 107.0
    df.iloc[idx, df.columns.get_loc("open")] = 106.5
    df.iloc[idx, df.columns.get_loc("close")] = 102.0
    return df


# ---------------------------------------------------------------------------
# Empty / missing input branches
# ---------------------------------------------------------------------------

def test_empty_dataframe_returns_flat_signal():
    df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    sig = build_ict_signal(df, settings={"SYMBOL": "ETHUSDT"})
    assert sig["side"] == "none"
    assert sig["qty"] == 0.0
    assert sig["symbol"] == "ETHUSDT"
    assert sig["meta"]["reason"] == "empty_candles"
    assert sig["meta"]["strategy_name"] == "ict"


def test_missing_trend_source_column_returns_flat():
    df = _trending_frame(80, direction="up")
    sig = build_ict_signal(
        df,
        settings={"ICT_TREND_SOURCE": "vwap_typ"},
    )
    assert sig["side"] == "none"
    assert sig["meta"]["reason"] == "trend_source_missing"
    assert sig["meta"]["trend_source"] == "vwap_typ"


# ---------------------------------------------------------------------------
# Trend gate
# ---------------------------------------------------------------------------

def test_neutral_trend_short_circuits_to_none():
    # Flat prices → fast EMA == slow EMA → neutral bias
    n = 80
    df = pd.DataFrame(
        {
            "open": np.full(n, 100.0),
            "high": np.full(n, 100.0),
            "low": np.full(n, 100.0),
            "close": np.full(n, 100.0),
            "volume": np.full(n, 10.0),
        },
        index=_make_index(n),
    )
    sig = build_ict_signal(df, settings={})
    assert sig["side"] == "none"
    assert sig["meta"]["trend_bias"] == "neutral"
    assert sig["meta"]["reason"] == "trend_neutral"


# ---------------------------------------------------------------------------
# Kill-zone gate
# ---------------------------------------------------------------------------

def test_killzone_gate_blocks_when_outside_zone():
    df = _trending_frame(80, direction="up", start_hour=OUT_OF_KZ_HOUR_UTC)
    df = _inject_bullish_fvg(df, idx=-5)
    sig = build_ict_signal(df, settings={})
    assert sig["side"] == "none"
    assert sig["meta"]["reason"] == "killzone_inactive"
    assert sig["meta"]["trend_bias"] == "bullish"
    assert sig["meta"]["kill_zone"] is None


def test_killzone_gate_can_be_disabled():
    df = _trending_frame(80, direction="up", start_hour=OUT_OF_KZ_HOUR_UTC)
    df = _inject_bullish_fvg(df, idx=-5)
    sig = build_ict_signal(
        df, settings={"ICT_REQUIRE_KILLZONE": False, "MAX_QTY": 2.5}
    )
    assert sig["side"] == "buy"
    assert sig["qty"] == pytest.approx(2.5)
    # No kill-zone is active but the gate was bypassed.
    assert sig["meta"]["kill_zone"] is None
    assert sig["meta"]["trigger_kind"] == "fvg"


# ---------------------------------------------------------------------------
# Happy paths — bullish + bearish FVG triggers
# ---------------------------------------------------------------------------

def test_bullish_trend_with_aligned_fvg_emits_buy():
    df = _trending_frame(80, direction="up", start_hour=LONDON_HOUR_UTC)
    df = _inject_bullish_fvg(df, idx=-5)
    sig = build_ict_signal(df, settings={"SYMBOL": "BTCUSDT", "MAX_QTY": 1.0})
    assert sig["side"] == "buy"
    assert sig["symbol"] == "BTCUSDT"
    assert sig["qty"] == pytest.approx(1.0)
    meta = sig["meta"]
    assert meta["strategy_name"] == "ict"
    assert meta["trend_bias"] == "bullish"
    assert meta["kill_zone"] == "london"
    assert meta["trigger_kind"] == "fvg"
    assert meta["trigger_zone"]["type"] == "bullish"
    assert meta["trigger_zone"]["filled"] is False


def test_bearish_trend_with_aligned_fvg_emits_sell():
    df = _trending_frame(
        80, direction="down", start_price=200.0, start_hour=LONDON_HOUR_UTC
    )
    df = _inject_bearish_fvg(df, idx=-5)
    sig = build_ict_signal(df, settings={"SYMBOL": "ETHUSDT"})
    assert sig["side"] == "sell"
    assert sig["symbol"] == "ETHUSDT"
    meta = sig["meta"]
    assert meta["trend_bias"] == "bearish"
    assert meta["kill_zone"] == "london"
    assert meta["trigger_kind"] == "fvg"
    assert meta["trigger_zone"]["type"] == "bearish"


# ---------------------------------------------------------------------------
# OB fallback + no-aligned-zone branch
# ---------------------------------------------------------------------------

def test_no_aligned_zone_returns_flat_with_diagnostics(monkeypatch):
    """Trend bullish + in killzone but analyzer returns no zones → flat.

    A naturally-built monotone uptrend tends to spawn incidental FVGs in
    the synthetic data. Patching ``analyze`` lets us assert the
    no-aligned-zone branch deterministically without fighting the
    detector's geometry.
    """
    import src.runtime.strategies.ict as ict_module

    df = _trending_frame(80, direction="up", start_hour=LONDON_HOUR_UTC)
    fake_signals = {
        "symbol": "BTCUSDT",
        "timeframe_rows": len(df),
        "fvgs": [],
        "order_blocks": [
            # Same-bias zones absent — only opposite-bias OB present.
            {
                "type": "bearish",
                "timestamp": df.index[-10],
                "high": 99.0,
                "low": 97.0,
                "open": 98.5,
                "close": 97.5,
                "tested": False,
            }
        ],
        "kill_zones": {
            "asia":     {ts: False for ts in df.index},
            "london":   {ts: True for ts in df.index},
            "new_york": {ts: False for ts in df.index},
        },
        "latest_signal": None,
        "latest_price": None,
    }
    monkeypatch.setattr(
        ict_module.ICTSignalsAnalyzer, "analyze",
        lambda self, _df: fake_signals,
    )

    sig = build_ict_signal(df, settings={})
    assert sig["side"] == "none"
    meta = sig["meta"]
    assert meta["reason"] == "no_aligned_zone"
    assert meta["trend_bias"] == "bullish"
    assert meta["kill_zone"] == "london"
    # Diagnostic payload always present so the writer can persist context.
    assert "fvgs" in meta
    assert "order_blocks" in meta


def test_ob_fallback_when_no_fvg(monkeypatch):
    """
    When no aligned FVG exists but an aligned Order Block does, the
    builder should fall back to OB as the trigger.

    We patch ``ICTSignalsAnalyzer.analyze`` for this single test so we
    can assert the OB-fallback branch deterministically without
    hand-crafting a 50-bar sequence that simultaneously trends, lands in
    a kill-zone, produces *no* FVGs, and produces a valid OB.
    """
    import src.runtime.strategies.ict as ict_module

    df = _trending_frame(80, direction="up", start_hour=LONDON_HOUR_UTC)
    last_ts = df.index[-1]
    fake_signals = {
        "symbol": "BTCUSDT",
        "timeframe_rows": len(df),
        "fvgs": [],
        "order_blocks": [
            {
                "type": "bullish",
                "timestamp": last_ts - pd.Timedelta(minutes=10),
                "high": 105.0,
                "low": 103.0,
                "open": 103.5,
                "close": 104.5,
                "tested": False,
            }
        ],
        "kill_zones": {
            "asia":     {ts: False for ts in df.index},
            "london":   {ts: True for ts in df.index},
            "new_york": {ts: False for ts in df.index},
        },
        "latest_signal": None,
        "latest_price": None,
    }

    def _fake_analyze(self, _df):
        return fake_signals

    monkeypatch.setattr(
        ict_module.ICTSignalsAnalyzer, "analyze", _fake_analyze
    )

    sig = build_ict_signal(df, settings={"MAX_QTY": 3.0})
    assert sig["side"] == "buy"
    assert sig["qty"] == pytest.approx(3.0)
    assert sig["meta"]["trigger_kind"] == "ob"
    assert sig["meta"]["trigger_zone"]["type"] == "bullish"
    assert sig["meta"]["kill_zone"] == "london"


# ---------------------------------------------------------------------------
# Defensive: settings parsing
# ---------------------------------------------------------------------------

def test_string_truthy_settings_are_accepted_for_killzone_flag():
    df = _trending_frame(80, direction="up", start_hour=OUT_OF_KZ_HOUR_UTC)
    df = _inject_bullish_fvg(df, idx=-5)
    sig = build_ict_signal(
        df, settings={"ICT_REQUIRE_KILLZONE": "false"}
    )
    # "false" string disables the gate → signal fires even outside KZ.
    assert sig["side"] == "buy"


def test_invalid_max_qty_falls_back_to_one():
    df = _trending_frame(80, direction="up")
    df = _inject_bullish_fvg(df, idx=-5)
    sig = build_ict_signal(df, settings={"MAX_QTY": "not-a-number"})
    assert sig["side"] == "buy"
    assert sig["qty"] == pytest.approx(1.0)


def test_default_symbol_when_settings_empty():
    df = _trending_frame(80, direction="up")
    df = _inject_bullish_fvg(df, idx=-5)
    sig = build_ict_signal(df, settings=None)
    assert sig["symbol"] == "BTCUSDT"
