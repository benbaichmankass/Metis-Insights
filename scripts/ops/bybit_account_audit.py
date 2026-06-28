"""Account-wide Bybit audit — pulls the authoritative ground truth.

Triggered by the 2026-05-18 operator concern that the live trader
"posts 7-10 losses per win" — and we know the trade_journal.db
has stale/wrong PnL on at least 11 rows (issue #1419 fallout).
Before drawing any strategy/performance conclusion, we need the
ACTUAL account history from Bybit, not the bot's local view.

This script does NOT touch the trade DB. It queries Bybit V5
directly for one account and prints:

  1. Closed-pnl aggregate (last N days, paginated):
     * total closed positions
     * win count / loss count / breakeven count
     * win rate %
     * sum closed_pnl (gross of fees per Bybit)
     * sum exec_fee (fees paid)
     * net realised pnl
     * largest win / largest loss
     * mean win / mean loss
     * Sharpe-ish: win/loss ratio
  2. Execution-level fees: pulls /v5/execution/list and sums
     ``execFee`` to verify Bybit's closedPnl already nets fees
     (it does — but we verify on real data, not docs)
  3. Per-day P&L distribution (last 7 days):
     * dt | count | wins | losses | win_rate | sum_pnl
  4. DB cross-check: for each Bybit closed position, find the
     matching trade row by (symbol, direction, entry_price ±10bps,
     opened_at_ms ±5min) and report:
     * matched / unmatched count
     * for matched: DB pnl vs Bybit pnl, |diff| > $0.01 flagged

Usage on the VM:
    cd /home/ubuntu/ict-trading-bot
    python3 scripts/ops/bybit_account_audit.py --account bybit_2

Tier-1 read-only diagnostic. No writes anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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


def _fetch_all_closed_pnl(
    client: Any,
    *,
    category: str,
    symbol: Optional[str],
    start_ms: int,
    end_ms: int,
) -> List[Dict[str, Any]]:
    """Paginate ``/v5/position/closed-pnl`` until cursor exhausted."""
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
        if symbol:
            kwargs["symbol"] = symbol
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
            print(f"warning: stopping pagination at page {page} "
                  "(6000+ records — investigate)", file=sys.stderr)
            break
    return all_records


def _fetch_all_executions(
    client: Any,
    *,
    category: str,
    symbol: Optional[str],
    start_ms: int,
    end_ms: int,
) -> List[Dict[str, Any]]:
    """Paginate ``/v5/execution/list`` to get the fee ledger."""
    all_execs: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    page = 0
    while True:
        page += 1
        kwargs: Dict[str, Any] = dict(
            category=category,
            startTime=start_ms,
            endTime=end_ms,
            limit=100,
        )
        if symbol:
            kwargs["symbol"] = symbol
        if cursor:
            kwargs["cursor"] = cursor
        try:
            resp = client.get_executions(**kwargs) or {}
        except Exception as exc:  # noqa: BLE001
            print(f"error: get_executions page={page} raised: {exc}",
                  file=sys.stderr)
            break
        result = resp.get("result") or {}
        execs = result.get("list") or []
        all_execs.extend(execs)
        cursor = result.get("nextPageCursor") or None
        if not cursor or not execs:
            break
        if page > 30:
            break
    return all_execs


def _summarise_closed_pnl(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not records:
        return {
            "count": 0, "wins": 0, "losses": 0, "breakeven": 0,
            "win_rate_pct": None, "sum_pnl": 0.0,
            "largest_win": None, "largest_loss": None,
            "mean_win": None, "mean_loss": None,
        }
    pnls = [_f(r.get("closedPnl")) for r in records]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    breakeven = [p for p in pnls if p == 0]
    return {
        "count": len(records),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate_pct": (
            round(100 * len(wins) / (len(wins) + len(losses)), 2)
            if (len(wins) + len(losses)) > 0 else None
        ),
        "sum_pnl": round(sum(pnls), 4),
        "largest_win": round(max(wins), 4) if wins else None,
        "largest_loss": round(min(losses), 4) if losses else None,
        "mean_win": round(sum(wins) / len(wins), 4) if wins else None,
        "mean_loss": round(sum(losses) / len(losses), 4) if losses else None,
    }


def _summarise_executions(execs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not execs:
        return {"count": 0, "sum_fee": 0.0}
    fees = [_f(e.get("execFee")) for e in execs]
    return {
        "count": len(execs),
        "sum_fee": round(sum(fees), 4),
    }


def _per_day_breakdown(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Bucket records by UTC date of ``createdTime`` and aggregate."""
    by_day: Dict[str, List[float]] = defaultdict(list)
    for rec in records:
        try:
            ts_ms = int(rec.get("createdTime") or rec.get("updatedTime") or 0)
        except (TypeError, ValueError):
            continue
        if ts_ms <= 0:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        by_day[d].append(_f(rec.get("closedPnl")))
    out: List[Dict[str, Any]] = []
    for d in sorted(by_day.keys()):
        pnls = by_day[d]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        out.append({
            "date": d,
            "count": len(pnls),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": (
                round(100 * len(wins) / (len(wins) + len(losses)), 1)
                if (len(wins) + len(losses)) > 0 else None
            ),
            "sum_pnl": round(sum(pnls), 4),
        })
    return out


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


