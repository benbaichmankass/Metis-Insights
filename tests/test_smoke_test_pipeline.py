"""Live-plumbing smoke-test pipeline tests.

Covers the strategies → coordinator → accounts → journal path that
``/smoke_test`` exercises:

* ``smoke_test`` strategy module returns a well-formed OrderPackage with
  ``meta.is_test=True`` and a sub-min-lot ``test_qty``.
* ``RiskManager.approve()`` short-circuits (returns True) for any
  ``is_test`` order — daily caps, pos size, drawdown all bypassed.
* ``size_order_from_cfg()`` returns ``meta.test_qty`` for test orders
  instead of risk-sized qty.
* ``execute_pkg`` routes test orders through ``_submit_test_order``
  which captures Bybit ``retCode != 0`` rejection in-band as
  ``"rejected_too_small:..."`` (no exception bubbles up).
* ``Coordinator.smoke_test_run`` drives the full pipeline, captures
  per-account result dicts, writes a row to ``trade_journal.db``, and
  pushes a dashboards alert.

All tests are offline — mocked Bybit clients, tmp_path DB, no live
exchange. See CLAUDE.md "Test data sources".
"""
from __future__ import annotations

import textwrap
from unittest.mock import MagicMock

import pytest

from src.core.coordinator import Coordinator, OrderPackage, _PAUSED_ACCOUNTS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

UNITS_YAML = textwrap.dedent("""\
    units:
      strategies:
        - name: smoke_test
          enabled: true
          model: null
          signal_prefixes: [smoke_test]
      accounts:
        - id: bybit_test
          exchange: bybit
          api_key_env: BYBIT_API_KEY_TEST
          risk_pct: 0.01
          balance_usdt: 10000.0
          strategies: [smoke_test]
      dashboards:
        db:
          trade_journal: trade_journal.db
          signals: data/trades.db
      return_commands:
        supported: []
      telegram_bot:
        data_source: dashboards
      app:
        config_enabled: true
      trading_school:
        auto_backtest: true
      db:
        trade_journal: trade_journal.db
        signals: data/trades.db
      workflows:
        docs: docs/claude/
""")


@pytest.fixture()
def units_yaml(tmp_path):
    p = tmp_path / "units.yaml"
    p.write_text(UNITS_YAML)
    return str(p)


@pytest.fixture()
def coord(units_yaml, tmp_path):
    _PAUSED_ACCOUNTS.clear()
    c = Coordinator(
        units_path=units_yaml,
        accounts_path=str(tmp_path / "no-accounts.yaml"),
    )
    yield c
    _PAUSED_ACCOUNTS.clear()


@pytest.fixture()
def journal_db(tmp_path, monkeypatch):
    """Point coordinator's journal logger at a tmp DB so tests don't touch
    the repo-root trade_journal.db."""
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    return str(db_path)


# ---------------------------------------------------------------------------
# Strategy module
# ---------------------------------------------------------------------------


class TestSmokeStrategy:
    def test_returns_well_formed_pkg(self):
        from src.units.strategies.smoke_test import order_package
        out = order_package({})
        assert out["symbol"] == "BTCUSDT"
        assert out["direction"] == "long"
        assert out["entry"] > 0
        assert out["sl"] < out["entry"] < out["tp"]
        assert out["meta"]["is_test"] is True
        assert out["meta"]["test_qty"] > 0
        assert out["meta"]["test_qty"] < 0.001  # below Bybit min-lot
        assert "smoke_id" in out["meta"]
        assert len(out["meta"]["smoke_id"]) == 8

    def test_short_direction_inverts_sl_tp(self):
        from src.units.strategies.smoke_test import order_package
        out = order_package({"direction": "short", "ref_price": 60_000})
        assert out["direction"] == "short"
        assert out["tp"] < out["entry"] < out["sl"]

    def test_invalid_direction_raises(self):
        from src.units.strategies.smoke_test import order_package
        with pytest.raises(ValueError):
            order_package({"direction": "sideways"})

    def test_invalid_test_qty_raises(self):
        from src.units.strategies.smoke_test import order_package
        with pytest.raises(ValueError):
            order_package({"test_qty": -0.001})

    def test_smoke_id_is_unique(self):
        from src.units.strategies.smoke_test import order_package
        a = order_package({})
        b = order_package({})
        assert a["meta"]["smoke_id"] != b["meta"]["smoke_id"]


