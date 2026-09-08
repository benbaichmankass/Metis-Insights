"""A zero-size sibling book must not make a live hedge symbol read FLAT.

WHAT THIS FILE IS FOR. ``order_monitor._bybit_position_protection`` graded a
symbol off ``rows[0]`` with no zero-size skip and no book selection, so once
HEDGE mode was armed (2026-08-30) a zero-size sibling book listed first returned
the ``_flat`` dict for a symbol that was NOT flat. The netting reconciler then
computed ``backed = 0.0``, made ``excess`` the whole journal row, and closed it.

⚠️ **THE FIRST TEST REPRODUCES THE DEFECT BEFORE ANY TEST SHOWS THE FIX.** It
runs the OLD selection logic — transcribed verbatim, and pinned by
:func:`test_legacy_transcription_matches_the_shipped_code_on_the_one_way_case`
so it cannot quietly drift into a straw man — against the real payload shape
that closed trade 5568, and asserts it produces the wrong verdict. Landing an
order-path change on an argument rather than a failing-then-passing case is what
``BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`` was.

The measured incident (``docs/claude/work/BYBIT-HEDGE-BOOK-FLAT-READ-2026-09-08.md``):
``bybit_1`` ETHUSDT held a live long 26.05 on ``positionIdx=1`` while the venue's
row list also carried the empty short book ``positionIdx=2``.
"""
from __future__ import annotations

import pytest

from src.runtime.bybit_position_book import (
    STATES,
    BookSelection,
    select_position_row,
)


def _row(idx, side, size, **over):
    row = {"symbol": "ETHUSDT", "positionIdx": idx, "side": side,
           "size": size, "avgPrice": "2476.6", "stopLoss": "", "tpSlMode": "Partial"}
    row.update(over)
    return row


#: The live long book that trade 5568 belonged to.
LIVE_LONG = _row(1, "Buy", "26.05")
#: Its empty hedge sibling — the row that was listed first and read as flat.
EMPTY_SHORT = _row(2, "Sell", "0")


# ---------------------------------------------------------------------------
# 1. REPRODUCE THE DEFECT
# ---------------------------------------------------------------------------

def _legacy_select(rows):
    """The shipped pre-fix logic, transcribed verbatim from order_monitor.

        if not rows:
            return _flat
        pos = rows[0]
        size = abs(float(pos.get("size") or 0) or 0.0)   # -> None on ValueError
        if size <= 0:
            return _flat

    Returns ``("flat", None)``, ``("live", size)`` or ``("unreadable", None)``.
    """
    if not rows:
        return ("flat", None)
    pos = rows[0]
    try:
        size = abs(float(pos.get("size") or 0) or 0.0)
    except (TypeError, ValueError):
        return ("unreadable", None)
    if size <= 0:
        return ("flat", None)
    return ("live", size)


def test_legacy_transcription_matches_the_shipped_code_on_the_one_way_case():
    # POSITIVE CONTROL for the transcription itself. If `_legacy_select` did not
    # reproduce the old behaviour on the case the old behaviour got RIGHT, the
    # defect test below would be arguing with a straw man.
    assert _legacy_select([_row(0, "Buy", "26.05")]) == ("live", 26.05)
    assert _legacy_select([_row(0, "Buy", "0")]) == ("flat", None)


def test_DEFECT_legacy_reads_a_live_hedge_symbol_as_flat():
    # The venue holds 26.05 long. The empty short book is listed FIRST.
    rows = [EMPTY_SHORT, LIVE_LONG]

    # This is the bug, reproduced: "flat" for a symbol carrying a live position.
    assert _legacy_select(rows) == ("flat", None)

    # And it is ORDER-DEPENDENT, which is why it was invisible for so long:
    # the identical book state read correctly whenever the venue happened to
    # list the live book first.
    assert _legacy_select([LIVE_LONG, EMPTY_SHORT]) == ("live", 26.05)


def test_DEFECT_legacy_reads_an_empty_response_as_flat():
    # The other half: an empty list is "the venue enumerated nothing", and the
    # old code graded it as a positive finding of flatness.
    assert _legacy_select([]) == ("flat", None)


def test_DEFECT_legacy_silently_picks_one_of_two_live_books():
    both = [_row(1, "Buy", "26.05"), _row(2, "Sell", "10.0")]
    # It picks the first and reports 26.05 as though it were the whole symbol,
    # with nothing anywhere recording that a second live book exists.
    assert _legacy_select(both) == ("live", 26.05)
    assert _legacy_select(list(reversed(both))) == ("live", 10.0)


# ---------------------------------------------------------------------------
# 2. THE SAME CASES, CORRECT
# ---------------------------------------------------------------------------

