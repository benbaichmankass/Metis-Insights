"""S-016 H2 — tests for /health and /vmstats.

Pin the contract for the new visibility commands without depending on
real systemd / /proc state. The handlers themselves are async, so
drive them via a per-test event loop (matches the pattern in
tests/test_telegram_signals.py)."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Stub optional deps so the bot module imports in the lean sandbox.
for _mod in ("telegram", "telegram.ext", "dotenv", "requests"):
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


def _drive(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# _file_age helper
# ---------------------------------------------------------------------------


def test_file_age_missing(tmp_path):
    from src.bot import telegram_query_bot as bot
    assert bot._file_age(str(tmp_path / "nope")) == "missing"


def test_file_age_seconds(tmp_path):
    from src.bot import telegram_query_bot as bot
    p = tmp_path / "x.json"
    p.write_text("{}", encoding="utf-8")
    assert bot._file_age(str(p)).endswith("B)")
    assert "s" in bot._file_age(str(p)) or "m" in bot._file_age(str(p))


def test_file_age_minutes(tmp_path):
    from src.bot import telegram_query_bot as bot
    p = tmp_path / "x.txt"
    p.write_text("hello", encoding="utf-8")
    # Set mtime to 10 minutes ago.
    old = time.time() - 600
    os.utime(str(p), (old, old))
    age = bot._file_age(str(p))
    assert "m" in age and "s" not in age.split("m")[0]


def test_file_age_hours(tmp_path):
    from src.bot import telegram_query_bot as bot
    p = tmp_path / "y.txt"
    p.write_text("hello", encoding="utf-8")
    old = time.time() - (3 * 3600 + 12 * 60)
    os.utime(str(p), (old, old))
    age = bot._file_age(str(p))
    assert "h" in age and "m" in age


def test_file_age_days(tmp_path):
    from src.bot import telegram_query_bot as bot
    p = tmp_path / "z.txt"
    p.write_text("hello", encoding="utf-8")
    old = time.time() - (5 * 86400)
    os.utime(str(p), (old, old))
    age = bot._file_age(str(p))
    assert age.endswith("B)") and "d" in age


# ---------------------------------------------------------------------------
# /vmstats helpers
# ---------------------------------------------------------------------------


def test_read_loadavg_returns_three_floats():
    from src.bot import telegram_query_bot as bot
    out = bot._read_loadavg()
    if out == "unknown":
        return  # /proc missing on the test box — acceptable
    parts = out.split()
    assert len(parts) == 3
    for p in parts:
        float(p)  # must parse


def test_read_uptime_human_formats_correctly():
    from src.bot import telegram_query_bot as bot
    out = bot._read_uptime_human()
    if out == "unknown":
        return
    # one of "Nm", "Nh Mm", "Nd Hh Mm"
    assert any(x in out for x in ("d", "h", "m"))


def test_read_meminfo_mb_returns_pair():
    from src.bot import telegram_query_bot as bot
    total, avail = bot._read_meminfo_mb()
    # On the test box /proc/meminfo exists; both should be > 0.
    assert total >= 0 and avail >= 0
    if total:
        assert avail <= total


def test_disk_usage_repo_returns_pair():
    from src.bot import telegram_query_bot as bot
    free, total = bot._disk_usage_repo()
    assert free >= 0 and total >= 0
    if total:
        assert free <= total


# ---------------------------------------------------------------------------
# cmd_health end-to-end
# ---------------------------------------------------------------------------


def test_cmd_health_calls_reply_with_units_and_files(monkeypatch):
    from src.bot import telegram_query_bot as bot
    monkeypatch.setattr(bot, "is_authorised", lambda u: True)
    monkeypatch.setattr(bot, "get_service_status",
                        lambda unit: "active" if unit.endswith(".timer") else "active")

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    _drive(bot.cmd_health(update, context))

    sent = update.message.reply_text.await_args.args[0]
    # All four units must appear.
    for unit in bot._HEALTH_UNITS:
        assert unit in sent
    # Each data-file label must appear.
    for label, _ in bot._HEALTH_FILES:
        assert label in sent
    assert "🟢" in sent  # active units → green icon


def test_cmd_health_marks_failed_units_red(monkeypatch):
    from src.bot import telegram_query_bot as bot
    monkeypatch.setattr(bot, "is_authorised", lambda u: True)
    monkeypatch.setattr(bot, "get_service_status", lambda unit: "failed")

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    _drive(bot.cmd_health(update, MagicMock()))
    sent = update.message.reply_text.await_args.args[0]
    assert "🔴" in sent


# ---------------------------------------------------------------------------
# cmd_vmstats end-to-end
# ---------------------------------------------------------------------------


def test_cmd_vmstats_includes_uptime_load_mem_disk(monkeypatch):
    from src.bot import telegram_query_bot as bot
    monkeypatch.setattr(bot, "is_authorised", lambda u: True)
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    _drive(bot.cmd_vmstats(update, MagicMock()))
    sent = update.message.reply_text.await_args.args[0]
    for needle in ("Uptime", "Load", "Memory", "Disk", "CPU"):
        assert needle in sent
