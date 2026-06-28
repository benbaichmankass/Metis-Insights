"""S-010 PR #4: End-to-end integration tests for the accounts + risk layer.

Tests the full data path:
  accounts.yaml  →  load_accounts()  →  RiskManager.approve()
  →  Coordinator.multi_account_execute()  →  alerts pushed

All tests are offline, dry-run only — no exchange, no network, no DB.
"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage


# NOTE: no per-account ``api_key_env`` here on purpose. With one set,
# load_accounts marks the account ``configured=False`` (the env var is
# absent in the test process) and multi_account_execute drops
# unconfigured accounts at the eligibility filter, so the dispatch tests
# below would see zero results. These are offline dry-run tests; they
# force dry mode via ``dry_run=True`` on the call (process-level
# override) rather than the account ``mode`` field, so the per-account
# RiskManager still approves (it isn't in dry_run mode) while no live
# exchange client is constructed.
FULL_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_main:
        type: regular
        exchange: bybit
        risk:
          max_dd_pct: 0.05
          daily_usd: 200
          pos_size: 1000
      bybit_secondary:
        type: regular
        exchange: bybit
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
      prop_breakout:
        type: prop
        exchange: breakout
        risk:
          max_dd_pct: 0.02
          daily_usd: 50
          pos_size: 200
""")


@pytest.fixture()
def accounts_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(FULL_ACCOUNTS_YAML)
    return str(p)


@pytest.fixture()
def coord():
    return Coordinator()


def _pkg(strategy="ict", symbol="BTCUSDT", direction="long",
         entry=50000.0, sl=49000.0, tp=52000.0, **meta) -> OrderPackage:
    return OrderPackage(
        strategy=strategy, symbol=symbol, direction=direction,
        entry=entry, sl=sl, tp=tp, meta=meta or {},
    )


def _seed_breach_trade(db_path: str, account_id: str, pnl: float = -200.0) -> None:
    """Seed a today-dated closed trade so the journal-sourced daily_pnl
    rebuild puts *account_id* past its daily-loss cap.

    PropRiskManager now wires ``account_name`` through as the base
    RiskManager's ``account_id`` (BL-20260617-PROP-RISK-ACCOUNT-ID), so a
    prop account's daily-loss cap is rebuilt from the canonical journal on
    every gate check — exactly like a regular account. A breach must
    therefore come from real journal state; a poked-in-memory ``daily_pnl``
    would be overwritten by the rebuild on the next ``evaluate()``."""
    from datetime import datetime, timezone
    from src.units.db.database import Database
    today = datetime.now(timezone.utc).date()
    Database(db_path=db_path).insert_trade({
        "timestamp": f"{today}T12:00:00+00:00",
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry_price": 50_000.0,
        "position_size": 0.01,
        "status": "closed",
        "pnl": pnl,
        "is_backtest": 0,
        "account_id": account_id,
        "created_at": f"{today} 12:00:00",
    })


@pytest.fixture()
def prop_journal(tmp_path, monkeypatch):
    """Isolated canonical journal for the prop-breach tests.

    Points TRADE_JOURNAL_DB at a fresh temp DB and DATA_DIR at an empty
    dir (so the balance-snapshot read finds nothing and can't perturb the
    PnL-only assertions). Returns the DB path for ``_seed_breach_trade``."""
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data-root"))
    return str(db_path)


# ---------------------------------------------------------------------------
# Full load → risk-gate path
# ---------------------------------------------------------------------------
# NOTE (2026-06-28 audit): the former ``TestAccountsYamlToPlaceOrder`` class
# exercised the vestigial ``TradingAccount.place_order`` (removed with
# ``integrator.route_order``). Its risk-gate coverage is preserved here against
# the surviving primitive — ``load_accounts() → RiskManager.approve`` — and the
# end-to-end dispatch is covered by ``TestCoordinatorMultiAccountExecute`` below
# (the live ``multi_account_execute`` → ``execute_pkg`` path).

class TestAccountsYamlToRiskGate:
    def test_all_three_accounts_approve_clean_order(self, accounts_yaml):
        from src.units.accounts import load_accounts
        accounts = load_accounts(accounts_yaml)
        assert len(accounts) == 3
        assert all(a.risk_manager.approve(_pkg()) for a in accounts)

    def test_risk_state_isolated_between_accounts(self, accounts_yaml, prop_journal):
        from src.units.accounts import load_accounts
        # Exhaust the prop account via real journal state (its daily-loss
        # cap is rebuilt from the journal, so a poked-in-memory daily_pnl
        # would be overwritten on the next gate check).
        _seed_breach_trade(prop_journal, "prop_breakout")
        accounts = load_accounts(accounts_yaml)
        prop = next(a for a in accounts if a.name == "prop_breakout")
        # The journal rebuild populated daily_pnl past the -50 cap.
        assert prop.risk_manager.daily_pnl == pytest.approx(-200.0)
        # Regular accounts still approve; the breached prop account does not —
        # state is isolated.
        regular = [a for a in accounts if a.account_type == "regular"]
        assert all(acc.risk_manager.approve(_pkg()) for acc in regular)
        assert prop.risk_manager.approve(_pkg()) is False

# ---------------------------------------------------------------------------
# Coordinator.multi_account_execute integration
# ---------------------------------------------------------------------------

