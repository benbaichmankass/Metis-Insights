"""API failure reporting — S-023 PR3.

Every Bybit / Binance / prop-firm API call that fails routes through
``report_api_failure(...)`` so the operator gets a Telegram ping with
the **direct API response** (retCode + retMsg, HTTP status, or
exception type + message) — replacing the previous pattern of
``logger.warning(...)`` + silent swallow.

Severity: ERROR by default. The per-fingerprint dedup in
``src/runtime/outcomes.py`` (1 alert per fingerprint per 5 min,
hard cap 30/hour) means a flapping API doesn't flood the operator.

Redaction
---------
API responses sometimes echo the API key in headers, signed URLs, or
explicit fields. ``_redact_for_telegram`` strips obvious patterns
before the message goes out:

  * Anything that looks like a long base64/hex token (32+ chars).
  * ``"api_key": "<...>"`` / ``"apiKey": "<...>"`` / ``api_secret`` /
    ``Authorization: Bearer <...>``.
  * Telegram bot tokens (existing ``_redact`` from log_redact).

Truncation: full response is capped at 500 chars before redaction so a
huge JSON payload doesn't blow past Telegram's message size limit.

Never raises. A failure inside this module logs a warning and returns;
the host call site keeps going.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


# A "long token" is 32+ chars of base64-url / hex. Tight enough not to
# match a normal English word, loose enough to cover Bybit's 36-char keys
# and Binance's 64-char keys.
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{18,}\b")
_KV_KEY_RE = re.compile(
    r"""("?(?:api[_-]?key|apikey|api[_-]?secret|secret|access[_-]?key|"""
    r"""password|token|authorization)"?\s*[:=]\s*"?)([^"'\s,}]+)""",
    re.IGNORECASE,
)
# Bearer / Basic prefixes followed by a token — needs its own pattern
# because the token follows a space, not a colon/equals.
_BEARER_RE = re.compile(
    r"\b(Bearer|Basic)\s+([A-Za-z0-9_\-\.=]+)",
    re.IGNORECASE,
)


def _redact_for_telegram(text: str) -> str:
    """Strip credential-shaped substrings from *text*. Best-effort."""
    if not isinstance(text, str):
        text = str(text)
    text = _KV_KEY_RE.sub(r"\1<REDACTED>", text)
    text = _BEARER_RE.sub(r"\1 <REDACTED>", text)
    text = _LONG_TOKEN_RE.sub("<REDACTED_TOKEN>", text)
    # Defer to the existing Telegram-token redactor too.
    try:
        from src.utils.log_redact import _redact as _tg_redact
        text = _tg_redact(text)
    except Exception:  # noqa: BLE001
        pass
    return text


def _excerpt(payload: Any, *, max_chars: int = 500) -> str:
    """Render *payload* for inclusion in a Telegram message.

    Prefers ``json.dumps`` for dicts/lists so the structure is
    readable; falls back to ``str(payload)``. Truncated to *max_chars*
    after redaction.
    """
    if payload is None:
        return ""
    try:
        if isinstance(payload, (dict, list)):
            text = json.dumps(payload, default=str, ensure_ascii=False)
        else:
            text = str(payload)
    except Exception:  # noqa: BLE001
        text = repr(payload)
    text = _redact_for_telegram(text)
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    return text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def report_api_failure(
    *,
    exchange: str,
    op: str,
    account_id: str = "unknown",
    error: str,
    response: Any = None,
    exception: Optional[BaseException] = None,
) -> None:
    """Route an API call failure through ``outcomes.report``.

    Parameters
    ----------
    exchange : str
        ``"bybit"`` / ``"binance"`` / etc. Used in fingerprint + ctx.
    op : str
        The operation name — ``"get_wallet_balance"``,
        ``"place_order"``, etc. Used in fingerprint.
    account_id : str
        The account_id (from accounts.yaml) so the operator knows
        which account is failing.
    error : str
        Human-readable description (already redacted at call site
        is fine; this function will redact again defensively).
    response : Any, optional
        Raw API response (dict, str, etc.) — included in the ping
        as a redacted excerpt. Bybit uses retCode + retMsg here;
        HTTP errors carry status + body.
    exception : BaseException, optional
        The bubble-out exception. Used to extract type + args when
        the failure was an exception, not a structured response.
    """
    try:
        from src.runtime.outcomes import Level, report

        ctx: dict[str, Any] = {"exchange": exchange, "op": op,
                               "account": account_id}

        # If we have a Bybit-style structured response, pull retCode /
        # retMsg into ctx so the operator can grep for them.
        if isinstance(response, dict):
            for key in ("retCode", "retMsg", "code", "msg",
                        "error", "status"):
                if key in response:
                    val = response[key]
                    if isinstance(val, (str, int, float, bool)) or val is None:
                        ctx[key] = val
            excerpt = _excerpt(response)
            if excerpt:
                ctx["response_excerpt"] = excerpt

        if exception is not None:
            ctx["exception_type"] = type(exception).__name__

        # Redact the human-readable error string defensively (the
        # caller may have copied API responses verbatim).
        clean_error = _redact_for_telegram(error)

        report(
            "api_call",
            f"{exchange}_{op}_failed",
            level=Level.ERROR,
            reason=clean_error,
            **ctx,
        )
    except Exception as exc:  # noqa: BLE001
        # Reporting a failure must NEVER itself raise — that would
        # mask the very failure we're trying to surface.
        logger.exception("report_api_failure itself failed: %s", exc)
