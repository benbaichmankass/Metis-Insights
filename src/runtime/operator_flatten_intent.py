"""ONE owner for the fact that an operator flattened a position.

THE PROBLEM THIS CLOSES. The flatten scripts
(``scripts/ops/flatten_{bybit,ib,alpaca}_position.py``) flatten at the BROKER
and deliberately leave the journal row alone — their docstrings say *"The
journal row is left for the trader's reconciler to close-on-disappear."* The
reconciler then stamps ``exit_reason='reconciler_filled'``, a correct
description of HOW the row closed and a misleading one about WHY. The result is
byte-indistinguishable from an ordinary close the reconciler happened to book,
so ``/api/bot/performance``'s ``perExitPath`` buckets it with genuine strategy
exits and the exit-refinement corpus reads its entry->exit geometry as evidence
about the strategy's exit quality — when a human chose the exit time for a
reason unrelated to the market.

``scripts/ops/mark_operator_flattened.py`` fixes a row AFTER the fact, but the
caller has to remember to run it. A remedy that depends on memory is the shape
this repo files as a bug everywhere else. This module is the durable half: the
flatten stamps its own INTENT at the moment it acts, while the row is still
open, and the marking becomes DERIVABLE from a marker the system wrote itself.

WHY STAMPING THE OPEN ROW IS SAFE, and why it needs no live-path change.
``order_monitor._close_trade_from_order_status`` starts from
``notes = _decode_notes(row.get("notes"))`` — it MERGES into the existing notes
rather than replacing them. Verified empirically too: live trade 4934 carries
entry-time keys (``trade_id``, ``confidence``, ``signal_logic``) alongside
close-time keys (``closed_by``, ``exit_price_source``). So a marker written
here survives the reconciler's close, and nothing in the close path has to
learn about it.

WHAT THIS DOES NOT DO. It does not set ``exit_reason`` and it touches no
monetary field. At stamp time the row is still OPEN and has no exit; the
conversion to ``exit_reason='operator_flatten_reconciled'`` happens afterwards
via ``mark_operator_flattened.py --from-intent``, which reads this marker.
Keeping the two steps separate is deliberate — a flatten that is placed but
never fills must not leave a row labelled as an operator close.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Key stamped into ``trades.notes`` on an OPEN row at flatten time.
INTENT_KEY = "operator_flatten_intent"


def _decode(raw: Any) -> Dict[str, Any]:
    try:
        n = json.loads(raw) if raw else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"_original_notes": raw}
    return n if isinstance(n, dict) else {"_original_notes": raw}


def stamp_intent(
    db_path: str,
    account_id: str,
    symbol: str,
    *,
    reason: str,
    actor: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Stamp the operator-flatten intent onto every OPEN row for the pair.

    Best-effort by contract: the caller is a flatten script whose single
    responsibility is the BROKER-side close, and a journal write must never
    turn a successful flatten into a reported failure. Every failure is
    returned as a state, never raised.

    Three states, never collapsed — a reader must be able to tell
    *we did not look* from *we looked and there was nothing*:

    ``stamped``       one or more open rows carried the marker away
    ``no_open_rows``  we READ the journal and it held no open row for the pair
                      (a flatten of a position the journal never knew about —
                      real, and not an error)
    ``unreadable``    we could NOT look (missing DB, locked, bad schema). This
                      is emphatically not ``no_open_rows``.
    """
    now = now or datetime.now(timezone.utc)
    out: Dict[str, Any] = {
        "state": "unreadable", "stamped_ids": [], "account_id": account_id,
        "symbol": symbol, "error": None,
    }
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, notes FROM trades WHERE status = 'open' "
                "AND account_id = ? AND UPPER(symbol) = UPPER(?) "
                "AND COALESCE(is_backtest, 0) = 0",
                (account_id, symbol),
            ).fetchall()
            if not rows:
                out["state"] = "no_open_rows"
                return out
            marker = {
                "at": now.isoformat(),
                "reason": reason,
                "by": actor,
                "account_id": account_id,
                "symbol": symbol,
            }
            ids: List[int] = []
            with conn:
                for r in rows:
                    notes = _decode(r["notes"])
                    if notes.get(INTENT_KEY):
                        continue  # idempotent — a re-run must not restamp
                    notes[INTENT_KEY] = marker
                    conn.execute("UPDATE trades SET notes = ? WHERE id = ?",
                                 (json.dumps(notes), int(r["id"])))
                    ids.append(int(r["id"]))
            out["state"] = "stamped"
            out["stamped_ids"] = ids
            return out
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("stamp_intent(%s/%s) failed: %s", account_id, symbol, exc)
        return out


def find_unmarked_intent_rows(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """CLOSED rows carrying the intent marker but not yet marked as an
    operator close. This is what makes the marking DERIVABLE rather than
    remembered."""
    conn.row_factory = sqlite3.Row
    found: List[Dict[str, Any]] = []
    for r in conn.execute(
        "SELECT id, account_id, symbol, exit_reason, notes FROM trades "
        "WHERE status = 'closed' AND COALESCE(is_backtest, 0) = 0 "
        "AND notes LIKE ?", (f"%{INTENT_KEY}%",),
    ).fetchall():
        notes = _decode(r["notes"])
        intent = notes.get(INTENT_KEY)
        if not isinstance(intent, dict):
            continue
        if notes.get("closed_by_operator"):
            continue  # already marked
        found.append({
            "id": int(r["id"]), "account_id": r["account_id"],
            "symbol": r["symbol"], "exit_reason": r["exit_reason"],
            "intent": intent,
        })
    return found