# ---------------------------------------------------------------------------
# Risk bypass
# ---------------------------------------------------------------------------


def _smoke_pkg(**overrides):
    base = {
        "strategy": "smoke_test",
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry": 70_000.0,
        "sl": 68_600.0,
        "tp": 71_400.0,
        "confidence": 0.0,
        "meta": {"is_test": True, "test_qty": 0.0001, "smoke_id": "deadbeef"},
    }
    base.update(overrides)
    return OrderPackage(**base)


def _real_pkg(**overrides):
    base = {
        "strategy": "vwap",
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry": 70_000.0,
        "sl": 68_600.0,
        "tp": 71_400.0,
        "confidence": 0.6,
        "meta": {},
    }
    base.update(overrides)
    return OrderPackage(**base)


class TestRiskBypass:
    def test_approve_bypasses_daily_loss_for_test_order(self):
        from src.units.accounts.risk import RiskManager
        rm = RiskManager({"daily_usd": 100, "pos_size": 500, "max_dd_pct": 0.05})
        # Trip the daily-loss gate hard: a real order would be rejected.
        rm.daily_pnl = -10_000.0
        assert rm.approve(_real_pkg()) is False
        # Test order short-circuits.
        assert rm.approve(_smoke_pkg()) is True

    def test_approve_smoke_order_passes_regardless_of_estimated_value(self):
        # Position-notional cap removed 2026-06-24: a large estimated_value
        # is no longer gated for EITHER a real or a smoke order. A real order
        # with a huge estimated_value now passes the size gate (clean account),
        # and the smoke bypass remains unconditional.
        from src.units.accounts.risk import RiskManager
        rm = RiskManager({"daily_usd": 100, "pos_size": 500, "max_dd_pct": 0.05})
        big = _real_pkg(meta={"estimated_value": 999_999})
        assert rm.approve(big) is True
        assert rm.approve(_smoke_pkg(meta={"is_test": True, "estimated_value": 999_999})) is True

    def test_approve_bypasses_drawdown_for_test_order(self):
        from src.units.accounts.risk import RiskManager
        rm = RiskManager({"daily_usd": 100, "pos_size": 500, "max_dd_pct": 0.01})
        rm.update_equity(100_000.0)
        rm.update_equity(80_000.0)  # 20% drawdown — way past 1% cap.
        assert rm.approve(_real_pkg()) is False
        assert rm.approve(_smoke_pkg()) is True

    def test_size_order_from_cfg_returns_test_qty(self):
        from src.units.accounts.risk import size_order_from_cfg
        qty = size_order_from_cfg(
            _smoke_pkg(meta={"is_test": True, "test_qty": 0.0002}),
            account_cfg={"risk_pct": 0.01},
            balance_usdt=10_000.0,
        )
        assert qty == 0.0002

    def test_size_order_from_cfg_uses_default_when_test_qty_missing(self):
        from src.units.accounts.risk import size_order_from_cfg, _DEFAULT_TEST_QTY
        qty = size_order_from_cfg(
            _smoke_pkg(meta={"is_test": True}),
            account_cfg={"risk_pct": 0.01},
            balance_usdt=10_000.0,
        )
        assert qty == _DEFAULT_TEST_QTY


# ---------------------------------------------------------------------------
# Execute path: rejection capture
# ---------------------------------------------------------------------------


