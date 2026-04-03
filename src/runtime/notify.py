from __future__ import annotations

import logging
from typing import Any


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
    except Exception as exc:
        logger.exception("Failed to notify operator: %s | original=%s", exc, message)
