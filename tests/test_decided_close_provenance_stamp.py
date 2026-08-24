"""M39(A) — every DECIDED close stamps its provenance, including the good one.

`BL-20260824-THE-DECIDED-EXIT-PATH-IS-THE-UNMEASURED-ONE`.

`_apply_update` resolves `exit_price_source` to `"exchange"` (a real venue fill,
via `_capture_fill_details` -> `account_order_status`) or `"verdict"` (the
monitor's projected level). It used to write the note ONLY under
`exit_price_source == "verdict"` — so the branch carrying the STRONGEST evidence
stamped nothing and the row classified as UNVERIFIED.

Measured on the live journal before the fix: of 175 unverified DECIDED closes,
**175 (100%)** carried an `exit_price` and **zero** carried a stamp, and the
string `"exchange"` appeared **0 times** in the whole decided-close source
distribution — the branch had never written a note in the system's history.

These tests pin all three outcomes, because the failure mode here was silence:
a test that only checked the `verdict` path passed happily while the measured
path wrote nothing.
"""
from __future__ import annotations

import json

import pytest

from src.runtime import order_monitor as om
from src.runtime import provenance as P


class _FakeDB:
    def __init__(self, trade):
        self._trade = dict(trade)
        self.trade_updates = []
        self.pkg_updates = []

    def get_trades(self, filters=None, limit=None):
        if self._trade.get("status") == "closed":
            return []
        row = dict(self._trade)
        for k, v in (filters or {}).items():
            if str(row.get(k)) != str(v):
                return []
        return [row]

    def update_order_package(self, pkg_id, updates):
        self.pkg_updates.append((pkg_id, updates))

    def update_trade(self, tid, updates):
        self.trade_updates.append((tid, updates))
        if str(tid) == str(self._trade.get("id")):
            self._trade.update(updates)


_MATCHED = {
    "id": 5150, "account_id": "bybit_1", "symbol": "BTCUSDT",
    "direction": "long", "position_size": 0.01,
    "status": "open", "order_package_id": "pkg-m39a", "is_backtest": 0,
}
_OPEN_PKG = {
    "order_package_id": "pkg-m39a", "linked_trade_id": 5150,
    "strategy_name": "vwap", "symbol": "BTCUSDT",
}
_VERDICT_WITH_PRICE = {"action": "close", "reason": "vwap_cross", "exit_price": 61000.0}
_VERDICT_NO_PRICE = {"action": "close", "reason": "vwap_cross"}
_OK = {"ok": True, "exchange_order_id": "ex-1"}


def _drive(monkeypatch, verdict, fill_details, matched=None):
    monkeypatch.setattr(om, "_send_close_to_exchange", lambda _t: dict(_OK))
    monkeypatch.setattr(om, "_capture_fill_details", lambda *_a, **_k: fill_details)
    db = _FakeDB(matched or _MATCHED)
    om._apply_update(db, dict(_OPEN_PKG), dict(verdict), om._StrategyTickSummary())
    return db


def _stamped_notes(db):
    """Return the notes dict the close wrote, or None if it wrote none."""
    for _tid, upd in db.trade_updates:
        if upd.get("status") == "closed" and "notes" in upd:
            return json.loads(upd["notes"])
    return None


# --------------------------------------------------------------------------- #
# The regression: the MEASURED branch was silent
# --------------------------------------------------------------------------- #

def test_a_real_venue_fill_is_stamped_and_classifies_measured(monkeypatch):
    """The branch that had NEVER written a note in the system's history.

    `_capture_fill_details` returns only on a non-zero `avg_price` from
    `account_order_status`, so this is genuine broker truth — unlike
    `recorded_exit_price`, demoted from MEASURED the same day for claiming a
    fill it never had.
    """
    db = _drive(monkeypatch, _VERDICT_WITH_PRICE, {"avg_price": 60950.5, "filled_qty": 0.01})
    notes = _stamped_notes(db)
    assert notes is not None, "the measured branch wrote NO notes — the original bug"
    assert notes["exit_price_source"] == "exchange"
    assert P.classify(notes["exit_price_source"], "exit_price_source") == P.MEASURED


