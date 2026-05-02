"""Telegram <-> Claude API bridge.

Long-lived process that listens for Telegram messages from an authorized
chat ID and forwards them to Claude via the Anthropic API. Conversation
history is kept per-chat in memory (resets on restart).

Run as a systemd service (deploy/ict-claude-bridge.service). Required env:
  TELEGRAM_CLAUDE_BOT_TOKEN  Telegram bot token (separate from main bot)
  ANTHROPIC_API_KEY          Anthropic API key
  TELEGRAM_CHAT_ID           Operator's Telegram chat ID (already in .env)

Optional:
  CLAUDE_MODEL               Defaults to claude-opus-4-7
  LOG_LEVEL                  Defaults to INFO
"""
from __future__ import annotations

import html
import logging
import os
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque, Dict, List

import anthropic
from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.bot import recurring_dispatch

load_dotenv()

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_CLAUDE_BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-7")
MAX_HISTORY = 40
MAX_TOKENS = 4096
TG_MAX_LEN = 4000  # Telegram hard limit is 4096; leave headroom

# Repo root resolved relative to this file: src/bot/claude_bridge.py → repo
REPO_ROOT = Path(__file__).resolve().parents[2]

SYSTEM_PROMPT = (
    "You are a helpful assistant connected to the operator's Telegram. "
    "The operator runs an algorithmic trading bot ('ict-trading-bot'). "
    "Keep responses concise and Telegram-friendly. Prefer short, direct "
    "answers. Avoid heavy markdown formatting; Telegram renders plain "
    "text best."
)

_history: Dict[int, Deque[dict]] = defaultdict(lambda: deque(maxlen=MAX_HISTORY))
_anthropic = anthropic.Anthropic()


def _is_authorized(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.id == ALLOWED_CHAT_ID


def _split(text: str, size: int) -> List[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


async def start_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        f"Claude bridge online (model={MODEL}). Send any message to chat. "
        "/reset clears history. /model shows the current model."
    )


async def reset_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    _history[update.effective_chat.id].clear()
    await update.message.reply_text("Conversation history cleared.")


async def model_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    history = _history[update.effective_chat.id]
    await update.message.reply_text(
        f"Model: {MODEL}\nTurns retained: {len(history)}/{MAX_HISTORY}"
    )


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        logger.warning(
            "Ignored message from unauthorized chat %s",
            getattr(update.effective_chat, "id", None),
        )
        return

    chat_id = update.effective_chat.id
    user_text = update.message.text or ""
    if not user_text.strip():
        return

    history = _history[chat_id]
    history.append({"role": "user", "content": user_text})

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    try:
        response = _anthropic.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            cache_control={"type": "ephemeral"},
            messages=list(history),
        )
    except anthropic.APIError as exc:
        logger.exception("Anthropic API call failed")
        history.pop()
        await update.message.reply_text(f"API error: {exc}")
        return

    reply_text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip() or "(empty response)"

    history.append({"role": "assistant", "content": reply_text})

    usage = response.usage
    logger.info(
        "chat_id=%s turns=%s in=%s out=%s cache_read=%s cache_write=%s",
        chat_id,
        len(history),
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_input_tokens,
        usage.cache_creation_input_tokens,
    )

    for chunk in _split(reply_text, TG_MAX_LEN):
        await update.message.reply_text(chunk)


def _format_starter_reply(label: str, prompt: str, triggered_at: str) -> str:
    # HTML mode: wrap the prompt in <pre><code> so Telegram renders a
    # monospace block with a one-tap "copy" affordance on mobile clients.
    # html.escape() is mandatory — Telegram's HTML parser rejects bare
    # &/</> in the body even outside the code block.
    safe_label = html.escape(label)
    safe_at = html.escape(triggered_at)
    safe_prompt = html.escape(prompt)
    return (
        f"🔧 {safe_label} session queued at {safe_at}\n\n"
        f"Open a new Claude Code session and tap-to-copy:\n\n"
        f"<pre><code>{safe_prompt}</code></pre>"
    )


