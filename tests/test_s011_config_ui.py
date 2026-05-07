"""S-011 PR #4: Strategy Config UI — data helper and loader tests.

Tests load_strategy_config, save_strategy_config, apply_edits,
validate_strategy_params, and Coordinator.reload_strategy_config().
No Streamlit dependency — helpers are tested directly.
All data is hand-crafted, no network/exchange calls.
"""
from __future__ import annotations

import textwrap

import pytest

from src.units.strategies import load_strategy_config, save_strategy_config
from src.web.config_ui import (
    apply_edits,
    validate_strategy_params,
    get_editable_fields,
)


STRATEGIES_YAML = textwrap.dedent("""\
    strategies:
      ict:
        service: ict-trader-ict
        model: null
        signal_prefixes: [fvg, ob]
        enabled: true
        risk_pct: 1.0
        timeframe: "5m"
        symbols:
          - BTCUSDT
          - ETHUSDT
        confidence_threshold: 0.6
      vwap:
        service: ict-trader-vwap
        model: null
        signal_prefixes: [vwap]
        enabled: true
        risk_pct: 1.0
        timeframe: "15m"
        symbols:
          - BTCUSDT
        threshold: 0.01
      killzone:
        service: ict-trader-live
        model: null
        signal_prefixes: [killzone]
        enabled: false
        risk_pct: 0.5
        timeframe: "5m"
        symbols:
          - BTCUSDT
""")


@pytest.fixture()
def strategies_yaml(tmp_path):
    p = tmp_path / "strategies.yaml"
    p.write_text(STRATEGIES_YAML)
    return str(p)


# ---------------------------------------------------------------------------
# load_strategy_config
# ---------------------------------------------------------------------------

class TestLoadStrategyConfig:
    def test_returns_dict(self, strategies_yaml):
        cfg = load_strategy_config(strategies_yaml)
        assert isinstance(cfg, dict)

    def test_all_strategies_loaded(self, strategies_yaml):
        cfg = load_strategy_config(strategies_yaml)
        assert set(cfg.keys()) == {"ict", "vwap", "killzone"}

    def test_params_correct(self, strategies_yaml):
        cfg = load_strategy_config(strategies_yaml)
        assert cfg["ict"]["risk_pct"] == 1.0
        assert cfg["ict"]["timeframe"] == "5m"
        assert "BTCUSDT" in cfg["ict"]["symbols"]

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_strategy_config(str(tmp_path / "nonexistent.yaml"))

    def test_enabled_flag_loaded(self, strategies_yaml):
        cfg = load_strategy_config(strategies_yaml)
        assert cfg["killzone"]["enabled"] is False
        assert cfg["ict"]["enabled"] is True


# ---------------------------------------------------------------------------
# save_strategy_config
# ---------------------------------------------------------------------------

class TestSaveStrategyConfig:
    def test_save_preserves_unedited_fields(self, strategies_yaml):
        load_strategy_config(strategies_yaml)
        # Only update risk_pct for ict
        save_strategy_config({"ict": {"risk_pct": 2.0}}, strategies_yaml)
        updated = load_strategy_config(strategies_yaml)
        assert updated["ict"]["risk_pct"] == 2.0
        # service and signal_prefixes preserved
        assert updated["ict"]["service"] == "ict-trader-ict"
        assert "fvg" in updated["ict"]["signal_prefixes"]

    def test_save_updates_multiple_strategies(self, strategies_yaml):
        save_strategy_config(
            {"ict": {"risk_pct": 0.5}, "vwap": {"threshold": 0.02}},
            strategies_yaml,
        )
        cfg = load_strategy_config(strategies_yaml)
        assert cfg["ict"]["risk_pct"] == 0.5
        assert cfg["vwap"]["threshold"] == 0.02

    def test_save_updates_enabled_flag(self, strategies_yaml):
        save_strategy_config({"killzone": {"enabled": True}}, strategies_yaml)
        cfg = load_strategy_config(strategies_yaml)
        assert cfg["killzone"]["enabled"] is True

    def test_save_and_reload_symbols(self, strategies_yaml):
        new_symbols = ["BTCUSDT", "SOLUSDT"]
        save_strategy_config({"ict": {"symbols": new_symbols}}, strategies_yaml)
        cfg = load_strategy_config(strategies_yaml)
        assert cfg["ict"]["symbols"] == new_symbols


# ---------------------------------------------------------------------------
# apply_edits
# ---------------------------------------------------------------------------

