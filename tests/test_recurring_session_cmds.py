"""Telegram bot command tests for /audit, /improve_strategy, /train_model, /roadmap.

These commands live on the Claude-bridge bot (@claude_ict_comms_bot,
src/bot/claude_bridge.py), NOT the trading-UI bot. Heavy deps (telegram,
anthropic, dotenv) are stubbed at sys.modules level so the import is
offline.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub heavy deps before any src import
# ---------------------------------------------------------------------------
for _mod in (
    "telegram",
    "telegram.constants",
    "telegram.ext",
    "telegram.error",
    "dotenv",
    "anthropic",
):
    sys.modules.setdefault(_mod, MagicMock())

sys.modules["telegram.error"].TelegramError = type("TelegramError", (Exception,), {})
sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None

_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.BotCommand = lambda *a, **kw: ("BotCommand", a, kw)
_tg_consts = sys.modules["telegram.constants"]
_tg_consts.ChatAction = MagicMock()
_tg_ext = sys.modules["telegram.ext"]
_tg_ext.Application = MagicMock
_tg_ext.CommandHandler = MagicMock
_tg_ext.MessageHandler = MagicMock
_tg_ext.ContextTypes = MagicMock()
_tg_ext.ContextTypes.DEFAULT_TYPE = object
_tg_ext.filters = MagicMock()

# claude_bridge reads these at import time.
os.environ.setdefault("TELEGRAM_CLAUDE_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "99999")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import src.bot.claude_bridge as bot  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_update(chat_id: int = 99999) -> MagicMock:
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
    monkeypatch.setattr(bot, "REPO_ROOT", tmp_path)
    return tmp_path


def _last_reply(update: MagicMock) -> str:
    call = update.message.reply_text.call_args
    # Most calls pass the body as the first positional arg, but the
    # roadmap-missing path uses a keyword. Handle both.
    if call.args:
        return call.args[0]
    return call.kwargs["text"]


def _last_parse_mode(update: MagicMock):
    return update.message.reply_text.call_args.kwargs.get("parse_mode")


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

        log = _read_trigger_log(repo_root)
        assert len(log) == 1
        assert log[0]["type"] == "audit"

        reply = _last_reply(update)
        assert "recurring-hardening-prompt.md" in reply
        assert "Hardening session queued" in reply
        # HTML mode + tap-to-copy code block.
        assert "<pre><code>" in reply
        assert _last_parse_mode(update) == "HTML"

    def test_unauthorised_does_nothing(self, repo_root: Path):
        update = _make_update(chat_id=12345)
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
        update = _make_update(chat_id=12345)
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

    def test_reads_roadmap_and_returns_summary(self, repo_root: Path, monkeypatch):
        # cmd_roadmap delegates to processor.get_roadmap_summary(), which uses
        # its own hardcoded repo-relative path — patching bot.REPO_ROOT has no
        # effect. Monkeypatch the processor function to use the tmp ROADMAP.md.
        from src.bot import recurring_dispatch
        from src.units.ui import processor

        roadmap_file = repo_root / "ROADMAP.md"
        roadmap_file.write_text(self.SAMPLE)

        def _summary():
            text = roadmap_file.read_text()
            return recurring_dispatch.render_roadmap_summary(text)

        monkeypatch.setattr(processor, "get_roadmap_summary", _summary)

        update = _make_update()
        _run(bot.cmd_roadmap(update, _make_context()))
        reply = _last_reply(update)
        assert "S-014" in reply
        assert "Web Client V1" in reply
        assert "✅ 1 done" in reply
        assert "📋 1 backlog" in reply

    def test_missing_roadmap_returns_warning(self, repo_root: Path, monkeypatch):
        # Simulate the missing-file error path by returning a ⚠️ string from
        # get_roadmap_summary (that is exactly what the real function returns
        # on FileNotFoundError).
        from src.units.ui import processor

        monkeypatch.setattr(
            processor, "get_roadmap_summary",
            lambda: "⚠️ Could not read ROADMAP.md from the repo.",
        )
        update = _make_update()
        _run(bot.cmd_roadmap(update, _make_context()))
        reply = _last_reply(update)
        assert "⚠️" in reply or "Could not" in reply

    def test_unauthorised_does_nothing(self, repo_root: Path):
        (repo_root / "ROADMAP.md").write_text(self.SAMPLE)
        update = _make_update(chat_id=12345)
        _run(bot.cmd_roadmap(update, _make_context()))
        update.message.reply_text.assert_not_called()
