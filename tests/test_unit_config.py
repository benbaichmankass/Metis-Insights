"""S-009 PR #2: App unit config operations — dynamic unit loader tests.

Tests load_enabled_units(), list_enabled_strategies(), and
Coordinator.reload_units() fully offline (no exchange, no network).
"""
from __future__ import annotations

import os
import textwrap

import pytest
import yaml

from src.units import load_enabled_units, list_enabled_strategies
from src.core.coordinator import Coordinator, _PAUSED_ACCOUNTS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

YAML_ALL_ENABLED = textwrap.dedent("""\
    units:
      strategies:
        - name: ict
          enabled: true
          service: ict-trader-ict
        - name: vwap
          enabled: true
          service: ict-trader-vwap
        - name: breakout_confirmation
          enabled: false
          service: ict-trader-breakout
        - name: killzone
          enabled: true
          service: ict-trader-live
      accounts:
        - id: live
          enabled: true
          exchange: bybit
          risk_pct: 0.01
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
      db:
        trade_journal: trade_journal.db
        signals: data/trades.db
      workflows:
        docs: docs/claude/
""")

YAML_NO_ENABLED_FIELD = textwrap.dedent("""\
    units:
      strategies:
        - name: ict
          service: ict-trader-ict
        - name: vwap
          service: ict-trader-vwap
      accounts:
        - id: live
          exchange: bybit
          risk_pct: 0.01
      dashboards:
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


@pytest.fixture()
def yaml_with_toggles(tmp_path):
    p = tmp_path / "units.yaml"
    p.write_text(YAML_ALL_ENABLED)
    return str(p)


@pytest.fixture()
def yaml_no_enabled_field(tmp_path):
    p = tmp_path / "units.yaml"
    p.write_text(YAML_NO_ENABLED_FIELD)
    return str(p)


@pytest.fixture()
def coord(yaml_with_toggles):
    _PAUSED_ACCOUNTS.clear()
    from src.units.dashboards.alerts import clear_alerts
    clear_alerts()
    c = Coordinator(units_path=yaml_with_toggles)
    yield c
    _PAUSED_ACCOUNTS.clear()
    clear_alerts()


# ---------------------------------------------------------------------------
# load_enabled_units()
# ---------------------------------------------------------------------------

class TestLoadEnabledUnits:
    def test_returns_dict(self, yaml_with_toggles):
        result = load_enabled_units(yaml_with_toggles)
        assert isinstance(result, dict)

    def test_strategies_key_present(self, yaml_with_toggles):
        result = load_enabled_units(yaml_with_toggles)
        assert "strategies" in result

    def test_disabled_strategy_excluded(self, yaml_with_toggles):
        result = load_enabled_units(yaml_with_toggles)
        names = [s["name"] for s in result["strategies"]]
        assert "breakout_confirmation" not in names

    def test_enabled_strategies_included(self, yaml_with_toggles):
        result = load_enabled_units(yaml_with_toggles)
        names = [s["name"] for s in result["strategies"]]
        assert "ict" in names
        assert "vwap" in names
        assert "killzone" in names

    def test_enabled_count_correct(self, yaml_with_toggles):
        result = load_enabled_units(yaml_with_toggles)
        assert len(result["strategies"]) == 3

    def test_no_enabled_field_defaults_to_enabled(self, yaml_no_enabled_field):
        result = load_enabled_units(yaml_no_enabled_field)
        assert len(result["strategies"]) == 2

    def test_non_list_sections_pass_through(self, yaml_with_toggles):
        result = load_enabled_units(yaml_with_toggles)
        assert "dashboards" in result
        assert result["dashboards"]["alerts_enabled"] is True

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_enabled_units(str(tmp_path / "nonexistent.yaml"))


# ---------------------------------------------------------------------------
# list_enabled_strategies()
# ---------------------------------------------------------------------------

class TestListEnabledStrategies:
    def test_returns_list_of_names(self, yaml_with_toggles):
        names = list_enabled_strategies(yaml_with_toggles)
        assert isinstance(names, list)
        assert all(isinstance(n, str) for n in names)

    def test_excludes_disabled(self, yaml_with_toggles):
        names = list_enabled_strategies(yaml_with_toggles)
        assert "breakout_confirmation" not in names

    def test_includes_enabled(self, yaml_with_toggles):
        names = list_enabled_strategies(yaml_with_toggles)
        assert set(names) == {"ict", "vwap", "killzone"}


# ---------------------------------------------------------------------------
# Coordinator.reload_units()
# ---------------------------------------------------------------------------

class TestCoordinatorReloadUnits:
    def test_returns_reloaded_true(self, coord):
        result = coord.reload_units()
        assert result["reloaded"] is True

    def test_returns_enabled_strategies(self, coord):
        result = coord.reload_units()
        assert "enabled_strategies" in result
        assert "breakout_confirmation" not in result["enabled_strategies"]

    def test_returns_strategy_count(self, coord):
        result = coord.reload_units()
        assert isinstance(result["strategy_count"], int)

    def test_pushes_alert_on_reload(self, coord):
        from src.units.dashboards.alerts import clear_alerts
        clear_alerts()
        coord.reload_units()
        alerts = coord.list_alerts()
        reload_alerts = [a for a in alerts if a.get("source") == "app"]
        assert len(reload_alerts) == 1
        assert "reloaded" in reload_alerts[0]["message"].lower()

    def test_config_reflects_updated_yaml(self, coord, yaml_with_toggles):
        # Patch yaml to re-enable breakout_confirmation
        import yaml as _yaml
        data = _yaml.safe_load(open(yaml_with_toggles).read())
        for s in data["units"]["strategies"]:
            s["enabled"] = True
        with open(yaml_with_toggles, "w") as fh:
            _yaml.dump(data, fh)

        coord.reload_units()
        # After reload all 4 strategies should appear in list_strategies()
        names = [s["name"] for s in coord.list_strategies()]
        assert "breakout_confirmation" in names
