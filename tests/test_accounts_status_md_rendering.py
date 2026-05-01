"""Regression test for /accounts_status Markdown rendering.

Telegram's "Markdown" parse mode treats `_` as an italic marker and
strips it from rendered output. Env-var names like BYBIT_API_KEY_1
embedded in the live_balance_error string ended up displayed as
BYBITAPIKEY1, which made the diagnostic useless.

The fix escapes underscores (and other Markdown specials) in dynamic
content before passing it to reply_text(parse_mode="Markdown").
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch


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

from src.bot.telegram_query_bot import cmd_accounts_status


def _make_update():
    update = MagicMock()
    update.effective_chat.id = "12345"
    update.message.reply_text = AsyncMock()
    return update


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_underscores_in_error_string_escaped_so_telegram_renders_them():
    """Diagnostic for missing env vars contains BYBIT_API_KEY_1 etc.
    Without escaping, Telegram would render this as BYBITAPIKEY1
    (italic markers stripped). The reply must contain the escaped
    form `BYBIT\\_API\\_KEY\\_1` so Telegram renders the underscores
    literally.
    """
    update = _make_update()
    fake_coord = MagicMock()
    fake_coord.accounts_status.return_value = [
        {
            "name": "bybit_1",
            "exchange": "bybit",
            "account_type": "regular",
            "halted": False,
            "daily_pnl": 0.0,
            "max_daily_loss_usd": 100.0,
            "max_pos_size_usd": 500.0,
            "open_positions": 0,
            "live_balance_usdt": None,
            "live_balance_error": (
                "missing env vars: BYBIT_API_KEY_1, BYBIT_API_SECRET_1"
            ),
        }
    ]
    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True), \
            patch("src.bot.telegram_query_bot.get_coordinator",
                  return_value=fake_coord):
        _run(cmd_accounts_status(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args.args[0]
    # Underscores in the dynamic content must be escaped so Telegram's
    # Markdown parser doesn't eat them as italic markers.
    assert "BYBIT\\_API\\_KEY\\_1" in msg
    assert "BYBIT\\_API\\_SECRET\\_1" in msg
    # Account name with underscore must also be escaped.
    assert "bybit\\_1" in msg


def test_account_with_no_special_chars_unaffected():
    """Account names + errors without underscores render identically."""
    update = _make_update()
    fake_coord = MagicMock()
    fake_coord.accounts_status.return_value = [
        {
            "name": "main",
            "exchange": "bybit",
            "account_type": "regular",
            "halted": False,
            "daily_pnl": 0.0,
            "max_daily_loss_usd": 100.0,
            "max_pos_size_usd": 500.0,
            "open_positions": 0,
            "live_balance_usdt": 1234.56,
            "live_balance_error": None,
        }
    ]
    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True), \
            patch("src.bot.telegram_query_bot.get_coordinator",
                  return_value=fake_coord):
        _run(cmd_accounts_status(update, MagicMock()))

    msg = update.message.reply_text.await_args.args[0]
    assert "*main*" in msg  # bold name still works
    assert "$1,234.56" in msg


def test_retcode_error_text_underscores_escaped():
    """retCode/retMsg failures don't typically have underscores in the
    message itself, but the operator's filename / path might. Make sure
    no accidental italic spans are introduced."""
    update = _make_update()
    fake_coord = MagicMock()
    fake_coord.accounts_status.return_value = [
        {
            "name": "bybit_1",
            "exchange": "bybit",
            "account_type": "regular",
            "halted": False,
            "daily_pnl": 0.0,
            "max_daily_loss_usd": 100.0,
            "max_pos_size_usd": 500.0,
            "open_positions": 0,
            "live_balance_usdt": None,
            "live_balance_error": "Bybit error retCode=10003: API key is invalid.",
        }
    ]
    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True), \
            patch("src.bot.telegram_query_bot.get_coordinator",
                  return_value=fake_coord):
        _run(cmd_accounts_status(update, MagicMock()))

    msg = update.message.reply_text.await_args.args[0]
    assert "10003" in msg
    assert "API key is invalid" in msg
