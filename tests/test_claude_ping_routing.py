"""Claude operational pings go to the DEDICATED bot — and never into silence.

The failure this file exists to prevent is not "the pings went to the wrong
conversation". It is the 2026-06-14 one: ict-claude-bridge's token did not carry
over the Ampere cutover, the resolver returned nothing, and the pings were
silently never delivered for weeks. The 2026-06-22 fold-in fixed that by sending
them through the trader bot; the dedicated bot (2026-09-01) splits them back out.

So every test below asserts the same invariant from a different angle:
**a Claude ping is delivered, and separation is what degrades — never delivery.**
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.bot import cloud_notifier  # noqa: E402
from src.bot.telegram_routes import claude_route  # noqa: E402


class _Bot:
    def __init__(self, name):
        self.name = name
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


class _Ctx:
    def __init__(self, bot):
        self.bot = bot


def _ping(dirpath: pathlib.Path, body: str) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "p1.json").write_text(json.dumps({"priority": "normal", "body": body}))


def test_an_override_bot_sends_and_the_default_bot_does_not(tmp_path):
    """The override is what makes a SEPARATE conversation possible at all."""
    trader, claude = _Bot("trader"), _Bot("claude")
    _ping(tmp_path, "system report ready")
    asyncio.run(cloud_notifier._drain_one_ping_dir(
        _Ctx(trader), "555", str(tmp_path), bot=claude))
    assert len(claude.sent) == 1
    assert claude.sent[0][0] == "555", "chat id must be unchanged — in a DM it is the operator's own id"
    assert trader.sent == [], "the trader bot must not also receive it (that would be the noise we are removing)"


def test_without_an_override_it_still_goes_out_on_the_trader_bot(tmp_path):
    """The 2026-06-22 fold-in behaviour, preserved byte-for-byte.

    This is the FALLBACK path, and it is the one that must never regress: a
    missing dedicated token has to mean 'less specific', not 'undelivered'.
    """
    trader = _Bot("trader")
    _ping(tmp_path, "review ping")
    asyncio.run(cloud_notifier._drain_one_ping_dir(_Ctx(trader), "555", str(tmp_path)))
    assert len(trader.sent) == 1


def test_a_delivered_ping_is_removed_and_a_failed_one_is_retained(tmp_path):
    """Retention on failure is what makes the fallback safe to rely on."""
    class _Failing(_Bot):
        async def send_message(self, chat_id, text, **kw):
            raise RuntimeError("telegram 401")

    _ping(tmp_path, "x")
    asyncio.run(cloud_notifier._drain_one_ping_dir(
        _Ctx(_Bot("trader")), "555", str(tmp_path), bot=_Failing("bad")))
    assert (tmp_path / "p1.json").exists(), "a failed send must NOT delete the ping"

    ok = _Bot("ok")
    asyncio.run(cloud_notifier._drain_one_ping_dir(
        _Ctx(_Bot("trader")), "555", str(tmp_path), bot=ok))
    assert not (tmp_path / "p1.json").exists()
    assert len(ok.sent) == 1


@pytest.mark.parametrize("env,expect_isolated", [
    ({"TELEGRAM_CLAUDE_BOT_SECRET": "tok"}, True),
    ({}, False),
])
def test_isolated_is_what_selects_the_dedicated_bot(monkeypatch, env, expect_isolated):
    """`isolated`, not `deliverable`.

    A route can be deliverable on the SHARED trader token — which is exactly the
    un-separated state this work leaves behind — so keying the choice on
    `deliverable` would select the dedicated path when there is no dedicated bot.
    """
    for k in ("TELEGRAM_CLAUDE_BOT_SECRET", "TELEGRAM_CLAUDE_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "trader-tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "555")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    route = claude_route()
    assert route.isolated is expect_isolated
    assert route.deliverable is True, (
        "deliverable must stay True in BOTH cases — that is the property that "
        "keeps a missing dedicated token from becoming an outage"
    )


def test_describe_never_leaks_a_token(monkeypatch):
    """The log line that names the route is written on every fallback."""
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_SECRET", "SUPERSECRET-do-not-log")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "555")
    text = claude_route().describe()
    assert "SUPERSECRET" not in text
    assert "TELEGRAM_CLAUDE_BOT_SECRET" in text, "it must name the VARIABLE that answered"


# ---------------------------------------------------------------------------
# The BRIDGE DRAIN — the path #10664 left behind (2026-09-01)
# ---------------------------------------------------------------------------
#
# ⚠️ WHY THESE EXIST. #10664 shipped the router and converted cloud_notifier,
# which every test above exercises. It did NOT convert
# claude_bridge._drain_pending_claude_pings — the path every `send_ping` ping
# actually travels — so that function kept its hardcoded
# `os.environ["TELEGRAM_BOT_TOKEN"]` from the 2026-06-17 fold.
#
# The result was a capability that was configured, resolvable and inert: the
# operator created the dedicated bot, the router resolved it, and pings kept
# going to the trader chat. Every surface looked healthy, because delivery was
# working — into the wrong conversation. Measured: a ping enqueued 20:58:49Z on
# 2026-09-01 was confirmed delivered to the trader chat while the operator
# watched the new bot and correctly reported that nothing arrived.
#
# The transferable lesson, and the reason these are tests and not a comment:
# a change that introduces a router must be tested at EVERY consumer, because
# the un-converted one fails in the direction that still looks like success.


def _drive(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _queue_one(tmp_path, body="hello"):
    (tmp_path / "000000000001-normal.json").write_text(
        json.dumps({"priority": "normal", "body": body}), encoding="utf-8")


def _patched_bridge(tmp_path, monkeypatch):
    """Import the bridge with its inbox pointed at tmp_path, capturing sends."""
    from src.bot import claude_bridge
    monkeypatch.setattr(claude_bridge, "PENDING_CLAUDE_PINGS_DIR", tmp_path)

    calls = []

    def _fake_send(text, *, parse_mode=None, mirror_to_fcm=True,
                   bot_token=None, chat_id=None, reply_markup=None):
        calls.append({"text": text, "bot_token": bot_token, "chat_id": chat_id})
        return True

    import src.runtime.notify as notify
    monkeypatch.setattr(notify, "send_telegram_direct", _fake_send)
    return claude_bridge, calls


def test_the_bridge_drain_uses_the_DEDICATED_bot_when_one_is_set(tmp_path, monkeypatch):
    """The regression that shipped: the drain ignored the dedicated token."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "trader-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_SECRET", "claude-token")
    monkeypatch.delenv("TELEGRAM_CLAUDE_CHAT_ID", raising=False)

    bridge, calls = _patched_bridge(tmp_path, monkeypatch)
    _queue_one(tmp_path)
    _drive(bridge._drain_pending_claude_pings(None))

    assert len(calls) == 1, "the ping must be sent exactly once"
    assert calls[0]["bot_token"] == "claude-token", (
        "the drain must send via the DEDICATED Claude bot, not the trader bot")
    # A DM shares the operator's chat id by construction — that is the normal,
    # correct state and must not read as a gap (see Route.isolated).
    assert calls[0]["chat_id"] == "111"
    assert not list(tmp_path.glob("*.json")), "a delivered ping is removed"


