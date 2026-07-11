"""Vision extraction of a prop report-back from a terminal screenshot.

The prop account is a **manual bridge**: the bot only learns a fill/close or an
account balance when the operator reports it back. Until now that report-back
was **text-only** — the operator typed ``close ETHUSD 2950 +80 tp`` / ``bal 5040
5010`` in the prop bot channel (:mod:`src.prop.telegram_commands` +
:mod:`src.prop.telegram_report_handler`). This module adds the **screenshot**
path: the operator sends a photo of the Breakout / DXtrade terminal (a single
**Position** screen, or the **account/portfolio** summary) and the bot extracts
the same structured report(s) via Claude vision, then routes them through the
one ``prop_report.ingest_report`` chokepoint every other path uses.

Design (mirrors the text parser's split responsibilities):

- :func:`parse_screenshot` — the transport-facing entry: raw image bytes →
  ``list`` of ingest-ready report dicts. It isolates the one LLM call in
  :func:`_call_vision` (lazy ``anthropic`` import, so this module imports in
  tests without the SDK) and shapes the model's JSON in the **pure**
  :func:`_reports_from_model_json` (fully unit-testable with a canned model
  response — no API).
- **Honest-null.** The extractor is instructed to OMIT any field it can't read
  rather than guess — a Position screen carries no balance, so it yields only a
  fill; an account screen yields an ``account_status``; a screen showing both
  yields both. A field the model can't see stays absent, never a fabricated 0
  (the money-number contract the rest of the stack already follows).
- **Best-effort + isolated.** Every failure path (no API key, SDK missing,
  unparseable model output, empty extraction) returns an empty list or raises
  :class:`ScreenshotParseError` with an operator-readable message — it never
  crashes the caller (the prop bot's photo handler).

One report shape per :func:`src.prop.prop_report.ingest_report`:
    * fill/close — ``{account_id, symbol, direction, status, entry_price,
      exit_price, qty, sl, tp, pnl, external_order_id, opened_at}``
    * account status — ``{kind:"account_status", account_id, balance, equity,
      realized_today, unrealized, day_start_balance}``
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-sonnet-5"
_FILL_STATUSES = {"placed", "open", "filled", "closed", "skipped"}
_SUPPORTED_MEDIA = {"image/png", "image/jpeg", "image/webp", "image/gif"}

# The extraction contract handed to the vision model. Deliberately strict about
# NOT inventing numbers — a Position detail screen has no account balance, and
# fabricating one would arm the rule-distance guard against a fiction.
_SYSTEM_PROMPT = (
    "You read screenshots of a prop-trading terminal (Breakout / DXtrade / "
    "MetaTrader-style) and extract the trade or account facts EXACTLY as shown. "
    "You never guess a value that is not visible in the image. Return ONLY a "
    "single JSON object, no prose, no code fence."
)

_USER_PROMPT = (
    "Extract every trade/account fact from this prop-terminal screenshot into "
    "this JSON schema and return ONLY the JSON:\n"
    "{\n"
    '  "reports": [\n'
    "    // Include a POSITION/TRADE report when a position or trade is shown:\n"
    "    {\n"
    '      "type": "fill",\n'
    '      "symbol": "<e.g. ETHUSD as shown>",\n'
    '      "direction": "buy|sell",\n'
    '      "status": "filled|closed|placed",   // filled=open live position; '
    "closed=already exited; placed=limit not yet filled\n"
    '      "entry_price": <fill/entry price, number>,\n'
    '      "exit_price": <number, only if the trade is CLOSED>,\n'
    '      "qty": <quantity/volume/lots, number>,\n'
    '      "sl": <stop-loss price, number, if shown>,\n'
    '      "tp": <take-profit price, number, if shown>,\n'
    '      "pnl": <realized P/L, number, ONLY if CLOSED>,\n'
    '      "external_order_id": "<position/order/ticket code if shown>"\n'
    "    },\n"
    "    // Include an ACCOUNT report ONLY when the account balance/equity is "
    "visible (portfolio/account screen). A single-position detail screen has "
    "NO balance — do not invent one:\n"
    "    {\n"
    '      "type": "account_status",\n'
    '      "balance": <account balance, number>,\n'
    '      "equity": <account equity, number>,\n'
    '      "realized_today": <today\'s realized P/L, number, if shown>,\n'
    '      "unrealized": <open P/L, number, if shown>\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "Rules: OMIT any field you cannot read (do not output 0 or a guess). "
    '"Used Margin" is NOT the account balance. "Open P/L" is the position\'s '
    "unrealized P/L, not a realized pnl. If nothing is extractable, return "
    '{"reports": []}.'
)


class ScreenshotParseError(Exception):
    """Raised for an operator-readable extraction failure (bad/absent API, etc.)."""


def _model_id() -> str:
    return os.environ.get("PROP_SCREENSHOT_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def _num(v: Any) -> Optional[float]:
    """Coerce a model-emitted numeric to float, tolerating "1,812.04"/"$5,116"."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("$", "").replace("USD", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _norm_direction(v: Any) -> Optional[str]:
    s = str(v or "").strip().lower()
    if s in ("buy", "long", "b"):
        return "buy"
    if s in ("sell", "short", "s"):
        return "sell"
    return None


