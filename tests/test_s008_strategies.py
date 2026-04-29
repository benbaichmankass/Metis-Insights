"""S-008 PR #121: units/strategies order_package() tests.

Fully offline — hand-crafted DataFrames only, no exchange calls, no DB.
Tests cover the four strategy adapters + Coordinator.strategy_order_pkg()
end-to-end wiring.
"""
from __future__ import annotations

import textwrap
from typing import Any, Dict

import pandas as pd
import pytest

from src.core.coordinator import Coordinator, OrderPackage, _PAUSED_ACCOUNTS


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

UNITS_YAML = textwrap.dedent("""\
    units:
      strategies:
        - name: ict
          service: ict-trader-ict
          model: null
          signal_prefixes: [fvg, ob, ict]
        - name: vwap
          service: ict-trader-vwap
          model: null
          signal_prefixes: [vwap]
        - name: breakout_confirmation
          service: ict-trader-breakout
          model: btc_v1.joblib
          signal_prefixes: [ml_breakout, breakout]
        - name: killzone
          service: ict-trader-live
          model: null
          signal_prefixes: [killzone, trade_signal]
      accounts:
        - id: live
          exchange: bybit
          risk_pct: 0.01
          env_path: .env
          strategies: [ict, vwap, breakout_confirmation, killzone]
      dashboards:
        db:
          trade_journal: trade_journal.db
          signals: data/trades.db
      return_commands:
        supported: []
      telegram_bot:
        data_source: dashboards
      app:
        config_enabled: true
      trading_school:
        auto_backtest: true
      db:
        trade_journal: trade_journal.db
        signals: data/trades.db
      workflows:
        docs: docs/claude/
""")


@pytest.fixture()
def units_yaml(tmp_path):
    p = tmp_path / "units.yaml"
    p.write_text(UNITS_YAML)
    return str(p)


@pytest.fixture()
def coord(units_yaml, tmp_path):
    _PAUSED_ACCOUNTS.clear()
    # S-012 PR B3: pass non-existent accounts_path so synthetic
    # units.yaml::accounts is honored.
    return Coordinator(
        units_path=units_yaml,
        accounts_path=str(tmp_path / "no-accounts.yaml"),
    )


