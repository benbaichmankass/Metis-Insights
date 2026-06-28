"""One-shot DB rebuild from Bybit ground truth.

Reverse-direction backfill: iterate Bybit's closed-pnl records
(the AUTHORITATIVE history) and assign each to the matching DB
trade row. Where the existing backfill scripts iterated DB rows
and looked up Bybit (which could miss closes the bot never
recorded), this iterates Bybit and rewrites the DB to match.

Operator directive 2026-05-18 after issue #1429's audit:
  "Fix the database, then investigate strategy performance."
  The audit revealed 92 of 122 matched DB rows have |diff| > $0.01
  vs Bybit, plus 11 stuck rows from issue #1419 that couldn't be
  reverted (notes truncated). This script rewrites all matched
  DB rows from Bybit so subsequent strategy analysis runs on
  clean data.

What this fixes:
  * Every closed DB trade in the last 7 days that has a matching
    Bybit closed-pnl record gets its pnl / exit_price / pnl_percent
    overwritten with Bybit's authoritative values.
  * notes JSON stamped with:
      - rebuilt_at / rebuilt_by / rebuilt_source='bybit_ground_truth'
      - bybit_closed_pnl (the true value)
      - pre_rebuild_pnl (what was there before — audit trail)
      - bybit_close_time / bybit_avg_exit_price (record metadata)
      - exit_price_source='bybit_closed_pnl_rebuild'

Matching rules (Bybit record → DB row):
  1. Filter DB rows by:
     - account_id matches
     - status = 'closed' (matches a closed-and-paired trade)
     - is_backtest = 0
     - symbol matches
     - direction → close_side (Sell for long, Buy for short)
     - qty within 5% of Bybit's qty
     - entry_price within 10 bps of Bybit's avgEntryPrice
     - created_at <= bybit.createdTime + 2s (open before close)
  2. Among matches, pick the DB row with created_at CLOSEST to
     (but at or before) bybit.createdTime — the most recent open
     before this close.
  3. Mark the DB row "consumed" so it doesn't match a later Bybit
     record. Each Bybit record claims exactly one DB row.

Idempotent: re-running rewrites with the same values, so a
successful rebuild produces 0 actual changes on retry. Each row
gets a fresh rebuilt_at timestamp.

Usage on the VM:
    cd /home/ubuntu/ict-trading-bot
    python3 scripts/ops/rebuild_pnl_from_bybit.py --account bybit_2          # dry-run
    python3 scripts/ops/rebuild_pnl_from_bybit.py --account bybit_2 --apply  # write
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.config.accounts_loader import load_accounts_dict  # noqa: E402
from src.units.accounts.clients import bybit_client_for  # noqa: E402
from src.units.accounts.execute import _bybit_category  # noqa: E402


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _parse_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if "T" not in s and " " in s:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_all_closed_pnl(
    client: Any, *, category: str, start_ms: int, end_ms: int,
) -> List[Dict[str, Any]]:
    """Paginate all closed-pnl records on the account in the window."""
    all_records: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    page = 0
    while True:
        page += 1
        kwargs: Dict[str, Any] = dict(
            category=category,
            startTime=start_ms,
            endTime=end_ms,
            limit=200,
        )
        if cursor:
            kwargs["cursor"] = cursor
        try:
            resp = client.get_closed_pnl(**kwargs) or {}
        except Exception as exc:  # noqa: BLE001
            print(f"error: get_closed_pnl page={page} raised: {exc}",
                  file=sys.stderr)
            break
        result = resp.get("result") or {}
        records = result.get("list") or []
        all_records.extend(records)
        cursor = result.get("nextPageCursor") or None
        if not cursor or not records:
            break
        if page > 30:
            print(f"warning: stopping at page {page}", file=sys.stderr)
            break
    return all_records


def _load_candidate_db_rows(
    conn: sqlite3.Connection, account_id: str, since_ms: int,
) -> List[Dict[str, Any]]:
    """Pull all closed non-backtest DB rows for the account, opened
    within the audit window (since_ms is the start of the Bybit
    closed-pnl window minus some slack for trades opened just
    before)."""
    since_iso = datetime.fromtimestamp(
        since_ms / 1000, tz=timezone.utc
    ).isoformat()
    rows = conn.execute(
        """
        SELECT id, symbol, direction, entry_price, exit_price,
               position_size, status, exit_reason, pnl, pnl_percent,
               is_backtest, strategy_name, account_id, created_at,
               timestamp, notes
        FROM trades
        WHERE account_id = ?
          AND status = 'closed'
          AND COALESCE(is_backtest, 0) = 0
          AND datetime(created_at) >= datetime(?)
        ORDER BY datetime(created_at) ASC, id ASC
        """,
        (account_id, since_iso),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["_created_at_ms"] = _parse_ms(d.get("created_at"))
        d["_consumed"] = False
        out.append(d)
    return out


def _find_matching_db_row(
    rec: Dict[str, Any], db_rows: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Find the DB row that this Bybit closed-pnl record corresponds to.

    Returns (matched_row, skip_reason). Exactly one is non-None.
    """
    sym = str(rec.get("symbol") or "")
    if not sym:
        return None, "Bybit record has no symbol"

    try:
        rec_qty = float(rec.get("qty") or 0)
        rec_entry = float(rec.get("avgEntryPrice") or 0)
        rec_created = int(rec.get("createdTime") or rec.get("updatedTime") or 0)
    except (TypeError, ValueError):
        return None, "Bybit record has non-numeric fields"
    if rec_qty <= 0 or rec_entry <= 0 or rec_created <= 0:
        return None, "Bybit record has zero/negative fields"

    rec_side = str(rec.get("side") or "")
    # The DB stores trade direction (long/short); Bybit's close-side
    # is the OPPOSITE — Sell-close for a long position, Buy-close
    # for a short. Map back.
    if rec_side.lower() == "sell":
        want_direction = "long"
    elif rec_side.lower() == "buy":
        want_direction = "short"
    else:
        return None, f"unrecognised side={rec_side!r}"

    best: Optional[Dict[str, Any]] = None
    best_gap_ms: int = -1

    for row in db_rows:
        if row["_consumed"]:
            continue
        if str(row.get("symbol") or "") != sym:
            continue
        if str(row.get("direction") or "").lower() != want_direction:
            continue
        try:
            row_qty = float(row.get("position_size") or 0)
            row_entry = float(row.get("entry_price") or 0)
        except (TypeError, ValueError):
            continue
        if row_qty <= 0 or row_entry <= 0:
            continue
        if abs(row_qty - rec_qty) / rec_qty > 0.05:
            continue
        if abs(row_entry - rec_entry) / rec_entry > 0.001:
            continue
        opened_at_ms = row.get("_created_at_ms")
        if opened_at_ms is None:
            continue
        # Close must happen at or after open (2s slack for clock skew).
        if rec_created + 2_000 < opened_at_ms:
            continue
        gap = rec_created - opened_at_ms
        # Prefer the OPEN that's closest to (but not after) the close —
        # smaller gap = the trade that opened most recently before
        # this close. This implements the same temporal ordering as
        # the live matcher in PR #1425.
        if best is None or gap < best_gap_ms:
            best = row
            best_gap_ms = gap

    if best is None:
        return None, (
            f"no DB row matches "
            f"symbol={sym} direction={want_direction} "
            f"qty={rec_qty} entry={rec_entry} "
            f"close_at_ms={rec_created}"
        )
    return best, None


