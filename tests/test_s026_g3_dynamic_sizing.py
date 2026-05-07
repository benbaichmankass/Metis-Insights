"""S-026 G3 — Dynamic sizing rules layered on top of G2's position_size.

Pins:
  - Floor-rounding (never overshoot the risk cap).
  - Daily-loss-budget gate (refuse / scale down when SL would bust the cap).
  - Live balance fetcher wired into Coordinator.multi_account_execute.
"""
from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager, _floor_to_step


def _pkg(entry=50_000.0, sl=49_500.0) -> OrderPackage:
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="long",
        entry=entry,
        sl=sl,
        tp=51_000.0,
        confidence=0.7,
    )


# ---------------------------------------------------------------------------
# Floor rounding — _floor_to_step is exposed because the operator's safety
# argument is "never round UP into the risk budget".
# ---------------------------------------------------------------------------


class TestFloorRounding:
    def test_floor_rounds_down_not_to_nearest(self):
        # 0.0245 → at precision 3, banker's rounds to 0.024 OR 0.025
        # depending on the implementation. Floor must always go DOWN.
        assert _floor_to_step(0.0249, precision=3) == 0.024
        assert _floor_to_step(0.0245, precision=3) == 0.024
        assert _floor_to_step(0.0241, precision=3) == 0.024

    def test_floor_handles_zero_and_negatives(self):
        assert _floor_to_step(0.0, precision=3) == 0.0
        assert _floor_to_step(-1.5, precision=3) == 0.0  # negatives clamped to 0

    def test_floor_zero_precision(self):
        assert _floor_to_step(7.9, precision=0) == 7.0

    def test_position_size_uses_floor_not_round(self):
        """Reproduces the safety bug banker's rounding can introduce.

        risk_pct=0.01, balance=$5000, distance=$200 → raw qty = 0.25
        That sits cleanly on the precision-3 grid. But for a balance
        that produces e.g. raw_qty=0.0249, banker's rounding could go
        to 0.025 (one step OVER the risk cap); floor rounds to 0.024.
        """
        # risk_pct=0.01, distance=200 → raw_qty = balance * 0.01 / 200
        # Pick a balance that produces 0.0249: balance = 0.0249 * 200 / 0.01 = 498
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 1,
            "min_qty": 0.0001,
            "qty_precision": 3,
            "daily_usd": 1_000_000,  # disable daily-loss gate for this assertion
        })
        pkg = _pkg(entry=50_000.0, sl=49_800.0)  # distance=200
        qty = rm.position_size(pkg, balance_usd=498.0)
        # raw = 498 * 0.01 / 200 = 0.0249 → floor at 3dp = 0.024
        assert qty == pytest.approx(0.024, abs=1e-6), (
            f"S-026 G3: position_size must floor-round, got {qty}"
        )


# ---------------------------------------------------------------------------
# Daily-loss-budget gate — refuse / scale down when full SL would bust.
# ---------------------------------------------------------------------------


