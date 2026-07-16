"""Prop ticket price-invalidation prompt — proactively warn when an emitted-but-
unreported prop ticket's opportunity window has closed.

The Breakout prop account is a **manual bridge**: the bot emits a paste-ready
ticket (``breakout_executor.emit_prop_ticket``) with the place-decision Yes/No
keyboard attached, then waits for the operator to report whether they placed it.
Today the ONLY thing that closes that waiting loop for an un-acted ticket is the
:mod:`prop_expiry_prompt` **timeout** path (the ticket passes its
``valid_until``).

But a ticket can go stale *before* it times out: while the bot is still waiting
for the operator's Yes/No, **price can move beyond the trade's brackets** — it
trades to/through the SL or the TP — so the entry the ticket describes is no
longer worth placing (a run to SL means the setup already failed; a run to TP
means the move already happened and placing now just chases it). This module
turns that into an **active, proactive update** the operator asked for
(2026-07-16): once per trader tick, for each still-``emitted`` ticket it fetches
the current price and, if price has left the ``[SL, TP]`` band, sends a prop-bot
message:

    🚫 PROP SETUP NO LONGER VALID — ETHUSDT SHORT …
        ⚠️ Do NOT place this trade if you haven't already.
        Did you already place it?   [✅ Yes — I placed it]  [❌ No — not placed]

The Yes/No answer reuses the **existing** ``propexp:*`` callback
(:func:`prop_expiry_prompt.handle_expiry_callback`), so no new bot wiring is
needed:

- **No**  → the ticket is logged ``expired`` (never placed — nothing to track).
- **Yes** → the ticket moves to ``awaiting_report`` and the operator gets the
  executor-assistant ``REPORT_PROMPT`` to paste the fill (it linked back via
  ``match_fill_to_ticket``, which accepts ``awaiting_report`` and — for a fill
  pasted directly without tapping a button — ``invalidated_prompted``).

Lifecycle (status on the ``prop_tickets`` row), parallel to the timeout path:

    emitted ──(price beyond brackets, warn+ask)──▶ invalidated_prompted ──┬─ No ──▶ expired
                                                                           └─ Yes ─▶ awaiting_report ──(fill)─▶ filled/closed

Idempotency is the status flip itself: the detector only scans ``emitted``
tickets, so once a ticket flips to ``invalidated_prompted`` it is never
re-detected here — AND it drops out of the timeout path's
``find_unacted_tickets`` (status == ``emitted`` only) too, so the two paths can
never double-prompt the same ticket. The flip happens **only after a confirmed
send**, so a delivery failure simply retries next tick.

Baseline, not gated (Prime Directive — no default-off flag in front of a
required capability). Knobs: ``PROP_INVALIDATION_PROMPT_SECONDS`` ``<= 0`` pauses
prompting without a redeploy; ``PROP_INVALIDATION_PROMPT_MAX_AGE_HOURS``
(default 12) bounds how old an emitted ticket may be before we stop bothering to
warn — an ancient un-acted ticket is the timeout path's job, not this one, and a
historical backlog can never spam on first deploy. Best-effort + isolated
everywhere — a prompt failure never propagates into the trader loop.

The price-fetch + bracket-crossing helpers deliberately mirror
:mod:`prop_sl_tp_alert` (same ``connector_for_symbol`` + ``fetch_candles`` last-
close path, same direction logic) rather than importing that module's private
helpers — keeping this observe-only prompt self-contained.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from src.prop import prop_journal

logger = logging.getLogger(__name__)

_DEFAULT_MAX_AGE_HOURS = 12.0
_SCAN_STATUS = "emitted"          # only a not-yet-acted ticket can be invalidated
_PROMPTED_STATUS = "invalidated_prompted"


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
    raw = os.environ.get("PROP_INVALIDATION_PROMPT_MAX_AGE_HOURS")
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_MAX_AGE_HOURS
    try:
        return float(raw)
    except (ValueError, TypeError):
        return _DEFAULT_MAX_AGE_HOURS


def _enabled() -> bool:
    """Prompting cadence gate. ``PROP_INVALIDATION_PROMPT_SECONDS <= 0`` pauses it.

    There is no per-ticket rate limit (a ticket is prompted exactly once, guarded
    by the status flip), so this is just an on/off pause knob; any positive value
    (or unset) means "active".
    """
    raw = os.environ.get("PROP_INVALIDATION_PROMPT_SECONDS")
    if raw is None or str(raw).strip() == "":
        return True
    try:
        return float(raw) > 0
    except (ValueError, TypeError):
        return True


# --- price + bracket-crossing (mirrors prop_sl_tp_alert; kept local so this
#     observe-only prompt is self-contained) --------------------------------
def _fetch_current_price(symbol: str, settings: dict) -> Optional[float]:
    """Latest close price for *symbol* (last 5m bar). None on any failure."""
    try:
        from src.runtime.market_data import connector_for_symbol, fetch_candles
        client = connector_for_symbol(symbol, settings)
        if client is None:
            return None
        df = fetch_candles(symbol, "5m", settings=settings,
                           exchange_client=client, limit=3)
        if df is None or df.empty:
            return None
        return float(df.iloc[-1]["close"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_invalidation_prompt: price fetch failed for %s: %s",
                       symbol, exc)
        return None


def _sl_crossed(direction: str, current: float, sl: float) -> bool:
    d = direction.lower()
    if d in ("buy", "long"):
        return current <= sl
    if d in ("sell", "short"):
        return current >= sl
    return False


def _tp_crossed(direction: str, current: float, tp: float) -> bool:
    d = direction.lower()
    if d in ("buy", "long"):
        return current >= tp
    if d in ("sell", "short"):
        return current <= tp
    return False


def bracket_invalidation(
    direction: str, current: float, sl: Optional[float], tp: Optional[float]
) -> Optional[str]:
    """Return ``"sl"``/``"tp"`` if *current* has left the ``[SL, TP]`` band, else None.

    "Beyond the brackets" = price has traded to or through either bracket, so the
    described entry is no longer a live setup. A missing bracket simply can't be
    crossed (contributes no invalidation).
    """
    try:
        if sl is not None and float(sl) > 0 and _sl_crossed(direction, current, float(sl)):
            return "sl"
        if tp is not None and float(tp) > 0 and _tp_crossed(direction, current, float(tp)):
            return "tp"
    except (ValueError, TypeError):
        return None
    return None


def find_tickets_to_check(
    *, account_id: Optional[str] = None, now: Optional[datetime] = None,
    max_age_hours: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Still-``emitted`` tickets recent enough to warn about, with a usable bracket.

    Only ``emitted`` tickets are scanned (a ticket already prompted — by this path
    or the timeout path — has moved off ``emitted`` and drops out automatically,
    the idempotency + no-double-prompt guard). The recency window
    (``max_age_hours``, on ``signal_time`` → ``created_at`` fallback) drops old
    un-acted tickets so a backlog can't spam and so the *timeout* path owns the
    stale end of the lifecycle.
    """
    now = now or _now()
    max_age = max_age_hours if max_age_hours is not None else _max_age_hours()
    cutoff = now - timedelta(hours=max_age) if max_age > 0 else None
    try:
        tickets = prop_journal.list_tickets(
            account_id=account_id, status=_SCAN_STATUS, limit=200)
    except Exception as exc:  # noqa: BLE001 — never break the trader loop
        logger.warning("prop_invalidation_prompt: list_tickets failed: %s", exc)
        return []
    out: List[Dict[str, Any]] = []
    for t in tickets:
        # Needs at least one bracket to be invalidatable.
        if not (t.get("sl") or t.get("tp")):
            continue
        if cutoff is not None:
            ts = _parse_iso(t.get("signal_time")) or _parse_iso(t.get("created_at"))
            if ts is not None and ts < cutoff:
                continue  # emitted too long ago — the timeout path owns this one
        out.append(t)
    return out