def _decode_notes(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _plan_rewrite(
    db_row: Dict[str, Any], rec: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Construct the UPDATE dict for one matched (DB row, Bybit rec) pair.

    Returns None when the values already agree (idempotent no-op)."""
    bybit_pnl = _f(rec.get("closedPnl"))
    bybit_exit = _f(rec.get("avgExitPrice"))
    if bybit_exit <= 0:
        return None

    try:
        entry = float(db_row.get("entry_price") or 0)
        qty = float(db_row.get("position_size") or 0)
    except (TypeError, ValueError):
        entry = 0
        qty = 0
    notional = entry * qty if entry > 0 and qty > 0 else 0
    pnl_percent = (
        round(bybit_pnl / notional * 100.0, 4) if notional > 0 else None
    )

    notes = _decode_notes(db_row.get("notes"))
    # Skip rewrite if the DB pnl already matches Bybit AND the notes
    # already carry our rebuild stamp (so re-runs are no-ops).
    db_pnl = db_row.get("pnl")
    if (
        db_pnl is not None
        and abs(float(db_pnl) - bybit_pnl) < 0.005
        and notes.get("rebuilt_by") == "rebuild_pnl_from_bybit_script"
    ):
        return None

    new_notes = dict(notes)
    new_notes["rebuilt_at"] = datetime.now(timezone.utc).isoformat()
    new_notes["rebuilt_by"] = "rebuild_pnl_from_bybit_script"
    new_notes["rebuilt_source"] = "bybit_ground_truth"
    new_notes["bybit_closed_pnl"] = bybit_pnl
    new_notes["bybit_avg_exit_price"] = bybit_exit
    new_notes["bybit_close_time"] = rec.get("createdTime")
    new_notes["pre_rebuild_pnl"] = db_pnl
    new_notes["exit_price_source"] = "bybit_closed_pnl_rebuild"
    # Drop the bad backfill stamps if present (they reference wrong
    # records).
    for k in ("backfilled_at", "backfilled_by", "backfilled_source",
              "original_pnl"):
        new_notes.pop(k, None)

    updates: Dict[str, Any] = {
        "pnl": round(bybit_pnl, 4),
        "exit_price": round(bybit_exit, 4),
        "notes": json.dumps(new_notes, ensure_ascii=False)[:4000],
    }
    if pnl_percent is not None:
        updates["pnl_percent"] = pnl_percent
    return updates


def _apply_updates(
    conn: sqlite3.Connection,
    plans: List[Tuple[int, Dict[str, Any]]],
) -> int:
    cur = conn.cursor()
    n = 0
    for trade_id, u in plans:
        sets = ", ".join(f"{k} = ?" for k in u.keys())
        params = list(u.values()) + [trade_id]
        cur.execute(
            f"UPDATE trades SET {sets} WHERE id = ?", params,
        )
        n += cur.rowcount
    conn.commit()
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True,
                        help="account_id from config/accounts.yaml")
    parser.add_argument("--days", type=int, default=7,
                        help="Lookback in days. Default 7 "
                             "(Bybit retention max).")
    parser.add_argument("--apply", action="store_true",
                        help="Write changes (default: dry-run).")
    parser.add_argument("--db", default=None,
                        help="Path to trade_journal.db")
    parser.add_argument("--show", type=int, default=30,
                        help="Cap on per-row preview lines printed.")
    args = parser.parse_args()

    cfgs = load_accounts_dict()
    cfg = cfgs.get(args.account)
    if cfg is None:
        print(f"error: account_id={args.account!r} not in accounts.yaml",
              file=sys.stderr)
        return 2

    try:
        category = _bybit_category(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"error: _bybit_category raised: {exc}", file=sys.stderr)
        return 3
    if category not in ("linear", "inverse"):
        print(f"error: unsupported category={category!r}", file=sys.stderr)
        return 4

    from src.utils.paths import trade_journal_db_path
    db_path = args.db or str(trade_journal_db_path())
    if not os.path.exists(db_path):
        print(f"error: db not found at {db_path}", file=sys.stderr)
        return 5

    client = bybit_client_for(cfg)
    if client is None:
        print("error: bybit_client_for returned None (creds missing?)",
              file=sys.stderr)
        return 6

    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - args.days * 24 * 60 * 60 * 1000

    print(f"===== rebuild_pnl_from_bybit: {args.account} =====")
    print(f"  window={args.days}d")
    print(f"  db={db_path}")
    print(f"  category={category}")
    print()

    print("===== fetching Bybit closed-pnl =====")
    records = _fetch_all_closed_pnl(
        client, category=category, start_ms=start_ms, end_ms=end_ms,
    )
    print(f"  {len(records)} closed-pnl record(s)")
    print()

    conn = _connect(db_path)
    # Look back slightly further than the closed-pnl window for the
    # DB so trades that opened just before the window edge can still
    # match closes inside.
    db_rows = _load_candidate_db_rows(
        conn, args.account, start_ms - 24 * 60 * 60 * 1000,
    )
    print(f"  {len(db_rows)} DB closed non-backtest row(s) in window")
    print()

    plans: List[Tuple[int, Dict[str, Any]]] = []
    skipped: List[Tuple[str, str]] = []
    noops = 0

    # Iterate Bybit records oldest-first so consumption is deterministic
    # and earlier closes claim earlier opens.
    records_sorted = sorted(
        records,
        key=lambda r: int(r.get("createdTime") or r.get("updatedTime") or 0),
    )
    for rec in records_sorted:
        row, reason = _find_matching_db_row(rec, db_rows)
        if row is None:
            skipped.append((str(rec.get("orderId") or "?"),
                            reason or "unknown"))
            continue
        plan = _plan_rewrite(row, rec)
        if plan is None:
            noops += 1
            row["_consumed"] = True
            continue
        plans.append((int(row["id"]), plan))
        row["_consumed"] = True

    unconsumed = [r for r in db_rows if not r["_consumed"]]

    print("===== match summary =====")
    print(f"  Bybit records:     {len(records)}")
    print(f"  → matched:         {len(plans) + noops}")
    print(f"  → already-fresh:   {noops}  (no rewrite needed)")
    print(f"  → would-rewrite:   {len(plans)}")
    print(f"  → unmatched-Bybit: {len(skipped)}")
    print(f"  DB unconsumed:     {len(unconsumed)}  "
          "(closed in DB but no Bybit match — typically partial-fills, "
          "retried opens, or Bybit-retention-expired)")
    print()

    if plans:
        print("===== rewrites (DB → Bybit) =====")
        for trade_id, u in plans[:args.show]:
            row = next(r for r in db_rows if r["id"] == trade_id)
            db_pnl = row["pnl"]
            print(f"  id={trade_id:<5} "
                  f"{str(row['direction'] or '?'):>5} "
                  f"{str(row['symbol'] or '?'):<10} "
                  f"entry={row['entry_price']!s:<10} "
                  f"db_pnl={db_pnl!s:<14} "
                  f"→ bybit_pnl={u['pnl']:+.4f}")
        if len(plans) > args.show:
            print(f"  ... and {len(plans) - args.show} more")
        print()

    if skipped:
        print("===== Bybit records with NO DB match =====")
        for order_id, why in skipped[:args.show]:
            print(f"  order={order_id}: {why}")
        if len(skipped) > args.show:
            print(f"  ... and {len(skipped) - args.show} more")
        print()

    if not args.apply:
        print("dry-run — pass --apply to write.")
        return 0

    n = _apply_updates(conn, plans)
    print(f"wrote {n} row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
