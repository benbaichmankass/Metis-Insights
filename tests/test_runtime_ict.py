"""
Runtime adapter tests for the ICT signal-builder (M7 Phase 2.5).

These tests exercise ``src.runtime.pipeline.ict_signal_builder`` \u2014 the
thin adapter that fetches OHLCV from the configured exchange, coerces
the candles into a ``DatetimeIndex``-keyed DataFrame, and delegates
to the pure ``build_ict_signal`` factory.

The pure builder itself is exhaustively covered by
``tests/test_ict_signal_builder.py``; here we focus on the runtime
plumbing:

* registration of ``\"ict\"`` in ``_STRATEGY_BUILDERS``
* OHLCV fetch path + DataFrame coercion (timestamp \u2192 UTC index)
* HTF frame fetch when ``ICT_HTF_TIMEFRAME`` is set
* graceful fallback when HTF fetch raises
* error path when no candles come back
* end-to-end: a fake exchange that produces a clear bullish-trend frame
  yields ``side=\"buy\"`` from the adapter

We patch ``_build_killzone_exchange`` rather than reaching for live
exchange clients \u2014 the goal is to verify wiring, not network behaviour.
"""
from __future__ import annotations

from typing import Any, List, Tuple

import numpy as np
import pandas as pd
import pytest

from src.runtime import pipeline as runtime_pipeline


# ---------------------------------------------------------------------------
# Fake exchange
# ---------------------------------------------------------------------------

class FakeExchange:
    """Minimal stand-in mirroring the ``get_ohlcv`` shape used by
    ``vwap_signal_builder`` / ``breakout_model_signal_builder``.

    Records every call so tests can assert on argument plumbing, and
    optionally returns different payloads keyed by ``timeframe`` so we
    can verify HTF vs strategy-frame routing without any network.
    """

    def __init__(self, frames_by_timeframe: dict[str, Any]):
        self._frames = frames_by_timeframe
        self.calls: List[Tuple[str, str, int]] = []

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100):
        self.calls.append((symbol, timeframe, limit))
        if timeframe not in self._frames:
            raise RuntimeError(
                f"FakeExchange: no fixture for timeframe={timeframe!r}"
            )
        payload = self._frames[timeframe]
        if callable(payload):
            return payload()
        return payload