def run_prop_invalidation_prompts(
    *, now: Optional[datetime] = None, settings: Optional[dict] = None,
    emitter: Optional[Callable[[Dict[str, Any], float, str], bool]] = None,
) -> Dict[str, Any]:
    """Warn + re-ask for any emitted ticket whose price has left its brackets.

    Called once per trader tick. For each still-``emitted`` ticket whose current
    price has crossed the SL or TP, sends the "no longer valid — do NOT place it;
    did you place it?" prompt and — only on a confirmed send — flips the ticket to
    ``invalidated_prompted`` so it is never prompted twice. Returns stats
    ``{candidates, checked, invalidated, prompted, failed, paused}``. Never raises.
    """
    stats = {"candidates": 0, "checked": 0, "invalidated": 0,
             "prompted": 0, "failed": 0, "paused": False}
    if not _enabled():
        stats["paused"] = True
        return stats

    now = now or _now()
    try:
        tickets = find_tickets_to_check(now=now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_invalidation_prompt: scan failed: %s", exc)
        return stats
    stats["candidates"] = len(tickets)
    if not tickets:
        return stats

    if settings is None:
        try:
            from src.runtime.validation import build_settings_from_env
            settings = build_settings_from_env()
        except Exception as exc:  # noqa: BLE001
            logger.warning("prop_invalidation_prompt: settings build failed: %s", exc)
            settings = {}

    if emitter is None:
        from src.prop.breakout_notify import emit_prop_invalidation_prompt as emitter  # type: ignore

    for t in tickets:
        ticket_id = t.get("ticket_id")
        if not ticket_id:
            continue
        symbol = str(t.get("symbol") or "")
        direction = str(t.get("direction") or "")
        price = _fetch_current_price(symbol, settings)
        if price is None:
            continue  # can't judge without a price — retry next tick
        stats["checked"] += 1
        which = bracket_invalidation(direction, price, t.get("sl"), t.get("tp"))
        if which is None:
            continue  # still inside the brackets — a live setup, leave it be
        stats["invalidated"] += 1
        try:
            sent = bool(emitter(t, price, which))
        except Exception as exc:  # noqa: BLE001 — emission never fatal
            logger.warning("prop_invalidation_prompt: emit failed for %s: %s",
                           ticket_id, exc)
            sent = False
        if not sent:
            stats["failed"] += 1
            continue  # leave status 'emitted' so it retries next tick
        try:
            prop_journal.set_ticket_status(ticket_id, _PROMPTED_STATUS)
        except Exception as exc:  # noqa: BLE001
            logger.warning("prop_invalidation_prompt: status flip failed for %s: %s",
                           ticket_id, exc)
        stats["prompted"] += 1
        logger.info(
            "prop_invalidation_prompt: warned %s %s [%s] price=%.6f crossed=%s "
            "(ticket=%s) — do-not-place + awaiting Y/N",
            symbol, direction, t.get("account_id"), price, which, ticket_id,
        )

    if stats["prompted"] or stats["failed"]:
        logger.info(
            "prop_invalidation_prompt: candidates=%d checked=%d invalidated=%d "
            "prompted=%d failed=%d",
            stats["candidates"], stats["checked"], stats["invalidated"],
            stats["prompted"], stats["failed"],
        )
    return stats


__all__ = [
    "bracket_invalidation",
    "find_tickets_to_check",
    "run_prop_invalidation_prompts",
]
