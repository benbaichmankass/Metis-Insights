"""Alpaca protection must be graded as a QUANTITY — the second half of
BL-20260816-COVERAGE-IS-ONE-SIDED.

That row was fixed here for SIDES only on 2026-08-16 (stop vs target). The
QUANTITY half was never ported: `_check_broker_naked_equity_positions` passed
`stop_qty=None` to its page under the comment *"Alpaca grades sides, not
qty"*, while Bybit had `covered_qty` (PR #8000) and IB had
`protection_coverage` (BL-20260814-IB-PROTECTION-BOOLEAN-NOT-QUANTITY).

Alpaca nets per symbol per account, so N journal trades share ONE broker
position sized to their SUM while each trade's bracket is sized to its OWN
qty. One surviving bracket therefore made the whole netted position read
protected.

MEASURED 2026-09-08T02:17:10Z via /api/diag/alpaca_open_orders,
read_state=orders_read (a confirmed clean read) — `alpaca_portfolio`/TLT held a
**72-share short carrying a resting stop for 16 shares**. Journal trades 5266
(16, opened 08-31) and 5414 (56, opened 09-03) share the one netted position
and only 5266's bracket rests. Fifty-six shares were unprotected and every
existing accessor reported the book covered. The fixtures below are that
reading.

Exercised through a fake transport so no broker is needed.
"""
from __future__ import annotations

import pytest

from src.units.accounts.alpaca_client import AlpacaClient


class _FakeClient(AlpacaClient):
    """Bypass __init__ so no credentials/HTTP are involved."""

    def __init__(self, legs):
        self._legs = legs

    def _open_orders_for_symbol(self, symbol):  # type: ignore[override]
        return [
            o for o in self._legs
            if str(o.get("symbol") or symbol).upper() == str(symbol).upper()
        ]


def _cov(legs, qty, side="sell"):
    return _FakeClient(legs).protection_coverage(
        "TLT", position={"qty": qty, "side": side}
    )


# ── The live measurement, reproduced ───────────────────────────────────────
def test_the_measured_partial_coverage_is_reported_as_a_quantity():
    """alpaca_portfolio/TLT as it actually rested on 2026-09-08T02:17:10Z."""
    legs = [
        {"type": "stop", "side": "buy", "qty": 16.0},
        {"type": "limit", "side": "buy", "qty": 16.0},
    ]
    cov = _cov(legs, qty=72.0)
    assert cov["size"] == 72.0
    assert cov["stop_qty"] == 16.0
    assert cov["target_qty"] == 16.0
    assert cov["unknown_qty_legs"] == 0
    # The whole point: 56 shares are unprotected, and the grade says so.
    assert cov["size"] - cov["stop_qty"] == 56.0


def test_a_boolean_cannot_distinguish_partial_from_full():
    """The defect, stated as a test: sides agree, quantities do not.

    Both books below grade `stop=True, target=True` through
    `protection_state`. Only the quantity grade separates them — which is why
    a side-only accessor reported a 56-share naked position as covered.
    """
    partial = [
        {"type": "stop", "side": "buy", "qty": 16.0},
        {"type": "limit", "side": "buy", "qty": 16.0},
    ]
    full = [
        {"type": "stop", "side": "buy", "qty": 72.0},
        {"type": "limit", "side": "buy", "qty": 72.0},
    ]
    assert _FakeClient(partial).protection_state("TLT")["stop"] is True
    assert _FakeClient(full).protection_state("TLT")["stop"] is True
    assert _cov(partial, 72.0)["stop_qty"] == 16.0
    assert _cov(full, 72.0)["stop_qty"] == 72.0


# ── Ungradeable is neither zero nor full ───────────────────────────────────
@pytest.mark.parametrize("bad_qty", [None, "", "abc", 0, -5, float("nan")])
def test_an_unreadable_leg_qty_makes_coverage_ungradeable_never_zero(bad_qty):
    """*We did not look* must not render as *nothing is covering it*.

    Reporting an unparseable leg as zero pages a false naked; reporting it as
    full hides a real one. Both are worse than refusing to grade — the same
    refusal IB and Bybit make.
    """
    cov = _cov([{"type": "stop", "side": "buy", "qty": bad_qty}], 72.0)
    assert cov["unknown_qty_legs"] == 1
    assert cov["stop_qty"] == 0.0  # not banked...
    # ...and the caller is told WHY, so it cannot read 0.0 as "naked".
    assert cov["unknown_qty_legs"] > 0


