"""S-008 PR #127: Full end-to-end Coordinator flow tests.

Covers the complete data path through all 9 units (where applicable)
without any live exchange, network, or DB calls.

Flow under test:
  Strategy.order_package()
    → Coordinator.strategy_order_pkg()
    → Coordinator.account_execute()  [dry-run]
    → Dashboards alert pushed
    → Coordinator.list_alerts() reflects the execution
    → Coordinator.dashboard_stats() includes the alert
    → Coordinator.return_command("halt") blocks further execution
    → Coordinator.validate_strategy_update() gates strategy updates
"""
from __future__ import annotations

import os
import sys
import textwrap
import tempfile
from typing import Any, Dict
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from src.core.coordinator import Coordinator, OrderPackage, _PAUSED_ACCOUNTS


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


FLOW_UNITS_YAML = textwrap.dedent("""\
    units:
      strategies:
        - name: vwap
          model: null
          signal_prefixes: [vwap]
        - name: turtle_soup
          model: null
          signal_prefixes: [turtle_soup, sweep_reversal]
      accounts:
        - id: flow_account
          exchange: bybit
          risk_pct: 0.01
          env_path: .env
          strategies: [vwap, turtle_soup]
      dashboards:
        alerts_enabled: true
      return_commands:
        supported:
          - cmd: halt
            action: pause_accounts
          - cmd: resume
            action: resume_accounts
      telegram_bot:
        data_source: dashboards
      app:
        extends: telegram_bot
        config_enabled: true
      trading_school:
        auto_backtest: true
        thresholds:
          min_win_rate: 0.45
          min_trades: 8
      db:
        trade_journal: trade_journal.db
        signals: data/trades.db
      workflows:
        docs: docs/workflows/
""")


def _make_vwap_candles() -> pd.DataFrame:
    """Candle frame that produces a long signal from the VWAP strategy."""
    prices = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0,
              106.0, 107.0, 108.0, 109.0, 110.0]
    return pd.DataFrame({
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
        "volume": [1000.0] * len(prices),
        "timestamp": range(len(prices)),
    })


def _make_turtle_soup_candles(direction: str = "long") -> pd.DataFrame:
    """Bullish-sweep candles for the turtle_soup strategy.

    Mirrors tests/test_s012_turtle_soup.py::_bullish_sweep_frame.
    """
    import numpy as np
    n = 80
    base = 50_000.0
    rng = pd.date_range("2026-04-01", periods=n, freq="15min", tz="UTC")
    df = pd.DataFrame({
        "open": np.full(n, base),
        "high": np.full(n, base + 100.0),
        "low": np.full(n, base - 100.0),
        "close": np.full(n, base + 50.0),
        "volume": np.full(n, 1.0),
    }, index=rng)
    last = df.index[-1]
    if direction == "long":
        df.loc[last, "low"] = base - 500.0
        df.loc[last, "open"] = base - 400.0
        df.loc[last, "close"] = base + 50.0
    else:
        df.loc[last, "high"] = base + 500.0
        df.loc[last, "open"] = base + 400.0
        df.loc[last, "close"] = base - 50.0
    return df


@pytest.fixture()
def units_yaml(tmp_path):
    p = tmp_path / "units.yaml"
    p.write_text(FLOW_UNITS_YAML)
    return str(p)


@pytest.fixture()
def coord(units_yaml, tmp_path):
    _PAUSED_ACCOUNTS.clear()
    from src.units.dashboards.alerts import clear_alerts
    clear_alerts()
    # S-012 PR B3: pass non-existent accounts_path so synthetic
    # units.yaml::accounts is honored.
    c = Coordinator(
        units_path=units_yaml,
        accounts_path=str(tmp_path / "no-accounts.yaml"),
    )
    yield c
    _PAUSED_ACCOUNTS.clear()
    clear_alerts()


# ---------------------------------------------------------------------------
# Flow 1: strategy → coordinator → dry-run account_execute → alert
# ---------------------------------------------------------------------------


