"""Strategy-performance audit: why is the trader losing money.

Joins the now-clean trade_journal.db (rebuilt from Bybit ground
truth in issue #1432) with the Bybit closed-pnl ledger and
extracts breakdowns that point at where the strategy is failing.

Triggered by the 2026-05-18 confirmation that the 18.25% win
rate + -$44/7d net is REAL (not accounting). With clean data we
can now ask:

  1. Where are the wins coming from?
     - By hour-of-day (regime / liquidity windows)
     - By direction (long vs short — one-sided risk?)
     - By exit_reason (tp_cross vs monitor SL vs reconciler-filled)
     - By signal-time deviation_std bucket (extreme stretches vs
       small ones)
  2. Is the R:R geometry viable?
     - Mean planned TP distance vs mean planned SL distance
     - Required win-rate for breakeven at the configured R:R
       (1 / (1 + R)); compare to observed
  3. How much of the loss is fees vs strategy?
     - Sum_fees / sum_|pnl| ratio
     - Per-trade fee drag vs mean win
  4. Is there slippage drift?
     - Signal-time entry vs Bybit avgEntryPrice (entry slippage)
     - Verdict exit vs Bybit avgExitPrice (exit slippage)

Read-only. No DB writes; no live-trading side effects.

Usage:
    python3 scripts/ops/strategy_performance_audit.py \\
        --account bybit_2 --days 7
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
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


def _decode_notes(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _fetch_bybit_closed_pnl(
    client: Any, *, category: str, start_ms: int, end_ms: int,
) -> List[Dict[str, Any]]:
    all_recs: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        kw: Dict[str, Any] = dict(category=category, startTime=start_ms,
                                  endTime=end_ms, limit=200)
        if cursor:
            kw["cursor"] = cursor
        resp = client.get_closed_pnl(**kw) or {}
        result = resp.get("result") or {}
        recs = result.get("list") or []
        all_recs.extend(recs)
        cursor = result.get("nextPageCursor") or None
        if not cursor or not recs:
            break
    return all_recs


def _fetch_bybit_executions(
    client: Any, *, category: str, start_ms: int, end_ms: int,
) -> List[Dict[str, Any]]:
    all_execs: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        kw: Dict[str, Any] = dict(category=category, startTime=start_ms,
                                  endTime=end_ms, limit=100)
        if cursor:
            kw["cursor"] = cursor
        resp = client.get_executions(**kw) or {}
        result = resp.get("result") or {}
        execs = result.get("list") or []
        all_execs.extend(execs)
        cursor = result.get("nextPageCursor") or None
        if not cursor or not execs:
            break
    return all_execs


def _load_db_rows(
    db_path: str, account_id: str, since_ms: int,
) -> List[Dict[str, Any]]:
    since_iso = datetime.fromtimestamp(
        since_ms / 1000, tz=timezone.utc
    ).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, symbol, direction, entry_price, exit_price,
               stop_loss, take_profit_1, position_size, status,
               exit_reason, pnl, account_id, strategy_name,
               created_at, timestamp, notes
        FROM trades
        WHERE account_id = ?
          AND status = 'closed'
          AND COALESCE(is_backtest, 0) = 0
          AND datetime(created_at) >= datetime(?)
        ORDER BY datetime(created_at) ASC, id ASC
        """,
        (account_id, since_iso),
    ).fetchall()
    conn.close()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["_created_at_ms"] = _parse_ms(d.get("created_at"))
        d["_consumed"] = False
        d["_notes"] = _decode_notes(d.get("notes"))
        out.append(d)
    return out


