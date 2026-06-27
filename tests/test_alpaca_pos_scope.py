"""Tests for shared-paper-account asset-class isolation (_alpaca_pos_in_scope).

A single Alpaca paper login backs both the equity bot-account (alpaca_paper) and
the options-expression bot-account (alpaca_options_paper). account_open_positions
must show each only its own asset class so the reverse reconciler never adopts the
other's legs as phantom orphans.
"""
from __future__ import annotations

from src.units.accounts.clients import _alpaca_pos_in_scope

OPTIONS_ACCT = {"exchange": "alpaca", "options": {"express_as": "debit_vertical"}}
EQUITY_ACCT = {"exchange": "alpaca"}  # no options block


def test_options_account_keeps_only_option_legs():
    assert _alpaca_pos_in_scope({"asset_class": "us_option"}, OPTIONS_ACCT) is True
    assert _alpaca_pos_in_scope({"asset_class": "us_equity"}, OPTIONS_ACCT) is False


def test_equity_account_drops_option_legs_keeps_equity():
    assert _alpaca_pos_in_scope({"asset_class": "us_equity"}, EQUITY_ACCT) is True
    assert _alpaca_pos_in_scope({"asset_class": "us_option"}, EQUITY_ACCT) is False


def test_equity_account_keeps_unknown_asset_class_legacy_behaviour():
    # No options present / older rows without asset_class → equity account keeps
    # everything that isn't explicitly an option leg (no behaviour change).
    assert _alpaca_pos_in_scope({"asset_class": None}, EQUITY_ACCT) is True
    assert _alpaca_pos_in_scope({}, EQUITY_ACCT) is True


def test_disabled_options_block_treated_as_equity():
    acct = {"exchange": "alpaca", "options": {"express_as": "debit_vertical", "enabled": False}}
    assert _alpaca_pos_in_scope({"asset_class": "us_option"}, acct) is False
    assert _alpaca_pos_in_scope({"asset_class": "us_equity"}, acct) is True