class TestCoordinatorMultiAccountExecute:
    # S-026 G2: multi_account_execute now sizes per-account. Tests
    # supply a fixed balance via balance_fetcher so position_size
    # produces a non-zero qty.
    #
    # dry_run=True is passed as the process-level override on every call:
    # accounts now default to ``mode: live`` (Autonomous live-trading
    # rule), so without the override the dispatch would try to construct a
    # real Bybit client (no creds in test env → per-account error). The
    # override forces the whole round into dry mode while leaving each
    # account's RiskManager out of dry_run mode so ``evaluate`` still
    # approves clean orders.
    _BALANCE_USD = 10_000.0

    def _balance_fetcher(self, _account):
        return self._BALANCE_USD

    def test_all_accounts_executed(self, coord, accounts_yaml):
        results = coord.multi_account_execute(
            _pkg(), accounts_path=accounts_yaml, dry_run=True,
            balance_fetcher=self._balance_fetcher,
        )
        assert len(results) == 3

    def test_no_errors_on_clean_accounts(self, coord, accounts_yaml):
        results = coord.multi_account_execute(
            _pkg(), accounts_path=accounts_yaml, dry_run=True,
            balance_fetcher=self._balance_fetcher,
        )
        assert all(r["error"] is None for r in results)

    def test_result_dict_keys(self, coord, accounts_yaml):
        results = coord.multi_account_execute(
            _pkg(), accounts_path=accounts_yaml, dry_run=True,
            balance_fetcher=self._balance_fetcher,
        )
        for r in results:
            assert {"name", "exchange", "account_type", "trade_id", "error"} <= r.keys()

    def test_prop_filter_returns_one(self, coord, accounts_yaml):
        results = coord.multi_account_execute(
            _pkg(), accounts_path=accounts_yaml, account_type="prop",
            dry_run=True,
            balance_fetcher=self._balance_fetcher,
        )
        assert len(results) == 1
        assert results[0]["name"] == "prop_breakout"

    def test_regular_filter_returns_two(self, coord, accounts_yaml):
        results = coord.multi_account_execute(
            _pkg(), accounts_path=accounts_yaml, account_type="regular",
            dry_run=True,
            balance_fetcher=self._balance_fetcher,
        )
        assert len(results) == 2

    def test_risk_breach_on_one_does_not_block_others(self, coord, accounts_yaml, prop_journal):
        from src.units.accounts import load_accounts
        _seed_breach_trade(prop_journal, "prop_breakout")
        accounts = load_accounts(accounts_yaml)
        assert next(
            a for a in accounts if a.name == "prop_breakout"
        ).risk_manager.daily_pnl == pytest.approx(-200.0)
        with patch("src.units.accounts.load_accounts", return_value=accounts):
            results = coord.multi_account_execute(
                _pkg(), accounts_path=accounts_yaml, dry_run=True,
                balance_fetcher=self._balance_fetcher,
            )
        ok = [r for r in results if r["error"] is None]
        err = [r for r in results if r["error"] is not None]
        assert len(ok) == 2
        assert len(err) == 1
        assert err[0]["name"] == "prop_breakout"

    def test_alerts_pushed_for_successful_trades(self, coord, accounts_yaml):
        coord.pop_alerts()
        coord.multi_account_execute(
            _pkg(), accounts_path=accounts_yaml, dry_run=True,
            balance_fetcher=self._balance_fetcher,
        )
        alerts = coord.list_alerts()
        multi = [a for a in alerts if "multi_execute" in a.get("message", "")]
        assert len(multi) == 3

    def test_exchange_type_present_in_results(self, coord, accounts_yaml):
        results = coord.multi_account_execute(
            _pkg(), accounts_path=accounts_yaml, dry_run=True,
            balance_fetcher=self._balance_fetcher,
        )
        exchanges = {r["exchange"] for r in results}
        assert "bybit" in exchanges
        assert "breakout" in exchanges


# ---------------------------------------------------------------------------
# Coordinator.accounts_status integration
# ---------------------------------------------------------------------------

class TestCoordinatorAccountsStatus:
    def test_status_count(self, coord, accounts_yaml):
        assert len(coord.accounts_status(accounts_yaml)) == 3

    def test_fresh_accounts_not_halted(self, coord, accounts_yaml):
        for s in coord.accounts_status(accounts_yaml):
            assert s["halted"] is False

    def test_prop_has_stricter_limits(self, coord, accounts_yaml):
        statuses = coord.accounts_status(accounts_yaml)
        prop = next(s for s in statuses if s["name"] == "prop_breakout")
        assert prop["max_daily_loss_usd"] == 50.0

    def test_bybit_main_has_larger_limits(self, coord, accounts_yaml):
        statuses = coord.accounts_status(accounts_yaml)
        main = next(s for s in statuses if s["name"] == "bybit_main")
        assert main["max_daily_loss_usd"] == 200.0


# ---------------------------------------------------------------------------
# Coordinator.reload_accounts integration
# ---------------------------------------------------------------------------

class TestCoordinatorReloadAccounts:
    def test_reload_returns_correct_count(self, coord, accounts_yaml):
        result = coord.reload_accounts(accounts_yaml)
        assert result["reloaded"] is True
        assert result["account_count"] == 3

    def test_reload_pushes_app_alert(self, coord, accounts_yaml):
        coord.pop_alerts()
        coord.reload_accounts(accounts_yaml)
        alerts = coord.list_alerts()
        assert any(
            "Accounts reloaded" in a.get("message", "") and a.get("source") == "app"
            for a in alerts
        )

    def test_reload_missing_file_returns_error(self, coord, tmp_path):
        result = coord.reload_accounts(str(tmp_path / "gone.yaml"))
        assert result["reloaded"] is False
