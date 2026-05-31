"""Reduce-only legs must not emit the "🟢 TRADE OPENED" lifecycle ping.

A reduce-only leg is a PARTIAL CLOSE of an existing position, not a new
open. The intent layer flips the leg's ``direction`` to the opposite
(reduce) side while keeping the parent position's entry/SL/TP, so firing
the open-ping for it printed a phantom "<SYMBOL> SHORT" carrying the
parent LONG's open-side SL/TP (SL below / TP above entry) — impossible
for a real short. ``execute._log_trade_to_journal`` now gates the
open-ping on ``not intent_reduce`` (health-review BL-20260531-001).

These pin the GUARD CONDITION the call site uses, mirroring the
lightweight predicate-test style in test_all_accounts_failed_ping.py
(the full ``_log_trade_to_journal`` path does real DB writes).
"""
from __future__ import annotations

import inspect

from src.units.accounts import execute as ex


def _open_ping_should_fire(*, status: str, is_dry: bool, intent_reduce: bool) -> bool:
    """The exact guard at the enqueue_trade_open call site."""
    return status == "open" and not is_dry and not intent_reduce


def test_normal_live_open_fires_ping():
    assert _open_ping_should_fire(status="open", is_dry=False, intent_reduce=False) is True


def test_reduce_leg_suppresses_ping():
    """The 2026-05-31 case: a reduce-only leg trimming an open long must
    NOT fire a 'TRADE OPENED — SHORT' ping."""
    assert _open_ping_should_fire(status="open", is_dry=False, intent_reduce=True) is False


def test_dry_run_open_suppresses_ping():
    assert _open_ping_should_fire(status="open", is_dry=True, intent_reduce=False) is False


def test_rejected_status_suppresses_ping():
    assert _open_ping_should_fire(status="rejected", is_dry=False, intent_reduce=False) is False


def test_call_site_actually_carries_the_intent_reduce_guard():
    """Guard against regression: the live source must gate the open-ping
    on ``not intent_reduce`` (not just status/is_dry). Pins the fix so a
    future refactor can't silently drop the reduce-leg carve-out."""
    src = inspect.getsource(ex._log_trade_to_journal)
    # The open-ping condition and the enqueue call both live in this fn.
    assert "enqueue_trade_open" in src
    assert "not intent_reduce" in src, (
        "open-ping call site lost its reduce-leg guard — reduce legs will "
        "again emit phantom 'TRADE OPENED — <opposite side>' pings"
    )
