"""S-012 PR C3: pipeline-level wiring tests for turtle_soup.

Exercises ``src.runtime.pipeline.turtle_soup_signal_builder`` end-to-end
via a stub exchange object — no network, no DB. Verifies the signal flows
through the same pipeline shape as VWAP (and therefore the same
multiplexer / RiskManager / order layer).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _bullish_sweep_candles(n: int = 200, base: float = 50_000.0) -> pd.DataFrame:
    """OHLCV with a bullish sweep on the most recent bar."""
    rng = pd.date_range("2026-04-01", periods=n, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": np.full(n, base),
            "high": np.full(n, base + 100.0),
            "low": np.full(n, base - 100.0),
            "close": np.full(n, base + 50.0),
            "volume": np.full(n, 1.0),
        },
        index=rng,
    )
    last = df.index[-1]
    df.loc[last, "low"] = base - 500.0
    df.loc[last, "high"] = base + 100.0
    df.loc[last, "open"] = base - 400.0
    df.loc[last, "close"] = base + 50.0
    return df


def _flat_candles(n: int = 200, base: float = 50_000.0) -> pd.DataFrame:
    rng = pd.date_range("2026-04-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "open": np.full(n, base),
            "high": np.full(n, base + 100.0),
            "low": np.full(n, base - 100.0),
            "close": np.full(n, base + 50.0),
            "volume": np.full(n, 1.0),
        },
        index=rng,
    )


class _StubExchange:
    def __init__(self, df: pd.DataFrame):
        self._df = df
        self.calls = []

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int):
        self.calls.append({"symbol": symbol, "timeframe": timeframe, "limit": limit})
        return self._df


@pytest.fixture
def patch_exchange(monkeypatch):
    """Patch _build_killzone_exchange to return a stub bound to a candle frame."""
    holder: dict = {}

    def _set(df: pd.DataFrame) -> _StubExchange:
        stub = _StubExchange(df)
        holder["stub"] = stub
        from src.runtime import strategy_signal_builders as ssb
        monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda settings: stub)
        return stub

    return _set


# ---------------------------------------------------------------------------
# turtle_soup_signal_builder — happy path
# ---------------------------------------------------------------------------


def test_turtle_soup_signal_builder_emits_buy_on_bullish_sweep(patch_exchange):
    from src.runtime.pipeline import turtle_soup_signal_builder

    patch_exchange(_bullish_sweep_candles())
    signal = turtle_soup_signal_builder(
        {"SYMBOL": "BTCUSDT", "TURTLE_SOUP_TIMEFRAME": "15m"}
    )
    assert signal["side"] == "buy"
    assert signal["symbol"] == "BTCUSDT"
    # S-026 G1: strategies emit the trade idea, not the order — no qty.
    assert "qty" not in signal


def test_turtle_soup_signal_includes_pipeline_required_fields(patch_exchange):
    from src.runtime.pipeline import turtle_soup_signal_builder

    patch_exchange(_bullish_sweep_candles())
    signal = turtle_soup_signal_builder({"SYMBOL": "BTCUSDT"})
    for key in ("symbol", "side", "price", "stop_loss", "take_profit", "meta"):
        assert key in signal, f"missing pipeline-shape key: {key}"
    # S-026 G1: qty is no longer a strategy-shape key.
    assert "qty" not in signal


def test_turtle_soup_meta_includes_strategy_name(patch_exchange):
    from src.runtime.pipeline import turtle_soup_signal_builder

    patch_exchange(_bullish_sweep_candles())
    signal = turtle_soup_signal_builder({"SYMBOL": "BTCUSDT"})
    assert signal["meta"]["strategy_name"] == "turtle_soup"


def test_turtle_soup_sl_below_entry_below_tp_for_long(patch_exchange):
    from src.runtime.pipeline import turtle_soup_signal_builder

    patch_exchange(_bullish_sweep_candles())
    signal = turtle_soup_signal_builder({"SYMBOL": "BTCUSDT"})
    assert signal["stop_loss"] < signal["price"] < signal["take_profit"]


# ---------------------------------------------------------------------------
# turtle_soup_signal_builder — flat market is NOT an error
# ---------------------------------------------------------------------------


def test_turtle_soup_flat_market_returns_side_none(patch_exchange):
    from src.runtime.pipeline import turtle_soup_signal_builder

    patch_exchange(_flat_candles())
    signal = turtle_soup_signal_builder({"SYMBOL": "BTCUSDT"})
    assert signal["side"] == "none"
    # S-026 G1: no qty field is emitted by strategies.
    assert "qty" not in signal
    assert signal["meta"]["strategy_name"] == "turtle_soup"


def test_turtle_soup_no_candles_raises_runtime_error(patch_exchange):
    from src.runtime.pipeline import turtle_soup_signal_builder

    patch_exchange(pd.DataFrame())
    with pytest.raises(RuntimeError, match="no candle data"):
        turtle_soup_signal_builder({"SYMBOL": "BTCUSDT"})


# ---------------------------------------------------------------------------
# Multiplexer integration — turtle_soup is now in STRATEGIES + builders
# ---------------------------------------------------------------------------


def test_turtle_soup_in_strategies_list():
    from src.runtime.pipeline import STRATEGIES
    assert "turtle_soup" in STRATEGIES


def test_turtle_soup_in_strategy_builders():
    from src.runtime.pipeline import _STRATEGY_BUILDERS
    assert "turtle_soup" in _STRATEGY_BUILDERS


def test_no_per_strategy_risk_map():
    # The per-strategy risk multiplier (STRATEGY_RISK_PCT) was removed
    # 2026-06-29 — sizing is the RiskManager's account-level job. The symbol
    # must no longer exist on the pipeline module.
    import src.runtime.pipeline as pipeline
    assert not hasattr(pipeline, "STRATEGY_RISK_PCT")


def test_multiplexer_dispatches_turtle_soup_when_actionable(patch_exchange, monkeypatch):
    """Multiplexer iterates STRATEGIES; turtle_soup actionable → returns its signal."""
    from src.runtime import pipeline as pl

    patch_exchange(_bullish_sweep_candles())
    # Force STRATEGIES order: turtle_soup first so it wins.
    monkeypatch.setattr(pl, "STRATEGIES", ["turtle_soup", "vwap"])
    signal = pl.multiplexed_signal_builder({"SYMBOL": "BTCUSDT", "MAX_QTY": 1.0})
    assert signal["side"] == "buy"
    assert signal["meta"]["strategy_name"] == "turtle_soup"


# ---------------------------------------------------------------------------
# run_pipeline dispatch via STRATEGY=turtle_soup env
# ---------------------------------------------------------------------------


def test_run_pipeline_routes_to_turtle_soup_via_env(patch_exchange, monkeypatch):
    """STRATEGY=turtle_soup env selects turtle_soup_signal_builder."""
    from src.runtime import pipeline as pl

    monkeypatch.setenv("STRATEGY", "turtle_soup")
    patch_exchange(_bullish_sweep_candles())

    # Bypass real env validation + DB writes — record which builder ran.
    called = {}

    def _fake_builder(settings):
        called["builder"] = "turtle_soup"
        return pl.turtle_soup_signal_builder(settings)

    # We monkey-patch the module-level reference run_pipeline reads.
    monkeypatch.setattr(pl, "turtle_soup_signal_builder", _fake_builder)
    monkeypatch.setattr(pl, "_write_ict_signals_from_meta", lambda *a, **kw: None)
    # Skip the trading-decision branches (place_order etc.); we only
    # verify which builder was selected.
    monkeypatch.setattr(pl, "place_order", lambda *a, **kw: {"status": "noop"}, raising=False)

    settings = {"SYMBOL": "BTCUSDT", "MAX_QTY": 1.0, "DRY_RUN": True}
    try:
        pl.run_pipeline(settings)
    except Exception:
        # run_pipeline does a lot beyond builder selection; we just need
        # to confirm the right builder was invoked before any later step
        # potentially raises in the sandbox.
        pass
    assert called.get("builder") == "turtle_soup"