class TestExecuteSmokePath:
    def test_dry_run_returns_dry_trade_id(self):
        from src.units.accounts.execute import execute_pkg
        tid = execute_pkg(
            _smoke_pkg(),
            account_cfg={"account_id": "bybit_test", "exchange": "bybit", "risk_pct": 0.01},
            exchange_client=None,  # forces dry-run
            balance_usdt=10_000.0,
            dry_run=True,
        )
        assert tid.startswith("dry-")

    def test_bybit_rejection_via_retcode_returned_in_band(self):
        from src.units.accounts.execute import execute_pkg
        client = MagicMock()
        client.get_wallet_balance.return_value = {
            "result": {"list": [{"coin": [{"usdValue": "10000"}]}]}
        }
        # Bybit returns retCode != 0 for too-small qty (no exception).
        client.place_order.return_value = {
            "retCode": 10001,
            "retMsg": "qty invalid",
            "result": {},
        }
        tid = execute_pkg(
            _smoke_pkg(),
            account_cfg={"account_id": "bybit_test", "exchange": "bybit", "risk_pct": 0.01},
            exchange_client=client,
            balance_usdt=10_000.0,
            dry_run=False,
        )
        assert tid.startswith("rejected_too_small:")
        assert "qty invalid" in tid

    def test_bybit_exception_returned_in_band(self):
        from src.units.accounts.execute import execute_pkg
        client = MagicMock()
        client.get_wallet_balance.return_value = {
            "result": {"list": [{"coin": [{"usdValue": "10000"}]}]}
        }
        client.place_order.side_effect = RuntimeError("min order amt 0.001")
        tid = execute_pkg(
            _smoke_pkg(),
            account_cfg={"account_id": "bybit_test", "exchange": "bybit", "risk_pct": 0.01},
            exchange_client=client,
            balance_usdt=10_000.0,
            dry_run=False,
        )
        assert tid.startswith("rejected_too_small:")
        assert "min order amt 0.001" in tid

    def test_unexpected_acceptance_returns_real_order_id(self):
        """If Bybit unexpectedly accepts (qty cap raised? testnet?), don't
        pretend it was rejected — return the real orderId so the operator
        sees the warning and can manually flatten."""
        from src.units.accounts.execute import execute_pkg
        client = MagicMock()
        client.get_wallet_balance.return_value = {
            "result": {"list": [{"coin": [{"usdValue": "10000"}]}]}
        }
        client.place_order.return_value = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"orderId": "real-order-xyz"},
        }
        tid = execute_pkg(
            _smoke_pkg(),
            account_cfg={"account_id": "bybit_test", "exchange": "bybit", "risk_pct": 0.01},
            exchange_client=client,
            balance_usdt=10_000.0,
            dry_run=False,
        )
        assert tid == "real-order-xyz"


# ---------------------------------------------------------------------------
# Coordinator.smoke_test_run
# ---------------------------------------------------------------------------


