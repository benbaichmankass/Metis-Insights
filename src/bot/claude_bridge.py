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

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.prop.prop_expiry_prompt import EXPIRY_CB_PREFIX, handle_expiry_callback
from src.prop.telegram_commands import REPORT_PROMPT, USAGE
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


# ── prop menu ──────────────────────────────────────────────────────────
# This bot is the **prop-account bot** (TELEGRAM_CLAUDE_BOT_TOKEN — the channel
# prop tickets are emitted to), so beyond delivering Claude's updates it now
# carries the prop report-back surface: a menu with the executor-assistant
# prompt + a format reminder, and a free-text handler that ingests a typed
# report-back command. The Claude update delivery is unchanged.
CB_PROP_PROMPT = "prop:prompt"
CB_PROP_HELP = "prop:help"


def _prop_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Prop report prompt", callback_data=CB_PROP_PROMPT)],
        [InlineKeyboardButton("❓ Report format", callback_data=CB_PROP_HELP)],
    ])


_MENU_TEXT = (
    "Prop bot — report a Breakout trade with one line.\n"
    "• Tap “📋 Prop report prompt” for the block to give your executor "
    "assistant, then paste its reply back here to log the trade.\n"
    "• Or just type it: close ETHUSD 2950 +80 tp · skip ETHUSD · bal 5040 5010\n"
    "• Or send a screenshot of the Position or account screen and I'll read it.\n"
    "(I also post Claude's sprint / review / system updates here.)"
)


async def start_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update) or update.message is None:
        return
    await update.message.reply_text(_MENU_TEXT, reply_markup=_prop_menu_keyboard())


# /menu is an alias for /start.
menu_cmd = start_cmd


async def testexpiry_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """`/testexpiry` — send one Yes/No expiry prompt for a throwaway test ticket.

    Operator verification of the live button round-trip: the prompt's buttons
    fire ``propexp:*`` callbacks handled by :func:`on_callback`. Clicking Yes/No
    mutates only the test ticket, never a real prop position. The DB write runs
    off the event loop so polling never stalls."""
    if not _is_authorized(update) or update.message is None:
        return
    from src.prop.prop_expiry_prompt import send_test_prompt

    try:
        ticket_id = await asyncio.to_thread(send_test_prompt)
    except Exception as exc:  # noqa: BLE001 — a test bug must never kill the bot
        logger.warning("testexpiry: send_test_prompt failed: %s", exc)
        ticket_id = None
    if ticket_id:
        await update.message.reply_text(
            f"🧪 Sent a test expiry prompt ({ticket_id}). Tap ✅/❌ above to try "
            "the flow — it only touches this throwaway ticket.")
    else:
        await update.message.reply_text(
            "⚠ Couldn't send the test prompt (check the prop bot token / logs).")


