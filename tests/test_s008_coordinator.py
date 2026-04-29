"""S-008 PR #120: Coordinator (TRANSLATOR) unit tests.

Tests are fully offline — no DB, no exchange, no network.
Strategy / account execution stubs (PRs #121, #122) are tested via
NotImplementedError assertions.
"""
from __future__ import annotations

import os
import tempfile
import textwrap
from typing import Any, Dict

import pytest
import yaml

from src.core.coordinator import (
    Coordinator,
    OrderPackage,
    _PAUSED_ACCOUNTS,
    is_paused,
)


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
        db:
          trade_journal: trade_journal.db
          signals: data/trades.db
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


@pytest.fixture()
def units_yaml(tmp_path):
    p = tmp_path / "units.yaml"
    p.write_text(MINIMAL_UNITS_YAML)
    return str(p)


@pytest.fixture()
def coord(units_yaml, tmp_path):
    _PAUSED_ACCOUNTS.clear()
    # S-012 PR B3: Coordinator now prefers config/accounts.yaml when present.
    # For these synthetic-fixture tests, pass a non-existent accounts_path
    # so the Coordinator falls back to units.yaml::accounts (the legacy
    # path the fixture relies on).
    c = Coordinator(
        units_path=units_yaml,
        accounts_path=str(tmp_path / "no-accounts.yaml"),
    )
    yield c
    _PAUSED_ACCOUNTS.clear()


# ---------------------------------------------------------------------------
# units.yaml loading
# ---------------------------------------------------------------------------


class TestUnitsYamlLoading:
    def test_loads_without_error(self, coord):
        assert coord._cfg != {}

    def test_missing_yaml_does_not_raise(self, tmp_path):
        _PAUSED_ACCOUNTS.clear()
        c = Coordinator(units_path=str(tmp_path / "nonexistent.yaml"))
        assert c._cfg == {}

    def test_real_units_yaml_exists(self):
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..")
        )
        path = os.path.join(repo_root, "config", "units.yaml")
        assert os.path.exists(path), "config/units.yaml must exist"

    def test_real_units_yaml_has_nine_units(self):
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..")
        )
        path = os.path.join(repo_root, "config", "units.yaml")
        with open(path) as fh:
            data = yaml.safe_load(fh)
        units = data.get("units") or {}
        assert len(units) == 9, f"Expected 9 units, got {len(units)}: {list(units)}"

    def test_real_units_yaml_unit_names(self):
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..")
        )
        path = os.path.join(repo_root, "config", "units.yaml")
        with open(path) as fh:
            data = yaml.safe_load(fh)
        units = set((data.get("units") or {}).keys())
        expected = {
            "strategies", "accounts", "dashboards", "return_commands",
            "telegram_bot", "app", "trading_school", "db", "workflows",
        }
        assert expected == units


# ---------------------------------------------------------------------------
# Unit 1 — Strategies
# ---------------------------------------------------------------------------


class TestStrategies:
    def test_list_strategies_returns_list(self, coord):
        strats = coord.list_strategies()
        assert isinstance(strats, list)

    def test_list_strategies_contains_test_strat(self, coord):
        names = [s["name"] for s in coord.list_strategies()]
        assert "test_strat" in names

    def test_strategy_cfg_returns_dict(self, coord):
        cfg = coord._strategy_cfg("test_strat")
        assert cfg["name"] == "test_strat"

    def test_strategy_cfg_unknown_returns_fallback(self, coord):
        cfg = coord._strategy_cfg("no_such_strategy")
        assert cfg == {"name": "no_such_strategy"}

    def test_strategy_order_pkg_raises_not_implemented(self, coord):
        with pytest.raises(NotImplementedError):
            coord.strategy_order_pkg("test_strat")

    def test_strategy_order_pkg_message_mentions_pr121(self, coord):
        with pytest.raises(NotImplementedError, match="PR #121"):
            coord.strategy_order_pkg("test_strat")


# ---------------------------------------------------------------------------
# Unit 2 — Accounts
# ---------------------------------------------------------------------------


class TestAccounts:
    def test_list_accounts_returns_list(self, coord):
        accounts = coord.list_accounts()
        assert isinstance(accounts, list)

    def test_list_accounts_contains_test_account(self, coord):
        ids = [a["account_id"] for a in coord.list_accounts()]
        assert "test_account" in ids

    def test_list_accounts_has_exchange_field(self, coord):
        acc = coord.list_accounts()[0]
        assert "exchange" in acc

    def test_account_execute_dry_run_returns_trade_id(self, coord):
        """account_execute() now wired (PR #122): dry-run returns a trade_id string."""
        pkg = OrderPackage(
            strategy="test_strat", symbol="BTCUSDT", direction="long",
            entry=50000.0, sl=49000.0, tp=52000.0,
        )
        trade_id = coord.account_execute("test_account", pkg, balance_usdt=10_000.0)
        assert isinstance(trade_id, str)
        assert trade_id.startswith("dry-")

    def test_account_execute_unknown_account_raises(self, coord):
        """Requesting an account not in units.yaml raises KeyError."""
        pkg = OrderPackage(
            strategy="test_strat", symbol="BTCUSDT", direction="long",
            entry=50000.0, sl=49000.0, tp=52000.0,
        )
        with pytest.raises(KeyError):
            coord.account_execute("no_such_account_xyz", pkg)

    def test_is_account_paused_default_false(self, coord):
        assert coord.is_account_paused("test_account") is False


