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


def conviction_spread(card: dict) -> Optional[float]:
    """The monetizable long-short measure of a positive IC: the mean net return of
    the **highest**-conviction populated bin minus the **lowest**-conviction populated
    bin (go long the high-conviction theses, short the low-conviction ones — a
    conviction-sorted, market-neutral spread that cancels the all-long bull-market
    drift the raw ``edge_vs_baseline`` unfairly penalises). ``None`` when fewer than
    two bins are populated."""
    bins = [b for b in (card.get("calibration_bins") or []) if b.get("n") and b.get("mean_net_return") is not None]
    if len(bins) < 2:
        return None
    return float(bins[-1]["mean_net_return"]) - float(bins[0]["mean_net_return"])


def scan_horizons(
    records, price_at, *, cfg, rebalance_dates, horizons,
    fee_frac: float = 0.0, carry_frac_per_day: float = 0.0, n_bins: int = 4,
    rebalance_dates_for=None,
) -> list:
    """Run the P4 replay at each horizon; return one row per horizon with the IC
    (conviction→net-return Spearman), its rule-of-thumb t, n, mean net return, edge
    vs the naive all-long baseline, and the conviction-sorted long-short spread.

    ``rebalance_dates_for`` (optional) is a callable ``horizon_days → [dates]`` — when
    given, each horizon uses its OWN rebalance dates (the non-overlapping mode: spacing
    ≈ the horizon, so forward windows don't overlap and the IC t-stat is honest rather
    than optimistically overlap-inflated). Absent, every horizon reuses the shared
    ``rebalance_dates`` (the legacy, overlap-prone behaviour). Each row records
    ``overlapping`` (whether windows could overlap) + ``n_windows`` for legibility."""
    rows = []
    for h in horizons:
        rb = rebalance_dates_for(h) if rebalance_dates_for is not None else rebalance_dates
        entries = build_replay_entries(
            records, price_at, rebalance_dates=rb, cfg=cfg, horizon_days=float(h),
        )
        card = run_thesis_backtest(
            entries, fee_frac=fee_frac, carry_frac_per_day=carry_frac_per_day, n_bins=n_bins,
        )
        ic = card.get("calibration_rank")
        n = card.get("n") or 0
        rows.append({
            "horizon_days": h,
            "n": n,
            "n_windows": len(rb or []),
            "overlapping": rebalance_dates_for is None,  # honest only when per-horizon (non-overlapping)
            "ic": None if ic is None else round(float(ic), 6),
            "ic_t": (lambda t: None if t is None else round(t, 3))(ic_t_stat(ic, n)),
            "win_rate": _r(card.get("win_rate")),
            "mean_net_return": _r(card.get("mean_net_return")),
            "edge_vs_baseline": _r(card.get("edge_vs_baseline")),
            "conv_spread": _r(conviction_spread(card)),
        })
    return rows


def _r(v):
    return None if v is None else round(float(v), 6)


