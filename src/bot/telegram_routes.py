"""ONE owner for "which Telegram bot and chat does this message go to?".

Before this module the answer lived in three places: an inline resolver in
``prop/breakout_notify.py``, a bare ``os.environ["TELEGRAM_CLAUDE_BOT_TOKEN"]``
in ``bot/claude_bridge.py``, and a chat-id read in ``bot/cloud_notifier.py``.
Adding a third destination to that shape would have made four. The repo's rule
is that a vocabulary gets ONE owner; two copies of a resolution order is exactly
how they drift.

⚠️ THE VARIABLE NAMED FOR CLAUDE IS THE PROP BOT. Historically
``TELEGRAM_CLAUDE_BOT_TOKEN`` was repurposed to serve the PROP channel, and
``ict-claude-bridge.service`` is the PROP bridge. That trap is why this module
exists and why every route below is named for what it DOES, never for the
variable that happens to feed it.

⚠️ CORRECTED 2026-09-01 BY THE OPERATOR, and the correction is load-bearing.
This module first said: *"a new bot does not reduce noise — a new destination
does; a different token posting to the same chat_id lands in the same chat."*
**That is true of a GROUP or CHANNEL and FALSE of a DM, which is what this system
uses.** ``TELEGRAM_CHAT_ID`` is the OPERATOR's id (see ``claude_bridge.py``,
where it is the allow-list of who may talk to the bot), and in Telegram a private
chat's ``chat.id`` IS the user's id — so it is necessarily identical for every
bot that DMs them. The existing bots share it for that reason, not by mistake.

So in a DM the SEPARATION COMES FROM THE TOKEN: a different bot is a different
conversation in the operator's app, separately readable and separately mutable,
at the same numeric chat id. A per-route chat id is only needed to target a
GROUP or CHANNEL instead, which is why it stays optional below rather than
required.

Getting this backwards would have sent the operator to create a channel and
chase a chat id for a problem the token already solves.

⚠️ NEVER LOG A TOKEN. ``describe()`` reports the NAME of the variable that
answered and never its value; that is what makes a route auditable without
putting a credential in a log line.

THREE STATES PER ROUTE, NEVER COLLAPSED
---------------------------------------
``dedicated``  the route's own variable answered — the intended path.
``fallback``   a shared/legacy variable answered. The message WILL be delivered,
               to somewhere less specific. Distinct from `dedicated` because
               "prop tickets reached the operator" and "prop tickets reached the
               PROP channel" are different facts, and today they are
               indistinguishable from outside.
``unset``      nothing answered. The caller must not send blind.

That distinction is not decorative. On 2026-06-14 neither the prop nor the
claude token carried over the Ampere cutover; the resolver returned None, the
sender silently skipped, and prop tickets were journaled but never delivered for
weeks. The `fallback` rung was added to stop that — but a fallback that cannot
be SEEN just relocates the silence, which is what this module fixes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Sequence

#: PROP tickets. `TELEGRAM_PROP_TRADE_BOT` is the operator's 2026-09-01 name,
#: chosen to reflect actual use; `TELEGRAM_PROP_BOT_TOKEN` is the name the code
#: already had; `TELEGRAM_CLAUDE_BOT_TOKEN` is the legacy repurposed one and is
#: kept ONLY so a mid-transition VM (whose .env still holds it) does not lose
#: prop delivery the moment the Actions secret is renamed.
_PROP_TOKEN_ORDER = (
    "TELEGRAM_PROP_TRADE_BOT",
    "TELEGRAM_PROP_BOT_TOKEN",
    "TELEGRAM_CLAUDE_BOT_TOKEN",
)

#: CLAUDE operational pings. `TELEGRAM_CLAUDE_BOT_SECRET` is the operator's
#: 2026-09-01 name for the NEW dedicated bot.
#:
#: ⚠️ `TELEGRAM_CLAUDE_BOT_TOKEN` is deliberately NOT in this order, despite its
#: name. It feeds the PROP bot. Including it here would route Claude pings into
#: the prop channel — the exact confusion this module was written to end.
_CLAUDE_TOKEN_ORDER = ("TELEGRAM_CLAUDE_BOT_SECRET",)

#: The shared trader bot, and the shared operator chat.
_TRADER_TOKEN = "TELEGRAM_BOT_TOKEN"
_SHARED_CHAT = "TELEGRAM_CHAT_ID"

#: Per-route chat ids. Unset → the shared chat, which is a `fallback`.
_PROP_CHAT_ORDER = ("TELEGRAM_PROP_CHAT_ID",)
_CLAUDE_CHAT_ORDER = ("TELEGRAM_CLAUDE_CHAT_ID",)

DEDICATED = "dedicated"
FALLBACK = "fallback"
UNSET = "unset"


@dataclass(frozen=True)
class Route:
    """Where one kind of message goes, and how confidently.

    ``token``/``chat_id`` are the values to send with. ``token_from`` and
    ``chat_from`` are the variable NAMES that answered — never the values.
    """
    name: str
    token: Optional[str]
    token_from: Optional[str]
    token_state: str
    chat_id: Optional[str]
    chat_from: Optional[str]
    chat_state: str

    @property
    def deliverable(self) -> bool:
        """Can this route send at all? A token with no chat cannot."""
        return bool(self.token) and bool(self.chat_id)

    @property
    def isolated(self) -> bool:
        """Does this route land in its OWN conversation?

        ⚠️ KEYED ON THE TOKEN ALONE, and that is the 2026-09-01 correction. This
        first required ``chat_state == DEDICATED`` too, which encoded the wrong
        model: at a DM the chat id is the OPERATOR's and is shared by every bot
        by construction, so requiring a dedicated one would have reported a
        correctly-separated route as un-separated forever — and sent someone to
        create a channel to satisfy a condition that cannot hold for a DM.

        A dedicated ``chat_id`` still MATTERS; it just answers a different
        question (which group/channel), not this one. Read ``targets_own_chat``
        for that.
        """
        return self.token_state == DEDICATED

    @property
    def targets_own_chat(self) -> bool:
        """Does this route point at its own GROUP/CHANNEL rather than the DM?

        Deliberately separate from ``isolated``: ``False`` is the normal, correct
        state for a DM route and must not read as a gap.
        """
        return self.chat_state == DEDICATED

    def describe(self) -> str:
        return (f"{self.name}: token={self.token_from or '(none)'}"
                f"[{self.token_state}] chat={self.chat_from or '(none)'}"
                f"[{self.chat_state}] deliverable={self.deliverable} "
                f"isolated={self.isolated} own_chat={self.targets_own_chat}")


def _first(names: Sequence[str]) -> tuple[Optional[str], Optional[str]]:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v, n
    return None, None


def _resolve(name: str, own: Sequence[str], shared: Sequence[str]) -> Route:
    tok, tok_from = _first(own)
    tok_state = DEDICATED if tok else UNSET
    if not tok:
        tok, tok_from = _first([_TRADER_TOKEN])
        tok_state = FALLBACK if tok else UNSET

    chat, chat_from = _first(shared)
    chat_state = DEDICATED if chat else UNSET
    if not chat:
        chat, chat_from = _first([_SHARED_CHAT])
        chat_state = FALLBACK if chat else UNSET

    return Route(name=name, token=tok, token_from=tok_from, token_state=tok_state,
                 chat_id=chat, chat_from=chat_from, chat_state=chat_state)


def prop_route() -> Route:
    """Prop tickets → the prop bot/channel, else the shared trader bot/chat."""
    return _resolve("prop", _PROP_TOKEN_ORDER, _PROP_CHAT_ORDER)


def claude_route() -> Route:
    """Claude operational pings → the dedicated bot/channel, else shared.

    ⚠️ A `fallback` here is the CURRENT production behaviour, not a fault: since
    2026-06-22 Claude pings have been drained by the trader bot into the shared
    chat. That is precisely the noise the dedicated bot exists to remove — so
    `fallback` means "working, in the wrong place", and only `isolated` means the
    separation actually happened.
    """
    return _resolve("claude", _CLAUDE_TOKEN_ORDER, _CLAUDE_CHAT_ORDER)


def _self_test() -> int:
    ok = True

    def check(label, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  self-test ({label}): {'PASS' if good else f'FAIL got={got!r}'}")

    saved = {k: os.environ.get(k) for k in (
        list(_PROP_TOKEN_ORDER) + list(_CLAUDE_TOKEN_ORDER) +
        [_TRADER_TOKEN, _SHARED_CHAT] + list(_PROP_CHAT_ORDER) +
        list(_CLAUDE_CHAT_ORDER))}

    def setenv(**kw):
        for k in saved:
            os.environ.pop(k, None)
        for k, v in kw.items():
            if v is not None:
                os.environ[k] = v

    try:
        # Nothing set at all.
        setenv()
        r = claude_route()
        check("nothing set -> unset, and NOT deliverable", (r.token_state, r.deliverable),
              (UNSET, False))

        # The CURRENT production shape: only the trader bot + shared chat.
        setenv(TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c")
        r = claude_route()
        check("trader-only -> fallback, deliverable", (r.token_state, r.deliverable),
              (FALLBACK, True))
        check("⚠️ trader-only is NOT isolated — delivered, wrong place", r.isolated, False)

        # The new bot, but no new chat: the trap the operator was warned about.
        setenv(TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c",
               TELEGRAM_CLAUDE_BOT_SECRET="new")
        r = claude_route()
        check("new BOT alone -> token dedicated", r.token_state, DEDICATED)
        # ⚠️ THIS ASSERTION WAS INVERTED until the operator corrected the model.
        # It read: "NEW BOT + SHARED CHAT IS NOT ISOLATED — the whole point".
        # At a DM the shared chat id is the OPERATOR's and every bot uses it, so
        # a new bot IS a new conversation. The old assertion would have pinned
        # the wrong behaviour permanently.
        check("a new BOT at the shared DM id IS isolated — separate conversation",
              r.isolated, True)
        check("...and it is honestly NOT targeting its own group/channel",
              r.targets_own_chat, False)

        # Both halves.
        setenv(TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c",
               TELEGRAM_CLAUDE_BOT_SECRET="new", TELEGRAM_CLAUDE_CHAT_ID="cc")
        r = claude_route()
        check("bot + own chat -> isolated AND targeting its own chat",
              (r.isolated, r.targets_own_chat), (True, True))
        check("and it reports WHICH vars answered, never the values",
              (r.token_from, r.chat_from),
              ("TELEGRAM_CLAUDE_BOT_SECRET", "TELEGRAM_CLAUDE_CHAT_ID"))
        check("describe() never contains a token value", "new" in r.describe(), False)

        # ⚠️ The confusion this module exists to end.
        setenv(TELEGRAM_CLAUDE_BOT_TOKEN="propbot", TELEGRAM_CHAT_ID="c")
        check("TELEGRAM_CLAUDE_BOT_TOKEN feeds PROP, not claude",
              prop_route().token_from, "TELEGRAM_CLAUDE_BOT_TOKEN")
        # ⚠️ The assertion here first read `== "TELEGRAM_BOT_TOKEN"` and FAILED
        # with None — correctly. No trader token is set in this case, so there is
        # nothing to fall back to. The code was right and the test was wrong; the
        # claim worth pinning is the NEGATIVE one, so it is asserted directly.
        check("...and NEVER claude — it would route pings to the prop channel",
              claude_route().token_from, None)
        check("the prop var can never answer the claude route, whatever else is set",
              "TELEGRAM_CLAUDE_BOT_TOKEN" in _CLAUDE_TOKEN_ORDER, False)
        # Positive control: with a trader token present the claude route DOES
        # fall back — so the None above is a real absence, not a dead resolver.
        os.environ["TELEGRAM_BOT_TOKEN"] = "t"
        check("positive control — claude falls back to the trader bot when it exists",
              claude_route().token_from, "TELEGRAM_BOT_TOKEN")
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)

        # The operator's new name wins over both older prop names.
        setenv(TELEGRAM_PROP_TRADE_BOT="new", TELEGRAM_PROP_BOT_TOKEN="old",
               TELEGRAM_CLAUDE_BOT_TOKEN="older", TELEGRAM_CHAT_ID="c")
        check("operator's TELEGRAM_PROP_TRADE_BOT wins",
              prop_route().token_from, "TELEGRAM_PROP_TRADE_BOT")

        # A mid-transition VM keeps working on the legacy name alone.
        setenv(TELEGRAM_CLAUDE_BOT_TOKEN="older", TELEGRAM_CHAT_ID="c")
        check("legacy-only VM still delivers prop (no cutover gap)",
              prop_route().deliverable, True)

        # Whitespace-only is not a value.
        setenv(TELEGRAM_CLAUDE_BOT_SECRET="   ", TELEGRAM_BOT_TOKEN="t",
               TELEGRAM_CHAT_ID="c")
        check("a whitespace-only secret is UNSET, not a token",
              claude_route().token_state, FALLBACK)

        # A token with no chat cannot send.
        setenv(TELEGRAM_CLAUDE_BOT_SECRET="new")
        check("token but NO chat -> not deliverable", claude_route().deliverable, False)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    print("telegram-routes self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_self_test())
