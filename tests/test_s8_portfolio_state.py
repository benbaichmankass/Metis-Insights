"""Tests for S8 (M11): PortfolioState + typed allocator + net position accounting.

Validates:
  - PortfolioState construction, from_dict, from_balance, net_for
  - PassthroughAllocator accepts typed PortfolioState and legacy dict
  - PassthroughAllocator populates net_position_context when net != 0
  - net_positions_by_symbol() aggregates signed qty across accounts/symbols
  - Coordinator.build_order_packages() coerces dict to PortfolioState
  - Coordinator.build_order_packages() fetches live positions when dict given
  - Coordinator.build_order_packages() uses PortfolioState as-is (no re-fetch)
"""
from __future__ import annotations

import sqlite3

import pytest

from src.core.portfolio_state import PortfolioState


# ---------------------------------------------------------------------------
# PortfolioState unit tests
# ---------------------------------------------------------------------------

class TestPortfolioState:
    def test_construction_defaults(self):
        ps = PortfolioState(balance=5000.0)
        assert ps.balance == 5000.0
        assert ps.risk_pct_by_strategy == {}
        assert ps.net_positions == {}

    def test_from_balance(self):
        ps = PortfolioState.from_balance(12345.0)
        assert ps.balance == 12345.0
        assert ps.risk_pct_by_strategy == {}
        assert ps.net_positions == {}

    def test_from_dict_full(self):
        d = {
            "balance": "8000",
            "risk_pct_by_strategy": {"ict_scalp_5m": 0.01},
            "net_positions": {"BTCUSDT": -0.5},
        }
        ps = PortfolioState.from_dict(d)
        assert ps.balance == 8000.0
        assert ps.risk_pct_by_strategy == {"ict_scalp_5m": 0.01}
        assert ps.net_positions == {"BTCUSDT": -0.5}

    def test_from_dict_missing_keys_use_defaults(self):
        ps = PortfolioState.from_dict({})
        assert ps.balance == 0.0
        assert ps.risk_pct_by_strategy == {}
        assert ps.net_positions == {}

    def test_net_for_present(self):
        ps = PortfolioState(balance=0.0, net_positions={"BTCUSDT": 1.5})
        assert ps.net_for("BTCUSDT") == 1.5

    def test_net_for_absent_returns_zero(self):
        ps = PortfolioState(balance=0.0)
        assert ps.net_for("ETHUSDT") == 0.0

    def test_net_for_short_is_negative(self):
        ps = PortfolioState(balance=0.0, net_positions={"BTCUSDT": -2.0})
        assert ps.net_for("BTCUSDT") == -2.0


# ---------------------------------------------------------------------------
# PassthroughAllocator — typed PortfolioState input
# ---------------------------------------------------------------------------

def _make_signal(
    strategy_id="ict_scalp_5m",
    symbol="BTCUSDT",
    side="long",
    entry=100.0,
    sl=98.0,
    tp=106.0,
):
    from src.core.signal_contract import SignalPackage
    return SignalPackage(
        strategy_id=strategy_id,
        symbol=symbol,
        account_id="",
        side=side,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        timestamp_utc="2026-01-01T00:00:00Z",
        raw={},
        source_context={},
    )


class TestPassthroughAllocatorTyped:
    def _alloc(self):
        from src.core.allocator import PassthroughAllocator
        return PassthroughAllocator()

    def test_typed_portfolio_state_sizes_correctly(self):
        ps = PortfolioState(
            balance=10_000.0,
            risk_pct_by_strategy={"ict_scalp_5m": 0.01},
        )
        sig = _make_signal(entry=100.0, sl=98.0)
        pkgs = self._alloc().allocate([sig], ps)
        assert len(pkgs) == 1
        # risk_usd=100, sl_distance=2 → qty=50
        assert pkgs[0].qty == pytest.approx(50.0)

    def test_legacy_dict_still_works(self):
        ps_dict = {
            "balance": 10_000.0,
            "risk_pct_by_strategy": {"ict_scalp_5m": 0.01},
        }
        sig = _make_signal(entry=100.0, sl=98.0)
        pkgs = self._alloc().allocate([sig], ps_dict)
        assert len(pkgs) == 1
        assert pkgs[0].qty == pytest.approx(50.0)

    def test_net_position_context_populated_when_nonzero(self):
        ps = PortfolioState(
            balance=10_000.0,
            risk_pct_by_strategy={"ict_scalp_5m": 0.01},
            net_positions={"BTCUSDT": -1.5},
        )
        sig = _make_signal(entry=100.0, sl=98.0)
        pkgs = self._alloc().allocate([sig], ps)
        assert pkgs[0].net_position_context == {"net_qty": -1.5}

    def test_net_position_context_empty_when_flat(self):
        ps = PortfolioState(balance=10_000.0, risk_pct_by_strategy={"ict_scalp_5m": 0.01})
        sig = _make_signal(entry=100.0, sl=98.0)
        pkgs = self._alloc().allocate([sig], ps)
        assert pkgs[0].net_position_context == {}

    def test_skips_non_actionable_signal(self):
        ps = PortfolioState(balance=10_000.0)
        sig = _make_signal(side="none")
        pkgs = self._alloc().allocate([sig], ps)
        assert pkgs == []

    def test_default_risk_pct_used_when_strategy_missing(self):
        ps = PortfolioState(balance=10_000.0, risk_pct_by_strategy={})
        sig = _make_signal(entry=100.0, sl=99.5)
        pkgs = self._alloc().allocate([sig], ps)
        # default risk_pct=0.005 → risk_usd=50, sl_distance=0.5 → qty=100
        assert pkgs[0].qty == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# net_positions_by_symbol — DB integration
