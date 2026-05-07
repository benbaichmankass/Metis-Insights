"""Project-wide pytest fixtures + stubs for the ICT Trading Bot suite.

S-016 H5 (BUG-010 fix): centralises the optional-import stubs that
~10 test files were copy-pasting individually. The copy-paste pattern
broke when S-014.5 PR #184 added a module-level
``_VM_WRITE_BUTTONS = InlineKeyboardMarkup([[…]])`` to
``src/bot/telegram_query_bot.py`` — passing a list as the first
positional arg to a bare ``MagicMock`` class crashes
``_mock_set_magics`` because lists are unhashable.

This conftest fixes the contract in one place: ``InlineKeyboardButton``
and ``InlineKeyboardMarkup`` are stubbed as **callable factories**
that return fresh ``MagicMock`` instances, so module-level constructor
calls work regardless of what positional shapes the caller uses.

Test files are free to add their own per-test stubs on top — this
conftest only stubs the things that need to exist *before* the bot
module imports.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Optional-dep stubs — only inserted if the real module isn't installed
# in this venv. Tests that genuinely need the real package can override
# this with ``pytest.importorskip("pandas")`` or similar; this conftest
# only fills holes left by the lean sandbox env.
# ---------------------------------------------------------------------------


def _stub_optional(name: str) -> None:
    """Insert ``MagicMock()`` for *name* if the import would fail."""
    if name in sys.modules:
        return
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = MagicMock()


# Heavy / optional imports the bot module pulls in transitively
# (signal_notifications → matplotlib; web client → fastapi; etc).
# Only stub the ones whose absence is benign for tests.
#
# S-045 T1 (BUG-062 fix): we also stub `telegram.error` and
# `telegram.constants` here. `src/bot/comms_handler.py` does
# `from telegram.error import TelegramError`, and
# `src/bot/claude_bridge.py` does `from telegram.constants import
# ChatAction`. Without these submodule stubs, ~45 tests that
# transitively import either bot module fail collection with
# "No module named 'telegram.error'; 'telegram' is not a package"
# because Python treats the bare-MagicMock `telegram` entry as a
# leaf, not a package. See `docs/claude/ci-status-checks.md`
# § pytest-collect.
for _name in (
    "matplotlib",
    "matplotlib.pyplot",
    "telegram",
    "telegram.ext",
    "telegram.error",
    "telegram.constants",
    "dotenv",
    "requests",
):
    _stub_optional(_name)


# ---------------------------------------------------------------------------
# Telegram stubs (centralised — fixes BUG-010).
#
# We expose `MagicMock`-style classes for the symbols imported at the
# top of `src/bot/telegram_query_bot.py`:
#
#     from telegram import (
#         Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
#     )
#     from telegram.ext import (
#         Application, CommandHandler, CallbackQueryHandler, ContextTypes,
#     )
#
# `Update`, `BotCommand`, etc. are fine as `MagicMock` (no callers use
# them in arg positions that crash `_mock_set_magics`).
#
# `InlineKeyboardButton` and `InlineKeyboardMarkup` MUST be callables
# that return fresh mocks — module-level code does
# `InlineKeyboardMarkup([[...]])` and we cannot pass a list to MagicMock's
# first positional (it ends up as `_mock_methods` which crashes when
# hashed).
# ---------------------------------------------------------------------------


_tg = sys.modules.get("telegram")
if _tg is not None:
    _tg.Update = getattr(_tg, "Update", MagicMock)
    _tg.BotCommand = getattr(_tg, "BotCommand", MagicMock)
    # Always override these two even if the test file already touched
    # the telegram module — the lambda factory is the only safe shape.
    _tg.InlineKeyboardButton = lambda *a, **kw: MagicMock()
    _tg.InlineKeyboardMarkup = lambda *a, **kw: MagicMock()

# S-045 T1: `telegram.error.TelegramError` MUST be a real exception
# class — `comms_handler.py` does `except TelegramError:` and a
# bare MagicMock attr crashes the except clause with TypeError.
_tg_err = sys.modules.get("telegram.error")
if _tg_err is not None:
    _existing_te = getattr(_tg_err, "TelegramError", None)
    if not (isinstance(_existing_te, type) and issubclass(_existing_te, BaseException)):
        class _StubTelegramError(Exception):
            """Stand-in for `telegram.error.TelegramError`."""

        _tg_err.TelegramError = _StubTelegramError
    # Cross-link so `telegram.error` is also reachable as `telegram.error`
    # when a caller does `import telegram` then `telegram.error.X`.
    if _tg is not None:
        _tg.error = _tg_err

# `telegram.constants.ChatAction` — referenced by `claude_bridge.py`.
_tg_const = sys.modules.get("telegram.constants")
if _tg_const is not None:
    _tg_const.ChatAction = getattr(_tg_const, "ChatAction", MagicMock())
    if _tg is not None:
        _tg.constants = _tg_const

_tgext = sys.modules.get("telegram.ext")
if _tgext is not None:
    _tgext.Application = getattr(_tgext, "Application", MagicMock)
    _tgext.CommandHandler = getattr(_tgext, "CommandHandler", MagicMock)
    _tgext.CallbackQueryHandler = getattr(
        _tgext, "CallbackQueryHandler", MagicMock,
    )
    # `MessageHandler` + `filters` are imported by `comms_handler.py`
    # and `claude_bridge.py` but were missing from the conftest stub.
    _tgext.MessageHandler = getattr(_tgext, "MessageHandler", MagicMock)
    _tgext.filters = getattr(_tgext, "filters", MagicMock())
    _ctx = getattr(_tgext, "ContextTypes", MagicMock())
    _ctx.DEFAULT_TYPE = getattr(_ctx, "DEFAULT_TYPE", MagicMock)
    _tgext.ContextTypes = _ctx
