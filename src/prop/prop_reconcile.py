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

from src.prop import prop_balance, prop_journal

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
_OPEN_TICKET_STATUSES = ("emitted", "placed", "filled", "expiry_prompted",
                         "invalidated_prompted", "awaiting_report")
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
      (``emitted``/``placed``/``filled``/``expiry_prompted``/``invalidated_prompted``/
      ``awaiting_report``). ``placed``/``expiry_prompted``/``invalidated_prompted``/
      ``awaiting_report`` are all "awaiting a fill report" — a working order or an
      operator-confirmed/prompted ticket whose later fill/close must link back.
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
    #
    # ⚠️ THE FALLBACK MATCH IS TIME-BOUNDED BY CAUSALITY, and must stay so.
    # BL-20260823-PROP-UNACTED-MASKED-BY-UNBOUNDED-FILL-MATCH.
    #
    # It used to be a flat set of (account, symbol, direction) keys with NO time
    # bound, so ONE historical fill masked EVERY future un-acted ticket on that
    # symbol+direction, permanently. Measured 2026-08-23 on the live prop
    # journal: **17 of 17** `emitted` tickets — 32 h to 62 DAYS past their
    # `valid_until`, none with a fill — were hidden by it, so
    # `/api/bot/prop/reconcile` reported `unacted_count: 0` while 17 sat stuck.
    # The drift alert built to catch exactly this reported clean over a
    # population it had excluded.
    #
    # The consequence was not only cosmetic: `breakout_executor` suppresses a
    # fresh ticket while an `outstanding_ticket:emitted` exists (7 of the 25
    # suppressions), and `prop_expiry_prompt` builds on THIS function — so an
    # invisible ticket is also an un-promptable one that silently blocks its
    # own symbol+direction forever.
    #
    # The bound is CAUSALITY, not a tuned window: a fill recorded BEFORE the
    # ticket existed cannot be that ticket's fill. That needs no evidence to
    # justify. A tighter window (fill within the ticket's validity + a
    # reporting-lag grace) would be more discriminating, but picking the grace
    # needs lag evidence this repo does not have yet — filed as the follow-up
    # rather than invented here.
    #
    # The explicit `ticket_id` link above stays UNBOUNDED: an explicit link is
    # explicit, whenever it was recorded.
    acted_times: Dict[tuple, List[datetime]] = {}
    for f in fills:
        k = (str(f.get("account_id") or "").strip(),
             str(f.get("symbol") or "").upper(),
             _norm_direction(f.get("direction")))
        ft = (_parse_iso(f.get("opened_at"))
              or _parse_iso(f.get("reported_at"))
              or _parse_iso(f.get("created_at")))
        acted_times.setdefault(k, []).append(ft)

    def _acted_by_fallback(key: tuple, emitted_at: Optional[datetime]) -> bool:
        times = acted_times.get(key)
        if not times:
            return False
        for ft in times:
            # FAIL-SAFE: an undateable fill keeps the OLD masking behaviour, so
            # a parse failure can never manufacture a new drift alert.
            if ft is None or emitted_at is None or ft >= emitted_at:
                return True
        return False

    out: List[Dict[str, Any]] = []
    for t in tickets:
        if t.get("status") != "emitted":
            continue
        if t.get("ticket_id") in acted_ids:
            continue
        key = (str(t.get("account_id") or "").strip(),
               str(t.get("symbol") or "").upper(),
               _norm_direction(t.get("direction")))
        emitted_at = (_parse_iso(t.get("signal_time"))
                      or _parse_iso(t.get("created_at")))
        if _acted_by_fallback(key, emitted_at):
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


