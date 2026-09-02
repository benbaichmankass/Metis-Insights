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


def _qty(leg):
    """The same parse `order_monitor._bybit_sl_leg_qty` injects — a second copy
    of "what qty does this leg close" is how the sum and the split would drift.
    """
    try:
        q = float(leg.get("qty"))
    except (TypeError, ValueError):
        return None
    return q if q > 0 else None


def _split(position_side, legs, position_idx):
    return bls.split_legs_by_side(
        position_side, legs, qty_of=_qty, position_idx=position_idx)


# ==========================================================================
# graded_book_coverage — the accessor the RE-ARM decision reads (2026-09-02)
# ==========================================================================
# ⚠️ This is no longer a diagnostic. `_check_broker_naked_bybit_positions`
# grades coverage through it, so a wrong answer here changes which live
# positions get a protective stop re-armed. Both directions, and the two
# "we did not look" states separately from the real zero.
def test_graded_coverage_counts_only_the_legs_that_reduce_this_book():
    """The live BTCUSDT shape: own Sell 0.018 + other-book Buy 0.46."""
    split = _split("Buy", [
        {"side": "Sell", "qty": "0.018"},
        {"side": "Buy", "qty": "0.46"},
    ], 1)
    qty, reason = bls.graded_book_coverage(split)
    assert reason == bls.COVERAGE_GRADED
    assert qty == pytest.approx(0.018)      # NOT 0.478 — that is the side-blind sum


def test_graded_coverage_of_a_short_is_its_BUY_legs():
    """The MIRROR — otherwise the accessor could be inverted and still pass."""
    split = _split("Sell", [
        {"side": "Buy", "qty": "10"},
        {"side": "Sell", "qty": "99"},
    ], 2)
    qty, reason = bls.graded_book_coverage(split)
    assert reason == bls.COVERAGE_GRADED and qty == pytest.approx(10.0)


def test_only_other_book_legs_is_a_measured_ZERO_not_a_refusal():
    """THE DEFECT'S CORE. A real reading of zero — nothing protects this book —
    must be a number the caller can act on, not an unknown. Returning `None`
    here would make the sweep refuse exactly where it must re-arm."""
    qty, reason = bls.graded_book_coverage(_split("Buy", [
        {"side": "Buy", "qty": "0.46"}], 1))
    assert reason == bls.COVERAGE_GRADED
    assert qty == 0.0


def test_no_legs_at_all_is_also_a_measured_zero():
    qty, reason = bls.graded_book_coverage(_split("Buy", [], 0))
    assert reason == bls.COVERAGE_GRADED and qty == 0.0


def test_unreadable_position_side_refuses_rather_than_returning_zero():
    """⚠️ `None`, never `0.0`. A zero would be read as "nothing covers this
    book" and drive a live re-arm on a position we could not grade."""
    qty, reason = bls.graded_book_coverage(_split("", [
        {"side": "Sell", "qty": "0.018"}], 1))
    assert qty is None
    assert reason == bls.COVERAGE_UNGRADED_POSITION_SIDE


def test_one_unreadable_leg_side_refuses_the_whole_grade():
    """A partial grade is a LOWER BOUND, and a lower bound compared against
    `size` under-reports coverage — which drives a re-arm."""
    qty, reason = bls.graded_book_coverage(_split("Buy", [
        {"side": "Sell", "qty": "0.018"},
        {"side": None, "qty": "0.005"},
    ], 1))
    assert qty is None and reason == bls.COVERAGE_UNGRADED_LEG_SIDE


def test_unreadable_leg_qty_refuses_too():
    """Defence in depth: `_bybit_position_protection`'s own
    `unknown_qty_sl_legs` guard already refuses upstream, but the accessor must
    be safe on its own terms — a leg whose side graded and whose qty did not
    contributes 0.0 and would silently understate the total."""
    qty, reason = bls.graded_book_coverage(_split("Buy", [
        {"side": "Sell", "qty": "nope"}], 1))
    assert qty is None and reason == bls.COVERAGE_UNGRADED_LEG_QTY


def test_absent_split_refuses_and_is_not_a_zero():
    """The Full-mode branch returns `leg_side_split: None` because it never
    read the legs. `None` is *we did not look*; a 0.0 would assert a
    measurement nobody took."""
    for absent in (None, "", [], 0):
        qty, reason = bls.graded_book_coverage(absent)
        assert qty is None and reason == bls.COVERAGE_UNGRADED_NO_SPLIT


def test_position_side_refusal_is_reported_ahead_of_the_leg_side_one():
    """Both are "we did not look", but they point at different halves of the
    read: with no position side NOTHING on the symbol is gradeable, so that is
    the more informative thing to tell an operator."""
    split = _split("", [{"side": None, "qty": "1"}], 1)
    _, reason = bls.graded_book_coverage(split)
    assert reason == bls.COVERAGE_UNGRADED_POSITION_SIDE