async def cmd_audit(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    entry = recurring_dispatch.log_trigger(REPO_ROOT, "audit")
    prompt = recurring_dispatch.build_starter_prompt("audit")
    await update.message.reply_text(
        _format_starter_reply("Hardening", prompt, entry["triggered_at"]),
        parse_mode="HTML",
    )


async def cmd_improve_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    args = context.args or []
    strategy = args[0] if args else None
    entry = recurring_dispatch.log_trigger(
        REPO_ROOT, "improve_strategy", args=args
    )
    prompt = recurring_dispatch.build_starter_prompt(
        "improve_strategy", strategy=strategy
    )
    label = (
        f"Strategy Improvement ({strategy})"
        if strategy
        else "Strategy Improvement"
    )
    await update.message.reply_text(
        _format_starter_reply(label, prompt, entry["triggered_at"]),
        parse_mode="HTML",
    )


async def cmd_train_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    args = context.args or []
    strategy = args[0] if args else None
    entry = recurring_dispatch.log_trigger(
        REPO_ROOT, "train_model", args=args
    )
    prompt = recurring_dispatch.build_starter_prompt(
        "train_model", strategy=strategy
    )
    label = (
        f"Model Training ({strategy})" if strategy else "Model Training"
    )
    await update.message.reply_text(
        _format_starter_reply(label, prompt, entry["triggered_at"]),
        parse_mode="HTML",
    )


async def cmd_roadmap(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the roadmap-status block.

    S-031 PR5 (architecture-audit-2026-05-02 P1-6): file read +
    rendering moved to ``processor.get_roadmap_summary``.
    """
    if not _is_authorized(update):
        return
    from src.ui import processor
    summary = processor.get_roadmap_summary()
    await update.message.reply_text(summary)


# Static schedule of automations configured in claude.ai/code. The full
# setup spec (form values + cron rationale) lives in
# docs/claude/web-automations.md; this command is a quick reminder of
# what's running in the cloud sandbox so the operator knows when to
# expect each ping.
WEB_AUTOMATIONS = (
    ("Hardening audit",      "every other day at 06:00 UTC", "0 6 1-31/2 * *"),
    ("Strategy improvement", "Mondays at 06:00 UTC",         "0 6 * * 1"),
    ("Model training",       "Thursdays at 06:00 UTC",       "0 6 * * 4"),
)


async def cmd_schedules(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    lines = ["📆 Cloud automations (claude.ai/code)", ""]
    for name, cadence, cron in WEB_AUTOMATIONS:
        lines.append(f"• {name} — {cadence}  ({cron})")
    lines.append("")
    lines.append("Setup: docs/claude/web-automations.md")
    lines.append("Manual triggers: /audit /improve_strategy /train_model")
    await update.message.reply_text("\n".join(lines))


BOT_COMMANDS: List[BotCommand] = [
    BotCommand("start", "Show help"),
    BotCommand("reset", "Clear conversation history"),
    BotCommand("model", "Show current model + history depth"),
    BotCommand("audit", "Trigger a recurring hardening session"),
    BotCommand("improve_strategy", "Trigger a strategy improvement session: /improve_strategy [strategy]"),
    BotCommand("train_model", "Trigger a model training session: /train_model [strategy]"),
    BotCommand("roadmap", "Show current roadmap status"),
    BotCommand("schedules", "Show cloud automation schedule"),
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
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("model", model_cmd))
    app.add_handler(CommandHandler("audit", cmd_audit))
    app.add_handler(CommandHandler("improve_strategy", cmd_improve_strategy))
    app.add_handler(CommandHandler("train_model", cmd_train_model))
    app.add_handler(CommandHandler("roadmap", cmd_roadmap))
    app.add_handler(CommandHandler("schedules", cmd_schedules))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    logger.info(
        "Claude bridge starting (model=%s, allowed_chat=%s)",
        MODEL,
        ALLOWED_CHAT_ID,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
