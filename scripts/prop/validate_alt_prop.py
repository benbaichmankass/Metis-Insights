#!/usr/bin/env python3
"""Validation gate for the trend_donchian-on-alts prop candidate (PB-20260616-004).

The research finding — ``trend_donchian`` is strongly +EV on Bybit high-vol
alts — was produced on **Binance SPOT** klines with **no funding** and the EV
model's realised-only optimism. Its own resolution criteria require, before any
Tier-3 live wiring:

  (a) re-validation on **real Bybit linear-perp candles + funding**, and
  (b) a **per-alt walk-forward** to confirm the trend edge is not a
      single-window artifact.

This script is that gate. For one symbol it:

  1. Loads the real Bybit-perp 5m candles (``--data``) and, when supplied, the
     real funding-rate history (``--funding``).
  2. **Full-period:** runs the portfolio engine once for ``--strategy`` to get
     the real per-trade ledger, charges each trade its perp funding
     (``src.prop.funding``), then runs the cost-aware EV + survival Monte-Carlo
     over the ``--risk-pct-grid`` on the FUNDED ledger.
  3. **Walk-forward:** splits the candle window into ``--folds`` sequential
     out-of-sample time spans, re-runs the engine + funded EV per fold, and
     checks the funded 12-month EV stays positive across folds.
  4. Emits ``validate_<symbol>.{md,json}`` and prints a PASS / MARGINAL / FAIL
     **VERDICT** (advisory — the operator owns the Tier-3 go/no-go).

Tier-1 research tooling: NO live order path, NO config writes. Mirrors the CLI
conventions of ``scripts/prop/montecarlo_prop.py``.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scripts.backtest_system as bt  # noqa: E402
from src.prop.funding import apply_funding_to_ledger, funding_summary  # noqa: E402
from src.prop.montecarlo import run_ev_montecarlo, run_montecarlo  # noqa: E402
from src.prop.ruleset import PropRuleset, load_ruleset  # noqa: E402


def _load_funding_rows(path: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    """Load a ``timestamp,funding_rate`` feed (csv/parquet) into normalize-able rows."""
    if not path:
        return None
    import pandas as pd

    df = pd.read_parquet(path) if str(path).endswith(".parquet") else pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    ts_col = cols.get("timestamp") or cols.get("ts") or cols.get("fundingratetimestamp")
    rate_col = cols.get("funding_rate") or cols.get("fundingrate") or cols.get("rate")
    if ts_col is None or rate_col is None:
        raise ValueError(f"funding feed {path} missing timestamp/funding_rate columns")
    out: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        out.append({"timestamp": r[ts_col], "funding_rate": float(r[rate_col])})
    return out


def _engine_ledger(
    base5m: Any, strategy: str, args: argparse.Namespace,
    start: Optional[str], end: Optional[str],
) -> List[Any]:
    """Run the engine once for one strategy/window; return its closed-trade ledger."""
    summary = bt.run_system_backtest(
        base5m, roster=[strategy], start=start, end=end,
        initial_balance=args.initial_balance, risk_pct=args.base_risk_pct,
        daily_loss_pct=args.daily_loss_pct, signal_ttl_bars=args.signal_ttl_bars,
        overrides={}, refresh=args.refresh_signals, clock_tf=args.clock_tf,
        flip_policy=args.flip_policy, reentry_policy="suppress", attach_full=True,
    )
    return summary.get("closed_trades", []) or []


def _ev_over_grid(
    funded_ledger: List[Dict[str, Any]], ruleset: PropRuleset, args: argparse.Namespace,
) -> List[Dict[str, Any]]:
    cells = []
    for rp in args.risk_pct_grid:
        ev = run_ev_montecarlo(
            funded_ledger, ruleset, risk_pct=rp, base_risk_pct=args.base_risk_pct,
            account_size=args.initial_balance, n_paths=args.n_paths,
            block_len=args.block_len, horizons_months=args.horizons, seed=args.seed,
        )
        surv = run_montecarlo(
            funded_ledger, ruleset, risk_pct=rp, base_risk_pct=args.base_risk_pct,
            account_size=args.initial_balance, n_paths=args.n_paths,
            block_len=args.block_len, horizons_months=args.horizons, seed=args.seed,
        )
        cells.append({"risk_pct": rp, "ev": ev, "survival": surv})
    return cells


def _ev_12mo(cell: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return (cell.get("ev", {}).get("horizons", {}) or {}).get("12.0")


def _best_cell(cells: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The risk cell with the highest funded 12-month mean net $."""
    best = None
    best_ev = None
    for c in cells:
        h = _ev_12mo(c)
        if not h:
            continue
        v = h.get("mean_net_usd")
        if v is None:
            continue
        if best_ev is None or v > best_ev:
            best_ev, best = v, c
    return best


