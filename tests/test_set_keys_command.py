"""Tests for the /set_keys Telegram command."""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


for _mod in (
    "telegram", "telegram.ext", "dotenv", "requests", "pandas", "numpy",
    "src.runtime.signal_notifications", "src.runtime.notify",
    "src.utils.signal_audit_logger", "src.runtime.signal_writer",
):
    sys.modules.setdefault(_mod, MagicMock())

_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.BotCommand = MagicMock
_tg.InlineKeyboardButton = lambda *a, **kw: MagicMock()
_tg.InlineKeyboardMarkup = lambda *a, **kw: MagicMock()

_tgext = sys.modules["telegram.ext"]
_tgext.Application = MagicMock
_tgext.CommandHandler = MagicMock
_tgext.CallbackQueryHandler = MagicMock
_ctx = MagicMock()
_ctx.DEFAULT_TYPE = MagicMock
_tgext.ContextTypes = _ctx

from src.bot.telegram_query_bot import cmd_set_keys, _COLAB_NOTEBOOK_URL


def _make_update(chat_id: str = "12345"):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.reply_text = AsyncMock()
    return update


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_set_keys_replies_with_colab_link():
    """Authorized caller → reply contains the Colab URL + required-secrets list."""
    update = _make_update()
    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True):
        _run(cmd_set_keys(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args.args[0]
    assert _COLAB_NOTEBOOK_URL in msg
    # Must list the required secrets so operator knows what to set up
    for required in (
        "BYBIT_API_KEY_1", "BYBIT_API_SECRET_1",
        "BYBIT_API_KEY_2", "BYBIT_API_SECRET_2",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "VM_SSH_HOST", "VM_SSH_USER",
    ):
        assert required in msg, f"missing {required} in /set_keys reply"


def test_set_keys_does_not_list_ssh_key_as_a_secret():
    """SSH key is uploaded as a file, NOT pasted into Colab Secrets.
    The reply must NOT mention VM_SSH_KEY in the secrets list (operators
    would then waste time creating a Colab Secret with that name)."""
    update = _make_update()
    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True):
        _run(cmd_set_keys(update, MagicMock()))
    msg = update.message.reply_text.await_args.args[0]
    # `VM_SSH_KEY` should not appear at all — the file path uses
    # `vm_ssh_key` (lowercase, hinting at filename convention).
    assert "VM_SSH_KEY" not in msg


def test_set_keys_mentions_ssh_key_file_upload():
    """Reply must tell the operator to drag-drop the SSH key file."""
    update = _make_update()
    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True):
        _run(cmd_set_keys(update, MagicMock()))
    msg = update.message.reply_text.await_args.args[0]
    # Should hint at the Files panel + the default filename
    assert "Files" in msg
    assert "vm_ssh_key" in msg


def test_set_keys_silent_when_unauthorised():
    """Unauthorized caller → no reply at all (matches existing pattern)."""
    update = _make_update()
    with patch("src.bot.telegram_query_bot.is_authorised", return_value=False):
        _run(cmd_set_keys(update, MagicMock()))
    update.message.reply_text.assert_not_called()


def test_set_keys_url_points_at_correct_repo_and_path():
    """URL must match the open-in-Colab format pointing at the repo's
    notebooks/operator/rotate_api_keys.ipynb."""
    assert "github/the-lizardking/ict-trading-bot" in _COLAB_NOTEBOOK_URL
    assert "notebooks/operator/rotate_api_keys.ipynb" in _COLAB_NOTEBOOK_URL
    assert _COLAB_NOTEBOOK_URL.startswith("https://colab.research.google.com/")


def test_set_keys_disables_link_preview():
    """Reply must use disable_web_page_preview=True so the message
    isn't covered by a giant Colab thumbnail."""
    update = _make_update()
    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True):
        _run(cmd_set_keys(update, MagicMock()))
    kwargs = update.message.reply_text.await_args.kwargs
    assert kwargs.get("disable_web_page_preview") is True


def test_set_keys_uses_markdown_parse_mode():
    update = _make_update()
    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True):
        _run(cmd_set_keys(update, MagicMock()))
    kwargs = update.message.reply_text.await_args.kwargs
    assert kwargs.get("parse_mode") == "Markdown"
