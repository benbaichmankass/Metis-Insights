"""S-012 PR E3: risk-cap firing tests for both turtle_soup and vwap.

Closes the test gap identified in audit § 7.4: no existing test
exercises ``RiskManager.approve()`` rejection paths, and no test
combines an OrderPackage from the new roster with the account-level
caps. PR E3a layers max_dd_pct on top.

DoD coverage (PR sequence § 9):
* place_order refuses when position size > pos_size cap, for both
  strategies.
* place_order refuses when daily loss > daily_usd, for both.
* place_order refuses when the kill-switch flag is set.
* (E3a) place_order refuses when intra-day drawdown ≥ max_dd_pct.
"""
from __future__ import annotations

import os

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.account import RiskBreach, TradingAccount
from src.units.accounts.risk import RiskManager


# ---------------------------------------------------------------------------
# Helpers — synthetic OrderPackages for each strategy
# ---------------------------------------------------------------------------


def _vwap_pkg(estimated_value: float = 100.0) -> OrderPackage:
    """OrderPackage produced by the vwap strategy."""
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="long",
        entry=50_000.0,
        sl=49_000.0,
        tp=52_000.0,
        confidence=0.8,
        meta={"strategy_name": "vwap", "estimated_value": estimated_value},
    )


def _turtle_soup_pkg(estimated_value: float = 100.0) -> OrderPackage:
    """OrderPackage produced by the turtle_soup strategy."""
    return OrderPackage(
        strategy="turtle_soup",
        symbol="BTCUSDT",
        direction="long",
        entry=50_050.0,
        sl=49_420.0,
        tp=50_837.5,
        confidence=0.875,
        meta={"strategy_name": "turtle_soup", "estimated_value": estimated_value},
    )