def _db_cross_check(
    db_path: str, account_id: str, records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Pair each Bybit closed-pnl record with one DB trade row.

    Uses the same temporal-ordering matcher as the live close-loop
    (PR #1425) and the rebuild script (PR #1430): for each Bybit
    record (oldest first), find the DB row whose
    (symbol, direction, qty±5%, entry_price±10bps) matches AND
    whose ``created_at`` is the most-recent open at or before the
    Bybit record's ``createdTime``. Each DB row is consumed once.

    2026-05-18 fix (post-#1433): the prior matcher used
    closest-by-entry-price only, which mis-paired adjacent trades
    that shared (side, qty, entry) within tolerance. The audit's
    "92 discrepancies" was largely audit-side mispairings, not
    rebuild-side errors.

    Returns matched / unmatched-db / unmatched-bybit counts +
    flagged rows where |db_pnl - bybit_pnl| > $0.01.
    """
    if not os.path.exists(db_path):
        return {"db_present": False}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    db_rows_raw = conn.execute(
        "SELECT id, symbol, direction, entry_price, exit_price, "
        "       position_size, status, pnl, account_id, created_at, "
        "       notes "
        "  FROM trades "
        " WHERE account_id = ? "
        "   AND COALESCE(is_backtest, 0) = 0 "
        "   AND status = 'closed' "
        " ORDER BY datetime(created_at) ASC, id ASC LIMIT 500",
        (account_id,),
    ).fetchall()
    conn.close()

    db_rows: List[Dict[str, Any]] = []
    for r in db_rows_raw:
        d = dict(r)
        d["_created_at_ms"] = _parse_ms(d.get("created_at"))
        d["_consumed"] = False
        db_rows.append(d)

    matched: List[Dict[str, Any]] = []
    bybit_unmatched: List[int] = []

    # Iterate Bybit records oldest-first so consumption is
    # deterministic — earlier closes claim earlier opens.
    records_sorted = sorted(
        records,
        key=lambda r: int(r.get("createdTime")
                          or r.get("updatedTime") or 0),
    )

    for rec in records_sorted:
        sym = str(rec.get("symbol") or "")
        try:
            rec_qty = float(rec.get("qty") or 0)
            rec_entry = float(rec.get("avgEntryPrice") or 0)
            rec_created = int(rec.get("createdTime")
                              or rec.get("updatedTime") or 0)
        except (TypeError, ValueError):
            continue
        if rec_qty <= 0 or rec_entry <= 0 or rec_created <= 0:
            continue
        rec_side = str(rec.get("side") or "")
        if rec_side.lower() == "sell":
            want_direction = "long"
        elif rec_side.lower() == "buy":
            want_direction = "short"
        else:
            continue

        best_row: Optional[Dict[str, Any]] = None
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
            if rec_created + 2_000 < opened_at_ms:
                continue
            gap = rec_created - opened_at_ms
            # Smallest gap = most-recent open before this close
            if best_row is None or gap < best_gap_ms:
                best_row = row
                best_gap_ms = gap

        if best_row is None:
            bybit_unmatched.append(records.index(rec))
            continue
        best_row["_consumed"] = True
        row = best_row
        bybit_pnl = _f(rec.get("closedPnl"))
        db_pnl = row["pnl"]
        diff = None
        flagged = False
        if db_pnl is not None:
            try:
                diff = round(float(db_pnl) - bybit_pnl, 4)
                flagged = abs(diff) > 0.01
            except (TypeError, ValueError):
                pass
        matched.append({
            "trade_id": row["id"],
            "symbol": sym,
            "direction": str(row.get("direction") or "").lower(),
            "entry": float(row.get("entry_price") or 0),
            "db_pnl": db_pnl,
            "bybit_pnl": bybit_pnl,
            "diff": diff,
            "flagged": flagged,
        })

    db_unmatched = [
        {"trade_id": r["id"],
         "symbol": r.get("symbol"),
         "direction": r.get("direction"),
         "entry": r.get("entry_price"),
         "reason": "no Bybit close paired (consumed-row matcher)"}
        for r in db_rows if not r["_consumed"]
    ]

    return {
        "db_present": True,
        "matched_count": len(matched),
        "db_unmatched_count": len(db_unmatched),
        "bybit_unmatched_count": len(bybit_unmatched),
        "matched": matched,
        "db_unmatched": db_unmatched,
        "bybit_unmatched_indices": sorted(bybit_unmatched),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True,
                        help="account_id from config/accounts.yaml")
    parser.add_argument("--symbol", default=None,
                        help="Restrict to one symbol (e.g. BTCUSDT). "
                             "Default: all symbols on the account.")
    parser.add_argument("--days", type=int, default=7,
                        help="Lookback in days (Bybit retention is "
                             "7 days for closed-pnl). Default 7.")
    parser.add_argument("--db", default=None,
                        help="Path to trade_journal.db (for cross-check)")
    parser.add_argument("--no-execs", action="store_true",
                        help="Skip the execution-list fee summary "
                             "(faster).")
    parser.add_argument("--no-db-check", action="store_true",
                        help="Skip the local-DB cross-check.")
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

    client = bybit_client_for(cfg)
    if client is None:
        print("error: bybit_client_for returned None (creds missing?)",
              file=sys.stderr)
        return 5

    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - args.days * 24 * 60 * 60 * 1000
    start_iso = datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp(end_ms/1000, tz=timezone.utc).isoformat()

    print(f"===== account audit: {args.account} =====")
    print(f"  category={category}")
    print(f"  symbol={args.symbol or '<all>'}")
    print(f"  window={start_iso} → {end_iso} ({args.days} days)")
    print()

    print("===== fetching closed-pnl (paginated) =====")
    closed = _fetch_all_closed_pnl(
        client, category=category, symbol=args.symbol,
        start_ms=start_ms, end_ms=end_ms,
    )
    print(f"  fetched {len(closed)} closed-pnl record(s)")
    print()

    summary = _summarise_closed_pnl(closed)
    print("===== closed-pnl aggregate =====")
    print(json.dumps(summary, indent=2))
    print()

    by_day = _per_day_breakdown(closed)
    print("===== per-day breakdown =====")
    for row in by_day:
        wr = (f"{row['win_rate_pct']:>5}%"
              if row['win_rate_pct'] is not None else "  n/a")
        print(f"  {row['date']}  count={row['count']:<3} "
              f"wins={row['wins']:<3} losses={row['losses']:<3} "
              f"win_rate={wr}  sum_pnl={row['sum_pnl']:+.4f}")
    print()

    if not args.no_execs:
        print("===== fetching executions (paginated) =====")
        execs = _fetch_all_executions(
            client, category=category, symbol=args.symbol,
            start_ms=start_ms, end_ms=end_ms,
        )
        fee_summary = _summarise_executions(execs)
        print(f"  {fee_summary['count']} executions, "
              f"sum_fee = {fee_summary['sum_fee']:.4f}")
        print()

    if not args.no_db_check:
        from src.utils.paths import trade_journal_db_path
        db_path = args.db or str(trade_journal_db_path())
        print("===== local-DB cross-check =====")
        cross = _db_cross_check(db_path, args.account, closed)
        if not cross.get("db_present"):
            print(f"  (DB not found at {db_path} — skipped)")
        else:
            print(f"  DB closed trades (account={args.account}): "
                  f"matched={cross['matched_count']}, "
                  f"unmatched_db={cross['db_unmatched_count']}, "
                  f"unmatched_bybit={cross['bybit_unmatched_count']}")
            flagged = [m for m in cross["matched"] if m["flagged"]]
            print(f"  PnL discrepancies (|diff| > $0.01): {len(flagged)}")
            if flagged:
                print()
                print("  Flagged rows (DB pnl vs Bybit truth):")
                # Sort by absolute diff descending
                flagged.sort(key=lambda m: abs(m["diff"] or 0), reverse=True)
                for m in flagged[:30]:
                    print(f"    id={m['trade_id']:<5} "
                          f"{str(m['direction']):>5} "
                          f"{str(m['symbol']):<10} "
                          f"entry={m['entry']:<10} "
                          f"db_pnl={m['db_pnl']!s:<10} "
                          f"bybit_pnl={m['bybit_pnl']:+.4f}  "
                          f"diff={m['diff']:+.4f}")
                if len(flagged) > 30:
                    print(f"    ... and {len(flagged) - 30} more")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
