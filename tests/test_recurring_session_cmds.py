"""Telegram bot command tests for /audit, /improve_strategy, /train_model, /roadmap.

Mirrors the offline async pattern from test_s008_5_telegram_sprint_cmds.py
— heavy deps (telegram, pybit) are stubbed at sys.modules level.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub heavy deps before any src import
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

# telegram.error.TelegramError must be a real class so `except` works.
sys.modules["telegram.error"].TelegramError = type("TelegramError", (Exception,), {})

sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"].dotenv_values = lambda *a, **kw: {}

_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.BotCommand = MagicMock
_tg.InlineKeyboardButton = lambda *a, **kw: MagicMock()
_tg.InlineKeyboardMarkup = lambda *a, **kw: MagicMock()
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


def _make_context(args=None) -> MagicMock:
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """Point bot.REPO_ROOT at a tmp dir so the trigger log is isolated."""
    monkeypatch.setattr(bot, "REPO_ROOT", str(tmp_path))
    bot.TELEGRAM_CHAT_ID = "99999"
    return tmp_path


def _last_reply(update: MagicMock) -> str:
    return update.message.reply_text.call_args[0][0]


def _read_trigger_log(repo_root: Path) -> list[dict]:
    log = repo_root / "runtime_logs" / "recurring_sessions.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# /audit
# ---------------------------------------------------------------------------


class TestCmdAudit:
    def test_logs_trigger_and_replies_with_starter_prompt(self, repo_root: Path):
        update = _make_update()
        _run(bot.cmd_audit(update, _make_context()))

        # Trigger logged
        log = _read_trigger_log(repo_root)
        assert len(log) == 1
        assert log[0]["type"] == "audit"

        # Reply contains the starter prompt
        reply = _last_reply(update)
        assert "recurring-hardening-prompt.md" in reply
        assert "Hardening session queued" in reply

    def test_unauthorised_does_nothing(self, repo_root: Path):
        update = _make_update(chat_id="bad")
        _run(bot.cmd_audit(update, _make_context()))
        update.message.reply_text.assert_not_called()
        assert _read_trigger_log(repo_root) == []


# ---------------------------------------------------------------------------
# /improve_strategy
# ---------------------------------------------------------------------------


class TestCmdImproveStrategy:
    def test_no_args_includes_no_strategy_clause(self, repo_root: Path):
        update = _make_update()
        _run(bot.cmd_improve_strategy(update, _make_context()))

        reply = _last_reply(update)
        assert "Strategy Improvement session queued" in reply
        assert "focused on" not in reply

        log = _read_trigger_log(repo_root)
        assert log[0]["type"] == "improve_strategy"
        assert log[0]["args"] == []

    def test_with_strategy_arg_includes_strategy_clause(self, repo_root: Path):
        update = _make_update()
        _run(bot.cmd_improve_strategy(update, _make_context(["vwap"])))

        reply = _last_reply(update)
        assert "vwap" in reply
        assert "Strategy Improvement (vwap) session queued" in reply

        log = _read_trigger_log(repo_root)
        assert log[0]["args"] == ["vwap"]

    def test_unauthorised_does_nothing(self, repo_root: Path):
        update = _make_update(chat_id="bad")
        _run(bot.cmd_improve_strategy(update, _make_context(["vwap"])))
        update.message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# /train_model
# ---------------------------------------------------------------------------


class TestCmdTrainModel:
    def test_no_args_default_label(self, repo_root: Path):
        update = _make_update()
        _run(bot.cmd_train_model(update, _make_context()))
        reply = _last_reply(update)
        assert "Model Training session queued" in reply
        assert "ml-training-policy.md" in reply

    def test_with_strategy_arg(self, repo_root: Path):
        update = _make_update()
        _run(bot.cmd_train_model(update, _make_context(["turtle_soup"])))
        reply = _last_reply(update)
        assert "Model Training (turtle_soup) session queued" in reply
        assert "turtle_soup" in reply

        log = _read_trigger_log(repo_root)
        assert log[0]["type"] == "train_model"
        assert log[0]["args"] == ["turtle_soup"]


# ---------------------------------------------------------------------------
# /roadmap
# ---------------------------------------------------------------------------


class TestCmdRoadmap:
    SAMPLE = """# Roadmap

| Sprint | Title | Status |
|--------|-------|--------|
| S-013 | **Backend Scaffold** | ✅ Done |
| S-014 | **Web Client V1** — HTMX | 🔜 Next |
| S-015 | **Web Client V2** | 📋 Backlog |
"""

    def test_reads_roadmap_and_returns_summary(self, repo_root: Path):
        (repo_root / "ROADMAP.md").write_text(self.SAMPLE)
        update = _make_update()
        _run(bot.cmd_roadmap(update, _make_context()))
        reply = _last_reply(update)
        assert "S-014" in reply
        assert "Web Client V1" in reply
        assert "✅ 1 done" in reply
        assert "📋 1 backlog" in reply

    def test_missing_roadmap_returns_warning(self, repo_root: Path):
        # No ROADMAP.md created
        update = _make_update()
        _run(bot.cmd_roadmap(update, _make_context()))
        reply = _last_reply(update)
        assert "⚠️" in reply or "Could not" in reply

    def test_unauthorised_does_nothing(self, repo_root: Path):
        (repo_root / "ROADMAP.md").write_text(self.SAMPLE)
        update = _make_update(chat_id="bad")
        _run(bot.cmd_roadmap(update, _make_context()))
        update.message.reply_text.assert_not_called()
