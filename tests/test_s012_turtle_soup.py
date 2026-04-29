"""S-012 PR C2: unit tests for src/units/strategies/turtle_soup.py.

Fully offline — synthetic OHLCV DataFrames only, no exchange calls.
Mirrors the structure of tests/test_s008_strategies.py for vwap.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.units.strategies.turtle_soup import order_package


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _flat_frame(n: int = 80, base: float = 50_000.0) -> pd.DataFrame:
    """Quiet sideways market — no setup will trigger."""
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


def _bullish_sweep_frame(n: int = 80, base: float = 50_000.0) -> pd.DataFrame:
    """Last bar pierces the rolling-min low and closes back above it.

    body_to_range = |close - open| / (high - low) is set to ≈ 0.75 so the
    body filter (default 0.60) passes.
    """
    df = _flat_frame(n, base).copy()
    last = df.index[-1]
    # Sweep the recent low (49_900) by ~400, then close back inside.
    df.loc[last, "low"] = base - 500.0   # 49_500
    df.loc[last, "high"] = base + 100.0  # 50_100
    df.loc[last, "open"] = base - 400.0  # 49_600
    df.loc[last, "close"] = base + 50.0  # 50_050
    return df


def _bearish_sweep_frame(n: int = 80, base: float = 50_000.0) -> pd.DataFrame:
    """Last bar pierces the rolling-max high and closes back below it."""
    df = _flat_frame(n, base).copy()
    last = df.index[-1]
    df.loc[last, "high"] = base + 500.0   # 50_500
    df.loc[last, "low"] = base - 100.0    # 49_900
    df.loc[last, "open"] = base + 400.0   # 50_400
    df.loc[last, "close"] = base - 50.0   # 49_950
    return df


# ---------------------------------------------------------------------------
# Happy path — bullish + bearish sweeps fire
# ---------------------------------------------------------------------------


class TestTurtleSoupHappyPath:
    def test_bullish_sweep_emits_long_package(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_sweep_frame())
        assert pkg["direction"] == "long"
        assert pkg["symbol"] == "BTCUSDT"

    def test_returned_dict_has_required_keys(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_sweep_frame())
        for key in ("symbol", "direction", "entry", "sl", "tp", "confidence", "meta"):
            assert key in pkg, f"missing key: {key}"

    def test_meta_contains_secondary_target_and_diagnostics(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_sweep_frame())
        for key in ("level", "sweep_extreme", "atr", "risk_per_unit", "tp2", "body_to_range", "setup_tf"):
            assert key in pkg["meta"], f"missing meta key: {key}"

    def test_long_sl_below_entry_below_tp(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_sweep_frame())
        assert pkg["sl"] < pkg["entry"] < pkg["tp"]

    def test_long_tp2_beyond_tp(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_sweep_frame())
        assert pkg["meta"]["tp2"] > pkg["tp"]

    def test_bearish_sweep_emits_short_package(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bearish_sweep_frame())
        assert pkg["direction"] == "short"

    def test_short_sl_above_entry_above_tp(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bearish_sweep_frame())
        assert pkg["sl"] > pkg["entry"] > pkg["tp"]

    def test_short_tp2_beyond_tp(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bearish_sweep_frame())
        assert pkg["meta"]["tp2"] < pkg["tp"]

    def test_confidence_in_unit_range(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_sweep_frame())
        assert 0.0 <= pkg["confidence"] <= 1.0

    def test_risk_per_unit_positive(self):
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=_bullish_sweep_frame())
        assert pkg["meta"]["risk_per_unit"] > 0


# ---------------------------------------------------------------------------
# No-signal cases
# ---------------------------------------------------------------------------


class TestTurtleSoupNoSignal:
    def test_flat_market_raises_non_actionable(self):
        with pytest.raises(ValueError, match="non-actionable"):
            order_package({"symbol": "BTCUSDT"}, candles_df=_flat_frame())

    def test_bar_with_low_body_to_range_raises(self):
        """Sweep present but body_to_range < min_body_to_range → no setup."""
        df = _bullish_sweep_frame()
        last = df.index[-1]
        # Compress the body: open ~= close, but keep the sweep wick.
        df.loc[last, "open"] = 50_045.0
        df.loc[last, "close"] = 50_050.0  # body = 5, range = 600 → ratio ≈ 0.008
        with pytest.raises(ValueError, match="non-actionable"):
            order_package({"symbol": "BTCUSDT"}, candles_df=df)


# ---------------------------------------------------------------------------
# Edge cases — input validation
# ---------------------------------------------------------------------------


class TestTurtleSoupEdgeCases:
    def test_none_candles_df_raises(self):
        with pytest.raises(ValueError):
            order_package({"symbol": "BTCUSDT"}, candles_df=None)

    def test_empty_dataframe_raises(self):
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        with pytest.raises(ValueError):
            order_package({"symbol": "BTCUSDT"}, candles_df=empty)

    def test_single_candle_raises(self):
        rng = pd.date_range("2026-04-01", periods=1, freq="15min", tz="UTC")
        df = pd.DataFrame(
            {
                "open": [50_000.0],
                "high": [50_100.0],
                "low": [49_900.0],
                "close": [50_050.0],
                "volume": [1.0],
            },
            index=rng,
        )
        with pytest.raises(ValueError, match="at least"):
            order_package({"symbol": "BTCUSDT"}, candles_df=df)

    def test_too_few_candles_raises(self):
        # Default lookback 60 + ATR period 14 + 2 = 76 needed
        df = _flat_frame(n=20)
        with pytest.raises(ValueError, match="at least"):
            order_package({"symbol": "BTCUSDT"}, candles_df=df)

    def test_all_zero_volume_does_not_crash(self):
        """Volume column is unused by setup detection — must not crash on zeros."""
        df = _bullish_sweep_frame()
        df["volume"] = 0.0
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=df)
        assert pkg["direction"] == "long"


# ---------------------------------------------------------------------------
# Cfg parameter overrides
# ---------------------------------------------------------------------------


class TestTurtleSoupCfgOverrides:
    def test_min_body_to_range_override_blocks_signal(self):
        """Raising min_body_to_range above the bar's actual ratio kills the signal."""
        df = _bullish_sweep_frame()
        # Force the threshold above any plausible body/range.
        with pytest.raises(ValueError, match="non-actionable"):
            order_package(
                {"symbol": "BTCUSDT", "min_body_to_range": 0.99},
                candles_df=df,
            )

    def test_atr_stop_mult_override_widens_sl(self):
        """A larger atr_stop_mult must produce a more-distant stop for longs."""
        df = _bullish_sweep_frame()
        pkg_default = order_package({"symbol": "BTCUSDT"}, candles_df=df)
        pkg_wider = order_package(
            {"symbol": "BTCUSDT", "atr_stop_mult": 1.0},
            candles_df=df,
        )
        # Long → wider stop is lower (further from entry).
        assert pkg_wider["sl"] < pkg_default["sl"]

    def test_tp1_at_r_override_changes_tp(self):
        df = _bullish_sweep_frame()
        pkg_default = order_package({"symbol": "BTCUSDT"}, candles_df=df)
        pkg_doubled = order_package(
            {"symbol": "BTCUSDT", "tp1_at_r": 2.5},
            candles_df=df,
        )
        # Long → larger tp1_at_r pushes TP further above entry.
        assert pkg_doubled["tp"] > pkg_default["tp"]

    def test_symbol_default_when_not_in_cfg(self):
        pkg = order_package({}, candles_df=_bullish_sweep_frame())
        assert pkg["symbol"] == "BTCUSDT"

    def test_symbol_from_cfg_propagates(self):
        pkg = order_package({"symbol": "ETHUSDT"}, candles_df=_bullish_sweep_frame())
        assert pkg["symbol"] == "ETHUSDT"


# ---------------------------------------------------------------------------
# Coordinator integration
# ---------------------------------------------------------------------------


class TestTurtleSoupViaCoordinator:
    def test_dispatch_via_coordinator_strategy_order_pkg(self):
        from src.core.coordinator import Coordinator, OrderPackage

        c = Coordinator()
        pkg = c.strategy_order_pkg("turtle_soup", symbol="BTCUSDT", candles_df=_bullish_sweep_frame())
        assert isinstance(pkg, OrderPackage)
        assert pkg.strategy == "turtle_soup"
        assert pkg.direction == "long"
        assert pkg.symbol == "BTCUSDT"
