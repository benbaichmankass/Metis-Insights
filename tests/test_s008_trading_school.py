"""S-008 PR #125: Trading School unit tests.

Tests are fully offline — no exchange, no Colab, no network.
Covers:
  - validate_metrics() standalone function
  - Coordinator.validate_strategy_update() (YAML thresholds + overrides)
  - trigger_backtest() stub raises NotImplementedError
  - Coordinator.trigger_backtest() raises NotImplementedError
"""
from __future__ import annotations

import os
import textwrap
import tempfile
from typing import Any, Dict

import pytest
import yaml

from src.units.trading_school.validator import validate_metrics, trigger_backtest
from src.core.coordinator import Coordinator, _PAUSED_ACCOUNTS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


MINIMAL_UNITS_YAML = textwrap.dedent("""\
    units:
      strategies:
        - name: test_strat
          service: ict-trader-test
          model: null
          signal_prefixes: [test_sig]
      accounts:
        - id: test_account
          exchange: bybit
          risk_pct: 0.01
          env_path: .env
          strategies: [test_strat]
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
      db:
        trade_journal: trade_journal.db
        signals: data/trades.db
      workflows:
        docs: docs/claude/
""")

UNITS_YAML_WITH_THRESHOLDS = textwrap.dedent("""\
    units:
      strategies:
        - name: test_strat
          service: ict-trader-test
          model: null
          signal_prefixes: [test_sig]
      accounts:
        - id: test_account
          exchange: bybit
          risk_pct: 0.01
          env_path: .env
          strategies: [test_strat]
      dashboards:
        alerts_enabled: true
      return_commands:
        supported:
          - cmd: halt
            action: pause_accounts
      telegram_bot:
        data_source: dashboards
      app:
        extends: telegram_bot
        config_enabled: true
      trading_school:
        auto_backtest: true
        thresholds:
          min_win_rate: 0.50
          min_profit_factor: 1.5
          max_drawdown_pct: 0.20
          min_trades: 10
      db:
        trade_journal: trade_journal.db
        signals: data/trades.db
      workflows:
        docs: docs/claude/
""")


@pytest.fixture()
def units_yaml(tmp_path):
    p = tmp_path / "units.yaml"
    p.write_text(MINIMAL_UNITS_YAML)
    return str(p)


@pytest.fixture()
def units_yaml_with_thresholds(tmp_path):
    p = tmp_path / "units.yaml"
    p.write_text(UNITS_YAML_WITH_THRESHOLDS)
    return str(p)


@pytest.fixture()
def coord(units_yaml):
    _PAUSED_ACCOUNTS.clear()
    c = Coordinator(units_path=units_yaml)
    yield c
    _PAUSED_ACCOUNTS.clear()


@pytest.fixture()
def coord_th(units_yaml_with_thresholds):
    _PAUSED_ACCOUNTS.clear()
    c = Coordinator(units_path=units_yaml_with_thresholds)
    yield c
    _PAUSED_ACCOUNTS.clear()


# ---------------------------------------------------------------------------
# validate_metrics — standalone function
# ---------------------------------------------------------------------------


class TestValidateMetricsStandalone:
    def _good_metrics(self):
        return {
            "win_rate": 0.55,
            "profit_factor": 1.8,
            "drawdown_pct": 0.10,
            "trade_count": 20,
        }

    def test_passing_metrics_returns_ok_true(self):
        result = validate_metrics("my_strat", self._good_metrics())
        assert result["ok"] is True
        assert result["issues"] == []

    def test_returns_strategy_name(self):
        result = validate_metrics("vwap", self._good_metrics())
        assert result["strategy"] == "vwap"

    def test_returns_metrics_copy(self):
        m = self._good_metrics()
        result = validate_metrics("ict", m)
        assert result["metrics"] == m

    def test_low_win_rate_fails(self):
        m = {**self._good_metrics(), "win_rate": 0.30}
        result = validate_metrics("ict", m)
        assert result["ok"] is False
        assert any("Win rate" in i for i in result["issues"])

    def test_low_profit_factor_fails(self):
        m = {**self._good_metrics(), "profit_factor": 0.8}
        result = validate_metrics("ict", m)
        assert result["ok"] is False
        assert any("Profit factor" in i for i in result["issues"])

    def test_high_drawdown_fails(self):
        m = {**self._good_metrics(), "drawdown_pct": 0.45}
        result = validate_metrics("ict", m)
        assert result["ok"] is False
        assert any("Drawdown" in i for i in result["issues"])

    def test_insufficient_trades_fails(self):
        m = {**self._good_metrics(), "trade_count": 3}
        result = validate_metrics("ict", m)
        assert result["ok"] is False
        assert any("Insufficient trades" in i or "trade" in i.lower() for i in result["issues"])

    def test_multiple_failures_listed(self):
        m = {"win_rate": 0.20, "profit_factor": 0.5, "drawdown_pct": 0.50, "trade_count": 2}
        result = validate_metrics("ict", m)
        assert result["ok"] is False
        assert len(result["issues"]) >= 3

    def test_custom_threshold_override_win_rate(self):
        m = {**self._good_metrics(), "win_rate": 0.45}
        # Default threshold is 0.40, so 0.45 should pass; with override of 0.50 it fails
        result_default = validate_metrics("ict", m)
        assert result_default["ok"] is True
        result_strict = validate_metrics("ict", m, thresholds={"min_win_rate": 0.50})
        assert result_strict["ok"] is False

    def test_missing_optional_metrics_does_not_raise(self):
        m = {"trade_count": 10}
        result = validate_metrics("ict", m)
        assert isinstance(result, dict)
        assert "ok" in result

    def test_empty_metrics_reports_insufficient_trades(self):
        result = validate_metrics("ict", {})
        assert result["ok"] is False
        assert any("trade" in i.lower() for i in result["issues"])


