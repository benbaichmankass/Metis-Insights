"""The Bybit over-cover page must name the condition it actually measured.

OBSERVED 2026-09-02T03:03:58Z on the live feed (trader ``git_sha 68e73de8``),
via ``/api/bot/notifications`` and ``/api/bot/logs?level=error``::

    bybit_1/BTCUSDT: position 0.018 but resting SL legs total 0.478 (2656%)
    across 2 leg(s).

Cross-read against ``/api/diag/bybit_open_orders?account_id=bybit_1``
(2026-09-02T03:30:33Z), the live position was ``Buy 0.018 positionIdx=1`` whose
own legs were ``Sell 0.018`` SL + ``Sell 0.018`` TP — a 1.00x match. The excess
was entirely ``Buy 0.46`` SL + ``Buy 0.46`` TP: reduce-only orders that can only
reduce a SHORT. The page invited an investigation into over-protection of a
position that was protected exactly right.

That is UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A — a message naming a cause
no code path tested (``CLAUDE.md`` § "Diagnostic provenance"). These tests pin
the branch, in BOTH directions.

⚠️ THE LAST CLAUSE OF THAT SENTENCE READ "and pin that the ORDER PATH did not
move" UNTIL 2026-09-02 AND MUST NOT BE RE-QUOTED. It was true of #10739 and
became false when the Tier-2 sibling that PR named was fixed: the re-arm
decision now grades the split via ``bybit_leg_sides.graded_book_coverage``.
What these tests still pin is that ``covered_qty`` — and therefore the
over-cover TRIP — stays side-blind; see the renamed test at the foot of the
``_bybit_position_protection`` block for why that is the opposite guard.
"""

import pytest

from src.runtime import bybit_leg_sides as bls
from src.runtime import order_monitor as om


def _qty(leg):
    try:
        q = float(leg.get("qty"))
    except (TypeError, ValueError):
        return None
    return q if q > 0 else None


def _split(position_side, legs, position_idx):
    return bls.split_legs_by_side(
        position_side, legs, qty_of=_qty, position_idx=position_idx)


# --------------------------------------------------------------------------
# THE FINDING: other-book legs must not be described as over-protection
# --------------------------------------------------------------------------
def test_live_btcusdt_case_does_not_claim_the_position_is_over_protected():
    which, fields = om._bybit_over_cover_condition(
        size=0.018, covered=0.478,
        leg_side_split=_split(
            "Buy",
            [{"side": "Sell", "qty": "0.018"}, {"side": "Buy", "qty": "0.46"}],
            1),
    )
    assert "NOT over-protected" in which
    assert "SAME-BOOK LEG OVER-ACCUMULATION" not in which
    assert "OPPOSITE book" in which
    assert fields["graded_book_qty"] == pytest.approx(0.018)
    assert fields["other_book_qty"] == pytest.approx(0.46)
    assert fields["other_book_state"] == bls.OTHER_BOOK_POSSIBLE_HEDGE


def test_hedge_case_refuses_to_call_the_other_book_legs_orphaned():
    """Hedge mode is armed on this account since 2026-08-30, so the sibling
    book MAY be live. Asserting 'orphaned' here would re-commit the exact
    error being fixed one level along."""
    which, _ = om._bybit_over_cover_condition(
        size=0.018, covered=0.478,
        leg_side_split=_split(
            "Buy",
            [{"side": "Sell", "qty": "0.018"}, {"side": "Buy", "qty": "0.46"}],
            1),
    )
    assert "MAY be a LIVE sibling" in which
    assert "DO NOT cancel them on this page alone" in which
    assert "STRANDED" not in which


def test_one_way_netting_does_call_them_stranded():
    """The MIRROR: with positionIdx=0 no opposite book can exist, so the
    hedging language above must NOT be emitted — otherwise the page would be
    uselessly non-committal on the case it can actually decide."""
    which, fields = om._bybit_over_cover_condition(
        size=1.0, covered=6.0,
        leg_side_split=_split(
            "Buy", [{"side": "Sell", "qty": "1"}, {"side": "Buy", "qty": "5"}],
            0),
    )
    assert "STRANDED legs of a position that is gone" in which
    assert "MAY be a LIVE sibling" not in which
    assert fields["other_book_state"] == bls.OTHER_BOOK_IMPOSSIBLE_ONE_WAY


