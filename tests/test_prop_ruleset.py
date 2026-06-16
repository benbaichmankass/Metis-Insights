"""Tests for src.prop.ruleset — load + validate the breakout.yaml ruleset."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.prop.ruleset import RulesetValidationError, load_ruleset, parse_ruleset

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BREAKOUT = _REPO_ROOT / "config" / "prop_rulesets" / "breakout.yaml"


def test_loads_breakout_confirmed_values():
    rs = load_ruleset(_BREAKOUT)
    assert rs.ruleset == "breakout"
    assert rs.plan == "1-step-classic"
    # CONFIRMED 1-Step Classic values (design §4).
    assert rs.evaluation.profit_target_pct == pytest.approx(0.10)
    assert rs.limits.daily_loss_pct == pytest.approx(0.03)
    assert rs.limits.max_drawdown_pct == pytest.approx(0.06)
    assert rs.limits.drawdown_type == "static"
    # one-phase eval
    assert rs.funded.profit_target_pct is None
    assert rs.funded_soak_days == 30


def test_confirmed_flag_is_false():
    # Headline 1-Step Classic rules were confirmed from Breakout's FAQ
    # (2026-06-16), so the ruleset is no longer a placeholder.
    rs = load_ruleset(_BREAKOUT)
    assert rs.unconfirmed is False


def test_unconfirmed_fields_present_and_tagged():
    rs = load_ruleset(_BREAKOUT)
    # min_trading_days default 0 (UNCONFIRMED)
    assert rs.evaluation.min_trading_days == 0
    # max_eval_days null → None (time-unlimited, UNCONFIRMED)
    assert rs.evaluation.max_eval_days is None
    # max_position_pct null (UNCONFIRMED)
    assert rs.limits.max_position_pct is None
    # consistency off (UNCONFIRMED presence)
    assert rs.consistency.enabled is False
    assert rs.consistency.max_single_day_profit_share == pytest.approx(0.40)
    # weekend/overnight (UNCONFIRMED) — crypto trades 24/7
    assert rs.restrictions.weekend_flat is False
    assert rs.restrictions.overnight_flat is False


def test_to_dict_roundtrips_key_fields():
    rs = load_ruleset(_BREAKOUT)
    d = rs.to_dict()
    assert d["ruleset"] == "breakout"
    assert d["unconfirmed"] is False
    assert d["limits"]["drawdown_type"] == "static"
    assert d["evaluation"]["profit_target_pct"] == pytest.approx(0.10)


def test_defaults_when_minimal():
    rs = parse_ruleset({"ruleset": "minimal"})
    assert rs.ruleset == "minimal"
    assert rs.account_size_usd == pytest.approx(25_000.0)
    assert rs.profit_split == pytest.approx(0.80)
    assert rs.unconfirmed is False
    assert rs.limits.drawdown_type == "static"
    assert rs.limits.daily_loss_pct is None
    assert rs.funded_soak_days == 30


def test_rejects_missing_name():
    with pytest.raises(RulesetValidationError):
        parse_ruleset({"plan": "x"})


def test_rejects_bad_drawdown_type():
    with pytest.raises(RulesetValidationError):
        parse_ruleset({"ruleset": "x", "limits": {"drawdown_type": "rolling"}})


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_ruleset(_REPO_ROOT / "config" / "prop_rulesets" / "does_not_exist.yaml")
