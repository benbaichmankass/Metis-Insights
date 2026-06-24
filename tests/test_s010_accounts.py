"""S-010 PR #1: Modular accounts unit tests.

Covers RiskManager, TradingAccount, Integrator, and load_accounts().
All tests offline — no exchange, no network, dry-run only.
"""
from __future__ import annotations

import textwrap

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager
from src.units.accounts.account import TradingAccount, RiskBreach
from src.units.accounts.integrator import route_order, EXCHANGE_MAP
from src.units.accounts import load_accounts


ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_1:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_1
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
      bybit_2:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_2
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
      prop_breakout_1:
        type: prop
        exchange: breakout
        api_key_env: BREAKOUT_KEY_1
        risk:
          max_dd_pct: 0.02
          daily_usd: 50
          pos_size: 200
""")


@pytest.fixture()
def accounts_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(ACCOUNTS_YAML)
    return str(p)


def _pkg(strategy="test", symbol="BTCUSDT", direction="long",
         entry=100.0, sl=98.0, tp=104.0, **meta) -> OrderPackage:
    return OrderPackage(
        strategy=strategy, symbol=symbol, direction=direction,
        entry=entry, sl=sl, tp=tp, meta=meta or {},
    )


# ---------------------------------------------------------------------------
# RiskManager
# ---------------------------------------------------------------------------

class TestRiskManager:
    def test_approve_passes_fresh_account(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 100, "pos_size": 500})
        assert rm.approve(_pkg()) is True

    def test_approve_rejects_daily_loss_exceeded(self):
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 100, "pos_size": 500})
        rm.daily_pnl = -101.0
        assert rm.approve(_pkg()) is False

    def test_approve_large_estimated_value_no_longer_rejected(self):
        """Position-notional cap removed 2026-06-24 — a large
        ``estimated_value`` no longer fails the size gate."""
        rm = RiskManager({"max_dd_pct": 0.05, "daily_usd": 100, "pos_size": 500})
        pkg = _pkg(estimated_value=600.0)
        assert rm.approve(pkg) is True

    def test_record_trade_updates_daily_pnl(self):
        rm = RiskManager({"daily_usd": 100})
        rm.record_trade_result(-30.0)
        assert rm.daily_pnl == pytest.approx(-30.0)

    def test_reset_daily_clears_pnl(self):
        rm = RiskManager({"daily_usd": 100})
        rm.daily_pnl = -80.0
        rm.reset_daily()
        assert rm.daily_pnl == 0.0

    def test_report_shows_halted_true_when_exceeded(self):
        rm = RiskManager({"daily_usd": 100})
        rm.daily_pnl = -101.0
        assert rm.report()["halted"] is True

    def test_report_shows_halted_false_when_ok(self):
        rm = RiskManager({"daily_usd": 100})
        assert rm.report()["halted"] is False

    def test_prop_account_stricter_defaults(self):
        rm = RiskManager({"max_dd_pct": 0.02, "daily_usd": 50, "pos_size": 200})
        rm.daily_pnl = -51.0
        assert rm.approve(_pkg()) is False


# ---------------------------------------------------------------------------
# TradingAccount
# ---------------------------------------------------------------------------

class TestTradingAccount:
    def _account(self, **risk_overrides):
        cfg = {"max_dd_pct": 0.05, "daily_usd": 100, "pos_size": 500, **risk_overrides}
        rm = RiskManager(cfg)
        return TradingAccount(
            name="test_bybit", exchange="bybit",
            api_key_env="BYBIT_KEY", risk_manager=rm,
        )

    def test_place_order_dry_run_returns_trade_id(self):
        acc = self._account()
        tid = acc.place_order(_pkg())
        assert tid.startswith("dry-")

    def test_place_order_raises_risk_breach_on_daily_loss(self):
        acc = self._account()
        acc.risk_manager.daily_pnl = -200.0
        with pytest.raises(RiskBreach):
            acc.place_order(_pkg())

    def test_accounts_are_isolated(self):
        acc1 = self._account()
        acc2 = self._account()
        acc1.risk_manager.daily_pnl = -200.0
        # acc2 is unaffected
        tid = acc2.place_order(_pkg())
        assert tid.startswith("dry-")

    def test_status_returns_dict_with_name(self):
        acc = self._account()
        s = acc.status()
        assert s["name"] == "test_bybit"
        assert "daily_pnl" in s
        assert "halted" in s


# ---------------------------------------------------------------------------
# Integrator
# ---------------------------------------------------------------------------

class TestIntegrator:
    def _account(self, exchange="bybit"):
        rm = RiskManager({"daily_usd": 100})
        return TradingAccount("acc", exchange, "KEY_ENV", rm)

    def test_bybit_dry_run_returns_dry_id(self):
        tid = route_order(self._account("bybit"), _pkg())
        assert tid.startswith("dry-bybit-")

    def test_breakout_dry_run_returns_dry_id(self):
        tid = route_order(self._account("breakout"), _pkg())
        assert tid.startswith("dry-breakout-")

    def test_unknown_exchange_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown exchange"):
            route_order(self._account("unknown"), _pkg())

    def test_exchange_map_contains_bybit_and_breakout(self):
        assert "bybit" in EXCHANGE_MAP
        assert "breakout" in EXCHANGE_MAP


# ---------------------------------------------------------------------------
# load_accounts()
# ---------------------------------------------------------------------------

class TestLoadAccounts:
    def test_returns_three_accounts(self, accounts_yaml):
        accounts = load_accounts(accounts_yaml)
        assert len(accounts) == 3

    def test_account_names_correct(self, accounts_yaml):
        names = {a.name for a in load_accounts(accounts_yaml)}
        assert names == {"bybit_1", "bybit_2", "prop_breakout_1"}

    def test_prop_account_stricter_risk(self, accounts_yaml):
        accounts = load_accounts(accounts_yaml)
        prop = next(a for a in accounts if a.name == "prop_breakout_1")
        assert prop.risk_manager.max_daily_loss_usd == 50.0

    def test_regular_accounts_bybit_exchange(self, accounts_yaml):
        accounts = load_accounts(accounts_yaml)
        bybit_accounts = [a for a in accounts if a.exchange == "bybit"]
        assert len(bybit_accounts) == 2

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_accounts(str(tmp_path / "nonexistent.yaml"))


# ---------------------------------------------------------------------------
# S-011 PR #1: dry_run flag + toggle
# ---------------------------------------------------------------------------

class TestDryRunFlag:
    def _account(self) -> "TradingAccount":
        from src.units.accounts.account import TradingAccount
        from src.units.accounts.risk import RiskManager
        rm = RiskManager({"daily_usd": 100, "pos_size": 500})
        return TradingAccount("test", "bybit", "KEY", rm)

    def test_default_dry_run_is_true(self):
        acc = self._account()
        assert acc.dry_run is True

    def test_place_order_default_returns_dry_id(self):
        acc = self._account()
        tid = acc.place_order(_pkg())
        assert tid.startswith("dry-")

    def test_explicit_dry_run_kwarg_overrides_instance(self):
        acc = self._account()
        acc.dry_run = False  # instance says live
        # but explicit kwarg says dry
        tid = acc.place_order(_pkg(), dry_run=True)
        assert tid.startswith("dry-")

    def test_status_includes_dry_run_key(self):
        acc = self._account()
        s = acc.status()
        assert "dry_run" in s
        assert s["dry_run"] is True

    def test_status_reflects_toggled_dry_run(self):
        acc = self._account()
        acc.dry_run = False
        assert acc.status()["dry_run"] is False
