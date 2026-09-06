"""MI-144(b) — the exit LABEL is re-derived from the recorded price, and the
re-derivation must agree with the writer's own classifier.

``trades.exit_reason`` is written ONCE, at close time, and for the largest close
path that is the one moment the answer cannot be known: the no-record fallback
hard-codes ``reconciler_filled`` with ``exit_price`` still NULL. #10262 re-runs
the classifier when the price later arrives, but on ONE path and FORWARD only.

MEASURED, live journal pulled 2026-09-06 via ``/api/bot/db/table/{trades,
order_packages}`` (5518 + 4435 rows). Population: closed, non-backtest,
``pnl NOT NULL``, minus ``orphan_adopt``/``superseded``/``exchange_reset_flat``
— n=1287. The STORED label reads ``sl|tp`` on 251 (19.5%); re-derived, of the
1125 gradeable rows **430 (38.2%)** reached a declared bracket. On the last 200
closes: stored 27 (13.5%) vs re-derived 46 (23.0%).
"""
from __future__ import annotations

import json

import pytest

from src.runtime.bracket_outcome import (
    BRACKET_DIRECTION_UNREADABLE,
    BRACKET_EXCLUDED_REDUCE_LEG,
    BRACKET_MID,
    BRACKET_NO_EXIT_PRICE,
    BRACKET_NO_RECORD,
    BRACKET_PRICE_NOT_MEASURABLE,
    BRACKET_REACHED_SL,
    BRACKET_REACHED_TP,
    BRACKET_STATES,
    classify_bracket_outcome,
    empty_bracket_counts,
)
from src.runtime.order_monitor import _classify_broker_exit


def _row(**kw):
    base = {"direction": "long", "exit_price": 100.0}
    base.update(kw)
    return base


# --- the verdicts --------------------------------------------------------
@pytest.mark.parametrize(
    "direction,price,sl,tp,expected",
    [
        # A fill SLIPS THROUGH a level, so the inequality is <= / >=, not ==.
        ("long", 95.0, 95.0, 110.0, BRACKET_REACHED_SL),
        ("long", 94.0, 95.0, 110.0, BRACKET_REACHED_SL),
        ("long", 110.0, 95.0, 110.0, BRACKET_REACHED_TP),
        ("long", 111.5, 95.0, 110.0, BRACKET_REACHED_TP),
        ("long", 102.0, 95.0, 110.0, BRACKET_MID),
        ("short", 105.0, 105.0, 90.0, BRACKET_REACHED_SL),
        ("short", 106.0, 105.0, 90.0, BRACKET_REACHED_SL),
        ("short", 90.0, 105.0, 90.0, BRACKET_REACHED_TP),
        ("short", 98.0, 105.0, 90.0, BRACKET_MID),
        # A half-bracket still grades on the leg it HAS.
        ("long", 94.0, 95.0, None, BRACKET_REACHED_SL),
        ("long", 96.0, 95.0, None, BRACKET_MID),
    ],
)
def test_the_conservative_inequality(direction, price, sl, tp, expected):
    state, _ = classify_bracket_outcome(
        _row(direction=direction, exit_price=price), sl, tp)
    assert state == expected


def test_it_agrees_with_the_writers_own_classifier_case_for_case():
    """The ONE guarantee that stops the read-path derivation drifting from the
    label the writer stamps. ``_classify_broker_exit`` reads its levels from the
    DB, so a stub stands in for that lookup ONLY — the inequality under test is
    the real one.

    (The follow-up that removes the duplication outright — having
    ``_classify_broker_exit`` call ``classify_bracket_outcome`` — touches the
    journal-writer path and is a Tier-2 proposal, not applied here.)
    """
    class _StubConn:
        def __init__(self, sl, tp):
            self._sl, self._tp = sl, tp
            self.row_factory = None

        def execute(self, *_a, **_k):
            sl, tp = self._sl, self._tp

            class _Cur:
                @staticmethod
                def fetchone():
                    return {"sl": sl, "tp": tp}
            return _Cur()

        def close(self):
            pass

    class _StubDb:
        def __init__(self, sl, tp):
            self._sl, self._tp = sl, tp

        def connect(self):
            return _StubConn(self._sl, self._tp)

    import src.runtime.order_monitor as om

    cases = [
        ("long", 95.0, 95.0, 110.0), ("long", 94.0, 95.0, 110.0),
        ("long", 110.0, 95.0, 110.0), ("long", 102.0, 95.0, 110.0),
        ("short", 105.0, 105.0, 90.0), ("short", 90.0, 105.0, 90.0),
        ("short", 98.0, 105.0, 90.0), ("long", 111.5, 95.0, 110.0),
    ]
    original = om._resolve_linked_package_id
    om._resolve_linked_package_id = lambda _db, _tid: "pkg-1"
    try:
        for direction, price, sl, tp in cases:
            row = {"id": 1, "direction": direction, "symbol": "BTCUSDT"}
            writer = _classify_broker_exit(_StubDb(sl, tp), row, price)
            _, reader = classify_bracket_outcome(
                {"direction": direction, "exit_price": price}, sl, tp)
            # The writer returns None for a mid-bracket close; the reader says
            # "other" — the SAME fact, spelled for a bucket key rather than a
            # label. Everything else must match exactly.
            assert (writer or "other") == reader, (direction, price, sl, tp)
    finally:
        om._resolve_linked_package_id = original


