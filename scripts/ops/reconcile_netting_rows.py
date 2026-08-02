#!/usr/bin/env python3
"""General same-moment reconcile for the one-way-netting partial-close class
(BL-20260801-NETTING-PARTIAL-CLOSE-ROWS-NEVER-REDUCED). Option (c)+(b),
operator-approved 2026-08-02.

THE BUG. Under Bybit one-way netting several journal `trades` rows share ONE
exchange position. `order_monitor._cascade_close_netted_siblings` closes siblings
only when the netted position reads FLAT — a PARTIAL shrink leaves the siblings
open at full `position_size`, inflating the journal (up to 155x pre-cleanup) and
suppressing netting-guard re-entries. The live `journal_qty_divergent` sweep
DETECTS every instance per tick but remediates nothing.

THIS TOOL. A same-moment reconcile that, per `(account, symbol, direction)` group,
closes the SURPLUS open journal rows so the remaining open sum matches the broker's
netted size — the generalization of the one-shot `reconcile_netting_phantom_rows.py`
(which was signature-pinned to the 2026-08-01 rows). Design constraints, all honoured:

  * PROVENANCE (src/runtime/provenance.py): the real closes happened at UNKNOWN
    moments inside position-level exits, so pnl / exit_price / pnl_percent stay
    NULL (UNMEASURED) — NEVER priced from a mark. status→'closed',
    reconcile_status→'superseded', exit_reason→'netting_partial_reconciled',
    notes.netting_partial_reconcile = {original, exchange evidence, why}.
  * PAIRS ISOLATION: pairs-sleeve rows (strategy LIKE 'pairs\\_%') are the
    pairs_executor's own state and are EXCLUDED — never closed behind its back
    (the BNB surplus is pairs-owned; it goes with the pairs sleeve).
  * FAIL-SAFE: a group whose exchange size could not be read (None in the
    snapshot) is SKIPPED — never close on an unconfirmed broker read. Never close
    MORE than the surplus (a row that straddles the boundary is kept, not closed),
    so a still-live share is never closed.
  * (b) PRECISION: when the snapshot carries the group's resting protective-leg
    ids, a surplus row whose tracked sl/tp leg id is ABSENT (fired) is closed
    BEFORE the oldest-first fallback — the leg that fired is the share that died.

DECOUPLED + TESTABLE. `reconcile_plan()` is pure: it takes the open rows + an
exchange snapshot and returns the close plan. The live exchange read
(account_open_positions) lives in the VM wrapper, which writes the snapshot JSON
and pipes it in — so this script never opens a socket and the logic is unit-tested
without a broker.

Dry-run by default; --apply writes inside one transaction. Idempotent (a
re-run finds the surplus already gone).

Usage (the system-action wrapper supplies --exchange-json from a live read):
    python3 scripts/ops/reconcile_netting_rows.py --exchange-json exch.json            # dry-run
    python3 scripts/ops/reconcile_netting_rows.py --exchange-json exch.json --apply
    python3 scripts/ops/reconcile_netting_rows.py --db /path/to/trade_journal.db ...
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.utils.paths import trade_journal_db_path  # noqa: E402

BACKLOG_ID = "BL-20260801-NETTING-PARTIAL-CLOSE-ROWS-NEVER-REDUCED"
EXIT_REASON = "netting_partial_reconciled"
# Absolute size tolerance: a residual below this is treated as matched (float noise
# / an unavoidable straddle). Deliberately small.
DEFAULT_TOLERANCE = 1e-9


def _is_pairs(strategy: Optional[str]) -> bool:
    """Pairs-sleeve rows carry per-leg strategy names pairs_<name>_a / _b — the
    pairs_executor owns them, so they are excluded from this reconcile."""
    return bool(strategy) and str(strategy).startswith("pairs_")


def _group_key(row: Dict[str, Any]) -> Tuple[str, str, str]:
    return (str(row.get("account_id") or ""), str(row.get("symbol") or ""),
            str(row.get("direction") or ""))


def _row_leg_ids(row: Dict[str, Any]) -> List[str]:
    return [str(row[k]) for k in ("sl_order_id", "tp_order_id")
            if row.get(k) not in (None, "")]


def _age_key(row: Dict[str, Any]):
    # Oldest first: created_at ascending, id ascending as the stable tiebreak.
    return (str(row.get("created_at") or ""), int(row.get("id") or 0))


def reconcile_plan(
    open_rows: List[Dict[str, Any]],
    exchange: Dict[Tuple[str, str, str], Optional[float]],
    resting_legs: Optional[Dict[Tuple[str, str, str], List[str]]] = None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Pure planner. Returns (to_close, skipped_groups).

    ``to_close`` is a list of {id, group, size, why, exchange_size} — the surplus
    rows to supersede. ``skipped_groups`` records groups left untouched and why
    (exchange unreadable / no surplus), for a transparent report.
    """
    resting_legs = resting_legs or {}
    # Bucket non-pairs open rows by group.
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for r in open_rows:
        if _is_pairs(r.get("strategy") or r.get("strategy_name")):
            continue
        groups.setdefault(_group_key(r), []).append(r)

    to_close: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for gk, rows in groups.items():
        exch = exchange.get(gk, None)
        if exch is None:
            skipped.append({"group": gk, "reason": "exchange_unreadable"})
            continue
        total = sum(float(r.get("position_size") or 0.0) for r in rows)
        surplus = total - float(exch)
        if surplus <= tolerance:
            skipped.append({"group": gk, "reason": "no_surplus",
                            "journal_sum": total, "exchange_size": exch})
            continue
        fired = set(resting_legs.get(gk, [])) if gk in resting_legs else None
        # (b) precision: rows whose tracked leg is ABSENT from the resting set
        # (fired) are the shares that died — close them first; then oldest-first.
        def _order(r):
            legs = _row_leg_ids(r)
            leg_fired = 0
            if fired is not None and legs:
                # a leg is 'fired' when it is no longer resting on the exchange
                leg_fired = 0 if any(lid in fired for lid in legs) else 1
            return (-leg_fired, *_age_key(r))
        ordered = sorted(rows, key=_order)
        closed_sum = 0.0
        for r in ordered:
            size = float(r.get("position_size") or 0.0)
            # NEVER close more than the surplus — a row that would push us past
            # the surplus is a still-live share; keep it.
            if closed_sum + size > surplus + tolerance:
                continue
            legs = _row_leg_ids(r)
            leg_note = ("leg_fired" if (fired is not None and legs
                        and not any(lid in fired for lid in legs)) else "fifo_oldest")
            to_close.append({
                "id": int(r["id"]), "group": gk, "size": size,
                "why": f"netted surplus {surplus:.10g} (journal {total:.10g} > "
                       f"exchange {float(exch):.10g}); {leg_note}",
                "exchange_size": float(exch),
                "original": {"status": r.get("status"),
                             "position_size": r.get("position_size"),
                             "pnl": r.get("pnl"), "exit_price": r.get("exit_price"),
                             "exit_reason": r.get("exit_reason")},
            })
            closed_sum += size
    return to_close, skipped


