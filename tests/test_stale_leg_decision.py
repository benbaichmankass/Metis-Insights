"""The stale-leg selection is arguable HERE, not against a live position.

The load-bearing test is `test_reproduces_the_2026_08_26_failure`: it asserts the
newest-created leg — which the shipped `_keep_and_stale` rule would have KEPT —
is the one cancelled, and the older leg owned by a live trade is the one kept.
If the newest-wins rule ever returns, that test fails.
"""
import pytest

from src.runtime.stale_leg_decision import (
    ALL_STATES,
    OPEN_STATUSES,
    STATE_CANCEL_LEGS,
    STATE_NOT_GRADED,
    STATE_NO_RESTING_LEGS,
    STATE_NO_STALE_LEGS,
    STATE_POSITION_ABSENT,
    STATE_UNATTRIBUTABLE,
    STATE_WOULD_UNDERCOVER,
    decide_stale_legs,
)


def _leg(order_id, side="stop", qty=1.0):
    return {"order_id": order_id, "side": side, "qty": qty}


def _row(trade_id, status, sl_order_id=None, tp_order_id=None, qty=None):
    return {
        "id": trade_id,
        "status": status,
        "sl_order_id": sl_order_id,
        "tp_order_id": tp_order_id,
        "position_size": qty,
    }


# --------------------------------------------------------------------------
# The recorded failure. This is the SHAPE measured on the live book at
# 2026-08-26T00:10Z (bybit_1/ETHUSDT, position 5.59, open rows 4921 qty 1.18 +
# 4903 qty 4.41 = 5.59 exactly), not a full 14-leg transcript — the diag route
# that produced the reading under-reports legs (7 visible against the trader's
# symbol-scoped 9), so the ids below are the ones the reading actually named.
# --------------------------------------------------------------------------
_RECORDED_LEGS = [
    _leg("leg-5003", qty=0.19),   # newest by createdTime (2026-08-25 01:05)
    _leg("leg-4987", qty=0.21),
    _leg("leg-4960", qty=0.30),
    _leg("leg-4903", qty=4.41),   # owned by an OPEN row
    _leg("leg-4941", qty=0.22),
    _leg("leg-4921", qty=1.18),   # owned by an OPEN row (2026-08-22 07:05)
]
_RECORDED_ROWS = [
    _row(5003, "closed", sl_order_id="leg-5003", qty=0.19),
    _row(4987, "closed", sl_order_id="leg-4987", qty=0.21),
    _row(4960, "closed", sl_order_id="leg-4960", qty=0.30),
    _row(4941, "closed", sl_order_id="leg-4941", qty=0.22),
    _row(4921, "open", sl_order_id="leg-4921", qty=1.18),
    _row(4903, "open", sl_order_id="leg-4903", qty=4.41),
]


def test_reproduces_the_2026_08_26_failure():
    """The newest leg belongs to a CLOSED trade and must be cancelled; the older
    leg belongs to a live trade and must be kept. The shipped newest-wins rule
    did the exact opposite, leaving 3.4% coverage."""
    out = decide_stale_legs(
        position_qty=5.59, legs=_RECORDED_LEGS, journal_rows=_RECORDED_ROWS)

    assert out["state"] == STATE_CANCEL_LEGS, out["reason"]
    # The newest leg is CANCELLED, not kept.
    assert "leg-5003" in out["cancel_order_ids"]
    # Both live trades' legs survive.
    assert "leg-4921" not in out["cancel_order_ids"]
    assert "leg-4903" not in out["cancel_order_ids"]
    kept = {r["order_id"] for r in out["keep_legs"]}
    assert kept == {"leg-4921", "leg-4903"}
    # And the position is still fully covered afterwards.
    assert out["stop_qty_kept"] == pytest.approx(5.59)
    assert out["stop_qty_kept"] >= out["position_qty"]


def test_the_shipped_rule_would_have_left_the_position_naked():
    """A control on the test above: state plainly what newest-wins produced, so
    the comparison is in the file rather than only in a backlog row."""
    newest_wins_keep = "leg-5003"          # sorted by createdTime, ordered[0]
    assert newest_wins_keep in decide_stale_legs(
        position_qty=5.59, legs=_RECORDED_LEGS,
        journal_rows=_RECORDED_ROWS)["cancel_order_ids"]
    # 0.19 of stop against a 5.59 position is 3.4% covered.
    assert 0.19 / 5.59 < 0.035


# --------------------------------------------------------------------------
# States, each reachable and none collapsed into another.
# --------------------------------------------------------------------------
def test_all_open_owned_is_not_a_refusal():
    out = decide_stale_legs(
        position_qty=2.0,
        legs=[_leg("a", qty=2.0)],
        journal_rows=[_row(1, "open", sl_order_id="a", qty=2.0)])
    assert out["state"] == STATE_NO_STALE_LEGS
    assert out["cancel_order_ids"] == []


def test_no_resting_legs_is_distinct_from_no_stale_legs():
    out = decide_stale_legs(position_qty=2.0, legs=[], journal_rows=[])
    assert out["state"] == STATE_NO_RESTING_LEGS
    assert out["state"] != STATE_NO_STALE_LEGS


