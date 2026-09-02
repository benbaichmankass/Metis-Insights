"""Both-direction controls for the Bybit protective-leg SIDE classifier.

The defect these guard against (2026-09-02): the over-cover page described the
side-blind SL sum as if it were coverage of the graded position, so a position
covered EXACTLY 1.00x by its own legs was paged as "2656%" over-protected while
the real condition — legs acting on a book we did not grade — went unnamed.

⚠️ EVERY POSITIVE HAS ITS NEGATIVE HERE, deliberately. A classifier tested only
on the case it was written for is a classifier that has been shown to fire, not
one that has been shown to DISCRIMINATE; the repo's own rule is that a probe
must find a positive before its silence means anything, and the converse holds
for a probe that must stay silent.
"""

import pytest

from src.runtime import bybit_leg_sides as bls
from src.runtime import order_monitor as om


# --------------------------------------------------------------------------
# the vocabularies cannot drift from order_monitor's own side normaliser
# --------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expect", [
    ("Buy", "long"), ("buy", "long"), ("long", "long"),
    ("Sell", "short"), ("sell", "short"), ("short", "short"),
    ("", ""), (None, ""), ("garbage", ""),
])
def test_side_normalisation_matches_order_monitor(raw, expect):
    """A private copy is only safe while it agrees with the original."""
    assert bls._norm_side(raw) == expect
    assert om._norm_position_side(raw) == expect


# --------------------------------------------------------------------------
# classify_leg_side — all four states, both directions
# --------------------------------------------------------------------------
def test_sell_leg_reduces_a_long():
    assert bls.classify_leg_side("Buy", "Sell") == bls.LEG_REDUCES_GRADED_BOOK


def test_buy_leg_does_not_reduce_a_long():
    """The live BTCUSDT case: Buy reduce-only cannot touch a long."""
    assert bls.classify_leg_side("Buy", "Buy") == bls.LEG_REDUCES_OTHER_BOOK


def test_buy_leg_reduces_a_short():
    """The MIRROR — otherwise the classifier could be inverted and still pass."""
    assert bls.classify_leg_side("Sell", "Buy") == bls.LEG_REDUCES_GRADED_BOOK


def test_sell_leg_does_not_reduce_a_short():
    assert bls.classify_leg_side("Sell", "Sell") == bls.LEG_REDUCES_OTHER_BOOK


@pytest.mark.parametrize("bad", [None, "", "  ", "Bid"])
def test_unreadable_leg_side_is_its_own_state(bad):
    """We did not look != we looked and it protects nothing."""
    assert bls.classify_leg_side("Buy", bad) == bls.LEG_SIDE_UNREADABLE


@pytest.mark.parametrize("bad", [None, "", "flat", "Bid"])
def test_unreadable_position_side_grades_every_leg_ungradeable(bad):
    """With no position side there is no 'opposite' — never guess one."""
    assert bls.classify_leg_side(bad, "Sell") == bls.POSITION_SIDE_UNREADABLE
    assert bls.classify_leg_side(bad, "Buy") == bls.POSITION_SIDE_UNREADABLE
    # ...and an unreadable position side WINS over an unreadable leg side:
    # nothing is gradeable, so reporting the leg as the problem would point at
    # the wrong half.
    assert bls.classify_leg_side(bad, None) == bls.POSITION_SIDE_UNREADABLE


# --------------------------------------------------------------------------
# other_book_state — all three, and the dangerous default is refused
# --------------------------------------------------------------------------
def test_one_way_netting_admits_no_other_book():
    assert bls.other_book_state(0) == bls.OTHER_BOOK_IMPOSSIBLE_ONE_WAY
    assert bls.other_book_state("0") == bls.OTHER_BOOK_IMPOSSIBLE_ONE_WAY


@pytest.mark.parametrize("idx", [1, 2, "1", "2"])
def test_hedge_books_admit_a_live_sibling(idx):
    assert bls.other_book_state(idx) == bls.OTHER_BOOK_POSSIBLE_HEDGE