async def on_callback(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Menu button handler — the prompt + the format reminder."""
    query = update.callback_query
    if query is None:
        return
    if not _is_authorized(update):
        await query.answer("Unauthorised", show_alert=True)
        return
    await query.answer()
    data = query.data or ""
    # Plain text (no parse_mode) — REPORT_PROMPT/USAGE carry <SYMBOL>/<...>
    # placeholders that an HTML parse_mode would reject as bad entities.
    if data == CB_PROP_PROMPT:
        await query.message.reply_text(REPORT_PROMPT)
    elif data == CB_PROP_HELP:
        await query.message.reply_text(USAGE)
    elif data.startswith(EXPIRY_CB_PREFIX + ":"):
        # Ticket-expiry Yes/No answer. The DB status flip is sync sqlite — run
        # it off the event loop so polling never stalls. On "Yes" we also send
        # the report prompt so the operator can paste the fill details.
        result = await asyncio.to_thread(handle_expiry_callback, data)
        if result is None:
            return
        try:
            await query.edit_message_text(result["ack"])
        except Exception:  # noqa: BLE001 — fall back to a fresh message
            await query.message.reply_text(result["ack"])
        if result.get("send_prompt"):
            await query.message.reply_text(REPORT_PROMPT)


async def _on_operator_message(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Free-text handler — the inbound half of the prop manual bridge.

    A typed line is tried as a prop report-back command (``close ETHUSD 2950 +80
    tp`` / ``skip …`` / ``bal …``). A recognised command is ingested via the same
    ``prop_report.ingest_report`` chokepoint the dashboard/REST path uses and we
    reply with a one-line ack — closing the bridge with no Claude/dashboard
    middle-man. Anything that isn't a prop command gets the menu hint. The DB
    ingest runs off the event loop (``to_thread``) so polling never stalls."""
    if not _is_authorized(update) or update.message is None:
        return
    text = update.message.text or ""
    try:
        from src.prop.telegram_report_handler import (
            default_prop_account,
            handle_command,
        )

        reply = await asyncio.to_thread(
            handle_command, text, default_account=default_prop_account())
    except Exception as exc:  # noqa: BLE001 — a handler bug must never kill the bot
        logger.warning("prop report handler failed: %s", exc)
        reply = None

    if reply is not None:
        await update.message.reply_text(reply)
        return

    await update.message.reply_text(
        "Not a prop command. Tap /menu for the report prompt, or type e.g. "
        "`close ETHUSD 2950 +80 tp`. (For Claude/ops, use GitHub or a new session.)"
    )


async def _on_operator_photo(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Screenshot handler — the image half of the prop manual bridge.

    The operator sends a photo of the Breakout/DXtrade terminal (a Position
    detail screen and/or the account/portfolio summary) and the bot vision-parses
    it into the same structured report(s) the text grammar produces, ingesting
    each through ``prop_report.ingest_report``. The download + LLM call + DB
    ingest all run off the event loop so polling never stalls. Best-effort: any
    failure replies with a readable hint instead of crashing the bot."""
    if not _is_authorized(update) or update.message is None:
        return
    photos = update.message.photo or []
    if not photos:
        return
    try:
        # photo sizes are ascending — the last is the highest resolution.
        tg_file = await photos[-1].get_file()
        image_bytes = bytes(await tg_file.download_as_bytearray())
    except Exception as exc:  # noqa: BLE001 — never kill the bot on a download hiccup
        logger.warning("prop screenshot download failed: %s", exc)
        await update.message.reply_text(
            "⚠ Couldn't download that image — try again, or type the report "
            "(e.g. `close ETHUSD 2950 +80 tp`).")
        return

    try:
        from src.prop.telegram_report_handler import (
            default_prop_account,
            handle_screenshot,
        )

        reply = await asyncio.to_thread(
            handle_screenshot, image_bytes, "image/jpeg",
            default_account=default_prop_account())
    except Exception as exc:  # noqa: BLE001 — a handler bug must never kill the bot
        logger.warning("prop screenshot handler failed: %s", exc)
        reply = "⚠ Couldn't read that screenshot — type the report instead."

    await update.message.reply_text(reply or "⚠ No report found in that image.")


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

            sent = send_telegram_direct(
                text, parse_mode=None, mirror_to_fcm=False,
                bot_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("claude ping inbox: trader-bot send failed for %s — %s", name, exc)
            continue   # leave file in place; retry next tick

        # Only delete on a CONFIRMED send. send_telegram_direct returns False
        # (no raise) when credentials are missing — pre-2026-06-23 that silent
        # skip was treated as success and the file was deleted, silently losing
        # the ping. Keep the file so a transient creds gap retries instead.
        if not sent:
            logger.warning(
                "claude ping inbox: send skipped (creds missing?) for %s — "
                "keeping for retry", name,
            )
            continue

        try:
            path.unlink()
        except OSError:
            pass


BOT_COMMANDS: List[BotCommand] = [
    BotCommand("start", "Prop menu (report prompt + format)"),
    BotCommand("menu", "Prop menu (report prompt + format)"),
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
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("testexpiry", testexpiry_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_operator_message))
    app.add_handler(MessageHandler(filters.PHOTO, _on_operator_photo))
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
