"""S-012 PR E3: risk-cap firing tests for both turtle_soup and vwap.

Closes the test gap identified in audit § 7.4: no existing test
exercises ``RiskManager.approve()`` rejection paths, and no test
combines an OrderPackage from the new roster with the account-level
caps. PR E3a layers max_dd_pct on top.

DoD coverage (PR sequence § 9):
* RiskManager.approve refuses when daily loss > daily_usd, for both
  strategies.
* safe_place_order returns 'halted' when the kill-switch flag is set.
* (E3a) RiskManager.approve refuses when intra-day drawdown ≥ max_dd_pct.

(2026-06-28 audit Workstream B: the dead-router ``TradingAccount.place_order``
entry point was removed; these tests now assert the risk gate it wrapped
``RiskManager.approve`` directly. The live path reaches the same gate via
``RiskManager.evaluate`` in ``Coordinator.multi_account_execute``.)
"""
from __future__ import annotations

import os

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.account import TradingAccount
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
# (Removed 2026-06-24) The arbitrary position-NOTIONAL cap (pos_size /
# POSITION_SIZE_CAP) was deleted from RiskManager — an order's
# ``estimated_value`` is no longer gated. Position size is bounded only by
# the risk budget, daily-loss budget, margin/buying-power, and exchange lot
# size. The former TestPosSizeCap class is gone; the daily-loss + drawdown +
# halt + smoke tests below remain the active risk-cap contract.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# daily_usd cap fires for BOTH strategies
# ---------------------------------------------------------------------------


class TestDailyLossCap:
    # Converted 2026-06-28 (audit Workstream B) from the removed dead-router
    # entry point ``acc.place_order`` to the actual risk gate
    # ``acc.risk_manager.approve`` it wrapped (False == would-refuse). The
    # live path reaches the same gate via RiskManager.evaluate in
    # Coordinator.multi_account_execute.
    def test_daily_loss_exceeded_rejects_vwap(self):
        """daily_pnl < -max_daily_loss_usd → approve() False (vwap)."""
        acc = _account(daily_usd=100.0)
        acc.risk_manager.record_trade_result(-150.0)  # blew through cap
        assert acc.risk_manager.approve(_vwap_pkg(estimated_value=100.0)) is False

    def test_daily_loss_exceeded_rejects_turtle_soup(self):
        acc = _account(daily_usd=100.0)
        acc.risk_manager.record_trade_result(-150.0)
        assert acc.risk_manager.approve(_turtle_soup_pkg(estimated_value=100.0)) is False

    def test_daily_loss_at_cap_still_passes(self):
        """Boundary: daily_pnl == -daily_usd is the exact cap. Still allowed.

        The check is `daily_pnl < -max_daily_loss_usd` (strict <), so
        equality passes. This is the documented S-010 contract.
        """
        acc = _account(daily_usd=100.0)
        acc.risk_manager.record_trade_result(-100.0)
        assert acc.risk_manager.approve(_vwap_pkg(estimated_value=100.0)) is True

    def test_reset_daily_clears_breach(self):
        acc = _account(daily_usd=100.0)
        acc.risk_manager.record_trade_result(-200.0)
        assert acc.risk_manager.approve(_vwap_pkg(estimated_value=100.0)) is False
        acc.risk_manager.reset_daily()
        assert acc.risk_manager.approve(_vwap_pkg(estimated_value=100.0)) is True


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

    def test_approve_large_estimated_value_no_longer_capped(self):
        """No position-notional cap anymore: an order whose estimated_value
        is well above the (now-ignored) pos_size still passes the size gate
        (operator directive 2026-06-24)."""
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0})
        assert rm.approve(_vwap_pkg(estimated_value=600.0)) is True

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


# ---------------------------------------------------------------------------
# S-012 PR E3a — max_dd_pct intra-day drawdown enforcement
# ---------------------------------------------------------------------------