def _reports_from_model_json(
    data: Any, *, default_account: Optional[str],
) -> List[Dict[str, Any]]:
    """Pure: shape the vision model's parsed JSON into ingest-ready reports.

    Drops any entry that lacks the minimum viable fields (a fill needs a symbol;
    an account_status needs a balance or equity) so a half-read screen never
    produces a junk ingest. Numbers are coerced; unreadable fields stay absent.
    """
    if isinstance(data, dict):
        raw_reports = data.get("reports")
        if raw_reports is None:  # a bare single report object is tolerated
            raw_reports = [data]
    elif isinstance(data, list):
        raw_reports = data
    else:
        return []
    if not isinstance(raw_reports, list):
        return []

    out: List[Dict[str, Any]] = []
    for item in raw_reports:
        if not isinstance(item, dict):
            continue
        rtype = str(item.get("type") or "").strip().lower()
        is_account = rtype == "account_status" or (
            not rtype and ("balance" in item or "equity" in item)
            and not item.get("symbol")
        )
        if is_account:
            balance = _num(item.get("balance"))
            equity = _num(item.get("equity"))
            if balance is None and equity is None:
                continue  # nothing usable — don't record a blank snapshot
            report: Dict[str, Any] = {
                "kind": "account_status",
                "account_id": default_account,
                "balance": balance,
                "equity": equity,
                "realized_today": _num(item.get("realized_today")),
                "unrealized": _num(item.get("unrealized")),
            }
            if item.get("day_start_balance") is not None:
                report["day_start_balance"] = _num(item.get("day_start_balance"))
            out.append({k: v for k, v in report.items() if v is not None
                        or k in ("kind", "account_id")})
            continue

        # --- fill / close ---
        symbol = item.get("symbol")
        if not symbol:
            continue  # a fill with no symbol is unusable
        status = str(item.get("status") or "").strip().lower()
        if status not in _FILL_STATUSES:
            # No usable status → infer from the presence of an exit price.
            status = "closed" if item.get("exit_price") is not None else "filled"
        report = {
            "account_id": default_account,
            "symbol": str(symbol).strip(),
            "direction": _norm_direction(item.get("direction")),
            "status": status,
            "entry_price": _num(item.get("entry_price")),
            "exit_price": _num(item.get("exit_price")),
            "qty": _num(item.get("qty")),
            "sl": _num(item.get("sl")),
            "tp": _num(item.get("tp")),
            "pnl": _num(item.get("pnl")),
            "external_order_id": (
                str(item.get("external_order_id")).strip()
                if item.get("external_order_id") not in (None, "") else None
            ),
        }
        out.append({k: v for k, v in report.items() if v is not None})
    return out


def _extract_text(resp: Any) -> str:
    """Pull the concatenated text out of an Anthropic Messages response."""
    parts: List[str] = []
    for block in getattr(resp, "content", None) or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(str(text))
    return "".join(parts).strip()


def _strip_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _call_vision(image_b64: str, media_type: str) -> str:
    """The one LLM call — lazy anthropic import so tests can monkeypatch this.

    Raises :class:`ScreenshotParseError` with an operator-readable reason when
    the SDK/key is unavailable so the photo handler can reply cleanly.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ScreenshotParseError(
            "screenshot reading is unavailable (no ANTHROPIC_API_KEY set) — "
            "type the report instead, e.g. `close ETHUSD 2950 +80 tp` / `bal 5040 5010`.")
    try:
        import anthropic  # noqa: F401  (lazy on purpose)
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise ScreenshotParseError(
            "screenshot reading is unavailable (anthropic SDK not installed) — "
            "type the report instead.") from exc

    client = anthropic.Anthropic()  # picks up ANTHROPIC_API_KEY from env
    resp = client.messages.create(
        model=_model_id(),
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": _USER_PROMPT},
            ],
        }],
    )
    return _extract_text(resp)


def parse_screenshot(
    image_bytes: bytes,
    media_type: str = "image/png",
    *,
    default_account: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Extract ingest-ready prop report(s) from a terminal screenshot.

    Returns a possibly-empty list of report dicts (each accepted by
    ``prop_report.ingest_report``). Raises :class:`ScreenshotParseError` for an
    unusable environment / unparseable model output — the caller turns that into
    an operator-readable reply.
    """
    import base64

    if not image_bytes:
        raise ScreenshotParseError("empty image")
    mt = (media_type or "image/png").split(";")[0].strip().lower()
    if mt not in _SUPPORTED_MEDIA:
        mt = "image/png"  # Telegram photos are JPEG/PNG; default is safe

    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    raw = _call_vision(image_b64, mt)
    if not raw:
        return []
    try:
        data = json.loads(_strip_fence(raw))
    except (ValueError, TypeError) as exc:
        logger.warning("screenshot_parse: model output not JSON: %s", raw[:200])
        raise ScreenshotParseError(
            "couldn't read the screenshot into a report — try a clearer shot or "
            "type it (e.g. `close ETHUSD 2950 +80 tp`).") from exc
    return _reports_from_model_json(data, default_account=default_account)


__all__ = ["parse_screenshot", "ScreenshotParseError"]
