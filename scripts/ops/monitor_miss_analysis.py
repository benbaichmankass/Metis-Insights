"""Diagnostic: classify reconciler-filled closes by where they
actually happened — TP hit, SL hit, or elsewhere.

The strategy-performance audit (#1439) showed 53 of 135 closes
(39%) were ``reconciler_filled`` — the monitor missed them and
the orphan reconciler caught the position-flat state. The
operator's question: is this a monitor-detection BUG (we should
have caught these locally), or is it WORKING AS DESIGNED (the
reconciler is the safety net for broker-side TP/SL fires that
happen between our candle ticks)?

This script answers that empirically. For each reconciler-filled
trade, computes the distance from the actual exit price to the
planned TP and SL, then classifies:

  * "TP hit"        — exit price within 10 bps of planned TP
  * "SL hit"        — exit price within 10 bps of planned SL
  * "between TP/SL" — exit price strictly inside the bracket
  * "beyond TP"     — exit price past TP (overshoot — TP slipped)
  * "beyond SL"     — exit price past SL (overshoot — SL slipped)

Interpretation:
  * If most reconciler closes are TP-hit / SL-hit → bot's monitor
    detection is correct, reconciler is the safety net for broker-
    side fires between candles. Working as designed.
  * If many are "between TP/SL" or "elsewhere" → something else is
    closing positions (manual flatten, partial fills, orphan
    adoptions, etc). Real bug or operator action.

Read-only. No DB writes.

Usage:
    python3 scripts/ops/monitor_miss_analysis.py --account bybit_2
"""
from __future__ import annotations

import argparse
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


