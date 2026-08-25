"""The over-cover selection, argued in tests rather than against a live position.

The anchor test is `test_reproduces_the_2026_08_20_failure`: it replays the
inputs of the remediation that CANCELLED THE LEG MATCHING THE JOURNAL
(`BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`) and
asserts this module reaches the opposite conclusion. Everything else here exists
to pin the refusals — the states where the journal does not single a group out
and the honest answer is to do nothing.
"""
from __future__ import annotations

import pytest

from src.runtime.over_cover_decision import (
    STATE_AMBIGUOUS,
    STATE_CANCEL_GROUP,
    STATE_NO_DECLARED_STOP,
    STATE_NO_JOURNAL_MATCH,
    STATE_NO_OVER_COVER,
    STATE_NOT_GRADED,
    STATE_POSITION_ABSENT,
    decide_over_cover,
)

# --- the recorded failure, as it was measured -------------------------------
# /api/diag/ib_open_orders 2026-08-20T20:23:39Z, read_state 'orders_read',
# against journal trade 4350 (ib_paper MES, 15 long, stop_loss 7533.69642857).
# MES tick size 0.25 (config/instruments.yaml).
MES_DECLARED_STOP = 7533.69642857
MES_TICK = 0.25
MES_STRAY = {
    "order_id": 338, "order_type": "STP", "side": "stop", "action": "SELL",
    "total_quantity": 15.0, "aux_price": 7516.50,
    "oca_group": "oca-protect-336", "client_id": 497,
}
MES_JOURNAL_MATCHING = {
    "order_id": 375, "order_type": "STP", "side": "stop", "action": "SELL",
    "total_quantity": 15.0, "aux_price": 7533.75,
    "oca_group": "oca-protect-373", "client_id": 597,
}


def _mes(**over):
    kwargs = dict(
        position_qty=15.0,
        direction="long",
        declared_stop=MES_DECLARED_STOP,
        declared_target=8390.59025,
        legs=[MES_STRAY, MES_JOURNAL_MATCHING],
        tick_size=MES_TICK,
    )
    kwargs.update(over)
    return decide_over_cover(**kwargs)


def test_reproduces_the_2026_08_20_failure():
    """The leg that MATCHES the journal must be KEPT, and the stray cancelled.

    On 2026-08-20 the repair did the reverse: order 375 (7533.75, within 0.21
    ticks of the declared 7533.69642857) was cancelled and stray 338 (7516.50,
    68.8 ticks away, matching nothing in the journal) survived — leaving the
    position protected 17.196 points low, $1,289.73 on 15 contracts at MES's
    $5/point.
    """
    decision = _mes()

    assert decision["state"] == STATE_CANCEL_GROUP
    # The whole point, stated as an assertion rather than a comment:
    assert 375 not in decision["cancel_order_ids"], (
        "order 375 matched the journal's declared stop to within one tick — "
        "cancelling it is the 2026-08-20 failure")
    assert decision["cancel_order_ids"] == [338]
    assert decision["keep_groups"] == ["oca-protect-373"]
    assert decision["cancel_groups"] == ["oca-protect-336"]
    assert decision["over_cover_pct"] == pytest.approx(200.0)


def test_the_selection_does_not_depend_on_order_id_or_input_order():
    """Not 'the newer id', not 'the first one seen' — the journal decides.

    An implicit input selection (newest / first / alphabetically-last standing
    in for the declared thing) is diagnostic-provenance sub-class B, and it
    would pass the anchor test above by coincidence: 375 happens to be the
    higher id AND the journal match. Reversing the list and swapping which id
    is higher separates the two.
    """
    reversed_legs = _mes(legs=[MES_JOURNAL_MATCHING, MES_STRAY])
    assert reversed_legs["cancel_order_ids"] == [338]

    # Same prices, but now the STRAY carries the higher order id.
    swapped = _mes(legs=[
        dict(MES_STRAY, order_id=999),
        dict(MES_JOURNAL_MATCHING, order_id=100),
    ])
    assert swapped["cancel_order_ids"] == [999]
    assert swapped["keep_groups"] == ["oca-protect-373"]


def test_a_whole_group_goes_together_stop_and_target():
    """Cancelling the stray STOP alone would leave its target resting.

    The live MHG shape: 29 long against two disjoint groups, each carrying a
    29-lot STP and a 29-lot LMT.
    """
    decision = decide_over_cover(
        position_qty=29.0,
        direction="long",
        declared_stop=6.2215,
        declared_target=7.1415,
        legs=[
            {"order_id": 401, "order_type": "STP", "side": "stop", "total_quantity": 29.0,
             "aux_price": 6.2215, "oca_group": "oca-protect-416"},
            {"order_id": 402, "order_type": "LMT", "side": "target", "total_quantity": 29.0,
             "lmt_price": 7.1415, "oca_group": "oca-protect-416"},
            {"order_id": 403, "order_type": "STP", "side": "stop", "total_quantity": 29.0,
             "aux_price": 5.9000, "oca_group": "oca-protect-432"},
            {"order_id": 404, "order_type": "LMT", "side": "target", "total_quantity": 29.0,
             "lmt_price": 7.5000, "oca_group": "oca-protect-432"},
        ],
        tick_size=0.0005,
    )
    assert decision["state"] == STATE_CANCEL_GROUP
    assert decision["keep_groups"] == ["oca-protect-416"]
    # BOTH legs of the stray group, not just its stop.
    assert sorted(decision["cancel_order_ids"]) == [403, 404]


