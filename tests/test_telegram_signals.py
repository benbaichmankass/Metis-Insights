"""Regression tests for the /signals Telegram command.

Pre-fix bug: ``_format_signal_row`` wrapped pipeline statuses / reasons in
Markdown ``_..._`` italic and ``*...*`` bold delimiters. Real-world reasons
(``no_signal``, ``halt_flag_active``, ``failed_validation``) contain
underscores, so Telegram's legacy Markdown parser saw an unbalanced italic
sequence and rejected the whole reply with ``Bad Request: Can't parse
entities``. The exception was thrown from ``reply_text`` and the user saw
nothing — matching the operator's report that ``/signals returns nothing
useful`` even while the audit log on disk was growing.

These tests pin the fix:

* happy-path — a record with an underscore-laden reason renders without any
  Markdown delimiter that could trip the legacy parser, and ``reply_text``
  is invoked **without** ``parse_mode``;
* empty-log path — the bot still responds with the operator-friendly
  ``📭 No signals logged yet`` message and never sets ``parse_mode``;
* path-resolution — ``SIGNAL_AUDIT_PATH`` honours the ``$SIGNAL_AUDIT_PATH``
  env override so the audit log can live outside the repo on the VM.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Bot import pulls in telegram + pandas + dotenv via signal_notifications.
# Stub the optional deps so the test runs in the lean pytest venv.
# ---------------------------------------------------------------------------
for _mod in ("telegram", "telegram.ext", "dotenv", "requests",
             "pandas", "matplotlib", "matplotlib.pyplot"):
    sys.modules.setdefault(_mod, MagicMock())

_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.BotCommand = MagicMock
# InlineKeyboardButton/Markup are *called* at module import time
# (S-014.5 added a top-level `_VM_WRITE_BUTTONS = InlineKeyboardMarkup(...)`).
# Passing a list as the first positional arg to a bare ``MagicMock`` class
# crashes ``_mock_set_magics`` (lists are unhashable). Use a callable
# factory that returns a fresh mock for each call instead.
_tg.InlineKeyboardButton = lambda *a, **kw: MagicMock()
_tg.InlineKeyboardMarkup = lambda *a, **kw: MagicMock()

_tgext = sys.modules["telegram.ext"]
_tgext.Application = MagicMock
_tgext.CommandHandler = MagicMock
_tgext.CallbackQueryHandler = MagicMock
_ContextTypes = MagicMock()
_ContextTypes.DEFAULT_TYPE = MagicMock
_tgext.ContextTypes = _ContextTypes


def _drive(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_format_signal_row_no_markdown_delimiters():
    """Output must not contain bare ``_``/``*`` Markdown delimiters around
    user-controlled fields. The first character is the status emoji; the
    remainder must be plain text."""
    from src.bot import telegram_query_bot as bot

    rec = {
        "strategy": "vwap",
        "symbol": "BTCUSDT",
        "side": "none",
        "qty": 0.0,
        "status": "skipped",
        "reason": "no_signal",
        "logged_at_utc": "2026-04-30T05:00:00Z",
    }
    text = bot._format_signal_row(rec)
    assert "no_signal" in text
    assert "skipped" in text
    # No Markdown wrappers — the legacy parser would choke on `_no_signal_`.
    assert "_no_signal_" not in text
    assert "*skipped*" not in text
    assert "`" not in text


def test_cmd_signals_happy_path_underscore_reason_does_not_use_markdown(
    tmp_path, monkeypatch,
):
    """The underscore-laden ``no_signal`` reason — the actual ``/signals``
    crash on the VM — must round-trip through ``reply_text`` cleanly."""
    from src.bot import telegram_query_bot as bot

    path = tmp_path / "signal_audit.jsonl"
    path.write_text(
        json.dumps({
            "strategy": "vwap", "symbol": "BTCUSDT", "side": "none",
            "qty": 0.0, "status": "skipped", "reason": "no_signal",
            "logged_at_utc": "2026-04-30T05:00:00Z",
        }) + "\n"
    )
    monkeypatch.setattr(bot, "SIGNAL_AUDIT_PATH", str(path))
    monkeypatch.setattr(bot, "is_authorised", lambda u: True)

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = []

    _drive(bot.cmd_signals(update, context))

    call = update.message.reply_text.await_args
    sent_text = call.args[0]
    assert "no_signal" in sent_text
    assert "skipped" in sent_text
    assert "vwap" in sent_text
    # parse_mode must NOT be set — that was the underlying mismatch.
    assert call.kwargs.get("parse_mode") is None


def test_cmd_signals_empty_log_response_is_plaintext(tmp_path, monkeypatch):
    """Empty-log path must also avoid Markdown so backtick-wrapped paths
    can't accidentally trigger entity parsing."""
    from src.bot import telegram_query_bot as bot

    monkeypatch.setattr(
        bot, "SIGNAL_AUDIT_PATH", str(tmp_path / "missing.jsonl")
    )
    monkeypatch.setattr(bot, "is_authorised", lambda u: True)

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = []

    _drive(bot.cmd_signals(update, context))

    call = update.message.reply_text.await_args
    sent_text = call.args[0]
    assert "No signals logged" in sent_text
    assert call.kwargs.get("parse_mode") is None


def test_signal_audit_path_env_override(tmp_path, monkeypatch):
    """``$SIGNAL_AUDIT_PATH`` must take precedence so the operator can pin
    the audit log to a different location (e.g. ``/var/log/...``) on the
    VM if writers and readers ever drift."""
    override = tmp_path / "custom_audit.jsonl"
    override.write_text("{}\n")
    monkeypatch.setenv("SIGNAL_AUDIT_PATH", str(override))

    from src.bot import telegram_query_bot as bot
    importlib.reload(bot)
    try:
        assert bot.SIGNAL_AUDIT_PATH == str(override)
    finally:
        monkeypatch.delenv("SIGNAL_AUDIT_PATH", raising=False)
        importlib.reload(bot)
