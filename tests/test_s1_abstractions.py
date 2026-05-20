"""S1 scaffold tests — covers all six new src/core/ abstract types.

All tests operate on the new types in isolation. No live runtime
components are imported. Safe to run in CI without network or secrets.
"""
from __future__ import annotations

import dataclasses
import pytest

from src.core.account_profile import AccountProfile
from src.core.instrument_profile import InstrumentProfile
from src.core.signal_contract import SignalPackage
from src.core.order_contract import OrderPackage
from src.core.allocator import PassthroughAllocator


# ---------------------------------------------------------------------------
# AccountProfile
# ---------------------------------------------------------------------------

class TestAccountProfile:
    def test_from_dict_bybit_live(self):
        data = {"exchange": "bybit", "dry_run": False}
        profile = AccountProfile.from_dict("bybit_2", data)
        assert profile.account_id == "bybit_2"
        assert profile.exchange == "bybit"
        assert profile.is_live is True
        assert profile.is_bybit is True
        assert profile.account_type == "bybit_live"

    def test_from_dict_bybit_demo(self):
        data = {"exchange": "bybit", "dry_run": True}
        profile = AccountProfile.from_dict("bybit_1", data)
        assert profile.is_demo is True
        assert profile.account_type == "bybit_demo"

    def test_frozen(self):
        data = {"exchange": "bybit", "dry_run": False}
        profile = AccountProfile.from_dict("bybit_2", data)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            profile.dry_run = True  # type: ignore[misc]

    def test_ib_paper_profile(self):
        data = {"exchange": "interactive_brokers", "dry_run": True}
        profile = AccountProfile.from_dict("ib_paper_1", data)
        assert profile.is_ib is True
        assert profile.account_type == "ib_paper"
        assert profile.is_live is False


# ---------------------------------------------------------------------------
# InstrumentProfile
# ---------------------------------------------------------------------------

class TestInstrumentProfile:
    def test_btcusdt_prebuilt(self):
        inst = InstrumentProfile.btcusdt_bybit_linear()
        assert inst.symbol == "BTCUSDT"
        assert inst.exchange == "bybit"
        assert inst.category == "linear"
        assert inst.is_crypto is True
        assert inst.is_futures is False

    def test_mes_prebuilt(self):
        inst = InstrumentProfile.mes_cme()
        assert inst.symbol == "MES"
        assert inst.exchange == "interactive_brokers"
        assert inst.category == "futures"
        assert inst.contract_value_usd == 5.0
        assert inst.is_futures is True
        assert inst.is_crypto is False

    def test_frozen(self):
        inst = InstrumentProfile.btcusdt_bybit_linear()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            inst.symbol = "ETHUSDT"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SignalPackage
# ---------------------------------------------------------------------------

class TestSignalPackage:
    def _make_signal(self, side="long", entry=100.0, sl=98.0, tp=104.0, account="bybit_1"):
        return SignalPackage(
            strategy_id="vwap",
            symbol="BTCUSDT",
            account_id=account,
            side=side,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            timestamp_utc="2026-05-20T00:00:00Z",
        )

    def test_is_actionable_true(self):
        sig = self._make_signal(side="long", entry=100.0)
        assert sig.is_actionable is True

    def test_is_actionable_false_when_side_none(self):
        sig = self._make_signal(side="none")
        assert sig.is_actionable is False

    def test_is_actionable_false_when_no_entry(self):
        sig = self._make_signal(entry=None)
        assert sig.is_actionable is False

    def test_with_account_copy(self):
        sig = self._make_signal(account="bybit_1")
        sig2 = sig.with_account("bybit_2")
        assert sig2.account_id == "bybit_2"
        assert sig.account_id == "bybit_1"  # original unchanged


# ---------------------------------------------------------------------------
# OrderPackage
# ---------------------------------------------------------------------------

class TestOrderPackage:
    def test_from_signal_preserves_attribution(self):
        sig = SignalPackage(
            strategy_id="turtle_soup",
            symbol="BTCUSDT",
            account_id="bybit_2",
            side="short",
            entry_price=50000.0,
            stop_loss=50500.0,
            take_profit=49000.0,
            timestamp_utc="2026-05-20T00:00:00Z",
            raw={"sweep_low": 49800.0},
        )
        order = OrderPackage.from_signal(sig, qty=0.01, order_type="limit")
        assert order.strategy_id == "turtle_soup"
        assert order.attribution["strategy_id"] == "turtle_soup"
        assert order.attribution["raw"] == {"sweep_low": 49800.0}
        assert order.qty == 0.01
        assert order.is_flat is False


# ---------------------------------------------------------------------------
# PassthroughAllocator
# ---------------------------------------------------------------------------

class TestPassthroughAllocator:
    def _make_signal(self, side="long", entry=100.0, sl=98.0, strategy_id="vwap"):
        return SignalPackage(
            strategy_id=strategy_id,
            symbol="BTCUSDT",
            account_id="bybit_1",
            side=side,
            entry_price=entry,
            stop_loss=sl,
            take_profit=104.0,
            timestamp_utc="2026-05-20T00:00:00Z",
        )

    def test_no_signals_returns_empty(self):
        alloc = PassthroughAllocator()
        result = alloc.allocate([], {"balance": 10000.0, "risk_pct_by_strategy": {}})
        assert result == []

    def test_flat_signal_skipped(self):
        alloc = PassthroughAllocator()
        sig = self._make_signal(side="none")
        result = alloc.allocate([sig], {"balance": 10000.0, "risk_pct_by_strategy": {}})
        assert result == []

    def test_qty_formula(self):
        # balance=10000, risk_pct=0.01 => risk_usd=100
        # entry=100, sl=98 => sl_distance=2
        # qty = 100 / 2 = 50.0
        alloc = PassthroughAllocator()
        sig = self._make_signal(side="long", entry=100.0, sl=98.0, strategy_id="vwap")
        portfolio = {
            "balance": 10000.0,
            "risk_pct_by_strategy": {"vwap": 0.01},
        }
        result = alloc.allocate([sig], portfolio)
        assert len(result) == 1
        assert result[0].qty == pytest.approx(50.0)

    def test_attribution_preserved_through_allocator(self):
        alloc = PassthroughAllocator()
        sig = self._make_signal(side="long", entry=100.0, sl=98.0, strategy_id="ict_scalp_5m")
        portfolio = {
            "balance": 10000.0,
            "risk_pct_by_strategy": {"ict_scalp_5m": 0.003},
        }
        result = alloc.allocate([sig], portfolio)
        assert result[0].attribution["strategy_id"] == "ict_scalp_5m"

    def test_multiple_signals_both_sized(self):
        alloc = PassthroughAllocator()
        sig1 = self._make_signal(side="long", entry=100.0, sl=98.0, strategy_id="vwap")
        sig2 = self._make_signal(side="short", entry=200.0, sl=202.0, strategy_id="turtle_soup")
        portfolio = {
            "balance": 10000.0,
            "risk_pct_by_strategy": {"vwap": 0.01, "turtle_soup": 0.005},
        }
        result = alloc.allocate([sig1, sig2], portfolio)
        assert len(result) == 2
        strat_ids = {r.strategy_id for r in result}
        assert strat_ids == {"vwap", "turtle_soup"}
