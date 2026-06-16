#!/usr/bin/env python3
"""Monte-Carlo prop survival+speed sweep — combos × risk_pct.

For each combo, run the portfolio engine ONCE (cached signals) at a base
``risk_pct`` to obtain the real timestamped per-trade ledger
(``run_system_backtest(attach_full=True)`` → ``closed_trades``), then run the
block-bootstrap Monte-Carlo (``src/prop/montecarlo.run_montecarlo``) across the
``--risk-pct-grid`` reusing that one ledger via the sizing-independent
R-rescaling (so we pay the engine cost once per combo, not once per cell).

Reframes the prop question around SPEED-TO-TARGET + PROBABILISTIC SURVIVAL:
per (combo, risk_pct) → P(pass), median/p5/p95 days-to-pass, P(survive
3/6/12 months), P(breach) split by cause, mean/median end return.

Output: runtime_logs/prop_eval/<UTC-date>/montecarlo.{json,md}.

Tier-1 research tooling — NO live order path, NO trading. Mirrors the CLI
conventions of scripts/prop/evaluate_prop.py.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scripts.backtest_system as bt  # noqa: E402
from src.prop.montecarlo import run_ev_montecarlo, run_montecarlo  # noqa: E402
from src.prop.ruleset import PropRuleset, load_ruleset  # noqa: E402

# Combos to evaluate (design §6 roster). The pair the single-path eval flagged
# as the best (squeeze+fvg) plus its two legs and a couple of others, so the
# matrix shows both the standout and the contrast. "all" enumerates every
# non-empty subset of the default roster.
DEFAULT_COMBOS = [
    "squeeze_breakout_4h",
    "fvg_range_15m",
    "squeeze_breakout_4h,fvg_range_15m",
    "fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m",
    "trend_donchian",
    "trend_donchian,squeeze_breakout_4h,fvg_range_15m",
    # ict_scalp_5m HIGH-FREQUENCY member (added 2026-06-16) — the speed lever
    # the 4-strategy roster lacked. Both the leg alone and paired with the
    # clean low-DD squeeze+fvg combo, to see whether more signal frequency
    # buys a "fast" (median ≤60-day) pass at ≥95% 6mo survival.
    "ict_scalp_5m",
    "ict_scalp_5m,squeeze_breakout_4h,fvg_range_15m",
    "ict_scalp_5m,squeeze_breakout_4h",
    "ict_scalp_5m,fvg_range_15m",
]
DEFAULT_RISK_GRID = [0.3, 0.5, 0.6, 0.75, 1.0]
DEFAULT_ROSTER_MEMBERS = [
    "trend_donchian", "fade_breakout_4h", "squeeze_breakout_4h", "fvg_range_15m",
    "ict_scalp_5m", "turtle_soup",
]


def _parse_combos(spec: str) -> List[List[str]]:
    spec = (spec or "").strip()
    if spec.lower() == "all":
        import itertools
        out: List[List[str]] = []
        for r in range(1, len(DEFAULT_ROSTER_MEMBERS) + 1):
            for c in itertools.combinations(DEFAULT_ROSTER_MEMBERS, r):
                out.append(list(c))
        return out
    groups = spec.split(";") if spec else DEFAULT_COMBOS
    rosters: List[List[str]] = []
    for group in groups:
        names = [n.strip() for n in group.split(",") if n.strip() in bt.ROSTER]
        if names:
            rosters.append(names)
    return rosters


def _run_one_combo(
    base5m: Any,
    roster: List[str],
    args: argparse.Namespace,
    ruleset: PropRuleset,
) -> Optional[Dict[str, Any]]:
    """Engine run (once) + Monte-Carlo across the risk grid for one combo."""
    roster_str = ",".join(roster)
    print(f"[mc] engine run: {roster_str} (base risk {args.base_risk_pct})", file=sys.stderr)
    try:
        summary = bt.run_system_backtest(
            base5m, roster=roster, start=args.start, end=args.end,
            initial_balance=args.initial_balance, risk_pct=args.base_risk_pct,
            daily_loss_pct=args.daily_loss_pct, signal_ttl_bars=args.signal_ttl_bars,
            overrides={}, refresh=args.refresh_signals, clock_tf=args.clock_tf,
            flip_policy=args.flip_policy, reentry_policy="suppress",
            attach_full=True,
        )
    except Exception as exc:  # noqa: BLE001 — one bad combo must not abort the sweep
        print(f"[mc] combo {roster_str} engine failed: {exc}", file=sys.stderr)
        return None

    ledger = summary.get("closed_trades", []) or []
    cells: List[Dict[str, Any]] = []
    for rp in args.risk_pct_grid:
        print(f"[mc]   risk_pct={rp} ({len(ledger)} ledger trades, {args.n_paths} paths)",
              file=sys.stderr)
        agg = run_montecarlo(
            ledger, ruleset,
            risk_pct=rp, base_risk_pct=args.base_risk_pct,
            account_size=args.initial_balance,
            n_paths=args.n_paths, block_len=args.block_len,
            horizons_months=args.horizons, seed=args.seed,
        )
        cells.append(agg)

    ev_cells: List[Dict[str, Any]] = []
    if getattr(args, "cost_aware", False):
        for rp in args.risk_pct_grid:
            print(f"[mc]   EV risk_pct={rp} (cost-aware, {len(ledger)} ledger trades)",
                  file=sys.stderr)
            ev_cells.append(run_ev_montecarlo(
                ledger, ruleset,
                risk_pct=rp, base_risk_pct=args.base_risk_pct,
                account_size=args.initial_balance,
                n_paths=args.n_paths, block_len=args.block_len,
                horizons_months=args.horizons, seed=args.seed,
            ))

    return {
        "combo": roster_str,
        "n_ledger_trades": len(ledger),
        "engine_metrics": {
            "net_pnl": summary.get("net_pnl"),
            "return_pct": summary.get("return_pct"),
            "max_drawdown_pct": summary.get("max_drawdown_pct"),
            "total_trades": summary.get("total_trades"),
            "win_rate_pct": summary.get("win_rate_pct"),
        },
        "cells": cells,
        "ev_cells": ev_cells,
    }


def _fmt(v: Any, suffix: str = "") -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:g}{suffix}"
    return f"{v}{suffix}"


def _render_markdown(
    results: List[Dict[str, Any]],
    *,
    ruleset: PropRuleset,
    args: argparse.Namespace,
    data_window: Dict[str, Any],
    generated_at: str,
) -> str:
    L: List[str] = []
    L.append("# Prop-firm Monte-Carlo — survival + speed sweep")
    L.append("")
    L.append(f"_Generated {generated_at}_")
    L.append("")
    if ruleset.unconfirmed:
        L.append("> **⚠️ UNCONFIRMED RULESET** — the numbers below are placeholders "
                 "until the operator verifies the firm's terms.")
        L.append("")
    L.append(f"**Ruleset:** `{ruleset.ruleset}` / `{ruleset.plan}` — "
             f"account ${ruleset.account_size_usd:,.0f}, target "
             f"{(ruleset.evaluation.profit_target_pct or 0)*100:.0f}%, daily-loss "
             f"{(ruleset.limits.daily_loss_pct or 0)*100:.0f}%, "
             f"{ruleset.limits.drawdown_type} DD "
             f"{(ruleset.limits.max_drawdown_pct or 0)*100:.0f}%.")
    L.append("")
    L.append(f"**Method:** block-bootstrap ({args.n_paths} paths, block_len "
             f"{args.block_len}, seed {args.seed}) of each combo's real "
             f"per-trade ledger, walked as a fresh ${ruleset.account_size_usd:,.0f} "
             f"account compounded at each `risk_pct`. Engine base risk_pct "
             f"{args.base_risk_pct}; ledger rescaled to each cell's risk via "
             f"sizing-independent R-multiples.")
    L.append("")
    L.append(f"**Data:** {data_window.get('data_start')} → {data_window.get('data_end')} "
             f"(clock_tf {args.clock_tf}, flip_policy {args.flip_policy}, "
             f"reentry suppress).")
    L.append("")
    L.append("> **Honesty caveat (daily-loss is OPTIMISTIC):** a per-trade "
             "bootstrap has no intraday open-position equity swing, so the "
             "daily-loss (and static-DD) checks see only REALISED closed-trade "
             "P&L per synthetic day. Breakout's real daily-loss fires on "
             "intraday equity incl. open positions — so P(breach by daily_loss) "
             "here UNDER-counts. Backtest ≠ funded reality (slippage/fills/funding).")
    L.append("")

    hcols = " | ".join(f"P(surv {int(m)}mo)" for m in args.horizons)
    header = (f"| combo | risk | P(pass) | days→pass (med / p5–p95) | "
              f"P(breach) | by cause | {hcols} | end ret (med) |")
    sep = "|" + "---|" * (8 + len(args.horizons))
    L.append(header)
    L.append(sep)
    for res in results:
        for cell in res["cells"]:
            d = cell["days_to_pass"]
            days = (f"{_fmt(d['median'])} / {_fmt(d['p5'])}–{_fmt(d['p95'])}"
                    if d.get("median") is not None else "—")
            cause = ", ".join(f"{k.split('_')[0]} {v*100:.0f}%"
                              for k, v in cell.get("breach_by_cause", {}).items()) or "—"
            surv = " | ".join(_fmt((cell["survival"].get(str(float(m))) or 0)*100, "%")
                              for m in args.horizons)
            er = cell["end_return"].get("median")
            L.append(
                f"| {res['combo']} | {cell['risk_pct']} | "
                f"{cell['p_pass']*100:.0f}% | {days} | "
                f"{cell['p_breach']*100:.0f}% | {cause} | {surv} | "
                f"{(er*100):.1f}% |" if er is not None else
                f"| {res['combo']} | {cell['risk_pct']} | "
                f"{cell['p_pass']*100:.0f}% | {days} | "
                f"{cell['p_breach']*100:.0f}% | {cause} | {surv} | — |"
            )
    L.append("")
    return "\n".join(L)


def _render_ev_markdown(
    results: List[Dict[str, Any]],
    *,
    ruleset: PropRuleset,
    args: argparse.Namespace,
    data_window: Dict[str, Any],
    generated_at: str,
) -> str:
    econ = ruleset.economics
    L: List[str] = []
    L.append("# Prop-firm Monte-Carlo — COST-AWARE EV sweep")
    L.append("")
    L.append(f"_Generated {generated_at}_")
    L.append("")
    L.append("> **Objective: expected $ netted per horizon, NET of fees, re-buying "
             "a fresh account on each breach.** This credits a strategy that burns "
             "an account fast but banks more than its fee in payouts first. Ranks by "
             "EV ($), not by survival.")
    L.append("")
    L.append(f"**Economics:** account fee ${econ.account_fee_usd:.0f}, re-buy "
             f"${econ.rebuy_fee_usd:.0f}, profit split {ruleset.profit_split*100:.0f}%, "
             f"withdrawal = BANK-ASAP (all equity above start + ${econ.withdrawal_policy.buffer_usd:.0f} "
             f"buffer; first payout day {econ.payout.first_payout_after_days:g}, then every "
             f"{econ.payout.payout_frequency_days:g}d, ${econ.payout.min_withdrawal_usd:.0f} min).")
    L.append("")
    L.append(f"**Data:** {data_window.get('data_start')} → {data_window.get('data_end')} "
             f"(clock_tf {args.clock_tf}, flip_policy {args.flip_policy}). "
             f"{args.n_paths} paths, block_len {args.block_len}, seed {args.seed}.")
    L.append("")
    L.append("> Same realised-only caveat as the survival sheet: a per-trade "
             "bootstrap has no intraday equity swing, so breaches (and thus fee "
             "churn) are UNDER-counted → EV here is, if anything, optimistic.")
    L.append("")
    for m in args.horizons:
        L.append(f"## {int(m)}-month horizon")
        L.append("")
        L.append("| combo | risk | mean net $ | median | p5 | p95 | P(net>0) | "
                 "accts | fees $ | ROI/fees |")
        L.append("|" + "---|" * 10)
        rows = []
        for res in results:
            for ev in res.get("ev_cells", []):
                h = ev.get("horizons", {}).get(str(float(m)))
                if not h:
                    continue
                rows.append((h["mean_net_usd"], res["combo"], ev["risk_pct"], h))
        for mean_net, combo, rp, h in sorted(rows, key=lambda r: r[0], reverse=True):
            roi = _fmt(h["roi_on_fees"]) if h["roi_on_fees"] is not None else "—"
            L.append(
                f"| {combo} | {rp} | ${h['mean_net_usd']:,.0f} | "
                f"${_fmt(h['median_net_usd'])} | ${_fmt(h['p5_net_usd'])} | "
                f"${_fmt(h['p95_net_usd'])} | {h['p_profitable']*100:.0f}% | "
                f"{_fmt(h['mean_accounts'])} | ${h['mean_fees_usd']:,.0f} | {roi} |"
            )
        L.append("")
    return "\n".join(L)


def run(args: argparse.Namespace) -> int:
    ruleset = load_ruleset(args.ruleset)
    rosters = _parse_combos(args.combos)
    if not rosters:
        print("ERROR: no valid combos resolved", file=sys.stderr)
        return 2

    try:
        base5m = bt._load_candles(args.data)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: candle load failed ({args.data}): {exc}", file=sys.stderr)
        return 1

    results: List[Dict[str, Any]] = []
    data_window: Dict[str, Any] = {}
    for roster in rosters:
        res = _run_one_combo(base5m, roster, args, ruleset)
        if res is None:
            continue
        results.append(res)

    if not results:
        print("ERROR: no combo produced a result", file=sys.stderr)
        return 1

    # data window from one quick engine summary field (reuse the last run's via a
    # tiny re-read is wasteful; instead pull from a no-op summary we already have)
    # — capture it on the first successful engine run instead:
    # (run_system_backtest already returned data_start/end inside _run_one_combo's
    #  summary; re-derive here from the base candle frame for the header.)
    try:
        ts = base5m["timestamp"]
        data_window = {"data_start": str(ts.iloc[0]), "data_end": str(ts.iloc[-1])}
    except Exception:  # noqa: BLE001
        data_window = {}

    generated_at = datetime.now(timezone.utc).isoformat()
    out_dir = Path(args.out_dir) if args.out_dir else (
        _REPO_ROOT / "runtime_logs" / "prop_eval" / date.today().isoformat()
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    import json
    payload = {
        "generated_at": generated_at,
        "ruleset": ruleset.to_dict(),
        "params": {
            "n_paths": args.n_paths, "block_len": args.block_len, "seed": args.seed,
            "base_risk_pct": args.base_risk_pct, "risk_pct_grid": args.risk_pct_grid,
            "horizons_months": list(args.horizons), "clock_tf": args.clock_tf,
            "flip_policy": args.flip_policy, "data": args.data,
        },
        "data_window": data_window,
        "results": results,
    }
    (out_dir / "montecarlo.json").write_text(json.dumps(payload, indent=2, default=str))
    md = _render_markdown(results, ruleset=ruleset, args=args,
                          data_window=data_window, generated_at=generated_at)
    (out_dir / "montecarlo.md").write_text(md)

    print(md)
    print(f"\nwrote {out_dir / 'montecarlo.md'} + montecarlo.json", file=sys.stderr)

    if getattr(args, "cost_aware", False):
        ev_md = _render_ev_markdown(results, ruleset=ruleset, args=args,
                                    data_window=data_window, generated_at=generated_at)
        (out_dir / "ev.md").write_text(ev_md)
        (out_dir / "ev.json").write_text(json.dumps(
            {"generated_at": generated_at, "ruleset": ruleset.to_dict(),
             "params": payload["params"], "data_window": data_window,
             "ev": [{"combo": r["combo"], "ev_cells": r.get("ev_cells", [])} for r in results]},
            indent=2, default=str))
        print(ev_md)
        print(f"\nwrote {out_dir / 'ev.md'} + ev.json", file=sys.stderr)
    return 0


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Monte-Carlo prop survival+speed sweep.")
    p.add_argument("--ruleset", default=str(_REPO_ROOT / "config" / "prop_rulesets" / "breakout.yaml"))
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH",
                   "/home/user/ict-trader-data/btc_5m.parquet"))
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--combos", default=";".join(DEFAULT_COMBOS),
                   help="'all' or a ';'-separated list of csv combos.")
    p.add_argument("--initial-balance", type=float, default=5000.0,
                   help="Account size (Breakout 1-Step Classic = $5000).")
    p.add_argument("--base-risk-pct", type=float, default=0.5,
                   help="risk_pct the engine ledger is generated at (rescaled per cell).")
    p.add_argument("--risk-pct-grid", default=",".join(str(x) for x in DEFAULT_RISK_GRID),
                   help="Comma list of risk_pct values to Monte-Carlo.")
    p.add_argument("--daily-loss-pct", type=float, default=3.0,
                   help="In-sim daily-loss halt %% for the engine run (not the MC check).")
    p.add_argument("--signal-ttl-bars", type=int, default=1)
    p.add_argument("--clock-tf", default="1h", choices=list(bt._PANDAS_TF.keys()))
    p.add_argument("--flip-policy", default="hold", choices=["reverse", "hold", "flat"])
    p.add_argument("--refresh-signals", action="store_true")
    p.add_argument("--n-paths", type=int, default=5000)
    p.add_argument("--block-len", type=int, default=8)
    p.add_argument("--horizons", default="3,6,12",
                   help="Comma list of survival horizons in months.")
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--cost-aware", action="store_true",
                   help="Also run the cost-aware EV sweep (expected $ net of fees, "
                        "re-buying on breach; writes ev.{md,json}).")
    args = p.parse_args(argv[1:])

    args.risk_pct_grid = [float(x) for x in str(args.risk_pct_grid).split(",") if x.strip()]
    args.horizons = [float(x) for x in str(args.horizons).split(",") if x.strip()]
    return run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