def _status_freshness(status: Dict[str, Any]) -> tuple:
    """``(age_hours, freshness)`` for the snapshot the distances are computed on.

    Four states, never collapsed, sharing ``prop_balance``'s vocabulary so the
    prop subsystem has ONE answer to "is this snapshot current":

      ``absent``    — no snapshot has ever been reported
      ``stale``     — older than the threshold, **or undateable** (a snapshot
                      that cannot be dated cannot be shown to be current, and
                      the fail-safe reading of a safety cushion is stale)
      ``ok``        — inside the threshold
      ``unchecked`` — ``PROP_STATUS_REQUEST_MAX_AGE_HOURS <= 0`` disables the
                      staleness check; *we did not look*, which is not ``ok``

    ``age_hours`` is ``None`` for an absent or undateable snapshot — read
    ``freshness`` to tell those apart, never the null.
    """
    if not status:
        return None, "absent"
    age = prop_balance.status_age_hours(status)
    limit = prop_balance.max_age_hours()
    if limit <= 0:
        return age, "unchecked"
    if age is None or age >= limit:
        return age, "stale"
    return age, "ok"


#: Reconstruction states for the balance the rule-distance is computed from.
#: NEVER collapsed — `unavailable` is *we could not look for later fills*,
#: which is a different fact from `snapshot` (*we looked; there are none*).
#: Registered with `collapsed-state-guard` as `prop_rule_distance.balance_basis`.
BALANCE_BASIS_STATES = ("snapshot", "snapshot_plus_fills", "unavailable")


def reconstruct_equity(
    account_id: str, status: Dict[str, Any],
) -> Dict[str, Any]:
    """Best available equity for the DD-floor distance: snapshot + later fills.

    **Why this exists** (BL-20260818-PROP-RULE-DISTANCE-IGNORES-THE-FILLS-STREAM).
    The manual bridge has no broker feed, so the account-status snapshot ages —
    but the journal keeps receiving CLOSED prop fills, each carrying a realized
    ``pnl``, and the cushion computed from the snapshot alone ignores every one
    of them. Measured 2026-08-18 on ``breakout_1``: a 694h-old snapshot of
    4825.61 rendered a $125.61 cushion to the $4700 static floor while two
    closed fills reported since it (−18.06, −50.55) reconstruct 4757.00 against
    an operator terminal truth of 4747.00 — the panel was 167% too generous
    about the distance to an account-killer, and a two-row query in the same DB
    would have got within $10.

    Grading the snapshot ``stale`` (2026-08-14) was right and is kept; it is
    just the weaker half. When a better NUMBER is available, the answer is the
    better number, not a louder caveat on the worse one.

    **Provenance:** the reconstruction is ESTIMATED, never measured — it is the
    operator's reported snapshot plus the operator's reported fills, so it
    inherits their gaps (fees and swap are not reported, which is most of the
    $10 residual above). It is an improvement on a stale snapshot, NOT a
    substitute for a fresh one, so the status-age nudge must still fire on age.

    **Deliberately scoped to the DD floor, not the daily-loss limit.** The
    static floor is a CUMULATIVE account-level line, so realized fills
    accumulate straight into it. "Today" is not reconstructible from a stale
    snapshot: without knowing where the day boundary falls relative to a
    three-week-old ``day_start_balance``, summing fills into ``realized_today``
    would manufacture a number rather than recover one.
    """
    equity = status.get("equity")
    if equity is None:
        equity = status.get("balance")
    base = {
        "balance_basis": "snapshot",
        "fills_applied": 0,
        "fills_pnl_usd": 0.0,
        "equity_used_usd": equity,
        "equity_provenance": "measured" if equity is not None else None,
    }
    snap_at = _parse_iso(status.get("reported_at"))
    if equity is None or snap_at is None:
        # Nothing to reconstruct FROM. Not an error, and not `unavailable`
        # either — we are not failing to look, there is no anchor to look from.
        return base
    try:
        fills = prop_journal.list_fills(account_id=account_id, limit=500)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_reconcile: fills read failed for %s: %s",
                       account_id, exc)
        return {**base, "balance_basis": "unavailable",
                "fills_applied": None, "fills_pnl_usd": None,
                "equity_provenance": None}

    # ⚠️ REPORT TIME IS NOT EVENT TIME, AND ON A MANUAL BRIDGE THEY ROUTINELY
    # DISAGREE. This used to select on `reported_at` alone, so a close the
    # operator had ALREADY read into their snapshot was applied a SECOND time
    # merely because they typed it in afterwards
    # (BL-20260828-RULE-DISTANCE-DOUBLE-COUNTS-A-LATE-REPORTED-EARLIER-FILL).
    #
    # MEASURED TWICE, IN BOTH DIRECTIONS:
    #   2026-08-28  a -77.22 close re-applied -> distance_to_dd_floor -19.29,
    #               i.e. the panel declared the account DEAD when it was not.
    #   2026-08-31  a +35.28 close re-applied -> equity_used 4822.62 against a
    #               snapshot of 4787.34, so the cushion to the $4,700 floor read
    #               122.62 when it was 87.34. The snapshot delta was +33.34 and
    #               the close is +35.28 gross of a -2.08 commission = +33.20,
    #               matching to $0.14 -- so the snapshot plainly already held it.
    #
    # The optimistic direction is the dangerous one: it does not just mislead a
    # panel, it is the number `prop_risk_gate` caps against, so an inflated
    # cushion makes the gate AUTHORISE a ticket that breaches.
    #
    # THE RULE IS ASYMMETRIC, DELIBERATELY. An event time is the honest
    # discriminator, but it is mostly absent: measured on the live table, only
    # 4 of the 19 pnl-carrying fills have `closed_at`. So selecting on event
    # time alone would silently drop 79% of the stream, which is worse than the
    # bug. Instead:
    #   * event time KNOWN  -> place it exactly, in BOTH directions.
    #   * event time UNKNOWN -> apply only a LOSS. A fill we cannot place must
    #     never GROW a safety cushion; it may shrink one. We accept false
    #     pessimism (a refused trade) and refuse false optimism (a breached
    #     floor), because only one of those is terminal.
    # The withheld gains are COUNTED, not dropped silently -- a reader must be
    # able to see that the reconstruction is deliberately conservative.
    applied, total, withheld_gain = 0, 0.0, 0
    for f in fills:
        pnl = f.get("pnl")
        if pnl is None:
            continue  # an open/filled report carries no realized pnl
        try:
            pnl_f = float(pnl)
        except (TypeError, ValueError):
            continue
        event_at = _parse_iso(f.get("closed_at"))
        if event_at is not None:
            if event_at <= snap_at:
                continue  # the snapshot already reflects it
        else:
            # Unplaceable. A gain may not inflate the cushion.
            if pnl_f >= 0:
                report_at = _parse_iso(f.get("reported_at"))
                if report_at is not None and report_at > snap_at:
                    withheld_gain += 1
                continue
            report_at = _parse_iso(f.get("reported_at"))
            if report_at is None or report_at <= snap_at:
                continue
        total += pnl_f
        applied += 1

    if applied == 0:
        return {**base, "fills_withheld_unplaceable_gain": withheld_gain}
    return {
        "balance_basis": "snapshot_plus_fills",
        "fills_applied": applied,
        "fills_pnl_usd": round(total, 8),
        "equity_used_usd": round(float(equity) + total, 8),
        "equity_provenance": "estimated",
        # How many later-reported GAINS were deliberately not applied because
        # they could not be placed in time. Non-zero means the cushion below is
        # conservative BY CONSTRUCTION, not that the stream was empty.
        "fills_withheld_unplaceable_gain": withheld_gain,
    }


