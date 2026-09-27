import logging
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class AlertManager:
    def __init__(self):
        self.token   = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            logger.warning("Telegram credentials missing. Alerts disabled.")

    def send_alert(self, message: str) -> bool:
        """Send *message* to Telegram. Returns True on a confirmed send,
        False when disabled or on any failure (FIX-CA-13: a failure used to
        be swallowed to None, so callers could not tell an alert was lost).
        """
        if not self.enabled:
            return False
        try:
            from src.utils.log_redact import suppress_httpx_logging
            suppress_httpx_logging()

            from telegram import Bot
            async def _send():
                async with Bot(token=self.token) as bot:
                    await bot.send_message(
                        chat_id=self.chat_id,
                        text=message,
                        parse_mode=None,
                    )
            asyncio.run(_send())
            return True
        except Exception as exc:
            # Message text is redacted: an httpx error can embed the
            # bot-token URL.
            from src.utils.log_redact import _redact
            logger.warning("Alert failed: %s: %s", type(exc).__name__,
                           _redact(str(exc)))
            return False
