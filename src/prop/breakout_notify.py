"""Breakout POC — emit a trade-setup ticket as a typed ``prop_signal`` notification.

Turns a built :class:`~src.prop.breakout_ticket.Ticket` into:

  1. a **typed FCM push** (event kind ``prop_signal``) to subscribed Android
     devices — its own notification channel, distinct from the live
     ``trade_opened`` / ``trade_closed`` money events; and
  2. a **plain-text Telegram message** to the operator (the full rendered
     ticket).

Tier-1: observe / notify only. Places NO order and is NOT wired into the live
order path. Best-effort — every leg is isolated and a failure logs a WARNING
but never raises into the caller (a notification must never affect anything
upstream).
"""
from __future__ import annotations

import logging
import os
from datetime import timezone
from typing import Any, Dict, Optional

from src.prop.breakout_ticket import Ticket, render_ticket

logger = logging.getLogger(__name__)


def ticket_to_fields(ticket: Ticket) -> Dict[str, str]:
    """Flatten a :class:`Ticket` into the ``prop_signal`` FCM payload (all strings).

    ``text`` carries the full rendered ticket so the Android notification body
    and the Telegram message are the same human-readable block; the structured
    fields drive the notification title (``symbol``/``side``/``entry``) and any
    future deep-link. FCM data payloads are string→string, so every value is
    stringified here (``publish_event``'s documented contract).
    """
    s = ticket.signal
    c = ticket.cfg
    sym = c.dxtrade_symbol or s.symbol
    return {
        "strategy": str(s.strategy),
        "symbol": str(sym),
        "side": str(ticket.side),
        "direction": str(s.direction),
        "entry": f"{s.entry:g}",
        "entry_min": f"{ticket.entry_min:g}",
        "entry_max": f"{ticket.entry_max:g}",
        "sl": f"{s.sl:g}",
        "tp": f"{s.tp:g}",
        "rr": f"{ticket.rr:g}",
        "qty": f"{ticket.qty_units:g}",
        "risk_usd": f"{ticket.risk_usd:.2f}",
        "valid_until": ticket.valid_until.astimezone(timezone.utc).isoformat(),
        "text": render_ticket(ticket),
    }


def emit_prop_signal(ticket: Ticket, *, push: bool = True, telegram: bool = True) -> Dict[str, bool]:
    """Fan a Breakout ticket out as a ``prop_signal`` FCM push + a Telegram message.

    Best-effort and fully isolated: each leg is wrapped so a failure logs a
    WARNING and is reported in the return dict but never raises. Returns
    ``{"push": bool, "telegram": bool}`` — ``True`` means the leg was attempted
    without an exception (delivery itself is fire-and-forget downstream).
    """
    fields = ticket_to_fields(ticket)
    out = {"push": False, "telegram": False}

    if push:
        try:
            from src.runtime.mobile_push import publish_event
            from src.runtime.mobile_push.event_kinds import PROP_SIGNAL

            publish_event(PROP_SIGNAL, fields)
            out["push"] = True
        except Exception as exc:  # noqa: BLE001 — notification failure is never fatal
            logger.warning("emit_prop_signal: FCM push failed: %s", exc)

    if telegram:
        try:
            from src.runtime.notify import send_telegram_direct

            # Route to the dedicated PROP-account bot (the repurposed comms bot):
            # TELEGRAM_PROP_BOT_TOKEN, falling back to the existing
            # TELEGRAM_CLAUDE_BOT_TOKEN being repurposed as the prop bot, else
            # (None) the default trader bot. mirror_to_fcm=False: the typed
            # prop_signal push above already covers Android; the generic
            # `telegram` mirror would double-notify the same event.
            send_telegram_direct(fields["text"], parse_mode=None,
                                 mirror_to_fcm=False, bot_token=_prop_bot_token())
            out["telegram"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("emit_prop_signal: telegram send failed: %s", exc)

    return out


def _prop_bot_token() -> Optional[str]:
    """The PROP Telegram bot token, falling back to the repurposed comms bot."""
    return (os.environ.get("TELEGRAM_PROP_BOT_TOKEN")
            or os.environ.get("TELEGRAM_CLAUDE_BOT_TOKEN"))


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:,.4f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def render_fill_message(fill: Dict[str, Any]) -> str:
    """Human-readable Telegram line for an inbound prop fill/close report."""
    sym = fill.get("symbol") or "?"
    direction = str(fill.get("direction") or "").upper()
    account = fill.get("account_id") or fill.get("account") or "prop"
    if str(fill.get("status") or "").lower() == "closed":
        pnl = fill.get("pnl")
        emoji = "⚪"
        if isinstance(pnl, (int, float)):
            emoji = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
        pct = fill.get("pnl_percent")
        pct_str = f" ({_fmt(pct)}%)" if pct is not None else ""
        reason = fill.get("reason") or "—"
        return (
            f"{emoji} PROP CLOSE {sym} {direction} [{account}] "
            f"PnL {_fmt(pnl)}{pct_str} · exit {_fmt(fill.get('exit_price'))} · {reason}"
        )
    return (
        f"🟪 PROP FILL {sym} {direction} [{account}] "
        f"{_fmt(fill.get('qty'))} @ {_fmt(fill.get('entry_price'))}"
    )


def emit_prop_fill(fill: Dict[str, Any], *, push: bool = True,
                   telegram: bool = True) -> Dict[str, bool]:
    """Fan an inbound prop fill/close out as a typed push + Telegram line.

    Fires ``PROP_CLOSED`` when ``fill['status'] == 'closed'`` (the trade-close
    follow-up the operator was missing), else ``PROP_FILL`` (a ticket was
    placed). Best-effort + fully isolated — a leg failure logs a WARNING and is
    reported in the return dict but never raises.
    """
    from src.runtime.mobile_push.event_kinds import PROP_CLOSED, PROP_FILL

    is_closed = str(fill.get("status") or "").lower() == "closed"
    kind = PROP_CLOSED if is_closed else PROP_FILL
    text = render_fill_message(fill)
    payload = {
        "account": str(fill.get("account_id") or fill.get("account") or ""),
        "symbol": str(fill.get("symbol") or ""),
        "direction": str(fill.get("direction") or ""),
        "qty": _fmt(fill.get("qty")),
        "entry_price": _fmt(fill.get("entry_price")),
        "exit_price": _fmt(fill.get("exit_price")),
        "pnl": _fmt(fill.get("pnl")),
        "pnl_percent": _fmt(fill.get("pnl_percent")),
        "reason": str(fill.get("reason") or ""),
        "ticket_id": str(fill.get("ticket_id") or ""),
        "external_order_id": str(fill.get("external_order_id") or ""),
        "text": text,
    }
    out = {"push": False, "telegram": False}

    if push:
        try:
            from src.runtime.mobile_push import publish_event

            publish_event(kind, payload)
            out["push"] = True
        except Exception as exc:  # noqa: BLE001 — notification never fatal
            logger.warning("emit_prop_fill: FCM push failed: %s", exc)

    if telegram:
        try:
            from src.runtime.notify import send_telegram_direct

            send_telegram_direct(text, parse_mode=None, mirror_to_fcm=False,
                                 bot_token=_prop_bot_token())
            out["telegram"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("emit_prop_fill: telegram send failed: %s", exc)

    return out
