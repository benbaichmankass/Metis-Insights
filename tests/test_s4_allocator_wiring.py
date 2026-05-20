"""S4 tests — allocator wiring: coordinator.allocator property + build_order_packages().

Tests verify:
1. coordinator.allocator returns an AllocatorInterface instance.
2. coordinator.allocator is the same instance on repeated calls (lazy-init cached).
3. PassthroughAllocator.allocate() sizes actionable signals into OrderPackages.
4. PassthroughAllocator skips non-actionable (side=none) signals.
5. PassthroughAllocator skips signals with no valid stop-loss distance.
6. coordinator.build_order_packages() delegates correctly to the allocator.
7. OrderPackage fields are correctly populated from SignalPackage.
8. with_account() binding flows through to OrderPackage.account_id.
"""
from __future__ import annotations

from src.core.allocator import AllocatorInterface, PassthroughAllocator
from src.core.order_contract import OrderPackage
from src.core.signal_contract import SignalPackage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(
    strategy_id: str = "test_strat",
    symbol: str = "BTCUSDT",
    side: str = "long",
    entry: float = 100.0,
    sl: float = 95.0,
    tp: float = 110.0,
    account_id: str = "bybit_1",
) -> SignalPackage:
    from datetime import datetime, timezone
    return SignalPackage(
        strategy_id=strategy_id,
        symbol=symbol,
        account_id=account_id,
        side=side,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
    )


def _portfolio(balance: float = 1000.0, risk_pct: float = 0.01) -> dict:
    return {
        "balance": balance,
        "risk_pct_by_strategy": {"test_strat": risk_pct},
    }


# ---------------------------------------------------------------------------
# PassthroughAllocator
# ---------------------------------------------------------------------------

class TestPassthroughAllocator:
    def test_is_allocator_interface(self):
        assert isinstance(PassthroughAllocator(), AllocatorInterface)

    def test_actionable_long_produces_order_package(self):
        alloc = PassthroughAllocator()
        sig = _make_signal(side="long", entry=100.0, sl=95.0)
        pkgs = alloc.allocate([sig], _portfolio(balance=1000.0, risk_pct=0.01))
        assert len(pkgs) == 1
        assert isinstance(pkgs[0], OrderPackage)

    def test_qty_formula_balance_times_risk_over_sl_distance(self):
        # balance=1000, risk_pct=0.01 → risk_usd=10; sl_distance=5 → qty=2.0
        alloc = PassthroughAllocator()
        sig = _make_signal(side="long", entry=100.0, sl=95.0)
        pkgs = alloc.allocate([sig], _portfolio(balance=1000.0, risk_pct=0.01))
        assert pkgs[0].qty == 2.0

    def test_none_side_skipped(self):
        alloc = PassthroughAllocator()
        sig = _make_signal(side="none", entry=None, sl=None, tp=None)
        pkgs = alloc.allocate([sig], _portfolio())
        assert pkgs == []

    def test_missing_sl_skipped(self):
        alloc = PassthroughAllocator()
        from datetime import datetime, timezone
        sig = SignalPackage(
            strategy_id="test",
            symbol="BTCUSDT",
            account_id="bybit_1",
            side="long",
            entry_price=100.0,
            stop_loss=None,
            take_profit=110.0,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )
        pkgs = alloc.allocate([sig], _portfolio())
        assert pkgs == []

    def test_zero_sl_distance_skipped(self):
        alloc = PassthroughAllocator()
        # entry == sl → distance=0
        sig = _make_signal(side="long", entry=100.0, sl=100.0)
        pkgs = alloc.allocate([sig], _portfolio())
        assert pkgs == []

    def test_multiple_signals_all_sized(self):
        alloc = PassthroughAllocator()
        sigs = [
            _make_signal(strategy_id="vwap", side="long", entry=100.0, sl=95.0),
            _make_signal(strategy_id="turtle_soup", side="short", entry=100.0, sl=105.0),
        ]
        portfolio = {
            "balance": 1000.0,
            "risk_pct_by_strategy": {"vwap": 0.01, "turtle_soup": 0.02},
        }
        pkgs = alloc.allocate(sigs, portfolio)
        assert len(pkgs) == 2

    def test_default_risk_pct_used_when_strategy_not_in_map(self):
        # Strategy not in risk_pct_by_strategy — falls back to 0.005
        alloc = PassthroughAllocator()
        sig = _make_signal(strategy_id="unknown_strat", side="long", entry=100.0, sl=90.0)
        pkgs = alloc.allocate([sig], {"balance": 1000.0, "risk_pct_by_strategy": {}})
        # risk_usd = 1000 * 0.005 = 5; sl_distance=10 → qty=0.5
        assert pkgs[0].qty == 0.5

    def test_order_package_account_id_from_signal(self):
        alloc = PassthroughAllocator()
        sig = _make_signal(side="long", entry=100.0, sl=95.0, account_id="bybit_2")
        pkgs = alloc.allocate([sig], _portfolio())
        assert pkgs[0].account_id == "bybit_2"

    def test_order_package_side_matches_signal(self):
        alloc = PassthroughAllocator()
        sig = _make_signal(side="short", entry=100.0, sl=105.0)
        pkgs = alloc.allocate([sig], _portfolio())
        assert pkgs[0].side == "short"

    def test_order_type_is_limit_by_default(self):
        alloc = PassthroughAllocator()
        sig = _make_signal(side="long", entry=100.0, sl=95.0)
        pkgs = alloc.allocate([sig], _portfolio())
        assert pkgs[0].order_type == "limit"

    def test_attribution_carries_strategy_id(self):
        alloc = PassthroughAllocator()
        sig = _make_signal(strategy_id="vwap", side="long", entry=100.0, sl=95.0)
        pkgs = alloc.allocate([sig], _portfolio(risk_pct=0.01))
        assert pkgs[0].attribution["strategy_id"] == "vwap"


