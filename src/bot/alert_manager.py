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

    def send_alert(self, message: str):
        if not self.enabled:
            return
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
        except Exception as exc:
            logger.warning("Alert failed: %s", type(exc).__name__)
