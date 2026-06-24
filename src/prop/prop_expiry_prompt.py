"""Prop ticket-expiry Yes/No prompt — close the manual-bridge loop on a stale ticket.

The Breakout prop account is a **manual bridge**: the bot emits a paste-ready
ticket (``breakout_executor.emit_prop_ticket``) and a human places it on the
DXTrade terminal, then reports back so the bot can journal + monitor the trade.
When a ticket passes its ``valid_until`` with no report-back, the bot can't tell
whether the operator placed it (and forgot to report) or skipped it — it just
sits as drift (``prop_reconcile.find_unacted_tickets``).

This module turns that silent drift into an **active question**. Once per trader
tick (called from ``src/main.py``), :func:`run_prop_expiry_prompts` finds tickets
that just expired un-acted and sends the operator a prop-bot message with two
inline buttons:

    ⏰ PROP TICKET EXPIRED — ETHUSDT SHORT … Did you place this trade?
        [✅ Yes — I placed it]   [❌ No — not placed]

The answer is handled in the prop bot (``src.bot.claude_bridge`` ``propexp:*``
callbacks → :func:`handle_expiry_callback`):

- **No**  → the ticket is logged ``expired`` (operator confirmed it was never
  placed). Done.
- **Yes** → the ticket moves to ``awaiting_report`` and the operator gets the
  executor-assistant ``REPORT_PROMPT`` so they can paste the fill details
  (``open …`` / ``close …``), which flow through the SAME
  ``prop_report.ingest_report`` chokepoint and link back to this ticket
  (``match_fill_to_ticket`` accepts ``awaiting_report``).

Lifecycle (status on the ``prop_tickets`` row):

    emitted ──(stale, prompt sent)──▶ expiry_prompted ──┬─ No ──▶ expired
                                                         └─ Yes ─▶ awaiting_report ──(fill)─▶ filled/closed

Idempotency is the status flip itself: the detector reuses
``find_unacted_tickets`` (status == ``emitted`` only), so once a ticket is flipped
to ``expiry_prompted`` it is never re-detected — no separate state file. The flip
happens **only after a confirmed send**, so a delivery failure simply retries next
tick.

Baseline, not gated (Prime Directive — no default-off flag in front of a required
capability). Knobs: ``PROP_EXPIRY_PROMPT_MAX_AGE_HOURS`` (default 12) bounds how
stale a ticket may be before we stop bothering to ask — so a historical backlog
of ancient emitted tickets can never spam the operator on first deploy. Set the
cadence knob ``PROP_EXPIRY_PROMPT_SECONDS`` ``<= 0`` to pause prompting without a
redeploy. Best-effort + isolated everywhere — a prompt failure never propagates
into the trader loop.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from src.prop import prop_journal, prop_reconcile

logger = logging.getLogger(__name__)

EXPIRY_CB_PREFIX = "propexp"
_DEFAULT_MAX_AGE_HOURS = 12.0
_PROMPTED_STATUS = "expiry_prompted"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _max_age_hours() -> float:
    raw = os.environ.get("PROP_EXPIRY_PROMPT_MAX_AGE_HOURS")
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_MAX_AGE_HOURS
    try:
        return float(raw)
    except (ValueError, TypeError):
        return _DEFAULT_MAX_AGE_HOURS


def _enabled() -> bool:
    """Prompting cadence gate. ``PROP_EXPIRY_PROMPT_SECONDS <= 0`` pauses it.

    There is no per-ticket rate limit (a ticket is prompted exactly once, guarded
    by the status flip), so this is just an on/off pause knob; any positive value
    (or unset) means "active".
    """
    raw = os.environ.get("PROP_EXPIRY_PROMPT_SECONDS")
    if raw is None or str(raw).strip() == "":
        return True
    try:
        return float(raw) > 0
    except (ValueError, TypeError):
        return True


def build_expiry_keyboard(ticket_id: str) -> Dict[str, Any]:
    """Telegram inline-keyboard ``reply_markup`` for the Yes/No expiry prompt.

    ``callback_data`` is ``propexp:<y|n>:<ticket_id>`` — well under Telegram's
    64-byte limit for a ``prop-manual-<12 hex>`` id.
    """
    return {
        "inline_keyboard": [[
            {"text": "✅ Yes — I placed it",
             "callback_data": f"{EXPIRY_CB_PREFIX}:y:{ticket_id}"},
            {"text": "❌ No — not placed",
             "callback_data": f"{EXPIRY_CB_PREFIX}:n:{ticket_id}"},
        ]]
    }


def find_tickets_to_prompt(
    *, account_id: Optional[str] = None, now: Optional[datetime] = None,
    max_age_hours: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Expired, un-acted, not-yet-prompted tickets recent enough to ask about.

    Built on ``prop_reconcile.find_unacted_tickets`` (status == ``emitted``, past
    ``valid_until``, no matching fill) — so once a ticket is flipped to
    ``expiry_prompted`` it drops out here automatically (the idempotency guard).
    The recency window (``max_age_hours``) drops tickets that expired long ago so
    a historical backlog can't spam the operator.
    """
    now = now or _now()
    max_age = max_age_hours if max_age_hours is not None else _max_age_hours()
    cutoff = now - timedelta(hours=max_age) if max_age > 0 else None
    try:
        stale = prop_reconcile.find_unacted_tickets(
            account_id=account_id, now=now, limit=200)
    except Exception as exc:  # noqa: BLE001 — never break the trader loop
        logger.warning("prop_expiry_prompt: find_unacted_tickets failed: %s", exc)
        return []
    out: List[Dict[str, Any]] = []
    for t in stale:
        vu = _parse_iso(t.get("valid_until"))
        if cutoff is not None and vu is not None and vu < cutoff:
            continue  # expired too long ago — not worth asking
        out.append(t)
    return out