class TestApplyEdits:
    def _current(self):
        return {
            "ict": {"risk_pct": 1.0, "timeframe": "5m", "service": "ict-trader"},
            "vwap": {"risk_pct": 1.0, "threshold": 0.01},
        }

    def test_applies_single_edit(self):
        result = apply_edits(self._current(), {"ict": {"risk_pct": 2.0}})
        assert result["ict"]["risk_pct"] == 2.0

    def test_preserves_unedited_fields(self):
        result = apply_edits(self._current(), {"ict": {"risk_pct": 2.0}})
        assert result["ict"]["service"] == "ict-trader"

    def test_applies_edits_across_strategies(self):
        result = apply_edits(
            self._current(),
            {"ict": {"risk_pct": 0.5}, "vwap": {"threshold": 0.02}},
        )
        assert result["ict"]["risk_pct"] == 0.5
        assert result["vwap"]["threshold"] == 0.02

    def test_adds_new_strategy(self):
        result = apply_edits(self._current(), {"new_strat": {"risk_pct": 1.0}})
        assert "new_strat" in result

    def test_does_not_mutate_original(self):
        current = self._current()
        apply_edits(current, {"ict": {"risk_pct": 99.0}})
        assert current["ict"]["risk_pct"] == 1.0


# ---------------------------------------------------------------------------
# validate_strategy_params
# ---------------------------------------------------------------------------

class TestValidateStrategyParams:
    def test_valid_params_no_errors(self):
        assert validate_strategy_params({"risk_pct": 1.0, "confidence_threshold": 0.6}) == []

    def test_risk_pct_zero_invalid(self):
        errors = validate_strategy_params({"risk_pct": 0.0})
        assert any("risk_pct" in e for e in errors)

    def test_risk_pct_over_100_invalid(self):
        errors = validate_strategy_params({"risk_pct": 101.0})
        assert any("risk_pct" in e for e in errors)

    def test_confidence_threshold_over_1_invalid(self):
        errors = validate_strategy_params({"confidence_threshold": 1.5})
        assert any("confidence_threshold" in e for e in errors)

    def test_confidence_threshold_negative_invalid(self):
        errors = validate_strategy_params({"confidence_threshold": -0.1})
        assert any("confidence_threshold" in e for e in errors)

    def test_non_numeric_risk_pct_invalid(self):
        errors = validate_strategy_params({"risk_pct": "bad"})
        assert any("risk_pct" in e for e in errors)

    def test_empty_params_no_errors(self):
        assert validate_strategy_params({}) == []


# ---------------------------------------------------------------------------
# get_editable_fields
# ---------------------------------------------------------------------------

class TestGetEditableFields:
    def test_returns_editable_subset(self):
        cfg = {"risk_pct": 1.0, "timeframe": "5m", "service": "svc",
               "model": None, "signal_prefixes": ["x"]}
        editable = get_editable_fields(cfg)
        assert "risk_pct" in editable
        assert "timeframe" in editable
        assert "service" not in editable
        assert "model" not in editable

    def test_includes_symbols_when_present(self):
        cfg = {"symbols": ["BTCUSDT"], "service": "svc"}
        editable = get_editable_fields(cfg)
        assert "symbols" in editable


# ---------------------------------------------------------------------------
# Coordinator.reload_strategy_config
# ---------------------------------------------------------------------------

class TestCoordinatorReloadStrategyConfig:
    def _coord(self):
        from src.core.coordinator import Coordinator
        return Coordinator()

    def test_returns_reloaded_true(self, strategies_yaml):
        coord = self._coord()
        result = coord.reload_strategy_config(strategies_yaml)
        assert result["reloaded"] is True

    def test_returns_correct_count(self, strategies_yaml):
        coord = self._coord()
        result = coord.reload_strategy_config(strategies_yaml)
        assert result["strategy_count"] == 3

    def test_returns_strategy_names(self, strategies_yaml):
        coord = self._coord()
        result = coord.reload_strategy_config(strategies_yaml)
        assert set(result["strategies"]) == {"ict", "vwap", "killzone"}

    def test_pushes_app_alert(self, strategies_yaml):
        coord = self._coord()
        coord.pop_alerts()
        coord.reload_strategy_config(strategies_yaml)
        alerts = coord.list_alerts()
        assert any(
            "Strategy config reloaded" in a.get("message", "") and a.get("source") == "app"
            for a in alerts
        )

    def test_missing_file_returns_error(self, tmp_path):
        coord = self._coord()
        result = coord.reload_strategy_config(str(tmp_path / "gone.yaml"))
        assert result["reloaded"] is False
        assert "error" in result

    def test_edit_then_reload_picks_up_changes(self, strategies_yaml):
        save_strategy_config({"ict": {"risk_pct": 3.0}}, strategies_yaml)
        coord = self._coord()
        result = coord.reload_strategy_config(strategies_yaml)
        assert result["reloaded"] is True
        cfg = load_strategy_config(strategies_yaml)
        assert cfg["ict"]["risk_pct"] == 3.0
