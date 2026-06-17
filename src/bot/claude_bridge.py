"""@claude_ict_comms_bot — one-way Claude → operator update channel.

This bot has exactly one job: deliver Claude's session updates to the
operator's Telegram, all in a **single thread**. It is **send-only by
design** — there is no freeform chat, no Anthropic API call, and no
session-trigger commands. Operator decisions flow back through GitHub
(PR comments, issue updates) or a new Claude session reading repo state,
never through this bot. (Overhaul 2026-05-24; the previous two-way
Anthropic-chat + /audit//train_model build was removed per operator
directive — see docs/claude/telegram-pings.md.)

What it delivers (drained from ``runtime_logs/pending_claude_pings/``):
  • sprint open / checkpoint / sprint close
  • health-review open / close
  • training-session open / close (+ results summary)
  • "waiting for operator input" pings
  • system-health snapshots
  • blocker / merge-review pings

The trading bot (@bict_trading_bot) keeps its OWN inbox
(``runtime_logs/pending_pings/``) for trade-execution alerts; the two
channels never share an inbox.

Run as a systemd service (deploy/ict-claude-bridge.service). Required env:
  TELEGRAM_CLAUDE_BOT_TOKEN   Telegram bot token (separate from main bot)
  TELEGRAM_CHAT_ID            Operator's Telegram chat ID

Optional:
  TELEGRAM_CLAUDE_THREAD_ID   Forum topic / message-thread id to pin every
                              message to ONE thread. Leave unset for a
                              normal (non-forum) chat. Set this when the
                              operator chat is a Telegram forum so updates
                              never scatter across topics.
  LOG_LEVEL                   Defaults to INFO
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from src.utils.paths import runtime_logs_dir

load_dotenv()

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_CLAUDE_BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])


def _resolve_thread_id() -> Optional[int]:
    """Optional forum topic id to pin every message to ONE thread.

    Unset / blank → ``None`` (normal chat; messages land in the single
    conversation). Set to an integer when the operator chat is a forum
    so updates never scatter across topics — the multi-thread bug this
    overhaul fixes.
    """
    raw = (os.environ.get("TELEGRAM_CLAUDE_THREAD_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "TELEGRAM_CLAUDE_THREAD_ID=%r is not an integer — ignoring", raw
        )
        return None


THREAD_ID = _resolve_thread_id()

# Repo root resolved relative to this file: src/bot/claude_bridge.py → repo
REPO_ROOT = Path(__file__).resolve().parents[2]

PENDING_CLAUDE_PINGS_DIR = runtime_logs_dir() / "pending_claude_pings"
CLAUDE_PING_DRAIN_INTERVAL_S = 5

_PRIORITY_ICONS: Dict[str, str] = {
    "urgent": "🚨 URGENT",
    "high":   "🔔",
    "normal": "ℹ️",
    "low":    "·",
}


def _is_authorized(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.id == ALLOWED_CHAT_ID


async def start_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "Claude update channel — one-way.\n"
        "I post sprint, health-review, training and system updates here, "
        "and ping you when I'm waiting on input.\n"
        "This channel doesn't take replies: respond on GitHub (PR/issue) "
        "or start a new Claude session."
    )


async def _one_way_notice(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Polite reply so the operator isn't left wondering why a typed
    message went unanswered. This bot is send-only — it never forwards
    operator text anywhere."""
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "This is a one-way update channel — I can't reply here. "
        "Use GitHub (PR/issue comment) or start a new Claude session."
    )


async def _drain_pending_claude_pings(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue task — scan the Claude inbox, send each, delete on success.

    Delivery goes to the **trader bot** (`@bict_trading_bot`) as of the 2026-06-17
    bot restructure — Claude's updates were folded off the comms bot (now the
    prop-account bot). A malformed JSON file is moved aside with a ``.broken``
    suffix; a send failure leaves the file in place to retry next tick. Files are
    sorted by name so the 12-digit numeric prefix preserves rough enqueue order.
    """
    try:
        PENDING_CLAUDE_PINGS_DIR.mkdir(parents=True, exist_ok=True)
        names = sorted(
            n.name for n in PENDING_CLAUDE_PINGS_DIR.iterdir()
            if n.name.endswith(".json") and not n.name.endswith(".tmp")
        )
    except OSError:
        return

    if not names:
        return

    for name in names:
        path = PENDING_CLAUDE_PINGS_DIR / name
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("claude ping inbox: malformed file %s — %s", name, exc)
            try:
                path.rename(path.with_suffix(path.suffix + ".broken"))
            except OSError:
                pass
            continue

        priority = str(payload.get("priority", "normal")).lower()
        body = str(payload.get("body", "")).strip()
        if not body:
            try:
                path.unlink()
            except OSError:
                pass
            continue

        prefix = _PRIORITY_ICONS.get(priority, _PRIORITY_ICONS["normal"])
        text = f"{prefix} {body}"

        # Bot restructure (2026-06-17): Claude's operational updates now deliver
        # via the TRADER bot (@bict_trading_bot, TELEGRAM_BOT_TOKEN) — folded off
        # the comms bot, which is being repurposed as the prop-account bot. The
        # trader bot uses no Claude thread, so the message lands in the operator's
        # main chat (the claude-thread pinning is intentionally dropped here).
        # Sent via the stdlib direct path so delivery no longer depends on this
        # service's own (prop-bot) Application; success → delete, failure → keep
        # for the next-tick retry (semantics unchanged).
        try:
            from src.runtime.notify import send_telegram_direct

            send_telegram_direct(
                text, parse_mode=None, mirror_to_fcm=False,
                bot_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("claude ping inbox: trader-bot send failed for %s — %s", name, exc)
            continue   # leave file in place; retry next tick

        try:
            path.unlink()
        except OSError:
            pass


BOT_COMMANDS: List[BotCommand] = [
    BotCommand("start", "What this channel is"),
]


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands(BOT_COMMANDS)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(_post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _one_way_notice))
    if app.job_queue is not None:
        app.job_queue.run_repeating(
            _drain_pending_claude_pings,
            interval=CLAUDE_PING_DRAIN_INTERVAL_S,
            first=CLAUDE_PING_DRAIN_INTERVAL_S,
            name="drain_pending_claude_pings",
        )
    logger.info(
        "Claude update channel starting (one-way; allowed_chat=%s, "
        "thread_id=%s, inbox=%s)",
        ALLOWED_CHAT_ID, THREAD_ID, PENDING_CLAUDE_PINGS_DIR,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
