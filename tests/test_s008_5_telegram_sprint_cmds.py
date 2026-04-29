"""S-008.5 PR #2: Telegram sprint command tests.

Tests cmd_sprintlet_status, cmd_sprintlet_complete, and cmd_checkpoint
using the same offline async pattern as test_s008_telegram_rewired.py.
Heavy deps (telegram, pybit) are stubbed at sys.modules level.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub heavy deps before any src import
# ---------------------------------------------------------------------------
for _mod in (
    "telegram",
    "telegram.ext",
    "dotenv",
    "requests",
    "pybit",
    "pybit.unified_trading",
    "src.runtime.signal_notifications",
):
    sys.modules.setdefault(_mod, MagicMock())

sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"].dotenv_values = lambda *a, **kw: {}

_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.BotCommand = MagicMock
_tg.InlineKeyboardButton = MagicMock
_tg.InlineKeyboardMarkup = MagicMock
_tg_ext = sys.modules["telegram.ext"]
_tg_ext.Application = MagicMock
_tg_ext.CommandHandler = MagicMock
_tg_ext.CallbackQueryHandler = MagicMock
_tg_ext.ContextTypes = MagicMock()
_tg_ext.ContextTypes.DEFAULT_TYPE = object

import src.bot.telegram_query_bot as bot  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_update(chat_id: str = "99999") -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.reply_text = AsyncMock()
    return update


def _make_context(args=None) -> MagicMock:
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


# ---------------------------------------------------------------------------
# cmd_sprintlet_status
# ---------------------------------------------------------------------------


class TestCmdSprintletStatus:
    def setup_method(self):
        bot.TELEGRAM_CHAT_ID = "99999"

    def test_replies_with_milestone(self):
        update = _make_update()
        _run(bot.cmd_sprintlet_status(update, _make_context(["PR1", "merged"])))
        text = update.message.reply_text.call_args[0][0]
        assert "PR1" in text
        assert "merged" in text

    def test_reply_contains_sprintlet_prefix(self):
        update = _make_update()
        _run(bot.cmd_sprintlet_status(update, _make_context(["PR2"])))
        text = update.message.reply_text.call_args[0][0]
        assert "✅" in text
        assert "S-008.5" in text

    def test_no_args_uses_update_fallback(self):
        update = _make_update()
        _run(bot.cmd_sprintlet_status(update, _make_context([])))
        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "S-008.5" in text

    def test_not_authorised_returns_early(self):
        update = _make_update(chat_id="wrong")
        _run(bot.cmd_sprintlet_status(update, _make_context(["PR1"])))
        update.message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# cmd_sprintlet_complete
# ---------------------------------------------------------------------------


class TestCmdSprintletComplete:
    def setup_method(self):
        bot.TELEGRAM_CHAT_ID = "99999"

    def test_replies_complete_message(self):
        update = _make_update()
        _run(bot.cmd_sprintlet_complete(update, _make_context()))
        text = update.message.reply_text.call_args[0][0]
        assert "COMPLETE" in text
        assert "S-008.5" in text

    def test_reply_contains_checkpoint_reference(self):
        update = _make_update()
        _run(bot.cmd_sprintlet_complete(update, _make_context()))
        text = update.message.reply_text.call_args[0][0]
        assert "CP-2026-04-29-58" in text

    def test_reply_contains_s009_reference(self):
        update = _make_update()
        _run(bot.cmd_sprintlet_complete(update, _make_context()))
        text = update.message.reply_text.call_args[0][0]
        assert "S-009" in text

    def test_not_authorised_returns_early(self):
        update = _make_update(chat_id="wrong")
        _run(bot.cmd_sprintlet_complete(update, _make_context()))
        update.message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# cmd_checkpoint
# ---------------------------------------------------------------------------


class TestCmdCheckpoint:
    def setup_method(self):
        bot.TELEGRAM_CHAT_ID = "99999"

    def test_reads_latest_checkpoint(self, tmp_path, monkeypatch):
        log = tmp_path / "CHECKPOINT_LOG.md"
        log.write_text(
            "# Log\n\n## CP-2026-04-29-58 — S-008 complete\n\n## CP-2026-04-29-57 — S-008 #127\n"
        )
        monkeypatch.setattr(bot, "REPO_ROOT", str(tmp_path))
        # Create the expected directory structure
        (tmp_path / "docs" / "claude" / "checkpoints").mkdir(parents=True)
        (tmp_path / "docs" / "claude" / "checkpoints" / "CHECKPOINT_LOG.md").write_text(
            "# Log\n\n## CP-2026-04-29-58 — S-008 complete\n\n## CP-2026-04-29-57 — S-008 #127\n"
        )
        update = _make_update()
        _run(bot.cmd_checkpoint(update, _make_context()))
        text = update.message.reply_text.call_args[0][0]
        assert "CP-2026-04-29-58" in text

    def test_missing_log_file_replies_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bot, "REPO_ROOT", str(tmp_path))
        (tmp_path / "docs" / "claude" / "checkpoints").mkdir(parents=True)
        # Do NOT create the file
        update = _make_update()
        _run(bot.cmd_checkpoint(update, _make_context()))
        text = update.message.reply_text.call_args[0][0]
        assert "⚠️" in text or "Could not" in text.lower()

    def test_not_authorised_returns_early(self):
        update = _make_update(chat_id="wrong")
        _run(bot.cmd_checkpoint(update, _make_context()))
        update.message.reply_text.assert_not_called()