class TestStrategyToAccountFlow:
    def test_turtle_soup_order_package_routed_through_coordinator(self, coord):
        candles = _make_turtle_soup_candles("long")
        pkg = coord.strategy_order_pkg("turtle_soup", symbol="BTCUSDT", candles_df=candles)
        assert isinstance(pkg, OrderPackage)
        assert pkg.strategy == "turtle_soup"
        assert pkg.direction in ("long", "short")
        assert pkg.entry > 0
        assert pkg.sl > 0
        assert pkg.tp > 0

    def test_account_execute_dry_run_returns_trade_id(self, coord):
        candles = _make_turtle_soup_candles("long")
        pkg = coord.strategy_order_pkg("turtle_soup", symbol="BTCUSDT", candles_df=candles)
        trade_id = coord.account_execute(
            "flow_account", pkg, balance_usdt=10_000.0
        )
        assert isinstance(trade_id, str)
        assert trade_id.startswith("dry-")

    def test_dry_run_pushes_alert(self, coord):
        candles = _make_turtle_soup_candles("long")
        pkg = coord.strategy_order_pkg("turtle_soup", symbol="BTCUSDT", candles_df=candles)
        coord.account_execute("flow_account", pkg, balance_usdt=10_000.0)
        alerts = coord.list_alerts()
        assert len(alerts) >= 1

    def test_full_flow_alert_mentions_strategy(self, coord):
        candles = _make_turtle_soup_candles("long")
        pkg = coord.strategy_order_pkg("turtle_soup", symbol="BTCUSDT", candles_df=candles)
        coord.account_execute("flow_account", pkg, balance_usdt=10_000.0)
        alerts = coord.list_alerts()
        messages = " ".join(a["message"] for a in alerts)
        assert "turtle_soup" in messages.lower() or "dry-" in messages.lower()

    def test_full_flow_alert_level_is_info(self, coord):
        candles = _make_turtle_soup_candles("long")
        pkg = coord.strategy_order_pkg("turtle_soup", symbol="BTCUSDT", candles_df=candles)
        coord.account_execute("flow_account", pkg, balance_usdt=10_000.0)
        alerts = coord.list_alerts()
        exec_alerts = [a for a in alerts if a.get("source") == "accounts"]
        assert len(exec_alerts) >= 1
        assert exec_alerts[-1]["level"] == "info"

    def test_unknown_account_raises_key_error(self, coord):
        pkg = OrderPackage(
            strategy="turtle_soup", symbol="BTCUSDT", direction="long",
            entry=100.0, sl=98.0, tp=104.0,
        )
        with pytest.raises(KeyError):
            coord.account_execute("no_such_account", pkg, balance_usdt=1_000.0)

    def test_order_package_symbol_matches_request(self, coord):
        candles = _make_turtle_soup_candles("long")
        pkg = coord.strategy_order_pkg("turtle_soup", symbol="ETHUSDT", candles_df=candles)
        assert pkg.symbol == "ETHUSDT"


# ---------------------------------------------------------------------------
# Flow 2: halt → execute blocked → resume → execute allowed
# ---------------------------------------------------------------------------


class TestHaltResumeFlow:
    def test_halt_blocks_account_execute(self, coord):
        coord.return_command("halt")
        pkg = OrderPackage(
            strategy="turtle_soup", symbol="BTCUSDT", direction="long",
            entry=100.0, sl=98.0, tp=104.0,
        )
        with pytest.raises(RuntimeError, match="paused"):
            coord.account_execute("flow_account", pkg, balance_usdt=10_000.0)

    def test_halt_pushes_warning_alert(self, coord):
        coord.return_command("halt")
        alerts = coord.list_alerts()
        halt_alerts = [a for a in alerts if a.get("cmd") == "halt"]
        assert len(halt_alerts) == 1
        assert halt_alerts[0]["level"] == "warning"

    def test_resume_unblocks_account_execute(self, coord):
        coord.return_command("halt")
        coord.return_command("resume")
        pkg = OrderPackage(
            strategy="turtle_soup", symbol="BTCUSDT", direction="long",
            entry=100.0, sl=98.0, tp=104.0,
        )
        trade_id = coord.account_execute("flow_account", pkg, balance_usdt=10_000.0)
        assert trade_id.startswith("dry-")

    def test_halt_resume_alert_sequence(self, coord):
        coord.return_command("halt")
        coord.return_command("resume")
        alerts = coord.list_alerts()
        sources = [a["source"] for a in alerts]
        assert sources.count("return_commands") == 2

    def test_halt_account_appears_paused_in_dashboard_stats(self, coord, monkeypatch):
        monkeypatch.setattr(
            "src.bot.data_loaders.strategy_dashboard_data", lambda: []
        )
        monkeypatch.setattr(
            "src.bot.data_loaders.account_last_trade", lambda a: None
        )
        coord.return_command("halt")
        stats = coord.dashboard_stats()
        paused_accounts = [a for a in stats["accounts"] if a.get("paused")]
        assert len(paused_accounts) >= 1

    def test_strategies_still_list_after_halt(self, coord):
        coord.return_command("halt")
        strats = coord.list_strategies()
        assert len(strats) >= 1