# ---------------------------------------------------------------------------
# Unit 3 — Dashboards
# ---------------------------------------------------------------------------


class TestDashboards:
    def test_dashboard_stats_keys(self, coord, monkeypatch):
        monkeypatch.setattr(
            "src.bot.data_loaders.strategy_dashboard_data",
            lambda: [{"strategy": "test_strat"}],
        )
        stats = coord.dashboard_stats()
        assert "strategies" in stats
        assert "accounts" in stats

    def test_dashboard_stats_strategies_is_list(self, coord, monkeypatch):
        monkeypatch.setattr(
            "src.bot.data_loaders.strategy_dashboard_data",
            lambda: [],
        )
        stats = coord.dashboard_stats()
        assert isinstance(stats["strategies"], list)

    def test_dashboard_stats_accounts_contain_same_ids(self, coord, monkeypatch):
        """dashboard_stats() enriches accounts; account_ids must match list_accounts()."""
        monkeypatch.setattr(
            "src.bot.data_loaders.strategy_dashboard_data",
            lambda: [],
        )
        monkeypatch.setattr("src.bot.data_loaders.account_last_trade", lambda a: None)
        stats = coord.dashboard_stats()
        stat_ids = {a["account_id"] for a in stats["accounts"]}
        cfg_ids = {a["account_id"] for a in coord.list_accounts()}
        assert stat_ids == cfg_ids


# ---------------------------------------------------------------------------
# Unit 4 — Return Commands
# ---------------------------------------------------------------------------


class TestReturnCommands:
    def test_halt_returns_ok(self, coord):
        result = coord.return_command("halt")
        assert result["status"] == "ok"
        assert result["cmd"] == "halt"

    def test_halt_with_slash_prefix(self, coord):
        result = coord.return_command("/halt")
        assert result["cmd"] == "halt"
        assert result["status"] == "ok"

    def test_halt_pauses_accounts(self, coord):
        coord.return_command("halt")
        assert coord.is_account_paused("test_account") is True

    def test_killswitch_alias_halts(self, coord):
        result = coord.return_command("killswitch")
        assert result["status"] == "ok"
        assert coord.is_account_paused("test_account") is True

    def test_pause_alias_halts(self, coord):
        result = coord.return_command("pause")
        assert result["status"] == "ok"
        assert coord.is_account_paused("test_account") is True

    def test_resume_returns_ok(self, coord):
        coord.return_command("halt")
        result = coord.return_command("resume")
        assert result["status"] == "ok"
        assert result["cmd"] == "resume"

    def test_resume_unpauses_accounts(self, coord):
        coord.return_command("halt")
        coord.return_command("resume")
        assert coord.is_account_paused("test_account") is False

    def test_unpause_alias_resumes(self, coord):
        coord.return_command("halt")
        result = coord.return_command("unpause")
        assert result["status"] == "ok"
        assert coord.is_account_paused("test_account") is False

    def test_unknown_command_returns_error(self, coord):
        result = coord.return_command("unknown_cmd")
        assert result["status"] == "error"
        assert "unknown_cmd" in result["detail"]

    def test_halt_result_lists_paused_accounts(self, coord):
        result = coord.return_command("halt")
        assert "test_account" in result["paused"]

    def test_resume_result_lists_resumed_accounts(self, coord):
        coord.return_command("halt")
        result = coord.return_command("resume")
        assert "test_account" in result["resumed"]

    def test_strategies_not_affected_by_halt(self, coord):
        coord.return_command("halt")
        # Strategies unit is independent — list_strategies still works
        assert isinstance(coord.list_strategies(), list)


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


class TestModuleLevelHelpers:
    def test_is_paused_returns_false_by_default(self):
        _PAUSED_ACCOUNTS.discard("some_account")
        assert is_paused("some_account") is False

    def test_is_paused_returns_true_after_add(self):
        _PAUSED_ACCOUNTS.add("some_account")
        assert is_paused("some_account") is True
        _PAUSED_ACCOUNTS.discard("some_account")


# ---------------------------------------------------------------------------
# OrderPackage dataclass
# ---------------------------------------------------------------------------


class TestOrderPackage:
    def test_required_fields(self):
        pkg = OrderPackage(
            strategy="ict", symbol="ETHUSDT", direction="short",
            entry=3000.0, sl=3100.0, tp=2800.0,
        )
        assert pkg.strategy == "ict"
        assert pkg.direction == "short"
        assert pkg.confidence == 0.0
        assert pkg.meta == {}

    def test_optional_fields(self):
        pkg = OrderPackage(
            strategy="vwap", symbol="BTCUSDT", direction="long",
            entry=50000.0, sl=49000.0, tp=52000.0,
            confidence=0.75, meta={"source": "vwap_cross"},
        )
        assert pkg.confidence == 0.75
        assert pkg.meta["source"] == "vwap_cross"
