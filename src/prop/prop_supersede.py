"""Supersede release: a suppressed prop ticket replaces the resting entry it was blocked by.

OPERATOR DECISION 2026-10-08 ~20:03Z, verbatim "Approve as proposed
(Recommended)" (pipeline item PI-20261008-3QRUJSYR-0001, checklist row
PROP-SUPERSEDE). The rule, end to end:

1. Emission (``breakout_executor.emit_prop_ticket``) suppresses a new signal
   whose (account, symbol, direction) has a ``placed`` ticket. When that
   ticket is the SAME strategy's, the suppressed row carries
   ``meta.supersede = {"blocked_by": <old id>, "message", "valid_until"}``.
2. The executor (``prop_executor._supersede_resting``), holding this cycle's
   terminal read, requires: no position on the venue symbol, exactly the old
   ticket's one resting order on it, the new ticket's entry band ``ok`` on
   the live quote. Then it cancels the resting entry, re-reads, and only when
   the order is gone (and no position appeared) reports the old ticket
   ``skipped: superseded by <new id>``.
3. Only then it posts ``{"kind": "supersede", "ticket_id": <new>,
   "supersedes": <old>}`` to ``POST /api/bot/prop/report``, which lands here.

This module is step 3's server half. It REUSES the ``prop-ticket-reissue``
decision (``scripts/ops/prop_ticket_reissue.plan``) and its guarded write
(``apply``): account ``live``, strategy on the roster with ``execution:
live``, the ticket ``suppressed`` and under the reissue age bound, its
blocker TERMINAL, no fill on the new ticket, the reticket guard re-run NOW
clear, and the ticket REBUILT with emission's own functions
(``rebuild_fields``: fresh ``valid_until``, emission's sizing, risk gate and
leverage cap; no new formula). On top of that it requires the supersede
link itself: the new ticket's candidate names the old one, the old one is
``skipped`` with a ``superseded by <new id>`` fill row, and both share
strategy, symbol and direction. Anything else is refused and the new ticket
stays ``suppressed``; nothing here ever touches the old ticket or the venue.
The new ticket then goes through the executor's normal intake unchanged.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

KIND = "supersede"


def superseded_reason(new_id: str) -> str:
    """The old ticket's skip reason. One spelling, shared by both halves."""
    return f"superseded by {new_id}"


def _candidate(meta: Any) -> Optional[Dict[str, Any]]:
    if isinstance(meta, str):
        import json
        try:
            meta = json.loads(meta)
        except (TypeError, ValueError):
            return None
    cand = (meta or {}).get("supersede") if isinstance(meta, dict) else None
    return cand if isinstance(cand, dict) else None


def release(account_id: str, new_id: str, old_id: str, *,
            now: Optional[datetime] = None, conn: Any = None,
            account_cfg: Any = "load", strategy_info: Any = None,
            guard: Any = None, rebuild: Any = None) -> Dict[str, Any]:
    """Flip ``new_id`` ``suppressed`` -> ``emitted`` (rebuilt) once ``old_id`` was
    superseded. Returns ``{"released": bool, "why": str, ...}``; never raises on
    a refusal. The keyword seams are for tests."""
    from scripts.ops import prop_ticket_reissue as reissue
    from src.prop import prop_journal

    now = now or datetime.now(timezone.utc)
    out: Dict[str, Any] = {"released": False, "ticket_id": new_id, "supersedes": old_id}
    own = conn is None
    if own:
        conn = prop_journal._connect()
    try:
        new = reissue._row(conn, new_id)
        old = reissue._row(conn, old_id)
        if new is None or old is None:
            out["why"] = f"ticket not found ({new_id if new is None else old_id})"
            return out
        if str(new.get("account_id")) != account_id or str(old.get("account_id")) != account_id:
            out["why"] = "tickets do not both belong to this account"
            return out
        cand = _candidate(new.get("meta"))
        if not cand or cand.get("blocked_by") != old_id:
            out["why"] = f"{new_id} carries no supersede candidate naming {old_id}"
            return out
        for k in ("strategy", "symbol", "direction"):
            if str(new.get(k) or "").lower() != str(old.get(k) or "").lower():
                out["why"] = f"{k} differs ({new.get(k)!r} vs {old.get(k)!r})"
                return out
        if str(old.get("status") or "") != "skipped":
            out["why"] = f"{old_id} is {old.get('status')!r}, not skipped (supersede not reported)"
            return out
        n = conn.execute(
            "SELECT COUNT(*) FROM prop_fills WHERE ticket_id = ? AND status = 'skipped' AND reason = ?",
            (old_id, superseded_reason(new_id))).fetchone()[0]
        if not n:
            out["why"] = f"{old_id} has no '{superseded_reason(new_id)}' fill row"
            return out
        n_pos = conn.execute(
            "SELECT COUNT(*) FROM prop_fills WHERE ticket_id = ? AND status IN ('open', 'filled', 'closed')",
            (old_id,)).fetchone()[0]
        if n_pos:
            out["why"] = f"{old_id} has {n_pos} position-bearing fill row(s); refused"
            return out
        cfg = reissue.account_cfg_for(account_id) if account_cfg == "load" else account_cfg
        decision = reissue.plan(
            conn, account_id=account_id, ticket_id=new_id, now=now, account_cfg=cfg,
            max_age_min=reissue.DEFAULT_MAX_AGE_MIN,
            strategy_info=strategy_info or reissue.strategy_gate,
            guard=guard or reissue._live_guard, rebuild=rebuild or reissue.rebuild_fields)
        if not decision.get("ok"):
            out["why"] = f"reissue checks refuse: {decision.get('why')}"
            return out
        decision["fields"]["meta"]["supersede"] = {**cand, "released_at": now.isoformat(),
                                                    "supersedes": old_id}
        after = reissue.apply(conn, decision)
        out.update(released=True, why=superseded_reason(new_id) + " released",
                   valid_until=after.get("valid_until"), status=after.get("status"))
        logger.info("prop_supersede: %s %s released (supersedes %s)", account_id, new_id, old_id)
        return out
    finally:
        if own:
            conn.close()
