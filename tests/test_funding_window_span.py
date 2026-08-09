"""The funding puller must declare the window it was SERVED, not just requested.

Why this exists: on 2026-08-08 the sibling fills puller was measured returning a
**7-day slice for a 90-day request** — Bybit V5 caps the queryable RANGE while
retaining 2 years, so a wide ``since`` MOVES the window to the old end rather
than widening it. The only reason that was caught is a monotonicity violation
(a count that went DOWN as the window got wider). Nothing in the puller itself
said anything was wrong; ``candidates=0`` read as "the venue has no history",
which was exactly backwards.

The funding puller is called with a **hardcoded 30-day** window. If the same cap
applies there, it has been serving ``[now-30d, now-23d]`` — silently missing the
most RECENT three weeks while printing a healthy row count
(``BL-20260808-FUNDING-PULLER-SAME-RANGE-CAP-EXPOSURE``, still unverified for
this endpoint).

So the puller now states the served span every run. The invariant under test is
narrow and deliberate: it must **flag the shape** without **claiming the cause**,
because from inside the puller a stale tail is genuinely ambiguous — no funding
accrued, or the window was never queried — and only the journal knows whether
positions were open. Naming a cause no code path tested is the sub-class-A
diagnostic-provenance defect this repo has already been bitten by.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from src.runtime.exchange_funding_puller import fetch_funding_window

NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)


def _entry(when: datetime, symbol: str = "BTC/USDT:USDT", amount: float = -0.5) -> dict:
    return {
        "id": f"f{int(when.timestamp())}",
        "symbol": symbol,
        "amount": amount,
        "timestamp": int(when.timestamp() * 1000),
        "info": {},
    }


def _pull(entries, *, days: int, caplog):
    def _fetch(_sym, _since, _limit, _params):
        return entries

    with caplog.at_level(logging.INFO, logger="src.runtime.exchange_funding_puller"):
        rows = fetch_funding_window(_fetch, "bybit_2", days=days, now=NOW)
    return rows, caplog.text


def test_a_healthy_window_reports_its_span_and_does_not_warn(caplog):
    """Funding settling right up to the window end is normal — no warning."""
    entries = [_entry(NOW - timedelta(days=d)) for d in (29, 20, 10, 2, 0)]
    rows, text = _pull(entries, days=30, caplog=caplog)
    assert len(rows) == 5
    assert "SERVED span" in text
    assert "NEWEST row is" not in text, "a current window must not be flagged"


def test_a_moved_window_is_flagged_by_its_shape(caplog):
    """The signature of a range cap: rows stop 23 days short of the window end.

    This is exactly what a 30-day request would return if Bybit served
    ``[now-30d, now-23d]``. Note the row COUNT is healthy — five payments, run
    OK — which is why a count-based check could never catch it.
    """
    entries = [_entry(NOW - timedelta(days=d)) for d in (30, 28, 26, 24, 23)]
    rows, text = _pull(entries, days=30, caplog=caplog)
    assert len(rows) == 5, "the count looks fine; that is the whole problem"
    assert "NEWEST row is 23.0d old" in text
    assert "most recent ~23.0d of the requested window returned nothing" in text


def test_the_warning_names_both_causes_and_picks_neither(caplog):
    """Diagnostic provenance: state what was computed, not a cause never tested.

    From inside the puller a stale tail is genuinely ambiguous. Asserting the
    warning offers BOTH readings is the point — a confident single cause here
    would send a reader to the wrong fix, and this function has no access to
    the position history that would settle it.
    """
    entries = [_entry(NOW - timedelta(days=d)) for d in (30, 25)]
    _rows, text = _pull(entries, days=30, caplog=caplog)
    assert "held no perp position" in text          # cause (a)
    assert "capped the query RANGE" in text          # cause (b)
    assert "NOT distinguished here" in text


def test_zero_rows_says_it_cannot_distinguish_rather_than_declaring_empty(caplog):
    """An empty result is not evidence of an empty book.

    The same lesson as ``FillsWindowUnavailable``: the fills puller returned
    ``[]`` for two demo accounts whose every request was rejected, and the run
    printed success over 100% missing coverage for weeks.
    """
    _rows, text = _pull([], days=30, caplog=caplog)
    assert "0 dated rows returned" in text
    assert "cannot distinguish" in text


def test_undated_rows_do_not_fabricate_a_span(caplog):
    """A row with no usable timestamp contributes no span claim."""
    entries = [{"id": "x", "symbol": "BTC/USDT:USDT", "amount": -0.1}]
    rows, text = _pull(entries, days=30, caplog=caplog)
    assert len(rows) == 1, "the row is still stored; only the span claim abstains"
    assert "0 dated rows returned" in text


def test_a_short_window_is_judged_against_its_own_length(caplog):
    """The staleness bar is a FRACTION of the request, not a fixed day count.

    A 2-day-old newest row is unremarkable in a 30-day window and glaring in a
    3-day one. A fixed threshold would either spam the short windows or go
    silent on the long ones — and the long ones are where the cap hides.
    """
    entries = [_entry(NOW - timedelta(days=2))]
    _rows, wide = _pull(entries, days=30, caplog=caplog)
    assert "NEWEST row is" not in wide

    caplog.clear()
    _rows, narrow = _pull(entries, days=3, caplog=caplog)
    assert "NEWEST row is 2.0d old" in narrow