class TestDailyLossBudgetGate:
    def test_gate_does_not_clip_small_trades(self):
        """Trade well within the daily budget passes unchanged."""
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 100,  # operator default
        })
        pkg = _pkg(entry=50_000.0, sl=49_500.0)  # distance=500

        # Balance=$10k → raw qty = 100/500 = 0.2; loss-at-SL = $100.
        # daily_usd=100 means budget is exactly the loss; should pass.
        qty = rm.position_size(pkg, balance_usd=10_000.0)
        assert qty > 0
        assert qty * 500 <= 100 + 1e-6  # within budget

    def test_gate_scales_down_when_trade_would_bust_budget(self):
        """Big balance + small daily budget → qty scaled to fit budget."""
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 50,  # very tight
        })
        pkg = _pkg(entry=50_000.0, sl=49_500.0)  # distance=500

        # Balance=$10k → raw qty=0.2 → loss-at-SL=$100 (>$50 budget).
        # Scaled to fit: qty = 50/500 = 0.1.
        qty = rm.position_size(pkg, balance_usd=10_000.0)
        # Floored to step-size (precision=3): 0.1 exactly.
        assert qty == pytest.approx(0.1, abs=1e-6)
        # Realised max loss must NOT exceed budget.
        assert qty * 500 <= 50 + 1e-6

    def test_gate_refuses_when_min_qty_busts_budget(self):
        """When even min_qty would bust the budget, qty=0 (refuse)."""
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 0.01,   # nearly zero budget
            "min_qty": 0.001,
        })
        pkg = _pkg(entry=50_000.0, sl=49_500.0)  # distance=500
        # min_qty * distance = 0.001 * 500 = $0.50 > $0.01 budget → refuse.
        qty = rm.position_size(pkg, balance_usd=10_000.0)
        assert qty == 0.0

    def test_gate_refuses_when_already_past_daily_loss(self):
        """If daily_pnl is already past -max_daily_loss_usd, refuse."""
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 100,
        })
        # Push daily_pnl past the cap.
        rm.daily_pnl = -150.0
        pkg = _pkg(entry=50_000.0, sl=49_500.0)
        assert rm.position_size(pkg, balance_usd=10_000.0) == 0.0

    def test_gate_uses_remaining_budget_when_partially_drawn(self):
        """If half the budget is already used, sizer scales to the half left."""
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 100,
        })
        rm.daily_pnl = -60.0  # $40 budget remaining
        pkg = _pkg(entry=50_000.0, sl=49_500.0)  # distance=500

        # Raw qty (no gate) = 100/500 = 0.2 → loss-at-SL = $100.
        # Budget remaining = $40 → scaled qty = 40/500 = 0.08.
        qty = rm.position_size(pkg, balance_usd=10_000.0)
        assert qty == pytest.approx(0.08, abs=1e-6)


# ---------------------------------------------------------------------------
# Live balance fetcher wired into Coordinator.multi_account_execute
# ---------------------------------------------------------------------------


