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
for _name in (
    "matplotlib",
    "matplotlib.pyplot",
    "telegram",
    "telegram.ext",
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

_tgext = sys.modules.get("telegram.ext")
if _tgext is not None:
    _tgext.Application = getattr(_tgext, "Application", MagicMock)
    _tgext.CommandHandler = getattr(_tgext, "CommandHandler", MagicMock)
    _tgext.CallbackQueryHandler = getattr(
        _tgext, "CallbackQueryHandler", MagicMock,
    )
    _ctx = getattr(_tgext, "ContextTypes", MagicMock())
    _ctx.DEFAULT_TYPE = getattr(_ctx, "DEFAULT_TYPE", MagicMock)
    _tgext.ContextTypes = _ctx
