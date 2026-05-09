"""Pin the registry-guard added to ``/test`` dispatch in M5 P1.

The handler reads ``config/strategies.yaml`` via
``src.strategy_registry.load_strategies`` and rejects unknown
strategy names with a Telegram reply listing the registered roster.
This saves a poll-cycle round-trip and surfaces typos immediately
(``/test vwapp`` no longer mints an artifact).
"""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Stub heavy deps — same pattern as test_telegram_query_bot.py.
# ---------------------------------------------------------------------------
for _mod in (
    "telegram",
    "telegram.ext",
    "telegram.error",
    "dotenv",
    "requests",
    "pybit",
    "pybit.unified_trading",
    "src.runtime.signal_notifications",
):
    sys.modules.setdefault(_mod, MagicMock())

sys.modules["telegram.error"].TelegramError = type("TelegramError", (Exception,), {})
sys.modules["telegram.ext"].filters = MagicMock()
sys.modules["telegram.ext"].MessageHandler = MagicMock
sys.modules["telegram.ext"].CommandHandler = MagicMock
sys.modules["telegram.ext"].CallbackQueryHandler = MagicMock
sys.modules["telegram.ext"].Application = MagicMock
sys.modules["telegram.ext"].ContextTypes = MagicMock()
sys.modules["telegram.ext"].ContextTypes.DEFAULT_TYPE = object

sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"].dotenv_values = lambda *a, **kw: {}

_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.BotCommand = lambda command, description="": SimpleNamespace(command=command, description=description)
_tg.InlineKeyboardButton = lambda *a, **kw: MagicMock()
_tg.InlineKeyboardMarkup = lambda *a, **kw: MagicMock()

import pytest  # noqa: E402

import src.bot.telegram_query_bot as bot  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _make_update(args_message: str = "vwap"):
    """Build a mock Update with .message.reply_text as AsyncMock."""
    upd = MagicMock()
    upd.effective_chat.id = 12345
    upd.callback_query = None
    upd.message = MagicMock()
    upd.message.reply_text = AsyncMock()
    return upd


def _make_context(args: list[str]):
    ctx = MagicMock()
    ctx.args = args
    return ctx


@pytest.fixture(autouse=True)
def _authorise(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "12345")


# ---------------------------------------------------------------------------
# cmd_test_strategy — registry validation

class TestCmdTestStrategyRegistryGuard:
    def test_rejects_unknown_strategy_with_roster(self, monkeypatch):
        monkeypatch.setattr(
            "src.strategy_registry.load_strategies",
            lambda *a, **kw: [
                {"name": "vwap", "service": "ict-trader-live", "model": None, "signal_prefixes": []},
                {"name": "turtle_soup", "service": "ict-trader-live", "model": None, "signal_prefixes": []},
            ],
        )
        # Make sure the queue helper is never reached.
        sentinel = AsyncMock()
        monkeypatch.setattr(bot, "_queue_comms_ask", sentinel)

        upd = _make_update()
        ctx = _make_context(["bogus_strategy"])

        _run(bot.cmd_test_strategy(upd, ctx))

        sentinel.assert_not_awaited()
        upd.message.reply_text.assert_awaited_once()
        msg = upd.message.reply_text.await_args.args[0]
        assert "Unknown strategy" in msg
        assert "bogus_strategy" in msg
        # Roster should be present so the operator can re-issue.
        assert "vwap" in msg
        assert "turtle_soup" in msg

    def test_accepts_known_strategy(self, monkeypatch):
        monkeypatch.setattr(
            "src.strategy_registry.load_strategies",
            lambda *a, **kw: [
                {"name": "vwap", "service": "ict-trader-live", "model": None, "signal_prefixes": []},
            ],
        )
        sentinel = AsyncMock()
        monkeypatch.setattr(bot, "_queue_comms_ask", sentinel)

        upd = _make_update()
        ctx = _make_context(["vwap"])

        _run(bot.cmd_test_strategy(upd, ctx))

        sentinel.assert_awaited_once()
        # The summary text mentions the consumer (not P1-D's "M5 backtest workflow").
        summary = sentinel.await_args.kwargs["summary"]
        assert "vwap" in summary
        assert "consumer" in summary.lower()

    def test_rejects_when_registry_read_fails(self, monkeypatch):
        def _boom(*a, **kw):
            raise RuntimeError("strategies.yaml not found")
        monkeypatch.setattr("src.strategy_registry.load_strategies", _boom)
        sentinel = AsyncMock()
        monkeypatch.setattr(bot, "_queue_comms_ask", sentinel)

        upd = _make_update()
        ctx = _make_context(["vwap"])

        _run(bot.cmd_test_strategy(upd, ctx))

        sentinel.assert_not_awaited()
        upd.message.reply_text.assert_awaited_once()
        msg = upd.message.reply_text.await_args.args[0]
        assert "registry" in msg.lower()

    def test_rejects_missing_args(self, monkeypatch):
        sentinel = AsyncMock()
        monkeypatch.setattr(bot, "_queue_comms_ask", sentinel)
        upd = _make_update()
        ctx = _make_context([])

        _run(bot.cmd_test_strategy(upd, ctx))

        sentinel.assert_not_awaited()
        upd.message.reply_text.assert_awaited_once()
        msg = upd.message.reply_text.await_args.args[0]
        assert "Usage" in msg
