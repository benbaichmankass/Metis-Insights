"""Tests for scripts/ops/leg_flow_report.py's unmapped-prop-status accounting
(PI-20260926-Y5MVFDD8-0001, the follow-up half of E18 post-E35 triage that
PR #13000 did not ship).

`find_unmapped_prop_tickets` is the pure row-accounting check:
`unmapped = len(ticket_rows_for_strategy) - received - held_back`. A nonzero
result means some ticket's `status` satisfied neither
`leg_flow_detector.PROP_RECEIVED_STATUSES` nor `PROP_NOT_RECEIVED_STATUSES` —
the exact gap that let `expiry_prompted` fall through silently (neither
bucket) before PR #13000. This module exists so a FUTURE such status is loud
(a surfaced count + the offending status name) instead of silently absent
from both buckets again.
"""
from __future__ import annotations

from scripts.ops import leg_flow_detector as lfd
from scripts.ops import leg_flow_report as lfr


def test_unknown_status_appears_as_unmapped_with_its_name():
    """A status absent from both vocabularies must be counted AND named,
    never just dropped — the `expiry_prompted` failure mode."""
    rows = [
        {"strategy": "x", "status": "filled"},
        {"strategy": "x", "status": "shadow"},
        {"strategy": "x", "status": "some_future_status"},
    ]
    received, held_back = lfd.count_received_prop(rows, strategy="x")
    unmapped, statuses = lfr.find_unmapped_prop_tickets(
        rows, strategy="x", received=received, held_back=held_back,
    )
    assert unmapped == 1
    assert statuses == ["some_future_status"]


def test_multiple_distinct_unknown_statuses_are_all_named_sorted_and_deduped():
    rows = [
        {"strategy": "x", "status": "zzz_unknown"},
        {"strategy": "x", "status": "aaa_unknown"},
        {"strategy": "x", "status": "aaa_unknown"},  # duplicate, not double-counted in the list
        {"strategy": "x", "status": None},           # missing status entirely
    ]
    received, held_back = lfd.count_received_prop(rows, strategy="x")
    unmapped, statuses = lfr.find_unmapped_prop_tickets(
        rows, strategy="x", received=received, held_back=held_back,
    )
    assert unmapped == 4
    assert statuses == ["<missing>", "aaa_unknown", "zzz_unknown"]


def test_known_statuses_give_unmapped_zero():
    """Every status accounted for by the two vocabularies must reconcile to
    zero unmapped, with no offending status(es) to report."""
    rows = [
        {"strategy": "x", "status": "filled"},          # received
        {"strategy": "x", "status": "emitted"},          # received
        {"strategy": "x", "status": "expiry_prompted"},  # received
        {"strategy": "x", "status": "shadow"},           # held back
        {"strategy": "x", "status": "suppressed"},       # held back
        {"strategy": "x", "status": "skipped"},          # held back
    ]
    received, held_back = lfd.count_received_prop(rows, strategy="x")
    unmapped, statuses = lfr.find_unmapped_prop_tickets(
        rows, strategy="x", received=received, held_back=held_back,
    )
    assert unmapped == 0
    assert statuses == []


def test_negative_control_other_strategys_unknown_status_does_not_bleed_in():
    """An unmapped status on a DIFFERENT strategy must not be attributed to
    this one — the population this check reconciles against is
    `ticket_rows_for_strategy`, not every row in the pull."""
    rows = [
        {"strategy": "x", "status": "filled"},
        {"strategy": "x", "status": "shadow"},
        {"strategy": "y", "status": "totally_unknown"},
    ]
    received, held_back = lfd.count_received_prop(rows, strategy="x")
    unmapped, statuses = lfr.find_unmapped_prop_tickets(
        rows, strategy="x", received=received, held_back=held_back,
    )
    assert unmapped == 0
    assert statuses == []


def test_negative_control_no_tickets_for_strategy_is_not_unmapped():
    """An empty population (no tickets at all for this strategy in the pull)
    must read as zero unmapped, not as a false positive."""
    rows = [{"strategy": "y", "status": "filled"}]
    received, held_back = lfd.count_received_prop(rows, strategy="x")
    assert (received, held_back) == (0, 0)
    unmapped, statuses = lfr.find_unmapped_prop_tickets(
        rows, strategy="x", received=received, held_back=held_back,
    )
    assert unmapped == 0
    assert statuses == []
