#!/usr/bin/env python3
"""M28/M29 — information-coefficient-by-horizon scan (observe-only).

The P4 value-thesis gate (`thesis_backtest_run.py`) evaluates conviction→return at
ONE horizon (default 30d). But different signals predict at different horizons —
value/positioning are weeks-to-months, funding/vol are days — so a single-horizon
gate can call a signal "dead" when it simply has the wrong horizon. This scan runs
the SAME point-in-time replay across a **range of horizons** and reports the
information coefficient IC(H) = Spearman(conviction, forward net-return) at each,
so we see WHERE (if anywhere) a signal predicts instead of forcing one window.

It re-uses the P4 machinery wholesale — the same leakage-safe `price_at` reader,
the same `build_replay_entries` (per-horizon) + `run_thesis_backtest` — so it works
for ANY signal whose snapshots are in the valuation-snapshot schema (value today;
the CFTC-COT + crypto-funding sleeves emit the same shape so this scan grades them
too).

**Reading IC(H):** |IC| ~ 0.02-0.05 with a |t| >= 2 is a real-but-modest edge;
IC flat at ~0 across all horizons means the signal doesn't predict at any tested
horizon (the honest "dead signal" verdict, now horizon-qualified).

**Caveat (stated, not hidden):** at horizons LONGER than the rebalance spacing the
forward windows OVERLAP, so their returns are autocorrelated and the IC t-stat is
OPTIMISTIC (effective sample < n). The t here is a rank-correlation rule-of-thumb
(|t| >= 2 ≈ 5%), not a rigorous overlap-corrected test — use it to rank horizons,
not as a p-value. Observe-only: reads logs + CSVs, writes a scorecard. No order path.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from thesis_backtest_run import (  # noqa: E402
    derive_rebalance_dates,
    load_close_panels,
    make_price_at,
)

from src.units.strategies.macro_thesis.thesis_backtest import run_thesis_backtest  # noqa: E402
from src.units.strategies.macro_thesis.thesis_replay import build_replay_entries  # noqa: E402
from src.units.strategies.macro_thesis.thesis_tick import load_sleeve_config  # noqa: E402
from src.units.strategies.macro_thesis.valuation_store import read_snapshot_records  # noqa: E402

DEFAULT_HORIZONS = [7, 14, 30, 60, 90, 180]  # calendar days: ~1wk → ~6mo
DEFAULT_SCORECARD_PATH = os.path.join("comms", "macro", "horizon_ic_scorecard.json")


def ic_t_stat(ic: Optional[float], n: int) -> Optional[float]:
    """Rank-correlation t ≈ ic*sqrt(n-2)/sqrt(1-ic²). None when undefined.
    OPTIMISTIC under overlapping windows (see module docstring)."""
    if ic is None or n is None or n < 3:
        return None
    denom = 1.0 - ic * ic
    if denom <= 0:
        return None
    return ic * math.sqrt(n - 2) / math.sqrt(denom)


def scan_horizons(
    records, price_at, *, cfg, rebalance_dates, horizons,
    fee_frac: float = 0.0, carry_frac_per_day: float = 0.0, n_bins: int = 4,
) -> list:
    """Run the P4 replay at each horizon; return one row per horizon with the IC
    (conviction→net-return Spearman), its rule-of-thumb t, n, mean net return, and
    edge vs the naive all-long baseline."""
    rows = []
    for h in horizons:
        entries = build_replay_entries(
            records, price_at, rebalance_dates=rebalance_dates, cfg=cfg, horizon_days=float(h),
        )
        card = run_thesis_backtest(
            entries, fee_frac=fee_frac, carry_frac_per_day=carry_frac_per_day, n_bins=n_bins,
        )
        ic = card.get("calibration_rank")
        n = card.get("n") or 0
        rows.append({
            "horizon_days": h,
            "n": n,
            "ic": None if ic is None else round(float(ic), 6),
            "ic_t": (lambda t: None if t is None else round(t, 3))(ic_t_stat(ic, n)),
            "win_rate": _r(card.get("win_rate")),
            "mean_net_return": _r(card.get("mean_net_return")),
            "edge_vs_baseline": _r(card.get("edge_vs_baseline")),
        })
    return rows


def _r(v):
    return None if v is None else round(float(v), 6)


def summarize(rows: list, *, t_flag: float = 2.0) -> dict:
    """Pick the best horizon (largest positive edge with a flagged |t|) + verdict."""
    scored = [r for r in rows if r["n"] and r["ic"] is not None]
    predictive = [
        r for r in scored
        if r["ic_t"] is not None and abs(r["ic_t"]) >= t_flag
        and r["edge_vs_baseline"] is not None and r["edge_vs_baseline"] > 0
    ]
    best = max(predictive, key=lambda r: r["edge_vs_baseline"], default=None)
    # Even absent an edge, surface where |IC| is largest (the direction to dig).
    strongest_ic = max(scored, key=lambda r: abs(r["ic"]), default=None)
    return {
        "any_predictive_horizon": bool(predictive),
        "best_horizon_days": best["horizon_days"] if best else None,
        "strongest_ic_horizon_days": strongest_ic["horizon_days"] if strongest_ic else None,
        "strongest_ic": strongest_ic["ic"] if strongest_ic else None,
        "t_flag": t_flag,
        "verdict": (
            "predictive_horizon_found" if predictive
            else "no_predictive_horizon" if scored
            else "no_data"
        ),
    }


def render(rows: list, summary: dict, *, meta: dict) -> str:
    lines = [
        "Horizon-IC scan — conviction → forward net-return (observe-only)",
        "=" * 62,
        f"snapshots={meta['snapshot_records']}  rebalances={meta['rebalances']}  "
        f"fee={meta['fee_frac']}  carry/day={meta['carry_frac_per_day']}",
        "",
        f"{'H(days)':>8} {'n':>6} {'IC':>9} {'IC_t':>8} {'win':>7} {'mean_net':>10} {'edge_vs_base':>13}",
    ]
    for r in rows:
        lines.append(
            f"{r['horizon_days']:>8} {r['n']:>6} "
            f"{_f(r['ic']):>9} {_f(r['ic_t']):>8} {_f(r['win_rate']):>7} "
            f"{_f(r['mean_net_return']):>10} {_f(r['edge_vs_baseline']):>13}"
        )
    lines += [
        "",
        f"verdict: {summary['verdict']}  "
        f"(best_horizon={summary['best_horizon_days']}  "
        f"strongest_IC={_f(summary['strongest_ic'])} @ {summary['strongest_ic_horizon_days']}d)",
        "note: IC_t is OPTIMISTIC at horizons > rebalance spacing (overlapping windows).",
    ]
    return "\n".join(lines)


def _f(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Information-coefficient-by-horizon scan (observe-only)")
    ap.add_argument("--snapshots", default=None, help="valuation-schema snapshots JSONL")
    ap.add_argument("--candles-dir", required=True, help="dir of per-symbol daily-close CSVs")
    ap.add_argument("--config", default=None, help="config/macro_theses.yaml override")
    ap.add_argument("--horizons", default=None, help="CSV of horizon days (default 7,14,30,60,90,180)")
    ap.add_argument("--rebalance-every", type=int, default=30, help="rebalance cadence in days (default 30)")
    ap.add_argument("--fee-frac", type=float, default=0.0)
    ap.add_argument("--carry-frac-per-day", type=float, default=0.0)
    ap.add_argument("--n-bins", type=int, default=4)
    ap.add_argument("--t-flag", type=float, default=2.0, help="|IC_t| threshold to flag a horizon predictive")
    ap.add_argument("--json", default=DEFAULT_SCORECARD_PATH, help=f"scorecard JSON out (default {DEFAULT_SCORECARD_PATH})")
    ap.add_argument("--generated-at", default=None)
    ap.add_argument("--dry-run", action="store_true", help="compute + print; write nothing")
    args = ap.parse_args(argv)

    horizons = (
        [int(x) for x in args.horizons.split(",") if x.strip()]
        if args.horizons else list(DEFAULT_HORIZONS)
    )
    records = read_snapshot_records(path=args.snapshots)
    cfg = load_sleeve_config(args.config)
    panels = load_close_panels(args.candles_dir)
    price_at = make_price_at(panels)
    rebalance_dates = derive_rebalance_dates(records, args.rebalance_every)

    rows = scan_horizons(
        records, price_at, cfg=cfg, rebalance_dates=rebalance_dates, horizons=horizons,
        fee_frac=args.fee_frac, carry_frac_per_day=args.carry_frac_per_day, n_bins=args.n_bins,
    )
    summary = summarize(rows, t_flag=args.t_flag)
    meta = {
        "snapshot_records": len(records),
        "rebalances": len(rebalance_dates),
        "fee_frac": args.fee_frac,
        "carry_frac_per_day": args.carry_frac_per_day,
        "horizons": horizons,
        "symbols_with_candles": sorted(panels.keys()),
        "generated_at": args.generated_at,
    }
    print(render(rows, summary, meta=meta))

    if not args.dry_run:
        out = {"rows": rows, "summary": summary, "meta": meta}
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
