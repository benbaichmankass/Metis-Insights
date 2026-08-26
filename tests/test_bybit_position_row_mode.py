"""The Bybit diag position row must carry the venue's POSITION MODE.

WHY THIS EXISTS. `positionIdx` (0 = one-way netting, 1 = hedge-long,
2 = hedge-short) is returned by Bybit on every position row and was dropped at
extraction, so "is this account one-way or hedge?" had no read surface anywhere
and was answerable only from prose. That question GATES a Tier-3 change: arming
`BYBIT_HEDGE_MODE_SYMBOLS` for a symbol means flipping a live-order setting that
could be neither confirmed beforehand nor verified afterwards.

The `None` case is the one that matters. An unread mode must NOT read as
0/one-way -- defaulting it to the netting value is exactly the reading that
would make a hedge account look safe to treat as netted.
"""
from __future__ import annotations

from src.units.accounts.clients import _bybit_position_row


def _p(**over):
    base = {"symbol": "BTCUSDT", "side": "Buy", "size": "0.01",
            "avgPrice": "80000", "stopLoss": "79000", "takeProfit": "81000",
            "tpSlMode": "Partial"}
    base.update(over)
    return base


def test_one_way_mode_is_reported_as_zero_not_as_absent():
    # 0 is a MEASURED value ("the venue says one-way"), not a missing one, so it
    # must survive as 0 rather than being falsy-collapsed into None.
    row = _bybit_position_row(_p(positionIdx=0))
    assert row["position_idx"] == 0
    assert row["position_idx"] is not None


def test_hedge_books_are_distinguished_from_each_other():
    assert _bybit_position_row(_p(positionIdx=1))["position_idx"] == 1
    assert _bybit_position_row(_p(positionIdx=2))["position_idx"] == 2


def test_an_unreported_mode_is_none_and_never_defaults_to_one_way():
    # THE LOAD-BEARING CASE. "We did not look" must stay distinguishable from
    # "the venue says one-way"; a 0 here would assert an observation nobody made.
    for absent in ({}, {"positionIdx": None}, {"positionIdx": ""}, {"positionIdx": "n/a"}):
        row = _bybit_position_row(_p(**absent))
        assert row["position_idx"] is None, absent


def test_bybit_returns_positionIdx_as_a_string_and_that_still_resolves():
    # Bybit's V5 REST serialises numerics as strings; an int-only check would
    # silently report every real row as unread.
    assert _bybit_position_row(_p(positionIdx="1"))["position_idx"] == 1


def test_both_read_paths_share_the_field_set():
    """The cross-check path recovers rows the settleCoin page omits, and those
    are the rows a reader is most likely reasoning about -- so it must not be
    missing a field the primary path has."""
    primary = _bybit_position_row(_p(positionIdx=0))
    recovered = _bybit_position_row(_p(positionIdx=0), settlecoin_blind=True)
    assert set(primary) <= set(recovered)
    assert recovered["settlecoin_blind"] is True
    # ...and the flag is absent, not False, on the primary path: the row either
    # came from a blind page or the question does not arise.
    assert "settlecoin_blind" not in primary
