"""The dedicated Claude bot's POLLING half — what makes a decision button work.

Operator, 2026-09-02: *"that's supposed to be showing up in Cloudbot. Right?
Not on the trader one, the decisions."*

WHY THIS IS A PROCESS AND NOT A ROUTE CHANGE
────────────────────────────────────────────
PR #10778 shipped the decision round-trip onto the TRADER bot, and that was
correct at the time for a reason worth restating rather than deleting:
**delivery and answerability are different properties.** A ``sendMessage``
needs only a token. An inline-keyboard **button** is inert unless a process is
POLLING that bot and has a ``CallbackQueryHandler`` registered — the tap emits
a ``callback_query`` update that nobody collects. Nothing errors.

Measured 2026-09-02: ``ict-telegram-bot.service`` polls ``TELEGRAM_BOT_TOKEN``,
``ict-claude-bridge.service`` polls the **prop** token despite its name, and
``TELEGRAM_CLAUDE_BOT_SECRET`` was polled by **nothing**. So simply re-pointing
``answerable_route()`` at the Claude bot would have shipped **dead buttons that
look healthy** — strictly worse than arriving in the wrong chat, because a
wrong channel is visible and an unreceived tap is not.

This module is the missing half. Once it runs, the Claude bot is polled, the
``wdec`` prefix has a handler, and the route may legitimately prefer it.

ONE OWNER FOR THE TAP
─────────────────────
The callback is dispatched to ``telegram_decisions.handle_decision_callback``
— the SAME function ``telegram_query_bot.callback_handler``'s ``wdec`` branch
calls. It is imported, never reimplemented. Two copies of the digest matching,
the collision refusal and the outcome vocabulary would drift, and the half that
drifted would be the half that records what a human decided.

Equally, the route stays the one owner of every REFUSAL: 400 unknown option,
400 empty submission, 409 already answered, 503 fail-closed write gate. This
bot TRANSLATES those for a human and re-implements none of them.

WHY ITS OWN SERVICE, AND NOT A SECOND ``Application`` IN THE TRADER BOT
───────────────────────────────────────────────────────────────────────
The cheaper option was a second ``Application`` inside
``src/bot/telegram_query_bot.py``, which already polls. It was rejected, and
the reasons are in the PR body; the two that belong next to the code are:

1. ``Application.run_polling()`` builds and OWNS an event loop, installs signal
   handlers, and blocks. Two of them do not compose — a second bot in that
   process means hand-rolling ``initialize()``/``start()``/``updater.start_polling()``
   for both and driving the shutdown yourself, i.e. rewriting the lifecycle of
   the process that owns the operator's KILL SWITCH and account-mode UI, for a
   feature unrelated to either. The Prime Directive makes that trade badly.
2. One process is one event loop. A stall in the decision channel would then be
   a stall in the kill switch. Isolation is the point, and it is what
   ``Restart=always`` on a separate unit buys.

⚠️ NEVER LOG A TOKEN. The banner names the VARIABLE that answered
(``TELEGRAM_CLAUDE_BOT_SECRET``), never its value. This repo is public.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from src.bot.operator_commands import OPERATOR_COMMANDS, install_operator_commands
from src.bot.telegram_routes import claude_route
from src.runtime.telegram_decisions import CB_PREFIX, handle_decision_callback
from src.runtime.telegram_poll_registry import (
    heartbeat_interval_seconds,
    log_poll_banner,
    record_poll,
)

load_dotenv()

logger = logging.getLogger(__name__)

SERVICE_NAME = "ict-claude-decision-bot"

#: Exit code for a CONFIGURATION problem (sysexits' EX_CONFIG). The unit maps it
#: to `RestartPreventExitStatus`, so a missing token stops the service in a
#: visible failed state instead of crash-looping every RestartSec forever.
#: Restarting cannot fix an unset variable, and 5,760 identical log lines a day
#: is the desensitised-alarm failure this repo files as a P1 in its own right.
EX_CONFIG = 78

#: The prefixes a tap on THIS bot will actually reach a handler for. Declared
#: once and used for BOTH the handler pattern and the poll claim, so the claim
#: can never say more than the handlers deliver.
#:
#: ⚠️ CALLBACK prefixes only — NOT slash commands. `/status` and `/decisions`
#: are `CommandHandler`s, which are not `callback_query` traffic, so adding
#: them below does not widen this tuple. Widening it would make the poll claim
#: assert a tap this bot does not handle, which is the exact failure the
#: registry exists to catch.
HANDLED_PREFIXES: tuple[str, ...] = (CB_PREFIX,)

#: This bot's operator-facing slash surface: `/start` plus the two operator
#: PULLS. A command absent from `set_my_commands` is one the operator has to
#: already know exists in order to use it, which is why the menu moved here
#: together with the handlers rather than being left behind on the trader bot.
_COMMAND_SURFACE: list[tuple[str, str]] = [
    ("start", "What this channel is"),
    *OPERATOR_COMMANDS,
]


def _allowed_chat_id() -> Optional[str]:
    return (os.environ.get("TELEGRAM_CHAT_ID") or "").strip() or None


def _is_authorised(update: Update) -> bool:
    """Only the operator may answer. An unset chat id authorises NOBODY.

    Fail-CLOSED, deliberately the opposite polarity to a read surface: this
    button records what a human decided, on a public-internet-reachable bot,
    so an unset variable must not open it to whoever finds the bot.
    """
    allowed = _allowed_chat_id()
    if not allowed:
        return False
    chat = update.effective_chat
    return chat is not None and str(chat.id) == allowed


async def start_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorised(update):
        return
    await update.message.reply_text(
        "Claude decision channel.\n\n"
        "Work decisions that are waiting on you arrive here as questions with "
        "buttons. Tapping one SUBMITS your answer — it is not committed until a "
        "committer writes it into the work object in the repo, and the "
        "confirmation says so."
    )


async def on_callback(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch a tap. Same handler as the trader bot's — never a fork."""
    query = update.callback_query
    await query.answer()
    if not _is_authorised(update):
        await query.edit_message_text("⛔ Unauthorised.")
        return

    raw = query.data or ""
    if not raw.startswith(f"{CB_PREFIX}:"):
        # Not ours. Say nothing rather than editing away someone else's message.
        return
    try:
        # Off the event loop: the handler does blocking loopback HTTP, and
        # polling must never stall behind it.
        result = await asyncio.to_thread(handle_decision_callback, raw)
    except Exception as exc:  # noqa: BLE001 — a tap bug must not kill the bot
        logger.warning("claude_decision_bot: callback %s failed: %s", raw, exc)
        await query.edit_message_text(f"⚠️ Action failed: {exc}")
        return
    if result is not None:
        await query.edit_message_text(result["reply"])


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Must run after basicConfig — the filter attaches to the root handler.
    # Without it httpx logs "POST https://api.telegram.org/bot<TOKEN>/getUpdates"
    # into journald, which is the proven leak vector behind
    # BL-20260801-TELEGRAM-BOT-TOKEN-COMPROMISE on this PUBLIC repo.
    from src.utils.log_redact import (
        assert_telegram_token_shape,
        install_redacting_filter,
        suppress_httpx_logging,
    )

    install_redacting_filter()
    suppress_httpx_logging()

    route = claude_route()
    # ⚠️ `isolated`, NOT `deliverable`. A deliverable claude_route can be the
    # SHARED TRADER TOKEN wearing this route's name — polling that here would
    # open a SECOND poller on the trader bot, and two pollers on one token
    # fight over getUpdates (Telegram serves each update once, so taps would be
    # delivered to one process at random). Refuse instead.
    if not route.isolated:
        logger.error(
            f"{SERVICE_NAME}: no DEDICATED Claude bot token. This service exists "
            "to make TELEGRAM_CLAUDE_BOT_SECRET answerable; without it there is "
            "nothing distinct to poll, and falling back to TELEGRAM_BOT_TOKEN "
            "would put a second poller on the trader bot. Set "
            "TELEGRAM_CLAUDE_BOT_SECRET in the VM .env (an operator action). "
            "⚠️ Do NOT use TELEGRAM_CLAUDE_BOT_TOKEN — despite its name it "
            f"drives the PROP bot. Route: {route.describe()}"
        )
        raise SystemExit(EX_CONFIG)
    if not route.chat_id:
        logger.error(
            "%s: TELEGRAM_CHAT_ID is unset, so no tap could be authorised and "
            "no prompt could be sent. Route: %s", SERVICE_NAME, route.describe(),
        )
        raise SystemExit(EX_CONFIG)

    # Fail secret-free on a malformed token BEFORE PTB can echo it in an
    # InvalidToken traceback (the 2026-08-01 half-paste class).
    assert_telegram_token_shape(route.token, "TELEGRAM_CLAUDE_BOT_SECRET")

    app = Application.builder().token(route.token).build()
    app.add_handler(CommandHandler("start", start_cmd))

    # ── the two operator PULLS: `/status` and `/decisions` ───────────────────
    # They live HERE, not on the trader bot. PR #10793 registered them in
    # `telegram_query_bot.py` against its own title ("HELD, must not reach the
    # trader bot") and its own WARNING (`route_state=answerable_elsewhere`);
    # the operator asked for "Cloudbot … not the trader one".
    #
    # This is also simply where they belong. `answerable_route()` resolves to
    # TELEGRAM_CLAUDE_BOT_SECRET — the token THIS process polls — so the route
    # state here is `answerable_here`, and a decision button `/decisions`
    # renders is received by `on_callback` below, in this same process. On the
    # trader bot the same button was a cross-bot tap the code had to warn about.
    #
    # Registered BEFORE the CallbackQueryHandler for the same reason the trader
    # bot orders `install_comms_handlers` first: handler order decides who wins.
    install_operator_commands(
        app,
        is_authorised=_is_authorised,
        polled_token=route.token,
    )

    # Pattern-scoped: this bot claims the `wdec` prefix and nothing else, so the
    # poll claim it registers below is true of exactly what it handles.
    app.add_handler(CallbackQueryHandler(on_callback, pattern=rf"^{CB_PREFIX}:"))

    async def _post_init(a: Application) -> None:
        # The hamburger menu Telegram shows in this bot's composer.
        await a.bot.set_my_commands(
            [BotCommand(name, desc) for name, desc in _COMMAND_SURFACE]
        )

    app.post_init = _post_init

    # ── the poll claim ───────────────────────────────────────────────────────
    # Registered AFTER the handlers are on the Application, never before: a
    # claim made earlier would be true of the intent rather than of the
    # process, which is the whole failure mode this registry exists to catch.
    async def _register_poll(_ctx) -> None:
        await asyncio.to_thread(
            record_poll, route.token_from, HANDLED_PREFIXES, service=SERVICE_NAME,
        )

    if app.job_queue is not None:
        # A HEARTBEAT, not a one-shot: a process that dies, hangs or is stopped
        # must have its claim EXPIRE on its own. A claim written once and never
        # refreshed is an assertion that outlives the thing it asserts.
        app.job_queue.run_repeating(
            _register_poll,
            interval=heartbeat_interval_seconds(),
            first=0,
            name="telegram_poll_heartbeat",
        )
    else:
        # No job queue: claim once so the route is not blinded, and say plainly
        # that the claim can no longer expire — a stale claim is exactly what
        # would ship dead buttons, so this must be loud rather than silent.
        record_poll(route.token_from, HANDLED_PREFIXES, service=SERVICE_NAME)
        logger.error(
            "%s: JobQueue unavailable — the poll claim was written ONCE and will "
            "NOT be refreshed, so it cannot expire if this process dies and the "
            "decision sweep may keep sending buttons here after it has. Install "
            "python-telegram-bot[job-queue].", SERVICE_NAME,
        )

    log_poll_banner(
        route.token_from, HANDLED_PREFIXES, service=SERVICE_NAME, log=logger,
    )
    logger.info(
        "%s starting — decision taps on this bot reach handle_decision_callback. "
        "Route: %s", SERVICE_NAME, route.describe(),
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
