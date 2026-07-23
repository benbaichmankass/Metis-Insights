"""M28 Phase A3 — the combined construction grader (S2 signal + S3 PnL, one call).

Every construction in the R&D program (`docs/research/M28-signal-RnD-program.md`)
is graded the SAME way: does conviction rank forward return honestly (S2), AND does
the conviction-weighted book make money net of costs out-of-sample (S3). This CLI runs
both on one construction's valuation-snapshot JSONL + candle set and emits a combined
scorecard with the single "is it worth building" verdict — so a construction either
graduates toward a production build (S4) or is recorded null in the ledger and we move
on, with no bespoke per-construction grading logic.

- **S2** — `horizon_ic_scan` run `--non-overlapping` (honest IC t-stat + conviction
  spread across horizons).
- **S3** — `pnl_harness.run_pnl_backtest` at the primary horizon (conviction-weighted
  net-of-cost portfolio → Sharpe / PnL / maxDD / turnover, OOS split).
- **Verdict** — `worth_building` = S2 has an honest monetizable horizon AND S3 pays OOS.

Pure/offline given a snapshots file + candle dir; reuses the same leakage-safe
loaders + `build_replay_entries` the scan uses, so S2 and S3 see identical entries.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import horizon_ic_scan as hic  # noqa: E402
import pnl_harness as ph  # noqa: E402
from thesis_backtest_run import (  # noqa: E402
    derive_rebalance_dates,
    load_close_panels,
    make_price_at,
)
from src.units.strategies.macro_thesis.thesis_replay import build_replay_entries  # noqa: E402
from src.units.strategies.macro_thesis.thesis_tick import load_sleeve_config  # noqa: E402
from src.units.strategies.macro_thesis.valuation_store import read_snapshot_records  # noqa: E402


def grade(records, price_at, *, cfg, rebalance_every: int, horizons: list,
          pnl_horizon: int, fee_frac: float = 0.0, carry_frac_per_day: float = 0.0,
          oos_frac: float = 0.5, n_bins: int = 4, t_flag: float = 2.0) -> dict:
    """Run S2 (non-overlapping horizon-IC) + S3 (PnL harness) on one construction and
    return the combined scorecard. S3 uses its own non-overlapping spacing at
    ``pnl_horizon`` (spacing = max(rebalance_every, pnl_horizon)) so the book's periods
    don't overlap — the same honesty the S2 scan applies."""
    # S2 — honest signal grade across horizons.
    def rebalance_dates_for(h):
        return derive_rebalance_dates(records, max(int(rebalance_every), int(h)))

    rows = hic.scan_horizons(
        records, price_at, cfg=cfg, rebalance_dates=derive_rebalance_dates(records, rebalance_every),
        horizons=horizons, fee_frac=fee_frac, carry_frac_per_day=carry_frac_per_day,
        n_bins=n_bins, rebalance_dates_for=rebalance_dates_for,
    )
    s2 = hic.summarize(rows, t_flag=t_flag)

    # S3 — PnL harness at the primary horizon, non-overlapping.
    pnl_dates = derive_rebalance_dates(records, max(int(rebalance_every), int(pnl_horizon)))
    entries = build_replay_entries(records, price_at, rebalance_dates=pnl_dates, cfg=cfg,
                                   horizon_days=float(pnl_horizon))
    s3 = ph.run_pnl_backtest(entries, fee_frac=fee_frac,
                             carry_frac_per_day=carry_frac_per_day * float(pnl_horizon),
                             oos_frac=oos_frac)

    s2_ok = bool(s2.get("any_honest_monetizable_horizon"))
    s3_ok = bool(s3.get("summary", {}).get("pays_oos"))
    verdict = (
        "worth_building" if (s2_ok and s3_ok)
        else "signal_but_no_pnl" if s2_ok
        else "pnl_but_no_signal" if s3_ok
        else "no_edge"
    )
    return {
        "verdict": verdict,
        "worth_building": s2_ok and s3_ok,
        "s2_signal": {"rows": rows, "summary": s2},
        "s3_pnl": s3,
        "meta": {"rebalance_every": rebalance_every, "horizons": horizons,
                 "pnl_horizon": pnl_horizon, "fee_frac": fee_frac,
                 "carry_frac_per_day": carry_frac_per_day, "oos_frac": oos_frac},
    }


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="M28 combined construction grader (S2 signal + S3 PnL)")
    ap.add_argument("--snapshots", required=True, help="construction's valuation-snapshot JSONL")
    ap.add_argument("--candles-dir", required=True)
    ap.add_argument("--config", default=None, help="config/macro_theses.yaml override")
    ap.add_argument("--rebalance-every", type=int, default=30)
    ap.add_argument("--horizons", default=None, help="CSV of horizon days (default 7,14,30,60,90,180)")
    ap.add_argument("--pnl-horizon", type=int, default=30, help="hold horizon for the S3 portfolio book")
    ap.add_argument("--fee-frac", type=float, default=0.0)
    ap.add_argument("--carry-frac-per-day", type=float, default=0.0)
    ap.add_argument("--oos-frac", type=float, default=0.5)
    ap.add_argument("--n-bins", type=int, default=4)
    ap.add_argument("--t-flag", type=float, default=2.0)
    ap.add_argument("--json", default=None, help="write the combined scorecard here")
    ap.add_argument("--label", default=None, help="construction label recorded in the scorecard")
    args = ap.parse_args(argv)

    horizons = ([int(x) for x in args.horizons.split(",")] if args.horizons else list(hic.DEFAULT_HORIZONS))
    records = read_snapshot_records(path=args.snapshots)
    panels = load_close_panels(args.candles_dir)
    price_at = make_price_at(panels)
    cfg = load_sleeve_config(args.config) if args.config else {
        "min_conviction": 0.4, "universe": [], "express_as": "debit_vertical",
        "account": "alpaca_options_paper",
    }
    card = grade(records, price_at, cfg=cfg, rebalance_every=args.rebalance_every,
                 horizons=horizons, pnl_horizon=args.pnl_horizon, fee_frac=args.fee_frac,
                 carry_frac_per_day=args.carry_frac_per_day, oos_frac=args.oos_frac,
                 n_bins=args.n_bins, t_flag=args.t_flag)
    card["meta"]["label"] = args.label
    card["meta"]["snapshot_records"] = len(records)

    s3s = card["s3_pnl"].get("summary", {})
    cw = card["s3_pnl"].get("conviction_weighted", {}).get("full", {})
    print(f"construction: {args.label or args.snapshots}")
    print(f"  S2 honest-monetizable: {card['s2_signal']['summary'].get('any_honest_monetizable_horizon')}  "
          f"(verdict {card['s2_signal']['summary'].get('verdict')})")
    print(f"  S3 pays_oos: {s3s.get('pays_oos')}  "
          f"conviction total_return={cw.get('total_return')} sharpe={cw.get('sharpe')} "
          f"maxDD={cw.get('max_drawdown')}")
    print(f"  ==> {card['verdict'].upper()}")

    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w") as f:
            json.dump(card, f, indent=2)
            f.write("\n")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
