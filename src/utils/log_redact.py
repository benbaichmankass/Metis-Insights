"""
Log redaction: strip Telegram bot tokens from log messages before they reach handlers.

Pattern matched and replaced:
  https://api.telegram.org/bot<TOKEN>/...
  → https://api.telegram.org/bot<REDACTED>/...

Also redacts bare token shapes (digits:base64url) that appear outside the URL context.
"""
from __future__ import annotations

import logging
import re

# Matches the token segment in Telegram API URLs.
_TELEGRAM_URL_RE = re.compile(
    r"(https?://api\.telegram\.org/bot)\d{8,12}:[A-Za-z0-9_-]{30,}(/)",
    re.IGNORECASE,
)

# Matches bare token-shaped strings that somehow escape the URL form.
_BARE_TOKEN_RE = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")

_REDACTED_URL = r"\1<REDACTED>\2"
_REDACTED_BARE = "<REDACTED_TOKEN>"


def _redact(text: str) -> str:
    text = _TELEGRAM_URL_RE.sub(_REDACTED_URL, text)
    text = _BARE_TOKEN_RE.sub(_REDACTED_BARE, text)
    return text


class RedactingFilter(logging.Filter):
    """Logging filter that redacts Telegram bot tokens from every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _redact(str(v)) for k, v in record.args.items()}
            else:
                record.args = tuple(_redact(str(a)) for a in record.args)
        return True


def install_redacting_filter(logger: logging.Logger | None = None) -> None:
    """
    Attach RedactingFilter to *logger* (default: root logger).

    Call once at process startup, before any handlers emit records.
    """
    target = logger if logger is not None else logging.getLogger()
    target.addFilter(RedactingFilter())


def suppress_httpx_logging() -> None:
    """
    Set httpx and httpcore to WARNING so request URLs are never emitted at INFO.

    python-telegram-bot uses httpx internally; without this, every Telegram
    API call logs the full URL including the bot token.
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.vendor.ptb_urllib3.urllib3").setLevel(logging.WARNING)
