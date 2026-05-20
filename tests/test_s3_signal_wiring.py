"""S3 tests — SignalPackage wiring into strategy signal builders.

Tests verify:
1. _with_signal_package() attaches a typed SignalPackage to any builder dict.
2. Side translation: "buy" -> "long", "sell" -> "short", other -> "none".
3. All existing dict keys are preserved (pipeline consumers unaffected).
4. SignalPackage.is_actionable, sl_distance, with_account work correctly.
5. "raw" field excludes the "signal_package" key itself (no self-reference).
6. "none"-side signals are not actionable and have no sl_distance.
"""
from __future__ import annotations

from src.core.signal_contract import SignalPackage
from src.runtime.strategy_signal_builders import _with_signal_package


# ---------------------------------------------------------------------------
# _with_signal_package helper
# ---------------------------------------------------------------------------

class TestWithSignalPackage:
    def _buy_dict(self) -> dict:
        return {
            "symbol": "BTCUSDT",
            "side": "buy",
            "price": 100.0,
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "meta": {"strategy_name": "test", "confidence": 0.8},
        }

    def _sell_dict(self) -> dict:
        return {
            "symbol": "BTCUSDT",
            "side": "sell",
            "price": 100.0,
            "entry_price": 100.0,
            "stop_loss": 105.0,
            "take_profit": 90.0,
            "meta": {"strategy_name": "test", "confidence": 0.7},
        }

    def _none_dict(self) -> dict:
        return {
            "symbol": "BTCUSDT",
            "side": "none",
            "meta": {"strategy_name": "test", "reason": "no_setup"},
        }

    # -- key presence and type --

    def test_adds_signal_package_key(self):
        sig = _with_signal_package("test_strat", self._buy_dict())
        assert "signal_package" in sig

    def test_signal_package_is_correct_type(self):
        sig = _with_signal_package("test_strat", self._buy_dict())
        assert isinstance(sig["signal_package"], SignalPackage)

    # -- side translation --

    def test_buy_translates_to_long(self):
        sig = _with_signal_package("test_strat", self._buy_dict())
        assert sig["signal_package"].side == "long"

    def test_sell_translates_to_short(self):
        sig = _with_signal_package("test_strat", self._sell_dict())
        assert sig["signal_package"].side == "short"

    def test_none_stays_none(self):
        sig = _with_signal_package("test_strat", self._none_dict())
        assert sig["signal_package"].side == "none"

    # -- existing dict keys preserved --

    def test_existing_keys_unchanged(self):
        original = self._buy_dict()
        sig = _with_signal_package("test_strat", original)
        assert sig["symbol"] == "BTCUSDT"
        assert sig["side"] == "buy"
        assert sig["price"] == 100.0
        assert sig["stop_loss"] == 95.0
        assert sig["take_profit"] == 110.0

    # -- SignalPackage field mapping --

    def test_strategy_id_set(self):
        sig = _with_signal_package("turtle_soup", self._buy_dict())
        assert sig["signal_package"].strategy_id == "turtle_soup"

    def test_symbol_set(self):
        sig = _with_signal_package("test", self._buy_dict())
        assert sig["signal_package"].symbol == "BTCUSDT"

    def test_entry_price_set(self):
        sig = _with_signal_package("test", self._buy_dict())
        assert sig["signal_package"].entry_price == 100.0

    def test_stop_loss_set(self):
        sig = _with_signal_package("test", self._buy_dict())
        assert sig["signal_package"].stop_loss == 95.0

    def test_take_profit_set(self):
        sig = _with_signal_package("test", self._buy_dict())
        assert sig["signal_package"].take_profit == 110.0

    def test_account_id_empty_before_s4_binding(self):
        sig = _with_signal_package("test", self._buy_dict())
        assert sig["signal_package"].account_id == ""

    def test_timestamp_utc_is_string(self):
        sig = _with_signal_package("test", self._buy_dict())
        assert isinstance(sig["signal_package"].timestamp_utc, str)
        assert "T" in sig["signal_package"].timestamp_utc

    # -- raw field --

    def test_raw_excludes_signal_package_key(self):
        sig = _with_signal_package("test", self._buy_dict())
        assert "signal_package" not in sig["signal_package"].raw

    def test_raw_preserves_original_keys(self):
        sig = _with_signal_package("test", self._buy_dict())
        assert sig["signal_package"].raw["side"] == "buy"
        assert sig["signal_package"].raw["price"] == 100.0

    # -- SignalPackage properties --

    def test_is_actionable_true_for_buy(self):
        sig = _with_signal_package("test", self._buy_dict())
        assert sig["signal_package"].is_actionable is True

    def test_is_actionable_false_for_none(self):
        sig = _with_signal_package("test", self._none_dict())
        assert sig["signal_package"].is_actionable is False

    def test_sl_distance_long(self):
        sig = _with_signal_package("test", self._buy_dict())
        # entry=100, sl=95 → distance=5
        assert sig["signal_package"].sl_distance == 5.0

    def test_sl_distance_short(self):
        sig = _with_signal_package("test", self._sell_dict())
        # entry=100, sl=105 → distance=5
        assert sig["signal_package"].sl_distance == 5.0

    def test_sl_distance_none_when_no_prices(self):
        sig = _with_signal_package("test", self._none_dict())
        assert sig["signal_package"].sl_distance is None

    def test_with_account_returns_new_package(self):
        sig = _with_signal_package("test", self._buy_dict())
        sp = sig["signal_package"]
        bound = sp.with_account("bybit_1")
        assert bound.account_id == "bybit_1"
        assert sp.account_id == ""  # original unchanged