def _make_candles(n: int = 50, base: float = 50_000.0, bullish: bool = True) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame with DatetimeIndex."""
    import numpy as np
    rng = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    step = 100.0 if bullish else -100.0
    closes = [base + i * step for i in range(n)]
    data = {
        "open": [c - 50 for c in closes],
        "high": [c + 100 for c in closes],
        "low": [c - 100 for c in closes],
        "close": closes,
        "volume": [10.0 + i for i in range(n)],
    }
    return pd.DataFrame(data, index=rng)


# ---------------------------------------------------------------------------
# _base helpers
# ---------------------------------------------------------------------------


class TestBase:
    def test_side_to_direction_buy(self):
        from src.units.strategies._base import side_to_direction
        assert side_to_direction("buy") == "long"

    def test_side_to_direction_sell(self):
        from src.units.strategies._base import side_to_direction
        assert side_to_direction("sell") == "short"

    def test_side_to_direction_none_raises(self):
        from src.units.strategies._base import side_to_direction
        with pytest.raises(ValueError):
            side_to_direction("none")

    def test_derive_sl_tp_long(self):
        from src.units.strategies._base import derive_sl_tp
        sl, tp = derive_sl_tp(50_000.0, "long", sl_pct=0.02, reward_ratio=2.0)
        assert sl < 50_000.0
        assert tp > 50_000.0
        assert abs(tp - 50_000.0) == pytest.approx(abs(50_000.0 - sl) * 2, rel=1e-6)

    def test_derive_sl_tp_short(self):
        from src.units.strategies._base import derive_sl_tp
        sl, tp = derive_sl_tp(50_000.0, "short", sl_pct=0.02, reward_ratio=2.0)
        assert sl > 50_000.0
        assert tp < 50_000.0

    def test_require_candles_raises_when_none(self):
        from src.units.strategies._base import require_candles
        with pytest.raises(ValueError, match="candles_df is required"):
            require_candles(None, "test_strat")

    def test_require_candles_raises_when_empty(self):
        from src.units.strategies._base import require_candles
        with pytest.raises(ValueError):
            require_candles(pd.DataFrame(), "test_strat")

    def test_require_candles_returns_df(self):
        from src.units.strategies._base import require_candles
        df = _make_candles(10)
        result = require_candles(df, "test_strat")
        assert result is df


# ---------------------------------------------------------------------------
# VWAP strategy adapter
# ---------------------------------------------------------------------------


class TestVwapOrderPackage:
    def _buy_candles(self) -> pd.DataFrame:
        """Price far below VWAP → buy signal."""
        rng = pd.date_range("2024-01-01", periods=20, freq="5min", tz="UTC")
        # High volume at high prices first, then low prices → VWAP above current
        data = {
            "open":  [100.0] * 10 + [50.0] * 10,
            "high":  [110.0] * 10 + [55.0] * 10,
            "low":   [90.0]  * 10 + [45.0] * 10,
            "close": [100.0] * 10 + [50.0] * 10,
            "volume":[1000.0] * 10 + [1.0] * 10,
        }
        return pd.DataFrame(data, index=rng)

    def _sell_candles(self) -> pd.DataFrame:
        """Price far above VWAP → sell signal."""
        rng = pd.date_range("2024-01-01", periods=20, freq="5min", tz="UTC")
        data = {
            "open":  [50.0]  * 10 + [100.0] * 10,
            "high":  [55.0]  * 10 + [110.0] * 10,
            "low":   [45.0]  * 10 + [90.0]  * 10,
            "close": [50.0]  * 10 + [100.0] * 10,
            "volume":[1000.0] * 10 + [1.0]  * 10,
        }
        return pd.DataFrame(data, index=rng)

    def test_requires_candles(self):
        from src.units.strategies.vwap import order_package
        with pytest.raises(ValueError):
            order_package({}, candles_df=None)

    def test_buy_signal_returns_long(self):
        from src.units.strategies.vwap import order_package
        df = self._buy_candles()
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=df)
        assert pkg["direction"] == "long"

    def test_sell_signal_returns_short(self):
        from src.units.strategies.vwap import order_package
        df = self._sell_candles()
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=df)
        assert pkg["direction"] == "short"

    def test_package_has_required_keys(self):
        from src.units.strategies.vwap import order_package
        df = self._buy_candles()
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=df)
        for key in ("symbol", "direction", "entry", "sl", "tp", "confidence", "meta"):
            assert key in pkg, f"Missing key: {key}"

    def test_sl_tp_valid_for_long(self):
        from src.units.strategies.vwap import order_package
        df = self._buy_candles()
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=df)
        assert pkg["sl"] < pkg["entry"]
        assert pkg["tp"] > pkg["sl"]

    def test_confidence_in_range(self):
        from src.units.strategies.vwap import order_package
        df = self._buy_candles()
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=df)
        assert 0.0 <= pkg["confidence"] <= 1.0

    def test_neutral_signal_raises_value_error(self):
        """Candles where price ≈ VWAP → side='none' → ValueError."""
        from src.units.strategies.vwap import order_package
        rng = pd.date_range("2024-01-01", periods=10, freq="5min", tz="UTC")
        data = {
            "open": [100.0] * 10, "high": [101.0] * 10,
            "low": [99.0] * 10,   "close": [100.0] * 10,
            "volume": [10.0] * 10,
        }
        df = pd.DataFrame(data, index=rng)
        with pytest.raises(ValueError):
            order_package({"symbol": "BTCUSDT"}, candles_df=df)


# ---------------------------------------------------------------------------
# Killzone strategy adapter
# ---------------------------------------------------------------------------


class TestKillzoneOrderPackage:
    def test_requires_candles_or_signal(self):
        from src.units.strategies.killzone import order_package
        with pytest.raises(ValueError):
            order_package({"symbol": "BTCUSDT"}, candles_df=None)

    def test_bullish_candle_returns_long(self):
        from src.units.strategies.killzone import order_package
        rng = pd.date_range("2024-01-01", periods=5, freq="5min", tz="UTC")
        df = pd.DataFrame({
            "open": [100.0] * 5, "high": [110.0] * 5,
            "low": [90.0] * 5,   "close": [105.0] * 5,
            "volume": [10.0] * 5,
        }, index=rng)
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=df)
        assert pkg["direction"] == "long"

    def test_bearish_candle_returns_short(self):
        from src.units.strategies.killzone import order_package
        rng = pd.date_range("2024-01-01", periods=5, freq="5min", tz="UTC")
        df = pd.DataFrame({
            "open": [110.0] * 5, "high": [115.0] * 5,
            "low": [90.0] * 5,   "close": [95.0] * 5,
            "volume": [10.0] * 5,
        }, index=rng)
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=df)
        assert pkg["direction"] == "short"

    def test_pre_built_signal_injection(self):
        """cfg['_signal'] bypasses candles entirely."""
        from src.units.strategies.killzone import order_package
        signal = {
            "symbol": "BTCUSDT", "side": "buy", "qty": 1.0,
            "meta": {"strategy_name": "killzone", "entry_price": 50000.0,
                     "stop_loss": 49000.0, "take_profit": 52000.0},
        }
        rng = pd.date_range("2024-01-01", periods=3, freq="5min", tz="UTC")
        df = pd.DataFrame({
            "open": [50000.0]*3, "high": [51000.0]*3,
            "low": [49000.0]*3, "close": [50500.0]*3,
            "volume": [10.0]*3,
        }, index=rng)
        pkg = order_package({"symbol": "BTCUSDT", "_signal": signal}, candles_df=df)
        assert pkg["direction"] == "long"
        assert pkg["sl"] == 49000.0
        assert pkg["tp"] == 52000.0

    def test_package_keys(self):
        from src.units.strategies.killzone import order_package
        rng = pd.date_range("2024-01-01", periods=5, freq="5min", tz="UTC")
        df = pd.DataFrame({
            "open": [100.0]*5, "high": [110.0]*5,
            "low": [90.0]*5, "close": [105.0]*5,
            "volume": [10.0]*5,
        }, index=rng)
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=df)
        for key in ("symbol", "direction", "entry", "sl", "tp", "confidence", "meta"):
            assert key in pkg

    def test_doji_raises(self):
        from src.units.strategies.killzone import order_package
        rng = pd.date_range("2024-01-01", periods=3, freq="5min", tz="UTC")
        df = pd.DataFrame({
            "open": [100.0]*3, "high": [110.0]*3,
            "low": [90.0]*3, "close": [100.0]*3,  # close == open
            "volume": [10.0]*3,
        }, index=rng)
        with pytest.raises(ValueError, match="doji"):
            order_package({"symbol": "BTCUSDT"}, candles_df=df)

    def test_confidence_is_08(self):
        from src.units.strategies.killzone import order_package
        rng = pd.date_range("2024-01-01", periods=3, freq="5min", tz="UTC")
        df = pd.DataFrame({
            "open": [100.0]*3, "high": [110.0]*3,
            "low": [90.0]*3, "close": [105.0]*3,
            "volume": [10.0]*3,
        }, index=rng)
        pkg = order_package({"symbol": "BTCUSDT"}, candles_df=df)
        assert pkg["confidence"] == 0.8


# ---------------------------------------------------------------------------
# Coordinator.strategy_order_pkg() end-to-end wiring
# ---------------------------------------------------------------------------


class TestCoordinatorStrategyOrderPkg:
    def test_vwap_end_to_end_returns_order_package(self, coord):
        """Coordinator.strategy_order_pkg('vwap', candles_df=...) → OrderPackage."""
        rng = pd.date_range("2024-01-01", periods=20, freq="5min", tz="UTC")
        data = {
            "open":  [100.0] * 10 + [50.0] * 10,
            "high":  [110.0] * 10 + [55.0] * 10,
            "low":   [90.0]  * 10 + [45.0] * 10,
            "close": [100.0] * 10 + [50.0] * 10,
            "volume":[1000.0] * 10 + [1.0]  * 10,
        }
        df = pd.DataFrame(data, index=rng)
        pkg = coord.strategy_order_pkg("vwap", symbol="BTCUSDT", candles_df=df)
        assert isinstance(pkg, OrderPackage)
        assert pkg.strategy == "vwap"
        assert pkg.direction in ("long", "short")

    def test_killzone_end_to_end(self, coord):
        rng = pd.date_range("2024-01-01", periods=5, freq="5min", tz="UTC")
        df = pd.DataFrame({
            "open": [100.0]*5, "high": [110.0]*5,
            "low": [90.0]*5,   "close": [105.0]*5,
            "volume": [10.0]*5,
        }, index=rng)
        pkg = coord.strategy_order_pkg("killzone", candles_df=df)
        assert isinstance(pkg, OrderPackage)
        assert pkg.strategy == "killzone"

    def test_unknown_strategy_raises_not_implemented(self, coord):
        with pytest.raises(NotImplementedError):
            coord.strategy_order_pkg("no_such_strategy_xyz")

    def test_neutral_signal_raises_value_error(self, coord):
        """Flat candles → VWAP signal is 'none' → ValueError from coordinator."""
        rng = pd.date_range("2024-01-01", periods=10, freq="5min", tz="UTC")
        df = pd.DataFrame({
            "open": [100.0]*10, "high": [101.0]*10,
            "low": [99.0]*10,   "close": [100.0]*10,
            "volume": [10.0]*10,
        }, index=rng)
        with pytest.raises(ValueError):
            coord.strategy_order_pkg("vwap", candles_df=df)

    def test_order_package_fields_populated(self, coord):
        rng = pd.date_range("2024-01-01", periods=20, freq="5min", tz="UTC")
        data = {
            "open":  [100.0]*10 + [50.0]*10, "high":  [110.0]*10 + [55.0]*10,
            "low":   [90.0]*10  + [45.0]*10, "close": [100.0]*10 + [50.0]*10,
            "volume":[1000.0]*10 + [1.0]*10,
        }
        df = pd.DataFrame(data, index=rng)
        pkg = coord.strategy_order_pkg("vwap", candles_df=df)
        assert pkg.entry > 0
        assert pkg.sl > 0
        assert pkg.tp > 0
        assert 0.0 <= pkg.confidence <= 1.0
        assert isinstance(pkg.meta, dict)
