"""Tests for the pure options-lifecycle helpers (Slice-4).

Covers OCC-root extraction, activity-sourced realized PnL, the open-underlying set,
and the concluded-structure decision. The live reconciler in order_monitor composes
these; verifying them directly proves its logic without a live Alpaca account.
"""
from __future__ import annotations

from src.units.accounts.options_lifecycle import (
    OPTION_LIFECYCLE_ACTIVITY_TYPES,
    realized_pnl_from_activities,
    structure_concluded,
    underlying_from_occ,
    underlyings_with_open_options,
)


def test_underlying_from_occ():
    assert underlying_from_occ("SLV260116C00025000") == "SLV"
    assert underlying_from_occ("GDX260220P00030000") == "GDX"
    assert underlying_from_occ("aapl240119c00150000") == "AAPL"
    assert underlying_from_occ("") is None
    assert underlying_from_occ(None) is None
    assert underlying_from_occ("260116C00025000") is None  # rootless


def test_realized_pnl_worthless_expiry_is_full_debit_loss():
    # No lifecycle cash for the underlying → worthless OTM expiry → lose the debit.
    life = realized_pnl_from_activities(
        [], underlying="SLV", net_debit=0.60, contracts=2,
    )
    # open_cost = 0.60 * 100 * 2 = 120 ; close_cash = 0 ; realized = -120
    assert life.open_cost == 120.0
    assert life.close_cash == 0.0
    assert life.realized_pnl == -120.0
    assert life.event_count == 0
    assert life.pnl_source == "alpaca_activity"


def test_realized_pnl_itm_expiry_nets_close_cash_minus_debit():
    acts = [
        {"id": "a1", "activity_type": "OPEXC", "symbol": "SLV260116C00025000", "net_amount": "300.00"},
        {"id": "a2", "activity_type": "OPASN", "symbol": "SLV260116C00027000", "net_amount": "-100.00"},
        # unrelated underlying — ignored
        {"id": "a3", "activity_type": "EXP", "symbol": "GDX260116C00030000", "net_amount": "50.00"},
        # non-lifecycle type — ignored
        {"id": "a4", "activity_type": "FILL", "symbol": "SLV260116C00025000", "net_amount": "999"},
    ]
    life = realized_pnl_from_activities(acts, underlying="SLV", net_debit=0.60, contracts=2)
    # close_cash = 300 - 100 = 200 ; open_cost = 120 ; realized = 80
    assert life.close_cash == 200.0
    assert life.open_cost == 120.0
    assert life.realized_pnl == 80.0
    assert life.event_count == 2
    assert set(life.activity_ids) == {"a1", "a2"}


def test_realized_pnl_skips_malformed_records():
    acts = [
        {"id": "a1", "activity_type": "EXP", "symbol": "SLV260116C00025000", "net_amount": "not_a_number"},
        {"activity_type": "EXP", "symbol": "SLV260116C00025000", "net_amount": "10.00"},  # no id
        None,  # entirely bad
    ]
    life = realized_pnl_from_activities(acts, underlying="SLV", net_debit=0.10, contracts=1)
    # only the 10.00 cash counts; the bad-number row contributes 0 ; open_cost = 10
    assert life.close_cash == 10.0
    assert life.open_cost == 10.0
    assert life.realized_pnl == 0.0
    assert life.event_count == 1  # the no-id row has no id appended


def test_underlyings_with_open_options():
    positions = [
        {"symbol": "SLV260116C00025000"},
        {"symbol": "SLV260116C00027000"},
        {"symbol": "GDX260220P00030000"},
        {"symbol": ""},  # skipped
    ]
    assert underlyings_with_open_options(positions) == {"SLV", "GDX"}


def test_structure_concluded_requires_event_and_absence():
    # Event seen + no open position → concluded.
    assert structure_concluded("SLV", open_option_underlyings=set(), lifecycle_event_seen=True) is True
    # Event seen but still holding a position (e.g. a different leg/expiry) → not yet.
    assert structure_concluded("SLV", open_option_underlyings={"SLV"}, lifecycle_event_seen=True) is False
    # No event but position absent → NOT concluded (the anti-incident guard: position
    # absence alone never closes a row).
    assert structure_concluded("SLV", open_option_underlyings=set(), lifecycle_event_seen=False) is False
    # Blank underlying → never concluded.
    assert structure_concluded("", open_option_underlyings=set(), lifecycle_event_seen=True) is False


def test_lifecycle_activity_types_constant():
    assert OPTION_LIFECYCLE_ACTIVITY_TYPES == ("EXP", "OPASN", "OPEXC")
