#!/usr/bin/env python3
"""Perp-funding drag check for the M22 pairs sleeve (research/Tier-1).

The D2 pairs finding is net-of-*fee* but NOT net-of-*funding*: a market-neutral
perp pair is long one perpetual and short another, and each leg accrues funding
every ~8h. The R-based backtest showed the edge is fee-insensitive (the wide
spread-stop dwarfs per-trade fees); this tool answers the sibling question —
**is the net funding differential large enough to threaten the edge, or is it
negligible like the fees?** — WITHOUT a backtest rewrite, by measuring the actual
funding series.

For a long-spread trade (long A perp / short B perp) the per-interval funding P&L
in return units is ``(−fundingA + fundingB)`` (you pay funding on the long leg,
receive it on the short); a short-spread trade flips the sign. So the funding
*exposure* per interval is bounded by ``|fundingA − fundingB|``. We report:
  * mean and mean-|·| of the net differential ``(fundingA − fundingB)`` per 8h and
    per day (bps), plus each leg's own mean-|·| for context;
  * a **worst-case per-trade drag** = mean-|net| × (mean_hold_hours / 8) — the
    honest upper bound (assumes funding always opposes the position). Compare it
    to the per-trade gross edge to judge materiality.

Research only. If the drag is a meaningful fraction of the edge, the follow-up is
a proper net-of-funding harness pass; if it's sub-percent (as fees were), funding
is not a threat and the R-based result stands.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

_RATE_COLS = ["funding_rate", "fundingrate", "rate", "funding", "funding_rate_8h"]


def _load_funding(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    lc = {c.lower(): c for c in df.columns}
    ts_col = next((lc[c] for c in ("timestamp", "time", "datetime", "date") if c in lc), None)
    rate_col = next((lc[c] for c in _RATE_COLS if c in lc), None)
    if ts_col is None or rate_col is None:
        raise ValueError(f"funding csv needs a timestamp + rate col; got {list(df.columns)}")
    out = df[[ts_col, rate_col]].rename(columns={ts_col: "timestamp", rate_col: "rate"})
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    out["rate"] = pd.to_numeric(out["rate"], errors="coerce")
    return out.dropna().sort_values("timestamp").reset_index(drop=True)


def _bps(x: float) -> float:
    return round(x * 1e4, 4)


def analyze(path_a: str, path_b: str, *, mean_hold_hours: float,
            interval_hours: float = 8.0) -> Dict[str, Any]:
    a = _load_funding(path_a)
    b = _load_funding(path_b)
    m = pd.merge(a.rename(columns={"rate": "ra"}), b.rename(columns={"rate": "rb"}),
                 on="timestamp", how="inner")
    n = len(m)
    if n == 0:
        return {"intervals": 0, "error": "no overlapping funding timestamps"}
    net = (m["ra"] - m["rb"]).to_numpy()
    per_day = 24.0 / interval_hours  # 8h -> 3 intervals/day
    mean_abs_net = float(np.mean(np.abs(net)))
    trade_intervals = mean_hold_hours / interval_hours
    return {
        "intervals": n,
        "window_start": str(m["timestamp"].iloc[0]),
        "window_end": str(m["timestamp"].iloc[-1]),
        "leg_a_mean_abs_bps_8h": _bps(float(np.mean(np.abs(m["ra"].to_numpy())))),
        "leg_b_mean_abs_bps_8h": _bps(float(np.mean(np.abs(m["rb"].to_numpy())))),
        "net_diff_mean_bps_8h": _bps(float(np.mean(net))),
        "net_diff_mean_abs_bps_8h": _bps(mean_abs_net),
        "net_diff_mean_abs_bps_day": _bps(mean_abs_net * per_day),
        "worst_case_drag_bps_per_trade": _bps(mean_abs_net * trade_intervals),
        "mean_hold_hours_assumed": mean_hold_hours,
    }


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Net perp-funding drag for a market-neutral pair.")
    p.add_argument("--funding-a", required=True)
    p.add_argument("--funding-b", required=True)
    p.add_argument("--symbol-a", default="A")
    p.add_argument("--symbol-b", default="B")
    p.add_argument("--mean-hold-hours", type=float, default=5.7,
                   help="observed mean hold (D2: ~5.7h) for the per-trade drag bound")
    p.add_argument("--json", dest="json_out", default=None)
    args = p.parse_args(argv[1:])
    try:
        out = analyze(args.funding_a, args.funding_b, mean_hold_hours=args.mean_hold_hours)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    out["pair"] = f"{args.symbol_a}/{args.symbol_b}"
    print(f"funding-drag — {out['pair']}")
    for k in ("intervals", "leg_a_mean_abs_bps_8h", "leg_b_mean_abs_bps_8h",
              "net_diff_mean_bps_8h", "net_diff_mean_abs_bps_8h",
              "net_diff_mean_abs_bps_day", "worst_case_drag_bps_per_trade"):
        print(f"  {k}={out.get(k)}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
