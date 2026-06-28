"""S-010 PR #1: Modular accounts unit tests.

Covers RiskManager, TradingAccount, Integrator, and load_accounts().
All tests offline — no exchange, no network, dry-run only.
"""
from __future__ import annotations

import textwrap

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager
from src.units.accounts.account import TradingAccount
from src.units.accounts.integrator import EXCHANGE_MAP
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

    # place_order dry-run / risk-breach / isolation tests REMOVED 2026-06-28
    # (audit Workstream B): place_order was the dead router entry point. The
    # risk gate it wrapped (RiskManager.approve / .evaluate) has direct
    # coverage in TestRiskManager (above) + tests/test_s012_risk_caps.py
    # (TestRiskManagerApprove) and the live-path RiskBreach + per-account
    # isolation are covered by tests/test_accounts_integration.py
    # (TestCoordinatorMultiAccountExecute.test_risk_breach_on_one_does_not_block_others).

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
    # route_order() REMOVED 2026-06-28 (audit Workstream B) — the dead router,
    # superseded by execute_pkg. The route_order dry-run / unknown-exchange
    # tests were removed with it. EXCHANGE_MAP stays as the integration registry
    # (consumed by the test_ltmgmt_p5_contract_ci CI guard + new-broker skill).

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

    # place_order-based dry-run tests REMOVED 2026-06-28 (audit Workstream B):
    # place_order was the dead router. The dry_run FLAG itself (the live-path
    # input to RiskManager/coordinator) is still covered by the default/status
    # tests here.

    def test_status_includes_dry_run_key(self):
        acc = self._account()
        s = acc.status()
        assert "dry_run" in s
        assert s["dry_run"] is True

    def test_status_reflects_toggled_dry_run(self):
        acc = self._account()
        acc.dry_run = False
        assert acc.status()["dry_run"] is False
