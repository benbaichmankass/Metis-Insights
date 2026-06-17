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
from typing import Dict

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
            prop_token = (os.environ.get("TELEGRAM_PROP_BOT_TOKEN")
                          or os.environ.get("TELEGRAM_CLAUDE_BOT_TOKEN"))
            send_telegram_direct(fields["text"], parse_mode=None,
                                 mirror_to_fcm=False, bot_token=prop_token)
            out["telegram"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("emit_prop_signal: telegram send failed: %s", exc)

    return out
