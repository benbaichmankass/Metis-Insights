"""Tests for folding the account-status ask into the prop trade-report flow.

Operator ask (2026-07-11): logging a trade should ALSO prompt for the account
balance — in the same reply — so the rule-distance guard isn't left blind while
a separate periodic ping catches up. These lock: the nudge fires on a fill when
the balance is stale/absent, stays quiet when a fresh balance is on file, and
the screenshot orchestration ingests fill + balance from one image with a single
(suppressed-when-fresh) nudge.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    monkeypatch.delenv("PROP_STATUS_REQUEST_MAX_AGE_HOURS", raising=False)
    return tmp_path


def test_open_report_nudges_when_no_balance_on_file(isolated_env: Path) -> None:
    from src.prop.telegram_report_handler import handle_command

    reply = handle_command("open ETHUSD 1812 1", default_account="breakout_1")
    assert reply is not None and reply.startswith("✅")
    assert "account balance" in reply.lower()
    assert "bal <balance> <equity>" in reply


def test_close_report_nudges(isolated_env: Path) -> None:
    from src.prop.telegram_report_handler import handle_command

    reply = handle_command("close ETHUSD 1850 +38 tp", default_account="breakout_1")
    assert reply is not None
    assert "account balance" in reply.lower()


def test_skip_report_does_not_nudge(isolated_env: Path) -> None:
    from src.prop.telegram_report_handler import handle_command

    reply = handle_command("skip ETHUSD stale", default_account="breakout_1")
    assert reply is not None and reply.startswith("✅")
    assert "account balance" not in reply.lower()


def test_fresh_balance_suppresses_nudge(isolated_env: Path) -> None:
    from src.prop.telegram_report_handler import handle_command

    # Record a balance first, then a fill — the fill ack should NOT nag.
    handle_command("bal 5116 5118", default_account="breakout_1")
    reply = handle_command("open ETHUSD 1812 1", default_account="breakout_1")
    assert reply is not None and reply.startswith("✅")
    assert "account balance" not in reply.lower()


def test_status_report_never_nudges(isolated_env: Path) -> None:
    from src.prop.telegram_report_handler import handle_command

    reply = handle_command("bal 5116 5118", default_account="breakout_1")
    assert reply is not None and reply.startswith("✅ account status")
    assert "Also send" not in reply


def test_nudge_disabled_by_env(isolated_env: Path,
                               monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop.telegram_report_handler import handle_command

    monkeypatch.setenv("PROP_STATUS_REQUEST_MAX_AGE_HOURS", "0")
    reply = handle_command("open ETHUSD 1812 1", default_account="breakout_1")
    assert reply is not None and reply.startswith("✅")
    assert "account balance" not in reply.lower()


def test_account_status_nudge_helper(isolated_env: Path) -> None:
    from src.prop.telegram_report_handler import account_status_nudge

    assert account_status_nudge("breakout_1") is not None  # nothing on file
    assert account_status_nudge(None) is None


def test_handle_screenshot_ingests_fill_and_balance(
        isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A portfolio screen showing both a position and the balance logs both, and
    the same-image balance suppresses the trade ack's nudge."""
    from src.prop import prop_journal, screenshot_parse, telegram_report_handler

    def fake_parse(image_bytes, media_type, *, default_account=None):
        return [
            {"account_id": default_account, "symbol": "ETHUSD",
             "direction": "buy", "status": "filled", "entry_price": 1812.04, "qty": 1},
            {"kind": "account_status", "account_id": default_account,
             "balance": 5116, "equity": 5118},
        ]

    monkeypatch.setattr(screenshot_parse, "parse_screenshot", fake_parse)
    reply = telegram_report_handler.handle_screenshot(
        b"fake-image", "image/jpeg", default_account="breakout_1")

    assert "recorded" in reply.lower()
    assert "account status recorded" in reply.lower()
    # balance from the SAME shot is on file → no trailing "also send balance" nag
    assert "Also send" not in reply

    assert len(prop_journal.list_fills()) == 1
    assert prop_journal.latest_account_status("breakout_1")["balance"] == pytest.approx(5116.0)


def test_handle_screenshot_fill_only_nudges(
        isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop import screenshot_parse, telegram_report_handler

    def fake_parse(image_bytes, media_type, *, default_account=None):
        return [{"account_id": default_account, "symbol": "ETHUSD",
                 "direction": "buy", "status": "filled", "entry_price": 1812.04}]

    monkeypatch.setattr(screenshot_parse, "parse_screenshot", fake_parse)
    reply = telegram_report_handler.handle_screenshot(
        b"fake-image", "image/jpeg", default_account="breakout_1")
    assert "account balance" in reply.lower()  # no balance in the shot → nudge


def test_handle_screenshot_empty_extraction(
        isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop import screenshot_parse, telegram_report_handler

    monkeypatch.setattr(screenshot_parse, "parse_screenshot",
                        lambda *a, **k: [])
    reply = telegram_report_handler.handle_screenshot(
        b"fake-image", "image/jpeg", default_account="breakout_1")
    assert "couldn't find" in reply.lower()


def test_handle_screenshot_parse_error(
        isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop import screenshot_parse, telegram_report_handler

    def boom(*a, **k):
        raise screenshot_parse.ScreenshotParseError("no API key")

    monkeypatch.setattr(screenshot_parse, "parse_screenshot", boom)
    reply = telegram_report_handler.handle_screenshot(
        b"img", "image/jpeg", default_account="breakout_1")
    assert "no API key" in reply