def test_the_fill_price_is_what_lands_on_the_row(monkeypatch):
    db = _drive(monkeypatch, _VERDICT_WITH_PRICE, {"avg_price": 60950.5, "filled_qty": 0.01})
    closes = [u for _t, u in db.trade_updates if u.get("status") == "closed"]
    assert closes and closes[0]["exit_price"] == 60950.5


# --------------------------------------------------------------------------- #
# Pre-existing behaviour must be preserved exactly
# --------------------------------------------------------------------------- #

def test_verdict_fallback_still_stamps_verdict(monkeypatch):
    """Unchanged from before the fix — a projected level, ESTIMATED."""
    db = _drive(monkeypatch, _VERDICT_WITH_PRICE, None)
    notes = _stamped_notes(db)
    assert notes["exit_price_source"] == "verdict"
    assert P.classify(notes["exit_price_source"], "exit_price_source") == P.ESTIMATED


def test_an_empty_fill_dict_falls_to_verdict(monkeypatch):
    """`_capture_fill_details` yields None for dry-run / read-failure /
    not-found; a dict with no usable avg_price must not read as a fill."""
    db = _drive(monkeypatch, _VERDICT_WITH_PRICE, {"avg_price": 0.0})
    assert _stamped_notes(db)["exit_price_source"] == "verdict"


# --------------------------------------------------------------------------- #
# The third state: we closed and could not establish a price
# --------------------------------------------------------------------------- #

def test_no_price_on_either_branch_declares_unmeasured_not_silence(monkeypatch):
    """Previously skipped as "nothing meaningful to source-tag".

    It is meaningful: it says we closed and could not establish what price we
    got. Silence makes that identical to a row nobody looked at — the collapse
    CLAUDE-RULES-CANONICAL § "Collapsed states" forbids.
    """
    db = _drive(monkeypatch, _VERDICT_NO_PRICE, None)
    notes = _stamped_notes(db)
    assert notes is not None, "a priceless close wrote nothing — the collapse"
    assert notes["exit_price_source"] == P.UNMEASURED_MARKER


def test_unmeasured_shares_the_unverified_trust_bucket(monkeypatch):
    """Same TRUST as unverified (neither is a measurement); the difference is
    ACCOUNTABILITY, read from the raw string — so it must NOT be promoted."""
    assert P.classify(P.UNMEASURED_MARKER, "exit_price_source") == P.UNVERIFIED
    assert P.UNMEASURED_MARKER not in P.MEASURED_SOURCES
    assert P.UNMEASURED_MARKER not in P.ESTIMATED_SOURCES


def test_a_priceless_close_records_no_exit_price(monkeypatch):
    """Declaring unmeasured must never be paired with a substituted price."""
    db = _drive(monkeypatch, _VERDICT_NO_PRICE, None)
    closes = [u for _t, u in db.trade_updates if u.get("status") == "closed"]
    assert closes and "exit_price" not in closes[0]


# --------------------------------------------------------------------------- #
# Never launder over a better stamp
# --------------------------------------------------------------------------- #

def test_an_existing_more_specific_stamp_is_never_overwritten(monkeypatch):
    """The sibling rule added to `_sweep_local_pnl_for_unpriced` the same day,
    after an unconditional overwrite stamped a projection over a broker source."""
    pre = dict(_MATCHED)
    pre["notes"] = json.dumps({"exit_price_source": "bybit_closed_pnl"})
    db = _drive(monkeypatch, _VERDICT_WITH_PRICE, None, matched=pre)
    notes = _stamped_notes(db)
    if notes is not None:
        assert notes["exit_price_source"] == "bybit_closed_pnl"


def test_the_helper_matches_the_canonical_marker():
    """The fail-safe literal must not drift from the module it mirrors."""
    assert om._prov_unmeasured_marker() == P.UNMEASURED_MARKER