def _f(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _classify(
    direction: str,
    entry: float, exit_: float, sl: float, tp: float,
    tol: float = 0.001,
) -> Tuple[str, float, float, float]:
    """Return (classification, dist_to_tp_bps, dist_to_sl_bps,
    realized_R) for one trade.

    ``realized_R`` = realized excursion / planned SL distance.
    Positive R = win-side, negative R = loss-side.
    """
    direction = direction.lower()
    if direction == "long":
        sl_dist = entry - sl
        tp_dist = tp - entry
        realized = exit_ - entry
    elif direction == "short":
        sl_dist = sl - entry
        tp_dist = entry - tp
        realized = entry - exit_
    else:
        return "unknown_direction", 0.0, 0.0, 0.0

    if sl_dist <= 0 or tp_dist <= 0:
        return "invalid_bracket", 0.0, 0.0, 0.0

    dist_to_tp_bps = abs(exit_ - tp) / tp * 10_000
    dist_to_sl_bps = abs(exit_ - sl) / sl * 10_000
    realized_R = realized / sl_dist

    # Use 30 bps as "near" — TP/SL can slip a bit on real fills.
    near_tol_bps = 30.0
    if dist_to_tp_bps < near_tol_bps:
        return "TP_hit", dist_to_tp_bps, dist_to_sl_bps, realized_R
    if dist_to_sl_bps < near_tol_bps:
        return "SL_hit", dist_to_tp_bps, dist_to_sl_bps, realized_R

    # Strictly inside (neither TP nor SL hit)
    if direction == "long":
        if exit_ > tp:
            return "beyond_TP", dist_to_tp_bps, dist_to_sl_bps, realized_R
        if exit_ < sl:
            return "beyond_SL", dist_to_tp_bps, dist_to_sl_bps, realized_R
    else:  # short
        if exit_ < tp:
            return "beyond_TP", dist_to_tp_bps, dist_to_sl_bps, realized_R
        if exit_ > sl:
            return "beyond_SL", dist_to_tp_bps, dist_to_sl_bps, realized_R

    return "between_TP_SL", dist_to_tp_bps, dist_to_sl_bps, realized_R


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--db", default=None)
    parser.add_argument(
        "--exit-reason", default="reconciler_filled",
        help="Comma-separated exit_reasons to analyse (default: "
             "reconciler_filled). 'all' analyses everything.",
    )
    args = parser.parse_args()

    from src.utils.paths import trade_journal_db_path
    db_path = args.db or str(trade_journal_db_path())
    if not os.path.exists(db_path):
        print(f"error: db not found at {db_path}", file=sys.stderr)
        return 2

    since_ms = int(
        datetime.now(timezone.utc).timestamp() * 1000
    ) - args.days * 24 * 60 * 60 * 1000
    since_iso = datetime.fromtimestamp(
        since_ms / 1000, tz=timezone.utc
    ).isoformat()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, symbol, direction, entry_price, exit_price,
               stop_loss, take_profit_1, position_size, exit_reason,
               pnl, created_at, notes
        FROM trades
        WHERE account_id = ?
          AND status = 'closed'
          AND COALESCE(is_backtest, 0) = 0
          AND datetime(created_at) >= datetime(?)
        ORDER BY datetime(created_at) ASC, id ASC
        """,
        (args.account, since_iso),
    ).fetchall()
    conn.close()

    target_reasons = set(
        s.strip() for s in args.exit_reason.split(",") if s.strip()
    )
    if "all" in target_reasons:
        target_reasons = None  # type: ignore[assignment]

    classified: List[Dict[str, Any]] = []
    skipped: List[Tuple[int, str]] = []

    for row in rows:
        reason = str(row["exit_reason"] or "<none>")
        if target_reasons and reason not in target_reasons:
            continue
        entry = _f(row["entry_price"])
        exit_ = _f(row["exit_price"])
        sl = _f(row["stop_loss"])
        tp = _f(row["take_profit_1"])
        direction = str(row["direction"] or "").lower()
        pnl = _f(row["pnl"])
        if (entry is None or exit_ is None or sl is None or tp is None
                or entry <= 0 or exit_ <= 0 or sl <= 0 or tp <= 0):
            skipped.append((row["id"], "missing prices"))
            continue
        if direction not in ("long", "short"):
            skipped.append((row["id"], f"bad direction={direction!r}"))
            continue
        klass, tp_bps, sl_bps, R = _classify(
            direction, entry, exit_, sl, tp,
        )
        classified.append({
            "id": row["id"], "direction": direction,
            "entry": entry, "exit": exit_, "sl": sl, "tp": tp,
            "pnl": pnl, "reason": reason,
            "class": klass,
            "dist_to_tp_bps": round(tp_bps, 2),
            "dist_to_sl_bps": round(sl_bps, 2),
            "realized_R": round(R, 3),
        })

    print(f"===== monitor_miss_analysis: account={args.account} =====")
    print(f"  window={args.days}d")
    print(f"  target exit_reasons: "
          f"{sorted(target_reasons) if target_reasons else 'all'}")
    print(f"  rows analysed: {len(classified)}")
    if skipped:
        print(f"  skipped (missing data): {len(skipped)}")
    print()

    # Per-class aggregate
    by_class: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in classified:
        by_class[r["class"]].append(r)

    print("===== classification summary =====")
    print("  class            n     pnl_sum    mean_R   examples")
    for klass in ("TP_hit", "SL_hit", "between_TP_SL",
                  "beyond_TP", "beyond_SL", "unknown_direction",
                  "invalid_bracket"):
        items = by_class.get(klass, [])
        if not items:
            continue
        pnl_sum = sum(r["pnl"] or 0 for r in items)
        mean_R = sum(r["realized_R"] for r in items) / len(items)
        # Two example IDs
        eg = ",".join(str(r["id"]) for r in items[:3])
        print(f"  {klass:<16} {len(items):<4} "
              f"{pnl_sum:+8.4f}  {mean_R:+6.3f}   {eg}")
    print()

    # Breakdown by (direction, class) — is long bias inheriting one
    # specific failure mode?
    by_dir_class: Dict[Tuple[str, str], int] = defaultdict(int)
    for r in classified:
        by_dir_class[(r["direction"], r["class"])] += 1
    if classified:
        print("===== by direction + class =====")
        directions = sorted(set(r["direction"] for r in classified))
        all_classes = sorted(set(r["class"] for r in classified))
        print("  direction  " + "  ".join(f"{c:<10}" for c in all_classes))
        for d in directions:
            counts = [str(by_dir_class.get((d, c), 0)) for c in all_classes]
            print(f"  {d:<10} " + "  ".join(f"{c:<10}" for c in counts))
        print()

    # Distribution: which side did exits cluster on?
    if classified:
        print("===== exit price relative to bracket =====")
        # For each trade, compute (exit - entry) / (tp - entry) as a
        # fraction. 0 = at entry; 1 = at TP; -SL_dist/TP_dist = at SL.
        # Show histogram.
        positions: List[float] = []
        for r in classified:
            entry = r["entry"]
            tp = r["tp"]
            exit_ = r["exit"]
            direction = r["direction"]
            if direction == "long":
                tp_dist = tp - entry
            else:
                tp_dist = entry - tp
            if tp_dist == 0:
                continue
            if direction == "long":
                pos = (exit_ - entry) / tp_dist
            else:
                pos = (entry - exit_) / tp_dist
            positions.append(pos)
        if positions:
            buckets = [
                ("<-2.0  (way past SL)",
                 [p for p in positions if p < -2.0]),
                ("-2.0..-1.0  (past SL)",
                 [p for p in positions if -2.0 <= p < -1.0]),
                ("-1.0..-0.5  (at SL band)",
                 [p for p in positions if -1.0 <= p < -0.5]),
                ("-0.5..0.0  (loss side)",
                 [p for p in positions if -0.5 <= p < 0.0]),
                ("0.0..0.5  (gain side)",
                 [p for p in positions if 0.0 <= p < 0.5]),
                ("0.5..1.0  (near TP)",
                 [p for p in positions if 0.5 <= p < 1.0]),
                (">=1.0  (at/past TP)",
                 [p for p in positions if p >= 1.0]),
            ]
            for label, items in buckets:
                bar = "#" * len(items)
                print(f"  {label:<28} n={len(items):<3} {bar}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