def test_unknown_position_idx_says_unknown_not_stranded_and_not_hedge():
    which, fields = om._bybit_over_cover_condition(
        size=1.0, covered=6.0,
        leg_side_split=_split(
            "Buy", [{"side": "Sell", "qty": "1"}, {"side": "Buy", "qty": "5"}],
            None),
    )
    assert "UNKNOWN" in which
    assert "NOT 'the book is flat'" in which
    assert "STRANDED" not in which
    assert fields["other_book_state"] == bls.OTHER_BOOK_UNKNOWN


# --------------------------------------------------------------------------
# THE CONTROL: a genuine same-book pile-up must still read as one
# --------------------------------------------------------------------------
def test_genuine_same_book_over_accumulation_is_still_named():
    """If the fix silenced the original condition it would have traded one
    wrong label for a missing one."""
    which, fields = om._bybit_over_cover_condition(
        size=1.0, covered=3.0,
        leg_side_split=_split(
            "Buy",
            [{"side": "Sell", "qty": "1"}, {"side": "Sell", "qty": "1"},
             {"side": "Sell", "qty": "1"}],
            0),
    )
    assert "SAME-BOOK LEG OVER-ACCUMULATION" in which
    assert "OPPOSITE book" not in which
    assert fields["graded_book_qty"] == pytest.approx(3.0)
    assert fields["other_book_legs"] == 0


def test_both_conditions_at_once_are_both_reported():
    which, fields = om._bybit_over_cover_condition(
        size=1.0, covered=8.0,
        leg_side_split=_split(
            "Buy",
            [{"side": "Sell", "qty": "1.5"}, {"side": "Sell", "qty": "1.5"},
             {"side": "Buy", "qty": "5"}],
            0),
    )
    assert "SAME-BOOK LEG OVER-ACCUMULATION" in which
    assert "OPPOSITE book" in which
    assert fields["graded_book_qty"] == pytest.approx(3.0)
    assert fields["other_book_qty"] == pytest.approx(5.0)


# --------------------------------------------------------------------------
# "we did not look" is never rendered as a clean read
# --------------------------------------------------------------------------
def test_unreadable_position_side_refuses_to_grade_anything():
    which, fields = om._bybit_over_cover_condition(
        size=1.0, covered=6.0,
        leg_side_split=_split(
            None, [{"side": "Sell", "qty": "1"}, {"side": "Buy", "qty": "5"}],
            0),
    )
    assert "We could not look" in which
    assert "NOT over-protected" not in which
    assert "SAME-BOOK LEG OVER-ACCUMULATION" not in which
    assert fields["position_side_unreadable_legs"] == 2


def test_unreadable_leg_side_is_declared_as_a_lower_bound():
    which, fields = om._bybit_over_cover_condition(
        size=1.0, covered=6.0,
        leg_side_split=_split(
            "Buy", [{"side": "Sell", "qty": "1"}, {"side": None, "qty": "5"}],
            0),
    )
    assert "no readable side" in which and "lower bounds" in which
    assert fields["leg_side_unreadable_legs"] == 1


def test_absent_split_says_not_computed_never_a_clean_zero():
    which, fields = om._bybit_over_cover_condition(
        size=1.0, covered=6.0, leg_side_split=None)
    assert "UNKNOWN" in which
    assert fields["leg_side_split_state"] == "not_computed"
    assert "graded_book_qty" not in fields  # never a fabricated zero


# --------------------------------------------------------------------------
# the ORDER PATH did not move — the whole safety argument for this change
# --------------------------------------------------------------------------
class _FakeClient:
    """Reproduces the live bybit_1/BTCUSDT venue read of 2026-09-02T03:30:33Z."""

    def get_positions(self, category, symbol):
        return {"result": {"list": [{
            "symbol": "BTCUSDT", "side": "Buy", "size": "0.018",
            "positionIdx": 1, "stopLoss": "",
        }]}}

    def get_open_orders(self, category, symbol, orderFilter):
        return {"result": {"list": [
            {"stopOrderType": "PartialStopLoss", "side": "Sell",
             "qty": "0.018", "triggerPrice": "38698.6", "orderId": "a1"},
            {"stopOrderType": "PartialTakeProfit", "side": "Sell",
             "qty": "0.018", "triggerPrice": "154794.4", "orderId": "a2"},
            {"stopOrderType": "PartialStopLoss", "side": "Buy",
             "qty": "0.46", "triggerPrice": "79232.0", "orderId": "b1"},
            {"stopOrderType": "PartialTakeProfit", "side": "Buy",
             "qty": "0.46", "triggerPrice": "69282.0", "orderId": "b2"},
        ]}}


