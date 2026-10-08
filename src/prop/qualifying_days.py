"""Qualifying-day counter for prop rulesets that gate on one (Velotrade).

Velotrade (``config/prop_rulesets/velotrade_classic_1step.yaml``) counts a
trading day toward ``min_trading_days`` only when it closes with a net profit of
``phases.evaluation.qualifying_day_profit_pct`` (0.008 = 0.8%) of the INITIAL
account balance (ruleset line ``qualifying_day_profit_pct: 0.008`` and header
"A trading day only counts if it closes with a net profit of 0.8% or more of the
initial account balance"). Day boundary: the firm's 00:30 UTC reset
(``limits.daily_loss_reset_utc``), so a day is [D 00:30Z, D+1 00:30Z).

Read-only. Computed from ``prop_fills`` (closed rows, grouped by ``closed_at``)
— never from a config constant. The threshold and required-day count are READ
from the ruleset; ``initial balance`` is ``account_size_usd``.

⚠️ Missing is not zero. A day containing a closed fill with no ``pnl`` is
``unmeasured`` (neither counted nor disqualified); a closed fill with no
``closed_at`` cannot be placed on a day and is reported in
``unplaceable_fills`` — while that is non-zero the count is a LOWER BOUND. An
unreadable journal gives ``state: unreadable`` with null counts, never 0.
Account P&L is net-of-commission only if the reporter's ``pnl`` is; the firm
charges 0.03%/side — the cross-check is ``status_realized_today_usd``.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STATES = ("measured", "not_declared", "unreadable")
DAY_RESET = timedelta(hours=0, minutes=30)  # limits.daily_loss_reset_utc "00:30"


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def day_of(ts: datetime) -> date:
    """Firm trading day a UTC instant belongs to (00:30Z reset)."""
    return (ts.astimezone(timezone.utc) - DAY_RESET).date()


def _num(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def count_qualifying_days(
    fills: List[Dict[str, Any]], *, initial_balance: Optional[float],
    threshold_pct: Optional[float], required_days: Optional[int],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Pure counter over a list of ``prop_fills`` dicts."""
    now = now or datetime.now(timezone.utc)
    if not initial_balance or not threshold_pct:
        return {"state": "not_declared", "qualifying_days": None}
    threshold_usd = round(initial_balance * threshold_pct, 8)
    per_day: Dict[date, Dict[str, Any]] = {}
    unplaceable = 0
    for f in fills:
        if str(f.get("status") or "closed").lower() != "closed":
            continue
        closed = _parse(f.get("closed_at"))
        if closed is None:
            unplaceable += 1
            continue
        b = per_day.setdefault(day_of(closed), {"pnl": 0.0, "fills": 0, "unmeasured": False})
        b["fills"] += 1
        p = _num(f.get("pnl"))
        if p is None:
            b["unmeasured"] = True
        else:
            b["pnl"] += p
    today = day_of(now)
    days, qual, unmeasured_days = [], 0, 0
    for d in sorted(per_day):
        b = per_day[d]
        in_progress = d == today
        if b["unmeasured"]:
            status, pnl, pct = "unmeasured", None, None
            unmeasured_days += 1
        else:
            pnl = round(b["pnl"], 8)
            pct = round(pnl / initial_balance, 8)
            # >= : "0.8% or more". Round to 8dp so a float-sum 39.99999999 at $40 isn't missed.
            status = "qualifying" if pnl >= threshold_usd - 1e-9 else "below"
            if status == "qualifying" and not in_progress:
                qual += 1
        days.append({"day": d.isoformat(), "pnl_usd": pnl, "pct_of_initial": pct,
                     "fills": b["fills"], "status": status, "in_progress": in_progress})
    # Today is still open: a qualifying-so-far day can give it back, so it is
    # reported separately and not added to the settled count.
    today_row = next((r for r in days if r["in_progress"]), None)
    req = int(required_days) if required_days else None
    return {
        "state": "measured",
        "basis": "prop_fills closed pnl grouped by closed_at, 00:30Z day",
        "initial_balance_usd": initial_balance,
        "threshold_pct": threshold_pct,
        "threshold_usd": threshold_usd,
        "phase": "evaluation",  # velotrade_1 is an evaluation; funded needs 5 more
        "required_days": req,
        "qualifying_days": qual,
        "days_remaining": max(0, req - qual) if req is not None else None,
        "min_days_met": (qual >= req) if req is not None else None,
        "today_in_progress": today_row,
        "unmeasured_days": unmeasured_days,
        "unplaceable_fills": unplaceable,
        "count_is_lower_bound": bool(unplaceable or unmeasured_days),
        "recent_days": days[-14:],
    }


def effective_dates_block(raw: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Ruleset ``rule_effective_dates`` with a per-rule ``binding`` flag (None = undated)."""
    now = now or datetime.now(timezone.utc)
    out: Dict[str, Any] = {}
    for rule, ds in (raw or {}).items():
        try:
            eff = datetime.fromisoformat(str(ds)).replace(tzinfo=timezone.utc)
        except ValueError:
            out[rule] = {"effective": str(ds), "binding": None}
            continue
        out[rule] = {"effective": eff.date().isoformat(), "binding": now >= eff}
    return out


def compute(account_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Qualifying-day standing for one account (never raises)."""
    try:
        import yaml

        from src.prop import prop_journal
        from src.prop.account_rulesets import all_account_units

        unit = all_account_units().get(account_id)
        rs = getattr(unit, "ruleset", None)
        ev = getattr(rs, "evaluation", None)
        raw = {}
        src = getattr(unit, "source", None)
        if src:
            raw = yaml.safe_load(open(src).read()) or {}
        pct = ((raw.get("phases") or {}).get("evaluation") or {}).get("qualifying_day_profit_pct")
        if pct is None:
            return {"state": "not_declared", "qualifying_days": None}
        fills = prop_journal.list_fills(account_id=account_id, limit=5000)
        out = count_qualifying_days(
            fills, initial_balance=getattr(rs, "account_size_usd", None),
            threshold_pct=float(pct), required_days=getattr(ev, "min_trading_days", None), now=now)
        out["rule_effective_dates"] = effective_dates_block(raw.get("rule_effective_dates"), now)
        try:
            snap = prop_journal.latest_account_status(account_id) or {}
            out["status_realized_today_usd"] = snap.get("realized_today")
        except Exception:  # noqa: BLE001  # allow-silent: cross-check only
            out["status_realized_today_usd"] = None
        return out
    except Exception:  # noqa: BLE001  # allow-silent: degrade to unreadable, never 0
        logger.warning("qualifying_days: read failed for %s", account_id, exc_info=True)
        return {"state": "unreadable", "qualifying_days": None}


def standing_line(qd: Optional[Dict[str, Any]]) -> Optional[str]:
    """One-line standing for status lines; None when the account has no such gate."""
    if not qd or qd.get("state") == "not_declared":
        return None
    n, req = qd.get("qualifying_days"), qd.get("required_days")
    if qd.get("state") != "measured" or n is None:
        return "qualifying days — (unreadable)"
    lb = "+" if qd.get("count_is_lower_bound") else ""
    return (f"qualifying days {n}{lb}/{req if req is not None else '—'} "
            f"(≥${qd['threshold_usd']:,.2f}/day = {qd['threshold_pct']*100:.1f}% of initial)")