def run(args: argparse.Namespace) -> int:
    ruleset = load_ruleset(args.ruleset)
    try:
        base5m = bt._load_candles(args.data)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: candle load failed ({args.data}): {exc}", file=sys.stderr)
        return 1

    # Cost model: Breakout is perpetual-futures-STYLE but charges a flat
    # CFD-style DAILY SWAP (~0.09%/day per public reviews), not Bybit's 8h
    # funding — so the venue-correct gate uses daily_swap. perp_funding (real
    # Bybit series) is kept as a lighter-cost comparison.
    swap_daily = args.swap_rate_daily if args.cost_model == "daily_swap" else None
    funding_rows = _load_funding_rows(args.funding) if swap_daily is None else None
    if swap_daily is not None:
        funding_mode = f"daily_swap_{swap_daily:g}/day (Breakout venue model)"
    elif funding_rows:
        funding_mode = "perp_funding_real_series (Bybit 8h)"
    else:
        funding_mode = f"perp_funding_constant_{args.const_rate_8h:g}/8h"

    ts = base5m["timestamp"]
    data_start, data_end = ts.iloc[0], ts.iloc[-1]

    # ---- full-period funded EV --------------------------------------------
    ledger = _engine_ledger(base5m, args.strategy, args, args.start, args.end)
    funded = apply_funding_to_ledger(
        ledger, funding_series=funding_rows, const_rate_8h=args.const_rate_8h,
        swap_rate_daily=swap_daily)
    fsum = funding_summary(funded)
    full_cells = _ev_over_grid(funded, ruleset, args)
    best = _best_cell(full_cells)

    # ---- per-alt walk-forward ---------------------------------------------
    import pandas as pd

    span_start = pd.Timestamp(data_start)
    span_end = pd.Timestamp(data_end)
    total = (span_end - span_start) / args.folds
    folds: List[Dict[str, Any]] = []

    def _naive(ts: "pd.Timestamp") -> str:
        # _date_filter wraps with pd.Timestamp(start, tz="UTC"), which raises on
        # an already-tz-aware string — so hand it a tz-naive boundary.
        return ts.tz_localize(None).strftime("%Y-%m-%d %H:%M:%S")

    for i in range(args.folds):
        f_start = (span_start + total * i)
        f_end = (span_start + total * (i + 1))
        f_ledger = _engine_ledger(
            base5m, args.strategy, args,
            _naive(f_start), _naive(f_end))
        f_funded = apply_funding_to_ledger(
            f_ledger, funding_series=funding_rows, const_rate_8h=args.const_rate_8h,
            swap_rate_daily=swap_daily)
        # evaluate the walk-forward at the full-period best risk (or first grid pt)
        rp = best["risk_pct"] if best else args.risk_pct_grid[0]
        ev = run_ev_montecarlo(
            f_funded, ruleset, risk_pct=rp, base_risk_pct=args.base_risk_pct,
            account_size=args.initial_balance, n_paths=args.n_paths,
            block_len=args.block_len, horizons_months=[12.0], seed=args.seed,
        )
        h = (ev.get("horizons", {}) or {}).get("12.0") or {}
        folds.append({
            "fold": i + 1,
            "start": f_start.isoformat(), "end": f_end.isoformat(),
            "risk_pct": rp, "n_trades": len(f_ledger),
            "mean_net_usd": h.get("mean_net_usd"),
            "p_profitable": h.get("p_profitable"),
            "roi_on_fees": h.get("roi_on_fees"),
        })

    # ---- verdict -----------------------------------------------------------
    full_net = (_ev_12mo(best) or {}).get("mean_net_usd") if best else None
    full_pprofit = (_ev_12mo(best) or {}).get("p_profitable") if best else None
    fold_nets = [f["mean_net_usd"] for f in folds if f["mean_net_usd"] is not None]
    folds_positive = sum(1 for v in fold_nets if v > 0)
    n_evaluable = len(fold_nets)
    # PASS: full-period funded EV clearly +, AND a robust majority of OOS folds +.
    need_folds = max(1, (n_evaluable * 3 + 3) // 4)  # ceil(0.75 * n)
    full_ok = (full_net is not None and full_net > 0
               and (full_pprofit is None or full_pprofit >= 0.5))
    folds_ok = n_evaluable > 0 and folds_positive >= need_folds
    if full_ok and folds_ok:
        verdict = "PASS"
    elif (full_net is not None and full_net > 0) and folds_positive >= max(1, n_evaluable // 2):
        verdict = "MARGINAL"
    else:
        verdict = "FAIL"

    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "generated_at": generated_at,
        "symbol": args.symbol,
        "strategy": args.strategy,
        "verdict": verdict,
        "funding_mode": funding_mode,
        "funding_summary": fsum,
        "data_window": {"start": str(data_start), "end": str(data_end)},
        "ruleset": ruleset.to_dict(),
        "params": {
            "risk_pct_grid": args.risk_pct_grid, "n_paths": args.n_paths,
            "block_len": args.block_len, "horizons": list(args.horizons),
            "clock_tf": args.clock_tf, "flip_policy": args.flip_policy,
            "base_risk_pct": args.base_risk_pct, "folds": args.folds,
            "const_rate_8h": args.const_rate_8h,
        },
        "full_period": {
            "best_risk_pct": best["risk_pct"] if best else None,
            "best_12mo": _ev_12mo(best) if best else None,
            "cells": full_cells,
            "n_ledger_trades": len(ledger),
        },
        "walk_forward": {
            "folds": folds,
            "folds_positive": folds_positive,
            "n_evaluable": n_evaluable,
            "need_for_pass": need_folds,
        },
    }

    out_dir = Path(args.out_dir) if args.out_dir else (
        _REPO_ROOT / "runtime_logs" / "prop_eval" / f"{date.today().isoformat()}-validate")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"validate_{args.symbol.lower()}.json").write_text(
        json.dumps(payload, indent=2, default=str))
    md = _render_md(payload, args)
    (out_dir / f"validate_{args.symbol.lower()}.md").write_text(md)
    print(md)
    print(f"\nwrote {out_dir}/validate_{args.symbol.lower()}.{{md,json}}", file=sys.stderr)
    print(f"\nVERDICT[{args.symbol}/{args.strategy}]: {verdict}", file=sys.stderr)
    return 0


