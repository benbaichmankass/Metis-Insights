"""S6: CENTRALIZED_ALLOCATOR primary path tests.

Verify the SignalPackage → allocator → OrderPackage chain that S6
promotes from shadow to primary. Tests stay in src.core.* so no
pipeline stubs are needed.
"""
import pytest

from src.core.allocator import PassthroughAllocator
from src.core.signal_contract import SignalPackage


def _make_sig(**overrides):
    defaults = dict(
        strategy_id="turtle_soup",
        symbol="BTCUSDT",
        account_id="bybit_1",
        side="long",
        entry_price=50_000.0,
        stop_loss=49_000.0,
        take_profit=52_000.0,
        timestamp_utc="2026-05-20T12:00:00Z",
        raw={"confidence": 0.75},
    )
    defaults.update(overrides)
    return SignalPackage(**defaults)


class TestSignalPackageContract:
    def test_is_actionable_long(self):
        assert _make_sig(side="long").is_actionable is True

    def test_is_actionable_short(self):
        assert _make_sig(side="short").is_actionable is True

    def test_not_actionable_none(self):
        assert _make_sig(side="none").is_actionable is False

    def test_not_actionable_no_entry(self):
        assert _make_sig(entry_price=None).is_actionable is False

    def test_sl_distance(self):
        sig = _make_sig(entry_price=50_000.0, stop_loss=49_000.0)
        assert sig.sl_distance == pytest.approx(1_000.0)

    def test_sl_distance_short(self):
        sig = _make_sig(side="short", entry_price=50_000.0, stop_loss=51_000.0)
        assert sig.sl_distance == pytest.approx(1_000.0)

    def test_sl_distance_none_when_sl_missing(self):
        assert _make_sig(stop_loss=None).sl_distance is None

    def test_side_is_already_long_short(self):
        assert _make_sig(side="long").side == "long"
        assert _make_sig(side="short").side == "short"


class TestPassthroughAllocatorChain:
    def test_positive_qty_from_real_portfolio_state(self):
        alloc = PassthroughAllocator()
        pkgs = alloc.allocate(
            [_make_sig()],
            {"balance": 10_000.0, "risk_pct_by_strategy": {"turtle_soup": 0.005}},
        )
        assert len(pkgs) == 1
        assert pkgs[0].qty > 0

    def test_qty_formula(self):
        # risk_usd = 10_000 * 0.01 = 100; sl_distance = 1_000; qty = 0.1
        alloc = PassthroughAllocator()
        pkgs = alloc.allocate(
            [_make_sig(entry_price=50_000.0, stop_loss=49_000.0)],
            {"balance": 10_000.0, "risk_pct_by_strategy": {"turtle_soup": 0.01}},
        )
        assert pkgs[0].qty == pytest.approx(0.1)

    def test_zero_balance_yields_no_packages(self):
        alloc = PassthroughAllocator()
        pkgs = alloc.allocate([_make_sig()], {"balance": 0.0})
        assert pkgs == []

    def test_non_actionable_skipped(self):
        alloc = PassthroughAllocator()
        pkgs = alloc.allocate([_make_sig(side="none")], {"balance": 10_000.0})
        assert pkgs == []

    def test_missing_sl_skipped(self):
        alloc = PassthroughAllocator()
        pkgs = alloc.allocate([_make_sig(stop_loss=None)], {"balance": 10_000.0})
        assert pkgs == []

    def test_strategy_id_preserved(self):
        alloc = PassthroughAllocator()
        pkgs = alloc.allocate(
            [_make_sig(strategy_id="vwap")],
            {"balance": 10_000.0, "risk_pct_by_strategy": {"vwap": 0.005}},
        )
        assert pkgs[0].strategy_id == "vwap"

    def test_symbol_preserved(self):
        alloc = PassthroughAllocator()
        pkgs = alloc.allocate(
            [_make_sig(symbol="ETHUSDT")],
            {"balance": 10_000.0},
        )
        assert pkgs[0].symbol == "ETHUSDT"

    def test_multiple_signals_first_wins(self):
        alloc = PassthroughAllocator()
        s1 = _make_sig(strategy_id="a")
        s2 = _make_sig(strategy_id="b")
        pkgs = alloc.allocate([s1, s2], {"balance": 10_000.0})
        assert len(pkgs) == 2
        assert pkgs[0].strategy_id == "a"
        assert pkgs[1].strategy_id == "b"
