from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)


def send_telegram_direct(message: str) -> None:
    """
    Stdlib-only direct POST to Telegram's sendMessage API.

    Reads ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` from the process
    environment. If either is missing, logs a warning and returns (back-compat
    with the previous AlertManager-based path).

    On present credentials, performs a synchronous form-encoded POST. Raises
    ``urllib.error.URLError`` / ``urllib.error.HTTPError`` on network failure
    or non-2xx responses, and ``RuntimeError`` if the API replies with
    ``ok=false``. Callers are responsible for translating those into exit codes.

    Security: the bot token is embedded in the request URL but is never logged
    or printed in any form (full, redacted, or length). Only ``ok``,
    ``message_id``, and HTTP ``status_code`` are logged on success.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning(
            "Telegram credentials missing (TELEGRAM_BOT_TOKEN or "
            "TELEGRAM_CHAT_ID); skipping send"
        )
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        status = resp.getcode()
        body = resp.read()
        if not (200 <= status < 300):
            raise urllib.error.HTTPError(
                "<redacted>", status, "non-2xx from Telegram", resp.headers, None
            )
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"Telegram returned non-JSON body: {exc}") from exc
        ok = bool(parsed.get("ok"))
        message_id = (parsed.get("result") or {}).get("message_id")
        logger.info(
            "Telegram send: ok=%s message_id=%s status_code=%s",
            ok,
            message_id,
            status,
        )
        if not ok:
            raise RuntimeError("Telegram API returned ok=false")


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