def _f(v: Any, money: bool = False) -> str:
    if v is None:
        return "—"
    if money:
        return f"${v:,.0f}"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _render_md(p: Dict[str, Any], args: argparse.Namespace) -> str:
    L: List[str] = []
    L.append(f"# Prop validation — `{p['strategy']}` on `{p['symbol']}` (real Bybit perp + funding)")
    L.append("")
    L.append(f"_Generated {p['generated_at']}_")
    L.append("")
    L.append(f"## VERDICT: **{p['verdict']}**")
    L.append("")
    L.append(f"- **Funding:** {p['funding_mode']} — "
             f"total drag ${p['funding_summary'].get('total_funding_cost_usd')}, "
             f"{_f(p['funding_summary'].get('funding_drag_pct_of_gross'))}% of gross "
             f"(pre ${p['funding_summary'].get('pnl_pre_funding_usd')} → "
             f"post ${p['funding_summary'].get('pnl_post_funding_usd')})")
    L.append(f"- **Data:** {p['data_window']['start']} → {p['data_window']['end']}, "
             f"clock {args.clock_tf}, flip {args.flip_policy}, "
             f"{p['full_period']['n_ledger_trades']} ledger trades")
    L.append("")
    L.append(f"### Full-period funded 12-mo EV (best cell @ risk "
             f"{p['full_period'].get('best_risk_pct')})")
    L.append("")
    L.append("| risk | mean net $ | median | p5 | P(net>0) | accts | fees $ | ROI/fees |")
    L.append("|" + "---|" * 8)
    for c in p["full_period"]["cells"]:
        h = (c.get("ev", {}).get("horizons", {}) or {}).get("12.0") or {}
        L.append(
            f"| {c['risk_pct']} | {_f(h.get('mean_net_usd'), money=True)} | "
            f"{_f(h.get('median_net_usd'), money=True)} | {_f(h.get('p5_net_usd'), money=True)} | "
            f"{_f((h.get('p_profitable') or 0) * 100)}% | {_f(h.get('mean_accounts'))} | "
            f"{_f(h.get('mean_fees_usd'), money=True)} | {_f(h.get('roi_on_fees'))} |")
    L.append("")
    L.append(f"### Walk-forward ({p['walk_forward']['n_evaluable']} evaluable folds; "
             f"{p['walk_forward']['folds_positive']} positive, "
             f"need {p['walk_forward']['need_for_pass']} for PASS)")
    L.append("")
    L.append("| fold | window | risk | trades | mean net $ | P(net>0) | ROI/fees |")
    L.append("|" + "---|" * 7)
    for f in p["walk_forward"]["folds"]:
        win = f"{str(f['start'])[:10]}→{str(f['end'])[:10]}"
        L.append(
            f"| {f['fold']} | {win} | {f['risk_pct']} | {f['n_trades']} | "
            f"{_f(f.get('mean_net_usd'), money=True)} | "
            f"{_f((f.get('p_profitable') or 0) * 100)}% | {_f(f.get('roi_on_fees'))} |")
    L.append("")
    L.append("> Realised-only caveat carries over from the EV engine: a per-trade "
             "bootstrap has no intraday open-position swing, so daily-loss/DD "
             "breaches (hence fee churn) are still UNDER-counted; funding is now "
             "charged but true EV remains, if anything, optimistic.")
    L.append("")
    return "\n".join(L)


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Validate a strategy on a real Bybit-perp alt for prop.")
    p.add_argument("--symbol", required=True, help="e.g. SOLUSDT (label only; drives output names)")
    p.add_argument("--data", required=True, help="Bybit-perp 5m candles csv/parquet (_load_candles schema)")
    p.add_argument("--funding", default=None, help="funding-rate csv/parquet (timestamp,funding_rate)")
    p.add_argument("--strategy", default="trend_donchian")
    p.add_argument("--ruleset", default=str(_REPO_ROOT / "config" / "prop_rulesets" / "breakout.yaml"))
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--initial-balance", type=float, default=5000.0)
    p.add_argument("--base-risk-pct", type=float, default=0.5)
    p.add_argument("--risk-pct-grid", default="0.5,1.0,1.5")
    p.add_argument("--daily-loss-pct", type=float, default=3.0)
    p.add_argument("--signal-ttl-bars", type=int, default=1)
    p.add_argument("--clock-tf", default="1h", choices=list(bt._PANDAS_TF.keys()))
    p.add_argument("--flip-policy", default="hold", choices=["reverse", "hold", "flat"])
    p.add_argument("--refresh-signals", action="store_true")
    p.add_argument("--n-paths", type=int, default=3000)
    p.add_argument("--block-len", type=int, default=8)
    p.add_argument("--horizons", default="3,6,12")
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--folds", type=int, default=4)
    p.add_argument("--const-rate-8h", type=float, default=1e-4,
                   help="Fallback constant 8h funding rate when --funding is absent.")
    p.add_argument("--cost-model", default="perp_funding",
                   choices=["perp_funding", "daily_swap"],
                   help="perp_funding=Bybit 8h funding (proxy); daily_swap=Breakout "
                        "flat CFD-style daily swap (the venue-correct gate).")
    p.add_argument("--swap-rate-daily", type=float, default=9e-4,
                   help="Daily swap rate for --cost-model daily_swap "
                        "(default 0.0009 = 0.09%%/day, the Breakout review figure).")
    p.add_argument("--out-dir", default=None)
    args = p.parse_args(argv[1:])
    args.risk_pct_grid = [float(x) for x in str(args.risk_pct_grid).split(",") if x.strip()]
    args.horizons = [float(x) for x in str(args.horizons).split(",") if x.strip()]
    return run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
