"""Tests for the JSON report-back path of the prop Telegram handler.

The rendered prop ticket instructs the executor to reply with a JSON block
(``{"account_id":…,"status":"open",…}``), but the bot's free-text handler
originally only understood the one-line command grammar — so a pasted JSON
report was rejected as "Not a prop command". These lock the JSON path:
handle_json_report ingests it (the same chokepoint as the REST endpoint), and
handle_command tries JSON first then falls back to the command grammar.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    return tmp_path


_OPEN_JSON = (
    '{"account_id":"breakout_1","symbol":"ETHUSDT","direction":"short",'
    '"status":"open","entry_price":1619.99,"qty":0.73,'
    '"ticket_id":"prop-manual-defdeaa5396d"}'
)


def test_json_open_report_is_ingested(isolated_env: Path) -> None:
    from src.prop import prop_journal
    from src.prop.telegram_report_handler import handle_command

    reply = handle_command(_OPEN_JSON, default_account="breakout_1")
    assert reply is not None and reply.startswith("✅")
    assert "ETHUSDT" in reply

    fills = prop_journal.list_fills()
    assert len(fills) == 1
    assert fills[0]["symbol"] == "ETHUSDT"
    assert fills[0]["status"] == "open"
    assert fills[0]["entry_price"] == pytest.approx(1619.99)


def test_json_with_code_fence_is_parsed(isolated_env: Path) -> None:
    from src.prop import prop_journal
    from src.prop.telegram_report_handler import handle_command

    fenced = f"```json\n{_OPEN_JSON}\n```"
    reply = handle_command(fenced, default_account="breakout_1")
    assert reply is not None and reply.startswith("✅")
    assert len(prop_journal.list_fills()) == 1


def test_json_missing_account_uses_default(isolated_env: Path) -> None:
    from src.prop import prop_journal
    from src.prop.telegram_report_handler import handle_command

    no_acct = (
        '{"symbol":"ETHUSDT","direction":"short","status":"open",'
        '"entry_price":1619.99,"qty":0.73}'
    )
    reply = handle_command(no_acct, default_account="breakout_1")
    assert reply is not None and reply.startswith("✅")
    assert prop_journal.list_fills()[0]["account_id"] == "breakout_1"


def test_malformed_json_returns_hint(isolated_env: Path) -> None:
    from src.prop.telegram_report_handler import handle_command

    reply = handle_command('{"account_id":"breakout_1", "status": ',
                           default_account="breakout_1")
    assert reply is not None and reply.startswith("⚠")
    assert "JSON" in reply


def test_non_json_falls_through_to_command_grammar(isolated_env: Path) -> None:
    from src.prop.telegram_report_handler import handle_command, handle_json_report

    # handle_json_report ignores a non-JSON line entirely…
    assert handle_json_report("hello there", default_account="breakout_1") is None
    # …and a non-command line still returns None from handle_command (caller
    # then shows its menu hint), i.e. JSON handling didn't swallow it.
    assert handle_command("hello there", default_account="breakout_1") is None


def test_command_grammar_still_works(isolated_env: Path) -> None:
    from src.prop import prop_journal
    from src.prop.telegram_report_handler import handle_command

    reply = handle_command("open ETHUSD 1620 0.73", default_account="breakout_1")
    assert reply is not None and reply.startswith("✅")
    assert prop_journal.list_fills()[0]["symbol"] == "ETHUSDT"  # ETHUSD→ETHUSDT