def test_an_unreadable_position_side_makes_every_leg_ungradeable():
    """Without the position side, protection and an opening order are the same.

    So the legs are counted as ungradeable rather than banked as coverage.
    """
    cov = _cov([{"type": "stop", "side": "buy", "qty": 72.0}], 72.0, side="")
    assert cov["unknown_qty_legs"] == 1
    assert cov["stop_qty"] == 0.0


def test_a_read_failure_returns_None_so_the_caller_skips():
    """`None` is the third state: never grade on an unconfirmed read."""
    client = _FakeClient([])
    client._open_orders_for_symbol = lambda symbol: None  # type: ignore
    assert client.protection_coverage("TLT", position={"qty": 72.0, "side": "sell"}) is None


# ── Side discipline ────────────────────────────────────────────────────────
def test_a_same_side_order_is_not_counted_as_protection():
    """A resting order that ADDS to the position does not protect it.

    On a 72-share SHORT the reducing side is `buy`; a resting `sell` stop is an
    entry, and counting it would manufacture coverage.
    """
    cov = _cov([{"type": "stop", "side": "sell", "qty": 72.0}], 72.0, side="sell")
    assert cov["stop_qty"] == 0.0
    assert cov["unknown_qty_legs"] == 0  # gradeable — it is simply not protection


def test_reducing_side_is_a_function_of_the_position_side():
    assert AlpacaClient._reducing_side_for("long") == "sell"
    assert AlpacaClient._reducing_side_for("buy") == "sell"
    assert AlpacaClient._reducing_side_for("short") == "buy"
    assert AlpacaClient._reducing_side_for("sell") == "buy"
    assert AlpacaClient._reducing_side_for("") == ""


def test_a_long_position_is_covered_by_sell_legs():
    cov = _FakeClient(
        [{"type": "stop", "side": "sell", "qty": 162.0}]
    ).protection_coverage("GDX", position={"qty": 162.0, "side": "buy"})
    assert cov["stop_qty"] == 162.0


# ── The precedence trap, again, on the quantity path ───────────────────────
def test_stop_limit_counts_as_a_STOP_not_a_target():
    """`"stop_limit"` contains "limit"; a limit-first test would file it as a
    take-profit and MANUFACTURE target coverage — strictly worse than the bug
    being fixed. Mirrors `IBClient._protective_leg_side`."""
    cov = _cov([{"type": "stop_limit", "side": "buy", "qty": 72.0}], 72.0)
    assert cov["stop_qty"] == 72.0
    assert cov["target_qty"] == 0.0


@pytest.mark.parametrize("otype,expect", [
    ("stop", "stop"),
    ("stop_limit", "stop"),
    ("trailing_stop", "stop"),
    ("limit", "target"),
    ("market", ""),
    ("", ""),
])
def test_leg_side_classifier(otype, expect):
    assert AlpacaClient._leg_protective_side(otype) == expect


# ── Flat is a positive answer, not a failure ───────────────────────────────
def test_a_flat_position_grades_flat_not_naked():
    cov = _cov([], 0.0)
    assert cov["source"] == "flat"
    assert cov["size"] == 0.0
    assert cov["stop_qty"] == 0.0


def test_a_fully_covered_book_grades_covered():
    cov = _cov([
        {"type": "stop", "side": "buy", "qty": 707.0},
        {"type": "limit", "side": "buy", "qty": 707.0},
    ], 707.0)
    assert cov["stop_qty"] == cov["size"] == 707.0
    assert cov["unknown_qty_legs"] == 0


def test_multiple_brackets_on_one_netted_position_sum():
    """Two trades, two brackets, one netted position — coverage is the SUM."""
    cov = _cov([
        {"type": "stop", "side": "buy", "qty": 16.0},
        {"type": "stop", "side": "buy", "qty": 56.0},
    ], 72.0)
    assert cov["stop_qty"] == 72.0