# ---------------------------------------------------------------------------
# trigger_backtest — standalone function (wired S-009 PR #1)
# ---------------------------------------------------------------------------


class TestTriggerBacktestStandalone:
    def test_returns_queued_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_QUEUE_PATH", str(tmp_path / "q.json"))
        result = trigger_backtest("test_strat")
        assert result["queued"] is True

    def test_returns_strategy_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_QUEUE_PATH", str(tmp_path / "q.json"))
        result = trigger_backtest("test_strat")
        assert result["strategy"] == "test_strat"

    def test_writes_queue_file(self, tmp_path, monkeypatch):
        import json
        queue = tmp_path / "q.json"
        monkeypatch.setenv("BACKTEST_QUEUE_PATH", str(queue))
        trigger_backtest("test_strat")
        assert queue.exists()
        payload = json.loads(queue.read_text().strip())
        assert payload["strategy"] == "test_strat"


# ---------------------------------------------------------------------------
# Coordinator.validate_strategy_update
# ---------------------------------------------------------------------------


class TestCoordinatorValidateStrategyUpdate:
    def test_returns_ok_for_good_metrics(self, coord):
        result = coord.validate_strategy_update(
            "test_strat",
            {"win_rate": 0.60, "profit_factor": 2.0, "drawdown_pct": 0.08, "trade_count": 30},
        )
        assert result["ok"] is True

    def test_returns_fail_for_bad_metrics(self, coord):
        result = coord.validate_strategy_update(
            "test_strat",
            {"win_rate": 0.20, "profit_factor": 0.5, "drawdown_pct": 0.50, "trade_count": 2},
        )
        assert result["ok"] is False
        assert len(result["issues"]) >= 3

    def test_strategy_name_in_result(self, coord):
        result = coord.validate_strategy_update("my_strategy", {"trade_count": 20})
        assert result["strategy"] == "my_strategy"

    def test_yaml_thresholds_applied(self, coord_th):
        # units.yaml sets min_win_rate=0.50; default is 0.40
        # win_rate=0.45 passes default but should fail with YAML threshold
        result = coord_th.validate_strategy_update(
            "test_strat",
            {"win_rate": 0.45, "profit_factor": 2.0, "drawdown_pct": 0.10, "trade_count": 15},
        )
        assert result["ok"] is False
        assert any("Win rate" in i for i in result["issues"])

    def test_yaml_min_trades_threshold_applied(self, coord_th):
        # units.yaml sets min_trades=10
        result = coord_th.validate_strategy_update(
            "test_strat",
            {"win_rate": 0.60, "profit_factor": 2.0, "drawdown_pct": 0.05, "trade_count": 8},
        )
        assert result["ok"] is False
        assert any("trade" in i.lower() for i in result["issues"])

    def test_caller_threshold_overrides_yaml(self, coord_th):
        # YAML says min_win_rate=0.50; caller overrides to 0.40 → 0.45 should pass
        result = coord_th.validate_strategy_update(
            "test_strat",
            {"win_rate": 0.45, "profit_factor": 2.0, "drawdown_pct": 0.10, "trade_count": 15},
            thresholds={"min_win_rate": 0.40},
        )
        assert result["ok"] is True

    def test_returns_dict_with_required_keys(self, coord):
        result = coord.validate_strategy_update("test_strat", {"trade_count": 10})
        for key in ("ok", "strategy", "metrics", "issues"):
            assert key in result


# ---------------------------------------------------------------------------
# Coordinator.trigger_backtest (wired S-009 PR #1)
# ---------------------------------------------------------------------------


class TestCoordinatorTriggerBacktest:
    def test_queues_job(self, coord, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_QUEUE_PATH", str(tmp_path / "q.json"))
        result = coord.trigger_backtest("test_strat")
        assert result["queued"] is True

    def test_pushes_alert(self, coord, tmp_path, monkeypatch):
        from src.units.dashboards.alerts import clear_alerts
        clear_alerts()
        monkeypatch.setenv("BACKTEST_QUEUE_PATH", str(tmp_path / "q.json"))
        coord.trigger_backtest("test_strat")
        alerts = coord.list_alerts()
        assert any(a.get("source") == "trading_school" for a in alerts)
