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
# (?<!\d) instead of a leading \b: \b between two word characters (e.g. a
# letter glued to the first digit, as in "bot<TOKEN>") never matches — the
# exact blindness that let scripts/secret_scan.py miss URL-embedded tokens
# for 4 months (BL-20260801-TELEGRAM-BOT-TOKEN-COMPROMISE).
_BARE_TOKEN_RE = re.compile(r"(?<!\d)\d{8,12}:[A-Za-z0-9_-]{30,}\b")

_REDACTED_URL = r"\1<REDACTED>\2"
_REDACTED_BARE = "<REDACTED_TOKEN>"


def _redact(text: str) -> str:
    text = _TELEGRAM_URL_RE.sub(_REDACTED_URL, text)
    text = _BARE_TOKEN_RE.sub(_REDACTED_BARE, text)
    return text


def _redact_preserving_type(value):
    """Redact a msg/arg value WITHOUT changing its type unless it leaks.

    Coercing every arg to str (the old behaviour) breaks numeric printf
    formatting ("%d format: a real number is required, not str") on every
    record the filter touches — latent while the filter was logger-only and
    effectively never ran, fatal once attached to the root handlers. A
    non-str value is stringified only when its str() actually contains a
    token (numbers never do), so %d/%f args pass through untouched.
    """
    if isinstance(value, str):
        return _redact(value)
    text = str(value)
    redacted = _redact(text)
    return redacted if redacted != text else value


class RedactingFilter(logging.Filter):
    """Logging filter that redacts Telegram bot tokens from every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_preserving_type(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: _redact_preserving_type(v) for k, v in record.args.items()
                }
            else:
                record.args = tuple(
                    _redact_preserving_type(a) for a in record.args
                )
        return True


def install_redacting_filter(logger: logging.Logger | None = None) -> None:
    """
    Attach RedactingFilter to *logger* (default: root logger) AND to each of
    its current handlers.

    The handler attachment is the load-bearing half: a filter on a LOGGER is
    only consulted for records logged directly to that logger — records
    propagating up from child loggers (httpx, telegram.*) skip ancestor
    logger filters entirely, so the previous logger-only install never
    filtered the exact records that carry token URLs. Handler filters DO run
    on every record the handler emits, propagated or not.

    Call once at process startup, AFTER logging.basicConfig (so the root
    handler exists to attach to).
    """
    target = logger if logger is not None else logging.getLogger()
    redactor = RedactingFilter()
    target.addFilter(redactor)
    for handler in target.handlers:
        handler.addFilter(redactor)


def suppress_httpx_logging() -> None:
    """
    Set httpx and httpcore to WARNING so request URLs are never emitted at INFO.

    python-telegram-bot uses httpx internally; without this, every Telegram
    API call logs the full URL including the bot token.
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.vendor.ptb_urllib3.urllib3").setLevel(logging.WARNING)
