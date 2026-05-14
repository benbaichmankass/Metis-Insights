from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)


def send_telegram_direct(message: str, *, parse_mode: Optional[str] = "HTML") -> None:
    """
    Stdlib-only direct POST to Telegram's sendMessage API.

    Reads ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` from the process
    environment. If either is missing, logs a warning and returns (back-compat
    with the previous AlertManager-based path).

    On present credentials, performs a synchronous form-encoded POST. Raises
    ``urllib.error.URLError`` / ``urllib.error.HTTPError`` on network failure
    or non-2xx responses, and ``RuntimeError`` if the API replies with
    ``ok=false``. Callers are responsible for translating those into exit codes.

    ``parse_mode`` defaults to ``"HTML"`` for back-compat with existing
    HTML-formatted callers (``cmd_accounts_status`` etc.). Plain-text content
    that contains ``<``/``>``/``&`` (e.g. the hourly report's
    ``expected <= 15m`` line) MUST pass ``parse_mode=None`` to avoid Telegram's
    HTML parser rejecting the message with ``BadRequest: Can't parse entities``.
    Pass an explicit ``"MarkdownV2"`` only when every special character has
    been escaped.

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
    fields = {"chat_id": chat_id, "text": message}
    if parse_mode:
        fields["parse_mode"] = parse_mode
    payload = urllib.parse.urlencode(fields).encode("utf-8")
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


def send_to_operator(
    plain: str,
    html: Optional[str] = None,
    *,
    telegram_client: Any = None,
) -> None:
    """Single entry-point for runtime-to-operator messages.

    Resolution order:
      1. telegram_client present → ``notify_operator(client, plain)``
      2. html provided           → ``send_telegram_direct(html, "HTML")``;
                                   on failure falls back to plain.
      3. plain only              → ``send_telegram_direct(plain, None)``

    Callers should not replicate this fallback chain themselves. Raises only
    if ALL paths fail; individual step failures are logged.
    """
    if telegram_client is not None:
        notify_operator(telegram_client, plain)
        return
    if html is not None:
        try:
            send_telegram_direct(html, parse_mode="HTML")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "send_to_operator: HTML send failed (%s); falling back to plain text",
                exc,
            )
    send_telegram_direct(plain, parse_mode=None)


def send_via_alert_manager(message: str) -> None:
    """Send a message to the operator's Telegram chat. Plain-text mode.

    Used by the hourly report scheduler, the news-veto pipeline reporter,
    and the "Pipeline result" message at the end of every tick. The name
    is historical — there is no AlertManager dependency anymore.

    Previously this routed through ``src.bot.alert_manager.AlertManager``
    which had two problems:

      1. The wrapper called ``mgr.send(message)`` but ``AlertManager``
         only exposes ``send_alert``. Every send raised ``AttributeError``,
         was caught by ``outcomes._send_telegram_or_queue``, and the
         message landed in the pending-queue JSONL. Operator never
         received hourly summaries (fixed CP-2026-05-02).
      2. The wrapper's nested ``asyncio.run`` could not run from inside
         an existing event loop, which the bot process is.

    The replacement is a direct sync call to ``send_telegram_direct``
    with ``parse_mode=None`` so the hourly summary's plain-text content
    (which contains characters like ``<= 15m`` that Telegram's HTML
    parser rejects) is delivered without the parser interpreting any
    of it. Callers that DO want HTML/Markdown should call
    ``send_telegram_direct`` directly with the appropriate ``parse_mode``.
    """
    try:
        send_telegram_direct(message, parse_mode=None)
    except Exception as exc:  # noqa: BLE001
        # Re-raise so the caller (typically ``outcomes._send_telegram_or_queue``)
        # can fall through to the pending-queue JSONL drain. The previous
        # silent-failure mode hid this exact code path for two sprints.
        logger.warning("send_via_alert_manager: telegram send failed: %s", exc)
        raise
