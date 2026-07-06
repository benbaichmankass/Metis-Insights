"""BL-20260628-PROP-ISPROP-PREDICATE-DRIFT — the single canonical prop predicate.

Consolidates the three divergent ``is_prop`` checks (account_rulesets,
telegram_report_handler, prop_journal) onto ``prop_identity.is_prop_account``.
These tests pin the union semantics + case-insensitivity + null-safety, and
prove each old call site's inputs still classify identically for the real
account shapes.
"""
from __future__ import annotations

import pytest

from src.prop.prop_identity import is_prop_account


@pytest.mark.parametrize("acct", [
    {"exchange": "breakout"},
    {"exchange": "BREAKOUT"},          # case-insensitive
    {"exchange": " Breakout "},        # whitespace-tolerant
    {"account_class": "prop"},
    {"account_class": "PROP"},
    {"type": "prop"},
    {"backtest_ruleset": "prop_rulesets/breakout.yaml"},
    {"backtest_ruleset": "anything_non_standard"},
    # real breakout_1 shape hits multiple signals at once
    {"exchange": "breakout", "account_class": "prop", "type": "prop"},
])
def test_prop_signals_true(acct):
    assert is_prop_account(acct) is True


@pytest.mark.parametrize("acct", [
    {},
    {"exchange": "bybit"},
    {"exchange": "interactive_brokers", "account_class": "real_money"},
    {"account_class": "paper"},
    {"type": "regular"},
    {"backtest_ruleset": "standard"},   # explicit standard is NOT prop
    {"backtest_ruleset": "STANDARD"},
    {"backtest_ruleset": ""},           # empty falls through
    {"backtest_ruleset": None},
    {"exchange": "bybit", "account_class": "real_money", "type": "regular"},
])
def test_non_prop_false(acct):
    assert is_prop_account(acct) is False


def test_non_mapping_is_false_never_raises():
    assert is_prop_account(None) is False        # type: ignore[arg-type]
    assert is_prop_account("breakout") is False  # type: ignore[arg-type]
    assert is_prop_account(123) is False         # type: ignore[arg-type]


def test_union_recovers_all_three_old_predicates():
    # account_rulesets predicate: backtest_ruleset != standard OR exchange==breakout
    assert is_prop_account({"backtest_ruleset": "prop_rulesets/x.yaml"}) is True
    assert is_prop_account({"exchange": "breakout"}) is True
    # telegram/journal predicate: account_class==prop (was case-insensitive)
    assert is_prop_account({"account_class": "prop"}) is True
    # journal-only predicate: type==prop
    assert is_prop_account({"type": "prop"}) is True