def test_two_matching_groups_refuse():
    """If the journal matches both, it has not singled one out. Refuse."""
    decision = _mes(legs=[
        dict(MES_STRAY, aux_price=7533.75, order_id=338),
        MES_JOURNAL_MATCHING,
    ])
    assert decision["state"] == STATE_AMBIGUOUS
    assert decision["cancel_order_ids"] == []


def test_a_group_matching_on_one_side_only_refuses():
    """One group holds the declared stop, another the declared target.

    Cancelling either strips a leg that was correct. This is the shape closest
    to the 2026-08-20 failure and the refusal is deliberate.
    """
    decision = decide_over_cover(
        position_qty=29.0,
        direction="long",
        declared_stop=6.2215,
        declared_target=7.1415,
        legs=[
            {"order_id": 401, "order_type": "STP", "side": "stop", "total_quantity": 29.0,
             "aux_price": 6.2215, "oca_group": "A"},
            {"order_id": 402, "order_type": "LMT", "side": "target", "total_quantity": 29.0,
             "lmt_price": 9.9999, "oca_group": "A"},
            {"order_id": 403, "order_type": "STP", "side": "stop", "total_quantity": 29.0,
             "aux_price": 5.9000, "oca_group": "B"},
            {"order_id": 404, "order_type": "LMT", "side": "target", "total_quantity": 29.0,
             "lmt_price": 7.1415, "oca_group": "B"},
        ],
        tick_size=0.0005,
    )
    assert decision["state"] == STATE_AMBIGUOUS
    assert decision["cancel_order_ids"] == []


def test_no_group_matches_refuses():
    """Every candidate is a stray — cancelling one is a guess about the rest."""
    decision = _mes(legs=[
        MES_STRAY,
        dict(MES_JOURNAL_MATCHING, aux_price=7400.00),
    ])
    assert decision["state"] == STATE_NO_JOURNAL_MATCH
    assert decision["cancel_order_ids"] == []


def test_within_tolerance_is_not_an_over_cover():
    decision = _mes(legs=[MES_JOURNAL_MATCHING])
    assert decision["state"] == STATE_NO_OVER_COVER
    assert decision["cancel_order_ids"] == []


@pytest.mark.parametrize("legs, expected", [
    (None, STATE_NOT_GRADED),
    ([], STATE_NOT_GRADED),
    ([{"order_id": 1, "side": "", "total_quantity": 15.0,
       "oca_group": "A"}], STATE_NOT_GRADED),
])
def test_unreadable_input_is_not_graded(legs, expected):
    """'We could not look' never renders as 'nothing to do'."""
    assert _mes(legs=legs)["state"] == expected


def test_missing_tick_size_is_not_graded():
    """A tick count needs a grid; a guessed grid is a fabricated verdict."""
    decision = _mes(tick_size=None)
    assert decision["state"] == STATE_NOT_GRADED
    assert decision["cancel_order_ids"] == []


def test_no_declared_stop_is_distinct_from_no_match():
    """Nothing to ask, versus asked and found nothing. Different facts."""
    decision = _mes(declared_stop=None)
    assert decision["state"] == STATE_NO_DECLARED_STOP


def test_absent_position():
    assert _mes(position_qty=None)["state"] == STATE_POSITION_ABSENT
    assert _mes(position_qty=0)["state"] == STATE_POSITION_ABSENT


def test_a_leg_with_no_side_is_not_graded():
    """An unclassified leg may be a STOP. Grading around it would under-count
    coverage and could report a real over-cover as `no_over_cover` — the
    reassuring value, fabricated. Classification is the caller's job (one
    definition, `broker_bracket_reconcile.protective_leg_side`), so an
    unclassified leg is a refusal here, not a guess and not a silent drop."""
    decision = _mes(legs=[
        MES_STRAY,
        dict(MES_JOURNAL_MATCHING, side=None),
    ])
    assert decision["state"] == STATE_NOT_GRADED
    assert decision["cancel_order_ids"] == []


def test_the_decision_never_takes_a_level_from_the_caller():
    """The level is the journal's. Feeding a different declared stop moves the
    verdict — which is the proof that nothing else is standing in for it."""
    as_declared = _mes()
    assert as_declared["keep_groups"] == ["oca-protect-373"]
    # Declare the OTHER level and the selection follows the journal, not the ids.
    flipped = _mes(declared_stop=7516.50)
    assert flipped["keep_groups"] == ["oca-protect-336"]
    assert flipped["cancel_order_ids"] == [375]