# ---------------------------------------------------------------------------
# coordinator.allocator property + build_order_packages
# ---------------------------------------------------------------------------

class TestCoordinatorAllocatorWiring:
    def test_allocator_property_returns_allocator_interface(self):
        from src.core.coordinator import Coordinator
        coord = Coordinator()
        assert isinstance(coord.allocator, AllocatorInterface)

    def test_allocator_is_passthrough_by_default(self):
        from src.core.coordinator import Coordinator
        coord = Coordinator()
        assert isinstance(coord.allocator, PassthroughAllocator)

    def test_allocator_is_cached(self):
        from src.core.coordinator import Coordinator
        coord = Coordinator()
        a1 = coord.allocator
        a2 = coord.allocator
        assert a1 is a2

    def test_build_order_packages_returns_list(self):
        from src.core.coordinator import Coordinator
        coord = Coordinator()
        sig = _make_signal(side="long", entry=100.0, sl=95.0)
        pkgs = coord.build_order_packages([sig], _portfolio())
        assert isinstance(pkgs, list)

    def test_build_order_packages_actionable_signal(self):
        from src.core.coordinator import Coordinator
        coord = Coordinator()
        sig = _make_signal(side="long", entry=100.0, sl=95.0)
        pkgs = coord.build_order_packages([sig], _portfolio(balance=1000.0, risk_pct=0.01))
        assert len(pkgs) == 1
        assert pkgs[0].qty == 2.0

    def test_build_order_packages_empty_on_none_signal(self):
        from src.core.coordinator import Coordinator
        coord = Coordinator()
        sig = _make_signal(side="none", entry=None, sl=None, tp=None)
        pkgs = coord.build_order_packages([sig], _portfolio())
        assert pkgs == []

    def test_with_account_binding_flows_through(self):
        from src.core.coordinator import Coordinator
        coord = Coordinator()
        sig = _make_signal(side="long", entry=100.0, sl=95.0, account_id="")
        bound = sig.with_account("bybit_1")
        pkgs = coord.build_order_packages([bound], _portfolio())
        assert pkgs[0].account_id == "bybit_1"
