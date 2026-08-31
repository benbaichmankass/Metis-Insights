"""Wallet truth from the venue's transaction log — the LIVE replacement for the
hand-pasted UM export.

Every test here is written against the failure that motivated the module: a
real-money account whose authoritative figure froze on 2026-07-13 because it
came from a CSV. The point is that the SAME quantity is now computable from
rows we pull ourselves, and that the states around it cannot collapse.
"""
from __future__ import annotations

import src.runtime.bybit_wallet_truth as wt


def _row(**kw):
    base = {"type": "TRADE", "currency": "USDT", "change": "0", "fee": "0", "funding": "0"}
    base.update(kw)
    return base


# ── the quantity itself ───────────────────────────────────────────────────────

def test_realized_is_the_sum_of_change_excluding_transfers():
    """This reproduces the hand export's definition: UM Change minus transfers."""
    rows = [
        _row(type="TRADE", change="-100.00"),
        _row(type="TRADE", change="+40.00"),
        _row(type="SETTLEMENT", change="-2.52"),   # funding: IS P&L
        _row(type="TRANSFER_IN", change="5000.00"),   # deposit: NOT P&L
        _row(type="TRANSFER_OUT", change="-1000.00"), # withdrawal: NOT P&L
    ]
    v = wt.compute_wallet_truth("bybit_2", rows)
    assert v.state == wt.STATE_MEASURED
    assert v.realized_usd == -62.52
    assert v.rows_excluded_transfers == 2


def test_a_funded_account_does_not_look_profitable():
    """The single most dangerous mistake available: counting a deposit as P&L.

    Without the transfer exclusion this account reads +$4,900 having actually
    lost $100.
    """
    rows = [_row(type="TRANSFER_IN", change="5000"), _row(type="TRADE", change="-100")]
    v = wt.compute_wallet_truth("bybit_2", rows)
    assert v.realized_usd == -100.0, "a deposit must never register as profit"


def test_change_is_used_not_cashflow():
    """`cashFlow` excludes fees; `change` is the wallet delta. Summing the wrong
    column is the class this whole provenance family exists to stop."""
    rows = [_row(type="TRADE", change="-11.50", cashFlow="-10.00", fee="-1.50")]
    v = wt.compute_wallet_truth("bybit_2", rows)
    assert v.realized_usd == -11.50
    assert v.realized_usd != -10.00


# ── states, never collapsed ───────────────────────────────────────────────────

def test_could_not_look_is_not_zero():
    v = wt.compute_wallet_truth("bybit_2", None)
    assert v.state == wt.STATE_UNREADABLE
    assert v.realized_usd is None, "unreadable must be None, never 0.0"


def test_empty_window_is_distinct_from_unreadable():
    """We looked and found nothing != we did not look. Opposite statements."""
    empty = wt.compute_wallet_truth("bybit_2", [])
    unread = wt.compute_wallet_truth("bybit_2", None)
    assert empty.state == wt.STATE_NO_ROWS
    assert unread.state == wt.STATE_UNREADABLE
    assert empty.state != unread.state


def test_a_genuine_flat_window_reports_measured_zero():
    """Zero IS a real reading and must not be suppressed into 'no rows'."""
    v = wt.compute_wallet_truth("bybit_2", [_row(type="TRADE", change="0.0")])
    assert v.state == wt.STATE_MEASURED
    assert v.realized_usd == 0.0


def test_unreadable_reason_is_carried():
    v = wt.compute_wallet_truth("b", [], unreadable_reason="creds_missing")
    assert v.state == wt.STATE_UNREADABLE and v.reason == "creds_missing"


# ── currency discipline ───────────────────────────────────────────────────────

def test_non_usd_rows_are_counted_never_summed():
    """Adding BTC to USDT yields a number in no unit. Report, never convert."""
    rows = [_row(currency="USDT", change="-50"), _row(currency="BTC", change="0.004")]
    v = wt.compute_wallet_truth("bybit_2", rows)
    assert v.realized_usd == -50.0
    assert v.non_usd_rows == 1
    assert "BTC" in v.currencies_seen, "a dropped row must still be visible"


def test_unreadable_change_is_not_folded_in_as_zero():
    rows = [_row(change="-25"), _row(change="not-a-number")]
    v = wt.compute_wallet_truth("bybit_2", rows)
    assert v.realized_usd == -25.0
    assert v.rows_counted == 1
    assert v.non_usd_rows == 1


# ── non-vacuity: the probe can find a positive ────────────────────────────────

def test_all_declared_states_are_reachable():
    """A state nothing can produce is decoration. Each must be constructible."""
    seen = {
        wt.compute_wallet_truth("a", [_row(change="1")]).state,
        wt.compute_wallet_truth("a", []).state,
        wt.compute_wallet_truth("a", None).state,
    }
    assert seen == {wt.STATE_MEASURED, wt.STATE_NO_ROWS, wt.STATE_UNREADABLE}
    assert wt.STATE_NOT_PULLED in wt.ALL_STATES


def test_the_2026_07_13_ledger_figure_is_reproducible_from_rows():
    """The committed ledger says bybit_2 realized -262.52 with fees -147.81.

    The point is not the exact history — it is that this shape of number is
    now computable from rows we pull, so it can never freeze again.
    """
    rows = [
        _row(type="TRADE", change="-114.71", fee="-147.81"),
        _row(type="SETTLEMENT", change="-0.55", funding="-0.55"),
        _row(type="TRADE", change="-147.26"),
        _row(type="TRANSFER_IN", change="5000"),
    ]
    v = wt.compute_wallet_truth("bybit_2", rows)
    assert round(v.realized_usd, 2) == -262.52
    assert round(v.fees_usd, 2) == -147.81
    assert round(v.funding_usd, 2) == -0.55
    assert v.is_measured
