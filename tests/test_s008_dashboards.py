"""S-008 PR #123: dashboards unit tests.

Fully offline — monkeypatched data_loaders, no DB, no exchange.
Covers AlertsQueue, global alert helpers, stats builder, and
Coordinator dashboard / alert integration.
"""
from __future__ import annotations

import textwrap
from typing import Any, Dict, List

import pytest

from src.core.coordinator import Coordinator, _PAUSED_ACCOUNTS
from src.units.dashboards.alerts import (
    AlertsQueue,
    clear_alerts,
    list_alerts,
    pop_alerts,
    push_alert,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

UNITS_YAML = textwrap.dedent("""\
    units:
      strategies:
        - name: vwap
          service: ict-trader-vwap
          model: null
          signal_prefixes: [vwap]
      accounts:
        - id: live
          exchange: bybit
          risk_pct: 0.01
          env_path: .env
          strategies: [vwap]
      dashboards:
        db:
          trade_journal: trade_journal.db
          signals: data/trades.db
        alerts_enabled: true
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


@pytest.fixture(autouse=True)
def reset_alerts():
    clear_alerts()
    yield
    clear_alerts()


@pytest.fixture()
def units_yaml(tmp_path):
    p = tmp_path / "units.yaml"
    p.write_text(UNITS_YAML)
    return str(p)


@pytest.fixture()
def coord(units_yaml):
    _PAUSED_ACCOUNTS.clear()
    c = Coordinator(units_path=units_yaml)
    yield c
    _PAUSED_ACCOUNTS.clear()


# ---------------------------------------------------------------------------
# AlertsQueue
# ---------------------------------------------------------------------------


class TestAlertsQueue:
    def test_push_returns_dict(self):
        q = AlertsQueue()
        alert = q.push("test message", source="test", level="info")
        assert isinstance(alert, dict)
        assert alert["message"] == "test message"
        assert alert["source"] == "test"
        assert alert["level"] == "info"
        assert "ts" in alert

    def test_list_all_returns_all(self):
        q = AlertsQueue()
        q.push("a")
        q.push("b")
        q.push("c")
        items = q.list_all()
        assert len(items) == 3

    def test_list_all_with_n(self):
        q = AlertsQueue()
        for i in range(5):
            q.push(f"msg-{i}")
        items = q.list_all(n=3)
        assert len(items) == 3
        assert items[-1]["message"] == "msg-4"

    def test_pop_all_drains_queue(self):
        q = AlertsQueue()
        q.push("x")
        q.push("y")
        items = q.pop_all()
        assert len(items) == 2
        assert len(q) == 0

    def test_clear_empties_queue(self):
        q = AlertsQueue()
        q.push("x")
        q.clear()
        assert len(q) == 0

    def test_maxlen_enforced(self):
        q = AlertsQueue(maxlen=3)
        for i in range(5):
            q.push(f"msg-{i}")
        assert len(q) == 3
        items = q.list_all()
        # Oldest two dropped; last three remain
        assert items[0]["message"] == "msg-2"

    def test_extra_fields_stored(self):
        q = AlertsQueue()
        alert = q.push("trade placed", source="accounts", level="info",
                        account_id="live", trade_id="t-001")
        assert alert["account_id"] == "live"
        assert alert["trade_id"] == "t-001"


# ---------------------------------------------------------------------------
# Global alert helpers
# ---------------------------------------------------------------------------


class TestGlobalAlertHelpers:
    def test_push_alert_appends(self):
        push_alert("hello", source="test")
        items = list_alerts()
        assert len(items) == 1
        assert items[0]["message"] == "hello"

    def test_list_alerts_with_n(self):
        for i in range(5):
            push_alert(f"msg-{i}")
        items = list_alerts(n=2)
        assert len(items) == 2

    def test_pop_alerts_drains(self):
        push_alert("a")
        push_alert("b")
        items = pop_alerts()
        assert len(items) == 2
        assert list_alerts() == []

    def test_clear_alerts(self):
        push_alert("x")
        clear_alerts()
        assert list_alerts() == []


# ---------------------------------------------------------------------------
# stats.build_stats()
# ---------------------------------------------------------------------------


class TestBuildStats:
    def _strategy_rows(self):
        return [
            {"strategy": "vwap", "service": "ict-trader-vwap", "model": None,
             "signals_today": 3, "pnl": 100.0, "open_pos": 1, "status": "active"},
        ]

    def _accounts(self):
        return [
            {"account_id": "live", "exchange": "bybit", "risk_pct": 0.01,
             "strategies": ["vwap"]},
        ]

    def test_returns_required_keys(self, monkeypatch):
        from src.units.dashboards import stats as stats_mod
        monkeypatch.setattr("src.bot.data_loaders.account_last_trade", lambda a: None)
        monkeypatch.setattr("src.bot.data_loaders.account_balance", lambda a: None)
        monkeypatch.setattr("src.bot.data_loaders.account_open_positions", lambda a: None)

        from src.units.dashboards.stats import build_stats
        result = build_stats(
            accounts=self._accounts(),
            paused_account_ids=set(),
            paused_strategy_names=set(),
            strategy_rows=self._strategy_rows(),
        )
        for key in ("strategies", "accounts", "alerts", "generated_at"):
            assert key in result

    def test_strategy_paused_flag_false_when_not_paused(self, monkeypatch):
        monkeypatch.setattr("src.bot.data_loaders.account_last_trade", lambda a: None)
        from src.units.dashboards.stats import build_stats
        result = build_stats(
            accounts=self._accounts(),
            paused_account_ids=set(),
            paused_strategy_names=set(),
            strategy_rows=self._strategy_rows(),
        )
        assert result["strategies"][0]["paused"] is False
        assert result["strategies"][0]["status"] == "active"

    def test_account_paused_flag_true_when_halted(self, monkeypatch):
        monkeypatch.setattr("src.bot.data_loaders.account_last_trade", lambda a: None)
        from src.units.dashboards.stats import build_stats
        result = build_stats(
            accounts=self._accounts(),
            paused_account_ids={"live"},
            paused_strategy_names=set(),
            strategy_rows=self._strategy_rows(),
        )
        assert result["accounts"][0]["paused"] is True

    def test_balance_none_when_no_client(self, monkeypatch):
        monkeypatch.setattr("src.bot.data_loaders.account_last_trade", lambda a: None)
        from src.units.dashboards.stats import build_stats
        result = build_stats(
            accounts=self._accounts(),
            paused_account_ids=set(),
            paused_strategy_names=set(),
            strategy_rows=self._strategy_rows(),
            exchange_clients=None,
        )
        assert result["accounts"][0]["balance_usdt"] is None

    def test_alerts_included_from_snapshot(self, monkeypatch):
        monkeypatch.setattr("src.bot.data_loaders.account_last_trade", lambda a: None)
        from src.units.dashboards.stats import build_stats
        snapshot = [{"ts": "now", "source": "test", "level": "info", "message": "hi"}]
        result = build_stats(
            accounts=self._accounts(),
            paused_account_ids=set(),
            paused_strategy_names=set(),
            strategy_rows=self._strategy_rows(),
            alert_snapshot=snapshot,
        )
        assert result["alerts"] == snapshot

    def test_generated_at_is_iso_string(self, monkeypatch):
        monkeypatch.setattr("src.bot.data_loaders.account_last_trade", lambda a: None)
        from src.units.dashboards.stats import build_stats
        result = build_stats(
            accounts=self._accounts(),
            paused_account_ids=set(),
            paused_strategy_names=set(),
            strategy_rows=self._strategy_rows(),
        )
        assert isinstance(result["generated_at"], str)
        assert "T" in result["generated_at"]


# ---------------------------------------------------------------------------
# Coordinator dashboard + alert integration
# ---------------------------------------------------------------------------


class TestCoordinatorDashboards:
    def test_dashboard_stats_has_required_keys(self, coord, monkeypatch):
        monkeypatch.setattr(
            "src.bot.data_loaders.strategy_dashboard_data",
            lambda: [{"strategy": "vwap", "service": "s", "model": None,
                      "signals_today": 0, "pnl": 0.0, "open_pos": 0, "status": "active"}],
        )
        monkeypatch.setattr("src.bot.data_loaders.account_last_trade", lambda a: None)
        stats = coord.dashboard_stats()
        for key in ("strategies", "accounts", "alerts", "generated_at"):
            assert key in stats

    def test_dashboard_stats_accounts_matches_coord_list(self, coord, monkeypatch):
        monkeypatch.setattr(
            "src.bot.data_loaders.strategy_dashboard_data", lambda: [],
        )
        monkeypatch.setattr("src.bot.data_loaders.account_last_trade", lambda a: None)
        stats = coord.dashboard_stats()
        ids_from_stats = {a["account_id"] for a in stats["accounts"]}
        ids_from_list = {a["account_id"] for a in coord.list_accounts()}
        assert ids_from_stats == ids_from_list

    def test_halted_account_shows_paused_true(self, coord, monkeypatch):
        monkeypatch.setattr(
            "src.bot.data_loaders.strategy_dashboard_data", lambda: [],
        )
        monkeypatch.setattr("src.bot.data_loaders.account_last_trade", lambda a: None)
        coord.return_command("halt")
        stats = coord.dashboard_stats()
        live_acc = next(a for a in stats["accounts"] if a["account_id"] == "live")
        assert live_acc["paused"] is True

    def test_push_alert_appears_in_list_alerts(self, coord):
        coord.push_alert("hello from coord", source="test")
        alerts = coord.list_alerts()
        assert any(a["message"] == "hello from coord" for a in alerts)

    def test_pop_alerts_drains(self, coord):
        coord.push_alert("a")
        coord.push_alert("b")
        items = coord.pop_alerts()
        assert len(items) >= 2
        assert coord.list_alerts() == []

    def test_halt_auto_pushes_alert(self, coord):
        coord.return_command("halt")
        alerts = coord.list_alerts()
        assert any(a["source"] == "return_commands" and a["level"] == "warning"
                   for a in alerts)

    def test_resume_auto_pushes_alert(self, coord):
        coord.return_command("halt")
        coord.pop_alerts()  # clear halt alert
        coord.return_command("resume")
        alerts = coord.list_alerts()
        assert any(a["source"] == "return_commands" and a["cmd"] == "resume"
                   for a in alerts)

    def test_list_alerts_with_n(self, coord):
        for i in range(5):
            coord.push_alert(f"msg-{i}")
        items = coord.list_alerts(n=3)
        assert len(items) == 3