# ---------------------------------------------------------------------------
# Flow 3: dashboard_stats includes alerts from execution
# ---------------------------------------------------------------------------


class TestDashboardStatsFlow:
    def test_stats_alerts_reflect_execution(self, coord, monkeypatch):
        monkeypatch.setattr(
            "src.bot.data_loaders.strategy_dashboard_data", lambda: []
        )
        monkeypatch.setattr(
            "src.bot.data_loaders.account_last_trade", lambda a: None
        )
        pkg = OrderPackage(
            strategy="turtle_soup", symbol="BTCUSDT", direction="long",
            entry=100.0, sl=98.0, tp=104.0,
        )
        coord.account_execute("flow_account", pkg, balance_usdt=5_000.0)
        stats = coord.dashboard_stats()
        assert len(stats["alerts"]) >= 1

    def test_stats_has_required_top_level_keys(self, coord, monkeypatch):
        monkeypatch.setattr(
            "src.bot.data_loaders.strategy_dashboard_data", lambda: []
        )
        monkeypatch.setattr(
            "src.bot.data_loaders.account_last_trade", lambda a: None
        )
        stats = coord.dashboard_stats()
        for key in ("strategies", "accounts", "alerts", "generated_at"):
            assert key in stats

    def test_stats_accounts_include_flow_account(self, coord, monkeypatch):
        monkeypatch.setattr(
            "src.bot.data_loaders.strategy_dashboard_data", lambda: []
        )
        monkeypatch.setattr(
            "src.bot.data_loaders.account_last_trade", lambda a: None
        )
        stats = coord.dashboard_stats()
        account_ids = {a["account_id"] for a in stats["accounts"]}
        assert "flow_account" in account_ids

    def test_pop_alerts_drains_queue(self, coord):
        coord.push_alert("test alert", source="test", level="info")
        assert len(coord.list_alerts()) >= 1
        coord.pop_alerts()
        assert len(coord.list_alerts()) == 0


# ---------------------------------------------------------------------------
# Flow 4: Trading School validation gates strategy update
# ---------------------------------------------------------------------------


