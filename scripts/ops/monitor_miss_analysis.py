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
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# --- Exit-price provenance -------------------------------------------------
#
# This script classifies a close by comparing exit_price against the bracket.
# That is only meaningful when exit_price is the ACTUAL close fill. It is not
# always: order_monitor._sweep_local_pnl_for_unpriced substitutes
# last_mark_price() -- the market 6+h after the close -- for any row whose real
# exit fill was never recovered. On a demo account that is nearly every row,
# because clients.account_closed_pnl_for_trade returns None for demo (#4503).
#
# Live receipt (2026-07-30, bybit_1 7d): 38/39 rows were mark-substituted and
# this script reported beyond_SL mean_R = -3.94 / beyond_TP = +6.31 -- values
# impossible for a bracket exit. The real-money control on the identical code
# path returned SL_hit mean_R = -1.008. The instrument was fine; the INPUT was.
#
# The vocabulary lives in ONE place -- src.runtime.provenance. A local copy
# here is exactly how the four bespoke exclude_* predicates came about.
from src.runtime.provenance import (  # noqa: E402
    FABRICATED, MEASURED, UNVERIFIED, classify_row, coverage,
)

# Bybit execType values that mean THE VENUE closed the position, not the bot:
# a liquidation / demo margin call, and an auto-deleverage. Recorded on
# notes.close_exec_type by the reconciler.
_FORCED_CLOSE_EXEC_TYPES = frozenset({"BustTrade", "AdlTrade"})


