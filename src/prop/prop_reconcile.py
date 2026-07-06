"""Prop manual-bridge reconciliation + rule-distance (P3).

Two read-only analytics over the prop journal (``src.prop.prop_journal``):

1. :func:`match_fill_to_ticket` — link an inbound fill to the outbound ticket
   it most likely came from (same account + symbol + direction, newest open
   ticket). Used by the ingest path so a fill carries its ``ticket_id``.
2. :func:`find_unacted_tickets` — outbound tickets that were emitted, have
   passed their ``valid_until``, and never got a matching fill. These are the
   "drift" the design's P3 alerts on (a ticket the operator/executor never
   acted on, or a fill that never got reported back).
3. :func:`compute_rule_distance` — distance from the latest account-status
   snapshot to the two account-killer limits (daily-loss and static-DD),
   resolved from the account's prop ruleset. Drives the dashboard panel.

Pure analytics — never sends an order, never mutates trading state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.prop import prop_journal

logger = logging.getLogger(__name__)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _norm_direction(value: Any) -> str:
    """Normalise direction synonyms to the ticket vocabulary (long/short).

    Terminal UIs (Breakout/DXTrade) say Buy/Sell while outbound tickets carry
    long/short; an inbound report typed from the terminal wording must still
    match its ticket (prop_fills id 15, 2026-07-05: 'buy' failed the exact
    compare vs the ticket's 'long' and left it un-linked, BL-20260705-PROP-
    DIRECTION-SYNONYM-MATCH). Same synonym sets as ``breakout_executor`` /
    ``funding``. Unknown values pass through lowered (never raises).
    """
    d = str(value or "").strip().lower()
    if d in ("buy", "b", "long", "1"):
        return "long"
    if d in ("sell", "s", "short", "-1"):
        return "short"
    return d


# Open-lifecycle ticket statuses a fallback match may link to, and which of
# them actually represent a *position* (something a close can act on).
_OPEN_TICKET_STATUSES = ("emitted", "placed", "filled", "expiry_prompted", "awaiting_report")
# A ``closed`` report closes a real position, so it may ONLY link to a ticket
# that has (or plausibly has) a position — a filled position, an operator-
# confirmed-placed ticket awaiting its paste, or a working `placed` limit that
# may have just filled. It must NEVER link to a never-placed `emitted` (or its
# `expiry_prompted` variant) SIGNAL: doing so marked a phantom signal "closed"
# and left the real filled position open (BL-20260706-PROP-CLOSE-MISLINK — my
# ETH close (fill 17) landed on the newer emitted ticket 849ece101a3c instead of
# the filled position ticket 5bc393741ec4). Ordered by preference (best first).
_CLOSE_LINKABLE_STATUSES = ("filled", "awaiting_report", "placed")


def match_fill_to_ticket(fill: Dict[str, Any]) -> Optional[str]:
    """Return the ticket_id an inbound fill most likely belongs to (or None).

    Explicit ``fill['ticket_id']`` wins. Otherwise a still-open ticket for the
    same account + symbol + direction (direction compared synonym-normalised:
    buy==long, sell==short), chosen by lifecycle appropriateness:

    - a **closing** report (``status='closed'``) links only to a ticket that
      represents a *position* — ``filled`` (best), then ``awaiting_report``,
      then ``placed`` — newest within each; it NEVER links to a never-placed
      ``emitted`` / ``expiry_prompted`` signal (that was the recurring mis-link
      that left the real position open, BL-20260706-PROP-CLOSE-MISLINK). If no
      position-bearing ticket matches, returns ``None`` so the close is
      journaled unlinked rather than corrupting an unrelated signal ticket.
    - any other report keeps the prior behaviour: the newest still-open ticket
      (``emitted``/``placed``/``filled``/``expiry_prompted``/``awaiting_report``).
      ``placed``/``expiry_prompted``/``awaiting_report`` are all "awaiting a fill
      report" — a working order or an operator-confirmed ticket whose later
      fill/close must link back.
    """
    explicit = fill.get("ticket_id")
    if explicit:
        return str(explicit)
    account_id = str(fill.get("account_id") or "").strip()
    if not account_id:
        return None
    symbol = str(fill.get("symbol") or "").upper()
    direction = _norm_direction(fill.get("direction"))
    inbound_status = str(fill.get("status") or "").strip().lower()
    candidates = prop_journal.list_tickets(account_id=account_id, limit=200)

    def _matches(t: Dict[str, Any]) -> bool:
        if symbol and str(t.get("symbol") or "").upper() != symbol:
            return False
        if direction and _norm_direction(t.get("direction")) != direction:
            return False
        return True

    # candidates is newest-first (list_tickets ORDER BY created_at DESC).
    if inbound_status == "closed":
        # Preference-ranked over position-bearing statuses; newest within each.
        for status in _CLOSE_LINKABLE_STATUSES:
            for t in candidates:
                if t.get("status") == status and _matches(t):
                    return t.get("ticket_id")
        return None

    for t in candidates:
        if t.get("status") not in _OPEN_TICKET_STATUSES:
            continue
        if _matches(t):
            return t.get("ticket_id")
    return None


def find_unacted_tickets(
    *, account_id: Optional[str] = None, now: Optional[datetime] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Emitted tickets past ``valid_until`` with no matching fill reported.

    A ticket is considered acted-on if a ``prop_fills`` row references its
    ``ticket_id`` OR matches its account+symbol+direction. Anything still
    ``emitted`` whose validity window has elapsed with no such fill is a
    drift candidate the operator should know about.
    """
    now = now or datetime.now(timezone.utc)
    tickets = prop_journal.list_tickets(account_id=account_id, limit=limit)
    fills = prop_journal.list_fills(account_id=account_id, limit=1000)
    acted_ids = {f.get("ticket_id") for f in fills if f.get("ticket_id")}
    # The (symbol, direction) fallback match MUST be account-scoped: when this is
    # called with account_id=None (the global path the expiry-prompt scan uses),
    # `fills` spans every prop account, and an unscoped key would let a fill on
    # account A mask a genuinely-unacted ticket on account B for the same
    # symbol+direction — that ticket would never be flagged as drift / prompted.
    # Keying on account too keeps the cross-account isolation the design's
    # multi-account-from-day-one invariant requires (matches the account-scoped
    # keys in prop_monitor_pulse._position_key + match_fill_to_ticket).
    acted_keys = {
        (str(f.get("account_id") or "").strip(),
         str(f.get("symbol") or "").upper(),
         _norm_direction(f.get("direction")))
        for f in fills
    }
    out: List[Dict[str, Any]] = []
    for t in tickets:
        if t.get("status") != "emitted":
            continue
        if t.get("ticket_id") in acted_ids:
            continue
        key = (str(t.get("account_id") or "").strip(),
               str(t.get("symbol") or "").upper(),
               _norm_direction(t.get("direction")))
        if key in acted_keys:
            continue
        vu = _parse_iso(t.get("valid_until"))
        if vu is not None and now <= vu:
            continue  # still within its validity window — not yet stale
        out.append(t)
    return out


