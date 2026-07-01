"""Tests for the inbound prop Telegram command parser + handler.

The parser (:func:`src.prop.telegram_commands.parse_prop_command`) is pure and
gets the bulk of the coverage; :func:`src.prop.telegram_report_handler.handle_command`
is exercised end-to-end against an isolated journal (no Telegram I/O) to prove a
typed command links to its ticket and writes the canonical symbol.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.prop.telegram_commands import build_report, parse_prop_command


# ── parser: close / open / skip / status ──────────────────────────────

def test_parse_close_full() -> None:
    i = parse_prop_command("close ETHUSD 2950 +80 tp")
    assert i["_action"] == "close" and i["status"] == "closed"
    assert i["symbol"] == "ETHUSD"
    assert i["exit_price"] == 2950.0 and i["pnl"] == 80.0
    assert i["reason"] == "tp"


def test_parse_close_negative_pnl_and_multiword_reason() -> None:
    i = parse_prop_command("closed SOLUSD 71.2 -15 stopped out early")
    assert i["exit_price"] == 71.2 and i["pnl"] == -15.0
    assert i["reason"] == "stopped out early"


def test_parse_close_exit_only_defaults_reason() -> None:
    i = parse_prop_command("close ETHUSD 2950")
    assert i["exit_price"] == 2950.0 and "pnl" not in i
    assert i["reason"] == "manual"


def test_parse_open() -> None:
    i = parse_prop_command("open ETHUSD 3000 0.5")
    assert i["_action"] == "open" and i["status"] == "open"
    assert i["entry_price"] == 3000.0 and i["qty"] == 0.5


def test_parse_placed_is_distinct_from_open() -> None:
    # `placed` = a limit order placed but not yet filled — its own status, NOT
    # an alias of open (the conflation the placed state fixes).
    i = parse_prop_command("placed ETHUSD 3000 0.5")
    assert i["_action"] == "placed" and i["status"] == "placed"
    assert i["entry_price"] == 3000.0 and i["qty"] == 0.5
    # 'placed' must no longer be an open alias.
    assert parse_prop_command("open ETHUSD 3000")["status"] == "open"


def test_parse_placed_needs_entry_price() -> None:
    with pytest.raises(ValueError):
        parse_prop_command("placed ETHUSD")


def test_build_report_placed_carries_placed_status() -> None:
    i = parse_prop_command("placed ETHUSD 3000 0.5")
    r = build_report(i, account_id="breakout_1", direction="long",
                     ticket_id="prop-manual-eth1")
    assert r["status"] == "placed" and r["symbol"] == "ETHUSD"
    assert r["entry_price"] == 3000.0 and r["qty"] == 0.5


def test_parse_skip_default_reason() -> None:
    i = parse_prop_command("skip ETHUSD")
    assert i["_action"] == "skip" and i["status"] == "skipped"
    assert i["reason"] == "stale/out-of-range"


def test_parse_status() -> None:
    i = parse_prop_command("bal 5040 5010 -12")
    assert i["_action"] == "status" and i["kind"] == "account_status"
    assert i["balance"] == 5040.0 and i["equity"] == 5010.0
    assert i["realized_today"] == -12.0


def test_parse_account_override() -> None:
    i = parse_prop_command("close ETHUSD 2950 +80 tp acct=breakout_2")
    assert i["account_id"] == "breakout_2"
    assert i["symbol"] == "ETHUSD" and i["reason"] == "tp"


def test_parse_dollar_and_comma_numbers() -> None:
    i = parse_prop_command("close BTCUSD $98,500 +1,200 tp")
    assert i["exit_price"] == 98500.0 and i["pnl"] == 1200.0


def test_parse_non_command_returns_none() -> None:
    assert parse_prop_command("good morning") is None
    assert parse_prop_command("") is None
    assert parse_prop_command("   ") is None


def test_parse_recognised_verb_bad_args_raises() -> None:
    with pytest.raises(ValueError):
        parse_prop_command("close")          # no symbol
    with pytest.raises(ValueError):
        parse_prop_command("close ETHUSD")   # no exit price
    with pytest.raises(ValueError):
        parse_prop_command("bal")            # no balance


# ── build_report ──────────────────────────────────────────────────────

def test_build_report_close_with_context() -> None:
    i = parse_prop_command("close ETHUSD 2950 +80 tp")
    r = build_report(i, account_id="breakout_1", direction="short",
                     ticket_id="prop-manual-eth1")
    assert r == {
        "account_id": "breakout_1", "symbol": "ETHUSD", "status": "closed",
        "direction": "short", "ticket_id": "prop-manual-eth1",
        "exit_price": 2950.0, "pnl": 80.0, "reason": "tp",
    }


def test_build_report_status() -> None:
    i = parse_prop_command("bal 5040 5010")
    r = build_report(i, account_id="breakout_1")
    assert r["kind"] == "account_status" and r["balance"] == 5040.0
    assert r["equity"] == 5010.0


# ── handler end-to-end (isolated journal, no Telegram I/O) ─────────────

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


def test_handle_close_links_open_ticket(isolated_db: Path, no_notify: list) -> None:
    from src.prop import prop_journal
    from src.prop.telegram_report_handler import handle_command

    # The bot emitted a short ETHUSDT ticket under the canonical symbol.
    prop_journal.record_ticket({
        "ticket_id": "prop-manual-eth1", "account_id": "breakout_1",
        "symbol": "ETHUSDT", "direction": "short", "entry": 3000.0,
        "status": "emitted",
    })
    # Operator types the venue symbol; handler resolves direction + ticket.
    reply = handle_command("close ETHUSD 2950 +80 tp", default_account="breakout_1")
    assert reply is not None and reply.startswith("✅")

    fills = prop_journal.list_fills(account_id="breakout_1")
    assert len(fills) == 1
    assert fills[0]["symbol"] == "ETHUSDT"        # canonicalised
    assert fills[0]["direction"] == "short"        # inherited from the ticket
    assert fills[0]["ticket_id"] == "prop-manual-eth1"
    assert prop_journal.list_tickets()[0]["status"] == "closed"


def test_handle_non_command_is_silent(isolated_db: Path) -> None:
    from src.prop.telegram_report_handler import handle_command

    assert handle_command("just chatting", default_account="breakout_1") is None


def test_handle_no_account_warns(isolated_db: Path) -> None:
    from src.prop.telegram_report_handler import handle_command

    reply = handle_command("skip ETHUSD", default_account=None)
    assert reply is not None and "No prop account" in reply


def test_handle_bad_command_returns_usage(isolated_db: Path) -> None:
    from src.prop.telegram_report_handler import handle_command

    reply = handle_command("close", default_account="breakout_1")
    assert reply is not None and "⚠" in reply


# ── menu prompt ↔ parser lock-step ────────────────────────────────────

def test_menu_prompt_examples_are_ingestible() -> None:
    """Every example line in the executor-assistant prompt must parse — so the
    prompt the operator hands the assistant can never drift from the grammar the
    parser ingests."""
    from src.prop.telegram_commands import REPORT_PROMPT

    body = REPORT_PROMPT.split("Examples:", 1)[1]
    examples = [ln.strip() for ln in body.splitlines() if ln.strip()]
    assert examples, "prompt carries no example lines"
    actions = set()
    for line in examples:
        intent = parse_prop_command(line)
        assert intent is not None, f"prompt example not recognised: {line!r}"
        actions.add(intent["_action"])
    assert actions == {"placed", "open", "close", "skip", "status"}
