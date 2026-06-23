"""Inbound prop Telegram listener — report a fill/close by typing in the channel.

The other half of :mod:`src.prop.telegram_commands`: a stdlib-only long-poll
loop over the prop bot's ``getUpdates`` that turns an operator's structured
command (``close ETHUSD 2950 +80 tp``) into a
:func:`src.prop.prop_report.ingest_report` call — closing the manual bridge with
**no Claude/dashboard middle-man**.

Activation is credential-driven (same shape as the outbound prop bot): the
listener runs only when a prop bot token is configured (``TELEGRAM_PROP_BOT_TOKEN``
→ ``TELEGRAM_CLAUDE_BOT_TOKEN`` → ``TELEGRAM_BOT_TOKEN``, via
``breakout_notify._prop_bot_token``) **and** an allowed chat is known. For
safety it acts only on messages from an allowlisted chat:
``TELEGRAM_PROP_ALLOWED_CHAT_IDS`` (CSV), falling back to ``TELEGRAM_CHAT_ID``.
With neither set it processes nothing (a message arriving from an unknown chat
must never write the prop journal).

Run as ``python -m src.prop.telegram_inbound`` (the systemd unit
``ict-prop-telegram-listener.service``). Best-effort throughout: one bad message
never stops the loop.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.prop import prop_journal
from src.prop.breakout_notify import _prop_bot_token
from src.prop.telegram_commands import USAGE, build_report, parse_prop_command

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
_OFFSET_PATH = Path("runtime_logs") / "prop_telegram_offset.txt"
_POLL_TIMEOUT_S = 50


# ── configuration ─────────────────────────────────────────────────────

def allowed_chat_ids() -> set:
    """The chat ids the listener will act on (str-keyed for robust compares)."""
    raw = os.environ.get("TELEGRAM_PROP_ALLOWED_CHAT_IDS") or os.environ.get(
        "TELEGRAM_CHAT_ID") or ""
    return {c.strip() for c in raw.replace(";", ",").split(",") if c.strip()}


def default_prop_account() -> Optional[str]:
    """The account a bare command targets when no ``acct=`` override is given.

    ``PROP_DEFAULT_ACCOUNT`` wins; otherwise resolve the single prop account
    from ``accounts.yaml`` (when exactly one exists — the common case today). If
    several prop accounts exist and none is pinned, returns ``None`` so the
    listener asks the operator to disambiguate rather than guess.
    """
    pinned = os.environ.get("PROP_DEFAULT_ACCOUNT")
    if pinned:
        return pinned
    try:
        from src.config.accounts_loader import load_accounts_dict

        accts = load_accounts_dict() or {}
    except Exception as exc:  # noqa: BLE001 — no config → no default
        logger.warning("telegram_inbound: accounts load failed: %s", exc)
        return None
    prop_ids = [
        aid for aid, a in accts.items()
        if isinstance(a, dict) and (
            str(a.get("exchange", "")).lower() == "breakout"
            or str(a.get("account_class", "")).lower() == "prop"
        )
    ]
    return prop_ids[0] if len(prop_ids) == 1 else None


# ── enrichment: resolve the open ticket for a fill/close ──────────────

def resolve_open_ticket(account_id: str, canonical_symbol: str) -> Tuple[
        Optional[str], Optional[str]]:
    """Newest still-open ticket for ``(account, symbol)`` → ``(direction, ticket_id)``.

    Lets a bare ``close ETHUSD ...`` inherit the direction + ticket id of the
    ticket it's reporting against, so the journal links exactly. Best-effort —
    ``(None, None)`` when nothing matches (``ingest_report`` then re-matches by
    symbol alone).
    """
    try:
        tickets = prop_journal.list_tickets(account_id=account_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram_inbound: ticket lookup failed: %s", exc)
        return None, None
    sym = str(canonical_symbol).upper()
    for t in tickets:  # list_tickets is newest-first
        if str(t.get("symbol", "")).upper() != sym:
            continue
        if t.get("status") not in ("emitted", "filled"):
            continue
        return t.get("direction"), t.get("ticket_id")
    return None, None


# ── message handling (pure-ish: DB + config reads, no network) ─────────

def handle_command(text: str, *, default_account: Optional[str] = None) -> Optional[str]:
    """Parse → enrich → ingest one message. Returns the reply text, or ``None``.

    ``None`` ⇒ the line was not a recognised command (caller stays silent). A
    malformed command returns a usage hint string; a successful ingest returns a
    one-line confirmation.
    """
    try:
        intent = parse_prop_command(text)
    except ValueError as exc:
        return f"⚠ {exc}\n\n{USAGE}"
    if intent is None:
        return None

    account_id = intent.get("account_id") or default_account
    if not account_id:
        return ("⚠ No prop account resolved — set PROP_DEFAULT_ACCOUNT or add "
                "`acct=<id>` to the command.")

    direction = ticket_id = None
    if intent.get("_action") in ("close", "open", "skip"):
        try:
            from src.prop.symbol_map import to_bot_symbol

            canonical = to_bot_symbol(intent.get("symbol")) or intent.get("symbol")
        except Exception:  # noqa: BLE001 — fall back to the typed symbol
            canonical = intent.get("symbol")
        direction, ticket_id = resolve_open_ticket(account_id, canonical)

    report = build_report(
        intent, account_id=account_id, direction=direction, ticket_id=ticket_id)

    try:
        from src.prop.prop_report import ingest_report

        out = ingest_report(report)
    except ValueError as exc:
        return f"⚠ rejected: {exc}"
    except Exception as exc:  # noqa: BLE001 — never crash the loop on one message
        logger.exception("telegram_inbound: ingest failed")
        return f"⚠ error: {exc}"

    return _confirm(intent, report, out)


def _confirm(intent: Dict[str, Any], report: Dict[str, Any], out: Dict[str, Any]) -> str:
    """Human one-line ack of a successful ingest."""
    if intent.get("_action") == "status":
        rd = out.get("rule_distance") or {}
        dl = rd.get("distance_to_daily_loss_usd")
        dd = rd.get("distance_to_dd_floor_usd")
        return (f"✅ account status recorded [{report['account_id']}] · "
                f"to daily-loss ${dl} · to DD-floor ${dd}")
    sym = report.get("symbol")
    act = intent.get("_action")
    tid = out.get("ticket_id")
    tail = f" · ticket {tid}" if tid else ""
    if act == "close":
        return (f"✅ recorded CLOSE {sym} @ {report.get('exit_price')} "
                f"pnl {report.get('pnl', '—')} ({report.get('reason')}){tail}")
    if act == "open":
        return (f"✅ recorded OPEN {sym} @ {report.get('entry_price')} "
                f"qty {report.get('qty', '—')}{tail}")
    return f"✅ recorded SKIP {sym} ({report.get('reason')}){tail}"


# ── Telegram I/O (stdlib only) ────────────────────────────────────────

def _api_call(token: str, method: str, params: Dict[str, Any], *, timeout: int) -> Dict[str, Any]:
    url = _API.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _send(token: str, chat_id: Any, text: str) -> None:
    try:
        _api_call(token, "sendMessage", {"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as exc:  # noqa: BLE001 — a failed reply must not stop the loop
        logger.warning("telegram_inbound: reply send failed: %s", exc)


def _read_offset() -> Optional[int]:
    try:
        return int(_OFFSET_PATH.read_text().strip())
    except Exception:  # noqa: BLE001
        return None


def _write_offset(offset: int) -> None:
    try:
        _OFFSET_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OFFSET_PATH.write_text(str(offset))
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram_inbound: offset persist failed: %s", exc)


def _extract(update: Dict[str, Any]) -> Tuple[Optional[Any], Optional[str]]:
    msg = update.get("message") or update.get("channel_post") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    return chat_id, msg.get("text")


def poll_once(token: str, allowed: set, default_account: Optional[str],
              offset: Optional[int]) -> Optional[int]:
    """One ``getUpdates`` long-poll cycle; returns the next offset (or input)."""
    params: Dict[str, Any] = {"timeout": _POLL_TIMEOUT_S}
    if offset is not None:
        params["offset"] = offset
    try:
        data = _api_call(token, "getUpdates", params, timeout=_POLL_TIMEOUT_S + 10)
    except Exception as exc:  # noqa: BLE001 — transient network — retry next cycle
        logger.warning("telegram_inbound: getUpdates failed: %s", exc)
        time.sleep(3)
        return offset
    updates: List[Dict[str, Any]] = data.get("result") or []
    for upd in updates:
        offset = int(upd["update_id"]) + 1
        chat_id, text = _extract(upd)
        if not text:
            continue
        if str(chat_id) not in allowed:
            logger.warning("telegram_inbound: ignoring message from chat %s "
                           "(not in allowlist)", chat_id)
            continue
        reply = handle_command(text, default_account=default_account)
        if reply is not None:
            _send(token, chat_id, reply)
    if updates:
        _write_offset(offset)
    return offset


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    token = _prop_bot_token()
    allowed = allowed_chat_ids()
    if not token or not allowed:
        logger.error("telegram_inbound: inactive — need a prop bot token AND an "
                     "allowed chat id (TELEGRAM_PROP_ALLOWED_CHAT_IDS / "
                     "TELEGRAM_CHAT_ID). Exiting.")
        return 1
    default_account = default_prop_account()
    logger.info("telegram_inbound: listening (allowed=%s, default_account=%s)",
                allowed, default_account)
    offset = _read_offset()
    while True:
        offset = poll_once(token, allowed, default_account, offset)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
