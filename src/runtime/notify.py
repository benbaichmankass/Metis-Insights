from src.runtime.signal_notifications import *
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def notify_operator(telegram_client: Any, message: str) -> None:
    """
    Send a short operator-facing message without allowing notification
    failures to crash the runtime.
    """
    try:
        if telegram_client is None:
            logger.warning("Telegram client missing; message not sent: %s", message)
            return

        if hasattr(telegram_client, "send_message"):
            telegram_client.send_message(message)
            return

        raise AttributeError("telegram_client has no send_message method")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to notify operator: %s | original=%s", exc, message)


async def _send_via_alert_manager_async(message: str) -> None:
    try:
        from src.bot.alert_manager import AlertManager
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to import AlertManager: %s", exc)
        return

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("AlertManager not configured (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")
        return

    mgr = AlertManager()
    await mgr.send(message)


def send_via_alert_manager(message: str) -> None:
    """
    Convenience function: fire-and-forget AlertManager.send from sync code.
    Used by Thread 2 pipeline when we don't have a custom telegram_client.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Fire-and-forget task in existing loop
        asyncio.create_task(_send_via_alert_manager_async(message))
    else:
        asyncio.run(_send_via_alert_manager_async(message))