def _pair_bybit_to_db(
    bybit_recs: List[Dict[str, Any]],
    db_rows: List[Dict[str, Any]],
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Same temporal-ordering matcher as PR #1425 / #1430 / #1434.
    Returns list of (db_row, bybit_rec) pairs."""
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    recs_sorted = sorted(
        bybit_recs,
        key=lambda r: int(r.get("createdTime") or r.get("updatedTime") or 0),
    )
    for rec in recs_sorted:
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
        rec_side = str(rec.get("side") or "").lower()
        want_dir = ("long" if rec_side == "sell"
                    else "short" if rec_side == "buy" else None)
        if want_dir is None:
            continue
        best_row: Optional[Dict[str, Any]] = None
        best_gap: int = -1
        for row in db_rows:
            if row["_consumed"]:
                continue
            if str(row.get("symbol") or "") != sym:
                continue
            if str(row.get("direction") or "").lower() != want_dir:
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
            opened = row.get("_created_at_ms")
            if opened is None:
                continue
            if rec_created + 2_000 < opened:
                continue
            gap = rec_created - opened
            if best_row is None or gap < best_gap:
                best_row = row
                best_gap = gap
        if best_row is None:
            continue
        best_row["_consumed"] = True
        pairs.append((best_row, rec))
    return pairs


def _hour_bucket(ts_ms: int) -> int:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).hour


def _deviation_bucket(dev: float) -> str:
    if dev < 1.5:
        return "<1.5"
    if dev < 2.0:
        return "1.5-2.0"
    if dev < 2.5:
        return "2.0-2.5"
    if dev < 3.0:
        return "2.5-3.0"
    if dev < 4.0:
        return "3.0-4.0"
    return "4.0+"


def _wlsummary(pnls: List[float]) -> Dict[str, Any]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    return {
        "count": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": (
            round(100 * len(wins) / (len(wins) + len(losses)), 1)
            if (len(wins) + len(losses)) > 0 else None
        ),
        "sum_pnl": round(sum(pnls), 4),
        "mean_win": round(sum(wins) / len(wins), 4) if wins else None,
        "mean_loss": round(sum(losses) / len(losses), 4) if losses else None,
        "expectancy": round(sum(pnls) / len(pnls), 4) if pnls else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    cfgs = load_accounts_dict()
    cfg = cfgs.get(args.account)
    if cfg is None:
        print(f"error: account {args.account!r} not in accounts.yaml",
              file=sys.stderr)
        return 2
    category = _bybit_category(cfg)
    if category not in ("linear", "inverse"):
        print(f"error: unsupported category={category!r}", file=sys.stderr)
        return 3
    client = bybit_client_for(cfg)
    if client is None:
        print("error: creds missing", file=sys.stderr)
        return 4
    from src.utils.paths import trade_journal_db_path
    db_path = args.db or str(trade_journal_db_path())
    if not os.path.exists(db_path):
        print(f"error: db not found at {db_path}", file=sys.stderr)
        return 5

    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - args.days * 24 * 60 * 60 * 1000

    print(f"===== strategy performance audit: {args.account} =====")
    print(f"  window={args.days}d")
    print()

    bybit_recs = _fetch_bybit_closed_pnl(
        client, category=category, start_ms=start_ms, end_ms=end_ms,
    )
    executions = _fetch_bybit_executions(
        client, category=category, start_ms=start_ms, end_ms=end_ms,
    )
    db_rows = _load_db_rows(
        db_path, args.account, start_ms - 24 * 60 * 60 * 1000,
    )
    pairs = _pair_bybit_to_db(bybit_recs, db_rows)
    print(f"  paired {len(pairs)} trade-and-close pairs "
          f"(of {len(bybit_recs)} Bybit / {len(db_rows)} DB)")
    print()

    # ============== 1. Overall + by-direction summary ==============
    pnls_all = [_f(rec.get("closedPnl")) for _, rec in pairs]
    pnls_long = [_f(rec.get("closedPnl")) for db, rec in pairs
                 if str(db.get("direction") or "").lower() == "long"]
    pnls_short = [_f(rec.get("closedPnl")) for db, rec in pairs
                  if str(db.get("direction") or "").lower() == "short"]

    print("===== overall =====")
    print(json.dumps(_wlsummary(pnls_all), indent=2))
    print()
    print("===== by direction =====")
    print(f"  LONG :  {json.dumps(_wlsummary(pnls_long))}")
    print(f"  SHORT:  {json.dumps(_wlsummary(pnls_short))}")
    print()

    # ============== 1b. By strategy_name ==============
    # Which strategy is responsible for which slice of the losses?
    # If one strategy is positive-edge and another is bleeding, that
    # tells us where to focus.
    by_strategy: Dict[str, List[float]] = defaultdict(list)
    by_strategy_dir: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for db, rec in pairs:
        s_name = str(db.get("strategy_name") or "<none>")
        direction = str(db.get("direction") or "?").lower()
        pnl = _f(rec.get("closedPnl"))
        by_strategy[s_name].append(pnl)
        by_strategy_dir[(s_name, direction)].append(pnl)
    print("===== by strategy_name =====")
    print("  strategy            n     W   L   wr      sum_pnl  expectancy")
    for sname in sorted(by_strategy.keys()):
        s = _wlsummary(by_strategy[sname])
        wr = (f"{s['win_rate_pct']:>5}%"
              if s["win_rate_pct"] is not None else " n/a ")
        print(f"  {sname:<19} {s['count']:<4} {s['wins']:<3} {s['losses']:<3} "
              f"{wr}  {s['sum_pnl']:+8.4f}  {s['expectancy']!s}")
    print()
    # Strategy × direction — does any strategy's long bias dominate?
    print("===== by strategy × direction =====")
    print("  strategy            dir    n    W   L   wr      sum_pnl")
    for sname in sorted(by_strategy.keys()):
        for dir_ in ("long", "short"):
            items = by_strategy_dir.get((sname, dir_), [])
            if not items:
                continue
            s = _wlsummary(items)
            wr = (f"{s['win_rate_pct']:>5}%"
                  if s["win_rate_pct"] is not None else " n/a ")
            print(f"  {sname:<19} {dir_:<5}  {s['count']:<4} "
                  f"{s['wins']:<3} {s['losses']:<3} {wr}  "
                  f"{s['sum_pnl']:+8.4f}")
    print()

    # ============== 2. By exit_reason ==============
    by_reason: Dict[str, List[float]] = defaultdict(list)
    for db, rec in pairs:
        reason = str(db.get("exit_reason") or "<none>")
        by_reason[reason].append(_f(rec.get("closedPnl")))
    print("===== by exit_reason =====")
    for reason in sorted(by_reason.keys()):
        s = _wlsummary(by_reason[reason])
        wr = f"{s['win_rate_pct']:>5}%" if s["win_rate_pct"] is not None else "n/a"
        print(f"  {reason:<35} n={s['count']:<4} wr={wr}  "
              f"sum_pnl={s['sum_pnl']:+.4f}  "
              f"exp={s['expectancy']!s}")
    print()

    # ============== 3. By hour-of-day (UTC) ==============
    by_hour: Dict[int, List[float]] = defaultdict(list)
    for db, rec in pairs:
        opened_ms = db.get("_created_at_ms")
        if opened_ms is None:
            continue
        by_hour[_hour_bucket(opened_ms)].append(_f(rec.get("closedPnl")))
    print("===== by hour-of-day (UTC) =====")
    print("  hour  n   W   L   wr      sum_pnl  expectancy")
    for h in sorted(by_hour.keys()):
        s = _wlsummary(by_hour[h])
        wr = f"{s['win_rate_pct']:>5}%" if s["win_rate_pct"] is not None else " n/a "
        print(f"  {h:02d}    {s['count']:<3} {s['wins']:<3} {s['losses']:<3} {wr}  "
              f"{s['sum_pnl']:+8.4f}  {s['expectancy']!s}")
    print()

    # ============== 4. By signal-time deviation_std ==============
    by_dev: Dict[str, List[float]] = defaultdict(list)
    for db, rec in pairs:
        notes = db.get("_notes") or {}
        # deviation_std lives in signal-meta; some trades have it,
        # some don't (depending on which strategy version was active)
        dev = _f(notes.get("deviation_std")
                 or notes.get("dev_std")
                 or notes.get("meta", {}).get("deviation_std"))
        if dev == 0.0:
            continue
        by_dev[_deviation_bucket(dev)].append(_f(rec.get("closedPnl")))
    if by_dev:
        print("===== by signal-time deviation_std (VWAP) =====")
        for bucket in ("<1.5", "1.5-2.0", "2.0-2.5", "2.5-3.0",
                       "3.0-4.0", "4.0+"):
            if bucket not in by_dev:
                continue
            s = _wlsummary(by_dev[bucket])
            wr = f"{s['win_rate_pct']:>5}%" if s["win_rate_pct"] is not None else "n/a"
            print(f"  dev={bucket:<8} n={s['count']:<3} wr={wr}  "
                  f"sum_pnl={s['sum_pnl']:+.4f}  exp={s['expectancy']!s}")
        print()
    else:
        print("===== by signal-time deviation_std =====")
        print("  (deviation_std not present in notes; skipping)")
        print()

    # ============== 5. R:R geometry ==============
    rr_records: List[Tuple[float, float, float, float]] = []
    # tuples of (planned_tp_dist, planned_sl_dist, planned_rr, realized_dist)
    for db, rec in pairs:
        try:
            entry = float(db.get("entry_price") or 0)
            sl = float(db.get("stop_loss") or 0)
            tp = float(db.get("take_profit_1") or 0)
        except (TypeError, ValueError):
            continue
        if entry <= 0 or sl <= 0 or tp <= 0:
            continue
        direction = str(db.get("direction") or "").lower()
        if direction == "long":
            sl_dist = entry - sl
            tp_dist = tp - entry
        elif direction == "short":
            sl_dist = sl - entry
            tp_dist = entry - tp
        else:
            continue
        if sl_dist <= 0 or tp_dist <= 0:
            continue
        rr = tp_dist / sl_dist
        # Realized exit distance from entry, signed by direction.
        try:
            exit_actual = float(rec.get("avgExitPrice") or 0)
        except (TypeError, ValueError):
            continue
        if exit_actual <= 0:
            continue
        if direction == "long":
            realized = exit_actual - entry
        else:
            realized = entry - exit_actual
        rr_records.append((tp_dist, sl_dist, rr, realized))

    if rr_records:
        mean_tp = sum(r[0] for r in rr_records) / len(rr_records)
        mean_sl = sum(r[1] for r in rr_records) / len(rr_records)
        mean_rr = sum(r[2] for r in rr_records) / len(rr_records)
        breakeven_wr = 100.0 / (1 + mean_rr)
        print("===== R:R geometry =====")
        print(f"  mean planned TP distance: {mean_tp:.2f} (price units)")
        print(f"  mean planned SL distance: {mean_sl:.2f}")
        print(f"  mean planned R:R       : {mean_rr:.2f}")
        print(f"  breakeven win rate     : {breakeven_wr:.1f}%")
        s_all = _wlsummary(pnls_all)
        actual = s_all["win_rate_pct"] or 0
        gap = actual - breakeven_wr
        verdict = ("PROFITABLE (wr > breakeven)" if gap > 0
                   else f"UNPROFITABLE by {-gap:.1f} pp")
        print(f"  observed win rate      : {actual}%  ({verdict})")
        print()

    # ============== 6. Slippage analysis ==============
    entry_slips: List[float] = []
    exit_slips: List[float] = []
    for db, rec in pairs:
        try:
            entry_db = float(db.get("entry_price") or 0)
            exit_db = float(db.get("exit_price") or 0)
            entry_b = float(rec.get("avgEntryPrice") or 0)
            exit_b = float(rec.get("avgExitPrice") or 0)
        except (TypeError, ValueError):
            continue
        if entry_db > 0 and entry_b > 0:
            entry_slips.append(
                (entry_b - entry_db) / entry_db * 10_000  # bps
            )
        if exit_db > 0 and exit_b > 0:
            exit_slips.append(
                (exit_b - exit_db) / exit_db * 10_000
            )

    def _stats(xs: List[float]) -> str:
        if not xs:
            return "(no data)"
        mean = sum(xs) / len(xs)
        mn = min(xs)
        mx = max(xs)
        return f"mean={mean:+.2f} bps  min={mn:+.2f}  max={mx:+.2f}  n={len(xs)}"

    print("===== slippage =====")
    print(f"  entry: {_stats(entry_slips)}")
    print(f"  exit : {_stats(exit_slips)}")
    print()

    # ============== 7. Fee drag ==============
    sum_fees = sum(_f(e.get("execFee")) for e in executions)
    sum_pnl = sum(pnls_all)
    print("===== fee drag =====")
    print(f"  fees paid (executions ledger): ${sum_fees:.2f}")
    print(f"  net realised PnL (Bybit)      : ${sum_pnl:.2f}")
    if len(pairs) > 0:
        fee_per_trade = sum_fees / (len(pairs) * 2)  # entry + exit
        print(f"  ~fee per side (avg)           : ${fee_per_trade:.4f}")
    if executions and len(pairs) > 0:
        gross_pnl = sum_pnl + sum_fees  # closedPnl already nets fees
        print(f"  ~gross PnL (pre-fee, derived) : ${gross_pnl:+.2f}")
        if abs(gross_pnl) > 0.01:
            print(f"  fee drag vs gross             : "
                  f"{sum_fees / abs(gross_pnl) * 100:.1f}% of |gross|")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