# --- the states that are NOT verdicts ------------------------------------
def test_a_fabricated_exit_price_is_REFUSED_not_graded():
    """`local_markprice` is the market at SWEEP time — hours after the exit.
    Comparing it to a bracket manufactures a verdict out of unrelated price
    action, so the row is refused, exactly as the writer side refuses it."""
    row = _row(exit_price=94.0,
               notes=json.dumps({"exit_price_source": "local_markprice"}))
    state, verdict = classify_bracket_outcome(row, 95.0, 110.0)
    assert state == BRACKET_PRICE_NOT_MEASURABLE
    assert verdict is None            # NEVER "other" — we refused, not looked
    # The same price on a measured source DOES grade — proving the refusal is
    # the source's doing, not the price's (a negative control).
    row_ok = _row(exit_price=94.0,
                  notes=json.dumps({"exit_price_source": "bybit_closed_pnl"}))
    assert classify_bracket_outcome(row_ok, 95.0, 110.0)[0] == BRACKET_REACHED_SL


def test_we_did_not_look_is_never_did_not_reach_a_bracket():
    for row, expected in (
        (_row(exit_price=None), BRACKET_NO_EXIT_PRICE),
        (_row(exit_price=0.0), BRACKET_NO_EXIT_PRICE),
        (_row(direction="sideways"), BRACKET_DIRECTION_UNREADABLE),
    ):
        state, verdict = classify_bracket_outcome(row, 95.0, 110.0)
        assert state == expected
        assert verdict is None


def test_no_bracket_record_is_not_a_mid_bracket_close():
    state, verdict = classify_bracket_outcome(_row(), None, None)
    assert state == BRACKET_NO_RECORD
    assert verdict is None
    # A zero/negative level is an absent one, not a real level at 0.
    assert classify_bracket_outcome(_row(), 0.0, -1.0)[0] == BRACKET_NO_RECORD


def test_a_reduce_leg_is_excluded_by_setup_type_or_by_the_notes_flag():
    """A reduce's SL/TP are the ORIGINAL position's while its direction is the
    CLOSING side, so its bracket is inverted and grading it would mislabel a
    deliberate partial close as a bracket hit."""
    assert classify_bracket_outcome(
        _row(setup_type="intent_reduce"), 95.0, 110.0
    )[0] == BRACKET_EXCLUDED_REDUCE_LEG
    # A reattached row carries the flag without the setup_type.
    assert classify_bracket_outcome(
        _row(notes=json.dumps({"intent_reduce": True})), 95.0, 110.0
    )[0] == BRACKET_EXCLUDED_REDUCE_LEG


def test_the_states_partition_the_population():
    counts = empty_bracket_counts()
    assert set(counts) == set(BRACKET_STATES)
    rows = [
        (_row(exit_price=94.0), 95.0, 110.0),
        (_row(exit_price=102.0), 95.0, 110.0),
        (_row(exit_price=None), 95.0, 110.0),
        (_row(setup_type="intent_reduce"), 95.0, 110.0),
        (_row(), None, None),
        (_row(direction="?"), 95.0, 110.0),
        (_row(notes=json.dumps({"exit_price_source": "prop_estimate"})), 95.0, 110.0),
    ]
    for row, sl, tp in rows:
        counts[classify_bracket_outcome(row, sl, tp)[0]] += 1
    assert sum(counts.values()) == len(rows)


def test_unparseable_notes_never_crash_and_never_fabricate_a_refusal():
    row = _row(exit_price=94.0, notes="{not json")
    assert classify_bracket_outcome(row, 95.0, 110.0)[0] == BRACKET_REACHED_SL
