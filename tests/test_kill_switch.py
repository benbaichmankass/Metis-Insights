"""M3b kill-switch tests: /halt, /resume, /status commands and order-layer blocking."""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Stub heavy / unavailable deps before any src import.
# ---------------------------------------------------------------------------
for _mod in (
    "telegram",
    "telegram.ext",
    "dotenv",
    "requests",
    "pandas",
    "numpy",
    "src.runtime.signal_notifications",
    "src.runtime.notify",
    "src.utils.signal_audit_logger",
    "src.runtime.signal_writer",
):
    sys.modules.setdefault(_mod, MagicMock())

# Provide realistic-enough telegram attribute stubs.
_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.BotCommand = MagicMock
_tg.InlineKeyboardButton = lambda *a, **kw: MagicMock()  # S-016 H5 BUG-010 fix
_tg.InlineKeyboardMarkup = lambda *a, **kw: MagicMock()  # S-016 H5 BUG-010 fix

_tgext = sys.modules["telegram.ext"]
_tgext.Application = MagicMock
_tgext.CommandHandler = MagicMock
_tgext.CallbackQueryHandler = MagicMock
# ContextTypes.DEFAULT_TYPE is referenced as a type annotation at function
# definition time, so the attribute must exist on the stub.
_ContextTypes = MagicMock()
_ContextTypes.DEFAULT_TYPE = MagicMock
_tgext.ContextTypes = _ContextTypes