def _notes_of(row: Any) -> Dict[str, Any]:
    """Decode a trade row's ``notes`` JSON. ``{}`` on anything unreadable."""
    try:
        raw = row["notes"]
    except (KeyError, IndexError, TypeError):
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _f(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _classify(
    direction: str,
    entry: float, exit_: float, sl: float, tp: float,
    tol: float = 0.001,  # inert: tol — classification below is by exact comparison with no tolerance band; the sole caller does not pass it, so this default has never been exercised
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
    parser.add_argument(
        "--include-fabricated", action="store_true",
        help="Also classify rows whose exit_price is a substituted mark price "
             "(notes.exit_price_source='local_markprice'). OFF by default: "
             "their exit_price is the market 6+h after the close, so their "
             "bracket classification and realized_R are meaningless. Use only "
             "to inspect the contamination itself, never to draw an exit "
             "conclusion.",
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
    prov_counts: Dict[str, int] = defaultdict(int)
    prov_excluded: Dict[str, int] = defaultdict(int)
    # A venue FORCE-CLOSE is not a strategy exit. Bybit reports the close's
    # execType on notes.close_exec_type: `BustTrade` = liquidation / demo margin
    # call, `AdlTrade` = auto-deleverage. Measuring either against the strategy's
    # own SL/TP bracket is a category error — the strategy never chose that exit,
    # so a liquidation lands arbitrarily far past the stop and shows up as an
    # impossible `beyond_SL` outlier. Segregated and reported, never silently
    # blended into the exit-quality verdict.
    forced_close_counts: Dict[str, int] = defaultdict(int)
    # Provenance of the exit REASON label itself (sl/tp/…): `price_vs_pkg_bracket`
    # = derived from the recovered price, `unresolved` = never determined. An
    # `unresolved` label is a placeholder, NOT a finding — grouping by it as if
    # it were evidence is the same write-only trap this whole module exists for.
    reason_src_counts: Dict[str, int] = defaultdict(int)
    in_window = 0

    for row in rows:
        reason = str(row["exit_reason"] or "<none>")
        if target_reasons and reason not in target_reasons:
            continue
        in_window += 1
        bucket, raw_src = classify_row(row, "exit_price_source")
        prov_counts[bucket] += 1
        # Provenance gate. A fabricated exit_price cannot be classified against
        # a bracket; an unverified one has no evidence that it can. Excluded
        # loudly below rather than silently dropped.
        if bucket == FABRICATED and not args.include_fabricated:
            prov_excluded[raw_src] += 1
            continue

        # Exit-REASON provenance ledger (reported below; does not gate).
        reason_bucket, reason_raw = classify_row(row, "exit_reason_source")
        reason_src_counts[f"{reason_raw} ({reason_bucket})"] += 1

        # Venue force-close gate. Read the close's execType and refuse to
        # classify a liquidation / auto-deleverage against the strategy bracket.
        if _notes_of(row).get("close_exec_type") in _FORCED_CLOSE_EXEC_TYPES:
            forced_close_counts[str(_notes_of(row)["close_exec_type"])] += 1
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
            "provenance": bucket, "exit_price_source": raw_src,
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

    # --- Provenance ledger: the honest denominator. Printed ALWAYS, even when
    # nothing was excluded, so a clean run is distinguishable from a run that
    # measured nothing (BL-20260730-MONITOR-MISS-ANALYSIS-VACUOUS-ON-DEMO).
    n_fab = prov_counts.get(FABRICATED, 0)
    n_unv = prov_counts.get(UNVERIFIED, 0)
    n_mea = prov_counts.get(MEASURED, 0)
    print()
    print("===== exit-price provenance (denominator) =====")
    print(f"  in-window rows matching exit_reason: {in_window}")
    print(f"    measured   : {n_mea}")
    print(f"    fabricated : {n_fab}"
          + ("  <-- EXCLUDED from classification" if prov_excluded else
             ("  <-- INCLUDED via --include-fabricated" if n_fab else "")))
    print(f"    unverified : {n_unv}  (no exit_price_source recorded)")
    _cov = coverage({MEASURED: n_mea, "total": in_window})
    print(f"    coverage   : {'n/a' if _cov is None else f'{_cov:.1%}'}"
          f"  (measured share -- the PnL analogue of /performance rCoverage)")
    for src, cnt in sorted(prov_excluded.items(), key=lambda kv: -kv[1]):
        print(f"      excluded {cnt} row(s) with exit_price_source={src!r}")

    if forced_close_counts:
        total_forced = sum(forced_close_counts.values())
        print()
        print("===== venue force-closes (EXCLUDED — not strategy exits) =====")
        for et, cnt in sorted(forced_close_counts.items(), key=lambda kv: -kv[1]):
            label = ("liquidation / margin call" if et == "BustTrade"
                     else "auto-deleverage" if et == "AdlTrade" else et)
            print(f"    {et:<12} {cnt:>4}   ({label})")
        print(f"  {total_forced} row(s) closed BY THE VENUE. The strategy never "
              f"chose these exits, so measuring them against its own SL/TP "
              f"bracket is a category error — they would surface as impossible "
              f"'beyond_SL' outliers.")

    if reason_src_counts:
        print()
        print("===== exit-REASON provenance =====")
        for src, cnt in sorted(reason_src_counts.items(), key=lambda kv: -kv[1]):
            print(f"    {src:<40} {cnt:>4}")
        unresolved = sum(c for k, c in reason_src_counts.items()
                         if k.startswith("unresolved"))
        if unresolved:
            print(f"  {unresolved} row(s) carry an UNRESOLVED exit_reason — the "
                  f"label is a placeholder, not a determination. Do not read "
                  f"those as an sl/tp finding.")
    if in_window and n_mea == 0:
        print()
        print("  *** WARNING: ZERO measured rows. Every classification below "
              "(if any) rests on an exit price that was never confirmed to be "
              "the actual close fill. This result is VACUOUS, not clean. ***")
    elif in_window and (n_fab + n_unv) > n_mea:
        print()
        print(f"  *** WARNING: most in-window rows ({n_fab + n_unv} of "
              f"{in_window}) lack a confirmed exit fill. Treat the "
              f"distribution below as covering only the {n_mea} measured "
              f"row(s), NOT the account. ***")
    if args.include_fabricated and n_fab:
        print()
        print("  *** --include-fabricated is ON: mark-substituted rows are in "
              "the numbers below. Their realized_R is a 6h-forward price "
              "excursion, NOT an exit. Do not draw an exit conclusion. ***")
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