class TestCoordinatorSmokeRun:
    def test_dry_run_returns_results_for_each_account(self, coord, journal_db):
        result = coord.smoke_test_run(dry_run=True)
        assert result["smoke_id"]
        assert result["ok"] is True
        assert len(result["results"]) == 1
        r = result["results"][0]
        assert r["account_id"] == "bybit_test"
        assert r["status"] == "dry_run"
        assert r["logged"] is True
        assert r["trade_id"].startswith("dry-")

    def test_filtered_by_account(self, coord, journal_db):
        result = coord.smoke_test_run("bybit_test", dry_run=True)
        assert len(result["results"]) == 1
        assert result["results"][0]["account_id"] == "bybit_test"

    def test_unknown_account_returns_error(self, coord, journal_db):
        result = coord.smoke_test_run("does_not_exist", dry_run=True)
        assert result["ok"] is False
        assert "not found" in result.get("error", "")

    def test_writes_row_to_trade_journal(self, coord, journal_db):
        import sqlite3
        coord.smoke_test_run(dry_run=True)
        with sqlite3.connect(journal_db) as conn:
            rows = list(conn.execute(
                "SELECT strategy_name, account_id, status, position_size FROM trades "
                "WHERE strategy_name='smoke_test'"
            ))
        assert len(rows) == 1
        assert rows[0][0] == "smoke_test"
        assert rows[0][1] == "bybit_test"
        assert rows[0][2] == "dry_run"
        # Position size is the test_qty, well below Bybit min-lot.
        assert 0 < rows[0][3] < 0.001

    def test_pushes_dashboards_alert(self, coord, journal_db):
        before = len(coord.list_alerts() or [])
        coord.smoke_test_run(dry_run=True)
        after = coord.list_alerts() or []
        assert len(after) > before
        assert any(
            "smoke_test" in (a.get("message") or "") for a in after[-3:]
        )

    def test_factory_called_per_account(self, coord, journal_db):
        """Multi-account live smokes call the factory once per account so
        keys don't get mis-routed."""
        client = MagicMock()
        client.get_wallet_balance.return_value = {
            "result": {"list": [{"coin": [{"usdValue": "10000"}]}]}
        }
        client.place_order.return_value = {
            "retCode": 10001, "retMsg": "qty invalid", "result": {},
        }
        seen: list[str] = []

        def factory(acc):
            seen.append(acc.get("account_id"))
            return client

        result = coord.smoke_test_run(
            exchange_client_factory=factory,
            dry_run=False,
        )
        assert result["ok"] is True
        assert seen == ["bybit_test"]
        assert result["results"][0]["status"] == "rejected_too_small"

    def test_factory_returning_none_in_live_mode_errors(self, coord, journal_db):
        """If the factory can't resolve creds in LIVE mode, surface the
        problem as an explicit error rather than silently dry-running.
        Silent dry-run was the previous behaviour and it masked broken
        per-account API integration — see /smoke_test fix in S-021."""
        result = coord.smoke_test_run(
            exchange_client_factory=lambda acc: None,
            dry_run=False,
        )
        assert result["ok"] is False
        r = result["results"][0]
        assert r["status"] == "error"
        assert "missing API credentials" in r["reason"]

    def test_factory_returning_none_with_explicit_dry_run_still_dry(self, coord, journal_db):
        """Tests that pass dry_run=True explicitly still get the dry-run
        path (the executor flips to dry-run when client is None). Only
        the silent fallback in LIVE mode is closed off."""
        result = coord.smoke_test_run(
            exchange_client_factory=lambda acc: None,
            dry_run=True,
        )
        assert result["ok"] is True
        assert result["results"][0]["status"] == "dry_run"

    def test_factory_exception_in_live_mode_errors(self, coord, journal_db):
        """Factory errors in LIVE mode are reported as 'missing
        credentials' (with the underlying exception attached) rather
        than silently dry-running."""
        def boom(acc):
            raise RuntimeError("env not loaded")

        result = coord.smoke_test_run(
            exchange_client_factory=boom,
            dry_run=False,
        )
        r = result["results"][0]
        assert r["status"] == "error"
        assert "missing API credentials" in r["reason"]
        assert "env not loaded" in r["reason"]

    def test_explicit_client_overrides_factory(self, coord, journal_db):
        client = MagicMock()
        client.get_wallet_balance.return_value = {
            "result": {"list": [{"coin": [{"usdValue": "10000"}]}]}
        }
        client.place_order.return_value = {
            "retCode": 10001, "retMsg": "qty invalid", "result": {},
        }

        def factory(acc):
            raise AssertionError("factory should not be called")

        result = coord.smoke_test_run(
            exchange_client=client,
            exchange_client_factory=factory,
            dry_run=False,
        )
        assert result["results"][0]["status"] == "rejected_too_small"

    def test_live_path_with_mocked_rejection(self, coord, journal_db):
        """End-to-end: live mode (dry_run=False), client returns retCode=10001."""
        import sqlite3
        client = MagicMock()
        client.get_wallet_balance.return_value = {
            "result": {"list": [{"coin": [{"usdValue": "10000"}]}]}
        }
        client.place_order.return_value = {
            "retCode": 10001,
            "retMsg": "qty invalid",
            "result": {},
        }
        result = coord.smoke_test_run(
            "bybit_test",
            exchange_client=client,
            dry_run=False,
        )
        assert result["ok"] is True
        r = result["results"][0]
        assert r["status"] == "rejected_too_small"
        assert "qty invalid" in r["reason"]
        # Journal row reflects the rejection status.
        with sqlite3.connect(journal_db) as conn:
            rows = list(conn.execute(
                "SELECT status, exit_reason FROM trades "
                "WHERE strategy_name='smoke_test'"
            ))
        assert rows[0][0] == "rejected_too_small"
        assert "qty invalid" in rows[0][1]
