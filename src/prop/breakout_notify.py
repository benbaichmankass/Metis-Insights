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

import json
import logging
import os
from datetime import timezone
from typing import Any, Dict, List, Optional

from src.prop.breakout_ticket import Ticket, render_ticket

logger = logging.getLogger(__name__)


def ticket_to_fields(ticket: Ticket, *, account_id: Optional[str] = None,
                     ticket_id: Optional[str] = None) -> Dict[str, str]:
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
        "text": render_ticket(ticket, account_id=account_id, ticket_id=ticket_id),
    }


def emit_prop_signal(ticket: Ticket, *, push: bool = True, telegram: bool = True,
                     account_id: Optional[str] = None,
                     ticket_id: Optional[str] = None) -> Dict[str, bool]:
    """Fan a Breakout ticket out as a ``prop_signal`` FCM push + a Telegram message.

    Best-effort and fully isolated: each leg is wrapped so a failure logs a
    WARNING and is reported in the return dict but never raises. Returns
    ``{"push": bool, "telegram": bool}`` — ``True`` means the leg was attempted
    without an exception (delivery itself is fire-and-forget downstream).
    """
    fields = ticket_to_fields(ticket, account_id=account_id, ticket_id=ticket_id)
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

            # Attach the "Did you place this trade?" Yes/No buttons directly to
            # the ticket so the operator reports back with a tap — ✅ → the fill
            # prompt, ❌ → logged not-placed (handled in claude_bridge propexp:*).
            # The buttons ARE the primary report-back; the JSON block in the text
            # stays as a fallback. Only when we have a ticket_id to key the
            # callback on.
            reply_markup = None
            if ticket_id:
                from src.prop.prop_expiry_prompt import build_place_decision_keyboard

                reply_markup = build_place_decision_keyboard(ticket_id)

            # Route to the dedicated PROP-account bot (the repurposed comms bot):
            # TELEGRAM_PROP_BOT_TOKEN, falling back to the existing
            # TELEGRAM_CLAUDE_BOT_TOKEN being repurposed as the prop bot, else
            # (None) the default trader bot. mirror_to_fcm=False: the typed
            # prop_signal push above already covers Android; the generic
            # `telegram` mirror would double-notify the same event.
            send_telegram_direct(fields["text"], parse_mode=None,
                                 mirror_to_fcm=False, bot_token=_prop_bot_token(),
                                 reply_markup=reply_markup)
            out["telegram"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("emit_prop_signal: telegram send failed: %s", exc)

    return out


def _prop_bot_token() -> Optional[str]:
    """The Telegram bot token for prop tickets.

    Prefer the dedicated prop bot (`TELEGRAM_PROP_BOT_TOKEN`), then the
    repurposed comms bot (`TELEGRAM_CLAUDE_BOT_TOKEN`), and FINALLY fall back to
    the trader bot (`TELEGRAM_BOT_TOKEN`). The last fallback (added 2026-06-22)
    matters: on the Ampere VM neither prop nor claude token carried over the
    cutover, so this returned None → `send_telegram_direct` silently skipped the
    send and prop tickets were journaled but never delivered. The trader bot
    token IS set (trade alerts work), so this guarantees prop tickets always
    reach the operator; set TELEGRAM_PROP_BOT_TOKEN to route them to a dedicated
    prop channel instead."""
    return (os.environ.get("TELEGRAM_PROP_BOT_TOKEN")
            or os.environ.get("TELEGRAM_CLAUDE_BOT_TOKEN")
            or os.environ.get("TELEGRAM_BOT_TOKEN"))


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
            emoji = "\U0001f7e2" if pnl > 0 else ("\U0001f534" if pnl < 0 else "⚪")
        pct = fill.get("pnl_percent")
        pct_str = f" ({_fmt(pct)}%)" if pct is not None else ""
        reason = fill.get("reason") or "—"
        return (
            f"{emoji} PROP CLOSE {sym} {direction} [{account}] "
            f"PnL {_fmt(pnl)}{pct_str} · exit {_fmt(fill.get('exit_price'))} · {reason}"
        )
    return (
        f"\U0001f7ea PROP FILL {sym} {direction} [{account}] "
        f"{_fmt(fill.get('qty'))} @ {_fmt(fill.get('entry_price'))}"
    )


def render_monitor_message(position: Dict[str, Any]) -> str:
    """Human-readable Telegram line for a 'still monitoring' prop pulse.

    Since the prop account has no live broker feed, there is genuinely
    nothing to update between report-backs — so the pulse says "no change"
    and restates the position the bot is tracking, plus how long it's been
    open. ``note`` overrides the "no change" tail when the caller has
    something concrete to say.
    """
    sym = position.get("symbol") or "?"
    direction = str(position.get("direction") or "").upper()
    account = position.get("account_id") or position.get("account") or "prop"
    age_min = position.get("age_minutes")
    age_str = f" · open {_fmt(age_min)}m" if age_min is not None else ""
    note = position.get("note") or "no change"
    levels = (
        f"entry {_fmt(position.get('entry') or position.get('entry_price'))} · "
        f"SL {_fmt(position.get('sl'))} · TP {_fmt(position.get('tp'))}"
    )
    return (
        f"\U0001f535 PROP MONITOR {sym} {direction} [{account}] — still monitoring · {note}"
        f"\n{levels}{age_str}"
    )


def emit_prop_monitor_pulse(position: Dict[str, Any], *, push: bool = True,
                            telegram: bool = True) -> Dict[str, bool]:
    """Fan a periodic 'still monitoring' pulse for an open prop trade.

    Reassures the operator the system is actively tracking the prop
    position between the real-time ``prop_fill`` / ``prop_closed`` events.
    Best-effort + fully isolated — a leg failure logs a WARNING and is
    reported in the return dict but never raises.
    """
    from src.runtime.mobile_push.event_kinds import PROP_MONITOR

    text = render_monitor_message(position)
    payload = {
        "account": str(position.get("account_id") or position.get("account") or ""),
        "symbol": str(position.get("symbol") or ""),
        "direction": str(position.get("direction") or ""),
        "qty": _fmt(position.get("qty")),
        "entry": _fmt(position.get("entry") or position.get("entry_price")),
        "sl": _fmt(position.get("sl")),
        "tp": _fmt(position.get("tp")),
        "opened_at": str(position.get("opened_at") or ""),
        "age_minutes": _fmt(position.get("age_minutes")),
        "ticket_id": str(position.get("ticket_id") or ""),
        "text": text,
    }
    out = {"push": False, "telegram": False}

    if push:
        try:
            from src.runtime.mobile_push import publish_event

            publish_event(PROP_MONITOR, payload)
            out["push"] = True
        except Exception as exc:  # noqa: BLE001 — notification never fatal
            logger.warning("emit_prop_monitor_pulse: FCM push failed: %s", exc)

    if telegram:
        try:
            from src.runtime.notify import send_telegram_direct

            send_telegram_direct(text, parse_mode=None, mirror_to_fcm=False,
                                 bot_token=_prop_bot_token())
            out["telegram"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("emit_prop_monitor_pulse: telegram send failed: %s", exc)

    return out


def render_monitor_consolidated_message(positions: List[Dict[str, Any]]) -> str:
    """One 'still monitoring' Telegram body covering ALL open prop trades.

    Replaces the per-position pulse (operator directive 2026-07-08 — "turn the
    monitoring down to once an hour, one ping with all the open trades"). Lists
    each open prop position on its own line under a single header.
    """
    n = len(positions)
    header = (
        f"\U0001f535 PROP MONITOR — still monitoring "
        f"{n} open prop trade{'s' if n != 1 else ''} · no change"
    )
    lines = [header]
    for p in positions:
        sym = p.get("symbol") or "?"
        direction = str(p.get("direction") or "").upper()
        account = p.get("account_id") or p.get("account") or "prop"
        age_min = p.get("age_minutes")
        age_str = f" · open {_fmt(age_min)}m" if age_min is not None else ""
        levels = (
            f"entry {_fmt(p.get('entry') or p.get('entry_price'))} · "
            f"SL {_fmt(p.get('sl'))} · TP {_fmt(p.get('tp'))}"
        )
        lines.append(f"• {sym} {direction} [{account}] — {levels}{age_str}")
    return "\n".join(lines)


def emit_prop_monitor_consolidated(positions: List[Dict[str, Any]], *,
                                   push: bool = True,
                                   telegram: bool = True) -> Dict[str, bool]:
    """Fan ONE consolidated 'still monitoring' pulse for all open prop trades.

    The once-an-hour replacement for the per-position ``emit_prop_monitor_pulse``
    (kept for back-compat). Rides the same ``prop_monitor`` push kind; the FCM
    payload carries a compact ``count`` + the rendered ``text``. Best-effort +
    fully isolated — a leg failure logs a WARNING but never raises.
    """
    from src.runtime.mobile_push.event_kinds import PROP_MONITOR

    text = render_monitor_consolidated_message(positions)
    first = positions[0] if positions else {}
    payload = {
        "count": str(len(positions)),
        "account": str(first.get("account_id") or first.get("account") or ""),
        "symbol": str(first.get("symbol") or "") if len(positions) == 1 else "",
        "text": text,
    }
    out = {"push": False, "telegram": False}

    if push:
        try:
            from src.runtime.mobile_push import publish_event

            publish_event(PROP_MONITOR, payload)
            out["push"] = True
        except Exception as exc:  # noqa: BLE001 — notification never fatal
            logger.warning("emit_prop_monitor_consolidated: FCM push failed: %s", exc)

    if telegram:
        try:
            from src.runtime.notify import send_telegram_direct

            send_telegram_direct(text, parse_mode=None, mirror_to_fcm=False,
                                 bot_token=_prop_bot_token())
            out["telegram"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("emit_prop_monitor_consolidated: telegram send failed: %s", exc)

    return out


def render_expiry_prompt_message(ticket: Dict[str, Any]) -> str:
    """Human-readable body for the 'did you place this expired ticket?' prompt."""
    sym = ticket.get("symbol") or "?"
    direction = str(ticket.get("direction") or "").upper()
    account = ticket.get("account_id") or "prop"
    valid_until = ticket.get("valid_until") or "?"
    return (
        f"⏰ PROP TICKET EXPIRED — {sym} {direction} [{account}]\n"
        f"entry {_fmt(ticket.get('entry'))} · SL {_fmt(ticket.get('sl'))} · "
        f"TP {_fmt(ticket.get('tp'))} · qty {_fmt(ticket.get('qty'))}\n"
        f"valid until {valid_until} (now past).\n"
        "Did you place this trade?"
    )


def emit_prop_expiry_prompt(ticket: Dict[str, Any], *,
                            telegram: bool = True) -> bool:
    """Ask the operator (Yes/No buttons) whether an EXPIRED prop ticket was placed.

    Sent to the **prop bot** (``_prop_bot_token``) so the inline-keyboard answer
    lands as a ``callback_query`` on the prop bot the operator already uses — the
    callback is handled in ``src.bot.claude_bridge`` (``propexp:*``). Telegram-only:
    the buttons only work in the Telegram client, so there is no FCM leg.

    Returns ``True`` only when the prompt was confirmed sent — the caller uses
    this to gate the ticket's status flip to ``expiry_prompted`` so a delivery
    failure simply retries next tick instead of silently going un-prompted.
    Best-effort + isolated: a failure logs a WARNING and returns ``False``.
    """
    if not telegram:
        return False
    ticket_id = str(ticket.get("ticket_id") or "")
    if not ticket_id:
        return False
    try:
        from src.prop.prop_expiry_prompt import build_expiry_keyboard
        from src.runtime.notify import send_telegram_direct

        return bool(send_telegram_direct(
            render_expiry_prompt_message(ticket),
            parse_mode=None,
            mirror_to_fcm=False,
            bot_token=_prop_bot_token(),
            reply_markup=build_expiry_keyboard(ticket_id),
        ))
    except Exception as exc:  # noqa: BLE001 — notification never fatal
        logger.warning("emit_prop_expiry_prompt: send failed for %s: %s",
                       ticket_id, exc)
        return False


def render_invalidation_prompt_message(
    ticket: Dict[str, Any], current_price: Any, which: str
) -> str:
    """Body for the 'price left the brackets — do NOT place it' prompt.

    ``which`` ∈ {``sl``, ``tp``}: which bracket price has traded to/through, so the
    operator sees *why* the setup is dead (SL hit → the trade already failed; TP
    hit → the move already happened).
    """
    sym = ticket.get("symbol") or "?"
    direction = str(ticket.get("direction") or "").upper()
    account = ticket.get("account_id") or "prop"
    level = "SL" if which == "sl" else "TP"
    level_val = ticket.get("sl") if which == "sl" else ticket.get("tp")
    return (
        f"🚫 PROP SETUP NO LONGER VALID — {sym} {direction} [{account}]\n"
        f"entry {_fmt(ticket.get('entry'))} · SL {_fmt(ticket.get('sl'))} · "
        f"TP {_fmt(ticket.get('tp'))} · qty {_fmt(ticket.get('qty'))}\n"
        f"Price {_fmt(current_price)} has moved beyond the brackets "
        f"({level} {_fmt(level_val)} reached).\n"
        "⚠️ Do NOT place this trade if you haven't already.\n"
        "Did you already place it?"
    )


def emit_prop_invalidation_prompt(
    ticket: Dict[str, Any], current_price: Any, which: str, *,
    telegram: bool = True,
) -> bool:
    """Warn the operator a still-emitted prop ticket is no longer placeable, + ask.

    Price has left the ticket's ``[SL, TP]`` band while the bot was still awaiting
    the operator's place-decision. Sent to the **prop bot** with the SAME Yes/No
    inline keyboard as the expiry prompt (``propexp:*`` → ``handle_expiry_callback``
    in ``src.bot.claude_bridge``): **No** logs it ``expired``; **Yes** moves it to
    ``awaiting_report`` for the fill paste. Telegram-only (the buttons only work in
    the Telegram client). Returns ``True`` only on a confirmed send — the caller
    gates the ticket's flip to ``invalidated_prompted`` on it, so a delivery
    failure simply retries next tick. Best-effort + isolated.
    """
    if not telegram:
        return False
    ticket_id = str(ticket.get("ticket_id") or "")
    if not ticket_id:
        return False
    try:
        from src.prop.prop_expiry_prompt import build_expiry_keyboard
        from src.runtime.notify import send_telegram_direct

        return bool(send_telegram_direct(
            render_invalidation_prompt_message(ticket, current_price, which),
            parse_mode=None,
            mirror_to_fcm=False,
            bot_token=_prop_bot_token(),
            reply_markup=build_expiry_keyboard(ticket_id),
        ))
    except Exception as exc:  # noqa: BLE001 — notification never fatal
        logger.warning("emit_prop_invalidation_prompt: send failed for %s: %s",
                       ticket_id, exc)
        return False


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


def render_sl_tp_alert_message(
    position: Dict[str, Any],
    level_type: str,
    current_price: float,
) -> str:
    """Human-readable Telegram message for a prop SL/TP crossing alert.

    Includes a pre-filled JSON report template the operator can paste directly
    to the assistant (via the prop-report workflow or Telegram) to log the close.
    ``level_type`` is ``'sl'`` or ``'tp'``.
    """
    sym = str(position.get("symbol") or "?").upper()
    direction = str(position.get("direction") or "").upper()
    account = str(position.get("account_id") or position.get("account") or "prop")
    age_min = position.get("age_minutes")
    age_str = f" · open {_fmt(age_min)}m" if age_min is not None else ""
    sl = position.get("sl")
    tp = position.get("tp")
    entry = position.get("entry") or position.get("entry_price")
    ticket_id = str(position.get("ticket_id") or "")

    is_sl = level_type.lower() == "sl"
    level_val = sl if is_sl else tp
    reason_tag = "sl" if is_sl else "tp"

    if is_sl:
        d = direction.lower()
        if d in ("sell", "short"):
            cross_desc = f"{_fmt(current_price)} ≥ SL {_fmt(level_val)}"
        else:
            cross_desc = f"{_fmt(current_price)} ≤ SL {_fmt(level_val)}"
        emoji = "\U0001f6a8"
        header = f"{emoji} PROP SL HIT? {sym} {direction} [{account}] — price crossed SL"
    else:
        d = direction.lower()
        if d in ("sell", "short"):
            cross_desc = f"{_fmt(current_price)} ≤ TP {_fmt(level_val)}"
        else:
            cross_desc = f"{_fmt(current_price)} ≥ TP {_fmt(level_val)}"
        emoji = "\U0001f3af"
        header = f"{emoji} PROP TP HIT? {sym} {direction} [{account}] — price crossed TP"

    levels_line = (
        f"entry {_fmt(entry)} · SL {_fmt(sl)} · TP {_fmt(tp)}{age_str}"
    )

    report_template = json.dumps({
        "account_id": account,
        "symbol": sym,
        "direction": direction.lower(),
        "status": "closed",
        "exit_price": round(current_price, 6),
        "pnl": None,
        "pnl_percent": None,
        "reason": reason_tag,
        **(  {"ticket_id": ticket_id} if ticket_id else {}),
    }, indent=2)

    return (
        f"{header}\n"
        f"{cross_desc}\n"
        f"{levels_line}\n\n"
        "Did the trade close? If yes, paste this report to the assistant:\n\n"
        f"```\n{report_template}\n```"
    )


def emit_prop_sl_tp_alert(
    position: Dict[str, Any],
    level_type: str,
    current_price: float,
    *,
    push: bool = True,
    telegram: bool = True,
) -> Dict[str, bool]:
    """Fire a one-shot SL/TP crossing alert for an open prop trade.

    Sends a Telegram message (via the prop bot) with a pre-filled JSON report
    template so the operator can immediately log the close if the trade hit.
    Also fires a typed ``PROP_SL_TP_ALERT`` FCM push so the Android app pings
    loudly. Best-effort + fully isolated — a leg failure logs a WARNING and is
    reported in the return dict but never raises.

    ``level_type`` is ``'sl'`` or ``'tp'``.
    """
    from src.runtime.mobile_push.event_kinds import PROP_SL_TP_ALERT

    text = render_sl_tp_alert_message(position, level_type, current_price)
    sym = str(position.get("symbol") or "")
    direction = str(position.get("direction") or "")
    account = str(position.get("account_id") or position.get("account") or "")
    sl = position.get("sl")
    tp = position.get("tp")

    payload = {
        "account": account,
        "symbol": sym,
        "direction": direction,
        "level_type": level_type,
        "current_price": _fmt(current_price),
        "sl": _fmt(sl),
        "tp": _fmt(tp),
        "entry": _fmt(position.get("entry") or position.get("entry_price")),
        "ticket_id": str(position.get("ticket_id") or ""),
        "age_minutes": _fmt(position.get("age_minutes")),
        "text": text,
    }
    out = {"push": False, "telegram": False}

    if push:
        try:
            from src.runtime.mobile_push import publish_event

            publish_event(PROP_SL_TP_ALERT, payload)
            out["push"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("emit_prop_sl_tp_alert: FCM push failed: %s", exc)

    if telegram:
        try:
            from src.runtime.notify import send_telegram_direct

            send_telegram_direct(text, parse_mode=None, mirror_to_fcm=False,
                                 bot_token=_prop_bot_token())
            out["telegram"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("emit_prop_sl_tp_alert: telegram send failed: %s", exc)

    return out


def render_status_request_message(account_id: str,
                                  open_positions: list,
                                  age_hours: "Optional[float]" = None) -> str:
    """Body for the account-status request ping — includes the exact reply
    formats ``telegram_report_handler`` parses, so the operator can answer by
    editing one line in place."""
    lines = [f"📋 PROP STATUS REQUEST [{account_id}]"]
    for pos in open_positions[:5]:
        lines.append(
            f"open: {pos.get('symbol') or '?'} "
            f"{str(pos.get('direction') or '').upper()} "
            f"qty {_fmt(pos.get('qty'))} @ {_fmt(pos.get('entry') or pos.get('entry_price'))}"
        )
    if age_hours is None:
        lines.append("No account-status snapshot has ever been reported — the "
                     "rule-distance guard (daily-loss / DD-floor cushion) is blind.")
    else:
        lines.append(f"Last account-status snapshot is {age_hours:.0f}h old — "
                     "too stale for the rule-distance guard.")
    lines.append("Reply with the terminal's CURRENT numbers, either format:")
    lines.append("• bal <balance> <equity> [realized_today]   e.g. `bal 5040 5010 -25`")
    lines.append(
        '• {"kind":"account_status","account_id":"%s","balance":0,"equity":0,'
        '"realized_today":0,"unrealized":0,"day_start_balance":0}' % account_id)
    return "\n".join(lines)


def emit_prop_status_request(account_id: str, open_positions: list, *,
                             age_hours: "Optional[float]" = None,
                             push: bool = True,
                             telegram: bool = True) -> Dict[str, bool]:
    """Ask the operator for a fresh account-status report-back (manual bridge).

    Fired by ``prop_status_request.run_prop_status_request`` when a prop
    position is open but the latest ``prop_account_status`` snapshot is absent
    or stale. Rides the existing ``prop_monitor`` push kind (it is a
    monitoring nudge, not a trade event). Best-effort + fully isolated.
    """
    from src.runtime.mobile_push.event_kinds import PROP_MONITOR

    text = render_status_request_message(account_id, open_positions, age_hours)
    payload = {
        "account": account_id,
        "kind_detail": "status_request",
        "open_positions": len(open_positions),
        "status_age_hours": _fmt(age_hours),
        "text": text,
    }
    out = {"push": False, "telegram": False}

    if push:
        try:
            from src.runtime.mobile_push import publish_event

            publish_event(PROP_MONITOR, payload)
            out["push"] = True
        except Exception as exc:  # noqa: BLE001 — notification never fatal
            logger.warning("emit_prop_status_request: FCM push failed: %s", exc)

    if telegram:
        try:
            from src.runtime.notify import send_telegram_direct

            send_telegram_direct(text, parse_mode=None, mirror_to_fcm=False,
                                 bot_token=_prop_bot_token())
            out["telegram"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("emit_prop_status_request: telegram send failed: %s", exc)

    return out