def compute_rule_distance(
    account_id: str, status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Distance from the latest account status to the two account-killer limits.

    Returns a dict with the resolved limits and the computed distances; any
    value that can't be derived from the available status fields is ``None``
    (never a fabricated 0). ``status`` defaults to the latest snapshot.

    ⚠️ **Read ``status_freshness`` before treating any distance as a live
    cushion** (added 2026-08-14, Tier-2). Every number here is a function of
    ONE operator-reported snapshot, and the manual bridge has no broker feed to
    refresh it — so a three-week-old row produces a full-looking cushion that
    describes an account state long gone. ``status_present: true`` said only
    that a row exists; it never said the row was current, and it was the single
    field a consumer had. The distances are still returned when stale (throwing
    away the last known cushion helps nobody) — the caveat travels *with* them,
    inside this dict, so a consumer reading only ``rule_distance`` cannot miss
    it the way it could a sibling field on the envelope.
    """
    status = status or prop_journal.latest_account_status(account_id) or {}
    rs = _ruleset_for(account_id)
    age_hours, freshness = _status_freshness(status)

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
    #
    # ⚠️ A MISSING TERM MUST NOT BECOME ZERO.
    # BL-20260823-PROP-DAILY-CUSHION-FABRICATED-FROM-A-MISSING-REALIZED-TODAY
    # (kept on ONE line — a line-wrapped id resolves to nothing when grepped,
    # which is what artifact-validity-guard caught here). The old guard passed whenever
    # EITHER term was present, and then `(realized_today or 0.0)` turned "we
    # did not look" into "nothing was realized today". That is the
    # anti-conservative direction on an account-killer limit: it reports MORE
    # cushion than exists.
    #
    # MEASURED 2026-08-23T17:5xZ on breakout_1, snapshot id 13: realized_today
    # is None (never reported by anyone) while unrealized is 0.0 (correctly
    # reported — the book was flat). One non-None term let the guard through,
    # so day_pnl came out 0.0 and the panel published a FULL $142.92 daily
    # cushion — on a day whose own prop_fills rows hold two closed losses
    # totalling -$218.79, i.e. 1.53x the limit, with the account $64 above its
    # static DD floor.
    #
    # The sum of an unknown and a known is UNKNOWN. Both terms are required.
    # `None` is an expected value downstream: nothing refuses a trade on this,
    # the API serves it as-is, and telegram_report_handler renders it through
    # `_cushion()` whose docstring already calls None "legitimate".
    day_pnl = None
    if realized_today is None and unrealized is None:
        day_pnl_state = "unreported"          # neither term ever reported
    elif realized_today is None:
        day_pnl_state = "realized_unreported"  # THE measured case above
    elif unrealized is None:
        day_pnl_state = "unrealized_unreported"
    else:
        day_pnl = realized_today + unrealized
        day_pnl_state = "measured"
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
    # Prefer snapshot + fills-reported-since over the snapshot alone. The basis
    # travels in the returned dict so the improvement is never silent — a
    # consumer must be able to tell an ESTIMATED cushion from a reported one.
    recon = reconstruct_equity(account_id, status)
    equity_used = recon.get("equity_used_usd")
    if equity_used is None:
        equity_used = equity_now
    distance_to_dd = (
        equity_used - dd_floor
        if (equity_used is not None and dd_floor is not None) else None
    )

    return {
        "account_id": account_id,
        "as_of": status.get("reported_at"),
        "account_size_usd": account_size,
        "balance": balance,
        "equity": equity_now,
        "day_pnl": day_pnl,
        # WHY day_pnl is None, never collapsed into the null itself: a consumer
        # must be able to tell "the operator reported no loss today" from "the
        # loss today was never reported". Only the first is a cushion.
        "day_pnl_state": day_pnl_state,
        "daily_loss_pct": daily_loss_pct,
        "daily_loss_limit_usd": daily_loss_limit_usd,
        "daily_loss_used_usd": daily_loss_used,
        "distance_to_daily_loss_usd": distance_to_daily,
        "max_drawdown_pct": max_dd_pct,
        "static_dd_floor_usd": dd_floor,
        "distance_to_dd_floor_usd": distance_to_dd,
        # WHICH equity the DD distance was computed from, and how good it is.
        # Read `balance_basis` before quoting the cushion: `snapshot_plus_fills`
        # is ESTIMATED, and `unavailable` means later fills could not be read at
        # all — the distance is then snapshot-only and may be optimistic.
        "balance_basis": recon.get("balance_basis"),
        "equity_used_usd": recon.get("equity_used_usd"),
        "equity_provenance": recon.get("equity_provenance"),
        "fills_applied_since_snapshot": recon.get("fills_applied"),
        "fills_pnl_since_snapshot_usd": recon.get("fills_pnl_usd"),
        "status_present": bool(status),
        "status_age_hours": age_hours,
        "status_freshness": freshness,
        "status_max_age_hours": prop_balance.max_age_hours(),
    }


__all__ = [
    "match_fill_to_ticket",
    "find_unacted_tickets",
    "compute_rule_distance",
    "reconstruct_equity",
    "BALANCE_BASIS_STATES",
]