def test_unreadable_legs_is_not_graded_never_clean():
    out = decide_stale_legs(position_qty=2.0, legs=None, journal_rows=[])
    assert out["state"] == STATE_NOT_GRADED
    assert out["state"] != STATE_NO_STALE_LEGS


def test_unreadable_journal_is_not_graded_never_unattributable():
    """We did not look is not the same as we looked and nothing claimed it —
    the second would justify cancelling nothing for a *reason*, the first is an
    absence of evidence."""
    out = decide_stale_legs(
        position_qty=2.0, legs=[_leg("a", qty=2.0)], journal_rows=None)
    assert out["state"] == STATE_NOT_GRADED
    assert out["state"] != STATE_UNATTRIBUTABLE


def test_a_leg_no_row_claims_refuses_rather_than_guessing():
    out = decide_stale_legs(
        position_qty=2.0,
        legs=[_leg("a", qty=2.0), _leg("orphan", qty=0.5)],
        journal_rows=[_row(1, "open", sl_order_id="a", qty=2.0)])
    assert out["state"] == STATE_UNATTRIBUTABLE
    assert out["cancel_order_ids"] == []
    assert [r["order_id"] for r in out["unattributable_legs"]] == ["orphan"]


def test_an_unreadable_side_makes_the_read_not_graded_not_a_smaller_sum():
    """An unclassified leg must not simply be dropped: dropping it shrinks the
    coverage sum toward the reassuring answer."""
    out = decide_stale_legs(
        position_qty=2.0,
        legs=[_leg("a", qty=2.0), {"order_id": "b", "qty": 1.0}],
        journal_rows=[_row(1, "open", sl_order_id="a", qty=2.0)])
    assert out["state"] == STATE_NOT_GRADED


def test_refuses_when_cancelling_would_undercover():
    """Two closed-owned legs carry all the cover; the one open row's leg does
    not reach the position. Cancelling first would open the gap."""
    out = decide_stale_legs(
        position_qty=5.0,
        legs=[_leg("live", qty=1.0), _leg("dead1", qty=3.0), _leg("dead2", qty=3.0)],
        journal_rows=[
            _row(1, "open", sl_order_id="live", qty=1.0),
            _row(2, "closed", sl_order_id="dead1", qty=3.0),
            _row(3, "closed", sl_order_id="dead2", qty=3.0),
        ])
    assert out["state"] == STATE_WOULD_UNDERCOVER
    assert out["cancel_order_ids"] == []


def test_position_absent_is_its_own_state():
    for qty in (None, 0, "", "not-a-number"):
        out = decide_stale_legs(position_qty=qty, legs=[], journal_rows=[])
        assert out["state"] == STATE_POSITION_ABSENT, qty


def test_target_legs_are_owned_too_and_do_not_count_toward_stop_cover():
    out = decide_stale_legs(
        position_qty=2.0,
        legs=[_leg("s", side="stop", qty=2.0), _leg("t", side="target", qty=2.0),
              _leg("dead_t", side="target", qty=1.0)],
        journal_rows=[
            _row(1, "open", sl_order_id="s", tp_order_id="t", qty=2.0),
            _row(2, "closed", tp_order_id="dead_t", qty=1.0),
        ])
    assert out["state"] == STATE_CANCEL_LEGS
    assert out["cancel_order_ids"] == ["dead_t"]
    # The kept TARGET leg does not inflate stop coverage.
    assert out["stop_qty_kept"] == pytest.approx(2.0)


def test_an_unanticipated_status_falls_out_as_not_open():
    """The membership test is OPEN-side. A status nobody anticipated becomes a
    cancel candidate we then have to justify, rather than silently joining the
    keep set."""
    assert "orphaned" not in OPEN_STATUSES
    out = decide_stale_legs(
        position_qty=2.0,
        legs=[_leg("live", qty=2.0), _leg("weird", qty=1.0)],
        journal_rows=[
            _row(1, "open", sl_order_id="live", qty=2.0),
            _row(2, "orphaned", sl_order_id="weird", qty=1.0),
        ])
    assert out["state"] == STATE_CANCEL_LEGS
    assert out["cancel_order_ids"] == ["weird"]


def test_every_declared_state_is_reachable():
    """A state nothing can produce is documentation, not a contract."""
    seen = set()
    for kwargs in (
        dict(position_qty=None, legs=[], journal_rows=[]),
        dict(position_qty=2.0, legs=None, journal_rows=[]),
        dict(position_qty=2.0, legs=[], journal_rows=[]),
        dict(position_qty=2.0, legs=[_leg("a", qty=2.0)],
             journal_rows=[_row(1, "open", sl_order_id="a", qty=2.0)]),
        dict(position_qty=2.0, legs=[_leg("x", qty=2.0)], journal_rows=[]),
        dict(position_qty=5.59, legs=_RECORDED_LEGS, journal_rows=_RECORDED_ROWS),
        dict(position_qty=5.0,
             legs=[_leg("live", qty=1.0), _leg("dead", qty=4.0)],
             journal_rows=[_row(1, "open", sl_order_id="live", qty=1.0),
                           _row(2, "closed", sl_order_id="dead", qty=4.0)]),
    ):
        seen.add(decide_stale_legs(**kwargs)["state"])
    assert seen == set(ALL_STATES), sorted(set(ALL_STATES) - seen)