def summarize(rows: list, *, t_flag: float = 2.0) -> dict:
    """Pick the best horizon + verdict.

    The **monetizable** test (upgraded 2026-07-23): a horizon is predictive when the
    conviction→return IC is positive with a flagged |t|, AND the conviction-sorted
    long-short spread (``conv_spread``) is positive — i.e. high-conviction theses beat
    low-conviction ones, market-neutral, so the all-long bull-market drift that unfairly
    sinks the raw ``edge_vs_baseline`` cancels. When the rows are **non-overlapping**
    (``overlapping=False``) the flagged t is honest; an overlapping row's t is only a
    lead to re-test at matched spacing."""
    scored = [r for r in rows if r["n"] and r["ic"] is not None]
    monetizable = [
        r for r in scored
        if r["ic_t"] is not None and r["ic_t"] >= t_flag       # positive IC, flagged (current orientation is right)
        and r.get("conv_spread") is not None and r["conv_spread"] > 0
    ]
    honest = [r for r in monetizable if not r.get("overlapping", True)]
    best = max(monetizable, key=lambda r: r["conv_spread"], default=None)
    strongest_ic = max(scored, key=lambda r: abs(r["ic"]), default=None)
    strongest_spread = max((r for r in scored if r.get("conv_spread") is not None),
                           key=lambda r: r["conv_spread"], default=None)
    return {
        "any_monetizable_horizon": bool(monetizable),
        "any_honest_monetizable_horizon": bool(honest),   # non-overlapping windows → real
        "best_horizon_days": best["horizon_days"] if best else None,
        "best_conv_spread": best["conv_spread"] if best else None,
        "strongest_ic_horizon_days": strongest_ic["horizon_days"] if strongest_ic else None,
        "strongest_ic": strongest_ic["ic"] if strongest_ic else None,
        "strongest_spread_horizon_days": strongest_spread["horizon_days"] if strongest_spread else None,
        "strongest_spread": strongest_spread["conv_spread"] if strongest_spread else None,
        "t_flag": t_flag,
        "verdict": (
            "monetizable_horizon_found" if honest
            else "monetizable_horizon_overlap_only" if monetizable
            else "no_monetizable_horizon" if scored
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
        f"non_overlapping={not (rows[0]['overlapping'] if rows else True)}",
        "",
        f"{'H(days)':>8} {'n':>6} {'nwin':>6} {'IC':>9} {'IC_t':>8} {'win':>7} "
        f"{'mean_net':>10} {'edge_base':>10} {'conv_spread':>12}",
    ]
    for r in rows:
        lines.append(
            f"{r['horizon_days']:>8} {r['n']:>6} {r.get('n_windows', 0):>6} "
            f"{_f(r['ic']):>9} {_f(r['ic_t']):>8} {_f(r['win_rate']):>7} "
            f"{_f(r['mean_net_return']):>10} {_f(r['edge_vs_baseline']):>10} {_f(r.get('conv_spread')):>12}"
        )
    lines += [
        "",
        f"verdict: {summary['verdict']}  "
        f"(best_horizon={summary['best_horizon_days']}  best_conv_spread={_f(summary.get('best_conv_spread'))}  "
        f"strongest_IC={_f(summary['strongest_ic'])} @ {summary['strongest_ic_horizon_days']}d)",
        "note: conv_spread = high-conviction bin net-return − low-conviction bin (market-neutral, the "
        "monetizable form of a +IC). IC_t is honest only for non-overlapping rows (horizon ≤ rebalance spacing).",
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
    ap.add_argument("--non-overlapping", action="store_true",
                    help="per-horizon rebalance spacing = max(rebalance-every, horizon) so forward windows "
                         "don't overlap → the IC t-stat is honest (not overlap-inflated)")
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

    # Non-overlapping mode: each horizon gets its own rebalance dates spaced ≥ the
    # horizon, so the forward windows don't overlap and the IC t-stat is honest.
    rebalance_dates_for = None
    if args.non_overlapping:
        _cache: dict = {}

        def rebalance_dates_for(h):  # noqa: F811
            spacing = max(int(args.rebalance_every), int(h))
            if spacing not in _cache:
                _cache[spacing] = derive_rebalance_dates(records, spacing)
            return _cache[spacing]

    rows = scan_horizons(
        records, price_at, cfg=cfg, rebalance_dates=rebalance_dates, horizons=horizons,
        fee_frac=args.fee_frac, carry_frac_per_day=args.carry_frac_per_day, n_bins=args.n_bins,
        rebalance_dates_for=rebalance_dates_for,
    )
    summary = summarize(rows, t_flag=args.t_flag)
    meta = {
        "snapshot_records": len(records),
        "rebalances": len(rebalance_dates),
        "non_overlapping": bool(args.non_overlapping),
        "rebalance_every": args.rebalance_every,
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
