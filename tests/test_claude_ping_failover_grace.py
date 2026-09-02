"""BL-20260901-CLAUDE-PING-TWO-DRAINERS-ONE-QUEUE.

``runtime_logs/pending_claude_pings`` has TWO live drainers, not one:

* ``ict-claude-bridge.service`` — ``claude_bridge._drain_pending_claude_pings``
* ``ict-telegram-bot.service``  — the ``_drain_claude_pings`` job in
  ``telegram_query_bot``, which calls ``cloud_notifier._drain_pending_pings``

Both tick every 5s on the same directory and each does read → send → unlink, so
the file is on disk for the whole Telegram POST. Measured live 2026-09-01: one
enqueue at 22:10:17Z was delivered TWICE — the bridge's POST took 3.28s
(22:10:19.93 → 22:10:23.21) and the trader bot's 22:10:21 tick read the same
file mid-flight. One enqueue, two conversations.

These tests pin BOTH halves of the fix and, deliberately, the thing it must NOT
do. The failure this system has actually paid for on this path is LOSS, not
duplication (``src/runtime/notify.py`` records the 2026-06-23 silent drop), and
the trader-bot drain exists at all because the bridge was dead for weeks
(2026-06-22). So a test that only proved "no duplicate" would be satisfied by
deleting the drainer — which re-opens that outage.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from src.bot import cloud_notifier as cn


def _drive(coro):
    # A FRESH loop per call, closed after. `asyncio.get_event_loop()` inherits
    # whatever the previously-run module left behind — `test_claude_ping_routing`
    # uses `asyncio.run`, which CLOSES the loop — so a shared-loop helper passes
    # alone and fails on suite order. Measured: these 6 tests pass in isolation
    # and failed when that module ran first.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _write_ping(d, name: str, body: str = "hello", age_s: float = 0.0):
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(json.dumps({"priority": "normal", "body": body}))
    if age_s:
        past = time.time() - age_s
        os.utime(p, (past, past))
    return p


class _Bot:
    """Records sends. Not a MagicMock: the assertions here are about WHICH
    process delivered and how many times, and a mock that silently accepts any
    call shape would pass while the drainer sent nothing."""

    def __init__(self):
        self.sent: list[str] = []

    async def send_message(self, chat_id=None, text=None, **kw):
        self.sent.append(text)


@pytest.fixture()
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "9999")
    monkeypatch.delenv("CLAUDE_PING_FAILOVER_GRACE_S", raising=False)
    # Keep the legacy-twin sweep out of every assertion below: it is a
    # DIFFERENT directory with a DIFFERENT (uncontended) contract.
    monkeypatch.setattr(cn, "_legacy_repo_ping_dir", lambda *_a, **_k: None)
    return tmp_path


# ── the grace resolver ─────────────────────────────────────────────────────

def test_grace_defaults_to_60s(monkeypatch):
    monkeypatch.delenv("CLAUDE_PING_FAILOVER_GRACE_S", raising=False)
    assert cn.claude_ping_failover_grace_s() == 60.0


def test_unparseable_grace_falls_back_to_the_default_not_to_zero(monkeypatch):
    """A typo must not silently re-arm the double delivery. Falling back to 0
    would disable the gate — the dangerous direction — which is exactly why
    this is asserted rather than left to the reader."""
    monkeypatch.setenv("CLAUDE_PING_FAILOVER_GRACE_S", "sixty")
    assert cn.claude_ping_failover_grace_s() == 60.0


def test_zero_grace_is_the_documented_rollback(monkeypatch):
    monkeypatch.setenv("CLAUDE_PING_FAILOVER_GRACE_S", "0")
    assert cn.claude_ping_failover_grace_s() == 0.0


# ── the gate ───────────────────────────────────────────────────────────────

def test_failover_leaves_a_young_file_for_the_bridge(_env):
    """The regression. A fresh ping belongs to the bridge; the failover must
    not touch it — and must LEAVE it on disk, not consume it."""
    d = _env / "pending_claude_pings"
    p = _write_ping(d, "100-normal.json", "fresh ping")
    bot = _Bot()

    _drive(cn._drain_pending_pings(
        None, pings_dir=str(d), bot=bot, deliver_only_older_than_s=60.0))

    assert bot.sent == []
    assert p.exists(), "a deferred ping must survive for the owner AND for the next tick"


def test_failover_delivers_once_the_grace_has_passed(_env):
    """The bridge being DOWN is the case this drainer exists for. Deleting it
    would pass the test above and fail this one."""
    d = _env / "pending_claude_pings"
    p = _write_ping(d, "100-normal.json", "bridge is down", age_s=120.0)
    bot = _Bot()

    _drive(cn._drain_pending_pings(
        None, pings_dir=str(d), bot=bot, deliver_only_older_than_s=60.0))

    assert bot.sent == ["ℹ️ bridge is down"]
    assert not p.exists()


def test_unreadable_mtime_delivers_rather_than_stranding(_env, monkeypatch):
    """Fail direction is chosen against LOSS. If the age cannot be read, a
    skipping gate would defer the same file on every tick forever."""
    d = _env / "pending_claude_pings"
    _write_ping(d, "100-normal.json", "ageless")
    bot = _Bot()

    real = os.path.getmtime

    def _boom(path):
        if str(path).endswith("100-normal.json"):
            raise OSError("mtime unavailable")
        return real(path)

    monkeypatch.setattr(os.path, "getmtime", _boom)
    _drive(cn._drain_pending_pings(
        None, pings_dir=str(d), bot=bot, deliver_only_older_than_s=60.0))

    assert bot.sent == ["ℹ️ ageless"]


def test_grace_of_zero_restores_the_pre_fix_behaviour(_env):
    d = _env / "pending_claude_pings"
    _write_ping(d, "100-normal.json", "immediate")
    bot = _Bot()

    _drive(cn._drain_pending_pings(
        None, pings_dir=str(d), bot=bot, deliver_only_older_than_s=0.0))

    assert bot.sent == ["ℹ️ immediate"]


def test_ungated_callers_are_byte_for_byte_unchanged(_env):
    """The trade-alert inbox (`pending_pings`) has ONE drainer and must not
    inherit a grace. Passing no argument must deliver immediately."""
    d = _env / "pending_pings"
    _write_ping(d, "100-normal.json", "trade alert")
    bot = _Bot()

    _drive(cn._drain_pending_pings(None, pings_dir=str(d), bot=bot))

    assert bot.sent == ["ℹ️ trade alert"]


# ── the race itself ────────────────────────────────────────────────────────

def test_the_measured_race_no_longer_double_delivers(_env, monkeypatch):
    """Reproduce the 2026-09-01 interleaving directly.

    The owner reads the file and its POST is still in flight when the failover
    ticks. Before the grace both delivered; now only the owner does.
    """
    d = _env / "pending_claude_pings"
    p = _write_ping(d, "436339508894-normal.json", "test ping")

    owner_bot, failover_bot = _Bot(), _Bot()
    delivered_by: list[str] = []

    async def _owner_send(chat_id=None, text=None, **kw):
        # The bridge's POST took 3.28s. While it is in flight the file is still
        # on disk — this is where the failover's tick landed.
        await cn._drain_pending_pings(
            None, pings_dir=str(d), bot=failover_bot,
            deliver_only_older_than_s=60.0)
        delivered_by.append("owner")

    monkeypatch.setattr(owner_bot, "send_message", _owner_send)
    _drive(cn._drain_pending_pings(
        None, pings_dir=str(d), bot=owner_bot))

    assert delivered_by == ["owner"]
    assert failover_bot.sent == [], (
        "the failover delivered a ping the owner was mid-send on — "
        "this is the 22:10:17Z double delivery"
    )
    assert not p.exists()
