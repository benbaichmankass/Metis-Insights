#!/usr/bin/env python3
"""Phase-0 per-(trend, vol) cell table for ict_scalp_5m (PB-20260630-ICTSCALP-DEGRADE).

Inputs
------
--ops PATH       compact order_packages JSON extracted from the trainer's
                 synced trade_journal.db ({'n', 'order_packages': [...]})
--trades PATH    compact trades JSON ({'n', 'trades': [...]})
--backtest PATH  (repeatable) an --emit-trades JSONL from
                 scripts/backtest_ict_scalp.py (rows carry meta.regime /
                 meta.vol_regime / meta.mfe_r / meta.mae_r when run with
                 --stamp-regime [--vol-spec-json ...])
--out PATH       write the full result JSON here (default stdout summary only)

Live-side R is computed from PRICES, not the journal's dollar pnl —
R = sign * (exit - entry) / |entry - sl| per filled leg — because several
paper-account journal pnl values are demonstrably corrupt (e.g. the same
-2970.986 stamped on two different trades) while entry/exit/stop prices
are consistent. Legs with no exit price resolve to None (unmeasured,
excluded from expectancy — never coerced to 0). The per-package outcome
prefers the real-money leg, falling back to the paper leg; the table also
reports the real-only split so real and paper are never silently blended.

The decision-time regime cell comes from order_packages.meta as persisted
at signal time (never backfilled). Rows created before the regime stamping
landed (2026-05-18 -> 2026-06-04) are bucketed 'prestamp' — the honest
name for what the M7 packet showed as 'unknown/unknown'.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional


def leg_r(leg: Dict[str, Any]) -> Optional[float]:
    e, x, sl = leg.get("e"), leg.get("x"), leg.get("sl")
    if e is None or x is None or sl is None:
        return None
    risk = abs(float(e) - float(sl))
    if risk <= 0:
        return None
    sign = 1.0 if leg.get("dir") == "long" else -1.0
    return sign * (float(x) - float(e)) / risk


def cell_of(op: Dict[str, Any]) -> tuple:
    rg = op.get("rg")
    vr = op.get("vr")
    if rg is None and vr is None:
        return ("prestamp", "prestamp")
    return (rg or "unknown", vr or "unknown")


def live_table(ops: List[dict], trades: List[dict]) -> Dict[str, Any]:
    legs_by_op: Dict[str, List[dict]] = defaultdict(list)
    for t in trades:
        if t.get("op"):
            legs_by_op[t["op"]].append(t)
    # Pre-stamp packages predate the order_package_id backlink on trades;
    # match those by (created ts prefix to the second, direction).
    unlinked = [t for t in trades if not t.get("op")]

    def find_legs(op: Dict[str, Any]) -> List[dict]:
        legs = legs_by_op.get(op["id"], [])
        if legs:
            return legs
        key = (op["ts"][:19], op["dir"])
        return [t for t in unlinked
                if (t.get("ts") or "")[:19] == key[0] and t.get("dir") == key[1]]

    cells: Dict[tuple, Dict[str, Any]] = defaultdict(lambda: {
        "n_decisions": 0, "n_filled": 0, "n_resolved": 0,
        "rs": [], "rs_real": [], "wins": 0,
    })
    per_decision = []
    for op in ops:
        c = cell_of(op)
        agg = cells[c]
        agg["n_decisions"] += 1
        legs = find_legs(op)
        # a leg counts as filled if it has qty > 0 and reached the exchange
        filled = [t for t in legs if (t.get("qty") or 0) > 0
                  and t.get("st") not in ("rejected", "exchange_rejected")
                  and t.get("recon") != "superseded"]
        if filled:
            agg["n_filled"] += 1
        real = [t for t in filled if t.get("ac") == "real_money"]
        paper = [t for t in filled if t.get("ac") != "real_money"]
        pick = (real or paper or [None])[0]
        r = leg_r(pick) if pick else None
        rr = leg_r(real[0]) if real else None
        per_decision.append({
            "id": op["id"], "ts": op["ts"], "cell": list(c), "dir": op["dir"],
            "conf": op.get("cf"), "status": op.get("st"),
            "filled": bool(filled), "r_price_based": round(r, 4) if r is not None else None,
            "r_real_only": round(rr, 4) if rr is not None else None,
            "account_of_r": (pick or {}).get("acct"),
        })
        if r is not None:
            agg["n_resolved"] += 1
            agg["rs"].append(r)
            if r > 0:
                agg["wins"] += 1
        if rr is not None:
            agg["rs_real"].append(rr)

    table = []
    for c, agg in sorted(cells.items()):
        rs, rs_real = agg.pop("rs"), agg.pop("rs_real")
        table.append({
            "cell": {"trend": c[0], "vol": c[1]},
            **agg,
            "win_rate": round(agg["wins"] / len(rs), 3) if rs else None,
            "expectancy_r": round(statistics.mean(rs), 4) if rs else None,
            "total_r": round(sum(rs), 4) if rs else None,
            "expectancy_r_real_only": round(statistics.mean(rs_real), 4) if rs_real else None,
            "n_real_resolved": len(rs_real),
        })
    return {"per_cell": table, "per_decision": per_decision}


def backtest_table(paths: List[str]) -> Dict[str, Any]:
    out = {}
    for p in paths:
        rows = [json.loads(line) for line in Path(p).read_text().splitlines() if line.strip()]
        cells: Dict[tuple, Dict[str, Any]] = defaultdict(lambda: {
            "n": 0, "wins": 0, "rs": [], "mfe": [], "mae": [], "by_outcome": defaultdict(int),
        })
        for r in rows:
            m = r.get("meta") or {}
            c = (m.get("regime") or "unknown", m.get("vol_regime") or "unknown")
            agg = cells[c]
            agg["n"] += 1
            rr = float(r["net_r"])
            agg["rs"].append(rr)
            if rr > 0:
                agg["wins"] += 1
            agg["by_outcome"][r.get("outcome") or "?"] += 1
            if m.get("mfe_r") is not None:
                agg["mfe"].append(float(m["mfe_r"]))
            if m.get("mae_r") is not None:
                agg["mae"].append(float(m["mae_r"]))
        table = []
        for c, agg in sorted(cells.items()):
            rs = agg.pop("rs")
            mfe = agg.pop("mfe")
            mae = agg.pop("mae")
            table.append({
                "cell": {"trend": c[0], "vol": c[1]},
                "n": agg["n"], "wins": agg["wins"],
                "win_rate": round(agg["wins"] / agg["n"], 3) if agg["n"] else None,
                "expectancy_r": round(statistics.mean(rs), 4) if rs else None,
                "total_r": round(sum(rs), 4) if rs else None,
                "median_mfe_r": round(statistics.median(mfe), 4) if mfe else None,
                "median_mae_r": round(statistics.median(mae), 4) if mae else None,
                "by_outcome": dict(agg["by_outcome"]),
            })
        out[Path(p).name] = {"n_trades": len(rows), "per_cell": table}
    return out


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", required=True)
    ap.add_argument("--trades", required=True)
    ap.add_argument("--backtest", action="append", default=[])
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv[1:])

    ops = json.loads(Path(args.ops).read_text())["order_packages"]
    trades = json.loads(Path(args.trades).read_text())["trades"]
    result = {"live": live_table(ops, trades)}
    if args.backtest:
        result["backtest"] = backtest_table(args.backtest)
    payload = json.dumps(result, indent=1)
    if args.out:
        Path(args.out).write_text(payload)
    # console summary
    print("LIVE per-cell (price-based R, real-preferred):")
    for row in result["live"]["per_cell"]:
        c = row["cell"]
        print(f"  {c['trend']:>12}/{c['vol']:<10} dec={row['n_decisions']:>2} fill={row['n_filled']:>2} "
              f"res={row['n_resolved']:>2} WR={row['win_rate']} expR={row['expectancy_r']} totR={row['total_r']} "
              f"(real-only n={row['n_real_resolved']} expR={row['expectancy_r_real_only']})")
    for name, bt in (result.get("backtest") or {}).items():
        print(f"BACKTEST {name} (n={bt['n_trades']}):")
        for row in bt["per_cell"]:
            c = row["cell"]
            print(f"  {c['trend']:>12}/{c['vol']:<10} n={row['n']:>4} WR={row['win_rate']} "
                  f"expR={row['expectancy_r']} totR={row['total_r']} "
                  f"medMFE={row['median_mfe_r']} medMAE={row['median_mae_r']} {row['by_outcome']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
