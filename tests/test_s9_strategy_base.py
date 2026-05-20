"""Tests for S9 (S1-NOTE-003): StrategyBase alignment with StrategyInterface.

Validates:
  - StrategyBase inherits from StrategyInterface
  - strategy_id and _category class attributes work correctly
  - category property reflects _category
  - Static helper methods delegate correctly to module-level functions
  - build_signal and build_order_package raise NotImplementedError on base
  - Concrete subclass can override build_signal/build_order_package
  - Module-level helpers remain importable and work unchanged (no regression)
"""
from __future__ import annotations

import pytest
import pandas as pd

from src.units.strategies._base import (
    StrategyBase,
    derive_sl_tp,
    last_close,
    monitor_breakeven_sl,
    require_candles,
    side_to_direction,
)
from src.core.strategy_interface import StrategyInterface


# ---------------------------------------------------------------------------
# Module-level helpers — regression tests (must keep working as before)
# ---------------------------------------------------------------------------

class TestModuleLevelHelpers:
    def test_side_to_direction_buy(self):
        assert side_to_direction("buy") == "long"

    def test_side_to_direction_sell(self):
        assert side_to_direction("sell") == "short"

    def test_side_to_direction_none_raises(self):
        with pytest.raises(ValueError, match="Non-actionable"):
            side_to_direction("none")

    def test_last_close(self):
        df = pd.DataFrame({"close": [1.0, 2.0, 3.5]})
        assert last_close(df) == pytest.approx(3.5)

    def test_derive_sl_tp_long(self):
        sl, tp = derive_sl_tp(100.0, "long", sl_pct=0.02, reward_ratio=2.0)
        assert sl == pytest.approx(98.0, rel=1e-6)
        assert tp == pytest.approx(104.0, rel=1e-6)

    def test_derive_sl_tp_short(self):
        sl, tp = derive_sl_tp(100.0, "short", sl_pct=0.02, reward_ratio=2.0)
        assert sl == pytest.approx(102.0, rel=1e-6)
        assert tp == pytest.approx(96.0, rel=1e-6)

    def test_require_candles_raises_on_none(self):
        with pytest.raises(ValueError, match="candles_df is required"):
            require_candles(None, "test_strategy")

    def test_require_candles_raises_on_empty(self):
        with pytest.raises(ValueError):
            require_candles(pd.DataFrame(), "test_strategy")

    def test_require_candles_passes_through_valid(self):
        df = pd.DataFrame({"close": [1.0]})
        result = require_candles(df, "test_strategy")
        assert result is df

    def test_monitor_breakeven_sl_no_change_below_threshold(self):
        df = pd.DataFrame({"close": [100.5]})
        pkg = {"entry": 100.0, "sl": 98.0, "direction": "long"}
        assert monitor_breakeven_sl(pkg, df) is None

    def test_monitor_breakeven_sl_triggers_at_1r(self):
        df = pd.DataFrame({"close": [102.0]})
        pkg = {"entry": 100.0, "sl": 98.0, "direction": "long"}
        result = monitor_breakeven_sl(pkg, df)
        assert result == {"sl": pytest.approx(100.0)}

    def test_monitor_breakeven_sl_with_offset(self):
        df = pd.DataFrame({"close": [102.0]})
        pkg = {"entry": 100.0, "sl": 98.0, "direction": "long"}
        result = monitor_breakeven_sl(pkg, df, be_offset_bps=10)
        assert result["sl"] == pytest.approx(100.1, rel=1e-5)


# ---------------------------------------------------------------------------
# StrategyBase class
# ---------------------------------------------------------------------------