def _load_open_rows(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    strat_col = "strategy_name" if "strategy_name" in cols else (
        "strategy" if "strategy" in cols else None)
    sel = ["id", "account_id", "symbol", "direction", "position_size", "status",
           "created_at", "pnl", "exit_price", "exit_reason", "notes"]
    if strat_col:
        sel.append(f"{strat_col} AS strategy")
    for opt in ("sl_order_id", "tp_order_id", "is_backtest"):
        if opt in cols:
            sel.append(opt)
    q = (f"SELECT {', '.join(sel)} FROM trades WHERE status='open'"
         + (" AND (is_backtest=0 OR is_backtest IS NULL)" if "is_backtest" in cols else ""))
    return [dict(r) for r in conn.execute(q)]


def _parse_exchange(raw: Any) -> Tuple[Dict[Tuple[str, str, str], Optional[float]],
                                       Dict[Tuple[str, str, str], List[str]]]:
    """Accept {"account/symbol/direction": size|null, ...} OR the richer
    {"account/symbol/direction": {"size": s, "resting_legs": [...]}} form."""
    exch: Dict[Tuple[str, str, str], Optional[float]] = {}
    legs: Dict[Tuple[str, str, str], List[str]] = {}
    for k, v in (raw or {}).items():
        parts = tuple(k.split("/", 2))
        if len(parts) != 3:
            continue
        if isinstance(v, dict):
            exch[parts] = None if v.get("size") is None else float(v["size"])
            if v.get("resting_legs") is not None:
                legs[parts] = [str(x) for x in v["resting_legs"]]
        else:
            exch[parts] = None if v is None else float(v)
    return exch, legs


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="trade_journal.db (default: canonical resolver)")
    ap.add_argument("--exchange-json", required=True,
                    help="JSON map 'account/symbol/direction' -> size|null (or {size,resting_legs}); "
                         "the live same-moment broker snapshot the VM wrapper writes")
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = ap.parse_args(argv)

    db_path = Path(args.db) if args.db else Path(trade_journal_db_path())
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2
    try:
        raw = json.loads(Path(args.exchange_json).read_text())
    except (OSError, ValueError) as exc:
        print(f"cannot read --exchange-json: {exc}", file=sys.stderr)
        return 2
    exchange, resting = _parse_exchange(raw)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        open_rows = _load_open_rows(conn)
        to_close, skipped = reconcile_plan(open_rows, exchange, resting, args.tolerance)

        print(f"{'APPLY' if args.apply else 'DRY-RUN'}: {len(open_rows)} open non-backtest rows; "
              f"{len(to_close)} to supersede across {len({c['group'] for c in to_close})} group(s); "
              f"{len(skipped)} group(s) skipped.")
        for c in to_close:
            a, s, d = c["group"]
            print(f"  {'close' if args.apply else 'would close'} trade {c['id']} "
                  f"{a}/{s}/{d} size={c['size']:.10g} — {c['why']}")
        unread = [g for g in skipped if g["reason"] == "exchange_unreadable"]
        if unread:
            print(f"  SKIPPED {len(unread)} group(s) with unreadable exchange size "
                  "(fail-safe — never close on an unconfirmed broker read):")
            for g in unread:
                print(f"    {'/'.join(g['group'])}")

        if not args.apply:
            print("\nDRY-RUN — no changes written. Re-run with --apply to commit.")
            return 0

        now = datetime.now(timezone.utc).isoformat()
        by_id = {int(r["id"]): r for r in open_rows}
        for c in to_close:
            r = by_id[c["id"]]
            try:
                notes = json.loads(r["notes"]) if r.get("notes") else {}
                if not isinstance(notes, dict):
                    notes = {"_original_notes": notes}
            except (ValueError, TypeError):
                notes = {"_original_notes_raw": r.get("notes")}
            notes["netting_partial_reconcile"] = {
                "backlog_id": BACKLOG_ID, "reconciled_at": now, "why": c["why"],
                "exchange_size_at_check": c["exchange_size"], "original": c["original"],
            }
            conn.execute(
                "UPDATE trades SET status='closed', reconcile_status='superseded', "
                "exit_reason=?, closed_at=?, notes=? WHERE id=? AND status='open'",
                (EXIT_REASON, now, json.dumps(notes), c["id"]),
            )
        conn.commit()
        print(f"\nAPPLIED — superseded {len(to_close)} row(s). pnl left UNMEASURED (never mark-priced).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
