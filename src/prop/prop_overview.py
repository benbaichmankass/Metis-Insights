"""Aggregate prop-book view for the SPA Overview "Prop" executive summary.

WHY (DASH-PROP-OVERVIEW, 2026-10-10). The Overview's Prop book rendered every
card as "—" and "No open prop trades" while the journal held an OPEN
velotrade_1 trade and fresh equity for two accounts. The SPA's ExecSummary
hard-coded the prop branch to null/[] ("lives on the Prop tab") and the only
prop endpoints were per-account (`/status`) or raw (`/fills`), so the
Overview had nothing to ask. This is the one read that answers it, computed
from the canonical prop journal (``prop_fills`` / ``prop_account_status``) —
no new store.

Contract (never collapse "unread" into 0):
  * ``equity.total_usd`` sums ONLY accounts whose latest snapshot is fresh
    (``status_freshness == "ok"``); ``null`` when none is. Stale / absent
    accounts are listed with their state and EXCLUDED from the sum.
  * ``open_trades`` are open/filled fill rows with no closing row for the same
    ticket/external id. ``placed`` (a resting limit) is not an open trade.
  * ``realized`` covers closed fills whose ``closed_at`` (else ``reported_at``)
    is inside the window; ``pnl`` null rows are counted in ``closed_unpriced``
    and excluded from P&L / win rate. ``win_rate`` / ``profit_factor`` are null
    when there is nothing to compute them over.
  * Unrealized P&L is null: the manual bridge has no mark feed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7),
           "30d": timedelta(days=30), "all": None}
_OPEN = {"open", "filled"}
_CLOSED = {"closed"}
_FILL_LIMIT = 500


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def prop_account_ids(accounts: Dict[str, Any]) -> List[str]:
    """Live prop accounts. A ``retired: true`` account (breakout_1, operator
    2026-10-09: it "shouldn't be coming up again") is excluded -- its equity and
    its never-closed historical fill rows are not this book's."""
    from src.prop.prop_identity import is_prop_account, is_retired_account
    return [aid for aid, a in (accounts or {}).items()
            if isinstance(a, dict) and is_prop_account(a) and not is_retired_account(a)]


def _key(f: Dict[str, Any]) -> Optional[tuple]:
    if f.get("external_order_id"):
        return (f["account_id"], "ext", str(f["external_order_id"]))
    if f.get("ticket_id"):
        return (f["account_id"], "tkt", str(f["ticket_id"]))
    return None


def split_fills(fills: List[Dict[str, Any]]):
    """-> (open_rows, closed_rows). A trade with a closing row is not open."""
    closed = [f for f in fills if str(f.get("status", "")).lower() in _CLOSED]
    closed_keys = set()
    for f in closed:
        k = _key(f)
        if k:
            closed_keys.add(k)
        if f.get("ticket_id"):
            closed_keys.add((f["account_id"], "tkt", str(f["ticket_id"])))
    open_rows = []
    for f in fills:
        if str(f.get("status", "")).lower() not in _OPEN:
            continue
        k = _key(f)
        tk = (f["account_id"], "tkt", str(f["ticket_id"])) if f.get("ticket_id") else None
        if (k and k in closed_keys) or (tk and tk in closed_keys):
            continue
        open_rows.append(f)
    return open_rows, closed


def build_overview(window: str = "7d", *, accounts: Optional[Dict[str, Any]] = None,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    from src.prop import prop_journal, prop_reconcile

    if window not in WINDOWS:
        window = "7d"
    now = now or datetime.now(timezone.utc)
    if accounts is None:
        from src.config.accounts_loader import load_accounts_dict
        accounts = load_accounts_dict()
    ids = prop_account_ids(accounts)

    acct_rows, total, fresh_n = [], 0.0, 0
    all_fills: List[Dict[str, Any]] = []
    for aid in ids:
        snap = prop_journal.latest_account_status(aid)
        rd = prop_reconcile.compute_rule_distance(aid, snap)
        fresh = rd.get("status_freshness")
        eq = (snap or {}).get("equity")
        counted = fresh == "ok" and isinstance(eq, (int, float))
        if counted:
            total += float(eq)
            fresh_n += 1
        acct_rows.append({
            "account_id": aid, "mode": (accounts[aid] or {}).get("mode"),
            "equity": eq if isinstance(eq, (int, float)) else None,
            "status_freshness": fresh, "status_age_hours": rd.get("status_age_hours"),
            "counted_in_total": counted,
        })
        all_fills.extend(prop_journal.list_fills(account_id=aid, limit=_FILL_LIMIT))

    open_rows, closed = split_fills(all_fills)
    cutoff = None if WINDOWS[window] is None else now - WINDOWS[window]
    in_win = []
    for f in closed:
        ts = _parse(f.get("closed_at")) or _parse(f.get("reported_at"))
        if cutoff is None or (ts is not None and ts >= cutoff):
            in_win.append(f)
    priced = [f for f in in_win if isinstance(f.get("pnl"), (int, float))]
    wins = [f["pnl"] for f in priced if f["pnl"] > 0]
    losses = [f["pnl"] for f in priced if f["pnl"] < 0]
    gl = -sum(losses)
    realized = {
        "window": window,
        "total_pnl": round(sum(f["pnl"] for f in priced), 2) if priced else None,
        "closed_trades": len(in_win), "closed_priced": len(priced),
        "closed_unpriced": len(in_win) - len(priced),
        "win_rate": (len(wins) / len(priced)) if priced else None,
        "profit_factor": (sum(wins) / gl) if gl > 0 else None,
    }
    open_trades = [{
        "id": f.get("id"), "account_id": f.get("account_id"), "symbol": f.get("symbol"),
        "side": f.get("direction"), "qty": f.get("qty"), "entry_price": f.get("entry_price"),
        "sl": f.get("sl"), "tp": f.get("tp"), "ticket_id": f.get("ticket_id"),
        "opened_at": f.get("opened_at") or f.get("reported_at"),
        "status": f.get("status"),
    } for f in open_rows]
    return {
        "generated_at": now.isoformat(),
        "equity": {
            "total_usd": round(total, 2) if fresh_n else None,
            "accounts_counted": fresh_n, "accounts_total": len(ids),
            "accounts": acct_rows,
        },
        "open_trades": open_trades,
        "realized": realized,
        "unrealized_pnl": None,
        "population": (f"{len(ids)} prop accounts; equity summed over {fresh_n} with a fresh "
                       f"operator-reported snapshot; realized over {len(in_win)} closed fills "
                       f"in {window} (last {_FILL_LIMIT} fills per account)"),
    }