def test_live_book_is_selected_regardless_of_row_order():
    for rows in ([EMPTY_SHORT, LIVE_LONG], [LIVE_LONG, EMPTY_SHORT]):
        sel = select_position_row(rows)
        assert sel.state == "selected"
        assert sel.is_usable
        assert sel.size == 26.05
        assert sel.position_idx == 1
        assert sel.row is LIVE_LONG
        assert sel.live_count == 1
        assert sel.row_count == 2


def test_empty_response_is_no_rows_and_is_NOT_flat():
    sel = select_position_row([])
    assert sel.state == "no_rows"
    assert sel.state != "flat"          # the distinction that closed trade 5568
    assert not sel.is_usable
    assert sel.row is None
    assert sel.row_count == 0
    assert "NOT evidence" in sel.detail


def test_none_response_is_no_rows_not_flat():
    assert select_position_row(None).state == "no_rows"


def test_all_books_zero_is_flat_and_that_is_a_real_measurement():
    # The venue enumerated both books and neither holds anything. Unlike
    # `no_rows`, this IS evidence of flatness and the caller may act on it.
    sel = select_position_row([EMPTY_SHORT, _row(1, "Buy", "0")])
    assert sel.state == "flat"
    assert sel.live_count == 0
    assert sel.row_count == 2
    assert sel.row is None


def test_one_way_flat_single_row_is_flat():
    assert select_position_row([_row(0, "", "0")]).state == "flat"


def test_two_live_books_refuse_rather_than_pick():
    both = [_row(1, "Buy", "26.05"), _row(2, "Sell", "10.0")]
    for rows in (both, list(reversed(both))):
        sel = select_position_row(rows)
        assert sel.state == "ambiguous_multi_book"
        assert not sel.is_usable
        assert sel.row is None          # refuses to hand back either book
        assert sel.live_count == 2
        # The refusal names both books, so the operator log says WHICH.
        assert "positionIdx=1" in sel.detail and "positionIdx=2" in sel.detail


@pytest.mark.parametrize("bad", ["", "abc", None, {}, []])
def test_unparseable_size_is_fatal_for_the_whole_symbol(bad):
    # Not "skip the bad row and grade the rest": with one size unknown we cannot
    # prove exactly one book is live, so `selected` would be a guess.
    sel = select_position_row([_row(2, "Sell", bad), LIVE_LONG])
    assert sel.state == "size_unreadable"
    assert not sel.is_usable
    assert sel.row is None


def test_missing_size_key_is_unreadable_not_zero():
    row = {"symbol": "ETHUSDT", "positionIdx": 2, "side": "Sell"}
    assert select_position_row([row]).state == "size_unreadable"


def test_non_mapping_row_is_unreadable_not_a_crash():
    assert select_position_row(["nonsense"]).state == "size_unreadable"


# ---------------------------------------------------------------------------
# 3. CONTRACT
# ---------------------------------------------------------------------------

def test_size_is_absolute_so_a_signed_short_is_still_live():
    sel = select_position_row([_row(2, "Sell", "-4.2")])
    assert sel.state == "selected"
    assert sel.size == 4.2


def test_every_declared_state_is_reachable():
    # A state nobody can produce is a state nobody branches on.
    seen = {
        select_position_row([LIVE_LONG]).state,
        select_position_row([EMPTY_SHORT]).state,
        select_position_row([]).state,
        select_position_row([LIVE_LONG, _row(2, "Sell", "1")]).state,
        select_position_row([_row(1, "Buy", "x")]).state,
    }
    assert seen == set(STATES)


def test_non_selected_states_never_hand_back_a_row_or_a_size():
    # `size 0.0` on a refusal must never be read as "flat"; the guard is that
    # there is no row to act on either.
    for rows in ([], [_row(1, "Buy", "x")], [LIVE_LONG, _row(2, "Sell", "1")]):
        sel = select_position_row(rows)
        assert sel.row is None and sel.size == 0.0 and not sel.is_usable


def test_selection_is_pure_and_does_not_mutate_the_payload():
    rows = [dict(EMPTY_SHORT), dict(LIVE_LONG)]
    before = [dict(r) for r in rows]
    select_position_row(rows)
    assert rows == before


def test_frozen_verdict_cannot_be_edited_by_a_caller():
    sel = select_position_row([LIVE_LONG])
    with pytest.raises(Exception):
        sel.state = "flat"  # type: ignore[misc]


def test_book_selection_defaults_are_the_refusal_shape():
    # A bare BookSelection must not look like a usable live book.
    assert not BookSelection(state="no_rows").is_usable
