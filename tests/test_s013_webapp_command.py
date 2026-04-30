"""S-013 M4 PR #2 — /webapp Telegram command."""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Stub heavy deps before importing the bot module
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
_tg.InlineKeyboardButton = MagicMock()
_tg.InlineKeyboardMarkup = MagicMock()
_tg_ext = sys.modules["telegram.ext"]
_tg_ext.Application = MagicMock
_tg_ext.CommandHandler = MagicMock
_tg_ext.CallbackQueryHandler = MagicMock
_tg_ext.ContextTypes = MagicMock()
_tg_ext.ContextTypes.DEFAULT_TYPE = object

import src.bot.telegram_query_bot as bot  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_update(chat_id: str = "99999") -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.reply_text = AsyncMock()
    return update


class TestCmdWebapp:
    def setup_method(self):
        bot.TELEGRAM_CHAT_ID = "99999"

    def test_replies_with_unconfigured_message_when_url_unset(self, monkeypatch):
        monkeypatch.delenv("WEBAPP_URL", raising=False)
        update = _make_update()
        _run(bot.cmd_webapp(update, MagicMock()))
        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "not configured" in text.lower()

    def test_replies_with_unconfigured_message_when_url_blank(self, monkeypatch):
        monkeypatch.setenv("WEBAPP_URL", "   ")  # whitespace only
        update = _make_update()
        _run(bot.cmd_webapp(update, MagicMock()))
        text = update.message.reply_text.call_args[0][0]
        assert "not configured" in text.lower()

    def test_replies_with_inline_button_when_url_set(self, monkeypatch):
        monkeypatch.setenv("WEBAPP_URL", "https://dashboard.example.com")
        # Patch bot.InlineKeyboardButton / Markup directly so we don't depend on
        # whichever telegram-stub set first wins import order in the suite.
        button_stub = MagicMock(name="InlineKeyboardButton")
        markup_stub = MagicMock(name="InlineKeyboardMarkup")
        monkeypatch.setattr(bot, "InlineKeyboardButton", button_stub)
        monkeypatch.setattr(bot, "InlineKeyboardMarkup", markup_stub)
        update = _make_update()
        _run(bot.cmd_webapp(update, MagicMock()))
        update.message.reply_text.assert_called_once()
        # Inline keyboard markup must be passed via reply_markup kwarg.
        call_kwargs = update.message.reply_text.call_args.kwargs
        assert "reply_markup" in call_kwargs
        # The inline button uses the URL we set.
        button_stub.assert_called_with(
            "🔐 Open dashboard", url="https://dashboard.example.com"
        )

    def test_not_authorised_returns_early(self, monkeypatch):
        monkeypatch.setenv("WEBAPP_URL", "https://dashboard.example.com")
        update = _make_update(chat_id="wrong")
        _run(bot.cmd_webapp(update, MagicMock()))
        update.message.reply_text.assert_not_called()