@pytest.mark.parametrize("idx", [None, "", "  ", "x", 7, -1])
def test_unreadable_position_idx_is_unknown_never_one_way(idx):
    """CLAUDE.md names this exact hazard: defaulting an unread mode to the
    netting value makes a hedge account look safe to treat as netted."""
    got = bls.other_book_state(idx)
    assert got == bls.OTHER_BOOK_UNKNOWN
    assert got != bls.OTHER_BOOK_IMPOSSIBLE_ONE_WAY


# --------------------------------------------------------------------------
# split_legs_by_side
# --------------------------------------------------------------------------
def _qty(leg):
    try:
        q = float(leg.get("qty"))
    except (TypeError, ValueError):
        return None
    return q if q > 0 else None


def test_split_reproduces_the_live_btcusdt_read():
    """MEASURED source: /api/diag/bybit_open_orders?account_id=bybit_1, read
    2026-09-02T03:30:33Z, trader git_sha 68e73de8. Position Buy 0.018
    positionIdx=1; SL legs Sell 0.018 (its own) and Buy 0.46 (other book)."""
    split = bls.split_legs_by_side(
        "Buy",
        [{"side": "Sell", "qty": "0.018"}, {"side": "Buy", "qty": "0.46"}],
        qty_of=_qty, position_idx=1,
    )
    assert split[f"{bls.LEG_REDUCES_GRADED_BOOK}_qty"] == pytest.approx(0.018)
    assert split[f"{bls.LEG_REDUCES_GRADED_BOOK}_legs"] == 1
    assert split[f"{bls.LEG_REDUCES_OTHER_BOOK}_qty"] == pytest.approx(0.46)
    assert split[f"{bls.LEG_REDUCES_OTHER_BOOK}_legs"] == 1
    assert split["other_book_state"] == bls.OTHER_BOOK_POSSIBLE_HEDGE
    assert split["legs_seen"] == 2


def test_split_of_genuine_same_book_over_accumulation():
    """The CONTROL for the opposite finding: three same-book legs on a long.
    If this graded as 'other book' the fix would have replaced one wrong label
    with another."""
    split = bls.split_legs_by_side(
        "Buy",
        [{"side": "Sell", "qty": "1"}, {"side": "Sell", "qty": "1"},
         {"side": "Sell", "qty": "1"}],
        qty_of=_qty, position_idx=0,
    )
    assert split[f"{bls.LEG_REDUCES_GRADED_BOOK}_qty"] == pytest.approx(3.0)
    assert split[f"{bls.LEG_REDUCES_OTHER_BOOK}_legs"] == 0
    assert split["other_book_state"] == bls.OTHER_BOOK_IMPOSSIBLE_ONE_WAY


def test_unreadable_qty_is_counted_never_silently_zero():
    """A leg with no readable qty contributes 0 — so the count MUST ship, or
    the sum reads as complete when it is a lower bound."""
    split = bls.split_legs_by_side(
        "Buy", [{"side": "Sell", "qty": "nope"}, {"side": "Sell", "qty": "2"}],
        qty_of=_qty, position_idx=0,
    )
    assert split[f"{bls.LEG_REDUCES_GRADED_BOOK}_qty"] == pytest.approx(2.0)
    assert split[f"{bls.LEG_REDUCES_GRADED_BOOK}_qty_unreadable"] == 1
    assert split["qty_unreadable_legs"] == 1


def test_empty_leg_list_is_a_real_zero_not_an_unknown():
    split = bls.split_legs_by_side("Buy", [], qty_of=_qty, position_idx=0)
    assert split["legs_seen"] == 0
    assert split["qty_unreadable_legs"] == 0
    for state in bls.LEG_SIDE_STATES:
        assert split[f"{state}_legs"] == 0


def test_every_declared_state_has_a_bucket():
    """The split must be able to REPORT each state, not merely define it."""
    split = bls.split_legs_by_side(
        "Buy",
        [{"side": "Sell", "qty": "1"}, {"side": "Buy", "qty": "1"},
         {"side": None, "qty": "1"}],
        qty_of=_qty, position_idx=1,
    )
    for state in bls.LEG_SIDE_STATES:
        assert f"{state}_legs" in split and f"{state}_qty" in split
    assert split[f"{bls.LEG_SIDE_UNREADABLE}_legs"] == 1
    # position side readable here, so that bucket is a real zero
    assert split[f"{bls.POSITION_SIDE_UNREADABLE}_legs"] == 0