def test_the_bridge_drain_falls_back_to_the_trader_bot_never_to_silence(
        tmp_path, monkeypatch):
    """⚠️ The safety argument for changing a delivery path at all.

    On a VM with no dedicated token this must be byte-for-byte the previous
    behaviour. The change may cost SEPARATION; it must never cost the ping.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "trader-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.delenv("TELEGRAM_CLAUDE_BOT_SECRET", raising=False)
    monkeypatch.delenv("TELEGRAM_CLAUDE_CHAT_ID", raising=False)

    bridge, calls = _patched_bridge(tmp_path, monkeypatch)
    _queue_one(tmp_path)
    _drive(bridge._drain_pending_claude_pings(None))

    assert len(calls) == 1, "an unseparated route still DELIVERS"
    assert calls[0]["bot_token"] == "trader-token"
    assert not claude_route().isolated, "and it correctly reports as not isolated"


def test_the_bridge_drain_honours_a_dedicated_chat(tmp_path, monkeypatch):
    """Without a chat override the route's chat half is decorative.

    send_telegram_direct read TELEGRAM_CHAT_ID from the environment, so a route
    could name its own conversation and never reach it: the token picked the
    BOT and the environment silently picked the CHAT.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "trader-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setenv("TELEGRAM_CLAUDE_BOT_SECRET", "claude-token")
    monkeypatch.setenv("TELEGRAM_CLAUDE_CHAT_ID", "999")

    bridge, calls = _patched_bridge(tmp_path, monkeypatch)
    _queue_one(tmp_path)
    _drive(bridge._drain_pending_claude_pings(None))

    assert calls[0]["chat_id"] == "999", "the route's own chat must be honoured"
    assert claude_route().targets_own_chat is True
