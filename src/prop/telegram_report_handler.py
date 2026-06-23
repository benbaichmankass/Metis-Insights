"""Inbound prop report handler — turn an operator's Telegram line into an ingest.

The parser half lives in :mod:`src.prop.telegram_commands`; this module is the
**enrich + ingest** half. It's transport-agnostic: it takes the raw text the
operator typed and returns a reply string (or ``None`` for a non-command line),
calling :func:`src.prop.prop_report.ingest_report` on the way.

The transport is the existing **Claude/prop comms bot** (``src/bot/claude_bridge.py``,
already long-polling ``TELEGRAM_CLAUDE_BOT_TOKEN`` — the channel prop tickets are
emitted to). Folding the handler into that bot's message handler means the
report-back loop closes with **no Claude/dashboard middle-man, no new bot token,
and no new service** — the operator just replies to a prop ticket in the same
channel (``close ETHUSD 2950 +80 tp``) and the trade updates.

Authorisation is the bridge's job (it already restricts to the operator's
``TELEGRAM_CHAT_ID``); this module assumes the caller has authorised the chat.
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

from src.prop import prop_journal
from src.prop.telegram_commands import USAGE, build_report, parse_prop_command

logger = logging.getLogger(__name__)


def default_prop_account() -> Optional[str]:
    """The account a bare command targets when no ``acct=`` override is given.

    ``PROP_DEFAULT_ACCOUNT`` wins; otherwise resolve the single prop account
    from ``accounts.yaml`` (when exactly one exists — the common case today). If
    several prop accounts exist and none is pinned, returns ``None`` so the
    handler asks the operator to disambiguate rather than guess.
    """
    pinned = os.environ.get("PROP_DEFAULT_ACCOUNT")
    if pinned:
        return pinned
    try:
        from src.config.accounts_loader import load_accounts_dict

        accts = load_accounts_dict() or {}
    except Exception as exc:  # noqa: BLE001 — no config → no default
        logger.warning("telegram_report_handler: accounts load failed: %s", exc)
        return None
    prop_ids = [
        aid for aid, a in accts.items()
        if isinstance(a, dict) and (
            str(a.get("exchange", "")).lower() == "breakout"
            or str(a.get("account_class", "")).lower() == "prop"
        )
    ]
    return prop_ids[0] if len(prop_ids) == 1 else None


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
        logger.warning("telegram_report_handler: ticket lookup failed: %s", exc)
        return None, None
    sym = str(canonical_symbol).upper()
    for t in tickets:  # list_tickets is newest-first
        if str(t.get("symbol", "")).upper() != sym:
            continue
        if t.get("status") not in ("emitted", "filled"):
            continue
        return t.get("direction"), t.get("ticket_id")
    return None, None


def handle_command(text: str, *, default_account: Optional[str] = None) -> Optional[str]:
    """Parse → enrich → ingest one message. Returns the reply text, or ``None``.

    ``None`` ⇒ the line was not a recognised prop command (caller stays silent /
    falls through to its own handling). A malformed command returns a usage hint
    string; a successful ingest returns a one-line confirmation.
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
    except Exception as exc:  # noqa: BLE001 — never crash the caller on one message
        logger.exception("telegram_report_handler: ingest failed")
        return f"⚠ error: {exc}"

    return _confirm(intent, report, out)


def _confirm(intent: dict, report: dict, out: dict) -> str:
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


__all__ = ["handle_command", "default_prop_account", "resolve_open_ticket"]