class TestMaxDrawdownIntraday:
    """Per PM § 8 #6: intra-day drawdown from today's high; UTC-midnight reset."""

    def test_no_drawdown_check_until_equity_seeded(self):
        """Backwards-compatible: rejection only fires after update_equity()."""
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0})
        # Equity unset → drawdown is None; approve passes.
        assert rm.intraday_drawdown() is None
        assert rm.approve(_vwap_pkg(estimated_value=100.0)) is True

    def test_drawdown_below_cap_passes_for_vwap(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 1_000.0, "pos_size": 1_000.0})
        rm.update_equity(10_000.0)        # establish daily high
        rm.update_equity(9_700.0)         # 3 % drawdown < 5 % cap
        assert rm.approve(_vwap_pkg(estimated_value=100.0)) is True

    def test_drawdown_at_or_above_cap_rejects_vwap(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 1_000.0, "pos_size": 1_000.0})
        rm.update_equity(10_000.0)
        rm.update_equity(9_500.0)         # exactly 5 % → reject (>= cap)
        assert rm.approve(_vwap_pkg(estimated_value=100.0)) is False

    def test_drawdown_at_or_above_cap_rejects_turtle_soup(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 1_000.0, "pos_size": 1_000.0})
        rm.update_equity(10_000.0)
        rm.update_equity(9_400.0)         # 6 % > 5 % cap
        assert rm.approve(_turtle_soup_pkg(estimated_value=100.0)) is False

    def test_drawdown_via_account_risk_manager_rejects(self):
        """The account's RiskManager refuses when the drawdown cap is breached.
        (Was an end-to-end ``place_order`` raising RiskBreach; place_order was
        the dead router, removed 2026-06-28 — the gate it wrapped is asserted
        directly here. The live path reaches it via RiskManager.evaluate.)"""
        acc = _account(max_dd_pct=0.05, daily_usd=1_000.0, pos_size=1_000.0)
        acc.risk_manager.update_equity(10_000.0)
        acc.risk_manager.update_equity(9_400.0)  # 6 % drawdown
        assert acc.risk_manager.approve(_vwap_pkg(estimated_value=100.0)) is False

    def test_intraday_high_bumps_when_equity_climbs(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 1_000.0, "pos_size": 1_000.0})
        rm.update_equity(10_000.0)
        rm.update_equity(11_000.0)       # new intra-day high
        rm.update_equity(10_500.0)       # 4.5 % drawdown vs 11 000 — passes
        assert rm.approve(_vwap_pkg(estimated_value=100.0)) is True
        rm.update_equity(10_400.0)       # 5.45 % drawdown vs 11 000 — fails
        assert rm.approve(_vwap_pkg(estimated_value=100.0)) is False

    def test_drawdown_clamped_at_zero_when_above_high(self):
        """Sanity: equity > high → drawdown is 0, not a negative."""
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 1_000.0, "pos_size": 1_000.0})
        rm.update_equity(10_000.0)
        rm.update_equity(11_000.0)
        # Force a stale high lower than current to exercise the clamp.
        rm.daily_high_equity = 10_500.0
        rm.current_equity = 11_000.0
        assert rm.intraday_drawdown() == 0.0

    def test_utc_midnight_rollover_re_anchors_high(self, monkeypatch):
        """When the UTC date advances, the intra-day high re-anchors to
        current_equity and daily_pnl resets. PM § 8 #6 contract."""
        from datetime import date

        # Monkeypatch BEFORE constructing the RiskManager so the
        # initial _last_reset_utc_date is the simulated "yesterday".
        monkeypatch.setattr(
            RiskManager, "_today_utc",
            staticmethod(lambda: date(2026, 4, 28)),
        )
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 1_000.0, "pos_size": 1_000.0})
        # Simulate yesterday: equity drops to a breach.
        rm.update_equity(10_000.0)
        rm.update_equity(9_000.0)         # 10 % drawdown — would block today
        rm.record_trade_result(-200.0)
        assert rm.daily_pnl == -200.0
        assert rm.intraday_drawdown() == pytest.approx(0.10)
        assert rm.approve(_vwap_pkg(estimated_value=100.0)) is False

        # Roll over to next UTC day.
        monkeypatch.setattr(
            RiskManager, "_today_utc",
            staticmethod(lambda: date(2026, 4, 29)),
        )
        # Any equity update or approve() triggers the reset.
        rm.update_equity(9_000.0)
        assert rm.daily_pnl == 0.0                  # daily_pnl reset
        assert rm.intraday_drawdown() == 0.0        # high re-anchored to 9 000
        assert rm.approve(_vwap_pkg(estimated_value=100.0)) is True

    def test_report_includes_drawdown_fields(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 1_000.0, "pos_size": 1_000.0})
        rm.update_equity(10_000.0)
        rm.update_equity(9_700.0)
        rep = rm.report()
        assert rep["current_equity"] == 9_700.0
        assert rep["daily_high_equity"] == 10_000.0
        assert rep["intraday_drawdown_pct"] == pytest.approx(0.03)
        assert rep["halted"] is False

    def test_report_halted_when_drawdown_breached(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 1_000.0, "pos_size": 1_000.0})
        rm.update_equity(10_000.0)
        rm.update_equity(9_000.0)        # 10 % > 5 % cap
        rep = rm.report()
        assert rep["halted"] is True
