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

import logging
import os
from collections import defaultdict, deque
from typing import Deque, Dict, List

import anthropic
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_CLAUDE_BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-7")
MAX_HISTORY = 40
MAX_TOKENS = 4096
TG_MAX_LEN = 4000  # Telegram hard limit is 4096; leave headroom

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


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("model", model_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    logger.info(
        "Claude bridge starting (model=%s, allowed_chat=%s)",
        MODEL,
        ALLOWED_CHAT_ID,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