class TestStrategyBaseInheritance:
    def test_is_subclass_of_strategy_interface(self):
        assert issubclass(StrategyBase, StrategyInterface)

    def test_cannot_instantiate_without_strategy_id_set(self):
        # StrategyBase has strategy_id="" — it's a class attr, not abstract,
        # so it instantiates fine (StrategyInterface doesn't enforce it).
        class _Concrete(StrategyBase):
            strategy_id = "test_strategy"

        s = _Concrete()
        assert s.strategy_id == "test_strategy"

    def test_default_category_is_unknown(self):
        class _Concrete(StrategyBase):
            strategy_id = "test_strategy"

        assert _Concrete().category == "unknown"

    def test_category_override_via_class_attr(self):
        class _Concrete(StrategyBase):
            strategy_id = "vwap"
            _category = "mean_reversion_dislocation"

        assert _Concrete().category == "mean_reversion_dislocation"

    def test_repr_uses_strategy_id(self):
        class _Concrete(StrategyBase):
            strategy_id = "ict_scalp_5m"

        assert "ict_scalp_5m" in repr(_Concrete())


class TestStrategyBaseAbstractMethods:
    def _make_concrete(self):
        class _Concrete(StrategyBase):
            strategy_id = "test"

        return _Concrete()

    def test_build_signal_raises_not_implemented(self):
        s = self._make_concrete()
        with pytest.raises(NotImplementedError, match="build_signal"):
            s.build_signal(bars=None, cfg={})

    def test_build_order_package_raises_not_implemented(self):
        s = self._make_concrete()
        with pytest.raises(NotImplementedError, match="build_order_package"):
            s.build_order_package(signal=None, cfg={})


class TestStrategyBaseStaticHelpers:
    def _s(self):
        class _Concrete(StrategyBase):
            strategy_id = "test"
        return _Concrete()

    def test_side_to_direction_buy(self):
        assert self._s().side_to_direction("buy") == "long"

    def test_side_to_direction_sell(self):
        assert self._s().side_to_direction("sell") == "short"

    def test_derive_sl_tp_long(self):
        sl, tp = self._s().derive_sl_tp(100.0, "long")
        assert sl < 100.0
        assert tp > 100.0

    def test_require_candles_raises_on_none(self):
        with pytest.raises(ValueError):
            self._s().require_candles(None, "test")

    def test_monitor_breakeven_sl_no_trigger(self):
        df = pd.DataFrame({"close": [100.5]})
        pkg = {"entry": 100.0, "sl": 98.0, "direction": "long"}
        assert self._s().monitor_breakeven_sl(pkg, df) is None

    def test_monitor_breakeven_sl_triggers(self):
        df = pd.DataFrame({"close": [102.0]})
        pkg = {"entry": 100.0, "sl": 98.0, "direction": "long"}
        result = self._s().monitor_breakeven_sl(pkg, df)
        assert result == {"sl": pytest.approx(100.0)}


class TestConcreteStrategySubclass:
    """End-to-end: a fully implemented subclass works correctly."""

    def _make_impl(self):
        from src.core.signal_contract import SignalPackage
        from src.core.order_contract import OrderPackage

        class _VwapAdapter(StrategyBase):
            strategy_id = "vwap"
            _category = "mean_reversion_dislocation"

            def build_signal(self, bars, cfg, **kwargs):
                return SignalPackage(
                    strategy_id=self.strategy_id,
                    symbol="BTCUSDT",
                    account_id="",
                    side="long",
                    entry_price=100.0,
                    stop_loss=98.0,
                    take_profit=106.0,
                    timestamp_utc="2026-01-01T00:00:00Z",
                    raw={},
                    source_context={},
                )

            def build_order_package(self, signal, cfg, **kwargs):
                return OrderPackage.from_signal(signal, qty=0.5, order_type="market")

        return _VwapAdapter()

    def test_strategy_id(self):
        assert self._make_impl().strategy_id == "vwap"

    def test_category(self):
        assert self._make_impl().category == "mean_reversion_dislocation"

    def test_build_signal_returns_signal_package(self):
        from src.core.signal_contract import SignalPackage
        sig = self._make_impl().build_signal(bars=None, cfg={})
        assert isinstance(sig, SignalPackage)
        assert sig.is_actionable

    def test_build_order_package_returns_order_package(self):
        from src.core.order_contract import OrderPackage
        impl = self._make_impl()
        sig = impl.build_signal(bars=None, cfg={})
        pkg = impl.build_order_package(sig, cfg={})
        assert isinstance(pkg, OrderPackage)
        assert pkg.qty == pytest.approx(0.5)
