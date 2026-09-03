"""Two operator-pull commands on the polled Telegram bot: ``/status`` + ``/decisions``.

The operator's ask, 2026-09-02:

    "I wanna also add some commands to that channel. One should be a **status**
     command where I get the status update to the channel, like, with the
     checklist and everything. Another one for **decisions** — if there's any
     decisions that are still waiting for me that aren't answered so they can
     all pop up at once. In case I don't get them as they come in."

This module is the WIRING layer only. The two readouts live where their data
lives -- `src.runtime.manager_status` and `src.runtime.telegram_decisions` --
so `/decisions` reuses the SAME prompt/keyboard builders the periodic
`sweep_work_decisions` job uses rather than a second copy of them.

**Why a separate module rather than more code in the bot that installs it:** it
follows the `install_comms_handlers(application, ...)` idiom the Telegram bots
already use, and it keeps the change at the call site to two lines -- an import
and a call. That cheapness is what made the fix below a move rather than a
rewrite.

⚠️ **WHERE THIS IS INSTALLED IS A CONSTRAINT, NOT A DETAIL (MI-92).** The sole
caller is `src.bot.claude_decision_bot` (`ict-claude-decision-bot.service`).
It is deliberately **NOT** installed on `src.bot.telegram_query_bot`, the
TRADER bot.

PR #10793 shipped it onto the trader bot on 2026-09-02 under its own title
*"HELD, must not reach the trader bot"*. It was auto-merged with no human
decision; the handlers went live at 18:59:03Z, and this module's own
`answerable_elsewhere` WARNING named the violation as it registered them. Still
live when re-observed at 2026-09-03T07:07:08Z. The operator's words:

    "that's supposed to be showing up in Cloudbot. Right? Not on the trader
     one, the decisions."

`tests/test_operator_commands.py` asserts by AST that `telegram_query_bot`
neither imports nor calls `install_operator_commands`. Do not re-add it there
without a recorded operator decision withdrawing the constraint.

**Destination.** Registration resolves
`telegram_decisions.answerable_route()` -- never a hardcoded token and never
`claude_route()`, which named a bot NO process polled when this was written. A
`/command` is delivered only to a bot something is POLLING, the same property
that makes a button live, so these commands ride whatever bot is genuinely
polled. On ClaudeBot the answerable route IS the polled token, so `route_state`
is `answerable_here` and a `/decisions` button is received by the very process
that sent it -- on the trader bot the same button was a cross-bot tap this
module had to warn about.

⚠️ **A route that does not resolve NEVER silently registers nothing.** That is
a `could_not_look`-shaped state: it is logged as a WARNING naming it, and the
handlers are still installed -- a command that is silently absent is
indistinguishable from a broken one, which is the failure this whole change
exists to prevent.

⚠️ **Both commands expose internal system state and this repo is PUBLIC**, so
the bot username is discoverable. Every handler gates on
`telegram_query_bot.is_authorised` -- the SAME chat-id check `callback_handler`
already uses, passed in rather than re-derived, because a second notion of
"authorised" is one that can drift from the first. An unrecognised chat gets a
refusal, never a status dump, and the refusal is logged.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

#: Shown in the bot's hamburger menu alongside the existing menu openers.
OPERATOR_COMMANDS = (
    ("status", "Manager status — checklist, recently done, next"),
    ("decisions", "Every decision still waiting on you"),
)


def _to_markup(raw: Optional[dict[str, Any]]) -> Optional[InlineKeyboardMarkup]:
    """Adapt the shared builder's raw Bot-API dict to a PTB markup object.

    A TRANSPORT adaptation only. `telegram_decisions.build_decision_keyboard`
    stays the single owner of every `callback_data` value -- this function must
    never construct one, or the 64-byte budget and the option key-digest scheme
    would have a second, drifting author.
    """
    if not raw:
        return None
    rows = []
    for row in raw.get("inline_keyboard") or []:
        rows.append([
            InlineKeyboardButton(b["text"], callback_data=b["callback_data"])
            for b in row if isinstance(b, dict)
        ])
    return InlineKeyboardMarkup(rows) if rows else None


async def _refuse(update: Update, command: str) -> None:
    chat = update.effective_chat
    logger.warning(
        "operator_commands: REFUSED /%s from unauthorised chat_id=%s",
        command, getattr(chat, "id", "(none)"),
    )
    if update.message is not None:
        await update.message.reply_text("⛔ Unauthorised.")


def build_handlers(
    is_authorised: Callable[[Update], bool],
) -> dict[str, Callable[[Update, ContextTypes.DEFAULT_TYPE], Any]]:
    """The two command coroutines, bound to the caller's auth predicate."""

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_authorised(update):
            await _refuse(update, "status")
            return
        from src.runtime.manager_status import build_status

        # Off the event loop: reading two JSON files and shelling three `git`
        # commands is blocking, and polling must never stall behind it.
        readout = await asyncio.to_thread(build_status)
        logger.info(
            "operator_commands: /status tree=%s checklist=%s messages=%d omissions=%d",
            readout.tree.state, readout.checklist_read,
            len(readout.messages), len(readout.omissions),
        )
        # ⚠️ THE PARSE MODE COMES FROM THE READOUT, never a literal here. The
        # expandable render is HTML and is escaped for it; the plain-text
        # packer's bodies are NOT escaped and would be mangled — or 400 the
        # whole reply — if sent as HTML. Carrying the mode with the text is
        # what makes it impossible to send one under the other's rules.
        for body in readout.messages:
            await update.message.reply_text(body, parse_mode=readout.parse_mode)

    async def cmd_decisions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_authorised(update):
            await _refuse(update, "decisions")
            return
        from src.runtime.telegram_decisions import build_on_demand_decisions

        # Blocking loopback HTTP -- same reason as above.
        outbound = await asyncio.to_thread(build_on_demand_decisions)
        logger.info("operator_commands: /decisions sending %d message(s)",
                    len(outbound))
        for text, keyboard in outbound:
            await update.message.reply_text(text, reply_markup=_to_markup(keyboard))

    return {"status": cmd_status, "decisions": cmd_decisions}