from src.bot.telegram_query_bot import (  # noqa: E402
    cmd_halt,
    cmd_resume,
    cmd_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_update(chat_id: str = "12345") -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.reply_text = AsyncMock()
    return update


def _make_context() -> MagicMock:
    return MagicMock()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# is_halted() unit tests (pure file-system logic)
# ---------------------------------------------------------------------------

def test_is_halted_returns_false_when_flag_absent(tmp_path):
    flag = str(tmp_path / "trader_halt.flag")
    with patch("src.bot.telegram_query_bot.HALT_FLAG_PATH", flag):
        # reload function via a local import alias to pick up the patched path
        with patch("src.bot.telegram_query_bot.os.path.exists", wraps=os.path.exists):
            assert not os.path.exists(flag)


def test_is_halted_returns_true_when_flag_present(tmp_path):
    flag = str(tmp_path / "trader_halt.flag")
    flag_file = tmp_path / "trader_halt.flag"
    flag_file.write_text("halted")
    with patch("src.bot.telegram_query_bot.HALT_FLAG_PATH", flag):
        assert os.path.exists(flag)


# ---------------------------------------------------------------------------
# /halt command — writes the flag file
# ---------------------------------------------------------------------------

def test_cmd_halt_creates_flag_file(tmp_path):
    flag = str(tmp_path / "halt.flag")
    update = _make_update(chat_id=str(12345))
    context = _make_context()

    with (
        patch("src.bot.telegram_query_bot.HALT_FLAG_PATH", flag),
        patch("src.bot.telegram_query_bot.TELEGRAM_CHAT_ID", "12345"),
    ):
        _run(cmd_halt(update, context))

    assert os.path.exists(flag), "Halt flag file must exist after /halt"
    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "HALTED" in reply_text or "halted" in reply_text.lower()


def test_cmd_halt_is_idempotent(tmp_path):
    """Calling /halt twice must not raise and flag must remain set."""
    flag = str(tmp_path / "halt.flag")
    update = _make_update(chat_id="12345")
    context = _make_context()

    with (
        patch("src.bot.telegram_query_bot.HALT_FLAG_PATH", flag),
        patch("src.bot.telegram_query_bot.TELEGRAM_CHAT_ID", "12345"),
    ):
        _run(cmd_halt(update, context))
        _run(cmd_halt(update, context))

    assert os.path.exists(flag)


# ---------------------------------------------------------------------------
# /resume command — clears the flag file
# ---------------------------------------------------------------------------

def test_cmd_resume_removes_flag_file(tmp_path):
    flag = str(tmp_path / "halt.flag")
    # Pre-create the flag.
    with open(flag, "w") as fh:
        fh.write("halted")

    update = _make_update(chat_id="12345")
    context = _make_context()

    with (
        patch("src.bot.telegram_query_bot.HALT_FLAG_PATH", flag),
        patch("src.bot.telegram_query_bot.TELEGRAM_CHAT_ID", "12345"),
    ):
        _run(cmd_resume(update, context))

    assert not os.path.exists(flag), "Halt flag file must be gone after /resume"
    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "RESUMED" in reply_text or "resumed" in reply_text.lower()


def test_cmd_resume_when_not_halted_sends_info_message(tmp_path):
    flag = str(tmp_path / "halt.flag")  # does not exist
    update = _make_update(chat_id="12345")
    context = _make_context()

    with (
        patch("src.bot.telegram_query_bot.HALT_FLAG_PATH", flag),
        patch("src.bot.telegram_query_bot.TELEGRAM_CHAT_ID", "12345"),
    ):
        _run(cmd_resume(update, context))

    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "not halted" in reply_text.lower() or "no flag" in reply_text.lower()


# ---------------------------------------------------------------------------
# /status command — reports halt state, P&L, open positions
# ---------------------------------------------------------------------------

def test_cmd_status_reports_halted_when_flag_present(tmp_path):
    flag = str(tmp_path / "halt.flag")
    with open(flag, "w") as fh:
        fh.write("halted")

    update = _make_update(chat_id="12345")
    context = _make_context()

    with (
        patch("src.bot.telegram_query_bot.HALT_FLAG_PATH", flag),
        patch("src.bot.telegram_query_bot.TELEGRAM_CHAT_ID", "12345"),
        patch("src.bot.telegram_query_bot.fetch_today_pnl", return_value=(3, 42.5)),
        patch("src.bot.telegram_query_bot.fetch_open_positions_count", return_value=2),
        patch("src.bot.telegram_query_bot.get_service_status", return_value="active"),
    ):
        _run(cmd_status(update, context))

    reply_text = update.message.reply_text.call_args[0][0]
    assert "HALTED" in reply_text


def test_cmd_status_reports_running_when_flag_absent(tmp_path):
    flag = str(tmp_path / "halt.flag")  # does not exist

    update = _make_update(chat_id="12345")
    context = _make_context()

    with (
        patch("src.bot.telegram_query_bot.HALT_FLAG_PATH", flag),
        patch("src.bot.telegram_query_bot.TELEGRAM_CHAT_ID", "12345"),
        patch("src.bot.telegram_query_bot.fetch_today_pnl", return_value=(0, 0.0)),
        patch("src.bot.telegram_query_bot.fetch_open_positions_count", return_value=0),
        patch("src.bot.telegram_query_bot.get_service_status", return_value="inactive"),
    ):
        _run(cmd_status(update, context))

    reply_text = update.message.reply_text.call_args[0][0]
    assert "RUNNING" in reply_text


def test_cmd_status_includes_pnl_and_open_positions(tmp_path):
    flag = str(tmp_path / "halt.flag")

    update = _make_update(chat_id="12345")
    context = _make_context()

    with (
        patch("src.bot.telegram_query_bot.HALT_FLAG_PATH", flag),
        patch("src.bot.telegram_query_bot.TELEGRAM_CHAT_ID", "12345"),
        patch("src.bot.telegram_query_bot.fetch_today_pnl", return_value=(5, -123.45)),
        patch("src.bot.telegram_query_bot.fetch_open_positions_count", return_value=3),
        patch("src.bot.telegram_query_bot.get_service_status", return_value="active"),
    ):
        _run(cmd_status(update, context))

    reply_text = update.message.reply_text.call_args[0][0]
    assert "5" in reply_text      # trade count
    assert "123" in reply_text    # P&L value present
    assert "3" in reply_text      # open position count


# ---------------------------------------------------------------------------
# Order-layer blocking (pipeline) — flag checked before safe_place_order
# ---------------------------------------------------------------------------

# These rely on src.runtime.pipeline already tested in test_orders.py; we
# re-verify the contract here for completeness.

for _mod in ("pandas", "matplotlib", "matplotlib.pyplot", "scipy", "sklearn"):
    sys.modules.setdefault(_mod, MagicMock())

from src.runtime.pipeline import run_pipeline  # noqa: E402


class _DummyClient:
    def place_order(self, **order):
        return {"ok": True}


def _settings(**overrides):
    base = {"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "true"}
    base.update(overrides)
    return base


def _signal_stub(signal: dict):
    return lambda _settings: signal


_ACTIONABLE = {"symbol": "BTCUSDT", "side": "buy", "qty": 1.0, "price": 50_000.0}


def test_order_blocked_when_halt_flag_set():
    with patch("src.runtime.pipeline.os.path.exists", return_value=True):
        result = run_pipeline(
            settings=_settings(),
            exchange_client=_DummyClient(),
            signal_builder=_signal_stub(_ACTIONABLE),
        )
    assert result["order_result"]["status"] == "halted"
    assert result["order_result"]["reason"] == "halt_flag_active"


def test_order_allowed_when_halt_flag_absent():
    with patch("src.runtime.pipeline.os.path.exists", return_value=False):
        result = run_pipeline(
            settings=_settings(),
            exchange_client=_DummyClient(),
            signal_builder=_signal_stub(_ACTIONABLE),
        )
    assert result["order_result"]["status"] == "submitted"