def _ohlcv_rows(start_price: float, n: int, step: float, hour_utc: int = 8):
    """Build n monotone-up OHLCV rows in the [ts_ms, o, h, l, c, v] shape."""
    base_ts = pd.Timestamp("2025-01-06", tz="UTC") + pd.Timedelta(hours=hour_utc)
    rows = []
    for i in range(n):
        ts = base_ts + pd.Timedelta(minutes=i)
        c = start_price + step * i
        o = c - 0.1
        h = max(o, c) + 0.05
        lo = min(o, c) - 0.05
        rows.append(
            [int(ts.value // 1_000_000), float(o), float(h), float(lo), float(c), 10.0]
        )
    return rows


def _bullish_fvg_rows(n: int = 80, hour_utc: int = 8):
    """Same monotone uptrend as the pure-builder fixture, with a
    carved 3-candle bullish FVG five bars from the end. Returned as
    list-of-lists so coercion is exercised end-to-end.
    """
    base_ts = pd.Timestamp("2025-01-06", tz="UTC") + pd.Timedelta(hours=hour_utc)
    closes = 100.0 + np.arange(n, dtype=float)
    opens = closes - 0.1
    highs = np.maximum(opens, closes) + 0.05
    lows = np.minimum(opens, closes) - 0.05

    # carve bullish FVG at idx = n - 5
    idx = n - 5
    highs[idx - 2], lows[idx - 2] = 100.0, 99.0
    opens[idx - 2], closes[idx - 2] = 99.2, 99.8
    highs[idx - 1], lows[idx - 1] = 105.0, 100.5
    opens[idx - 1], closes[idx - 1] = 100.5, 105.0
    highs[idx], lows[idx] = 106.0, 102.0
    opens[idx], closes[idx] = 102.5, 105.5

    rows = []
    for i in range(n):
        ts = base_ts + pd.Timedelta(minutes=i)
        rows.append(
            [
                int(ts.value // 1_000_000),
                float(opens[i]),
                float(highs[i]),
                float(lows[i]),
                float(closes[i]),
                10.0,
            ]
        )
    return rows


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_ict_registered_in_strategy_builders():
    """``\"ict\"`` is callable via the registry and appears as the last
    fallback in the multiplexer order (M7 Phase 2.6 / CP-14)."""
    assert "ict" in runtime_pipeline._STRATEGY_BUILDERS
    assert (
        runtime_pipeline._STRATEGY_BUILDERS["ict"]
        is runtime_pipeline.ict_signal_builder
    )
    # CP-14 ordering: ICT is last so it cannot pre-empt breakout_confirmation
    # or vwap. Detailed ordering tests live in tests/test_runtime_pipeline.py.
    assert "ict" in runtime_pipeline.STRATEGIES
    assert runtime_pipeline.STRATEGIES[-1] == "ict"


# ---------------------------------------------------------------------------
# OHLCV coercion helper
# ---------------------------------------------------------------------------

def test_coerce_ohlcv_with_dt_index_from_list_rows():
    rows = _ohlcv_rows(100.0, n=5, step=1.0)
    df = runtime_pipeline._coerce_ohlcv_with_dt_index(rows)

    assert isinstance(df.index, pd.DatetimeIndex)
    assert str(df.index.tz) == "UTC"
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 5


def test_coerce_ohlcv_with_dt_index_from_dataframe_with_ts_column():
    rows = _ohlcv_rows(100.0, n=5, step=1.0)
    raw = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df = runtime_pipeline._coerce_ohlcv_with_dt_index(raw)
    assert isinstance(df.index, pd.DatetimeIndex)
    assert "timestamp" not in df.columns


def test_coerce_ohlcv_passes_through_existing_dt_index():
    idx = pd.date_range("2025-01-06", periods=3, freq="1min", tz="UTC")
    raw = pd.DataFrame(
        {
            "open": [1.0, 2.0, 3.0],
            "high": [1.1, 2.1, 3.1],
            "low": [0.9, 1.9, 2.9],
            "close": [1.05, 2.05, 3.05],
            "volume": [10.0, 10.0, 10.0],
        },
        index=idx,
    )
    df = runtime_pipeline._coerce_ohlcv_with_dt_index(raw)
    assert df.index.equals(idx)


# ---------------------------------------------------------------------------
# Adapter wiring \u2014 happy path
# ---------------------------------------------------------------------------

def test_ict_signal_builder_returns_buy_on_bullish_fvg(monkeypatch):
    fake = FakeExchange({"15m": _bullish_fvg_rows(n=80)})
    monkeypatch.setattr(
        runtime_pipeline, "_build_killzone_exchange", lambda settings: fake
    )

    settings = {
        "EXCHANGE": "bybit",
        "SYMBOL": "BTC/USDT:USDT",
        "MAX_QTY": 1.0,
    }
    sig = runtime_pipeline.ict_signal_builder(settings)

    assert sig["side"] == "buy"
    assert sig["symbol"] == "BTC/USDT:USDT"
    assert sig["qty"] == pytest.approx(1.0)
    assert sig["meta"]["strategy_name"] == "ict"
    assert sig["meta"]["trigger_kind"] == "fvg"
    # Default timeframe is 15m, default candle limit is 200.
    assert fake.calls == [("BTC/USDT:USDT", "15m", 200)]


def test_ict_signal_builder_overrides_timeframe_and_limit(monkeypatch):
    fake = FakeExchange({"5m": _bullish_fvg_rows(n=80)})
    monkeypatch.setattr(
        runtime_pipeline, "_build_killzone_exchange", lambda settings: fake
    )

    settings = {
        "EXCHANGE": "bybit",
        "SYMBOL": "ETHUSDT",
        "ICT_TIMEFRAME": "5m",
        "ICT_CANDLE_LIMIT": 120,
    }
    sig = runtime_pipeline.ict_signal_builder(settings)

    assert sig["side"] == "buy"
    assert fake.calls == [("ETHUSDT", "5m", 120)]


# ---------------------------------------------------------------------------
# HTF frame routing
# ---------------------------------------------------------------------------

def test_ict_signal_builder_fetches_htf_when_configured(monkeypatch):
    """When ``ICT_HTF_TIMEFRAME`` is set, the adapter must issue a second
    ``get_ohlcv`` call with that timeframe and feed it to ``build_ict_signal``.
    """
    fake = FakeExchange(
        {
            "15m": _bullish_fvg_rows(n=80),
            "1h": _bullish_fvg_rows(n=80),
        }
    )
    monkeypatch.setattr(
        runtime_pipeline, "_build_killzone_exchange", lambda settings: fake
    )

    captured: dict = {}

    def _capturing_builder(candles_df, settings=None, htf_df=None):
        captured["candles_df"] = candles_df
        captured["htf_df"] = htf_df
        captured["settings"] = settings
        return {"symbol": "X", "side": "none", "qty": 0, "meta": {"strategy_name": "ict"}}

    # Patch the import target inside the adapter (it imports lazily).
    import src.runtime.strategies.ict as ict_strat
    monkeypatch.setattr(
        ict_strat, "build_ict_signal", _capturing_builder
    )

    settings = {
        "EXCHANGE": "bybit",
        "SYMBOL": "BTCUSDT",
        "ICT_HTF_TIMEFRAME": "1h",
        "ICT_HTF_CANDLE_LIMIT": 50,
    }
    runtime_pipeline.ict_signal_builder(settings)

    # Two fetches: strategy frame then HTF.
    assert ("BTCUSDT", "15m", 200) in fake.calls
    assert ("BTCUSDT", "1h", 50) in fake.calls
    assert captured["htf_df"] is not None
    assert isinstance(captured["htf_df"].index, pd.DatetimeIndex)


def test_ict_signal_builder_falls_back_when_htf_fetch_fails(monkeypatch):
    """A raising HTF fetch should be logged + swallowed; the strategy
    frame still drives the trend gate.
    """
    def _raising_htf():
        raise RuntimeError("upstream 5xx")

    fake = FakeExchange(
        {
            "15m": _bullish_fvg_rows(n=80),
            "1h": _raising_htf,  # callable \u2192 invoked per call
        }
    )
    monkeypatch.setattr(
        runtime_pipeline, "_build_killzone_exchange", lambda settings: fake
    )

    captured: dict = {}

    def _capturing_builder(candles_df, settings=None, htf_df=None):
        captured["htf_df"] = htf_df
        return {"symbol": "X", "side": "none", "qty": 0, "meta": {"strategy_name": "ict"}}

    import src.runtime.strategies.ict as ict_strat
    monkeypatch.setattr(ict_strat, "build_ict_signal", _capturing_builder)

    settings = {
        "EXCHANGE": "bybit",
        "SYMBOL": "BTCUSDT",
        "ICT_HTF_TIMEFRAME": "1h",
    }
    sig = runtime_pipeline.ict_signal_builder(settings)
    assert sig["side"] == "none"
    # HTF fetch raised \u2192 builder gets None, not a frame.
    assert captured["htf_df"] is None


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_ict_signal_builder_raises_when_no_candles(monkeypatch):
    fake = FakeExchange({"15m": []})
    monkeypatch.setattr(
        runtime_pipeline, "_build_killzone_exchange", lambda settings: fake
    )

    with pytest.raises(RuntimeError, match="no candle data"):
        runtime_pipeline.ict_signal_builder({"EXCHANGE": "bybit"})


def test_coerce_raises_when_neither_dt_index_nor_timestamp_column():
    raw = pd.DataFrame(
        {
            "open": [1.0],
            "high": [1.1],
            "low": [0.9],
            "close": [1.05],
            "volume": [10.0],
        }
    )
    with pytest.raises(RuntimeError, match="timestamp"):
        runtime_pipeline._coerce_ohlcv_with_dt_index(raw)