def install_operator_commands(
    application: Application,
    *,
    is_authorised: Callable[[Update], bool],
    polled_token: Optional[str] = None,
) -> dict[str, Any]:
    """Register ``/status`` and ``/decisions``. Returns a diagnostic dict.

    ``polled_token`` is the token THIS process polls. It is compared against
    ``answerable_route()`` so a divergence is reported rather than assumed
    away: the two coincide today, and MI-58 is deliberately changing which bot
    is polled.

    ``route_state`` is three values, never collapsed:
      ``answerable_here``  the answerable route IS the bot this process polls
      ``answerable_elsewhere``  a route resolved, but names a different bot
      ``could_not_look``   no answerable route resolved at all

    The handlers are registered in ALL THREE cases. Registering nothing on the
    third would make the command silently absent, which is exactly the state
    the operator cannot tell apart from a broken one.
    """
    from src.runtime.telegram_decisions import answerable_route

    try:
        route = answerable_route()
    except Exception as exc:  # noqa: BLE001 -- never block registration
        logger.warning("operator_commands: answerable_route() raised: %s", exc)
        route = None

    if route is None or not route.deliverable:
        state = "could_not_look"
        logger.warning(
            "operator_commands: no answerable bot resolved (%s) — registering "
            "/status and /decisions anyway on the bot this process polls, "
            "because a silently absent command is indistinguishable from a "
            "broken one. Decision buttons will be omitted until a route "
            "resolves.",
            route.note if route is not None else "answerable_route() raised",
        )
    elif polled_token and route.token and polled_token != route.token:
        state = "answerable_elsewhere"
        logger.warning(
            "operator_commands: the answerable route (%s) is NOT the bot this "
            "process polls — /status and /decisions are registered here, but a "
            "decision tap is received only by the process polling the "
            "answerable bot.",
            route.token_from,
        )
    else:
        state = "answerable_here"
        logger.info("operator_commands: %s", route.describe())

    handlers = build_handlers(is_authorised)
    for name, fn in handlers.items():
        application.add_handler(CommandHandler(name, fn))
    logger.info("operator_commands: registered /%s (route_state=%s)",
                ", /".join(handlers), state)
    return {"route_state": state, "registered": sorted(handlers)}


__all__ = [
    "OPERATOR_COMMANDS",
    "build_handlers",
    "install_operator_commands",
]
