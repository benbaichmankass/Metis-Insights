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
