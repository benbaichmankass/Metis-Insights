"""Hedge-mode plumbing: inert by default, correct by book when armed.

Workplan item **T.2**. `BL-20260821-PAIRS-SOL-ETH-STRANDS-ON-EVERY-OPEN`.

TWO THINGS ARE UNDER TEST, and the first matters more than the second today.

1. **The inert path is byte-for-byte.** With an empty allowlist NO call site may
   add a `positionIdx` key to any payload. The operator's approval for this
   change was explicitly "with an EMPTY allowlist (byte-for-byte no behaviour
   change)", so a test that only checked the armed path would not be testing the
   thing that was approved.

2. **`positionIdx` names the BOOK, not the order side.** Closing a LONG sends
   `side="Sell"` and belongs to `positionIdx=1`. A resolver keyed on order side
   passes every naive test and silently acts on the wrong book in production;
   these tests pin the inversion at the reduce-only boundary.
"""
from __future__ import annotations

import pytest

from src.runtime import bybit_position_mode as pm


# ------------------------------------------------------- the inert default


def test_empty_allowlist_resolves_one_way(monkeypatch):
    monkeypatch.delenv(pm._ENV_ALLOWLIST, raising=False)
    res = pm.position_idx_for("bybit_1", "SOLUSDT", "long")
    assert res.idx is None
    assert res.state == pm.ONE_WAY


def test_apply_leaves_the_payload_untouched_when_inert(monkeypatch):
    """THE BYTE-FOR-BYTE ASSERTION. No key added, nothing mutated."""
    monkeypatch.delenv(pm._ENV_ALLOWLIST, raising=False)
    kwargs = {"category": "linear", "symbol": "SOLUSDT", "side": "Buy", "qty": "1"}
    before = dict(kwargs)
    res = pm.apply_position_idx(kwargs, "bybit_1", "SOLUSDT", "long")
    assert kwargs == before, "an inert allowlist must not change a single key"
    assert "positionIdx" not in kwargs
    assert res.state == pm.ONE_WAY


def test_an_unrelated_symbol_on_an_armed_account_stays_one_way(monkeypatch):
    """The allowlist is per (account, SYMBOL) — arming one must not arm the account."""
    monkeypatch.setenv(pm._ENV_ALLOWLIST, "bybit_1:SOLUSDT")
    assert pm.position_idx_for("bybit_1", "ETHUSDT", "long").state == pm.ONE_WAY
    assert pm.position_idx_for("bybit_2", "SOLUSDT", "long").state == pm.ONE_WAY


# ------------------------------------------------------- the armed path


def test_armed_pair_resolves_the_book_by_position_direction(monkeypatch):
    monkeypatch.setenv(pm._ENV_ALLOWLIST, "bybit_1:SOLUSDT")
    assert pm.position_idx_for("bybit_1", "SOLUSDT", "long") == (1, pm.HEDGE_LONG, "")
    assert pm.position_idx_for("bybit_1", "SOLUSDT", "short") == (2, pm.HEDGE_SHORT, "")


def test_symbol_match_is_case_insensitive(monkeypatch):
    monkeypatch.setenv(pm._ENV_ALLOWLIST, "bybit_1:solusdt")
    assert pm.position_idx_for("bybit_1", "SOLUSDT", "long").idx == 1


def test_closing_a_long_belongs_to_the_LONG_book(monkeypatch):
    """The inversion this module exists to get right.

    A reduce-only close of a long carries `side="Sell"`. Resolving on that side
    would return 2 and act on the SHORT book — an order the venue accepts and
    that does nothing the caller intended.
    """
    monkeypatch.setenv(pm._ENV_ALLOWLIST, "bybit_1:SOLUSDT")
    order_side = "Sell"                      # what a long-close puts on the wire
    book = pm.opposite_side(order_side)      # -> "long"
    assert book == "long"
    assert pm.position_idx_for("bybit_1", "SOLUSDT", book).idx == 1
    # And the naive version really is wrong, so the test has teeth:
    assert pm.position_idx_for("bybit_1", "SOLUSDT", order_side).idx == 2


def test_opposite_side_round_trip():
    assert pm.opposite_side("Buy") == "short"
    assert pm.opposite_side("Sell") == "long"
    assert pm.opposite_side("long") == "short"
    assert pm.opposite_side(None) is None
    assert pm.opposite_side("garbage") is None


# ---------------------------------------------- the four-state discipline


def test_unresolved_is_distinct_from_one_way_and_sends_nothing(monkeypatch):
    """`idx is None` twice over, for OPPOSITE reasons — the states must differ."""
    monkeypatch.setenv(pm._ENV_ALLOWLIST, "bybit_1:SOLUSDT")
    res = pm.position_idx_for("bybit_1", "SOLUSDT", None)
    assert res.idx is None
    assert res.state == pm.UNRESOLVED
    assert "neither long nor short" in res.reason
    kwargs = {"symbol": "SOLUSDT"}
    pm.apply_position_idx(kwargs, "bybit_1", "SOLUSDT", "sideways")
    assert "positionIdx" not in kwargs, (
        "an unresolved book must send NO positionIdx so Bybit refuses the order; "
        "guessing would place a live order against the wrong book"
    )


@pytest.mark.parametrize("raw", ["", "   ", "bybit_1", "no-colon,also-none", ",,,"])
def test_malformed_allowlist_entries_are_dropped_not_widened(monkeypatch, raw):
    monkeypatch.setenv(pm._ENV_ALLOWLIST, raw)
    assert pm.position_idx_for("bybit_1", "SOLUSDT", "long").state == pm.ONE_WAY


def test_allowlist_is_read_at_call_time(monkeypatch):
    """A VM env flip must take effect without a redeploy."""
    monkeypatch.delenv(pm._ENV_ALLOWLIST, raising=False)
    assert pm.position_idx_for("bybit_1", "SOLUSDT", "long").idx is None
    monkeypatch.setenv(pm._ENV_ALLOWLIST, "bybit_1:SOLUSDT")
    assert pm.position_idx_for("bybit_1", "SOLUSDT", "long").idx == 1
    monkeypatch.delenv(pm._ENV_ALLOWLIST, raising=False)
    assert pm.position_idx_for("bybit_1", "SOLUSDT", "long").idx is None


def test_hedge_mode_enabled_needs_both_account_and_symbol(monkeypatch):
    monkeypatch.setenv(pm._ENV_ALLOWLIST, "bybit_1:SOLUSDT")
    assert pm.hedge_mode_enabled("bybit_1", "SOLUSDT") is True
    assert pm.hedge_mode_enabled(None, "SOLUSDT") is False
    assert pm.hedge_mode_enabled("bybit_1", None) is False


# ------------------------------------------- every call site is wired


def test_every_bybit_order_payload_site_consults_the_resolver():
    """Single-homing check: a site that skips the resolver half-applies the change.

    Asserted structurally rather than by grepping for a symbol name, because the
    failure mode is a NEW call site added later that never learns about hedge
    mode — exactly how `positionIdx=0` came to be hardcoded in the first place.
    """
    src = open("src/units/accounts/execute.py").read()
    assert src.count("bybit_position_mode.apply_position_idx(") == 4, (
        "execute.py has four Bybit payload-building sites (submit, test-submit, "
        "modify/set_trading_stop, close); each must resolve its book"
    )
    mon = open("src/runtime/order_monitor.py").read()
    assert "position_idx_for(" in mon
    assert "positionIdx=0," not in mon, (
        "the naked-autoprotect re-arm must not hardcode the one-way book again"
    )