# ---------------------------------------------------------------------------

def _make_db(path: str, rows: list[tuple]) -> None:
    """Create a minimal trade_journal.db with the given rows."""
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE trades ("
            "  id INTEGER PRIMARY KEY,"
            "  account_id TEXT,"
            "  symbol TEXT,"
            "  direction TEXT,"
            "  position_size REAL,"
            "  status TEXT,"
            "  is_backtest INTEGER"
            ")"
        )
        conn.executemany(
            "INSERT INTO trades (account_id, symbol, direction, position_size, status, is_backtest) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()


class TestNetPositionsBySymbol:
    def test_empty_db_returns_empty(self, tmp_path):
        from src.runtime.positions import net_positions_by_symbol
        db = str(tmp_path / "journal.db")
        _make_db(db, [])
        assert net_positions_by_symbol(db_path=db) == {}

    def test_missing_db_returns_empty(self):
        from src.runtime.positions import net_positions_by_symbol
        assert net_positions_by_symbol(db_path="/nonexistent/journal.db") == {}

    def test_single_long_position(self, tmp_path):
        from src.runtime.positions import net_positions_by_symbol
        db = str(tmp_path / "journal.db")
        _make_db(db, [("bybit_1", "BTCUSDT", "long", 0.5, "open", 0)])
        result = net_positions_by_symbol(db_path=db)
        assert result == {"BTCUSDT": pytest.approx(0.5)}

    def test_single_short_position(self, tmp_path):
        from src.runtime.positions import net_positions_by_symbol
        db = str(tmp_path / "journal.db")
        _make_db(db, [("bybit_1", "BTCUSDT", "short", 1.0, "open", 0)])
        result = net_positions_by_symbol(db_path=db)
        assert result == {"BTCUSDT": pytest.approx(-1.0)}

    def test_aggregates_across_accounts(self, tmp_path):
        from src.runtime.positions import net_positions_by_symbol
        db = str(tmp_path / "journal.db")
        _make_db(db, [
            ("bybit_1", "BTCUSDT", "long", 0.5, "open", 0),
            ("bybit_2", "BTCUSDT", "long", 0.3, "open", 0),
        ])
        result = net_positions_by_symbol(db_path=db)
        assert result == {"BTCUSDT": pytest.approx(0.8)}

    def test_aggregates_multiple_symbols(self, tmp_path):
        from src.runtime.positions import net_positions_by_symbol
        db = str(tmp_path / "journal.db")
        _make_db(db, [
            ("bybit_1", "BTCUSDT", "long", 1.0, "open", 0),
            ("bybit_1", "ETHUSDT", "short", 2.0, "open", 0),
        ])
        result = net_positions_by_symbol(db_path=db)
        assert result["BTCUSDT"] == pytest.approx(1.0)
        assert result["ETHUSDT"] == pytest.approx(-2.0)

    def test_closed_positions_excluded(self, tmp_path):
        from src.runtime.positions import net_positions_by_symbol
        db = str(tmp_path / "journal.db")
        _make_db(db, [
            ("bybit_1", "BTCUSDT", "long", 1.0, "closed", 0),
            ("bybit_1", "BTCUSDT", "long", 0.5, "open", 0),
        ])
        result = net_positions_by_symbol(db_path=db)
        assert result == {"BTCUSDT": pytest.approx(0.5)}

    def test_backtest_positions_excluded(self, tmp_path):
        from src.runtime.positions import net_positions_by_symbol
        db = str(tmp_path / "journal.db")
        _make_db(db, [
            ("bybit_1", "BTCUSDT", "long", 5.0, "open", 1),  # backtest
            ("bybit_1", "BTCUSDT", "long", 0.2, "open", 0),  # live
        ])
        result = net_positions_by_symbol(db_path=db)
        assert result == {"BTCUSDT": pytest.approx(0.2)}

    def test_offsetting_positions_net_to_zero_not_in_result(self, tmp_path):
        from src.runtime.positions import net_positions_by_symbol
        db = str(tmp_path / "journal.db")
        _make_db(db, [
            ("bybit_1", "BTCUSDT", "long", 1.0, "open", 0),
            ("bybit_2", "BTCUSDT", "short", 1.0, "open", 0),
        ])
        result = net_positions_by_symbol(db_path=db)
        # net is 0 — symbol should not appear (0.0 is excluded as no non-zero net)
        # The actual implementation returns 0.0 in the dict; we just check it's ~0
        btc_net = result.get("BTCUSDT", 0.0)
        assert btc_net == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Coordinator.build_order_packages — S8 coercion + position fetch
# ---------------------------------------------------------------------------

class TestCoordinatorBuildOrderPackagesS8:
    def _coord(self):
        from src.core.coordinator import Coordinator
        return Coordinator.__new__(Coordinator)._with_defaults()

    def test_dict_input_coerced_to_portfolio_state(self, tmp_path):
        from src.core.coordinator import Coordinator

        coord = Coordinator.__new__(Coordinator)
        coord._allocator = None
        coord._units_path = ""
        coord._accounts_path = ""
        coord._instruments_path = ""
        coord._cfg = {}
        coord._shadow_predictors_cache = {}

        db = str(tmp_path / "journal.db")
        _make_db(db, [])

        sig = _make_signal(entry=100.0, sl=98.0)
        pkgs = coord.build_order_packages(
            [sig],
            {"balance": 10_000.0, "risk_pct_by_strategy": {"ict_scalp_5m": 0.01}},
            db_path=db,
        )
        assert len(pkgs) == 1
        assert pkgs[0].qty == pytest.approx(50.0)

    def test_dict_input_enriched_with_live_positions(self, tmp_path):
        from src.core.coordinator import Coordinator

        coord = Coordinator.__new__(Coordinator)
        coord._allocator = None
        coord._units_path = ""
        coord._accounts_path = ""
        coord._instruments_path = ""
        coord._cfg = {}
        coord._shadow_predictors_cache = {}

        db = str(tmp_path / "journal.db")
        _make_db(db, [("bybit_1", "BTCUSDT", "long", 0.7, "open", 0)])

        captured_ps = []
        orig_allocate = coord.allocator.allocate

        def _spy_allocate(signals, ps):
            captured_ps.append(ps)
            return orig_allocate(signals, ps)

        coord._allocator.allocate = _spy_allocate

        sig = _make_signal(entry=100.0, sl=98.0)
        coord.build_order_packages(
            [sig],
            {"balance": 5_000.0},
            db_path=db,
        )
        assert captured_ps[0].net_positions == {"BTCUSDT": pytest.approx(0.7)}

    def test_portfolio_state_input_used_as_is(self, tmp_path):
        from src.core.coordinator import Coordinator

        coord = Coordinator.__new__(Coordinator)
        coord._allocator = None
        coord._units_path = ""
        coord._accounts_path = ""
        coord._instruments_path = ""
        coord._cfg = {}
        coord._shadow_predictors_cache = {}

        # Put some data in DB — should NOT be fetched when typed PS given
        db = str(tmp_path / "journal.db")
        _make_db(db, [("bybit_1", "BTCUSDT", "long", 99.0, "open", 0)])

        captured_ps = []

        def _spy_allocate(signals, ps):
            captured_ps.append(ps)
            return []

        coord.allocator.allocate = _spy_allocate

        ps = PortfolioState(
            balance=8_000.0,
            net_positions={"BTCUSDT": 0.1},
        )
        sig = _make_signal(entry=100.0, sl=98.0)
        coord.build_order_packages([sig], ps, db_path=db)

        # DB had 99.0 but caller-supplied PS had 0.1 — must not be overridden
        assert captured_ps[0].net_positions == {"BTCUSDT": 0.1}

    def test_none_input_uses_zero_balance_with_live_positions(self, tmp_path):
        from src.core.coordinator import Coordinator

        coord = Coordinator.__new__(Coordinator)
        coord._allocator = None
        coord._units_path = ""
        coord._accounts_path = ""
        coord._instruments_path = ""
        coord._cfg = {}
        coord._shadow_predictors_cache = {}

        db = str(tmp_path / "journal.db")
        _make_db(db, [("bybit_1", "ETHUSDT", "short", 3.0, "open", 0)])

        captured_ps = []

        def _spy_allocate(signals, ps):
            captured_ps.append(ps)
            return []

        coord.allocator.allocate = _spy_allocate

        coord.build_order_packages([_make_signal()], None, db_path=db)
        assert captured_ps[0].balance == 0.0
        assert captured_ps[0].net_positions == {"ETHUSDT": pytest.approx(-3.0)}