def run_prop_expiry_prompts(
    *, now: Optional[datetime] = None,
    emitter: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> Dict[str, Any]:
    """Send a Yes/No 'did you place this?' prompt for any just-expired ticket.

    Called once per trader tick. For each newly-expired un-acted ticket it sends
    the prompt and — only on a confirmed send — flips the ticket to
    ``expiry_prompted`` so it is never prompted twice. Returns a stats dict
    ``{candidates, prompted, failed, paused}``. Never raises.
    """
    stats = {"candidates": 0, "prompted": 0, "failed": 0, "paused": False}
    if not _enabled():
        stats["paused"] = True
        return stats

    now = now or _now()
    try:
        tickets = find_tickets_to_prompt(now=now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_expiry_prompt: scan failed: %s", exc)
        return stats
    stats["candidates"] = len(tickets)
    if not tickets:
        return stats

    if emitter is None:
        from src.prop.breakout_notify import emit_prop_expiry_prompt as emitter  # type: ignore

    for t in tickets:
        ticket_id = t.get("ticket_id")
        if not ticket_id:
            continue
        try:
            sent = bool(emitter(t))
        except Exception as exc:  # noqa: BLE001 — emission never fatal
            logger.warning("prop_expiry_prompt: emit failed for %s: %s",
                           ticket_id, exc)
            sent = False
        if not sent:
            stats["failed"] += 1
            continue  # leave status 'emitted' so it retries next tick
        try:
            prop_journal.set_ticket_status(ticket_id, _PROMPTED_STATUS)
        except Exception as exc:  # noqa: BLE001
            logger.warning("prop_expiry_prompt: status flip failed for %s: %s",
                           ticket_id, exc)
        stats["prompted"] += 1
        logger.info(
            "prop_expiry_prompt: prompted %s %s [%s] (ticket=%s) — awaiting Y/N",
            t.get("symbol"), t.get("direction"), t.get("account_id"), ticket_id,
        )

    return stats


def handle_expiry_callback(callback_data: str) -> Optional[Dict[str, Any]]:
    """Process a ``propexp:<y|n>:<ticket_id>`` button press (transport-agnostic).

    Performs the ticket-status DB write and returns what the bot should show:

        {"answer": "yes"|"no", "ticket_id": str,
         "ack": str,                 # short text to replace the prompt message
         "send_prompt": bool}        # True → also send REPORT_PROMPT (Yes path)

    Returns ``None`` when ``callback_data`` is not a prop-expiry callback (the
    caller falls through to its other handlers). Raises nothing the caller must
    handle beyond the normal try/except around a callback.
    """
    if not callback_data or not callback_data.startswith(EXPIRY_CB_PREFIX + ":"):
        return None
    parts = callback_data.split(":", 2)
    if len(parts) != 3:
        return None
    _, verb, ticket_id = parts
    ticket_id = ticket_id.strip()
    if not ticket_id:
        return None

    if verb == "n":  # operator says: NOT placed
        try:
            prop_journal.set_ticket_status(ticket_id, "expired")
        except Exception as exc:  # noqa: BLE001
            logger.warning("prop_expiry_prompt: 'expired' flip failed for %s: %s",
                           ticket_id, exc)
        return {
            "answer": "no",
            "ticket_id": ticket_id,
            "ack": f"❌ Logged as expired — ticket not placed.\n({ticket_id})",
            "send_prompt": False,
        }

    if verb == "y":  # operator says: I placed it → collect the fill details
        try:
            prop_journal.set_ticket_status(ticket_id, "awaiting_report")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "prop_expiry_prompt: 'awaiting_report' flip failed for %s: %s",
                ticket_id, exc)
        return {
            "answer": "yes",
            "ticket_id": ticket_id,
            "ack": ("✅ Got it — you placed it. Send the trade details so I can "
                    "log + monitor it (prompt below)."),
            "send_prompt": True,
        }

    return None


def send_test_prompt(
    *, account_id: str = "breakout_1", symbol: str = "ETHUSDT",
    direction: str = "short", entry: float = 1619.99, sl: float = 1644.0,
    tp: float = 1550.0, qty: float = 0.73,
    emitter: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> Optional[str]:
    """Send ONE Yes/No expiry prompt for a **throwaway** test ticket.

    For operator verification of the live button round-trip (the prop bot sends
    the inline keyboard; the answer comes back to ``claude_bridge``'s ``propexp:*``
    handler). A ``prop-test-<uuid>`` ticket is journaled (status ``emitted``,
    already past ``valid_until``) so clicking **Yes** (→ ``awaiting_report``) or
    **No** (→ ``expired``) mutates only this throwaway row — never a real prop
    position. Returns the test ticket id on a confirmed send, else ``None``.
    Best-effort: a failure logs a WARNING and returns ``None``.
    """
    now = _now()
    ticket_id = f"prop-test-{uuid.uuid4().hex[:12]}"
    ticket = {
        "ticket_id": ticket_id, "account_id": account_id,
        "strategy": "expiry_prompt_test", "symbol": symbol, "direction": direction,
        "side": "Sell" if direction == "short" else "Buy",
        "entry": entry, "sl": sl, "tp": tp, "qty": qty,
        "signal_time": (now - timedelta(hours=2)).isoformat(),
        "valid_until": (now - timedelta(hours=1)).isoformat(),
        "status": "emitted",
    }
    try:
        prop_journal.record_ticket(ticket)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_expiry_prompt: test ticket write failed: %s", exc)
        return None
    if emitter is None:
        from src.prop.breakout_notify import emit_prop_expiry_prompt as emitter  # type: ignore
    try:
        sent = bool(emitter(ticket))
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_expiry_prompt: test prompt emit failed: %s", exc)
        return None
    return ticket_id if sent else None


__all__ = [
    "EXPIRY_CB_PREFIX",
    "build_expiry_keyboard",
    "find_tickets_to_prompt",
    "run_prop_expiry_prompts",
    "handle_expiry_callback",
    "send_test_prompt",
]
