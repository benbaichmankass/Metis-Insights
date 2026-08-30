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

⚠️ THAT ARGUMENT WAS TRUE AND STILL INSUFFICIENT — the marker did NOT survive
its first live exercise, and the shape below is what fixes it. The merge is
real; what killed the marker was the ``dump_capped(notes, 500)`` the close then
writes. The marker was one 5-key DICT (~217 chars) and ``_shrink_dict`` only
ever trims *strings*, so it could be neither shortened nor kept: it pushed the
blob from 410 to 627 chars and was then deleted wholesale by the minimal-envelope
fallback it had itself triggered. Measured on live trade 4905
(``bybit_portfolio``/ETHUSDT, closed 2026-08-30T14:01:17Z): stamped at 13:58:26,
closed 3 minutes later carrying exactly the protected set + ``_truncated`` and no
marker — while all six of its ``bybit_portfolio`` siblings, closed by the same
path without a marker, retain their entry-time keys untruncated. Reproduced from
sibling 4887's own notes: +marker → envelope, -marker → 410 chars, nothing lost.

So the marker is now TWO keys, the flag/prose split ``json_notes`` already draws
for ``closed_by_operator`` vs ``operator_close_reason``:

``operator_flatten_intent``         a bare ``True``. PROTECTED in
                                    ``_DEFAULT_PROTECTED``, and small enough that
                                    protecting it cannot itself overflow the
                                    envelope (measured: 344 chars with it, under
                                    the 500 cap).
``operator_flatten_intent_detail``  the ``{at, reason, by, ...}`` dict.
                                    Deliberately UNPROTECTED — it is the first
                                    thing that should be shed, and losing it costs
                                    the prose, never the fact.

``_shrink_dict`` gained the matching rung in the same change: when no trimmable
string remains it now sheds the largest unprotected value ONE AT A TIME before
falling back to the envelope, so this payload lands at 458 chars with every other
key intact rather than collapsing.

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

#: Flag stamped into ``trades.notes`` on an OPEN row at flatten time. A bare
#: ``True`` — this is what a consumer branches on, and it is PROTECTED against
#: the notes cap (``json_notes._DEFAULT_PROTECTED``).
INTENT_KEY = "operator_flatten_intent"
#: The human-readable half (``at`` / ``reason`` / ``by`` / ``account_id`` /
#: ``symbol``). Deliberately NOT protected: on a blob near the cap this is the
#: right thing to shed, and a marking that loses its prose is still a marking.
INTENT_DETAIL_KEY = "operator_flatten_intent_detail"


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
            # Route the WRITE through the canonical helper rather than a raw
            # UPDATE — writer-conformance-guard flagged the first draft of this
            # file, correctly: one-off writers are how the canonical-JSON /
            # closed_at / direction invariants get skipped. The helper's
            # mobile-push observer keys on the UPDATE dict's own ``status`` /
            # ``sl`` / ``tp`` keys, and this passes none of them, so a
            # notes-only stamp fires no notification (asserted in the tests).
            from src.units.db.database import Database
            db = Database(db_path)
            ids: List[int] = []
            for r in rows:
                notes = _decode(r["notes"])
                if notes.get(INTENT_KEY):
                    continue  # idempotent — a re-run must not restamp
                notes[INTENT_KEY] = True
                notes[INTENT_DETAIL_KEY] = marker
                db.update_trade(int(r["id"]), {"notes": json.dumps(notes)})
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
        flag = notes.get(INTENT_KEY)
        if not flag:
            continue
        if notes.get("closed_by_operator"):
            continue  # already marked
        # Three states, never collapsed — the detail is droppable by design, so
        # a reader must be able to tell "the flatten recorded no prose" from
        # "the notes cap shed it" from "this row predates the flag/detail split".
        detail = notes.get(INTENT_DETAIL_KEY)
        if isinstance(detail, dict):
            detail_state = "full"
        elif isinstance(flag, dict):
            # Legacy inline shape: the whole marker WAS the flag's value.
            detail, detail_state = flag, "legacy_inline"
        else:
            detail, detail_state = {}, "shed"
        found.append({
            "id": int(r["id"]), "account_id": r["account_id"],
            "symbol": r["symbol"], "exit_reason": r["exit_reason"],
            "intent": detail, "detail_state": detail_state,
        })
    return found
