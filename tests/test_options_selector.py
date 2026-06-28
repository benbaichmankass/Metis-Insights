"""Tests for the debit-vertical strike/expiry selector (Slice-3a, pure).

Uses a synthetic XLF chain and verifies selection, refusals, the IV-rank gate,
and composition with the sizer (Slice 0) + executor legs (Slice 2).
"""
from __future__ import annotations

import datetime as _dt

import pytest

from src.units.accounts.options_selector import (
    ChainContract,
    select_debit_vertical,
    to_option_legs,
)
from src.units.accounts.options_sizing import size_debit_structure

TODAY = _dt.date(2026, 6, 27)
IN_BAND = "2026-07-31"   # ~34 DTE (in [21,60])
TOO_SOON = "2026-07-05"  # ~8 DTE (below 21)


def _chain():
    rows = []
    # In-band expiry calls (XLF ~ $54): 54-call 0.90, 55-call 0.45 -> 54/55 debit 0.45.
    for strike, mid in [(53, 1.40), (54, 0.90), (55, 0.45), (56, 0.20)]:
        rows.append(ChainContract(f"XLF260731C{strike:05d}000", "call", strike, IN_BAND, mid=mid))
    # In-band expiry puts: 54-put 0.85, 53-put 0.45 -> 54/53 debit 0.40.
    for strike, mid in [(55, 1.30), (54, 0.85), (53, 0.45), (52, 0.20)]:
        rows.append(ChainContract(f"XLF260731P{strike:05d}000", "put", strike, IN_BAND, mid=mid))
    # Too-soon expiry (must be excluded by the DTE band).
    rows.append(ChainContract("XLF260705C00054000", "call", 54, TOO_SOON, mid=0.50))
    rows.append(ChainContract("XLF260705C00055000", "call", 55, TOO_SOON, mid=0.20))
    return rows


def test_bull_call_debit_vertical():
    v = select_debit_vertical(_chain(), direction="long", underlying_price=54.0, today=TODAY)
    assert v.ok
    assert v.long_leg.strike == 54 and v.short_leg.strike == 55
    assert v.width == 1.0
    assert v.net_debit == 0.45
    assert v.max_loss_usd == 45.0
    assert v.max_gain_usd == 55.0
    assert v.breakeven == 54.45
    assert v.expiration == IN_BAND
    assert 21 <= v.dte <= 60


def test_bear_put_debit_vertical():
    v = select_debit_vertical(_chain(), direction="short", underlying_price=54.0, today=TODAY)
    assert v.ok
    assert v.long_leg.strike == 54 and v.short_leg.strike == 53
    assert v.net_debit == 0.40
    assert v.max_loss_usd == 40.0


def test_unknown_direction_refused():
    v = select_debit_vertical(_chain(), direction="sideways", underlying_price=54.0, today=TODAY)
    assert not v.ok and v.reason.startswith("unknown_direction")


def test_no_expiration_in_band_refused():
    only_soon = [c for c in _chain() if c.expiration == TOO_SOON]
    v = select_debit_vertical(only_soon, direction="long", underlying_price=54.0, today=TODAY)
    assert not v.ok and v.reason == "no_expiration_in_dte_band"


def test_iv_rank_gate_blocks_high_iv():
    v = select_debit_vertical(
        _chain(), direction="long", underlying_price=54.0, today=TODAY,
        iv_rank=0.80, max_iv_rank=0.50,
    )
    assert not v.ok and v.reason.startswith("iv_rank_too_high")


def test_iv_rank_gate_passes_low_iv():
    v = select_debit_vertical(
        _chain(), direction="long", underlying_price=54.0, today=TODAY,
        iv_rank=0.20, max_iv_rank=0.50,
    )
    assert v.ok


def test_non_positive_debit_refused():
    # Inverted mids (short richer than long) -> not a debit spread.
    bad = [
        ChainContract("XLF260731C00054000", "call", 54, IN_BAND, mid=0.30),
        ChainContract("XLF260731C00055000", "call", 55, IN_BAND, mid=0.60),
    ]
    v = select_debit_vertical(bad, direction="long", underlying_price=54.0, today=TODAY)
    assert not v.ok and v.reason.startswith("non_positive_debit")


def test_compose_selector_into_sizer():
    v = select_debit_vertical(_chain(), direction="long", underlying_price=54.0, today=TODAY)
    # $0.45 debit -> $45/contract; $150 budget -> 3 contracts.
    sized = size_debit_structure(net_debit=v.net_debit, max_loss_budget_usd=150.0)
    assert sized.contracts == 3
    assert sized.total_max_loss_usd == 135.0


def test_to_option_legs_composes_with_executor():
    v = select_debit_vertical(_chain(), direction="long", underlying_price=54.0, today=TODAY)
    legs = to_option_legs(v)
    assert [leg.side for leg in legs] == ["buy", "sell"]
    assert [leg.position_intent for leg in legs] == ["buy_to_open", "sell_to_open"]
    assert legs[0].symbol == v.long_leg.symbol


def test_to_option_legs_raises_on_refusal():
    v = select_debit_vertical(_chain(), direction="sideways", underlying_price=54.0, today=TODAY)
    with pytest.raises(ValueError):
        to_option_legs(v)