class TestLiveBalanceFetcher:
    def _stub_accounts(self, monkeypatch):
        rm_a = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50,
                            "daily_usd": 1_000_000})
        rm_b = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50,
                            "daily_usd": 1_000_000})

        class _Account:
            def __init__(self, name, rm):
                self.name = name
                self.exchange = "bybit"
                self.account_type = "regular"
                self.risk_manager = rm
                self.dry_run = True
                self.calls = []

            def place_order(self, pkg, *, dry_run=None):
                self.calls.append(pkg)
                return f"dry-{self.name}-1"

        accounts = [_Account("acc_a", rm_a), _Account("acc_b", rm_b)]
        monkeypatch.setattr(
            "src.units.accounts.load_accounts", lambda _path: accounts,
        )
        return accounts

    def test_live_fetcher_consults_get_account_balances(self, monkeypatch, tmp_path):
        """multi_account_execute calls processor.get_account_balances and
        uses each row's total_usdt as the per-account balance."""
        from src.core.coordinator import Coordinator

        self._stub_accounts(monkeypatch)
        accounts_path = tmp_path / "accounts.yaml"
        accounts_path.write_text("accounts: {}\n")

        # Stub the live processor lookup.
        live_calls = []

        def _fake_live():
            live_calls.append(True)
            return [
                {"account_id": "acc_a", "total_usdt": 4_000.0},
                {"account_id": "acc_b", "total_usdt": 1_500.0},
            ]

        monkeypatch.setattr("src.units.ui.processor.get_account_balances", _fake_live)

        pkg = _pkg(entry=50_000.0, sl=49_500.0)  # distance=500
        coord = Coordinator()
        results = coord.multi_account_execute(
            pkg, accounts_path=str(accounts_path), dry_run=True,
        )

        # Live fetcher consulted exactly once (cache for the round).
        assert len(live_calls) == 1

        names = {r["name"]: r for r in results}
        # acc_a: 4000 * 0.01 / 500 = 0.08
        # acc_b: 1500 * 0.01 / 500 = 0.03
        assert names["acc_a"]["sized_qty"] == pytest.approx(0.08, abs=1e-6)
        assert names["acc_b"]["sized_qty"] == pytest.approx(0.03, abs=1e-6)
        assert names["acc_a"]["error"] is None
        assert names["acc_b"]["error"] is None

    def test_live_fetcher_failure_falls_back_safely(self, monkeypatch, tmp_path):
        """If get_account_balances raises, sizing falls back to the
        explicit pkg-meta override / cached fixture path; no crash."""
        from src.core.coordinator import Coordinator

        self._stub_accounts(monkeypatch)
        accounts_path = tmp_path / "accounts.yaml"
        accounts_path.write_text("accounts: {}\n")

        def _bork():
            raise RuntimeError("processor down")

        monkeypatch.setattr("src.units.ui.processor.get_account_balances", _bork)

        pkg = _pkg(entry=50_000.0, sl=49_500.0)
        # Provide an explicit override on the package so sizing still
        # produces a non-zero qty after the live fetcher failed.
        pkg.meta = {"account_balances_usd": {"acc_a": 5_000.0, "acc_b": 5_000.0}}

        coord = Coordinator()
        results = coord.multi_account_execute(
            pkg, accounts_path=str(accounts_path), dry_run=True,
        )

        # Both accounts still sized (no crash from the failed live lookup).
        names = {r["name"]: r for r in results}
        assert names["acc_a"]["sized_qty"] > 0
        assert names["acc_b"]["sized_qty"] > 0

    def test_live_fetcher_missing_total_usdt_treated_as_no_balance(
        self, monkeypatch, tmp_path,
    ):
        """A row whose total_usdt is None (exchange call failed) must NOT
        be treated as $0 → the per-account RiskManager refuses to size
        and the operator sees a clear ``below_min_balance`` skip
        instead of a phantom zero-qty trade."""
        from src.core.coordinator import Coordinator

        self._stub_accounts(monkeypatch)
        accounts_path = tmp_path / "accounts.yaml"
        accounts_path.write_text("accounts: {}\n")

        # acc_a balance is None (failed lookup); acc_b is fine.
        monkeypatch.setattr(
            "src.units.ui.processor.get_account_balances",
            lambda: [
                {"account_id": "acc_a", "total_usdt": None},
                {"account_id": "acc_b", "total_usdt": 5_000.0},
            ],
        )

        pkg = _pkg(entry=50_000.0, sl=49_500.0)
        coord = Coordinator()
        results = coord.multi_account_execute(
            pkg, accounts_path=str(accounts_path), dry_run=True,
        )

        names = {r["name"]: r for r in results}
        # acc_a: total_usdt=None → fetcher returns 0.0 → below_min_balance.
        assert names["acc_a"]["sized_qty"] == 0.0
        assert "below_min_balance" in names["acc_a"]["error"]
        # acc_b: sized normally.
        assert names["acc_b"]["sized_qty"] > 0
        assert names["acc_b"]["error"] is None

    def test_explicit_override_wins_over_live_fetch(self, monkeypatch, tmp_path):
        """pkg.meta['account_balances_usd'] takes precedence over the
        live lookup — useful for tests + for the bot's per-tick
        balance-refresh loop."""
        from src.core.coordinator import Coordinator

        self._stub_accounts(monkeypatch)
        accounts_path = tmp_path / "accounts.yaml"
        accounts_path.write_text("accounts: {}\n")

        # Live says one thing, explicit override says another.
        monkeypatch.setattr(
            "src.units.ui.processor.get_account_balances",
            lambda: [{"account_id": "acc_a", "total_usdt": 999_999.0}],
        )

        pkg = _pkg(entry=50_000.0, sl=49_500.0)
        pkg.meta = {"account_balances_usd": {"acc_a": 1_000.0, "acc_b": 1_000.0}}

        coord = Coordinator()
        results = coord.multi_account_execute(
            pkg, accounts_path=str(accounts_path), dry_run=True,
        )

        # Explicit override of $1000 wins → qty = 1000 * 0.01 / 500 = 0.02.
        names = {r["name"]: r for r in results}
        assert names["acc_a"]["sized_qty"] == pytest.approx(0.02, abs=1e-6)
