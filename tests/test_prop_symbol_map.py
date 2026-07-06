"""Tests for the prop venue↔bot symbol mapping (src/prop/symbol_map.py) and the
inbound-report normalisation that uses it.

The map's source of truth is the live ``config/prop_rulesets/breakout_routing.yaml``
``symbols[<bot>].dxtrade_symbol`` block, so these assert the operator-confirmed
ETHUSDT→ETHUSD / SOLUSDT→SOLUSD wiring round-trips both directions and that an
unmapped symbol passes through unchanged (fail-open).
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ── symbol_map: both directions ───────────────────────────────────────

def test_outbound_bot_to_venue() -> None:
    from src.prop.symbol_map import to_venue_symbol

    assert to_venue_symbol("ETHUSDT") == "ETHUSD"
    assert to_venue_symbol("SOLUSDT") == "SOLUSD"
    assert to_venue_symbol("BTCUSDT") == "BTCUSD"


def test_inbound_venue_to_bot() -> None:
    from src.prop.symbol_map import to_bot_symbol

    assert to_bot_symbol("ETHUSD") == "ETHUSDT"
    assert to_bot_symbol("SOLUSD") == "SOLUSDT"
    # Case-insensitive on the operator's typed form.
    assert to_bot_symbol("ethusd") == "ETHUSDT"


def test_canonical_symbol_passthrough() -> None:
    from src.prop.symbol_map import to_bot_symbol, to_venue_symbol

    # An already-canonical bot symbol must NOT be re-mapped on the inbound side.
    assert to_bot_symbol("ETHUSDT") == "ETHUSDT"
    # An unmapped instrument passes through unchanged both ways (fail-open).
    assert to_venue_symbol("XRPUSDT") == "XRPUSDT"
    assert to_bot_symbol("XRPUSDT") == "XRPUSDT"


def test_empty_and_none_inputs() -> None:
    from src.prop.symbol_map import to_bot_symbol, to_venue_symbol

    assert to_venue_symbol(None) is None
    assert to_bot_symbol(None) is None
    assert to_venue_symbol("") == ""


# ── ingest normalises an inbound venue symbol to canonical ────────────

@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db))
    return db


@pytest.fixture
def no_notify(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []
    from src.prop import breakout_notify

    def _fake(fill, **kwargs):
        calls.append(fill)
        return {"push": True, "telegram": True}

    monkeypatch.setattr(breakout_notify, "emit_prop_fill", _fake)
    return calls


def test_ingest_venue_symbol_links_canonical_ticket(
    isolated_db: Path, no_notify: list
) -> None:
    """A report that comes back with the VENUE symbol (ETHUSD) must still link to
    the ticket the bot emitted under the canonical symbol (ETHUSDT)."""
    from src.prop import prop_journal, prop_report

    prop_journal.record_ticket({
        "ticket_id": "prop-manual-eth1", "account_id": "breakout_1",
        "symbol": "ETHUSDT", "direction": "short", "entry": 3000.0,
    })
    # A close links only to a ticket that represents a position (never a
    # never-placed `emitted` signal — BL-20260706-PROP-CLOSE-MISLINK); advance
    # it to `filled` first. This test's point is the ETHUSD→ETHUSDT normalise.
    prop_journal.set_ticket_status("prop-manual-eth1", "filled")
    out = prop_report.ingest_report({
        "account_id": "breakout_1", "symbol": "ETHUSD", "direction": "short",
        "status": "closed", "exit_price": 2950.0, "pnl": 80.0, "reason": "tp",
    })
    assert out["ok"] and out["kind"] == "fill"
    # Normalised symbol let reconciliation find the canonical ticket.
    assert out["ticket_id"] == "prop-manual-eth1"
    # The journal row is stored under the canonical symbol, not the venue one.
    fills = prop_journal.list_fills(account_id="breakout_1")
    assert fills[0]["symbol"] == "ETHUSDT"
    # …but the operator's original wording is preserved verbatim in raw.
    import json

    assert json.loads(fills[0]["raw"])["symbol"] == "ETHUSD"
    assert prop_journal.list_tickets()[0]["status"] == "closed"