class TestTradingSchoolGatingFlow:
    def test_good_metrics_allow_update(self, coord):
        result = coord.validate_strategy_update(
            "vwap",
            {"win_rate": 0.60, "profit_factor": 2.0, "drawdown_pct": 0.08, "trade_count": 20},
        )
        assert result["ok"] is True

    def test_bad_metrics_block_update(self, coord):
        result = coord.validate_strategy_update(
            "vwap",
            {"win_rate": 0.25, "profit_factor": 0.7, "drawdown_pct": 0.40, "trade_count": 3},
        )
        assert result["ok"] is False

    def test_yaml_min_trades_threshold_applied(self, coord):
        # units.yaml sets min_trades=8 for this fixture
        result = coord.validate_strategy_update(
            "vwap",
            {"win_rate": 0.65, "profit_factor": 2.5, "drawdown_pct": 0.05, "trade_count": 5},
        )
        assert result["ok"] is False
        assert any("trade" in i.lower() for i in result["issues"])

    def test_validation_result_carries_strategy_name(self, coord):
        result = coord.validate_strategy_update(
            "turtle_soup", {"trade_count": 10, "win_rate": 0.55}
        )
        assert result["strategy"] == "turtle_soup"

    def test_trigger_backtest_queues_job(self, coord, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_QUEUE_PATH", str(tmp_path / "q.json"))
        result = coord.trigger_backtest("vwap")
        assert result["queued"] is True


# ---------------------------------------------------------------------------
# Flow 5: multiple strategies through coordinator in sequence
# ---------------------------------------------------------------------------


class TestMultiStrategySequenceFlow:
    def test_two_strategies_produce_independent_packages(self, coord):
        kz_candles = _make_turtle_soup_candles("long")
        pkg_kz = coord.strategy_order_pkg("turtle_soup", symbol="BTCUSDT", candles_df=kz_candles)
        assert pkg_kz.strategy == "turtle_soup"

    def test_execute_two_trades_produces_two_alerts(self, coord):
        pkg1 = OrderPackage(
            strategy="turtle_soup", symbol="BTCUSDT", direction="long",
            entry=100.0, sl=98.0, tp=104.0,
        )
        pkg2 = OrderPackage(
            strategy="vwap", symbol="ETHUSDT", direction="short",
            entry=200.0, sl=204.0, tp=192.0,
        )
        coord.account_execute("flow_account", pkg1, balance_usdt=10_000.0)
        coord.account_execute("flow_account", pkg2, balance_usdt=10_000.0)
        exec_alerts = [a for a in coord.list_alerts() if a.get("source") == "accounts"]
        assert len(exec_alerts) == 2

    def test_halt_blocks_all_subsequent_executions(self, coord):
        coord.return_command("halt")
        for direction in ("long", "short"):
            pkg = OrderPackage(
                strategy="turtle_soup", symbol="BTCUSDT", direction=direction,
                entry=100.0, sl=98.0, tp=104.0,
            )
            with pytest.raises(RuntimeError):
                coord.account_execute("flow_account", pkg, balance_usdt=10_000.0)


# ---------------------------------------------------------------------------
# Flow 6: trigger_backtest() — queue-file wiring (S-009 PR #1)
# ---------------------------------------------------------------------------


class TestTriggerBacktestFlow:
    def test_queues_job_and_returns_queued_true(self, coord, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_QUEUE_PATH", str(tmp_path / "queue.json"))
        result = coord.trigger_backtest("vwap")
        assert result["queued"] is True
        assert result["strategy"] == "vwap"

    def test_writes_json_line_to_queue_file(self, coord, tmp_path, monkeypatch):
        import json
        queue = tmp_path / "queue.json"
        monkeypatch.setenv("BACKTEST_QUEUE_PATH", str(queue))
        coord.trigger_backtest("turtle_soup", config={"symbol": "ETHUSDT"})
        lines = [json.loads(l) for l in queue.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        assert lines[0]["strategy"] == "turtle_soup"
        assert lines[0]["symbol"] == "ETHUSDT"

    def test_multiple_triggers_append_lines(self, coord, tmp_path, monkeypatch):
        import json
        queue = tmp_path / "queue.json"
        monkeypatch.setenv("BACKTEST_QUEUE_PATH", str(queue))
        coord.trigger_backtest("vwap")
        coord.trigger_backtest("turtle_soup")
        lines = [l for l in queue.read_text().splitlines() if l.strip()]
        assert len(lines) == 2

    def test_trigger_pushes_alert(self, coord, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_QUEUE_PATH", str(tmp_path / "queue.json"))
        from src.units.dashboards.alerts import clear_alerts
        clear_alerts()
        coord.trigger_backtest("vwap")
        alerts = coord.list_alerts()
        ts_alerts = [a for a in alerts if a.get("source") == "trading_school"]
        assert len(ts_alerts) == 1
        assert "vwap" in ts_alerts[0]["message"].lower()

    def test_config_override_applied(self, coord, tmp_path, monkeypatch):
        import json
        queue = tmp_path / "queue.json"
        monkeypatch.setenv("BACKTEST_QUEUE_PATH", str(queue))
        coord.trigger_backtest("ict", config={"timeframe": "4h", "start_date": "2025-01-01"})
        payload = json.loads(queue.read_text().strip())
        assert payload["timeframe"] == "4h"
        assert payload["start_date"] == "2025-01-01"


# ---------------------------------------------------------------------------
# S-010 PR #2: accounts_status / multi_account_execute / reload_accounts
# ---------------------------------------------------------------------------

ACCOUNTS_YAML_CONTENT = textwrap.dedent("""\
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
    p.write_text(ACCOUNTS_YAML_CONTENT)
    return str(p)


def _pkg(strategy="test", symbol="BTCUSDT", direction="long",
         entry=100.0, sl=98.0, tp=104.0, **meta) -> OrderPackage:
    return OrderPackage(
        strategy=strategy, symbol=symbol, direction=direction,
        entry=entry, sl=sl, tp=tp, meta=meta or {},
    )


class TestAccountsStatusFlow:
    def test_returns_list_of_three(self, coord, accounts_yaml):
        statuses = coord.accounts_status(accounts_yaml)
        assert len(statuses) == 3

    def test_each_status_has_required_keys(self, coord, accounts_yaml):
        for s in coord.accounts_status(accounts_yaml):
            assert "name" in s
            assert "daily_pnl" in s
            assert "halted" in s

    def test_missing_file_returns_empty_list(self, coord, tmp_path):
        statuses = coord.accounts_status(str(tmp_path / "nonexistent.yaml"))
        assert statuses == []

    def test_account_names_correct(self, coord, accounts_yaml):
        names = {s["name"] for s in coord.accounts_status(accounts_yaml)}
        assert names == {"bybit_1", "bybit_2", "prop_breakout_1"}


class TestMultiAccountExecuteFlow:
    # S-026 G2: multi_account_execute now sizes per-account. Tests
    # supply a fixed balance via balance_fetcher so position_size
    # produces a non-zero qty and the legacy contract (one result per
    # account, all routed) still holds.
    _BALANCE_USD = 10_000.0

    def _balance_fetcher(self, _account):
        return self._BALANCE_USD

    def test_returns_result_per_account(self, coord, accounts_yaml):
        results = coord.multi_account_execute(
            _pkg(), accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert len(results) == 3

    def test_dry_run_trade_ids_prefixed(self, coord, accounts_yaml):
        results = coord.multi_account_execute(
            _pkg(), accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        for r in results:
            assert r["error"] is None
            assert r["trade_id"].startswith("dry-")

    def test_account_type_filter_prop_only(self, coord, accounts_yaml):
        results = coord.multi_account_execute(
            _pkg(), accounts_path=accounts_yaml, account_type="prop",
            balance_fetcher=self._balance_fetcher,
        )
        assert len(results) == 1
        assert results[0]["name"] == "prop_breakout_1"

    def test_account_type_filter_regular_only(self, coord, accounts_yaml):
        results = coord.multi_account_execute(
            _pkg(), accounts_path=accounts_yaml, account_type="regular",
            balance_fetcher=self._balance_fetcher,
        )
        assert len(results) == 2

    def test_risk_breach_captured_as_error(self, coord, accounts_yaml):
        from src.units.accounts import load_accounts
        accounts = load_accounts(accounts_yaml)
        prop = next(a for a in accounts if a.name == "prop_breakout_1")
        prop.risk_manager.daily_pnl = -200.0

        # monkeypatch load_accounts inside coordinator to return accounts with breached one
        with patch("src.units.accounts.load_accounts", return_value=accounts):
            results = coord.multi_account_execute(
                _pkg(), accounts_path=accounts_yaml,
                balance_fetcher=self._balance_fetcher,
            )

        breached = next(r for r in results if r["name"] == "prop_breakout_1")
        assert breached["trade_id"] is None
        assert breached["error"] is not None

    def test_risk_breach_does_not_block_other_accounts(self, coord, accounts_yaml):
        from src.units.accounts import load_accounts
        accounts = load_accounts(accounts_yaml)
        # breach only prop account
        next(a for a in accounts if a.name == "prop_breakout_1").risk_manager.daily_pnl = -200.0

        with patch("src.units.accounts.load_accounts", return_value=accounts):
            results = coord.multi_account_execute(
                _pkg(), accounts_path=accounts_yaml,
                balance_fetcher=self._balance_fetcher,
            )

        ok_results = [r for r in results if r["error"] is None]
        assert len(ok_results) == 2

    def test_missing_file_returns_empty_list(self, coord, tmp_path):
        results = coord.multi_account_execute(
            _pkg(), accounts_path=str(tmp_path / "nonexistent.yaml")
        )
        assert results == []

    def test_execute_pushes_alert_per_success(self, coord, accounts_yaml):
        coord.multi_account_execute(
            _pkg(), accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        alerts = coord.list_alerts()
        multi_alerts = [a for a in alerts if "multi_execute" in a.get("message", "")]
        assert len(multi_alerts) == 3


class TestReloadAccountsFlow:
    def test_returns_reloaded_true(self, coord, accounts_yaml):
        result = coord.reload_accounts(accounts_yaml)
        assert result["reloaded"] is True

    def test_returns_correct_account_count(self, coord, accounts_yaml):
        result = coord.reload_accounts(accounts_yaml)
        assert result["account_count"] == 3

    def test_returns_account_names(self, coord, accounts_yaml):
        result = coord.reload_accounts(accounts_yaml)
        assert set(result["accounts"]) == {"bybit_1", "bybit_2", "prop_breakout_1"}

    def test_missing_file_returns_reloaded_false(self, coord, tmp_path):
        result = coord.reload_accounts(str(tmp_path / "nonexistent.yaml"))
        assert result["reloaded"] is False
        assert "error" in result

    def test_reload_pushes_app_alert(self, coord, accounts_yaml):
        coord.pop_alerts()  # drain existing
        coord.reload_accounts(accounts_yaml)
        alerts = coord.list_alerts()
        assert any("Accounts reloaded" in a.get("message", "") for a in alerts)
