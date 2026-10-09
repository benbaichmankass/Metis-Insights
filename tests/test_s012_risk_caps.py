"""S-012 PR E3: risk-cap firing tests for both turtle_soup and vwap.

Closes the test gap identified in audit § 7.4: no existing test
exercises ``RiskManager.approve()`` rejection paths, and no test
combines an OrderPackage from the new roster with the account-level
caps. PR E3a layers max_dd_pct on top.

DoD coverage (PR sequence § 9), as AMENDED 2026-10-09:
* RiskManager.approve NO LONGER refuses on daily loss or intra-day drawdown,
  for either strategy. OPERATOR DECISION 2026-10-09 (verbatim option
  chosen): "Remove them" — "No account-wide daily stop at all; only
  per-trade sizing and the prop-firm floors apply." The tests that asserted
  the DAILY_LOSS_CAP / INTRADAY_DRAWDOWN refusals now assert the opposite:
  an account past the old caps still approves its next trade, while daily
  PnL and drawdown are still computed and reported.
* safe_place_order returns 'halted' when the kill-switch flag is set.

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
# the risk budget, margin/buying-power, and exchange lot size. The former
# TestPosSizeCap class is gone; the daily-loss + drawdown tests below now
# assert those caps are GONE too (2026-10-09).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# daily_usd no longer refuses, for BOTH strategies (removed 2026-10-09)
# ---------------------------------------------------------------------------


class TestNoDailyLossStop:
    # 2026-10-09: the daily-loss cap was REMOVED (operator decision, "Remove
    # them"). A loss past the old ``daily_usd`` no longer refuses anything.
    def test_daily_loss_past_old_cap_still_approves_vwap(self):
        acc = _account(daily_usd=100.0)
        acc.risk_manager.record_trade_result(-150.0)  # past the old cap
        assert acc.risk_manager.approve(_vwap_pkg(estimated_value=100.0)) is True
        assert acc.risk_manager.daily_pnl == -150.0   # still tracked

    def test_daily_loss_past_old_cap_still_approves_turtle_soup(self):
        acc = _account(daily_usd=100.0)
        acc.risk_manager.record_trade_result(-150.0)
        assert acc.risk_manager.approve(_turtle_soup_pkg(estimated_value=100.0)) is True

    def test_daily_loss_at_cap_still_passes(self):
        """Boundary: daily_pnl == -daily_usd is the exact cap. Still allowed.

        The check is `daily_pnl < -max_daily_loss_usd` (strict <), so
        equality passes. This is the documented S-010 contract.
        """
        acc = _account(daily_usd=100.0)
        acc.risk_manager.record_trade_result(-100.0)
        assert acc.risk_manager.approve(_vwap_pkg(estimated_value=100.0)) is True

    def test_reset_daily_zeroes_reported_pnl(self):
        acc = _account(daily_usd=100.0)
        acc.risk_manager.record_trade_result(-200.0)
        assert acc.risk_manager.approve(_vwap_pkg(estimated_value=100.0)) is True
        acc.risk_manager.reset_daily()
        assert acc.risk_manager.daily_pnl == 0.0
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

    def test_approve_after_old_daily_loss_breach_returns_true(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0})
        rm.record_trade_result(-150.0)
        assert rm.approve(_vwap_pkg(estimated_value=100.0)) is True

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
    """Intra-day drawdown from today's high; UTC-midnight reset. Still
    COMPUTED and reported; since 2026-10-09 it never refuses a trade."""

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

    def test_drawdown_at_old_cap_still_approves_vwap(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 1_000.0, "pos_size": 1_000.0})
        rm.update_equity(10_000.0)
        rm.update_equity(9_500.0)         # exactly the old 5 % cap
        assert rm.intraday_drawdown() == pytest.approx(0.05)
        assert rm.approve(_vwap_pkg(estimated_value=100.0)) is True

    def test_drawdown_past_old_cap_still_approves_turtle_soup(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 1_000.0, "pos_size": 1_000.0})
        rm.update_equity(10_000.0)
        rm.update_equity(9_400.0)         # 6 % > the old 5 % cap
        assert rm.approve(_turtle_soup_pkg(estimated_value=100.0)) is True

    def test_drawdown_via_account_risk_manager_still_approves(self):
        acc = _account(max_dd_pct=0.05, daily_usd=1_000.0, pos_size=1_000.0)
        acc.risk_manager.update_equity(10_000.0)
        acc.risk_manager.update_equity(9_400.0)  # 6 % drawdown
        assert acc.risk_manager.approve(_vwap_pkg(estimated_value=100.0)) is True

    def test_intraday_high_bumps_when_equity_climbs(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 1_000.0, "pos_size": 1_000.0})
        rm.update_equity(10_000.0)
        rm.update_equity(11_000.0)       # new intra-day high
        rm.update_equity(10_500.0)       # 4.5 % drawdown vs 11 000
        assert rm.intraday_drawdown() == pytest.approx(500.0 / 11_000.0)
        rm.update_equity(10_400.0)       # 5.45 % drawdown vs 11 000
        assert rm.intraday_drawdown() == pytest.approx(600.0 / 11_000.0)
        assert rm.approve(_vwap_pkg(estimated_value=100.0)) is True

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
        rm.update_equity(9_000.0)         # 10 % drawdown — no longer blocks
        rm.record_trade_result(-200.0)
        assert rm.daily_pnl == -200.0
        assert rm.intraday_drawdown() == pytest.approx(0.10)
        assert rm.approve(_vwap_pkg(estimated_value=100.0)) is True

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
        # No halted / cap fields: the caps were removed 2026-10-09.
        for gone in ("halted", "max_daily_loss_usd", "max_dd_pct",
                     "daily_loss_remaining", "daily_loss_pct"):
            assert gone not in rep

    def test_report_still_reports_drawdown_past_old_cap(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 1_000.0, "pos_size": 1_000.0})
        rm.update_equity(10_000.0)
        rm.update_equity(9_000.0)        # 10 % — past the old 5 % cap
        rm.record_trade_result(-2_000.0)  # past the old $1,000 daily cap
        rep = rm.report()
        assert rep["intraday_drawdown_pct"] == pytest.approx(0.10)
        assert rep["daily_pnl"] == -2_000.0
        assert "halted" not in rep
