"""Tests for the sanctioned strategies.yaml execution-gate writer.

Mirrors set_account_mode.sh's contract: a targeted single-line edit that
flips ``execution: live | shadow`` for one strategy, preserving every
surrounding comment and field byte-for-byte.
"""
from __future__ import annotations

import pytest

from src.bot.strategy_execution_writer import (
    StrategyExecutionWriteError,
    read_strategy_execution,
    set_strategy_execution,
)

SAMPLE = """\
# header comment
strategies:

  turtle_soup:
    model: null
    enabled: true
    execution: live          # inline comment must survive
    risk_pct: 0.5

  vwap:
    enabled: true
    execution: shadow
    risk_pct: 0.3

  no_exec_strat:
    enabled: true
    risk_pct: 0.1
"""


@pytest.fixture
def yaml_file(tmp_path):
    p = tmp_path / "strategies.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


def test_read_returns_current_value(yaml_file):
    assert read_strategy_execution(yaml_file, "turtle_soup") == "live"
    assert read_strategy_execution(yaml_file, "vwap") == "shadow"


def test_read_defaults_to_live_when_absent(yaml_file):
    assert read_strategy_execution(yaml_file, "no_exec_strat") == "live"


def test_flip_live_to_shadow_preserves_inline_comment(yaml_file):
    prev, new = set_strategy_execution(yaml_file, "turtle_soup", "shadow")
    assert (prev, new) == ("live", "shadow")
    text = yaml_file.read_text(encoding="utf-8")
    assert "    execution: shadow          # inline comment must survive" in text
    # Other strategies untouched.
    assert "  vwap:\n    enabled: true\n    execution: shadow" in text
    assert read_strategy_execution(yaml_file, "turtle_soup") == "shadow"


def test_flip_shadow_to_live(yaml_file):
    prev, new = set_strategy_execution(yaml_file, "vwap", "live")
    assert (prev, new) == ("shadow", "live")
    assert read_strategy_execution(yaml_file, "vwap") == "live"


def test_inserts_execution_line_when_missing(yaml_file):
    prev, new = set_strategy_execution(yaml_file, "no_exec_strat", "shadow")
    assert (prev, new) == ("live", "shadow")
    text = yaml_file.read_text(encoding="utf-8")
    assert "  no_exec_strat:\n    execution: shadow\n    enabled: true" in text


def test_unknown_strategy_raises(yaml_file):
    with pytest.raises(StrategyExecutionWriteError):
        set_strategy_execution(yaml_file, "ghost", "shadow")


def test_invalid_execution_raises(yaml_file):
    with pytest.raises(StrategyExecutionWriteError):
        set_strategy_execution(yaml_file, "turtle_soup", "bogus")


def test_only_target_block_changes(yaml_file):
    before = yaml_file.read_text(encoding="utf-8")
    set_strategy_execution(yaml_file, "turtle_soup", "shadow")
    after = yaml_file.read_text(encoding="utf-8")
    # Exactly one line differs.
    diff = [
        (b, a)
        for b, a in zip(before.splitlines(), after.splitlines())
        if b != a
    ]
    assert len(diff) == 1
    assert "turtle_soup" not in diff[0][0]  # the changed line is the execution line
    assert diff[0][0].strip().startswith("execution:")
