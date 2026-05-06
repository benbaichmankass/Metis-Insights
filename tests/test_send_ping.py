"""S-019 — tests for scripts/send_ping.py + the bot's inbox drain.

The send_ping helper is the canonical producer; the bot's
``_drain_pending_pings`` is the consumer. Tests pin both halves.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import send_ping  # noqa: E402


def _drive(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# scripts/send_ping.py — enqueue contract
# ---------------------------------------------------------------------------


def test_enqueue_writes_atomic_json(tmp_path, monkeypatch):
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", tmp_path)
    path = send_ping.enqueue("hello world", priority="high")
    assert path.exists()
    payload = json.loads(path.read_text())
    assert payload == {"priority": "high", "body": "hello world"}
    # Atomic — no leftover .tmp.
    assert not list(tmp_path.glob("*.tmp"))


def test_enqueue_rejects_invalid_priority(tmp_path, monkeypatch):
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", tmp_path)
    with pytest.raises(ValueError):
        send_ping.enqueue("x", priority="WAT")


def test_enqueue_rejects_empty_body(tmp_path, monkeypatch):
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", tmp_path)
    with pytest.raises(ValueError):
        send_ping.enqueue("   ", priority="normal")


def test_enqueue_creates_dir(tmp_path, monkeypatch):
    target = tmp_path / "fresh" / "inbox"
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", target)
    send_ping.enqueue("hi", priority="normal")
    assert target.exists() and target.is_dir()


def test_main_cli_writes_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", tmp_path)
    rc = send_ping.main(["hello", "from", "CLI"])
    assert rc == 0
    queued = sorted(tmp_path.glob("*.json"))
    assert len(queued) == 1
    assert json.loads(queued[0].read_text())["body"] == "hello from CLI"
    out = capsys.readouterr().out.strip()
    # Stdout prints the path of the queued file for shell-script chaining.
    assert out == str(queued[0])


# ---------------------------------------------------------------------------
# Bot drain — consumer side
# ---------------------------------------------------------------------------


def test_bot_drain_sends_each_file_and_deletes(tmp_path, monkeypatch):
    from src.bot import telegram_query_bot as bot

    monkeypatch.setattr(bot, "PENDING_PINGS_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "1234")

    # Drop two files via the same atomic mechanism the writers use.
    send_ping.PENDING_PINGS_DIR = tmp_path  # for this call only
    p1 = send_ping.enqueue("first", priority="high")
    p2 = send_ping.enqueue("second", priority="urgent")

    sent: list = []
    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock(side_effect=lambda **kw: sent.append(kw))
    ctx = MagicMock()
    ctx.bot = bot_mock

    _drive(bot._drain_pending_pings(ctx))

    # Both files were sent and removed.
    assert len(sent) == 2
    bodies = [s["text"] for s in sent]
    assert any("🔔 first" in b for b in bodies)
    assert any("🚨 URGENT second" in b for b in bodies)
    assert not p1.exists()
    assert not p2.exists()


def test_bot_drain_skips_when_no_files(tmp_path, monkeypatch):
    from src.bot import telegram_query_bot as bot

    monkeypatch.setattr(bot, "PENDING_PINGS_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "1234")
    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock()
    ctx = MagicMock()
    ctx.bot = bot_mock

    _drive(bot._drain_pending_pings(ctx))
    bot_mock.send_message.assert_not_awaited()


def test_bot_drain_renames_malformed_to_broken(tmp_path, monkeypatch):
    from src.bot import telegram_query_bot as bot

    monkeypatch.setattr(bot, "PENDING_PINGS_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "1234")
    bad = tmp_path / "bad.json"
    bad.write_text("not json {")
    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock()
    ctx = MagicMock()
    ctx.bot = bot_mock

    _drive(bot._drain_pending_pings(ctx))
    assert (tmp_path / "bad.json.broken").exists()
    bot_mock.send_message.assert_not_awaited()


def test_bot_drain_leaves_file_on_send_failure_for_retry(tmp_path, monkeypatch):
    """A transient telegram failure must NOT delete the file — the next
    drain tick retries."""
    from src.bot import telegram_query_bot as bot

    monkeypatch.setattr(bot, "PENDING_PINGS_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "1234")
    send_ping.PENDING_PINGS_DIR = tmp_path
    p = send_ping.enqueue("retry me", priority="normal")

    async def _boom(**kw):
        raise RuntimeError("telegram 503")

    bot_mock = MagicMock()
    bot_mock.send_message = _boom
    ctx = MagicMock()
    ctx.bot = bot_mock

    _drive(bot._drain_pending_pings(ctx))
    assert p.exists()  # NOT deleted; retried next tick.


def test_bot_drain_no_chat_id_warns_and_skips(tmp_path, monkeypatch):
    from src.bot import telegram_query_bot as bot

    monkeypatch.setattr(bot, "PENDING_PINGS_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", None)
    send_ping.PENDING_PINGS_DIR = tmp_path
    p = send_ping.enqueue("no chat", priority="normal")
    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock()
    ctx = MagicMock()
    ctx.bot = bot_mock

    _drive(bot._drain_pending_pings(ctx))
    bot_mock.send_message.assert_not_awaited()
    # File preserved so a config fix + restart re-attempts.
    assert p.exists()


# ---------------------------------------------------------------------------
# Two-bot routing — BUG-058 follow-up (2026-05-06)
# ---------------------------------------------------------------------------


def test_enqueue_target_claude_writes_to_claude_inbox(tmp_path, monkeypatch):
    """``target='claude'`` MUST land in the claude inbox, not the trader
    inbox. This is the routing contract — Claude session pings ride
    on @claude_ict_comms_bot per the new workplan."""
    trader_inbox = tmp_path / "trader"
    claude_inbox = tmp_path / "claude"
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", trader_inbox)
    monkeypatch.setattr(send_ping, "PENDING_CLAUDE_PINGS_DIR", claude_inbox)

    path = send_ping.enqueue("session ping", priority="high", target="claude")
    assert path.parent == claude_inbox
    assert path.exists()
    assert not trader_inbox.exists() or not list(trader_inbox.iterdir())


def test_enqueue_target_trader_keeps_default_inbox(tmp_path, monkeypatch):
    """The default target preserves the existing producers' contract —
    every trade-alert call site goes here without modification."""
    trader_inbox = tmp_path / "trader"
    claude_inbox = tmp_path / "claude"
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", trader_inbox)
    monkeypatch.setattr(send_ping, "PENDING_CLAUDE_PINGS_DIR", claude_inbox)

    # Default target is "trader" — no kwarg required.
    path = send_ping.enqueue("trade alert", priority="urgent")
    assert path.parent == trader_inbox
    assert not claude_inbox.exists() or not list(claude_inbox.iterdir())


def test_enqueue_rejects_invalid_target(tmp_path, monkeypatch):
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", tmp_path / "t")
    monkeypatch.setattr(send_ping, "PENDING_CLAUDE_PINGS_DIR", tmp_path / "c")
    with pytest.raises(ValueError):
        send_ping.enqueue("x", priority="normal", target="nope")


def test_main_cli_target_claude_routes_via_flag(tmp_path, monkeypatch, capsys):
    trader_inbox = tmp_path / "trader"
    claude_inbox = tmp_path / "claude"
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", trader_inbox)
    monkeypatch.setattr(send_ping, "PENDING_CLAUDE_PINGS_DIR", claude_inbox)

    rc = send_ping.main(["--target", "claude", "checkpoint", "appended"])
    assert rc == 0
    queued = sorted(claude_inbox.glob("*.json"))
    assert len(queued) == 1
    # Stdout reports the path of the queued file in the CLAUDE inbox.
    assert str(queued[0]) in capsys.readouterr().out


# ---------------------------------------------------------------------------
# claude_bridge drain loop — consumer side
# ---------------------------------------------------------------------------


def test_claude_bridge_drain_sends_each_file_and_deletes(
    tmp_path, monkeypatch,
):
    """Mirror of the trader-bot drain test — same semantics, different
    inbox + bot."""
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "9999")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    from src.bot import claude_bridge as bridge

    monkeypatch.setattr(bridge, "PENDING_CLAUDE_PINGS_DIR", tmp_path)
    monkeypatch.setattr(bridge, "ALLOWED_CHAT_ID", 9999)

    send_ping.PENDING_CLAUDE_PINGS_DIR = tmp_path
    p1 = send_ping.enqueue("checkpoint X", priority="normal", target="claude")
    p2 = send_ping.enqueue("BLOCKED Y", priority="urgent", target="claude")

    sent: list = []
    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock(side_effect=lambda **kw: sent.append(kw))
    ctx = MagicMock()
    ctx.bot = bot_mock

    _drive(bridge._drain_pending_claude_pings(ctx))

    assert len(sent) == 2
    bodies = [s["text"] for s in sent]
    assert any("ℹ️ checkpoint X" in b for b in bodies)
    assert any("🚨 URGENT BLOCKED Y" in b for b in bodies)
    # Both went to the operator chat ID, not some other chat.
    assert all(s["chat_id"] == 9999 for s in sent)
    # Files removed on success.
    assert not p1.exists()
    assert not p2.exists()


def test_claude_bridge_drain_skips_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "9999")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    from src.bot import claude_bridge as bridge

    monkeypatch.setattr(bridge, "PENDING_CLAUDE_PINGS_DIR", tmp_path)
    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock()
    ctx = MagicMock()
    ctx.bot = bot_mock

    _drive(bridge._drain_pending_claude_pings(ctx))
    bot_mock.send_message.assert_not_awaited()


def test_claude_bridge_drain_renames_malformed_to_broken(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "9999")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    from src.bot import claude_bridge as bridge

    monkeypatch.setattr(bridge, "PENDING_CLAUDE_PINGS_DIR", tmp_path)
    monkeypatch.setattr(bridge, "ALLOWED_CHAT_ID", 9999)
    bad = tmp_path / "bad.json"
    bad.write_text("not json {")
    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock()
    ctx = MagicMock()
    ctx.bot = bot_mock

    _drive(bridge._drain_pending_claude_pings(ctx))
    assert (tmp_path / "bad.json.broken").exists()
    bot_mock.send_message.assert_not_awaited()


def test_claude_bridge_drain_leaves_file_on_send_failure(tmp_path, monkeypatch):
    """A transient telegram failure must leave the file in place so the
    next tick retries — same retry semantics as the trader bot."""
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "9999")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    from src.bot import claude_bridge as bridge

    monkeypatch.setattr(bridge, "PENDING_CLAUDE_PINGS_DIR", tmp_path)
    monkeypatch.setattr(bridge, "ALLOWED_CHAT_ID", 9999)
    send_ping.PENDING_CLAUDE_PINGS_DIR = tmp_path
    p = send_ping.enqueue("retry me", priority="normal", target="claude")

    async def _boom(**kw):
        raise RuntimeError("telegram 503")

    bot_mock = MagicMock()
    bot_mock.send_message = _boom
    ctx = MagicMock()
    ctx.bot = bot_mock

    _drive(bridge._drain_pending_claude_pings(ctx))
    assert p.exists()


def test_notify_on_pull_routes_to_claude_target(tmp_path, monkeypatch):
    """End-to-end: notify_on_pull.py emits via target='claude' so every
    session ping lands in the bridge bot's inbox, not the trader's."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import notify_on_pull as nop

    trader_inbox = tmp_path / "trader"
    claude_inbox = tmp_path / "claude"
    monkeypatch.setattr(nop, "CHECKPOINT_LOG", tmp_path / "log.md")
    monkeypatch.setattr(nop, "PENDING_PINGS", tmp_path / "queue.jsonl")
    monkeypatch.setattr(nop, "_blocker_pings",
                        lambda pre, post: [("urgent", "session BLOCKED")])
    monkeypatch.setattr(nop, "_diff_touched_checkpoint_log",
                        lambda pre, post: False)
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", trader_inbox)
    monkeypatch.setattr(send_ping, "PENDING_CLAUDE_PINGS_DIR", claude_inbox)

    rc = nop.main(["--pre", "abc", "--post", "def"])
    assert rc == 0
    # Routed to the Claude bot's inbox; the trader inbox is untouched.
    assert sorted(claude_inbox.glob("*.json"))
    assert not trader_inbox.exists() or not list(trader_inbox.iterdir())