def _account(name: str = "test", **risk_overrides) -> TradingAccount:
    """Build a TradingAccount with caps from accounts.yaml-style cfg."""
    rm_cfg = {"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0}
    rm_cfg.update(risk_overrides)
    rm = RiskManager(rm_cfg)
    return TradingAccount(
        name=name,
        exchange="bybit",
        api_key_env="BYBIT_API_KEY_TEST",
        risk_manager=rm,
        account_type="regular",
        dry_run=True,  # don't reach the exchange even if a test slips through
    )


# ---------------------------------------------------------------------------
# pos_size cap fires for BOTH strategies
# ---------------------------------------------------------------------------


class TestPosSizeCap:
    def test_oversized_vwap_order_rejected(self):
        """estimated_value > max_pos_size_usd → RiskBreach (vwap)."""
        acc = _account(pos_size=500.0)
        with pytest.raises(RiskBreach, match="position size"):
            acc.place_order(_vwap_pkg(estimated_value=600.0))

    def test_oversized_turtle_soup_order_rejected(self):
        """estimated_value > max_pos_size_usd → RiskBreach (turtle_soup)."""
        acc = _account(pos_size=500.0)
        with pytest.raises(RiskBreach, match="position size"):
            acc.place_order(_turtle_soup_pkg(estimated_value=600.0))

    def test_at_or_below_pos_size_cap_passes_for_vwap(self):
        acc = _account(pos_size=500.0)
        trade_id = acc.place_order(_vwap_pkg(estimated_value=500.0))
        assert isinstance(trade_id, str)

    def test_at_or_below_pos_size_cap_passes_for_turtle_soup(self):
        acc = _account(pos_size=500.0)
        trade_id = acc.place_order(_turtle_soup_pkg(estimated_value=500.0))
        assert isinstance(trade_id, str)


# ---------------------------------------------------------------------------
# daily_usd cap fires for BOTH strategies
# ---------------------------------------------------------------------------


class TestDailyLossCap:
    def test_daily_loss_exceeded_rejects_vwap(self):
        """daily_pnl < -max_daily_loss_usd → RiskBreach (vwap)."""
        acc = _account(daily_usd=100.0)
        acc.risk_manager.record_trade_result(-150.0)  # blew through cap
        with pytest.raises(RiskBreach, match="daily loss"):
            acc.place_order(_vwap_pkg(estimated_value=100.0))

    def test_daily_loss_exceeded_rejects_turtle_soup(self):
        acc = _account(daily_usd=100.0)
        acc.risk_manager.record_trade_result(-150.0)
        with pytest.raises(RiskBreach, match="daily loss"):
            acc.place_order(_turtle_soup_pkg(estimated_value=100.0))

    def test_daily_loss_at_cap_still_passes(self):
        """Boundary: daily_pnl == -daily_usd is the exact cap. Still allowed.

        The check is `daily_pnl < -max_daily_loss_usd` (strict <), so
        equality passes. This is the documented S-010 contract.
        """
        acc = _account(daily_usd=100.0)
        acc.risk_manager.record_trade_result(-100.0)
        trade_id = acc.place_order(_vwap_pkg(estimated_value=100.0))
        assert isinstance(trade_id, str)

    def test_reset_daily_clears_breach(self):
        acc = _account(daily_usd=100.0)
        acc.risk_manager.record_trade_result(-200.0)
        with pytest.raises(RiskBreach):
            acc.place_order(_vwap_pkg(estimated_value=100.0))
        acc.risk_manager.reset_daily()
        trade_id = acc.place_order(_vwap_pkg(estimated_value=100.0))
        assert isinstance(trade_id, str)


# ---------------------------------------------------------------------------
# Kill-switch / halt flag fires
# ---------------------------------------------------------------------------


class TestKillSwitchHaltsExecution:
    def test_halt_flag_blocks_safe_place_order(self, tmp_path, monkeypatch):
        """When HALT_FLAG_PATH exists, safe_place_order returns 'halted'."""
        from src.runtime.orders import safe_place_order

        flag = tmp_path / "trader_halt.flag"
        flag.write_text("halted by operator\n")

        # Build minimal settings dict; safe_place_order reads HALT_FLAG_PATH
        # from settings and uses MAX_DAILY_LOSS_USD/MAX_OPEN_POSITIONS guards.
        settings = {
            "HALT_FLAG_PATH": str(flag),
            "MAX_DAILY_LOSS_USD": 1_000.0,
            "MAX_OPEN_POSITIONS": 10,
            "DRY_RUN": True,
        }

        order = {
            "symbol": "BTCUSDT",
            "side": "buy",
            "qty": 0.1,
            "meta": {"strategy_name": "vwap"},
        }

        # safe_place_order checks the halt flag before reaching the
        # exchange or risk gates.
        result = safe_place_order(order, settings, client=None)
        assert isinstance(result, dict)
        assert result.get("status") == "halted"
        assert "halt" in result.get("reason", "").lower()

    def test_halt_flag_absence_does_not_halt(self, tmp_path):
        """No flag file → safe_place_order does not return 'halted'."""
        from src.runtime.orders import safe_place_order

        flag_path = str(tmp_path / "trader_halt.flag")
        assert not os.path.exists(flag_path)

        settings = {
            "HALT_FLAG_PATH": flag_path,
            "MAX_DAILY_LOSS_USD": 1_000.0,
            "MAX_OPEN_POSITIONS": 10,
            "DRY_RUN": True,
        }

        order = {
            "symbol": "BTCUSDT",
            "side": "buy",
            "qty": 0.1,
            "meta": {"strategy_name": "vwap"},
        }

        result = safe_place_order(order, settings, client=None)
        assert result.get("status") != "halted"


# ---------------------------------------------------------------------------
# RiskManager.approve() — direct unit tests (gap closer for audit § 7.4)
# ---------------------------------------------------------------------------


class TestRiskManagerApprove:
    def test_approve_within_caps_returns_true(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0})
        assert rm.approve(_vwap_pkg(estimated_value=100.0)) is True

    def test_approve_oversized_returns_false(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0})
        assert rm.approve(_vwap_pkg(estimated_value=600.0)) is False

    def test_approve_after_daily_loss_breach_returns_false(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0})
        rm.record_trade_result(-150.0)
        assert rm.approve(_vwap_pkg(estimated_value=100.0)) is False

    def test_approve_with_no_estimated_value_passes(self):
        """When meta omits estimated_value, the size cap cannot be checked
        and the check is skipped (existing S-010 behaviour, documented)."""
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0})
        pkg = _turtle_soup_pkg()
        pkg.meta.pop("estimated_value", None)
        assert rm.approve(pkg) is True