def _ruleset_for(account_id: str):
    """Resolve the prop ruleset (limits + account size) for an account."""
    try:
        from src.prop.account_rulesets import all_account_units

        unit = all_account_units().get(account_id)
        return unit.ruleset if unit else None
    except Exception as exc:  # noqa: BLE001 — fail soft to "unknown limits"
        logger.warning("prop_reconcile: ruleset lookup failed for %s: %s",
                       account_id, exc)
        return None


def compute_rule_distance(
    account_id: str, status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Distance from the latest account status to the two account-killer limits.

    Returns a dict with the resolved limits and the computed distances; any
    value that can't be derived from the available status fields is ``None``
    (never a fabricated 0). ``status`` defaults to the latest snapshot.
    """
    status = status or prop_journal.latest_account_status(account_id) or {}
    rs = _ruleset_for(account_id)

    account_size = getattr(rs, "account_size_usd", None) if rs else None
    limits = getattr(rs, "limits", None) if rs else None
    daily_loss_pct = getattr(limits, "daily_loss_pct", None) if limits else None
    max_dd_pct = getattr(limits, "max_drawdown_pct", None) if limits else None

    balance = status.get("balance")
    equity = status.get("equity")
    realized_today = status.get("realized_today")
    unrealized = status.get("unrealized")
    day_start = status.get("day_start_balance")

    # Daily-loss: limit amount is daily_loss_pct of the day-start balance.
    day_basis = day_start if day_start is not None else balance
    if day_basis is None:
        day_basis = account_size
    daily_loss_limit_usd = (
        daily_loss_pct * day_basis
        if (daily_loss_pct is not None and day_basis is not None) else None
    )
    # Day P&L = realized today + unrealized (equity-basis, like Breakout).
    day_pnl = None
    if realized_today is not None or unrealized is not None:
        day_pnl = (realized_today or 0.0) + (unrealized or 0.0)
    daily_loss_used = (-day_pnl if (day_pnl is not None and day_pnl < 0) else 0.0) \
        if day_pnl is not None else None
    distance_to_daily = (
        daily_loss_limit_usd - daily_loss_used
        if (daily_loss_limit_usd is not None and daily_loss_used is not None) else None
    )

    # Static drawdown floor = account_size × (1 − max_dd_pct), off the start.
    dd_floor = (
        account_size * (1.0 - max_dd_pct)
        if (account_size is not None and max_dd_pct is not None) else None
    )
    equity_now = equity if equity is not None else balance
    distance_to_dd = (
        equity_now - dd_floor
        if (equity_now is not None and dd_floor is not None) else None
    )

    return {
        "account_id": account_id,
        "as_of": status.get("reported_at"),
        "account_size_usd": account_size,
        "balance": balance,
        "equity": equity_now,
        "day_pnl": day_pnl,
        "daily_loss_pct": daily_loss_pct,
        "daily_loss_limit_usd": daily_loss_limit_usd,
        "daily_loss_used_usd": daily_loss_used,
        "distance_to_daily_loss_usd": distance_to_daily,
        "max_drawdown_pct": max_dd_pct,
        "static_dd_floor_usd": dd_floor,
        "distance_to_dd_floor_usd": distance_to_dd,
        "status_present": bool(status),
    }


__all__ = [
    "match_fill_to_ticket",
    "find_unacted_tickets",
    "compute_rule_distance",
]
