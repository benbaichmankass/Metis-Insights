"""MI-204 — the hedge-book flat read, at the ORDER PATH rather than the pure fn.

``tests/test_bybit_position_book.py`` pins the pure selection policy and
reproduces the defect against a transcription of the old logic. This file pins
the same three fixes where they actually run:

* **D1** ``_bybit_position_protection`` — the live book is graded, an EMPTY
  response REFUSES instead of grading flat, and two live books REFUSE instead of
  picking one.
* **D2** ``_recently_closed_adopted_orphan`` — ``netting_attributed`` now
  suppresses the re-adopt flap, exercised against the REAL journal schema.
* **D3** ``_netting_soak_row`` — the row carries ``position_idx`` and
  ``exchange_read_source``, so ``exchange_qty: 0.0`` stops being three facts
  wearing one value.

Operator-approved Tier-2, 2026-09-08. Evidence:
``docs/claude/work/BYBIT-HEDGE-BOOK-FLAT-READ-2026-09-08.md``.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import order_monitor as om


class _MultiBookBybit:
    """pybit-shaped stub that can return MORE THAN ONE position row.

    The existing `_FakeBybit` in tests/test_bybit_naked_rearm.py returns
    ``[pos] if pos else []`` — one row — which is precisely why a hedge symbol's
    two-row payload had no test coverage before this file.
    """

    def __init__(self, rows, stop_legs=None):
        self._rows = rows
        self._stop_legs = stop_legs or []

    def get_positions(self, category=None, symbol=None):
        return {"retCode": 0, "result": {"list": list(self._rows)}}

    def get_open_orders(self, category=None, symbol=None, orderFilter=None):
        return {"retCode": 0, "result": {"list": list(self._stop_legs)}}


def _book(idx, side, size, stop_loss=""):
    return {"symbol": "ETHUSDT", "positionIdx": idx, "side": side,
            "size": size, "stopLoss": stop_loss, "tpSlMode": "Partial"}


# The measured incident: live long 26.05 on positionIdx=1, empty short sibling
# listed FIRST.
LIVE_LONG = _book(1, "Buy", "26.05")
EMPTY_SHORT = _book(2, "Sell", "0")


# ---------------------------------------------------------------- D1
def test_D1_live_hedge_book_is_graded_even_when_the_empty_sibling_is_first():
    """THE regression this change exists to prevent.

    Pre-fix this returned the `_flat` dict (size 0.0, side ""), the netting
    reconciler computed `backed = 0.0`, and it closed the whole live row —
    trade 5568, +$63.8225 fabricated against a position the venue still held.
    """
    c = _MultiBookBybit(
        [EMPTY_SHORT, LIVE_LONG],
        stop_legs=[{"stopOrderType": "PartialStopLoss", "qty": "26.05",
                    "orderId": "7b5af72f"}],
    )
    st = om._bybit_position_protection(c, "linear", "ETHUSDT")

    assert st is not None
    assert st["size"] == 26.05          # NOT 0.0
    assert st["side"] == "long"         # NOT ""
    assert st["source"] == "partial_sl_legs"
    assert st["position_idx"] == 1
    # 5568's own tracked SL leg was resting throughout — the attribution basis
    # `leg_gone` could only have fired because sl_leg_ids came back EMPTY.
    assert st["sl_leg_ids"] == {"7b5af72f"}


def test_D1_row_order_does_not_change_the_verdict():
    a = om._bybit_position_protection(
        _MultiBookBybit([EMPTY_SHORT, LIVE_LONG]), "linear", "ETHUSDT")
    b = om._bybit_position_protection(
        _MultiBookBybit([LIVE_LONG, EMPTY_SHORT]), "linear", "ETHUSDT")
    assert a["size"] == b["size"] == 26.05
    assert a["position_idx"] == b["position_idx"] == 1


def test_D1_empty_response_REFUSES_and_is_not_graded_flat():
    st = om._bybit_position_protection(_MultiBookBybit([]), "linear", "ETHUSDT")
    # None => the caller SKIPS. Pre-fix this was the `_flat` dict, which the
    # netting reconciler consumed as a positive finding of flatness.
    assert st is None


def test_D1_two_live_books_REFUSE_rather_than_pick_one():
    both = [_book(1, "Buy", "26.05"), _book(2, "Sell", "10.0")]
    assert om._bybit_position_protection(
        _MultiBookBybit(both), "linear", "ETHUSDT") is None
    assert om._bybit_position_protection(
        _MultiBookBybit(list(reversed(both))), "linear", "ETHUSDT") is None


def test_D1_refusal_is_LOUD(caplog):
    # A silent refusal would convert a false-close defect into an invisible
    # protection gap: both callers skip on None, and in the naked sweep that
    # means no protective re-arm.
    with caplog.at_level("WARNING"):
        om._bybit_position_protection(_MultiBookBybit([]), "linear", "ETHUSDT")
    assert any("REFUSED" in r.message or "REFUSED" in r.getMessage()
               for r in caplog.records)


def test_D1_genuinely_flat_symbol_still_grades_flat():
    # The venue ENUMERATED both books and neither holds anything. This must stay
    # a positive answer — over-refusing would strand the netting reconciler on
    # every flat symbol.
    st = om._bybit_position_protection(
        _MultiBookBybit([EMPTY_SHORT, _book(1, "Buy", "0")]), "linear", "ETHUSDT")
    assert st is not None
    assert st["size"] == 0.0 and st["source"] == "flat"
    assert st["position_idx"] is None     # no book named — never one-way's 0


def test_D1_one_way_single_row_is_unaffected():
    # The whole pre-hedge world. A regression here would be the change breaking
    # every non-hedge symbol to fix the hedge ones.
    c = _MultiBookBybit([_book(0, "Buy", "157.7", stop_loss="1.085")])
    st = om._bybit_position_protection(c, "linear", "ETHUSDT")
    assert st["size"] == 157.7 and st["covered_qty"] == 157.7
    assert st["source"] == "full_position_stop"
    assert st["position_idx"] == 0        # measured one-way, not None


def test_D1_read_failure_still_returns_None():
    class _Boom:
        def get_positions(self, **kw):
            raise RuntimeError("boom")
    assert om._bybit_position_protection(_Boom(), "linear", "ETHUSDT") is None


def test_D1_every_return_shape_carries_position_idx():
    shapes = [
        _MultiBookBybit([_book(0, "Buy", "1", stop_loss="9")]),   # full stop
        _MultiBookBybit([_book(1, "Buy", "1")]),                  # partial legs
        _MultiBookBybit([_book(1, "Buy", "0")]),                  # flat
    ]
    for c in shapes:
        st = om._bybit_position_protection(c, "linear", "ETHUSDT")
        assert st is not None and "position_idx" in st


# ---------------------------------------------------------------- D2
class _JournalDB:
    def __init__(self, path):
        self.path = str(path)
        from src.units.db.database import Database
        Database(self.path)          # the REAL schema, not a hand-rolled one

    def connect(self):
        return sqlite3.connect(self.path)


def _seed_closed(db, *, exit_reason, setup_type="ict_scalp_eth_15m",
                 minutes_ago=3.0):
    closed = (datetime.now(timezone.utc)
              - timedelta(minutes=minutes_ago)).isoformat()
    conn = sqlite3.connect(db.path)
    conn.execute(
        "INSERT INTO trades (id,account_id,symbol,direction,position_size,"
        "entry_price,created_at,timestamp,setup_type,strategy_name,status,"
        "is_backtest,closed_at,exit_reason,pnl) "
        "VALUES (5568,'bybit_1','ETHUSDT','long',26.05,2476.6,:c,:c,:st,"
        "'ict_scalp_eth_15m','closed',0,:closed,:er,63.8225)",
        {"c": closed, "st": setup_type, "closed": closed, "er": exit_reason},
    )
    conn.commit()
    conn.close()


def _guard(db):
    return om._recently_closed_adopted_orphan(
        db, account_id="bybit_1", symbol="ETHUSDT", direction="long",
        window_seconds=1800,
    )


def test_D2_netting_attributed_close_now_suppresses_the_re_adopt(tmp_path):
    """The loop this fix breaks.

    The netting reconciler false-closed 5568, the position never left the
    venue, and the reverse reconciler re-adopted it 3–4 min later as a fresh
    `adopted_orphan` — because `netting_attributed` was not a key this guard
    matched. The window (1800s) was never the problem.
    """
    db = _JournalDB(tmp_path / "j.db")
    _seed_closed(db, exit_reason="netting_attributed")
    hit = _guard(db)
    assert hit is not None and hit["id"] == 5568


def test_D2_a_broker_confirmed_close_still_does_NOT_suppress(tmp_path):
    # The guard must not swallow a genuine flatten — that would block a
    # legitimate new position on the same symbol/direction.
    db = _JournalDB(tmp_path / "j.db")
    _seed_closed(db, exit_reason="reconciler_filled")
    assert _guard(db) is None


@pytest.mark.parametrize("reason", ["sl_cross", "tp_cross", "strategy_close"])
def test_D2_ordinary_strategy_exits_still_do_NOT_suppress(tmp_path, reason):
    db = _JournalDB(tmp_path / "j.db")
    _seed_closed(db, exit_reason=reason)
    assert _guard(db) is None


def test_D2_the_pre_existing_reasons_still_suppress(tmp_path):
    # Positive control that the allowlist was ADDED to, not replaced.
    for reason in ("exchange_flat_reconciled", "exit_coverage_no_strategy"):
        db = _JournalDB(tmp_path / f"j-{reason}.db")
        _seed_closed(db, exit_reason=reason)
        assert _guard(db) is not None, reason


def test_D2_outside_the_window_does_not_suppress(tmp_path):
    db = _JournalDB(tmp_path / "j.db")
    _seed_closed(db, exit_reason="netting_attributed", minutes_ago=45.0)
    assert _guard(db) is None


# ---------------------------------------------------------------- D3
def test_D3_soak_row_records_which_book_and_how_it_was_read(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.paths.runtime_logs_dir", lambda: tmp_path)
    om._netting_soak_row(
        account_id="bybit_1", symbol="ETHUSDT", direction="long",
        row={"id": 5568, "strategy_name": "ict_scalp_eth_15m",
             "position_size": 26.05},
        take=26.05, basis="leg_gone", mode="apply",
        journal_qty=26.05, exchange_qty=0.0,
        anchor_status="anchored", anchor_price=2479.0,
        anchored_at="2026-09-08T14:21:00Z",
        global_mode="apply", apply_scope="allowlisted",
        position_idx=1, exchange_read_source="flat",
    )
    rec = json.loads(
        (tmp_path / "netting_attribution_soak.jsonl").read_text().strip())
    # An exchange_qty of 0.0 is now gradeable: this row says the read graded the
    # symbol `flat` while naming book 1 — which is the wrong-book signature.
    assert rec["exchange_qty"] == 0.0
    assert rec["position_idx"] == 1
    assert rec["exchange_read_source"] == "flat"


def test_D3_absent_book_is_None_not_zero(tmp_path, monkeypatch):
    # `None` = no book was named. Defaulting it to 0 would read as one-way
    # netting, which is a measurement nobody took.
    monkeypatch.setattr("src.utils.paths.runtime_logs_dir", lambda: tmp_path)
    om._netting_soak_row(
        account_id="bybit_1", symbol="ETHUSDT", direction="long",
        row={"id": 1, "strategy_name": "s", "position_size": 1.0},
        take=1.0, basis="fifo", mode="annotate",
        journal_qty=1.0, exchange_qty=0.0,
        anchor_status="no_anchor", anchor_price=None, anchored_at=None,
    )
    rec = json.loads(
        (tmp_path / "netting_attribution_soak.jsonl").read_text().strip())
    assert rec["position_idx"] is None
    assert rec["exchange_read_source"] is None