def test_covered_qty_is_still_side_blind_because_the_TRIP_needs_the_union():
    """⚠️ RENAMED AND RE-ARGUED 2026-09-02 — the ASSERTIONS ARE UNCHANGED and
    are deliberately NOT deleted; only the reason they are here has moved.

    It was written as `..._so_the_rearm_decision_is_unchanged`, pinning that
    #10739's diagnostic repair had not smuggled in the Tier-2 order-path change
    it named. That change has now been made: the re-arm decision reads
    `bybit_leg_sides.graded_book_coverage(leg_side_split)`, so the old NAME now
    asserts something false about this system and would mislead the next reader.

    ⚠️ WHAT IT PINS IS STILL LOAD-BEARING, WHICH IS WHY IT SURVIVES. `covered_qty`
    must stay SIDE-BLIND: it feeds the over-cover TRIP, and that check is the
    UNION of two conditions — genuine same-book pile-up AND other-book legs
    resting on the symbol. Narrowing it to the graded book would make the second
    case stop tripping and go SILENT, which is strictly worse than the
    mislabelling #10739 fixed. So this now guards the OPPOSITE mistake from the
    one it was written for: not "the order path moved when it shouldn't have",
    but "the trip threshold followed the order path when it shouldn't have".

    The re-arm side has its own both-direction controls in
    `tests/test_bybit_naked_rearm.py` (§ 2026-09-02).
    """
    st = om._bybit_position_protection(_FakeClient(), "linear", "BTCUSDT")
    assert st["size"] == pytest.approx(0.018)
    assert st["covered_qty"] == pytest.approx(0.478)   # 0.018 + 0.46, side-blind
    assert st["protective_leg_count"] == 4             # combined TP+SL, for the cap
    assert len(st["sl_leg_ids"]) == 2


def test_protection_read_carries_the_split_additively():
    st = om._bybit_position_protection(_FakeClient(), "linear", "BTCUSDT")
    split = st["leg_side_split"]
    assert split[f"{bls.LEG_REDUCES_GRADED_BOOK}_qty"] == pytest.approx(0.018)
    assert split[f"{bls.LEG_REDUCES_OTHER_BOOK}_qty"] == pytest.approx(0.46)
    assert split["other_book_state"] == bls.OTHER_BOOK_POSSIBLE_HEDGE


def test_full_mode_split_is_none_because_open_orders_were_never_read():
    """`None`, not an empty split: the Full-mode branch returns before
    `get_open_orders`, so an empty split would assert a measurement nobody
    took."""
    class _Full(_FakeClient):
        def get_positions(self, category, symbol):
            return {"result": {"list": [{
                "symbol": "BTCUSDT", "side": "Buy", "size": "0.018",
                "positionIdx": 0, "stopLoss": "38698.6",
            }]}}

    st = om._bybit_position_protection(_Full(), "linear", "BTCUSDT")
    assert st["source"] == "full_position_stop"
    assert st["leg_side_split"] is None


# --------------------------------------------------------------------------
# the emitter carries the split through to the operator-facing page
# --------------------------------------------------------------------------
def test_emitter_publishes_the_per_book_fields(monkeypatch):
    seen = {}

    def _fake_report(kind, status, **kw):
        seen["kind"] = kind
        seen.update(kw)

    import src.runtime.outcomes as outcomes
    monkeypatch.setattr(outcomes, "report", _fake_report)
    monkeypatch.setattr(om, "_cooldown_admits", lambda *a, **k: True)

    st = om._bybit_position_protection(_FakeClient(), "linear", "BTCUSDT")
    assert om._emit_bybit_over_cover_alert(
        account_id="bybit_1", symbol="BTCUSDT",
        size=st["size"], covered=st["covered_qty"],
        leg_count=len(st["sl_leg_ids"]),
        protective_leg_count=st["protective_leg_count"],
        leg_side_split=st["leg_side_split"],
    ) is True

    assert seen["kind"] == "bybit_over_cover"
    # the side-blind keys keep their OLD meaning (no silent re-pointing)
    assert seen["covered_qty"] == pytest.approx(0.478)
    # ...and the per-book truth ships beside them
    assert seen["graded_book_qty"] == pytest.approx(0.018)
    assert seen["other_book_qty"] == pytest.approx(0.46)
    assert seen["other_book_state"] == bls.OTHER_BOOK_POSSIBLE_HEDGE
    assert "NOT over-protected" in seen["reason"]
