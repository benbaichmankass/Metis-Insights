"""BL-20260706-PROP-CLOSE-MISLINK — a prop CLOSE must link to the ticket that
represents the real POSITION (filled), never a newer never-placed `emitted`
signal.

Reproduces the ETH incident (2026-07-06): the operator's ETHUSD close (no
explicit ticket_id) was routed by ``match_fill_to_ticket`` to the *newest*
open-status ticket — a stale `emitted` signal (prop-manual-849ece101a3c, 14:01)
— instead of the actually-`filled` position ticket (prop-manual-5bc393741ec4,
08:00). That marked a phantom signal "closed" and left the real position open
("still open" + a double-logged close). The fix: a `closed` report links only to
a position-bearing ticket (filled > awaiting_report > placed), newest within
each; never to `emitted`/`expiry_prompted`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.prop import prop_journal, prop_reconcile


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db))
    return db


def _ticket(tid: str, status: str, symbol="ETHUSDT", direction="long"):
    prop_journal.record_ticket({
        "ticket_id": tid, "account_id": "breakout_1",
        "symbol": symbol, "direction": direction, "status": "emitted",
    })
    if status != "emitted":
        prop_journal.set_ticket_status(tid, status)


def _close(symbol="ETHUSDT", direction="long"):
    # symbol is already the canonical bot symbol here: ingest_report normalises
    # ETHUSD→ETHUSDT BEFORE calling match_fill_to_ticket, which normalises only
    # direction (buy==long) internally.
    return {
        "account_id": "breakout_1", "symbol": symbol, "direction": direction,
        "status": "closed", "entry_price": 1767.71, "exit_price": 1732.0,
        "qty": 1.9, "pnl": -71.66,
    }


class TestCloseMatchesPositionNotSignal:
    def test_close_links_filled_not_newer_emitted(self, isolated_db):
        # Older FILLED position, then a NEWER never-placed EMITTED signal —
        # the exact ETH shape. A close must hit the filled one.
        _ticket("tk-filled-old", "filled")     # inserted first → older
        _ticket("tk-emitted-new", "emitted")   # inserted second → newer
        assert prop_reconcile.match_fill_to_ticket(_close()) == "tk-filled-old"

    def test_close_never_links_emitted_only(self, isolated_db):
        # No position exists (only never-placed signals) → the close must NOT
        # corrupt a signal ticket; it returns None (journaled unlinked).
        _ticket("tk-emitted-1", "emitted")
        _ticket("tk-emitted-2", "emitted")
        assert prop_reconcile.match_fill_to_ticket(_close()) is None

    def test_close_never_links_expiry_prompted(self, isolated_db):
        _ticket("tk-prompted", "expiry_prompted")
        assert prop_reconcile.match_fill_to_ticket(_close()) is None

    def test_close_prefers_filled_over_placed(self, isolated_db):
        _ticket("tk-placed", "placed")
        _ticket("tk-filled", "filled")
        assert prop_reconcile.match_fill_to_ticket(_close()) == "tk-filled"

    def test_close_falls_back_to_placed_when_no_filled(self, isolated_db):
        # A working `placed` limit that filled-then-closed with no interim
        # `filled` report still links (a placed order may carry a position).
        _ticket("tk-placed", "placed")
        _ticket("tk-emitted", "emitted")  # newer, but a close ignores emitted
        assert prop_reconcile.match_fill_to_ticket(_close()) == "tk-placed"

    def test_close_respects_direction(self, isolated_db):
        _ticket("tk-short-filled", "filled", direction="short")
        # A long close must not link a short position.
        assert prop_reconcile.match_fill_to_ticket(_close(direction="long")) is None

    def test_explicit_ticket_id_still_wins(self, isolated_db):
        _ticket("tk-filled", "filled")
        fill = _close()
        fill["ticket_id"] = "prop-manual-explicit"
        assert prop_reconcile.match_fill_to_ticket(fill) == "prop-manual-explicit"


class TestNonCloseUnchanged:
    def test_open_links_newest_open_ticket(self, isolated_db):
        # A non-close (fill) report keeps prior behaviour: newest open ticket
        # (the just-placed order that filled), even if an older filled exists.
        _ticket("tk-old-emitted", "emitted")
        _ticket("tk-new-emitted", "emitted")
        fill = {"account_id": "breakout_1", "symbol": "ETHUSDT",
                "direction": "long", "status": "filled", "qty": 1.9}
        assert prop_reconcile.match_fill_to_ticket(fill) == "tk-new-emitted"

    def test_open_buy_synonym_matches_long_ticket(self, isolated_db):
        # direction 'buy' normalises to 'long' inside the matcher (symbol is
        # already canonical — ingest normalises ETHUSD→ETHUSDT upstream).
        _ticket("tk-long", "emitted")
        fill = {"account_id": "breakout_1", "symbol": "ETHUSDT",
                "direction": "buy", "status": "filled", "qty": 1.9}
        assert prop_reconcile.match_fill_to_ticket(fill) == "tk-long"
