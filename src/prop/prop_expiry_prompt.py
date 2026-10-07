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

⚠️ **Superseded in part by the operator directive of 2026-10-07** ("if a
ticket expires and I haven't logged a trade, the system should assume that the
trade wasnt placed and the ticket should be kept alive. And the prop accounts
should have their own separate flow that isn't contaminated by the telegram
channels activity"). The canonical per-executor state machine is
``docs/ARCHITECTURE-CANONICAL.md`` § "Prop ticket flow". What this module does
on every trader tick now (:func:`run_prop_expiry_prompts`):

- **manual** account, ticket just expired with no fill → one NOTICE ("recorded
  as NOT placed") with a single ✅ "I did place it" button, then the ticket is
  set ``expired``. It never waits in ``expiry_prompted``.
- **rest / phone / browser** (machine-executed) account → no Telegram at all. A ticket still ``emitted``
  ``MACHINE_EXPIRY_GRACE`` after its ``valid_until`` is set ``expired`` (the
  REST executor normally reports its own terminal ``skipped`` first).
- unanswered ``expiry_prompted`` / ``invalidated_prompted`` tickets past their
  validity (legacy rows, or a manual invalidation warning) → ``expired``.

Every write is a compare-and-set on the status read, so a fill, a claim or a
placement that lands in between is never overwritten.

    manual:  emitted ──(expired, no fill; notice sent)──▶ expired ──(✅ late)──▶ awaiting_report ──(fill)─▶ filled
    rest:    emitted ──(executor intake)──▶ skipped "expired unplaced; …"   (or ──(T + grace)──▶ expired)
    phone:   emitted ──(never claimed; T + grace)──▶ expired

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
_EXPIRED_STATUS = "expired"
#: Statuses the expiry sweep may end as ``expired`` once validity has passed
#: with no fill logged.
_SWEEPABLE = ("emitted", _PROMPTED_STATUS, "invalidated_prompted")
#: Statuses a ``propexp:*`` tap may move from. Anything else (placed, filled,
#: closed, skipped, claimed, ...) is a later state a tap must never overwrite.
_TAPPABLE_FROM = ("emitted", _PROMPTED_STATUS, "invalidated_prompted", _EXPIRED_STATUS)
#: How long after ``valid_until`` a machine account's still-``emitted`` ticket
#: waits before the trader ends it. The REST executor's timer runs every few
#: minutes and reports its own terminal ``skipped`` (with its verdict) first;
#: this only ends the ticket when that executor was off or read_only, or a phone
#: never claimed it.
MACHINE_EXPIRY_GRACE = timedelta(minutes=30)


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


# The SAME Yes/No keyboard is attached to a freshly-emitted ticket so the
# operator reports back with a tap instead of typing — named distinctly for that
# call site (breakout_notify.emit_prop_signal). Shares the propexp:* callbacks,
# so the existing claude_bridge handler drives both: ✅ → fill prompt, ❌ → logged
# not-placed.
def build_place_decision_keyboard(ticket_id: str) -> Dict[str, Any]:
    """Yes/No 'did you place this trade?' keyboard for a just-emitted ticket."""
    return build_expiry_keyboard(ticket_id)


def build_late_fill_keyboard(ticket_id: str) -> Dict[str, Any]:
    """The single "I did place it" button on a manual-account expiry notice.
    The ticket is already ``expired``; the tap moves it to ``awaiting_report``
    and sends the report prompt (same ``propexp:y`` handler)."""
    return {
        "inline_keyboard": [[
            {"text": "✅ I did place it",
             "callback_data": f"{EXPIRY_CB_PREFIX}:y:{ticket_id}"},
        ]]
    }


def _flows() -> Dict[str, str]:
    try:
        from src.prop.platform import ticket_flows

        return ticket_flows()
    except Exception as exc:  # noqa: BLE001 — every account reads as manual
        logger.warning("prop_expiry_prompt: platform read failed: %s", exc)
        return {}


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


def _expire(ticket: Dict[str, Any], why: str) -> int:
    """CAS one swept ticket to ``expired``; 0 when it moved on first."""
    tid = str(ticket.get("ticket_id") or "")
    try:
        n = prop_journal.transition_ticket_status(
            tid, _EXPIRED_STATUS, from_statuses=(ticket.get("status"),),
            account_id=ticket.get("account_id") or None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_expiry_prompt: expire failed for %s: %s", tid, exc)
        return 0
    if n:
        logger.info("prop_expiry_prompt: %s %s %s [%s] %s -> expired (%s)",
                    tid, ticket.get("symbol"), ticket.get("direction"),
                    ticket.get("account_id"), ticket.get("status"), why)
    return n


def run_prop_expiry_prompts(
    *, now: Optional[datetime] = None,
    emitter: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> Dict[str, Any]:
    """End every expired, unfilled ticket as ``expired`` (not placed), per
    executor type. Called once per trader tick; never raises.

    ``emitter`` sends the manual-account notice (default
    :func:`breakout_notify.emit_prop_expiry_notice`); a manual ticket is set
    ``expired`` only after a confirmed send, so a failed send retries next tick.
    Past the recency window, or while paused, it is set ``expired`` silently.
    Machine accounts never reach the emitter.

    Stats: ``candidates`` (manual notices due), ``prompted`` (sent and
    expired), ``failed``, ``expired`` (silent manual / legacy), ``machine_expired``,
    ``rest_left_to_executor`` (REST tickets inside the grace), ``paused``.
    """
    stats = {"candidates": 0, "prompted": 0, "failed": 0, "paused": False,
             "expired": 0, "machine_expired": 0, "rest_left_to_executor": 0}
    stats["paused"] = paused = not _enabled()
    now = now or _now()
    try:
        stale = prop_reconcile.find_unacted_tickets(
            now=now, limit=200, statuses=_SWEEPABLE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_expiry_prompt: scan failed: %s", exc)
        return stats

    from src.prop.platform import FLOW_MANUAL, FLOW_PHONE, MACHINE_FLOWS
    flows = _flows()
    max_age = _max_age_hours()
    cutoff = now - timedelta(hours=max_age) if max_age > 0 else None

    notices: List[Dict[str, Any]] = []
    for t in stale:
        if not t.get("ticket_id"):
            continue
        vu = _parse_iso(t.get("valid_until"))
        if vu is None:
            continue  # validity unreadable: not known to have passed (fail-safe)
        flow = flows.get(str(t.get("account_id") or ""), FLOW_MANUAL)
        status = t.get("status")
        if flow in MACHINE_FLOWS:
            if status == "emitted" and now < vu + MACHINE_EXPIRY_GRACE:
                if flow != FLOW_PHONE:  # rest / browser: the VM executor's intake
                    stats["rest_left_to_executor"] += 1
                continue  # its executor gets the first word; it blocks nothing
            stats["machine_expired"] += _expire(t, f"{flow} account, no fill logged")
            continue
        if status != "emitted":
            # An unanswered prompt past validity: not placed (directive 2026-10-07).
            stats["expired"] += _expire(t, f"unanswered {status}, no fill logged")
            continue
        if paused or (cutoff is not None and vu < cutoff):
            stats["expired"] += _expire(t, "no fill logged; notice " +
                                        ("paused" if paused else "past the recency window"))
            continue
        notices.append(t)

    stats["candidates"] = len(notices)
    if not notices:
        return stats
    if emitter is None:
        from src.prop.breakout_notify import emit_prop_expiry_notice as emitter  # type: ignore

    for t in notices:
        ticket_id = t.get("ticket_id")
        try:
            sent = bool(emitter(t))
        except Exception as exc:  # noqa: BLE001 — emission never fatal
            logger.warning("prop_expiry_prompt: emit failed for %s: %s",
                           ticket_id, exc)
            sent = False
        if not sent:
            stats["failed"] += 1
            continue  # stays 'emitted' (blocks nothing past validity); retries
        _expire(t, "no fill logged; notice sent")
        stats["prompted"] += 1

    return stats


def _refusal(ticket_id: str, why: str) -> Dict[str, Any]:
    return {"answer": "refused", "ticket_id": ticket_id,
            "ack": f"⚠️ Nothing changed — {why}\n({ticket_id})", "send_prompt": False}


def handle_expiry_callback(callback_data: str) -> Optional[Dict[str, Any]]:
    """Process a ``propexp:<y|n>:<ticket_id>`` button press (transport-agnostic).

    Performs the ticket-status DB write and returns what the bot should show:

        {"answer": "yes"|"no"|"refused", "ticket_id": str,
         "ack": str,                 # short text to replace the prompt message
         "send_prompt": bool}        # True → also send REPORT_PROMPT (Yes path)

    Each write is a compare-and-set from :data:`_TAPPABLE_FROM`, so a tap can
    never overwrite a placed / filled / closed ticket, and a tap on a REST- or
    phone-executed account's ticket is refused without a write (operator
    directive 2026-10-07: those flows are not driven from Telegram).

    Returns ``None`` when ``callback_data`` is not a prop-expiry callback (the
    caller falls through to its other handlers).
    """
    if not callback_data or not callback_data.startswith(EXPIRY_CB_PREFIX + ":"):
        return None
    parts = callback_data.split(":", 2)
    if len(parts) != 3:
        return None
    _, verb, ticket_id = parts
    ticket_id = ticket_id.strip()
    if not ticket_id or verb not in ("y", "n"):
        return None

    try:
        ticket = prop_journal.get_ticket(ticket_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_expiry_prompt: ticket read failed for %s: %s", ticket_id, exc)
        return _refusal(ticket_id, "the ticket could not be read.")
    if ticket is None:
        return _refusal(ticket_id, "no such ticket.")
    account_id = str(ticket.get("account_id") or "")
    from src.prop.platform import MACHINE_FLOWS
    flow = _flows().get(account_id)
    if flow in MACHINE_FLOWS:
        return _refusal(ticket_id, f"{account_id} is executed by its {flow} executor; "
                                   "its tickets are not changed from Telegram.")

    to_status = _EXPIRED_STATUS if verb == "n" else "awaiting_report"
    try:
        n = prop_journal.transition_ticket_status(
            ticket_id, to_status, from_statuses=_TAPPABLE_FROM,
            account_id=account_id or None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_expiry_prompt: %r flip failed for %s: %s",
                       to_status, ticket_id, exc)
        n = 0
    if n != 1:
        return _refusal(ticket_id, f"the ticket is already {ticket.get('status')!r}.")

    if verb == "n":  # operator says: NOT placed
        return {
            "answer": "no",
            "ticket_id": ticket_id,
            "ack": f"❌ Logged — you did not place this trade. Nothing to track.\n({ticket_id})",
            "send_prompt": False,
        }
    return {  # operator says: I placed it → collect the fill details
        "answer": "yes",
        "ticket_id": ticket_id,
        "ack": ("✅ Got it — you placed it. Send the trade details so I can "
                "log + monitor it (prompt below)."),
        "send_prompt": True,
    }


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
    "build_place_decision_keyboard",
    "build_late_fill_keyboard",
    "MACHINE_EXPIRY_GRACE",
    "find_tickets_to_prompt",
    "run_prop_expiry_prompts",
    "handle_expiry_callback",
    "send_test_prompt",
]
