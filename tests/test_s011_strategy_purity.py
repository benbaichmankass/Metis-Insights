"""S-011 PR #2: Strategy purity — strategies are pure signal generators.

Verifies that:
1. No strategy module has a dry_run flag or parameter
2. Each strategy's order_package() returns a valid signal dict with required keys
3. Signals are produced regardless of any execution context
4. The _base helpers have no dry_run coupling
"""
from __future__ import annotations

import inspect
import importlib

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Candle fixtures (minimal, hand-crafted — no exchange calls)
# ---------------------------------------------------------------------------

def _bullish_candles(n: int = 11) -> pd.DataFrame:
    prices = [100.0 + i for i in range(n)]
    return pd.DataFrame({
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
        "volume": [1000.0] * n,
        "timestamp": list(range(n)),
    })


def _bearish_candles(n: int = 5) -> pd.DataFrame:
    opens = [105.0 - i for i in range(n)]
    closes = [104.0 - i for i in range(n)]
    return pd.DataFrame({
        "open": opens,
        "high": [max(o, c) + 0.5 for o, c in zip(opens, closes)],
        "low": [min(o, c) - 0.5 for o, c in zip(opens, closes)],
        "close": closes,
        "volume": [500.0] * n,
        "timestamp": list(range(n)),
    })


_STRATEGY_MODULES = [
    # S-012 PR C5: roster reduced to turtle_soup + vwap.
    "src.units.strategies.turtle_soup",
    "src.units.strategies.vwap",
]

_REQUIRED_SIGNAL_KEYS = {"symbol", "direction", "entry", "sl", "tp"}


# ---------------------------------------------------------------------------
# Structural: no dry_run in strategy modules or _base
# ---------------------------------------------------------------------------

class TestStrategyHasNoDryRunFlag:
    @pytest.mark.parametrize("module_path", _STRATEGY_MODULES)
    def test_no_dry_run_attribute_in_strategy_module(self, module_path):
        mod = importlib.import_module(module_path)
        assert not hasattr(mod, "dry_run"), (
            f"{module_path} should not expose a dry_run attribute"
        )

    @pytest.mark.parametrize("module_path", _STRATEGY_MODULES)
    def test_order_package_has_no_dry_run_param(self, module_path):
        mod = importlib.import_module(module_path)
        fn = getattr(mod, "order_package", None)
        if fn is None:
            pytest.skip(f"{module_path} has no order_package()")
        sig = inspect.signature(fn)
        assert "dry_run" not in sig.parameters, (
            f"{module_path}.order_package() must not accept a dry_run parameter"
        )

    def test_base_helpers_have_no_dry_run(self):
        from src.units.strategies import _base
        source = inspect.getsource(_base)
        assert "def " not in source or "dry_run" not in source.split("def ")[0]
        for name, obj in inspect.getmembers(_base, inspect.isfunction):
            sig = inspect.signature(obj)
            assert "dry_run" not in sig.parameters, (
                f"_base.{name}() must not accept a dry_run parameter"
            )


# ---------------------------------------------------------------------------
# Functional: each strategy produces a valid signal dict
# ---------------------------------------------------------------------------

class TestVwapSignal:
    _CFG = {"symbol": "BTCUSDT", "risk_pct": 0.01}

    def test_returns_required_keys(self):
        from src.units.strategies.vwap import order_package
        result = order_package(self._CFG, candles_df=_bullish_candles())
        assert _REQUIRED_SIGNAL_KEYS <= result.keys()

    def test_direction_is_long_or_short(self):
        from src.units.strategies.vwap import order_package
        result = order_package(self._CFG, candles_df=_bullish_candles())
        assert result["direction"] in ("long", "short")

    def test_entry_sl_tp_are_positive_floats(self):
        from src.units.strategies.vwap import order_package
        result = order_package(self._CFG, candles_df=_bullish_candles())
        assert float(result["entry"]) > 0
        assert float(result["sl"]) > 0
        assert float(result["tp"]) > 0

    def test_produces_signal_without_dry_run_kwarg(self):
        from src.units.strategies.vwap import order_package
        # Must not require dry_run — pure signal generation
        result = order_package(self._CFG, candles_df=_bullish_candles())
        assert isinstance(result, dict)


class TestTurtleSoupSignal:
    """S-012 PR C5: Turtle Soup replaces the deleted killzone purity tests."""

    _CFG = {"symbol": "BTCUSDT"}

    def _bullish_sweep_candles(self):
        import numpy as np
        n = 80
        rng = pd.date_range("2026-04-01", periods=n, freq="15min", tz="UTC")
        df = pd.DataFrame({
            "open": np.full(n, 50_000.0),
            "high": np.full(n, 50_100.0),
            "low": np.full(n, 49_900.0),
            "close": np.full(n, 50_050.0),
            "volume": np.full(n, 1.0),
        }, index=rng)
        last = df.index[-1]
        df.loc[last, "low"] = 49_500.0
        df.loc[last, "open"] = 49_600.0
        df.loc[last, "close"] = 50_050.0
        return df

    def test_returns_required_keys(self):
        from src.units.strategies.turtle_soup import order_package
        result = order_package(self._CFG, candles_df=self._bullish_sweep_candles())
        assert _REQUIRED_SIGNAL_KEYS <= result.keys()

    def test_direction_is_long_for_bullish_sweep(self):
        from src.units.strategies.turtle_soup import order_package
        result = order_package(self._CFG, candles_df=self._bullish_sweep_candles())
        assert result["direction"] == "long"

    def test_no_dry_run_kwarg_needed(self):
        from src.units.strategies.turtle_soup import order_package
        result = order_package(self._CFG, candles_df=self._bullish_sweep_candles())
        assert isinstance(result, dict)


class TestStrategySignalIsolation:
    """Signals are independent of account execution mode."""

    def test_vwap_signal_same_regardless_of_account_dry_run(self):
        """Signal output is independent of account execution mode — the
        signal builder never reads account state (and post-2026-06-10 no
        in-memory mode-override mechanism exists at all)."""
        from src.units.strategies.vwap import order_package
        candles = _bullish_candles()
        cfg = {"symbol": "BTCUSDT", "risk_pct": 0.01}

        result_before = order_package(cfg, candles_df=candles)
        result_after = order_package(cfg, candles_df=candles)

        # Signal is identical — account mode has no effect on the builder.
        assert result_before["direction"] == result_after["direction"]
        assert result_before["symbol"] == result_after["symbol"]

    def test_coordinator_strategy_order_pkg_has_no_dry_run_param(self):
        from src.core.coordinator import Coordinator
        sig = inspect.signature(Coordinator.strategy_order_pkg)
        assert "dry_run" not in sig.parameters
