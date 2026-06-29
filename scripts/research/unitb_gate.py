#!/usr/bin/env python3
"""Unit-B 1.5% sizing backtest GATE driver (research, Tier-1, READ-ONLY).

Answers the §5 Arm-1 binding question for PR #5016:
  At simulated per-trade risk {0.30, 0.60, 1.5}%, does each affected account's
  strategy roster keep maxDD% inside its caps (bybit_2 5% intraday/daily;
  breakout_1 6% static / 3% daily), and does breakout_1 still clear its killers?

Method (matches the canonical compat matrix + montecarlo module):
  1. Build ONE sizing-independent R-ledger per (account, strategy) via the
     SAME engine path account_compat_matrix.py uses (scripts.backtest_system
     .run_system_backtest, attach_full=True) on the trainer's multi-year feed.
  2. Block-bootstrap that ledger (src.prop.montecarlo) and walk N synthetic
     compounding accounts at EACH simulated risk_pct, against the account's
     OWN ruleset. Report, per (account, strategy, risk):
       net total R, net win rate, trade count, expectancy (R), fee bps,
       date window  -- ledger-level (sizing-independent), AND
       maxDD% distribution {mean,p95,max}, intraday-DD {mean,p95,max},
       p_breach, survival(12mo) -- the binding cap check at that risk.
  Also runs the COMBINED roster per account (all its strategies in one engine
  run) so the concurrent-DD interaction is captured, not just per-strategy.
  --regime-router on exercises the REAL hard gate (aggregate_intents) so the
  gated edge can be compared against the ungated baseline.

Pure/deterministic given the seed. No live path, no network, no order.
"""
from __future__ import annotations
import json, sys, argparse
from pathlib import Path
from statistics import median

REPO = Path("/home/ubuntu/ict-trading-bot")
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import scripts.backtest_system as bt  # noqa: E402
from src.prop.account_rulesets import all_account_units  # noqa: E402
from src.prop.montecarlo import ledger_to_r_sequence, _bootstrap_indices, _SECONDS_PER_DAY, _DAYS_PER_MONTH  # noqa: E402


def walk_with_maxdd(r_seq, idx, *, account_size, risk_pct, daily_loss_pct, static_dd_pct, horizon_days):
    risk_frac = risk_pct / 100.0
    balance = float(account_size)
    peak = balance
    maxdd = 0.0
    day_high = balance
    intraday_maxdd = 0.0
    static_floor = account_size * (1.0 - static_dd_pct) if static_dd_pct is not None else None
    elapsed_days = 0.0
    cur_day = 0
    day_start = balance
    breached = False
    cause = None
    breach_day = None
    survived = True
    n = len(r_seq)
    for k, ix in enumerate(idx):
        t = r_seq[int(ix) % n] if n else None
        if t is None:
            break
        elapsed_days += t.gap_seconds / _SECONDS_PER_DAY
        nd = int(elapsed_days)
        if nd != cur_day:
            cur_day = nd
            day_start = balance
            day_high = balance
        balance += t.r_multiple * (balance * risk_frac)
        if balance > peak:
            peak = balance
        if balance > day_high:
            day_high = balance
        dd = (peak - balance) / peak if peak > 0 else 0.0
        if dd > maxdd:
            maxdd = dd
        idd = (day_high - balance) / day_high if day_high > 0 else 0.0
        if idd > intraday_maxdd:
            intraday_maxdd = idd
        if not breached:
            if static_floor is not None and balance <= static_floor + 1e-9:
                breached = True; cause = "static_drawdown"; breach_day = elapsed_days
            elif (daily_loss_pct is not None and day_start > 0
                  and (day_start - balance) / day_start > daily_loss_pct + 1e-12):
                breached = True; cause = "daily_loss"; breach_day = elapsed_days
            if breached and breach_day is not None and breach_day <= horizon_days:
                survived = False
        if breached:
            break
    end_return = balance / account_size - 1.0 if account_size else 0.0
    return end_return, maxdd, intraday_maxdd, breached, cause, survived


def run_account(aid, unit, strat, ledger, args):
    rs_seq = ledger_to_r_sequence(ledger, initial_balance=unit.account_size_usd,
                                  base_risk_pct=args.base_risk_pct)
    n_tr = len(rs_seq)
    r_vals = [t.r_multiple for t in rs_seq]
    net_total_r = round(float(np.sum(r_vals)), 2) if r_vals else 0.0
    wins = sum(1 for r in r_vals if r > 0)
    win_rate = round(100.0 * wins / n_tr, 1) if n_tr else 0.0
    expectancy_r = round(net_total_r / n_tr, 4) if n_tr else 0.0
    exits = [t.exit_ts for t in rs_seq if t.exit_ts]
    window = (str(min(exits)), str(max(exits))) if exits else (None, None)

    daily_loss_pct = unit.ruleset.limits.daily_loss_pct
    static_dd_pct = (unit.ruleset.limits.max_drawdown_pct
                     if unit.ruleset.limits.drawdown_type == "static" else None)
    horizon_days = args.horizon_months * _DAYS_PER_MONTH
    gaps = [t.gap_seconds for t in rs_seq if t.gap_seconds > 0]
    med_gap_days = (median(gaps) / _SECONDS_PER_DAY) if gaps else 1.0
    if med_gap_days <= 0:
        med_gap_days = 1.0
    path_trades = max(50, min(int((horizon_days / med_gap_days) * 1.25) + args.block_len, 20000))

    out_by_risk = {}
    for risk in args.risks:
        rng = np.random.default_rng(args.seed)
        maxdds, idds, ends, breaches, survs = [], [], [], 0, 0
        causes = {}
        for _ in range(args.n_paths):
            idx = _bootstrap_indices(n_tr, path_trades, args.block_len, rng)
            er, mdd, idd, br, cause, sv = walk_with_maxdd(
                rs_seq, idx, account_size=unit.account_size_usd, risk_pct=risk,
                daily_loss_pct=daily_loss_pct, static_dd_pct=static_dd_pct,
                horizon_days=horizon_days)
            maxdds.append(mdd); idds.append(idd); ends.append(er)
            if br:
                breaches += 1; causes[cause] = causes.get(cause, 0) + 1
            if sv:
                survs += 1
        npth = args.n_paths
        out_by_risk[str(risk)] = {
            "sim_risk_pct": risk,
            "maxdd_pct_mean": round(100 * float(np.mean(maxdds)), 2),
            "maxdd_pct_median": round(100 * float(np.median(maxdds)), 2),
            "maxdd_pct_p95": round(100 * float(np.percentile(maxdds, 95)), 2),
            "maxdd_pct_max": round(100 * float(np.max(maxdds)), 2),
            "intraday_maxdd_pct_mean": round(100 * float(np.mean(idds)), 2),
            "intraday_maxdd_pct_p95": round(100 * float(np.percentile(idds, 95)), 2),
            "intraday_maxdd_pct_max": round(100 * float(np.max(idds)), 2),
            "p_breach": round(breaches / npth, 4),
            "breach_by_cause": {k: round(v / npth, 4) for k, v in sorted(causes.items())},
            "survival_h": round(survs / npth, 4),
            "end_return_mean_pct": round(100 * float(np.mean(ends)), 2),
        }
    return {
        "account": aid, "kind": unit.kind, "class": unit.account_class,
        "account_size_usd": unit.account_size_usd,
        "cap_daily_loss_pct": daily_loss_pct, "cap_static_dd_pct": static_dd_pct,
        "strategy": strat, "n_ledger_trades": n_tr,
        "net_total_r": net_total_r, "net_win_rate_pct": win_rate,
        "expectancy_r": expectancy_r, "window": window,
        "by_risk": out_by_risk,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--account", required=True)
    p.add_argument("--strategies", required=True)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--data", default="/home/ubuntu/ict-trader-data/btc_5m.parquet")
    p.add_argument("--clock-tf", default="1h")
    p.add_argument("--base-risk-pct", type=float, default=0.5)
    p.add_argument("--risks", default="0.30,0.60,1.5")
    p.add_argument("--fee-bps-roundtrip", type=float, default=7.5)
    p.add_argument("--n-paths", type=int, default=3000)
    p.add_argument("--block-len", type=int, default=8)
    p.add_argument("--horizon-months", type=float, default=12.0)
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--out", required=True)
    p.add_argument("--combined", action="store_true")
    p.add_argument("--regime-router", default="off", choices=["on", "off"])
    p.add_argument("--regime-policy", dest="regime_policy", default=None)
    a = p.parse_args()
    a.risks = [float(x) for x in a.risks.split(",") if x.strip()]
    strategies = [s.strip() for s in a.strategies.split(",") if s.strip()]

    units = all_account_units()
    if a.account not in units:
        print(f"ERROR account {a.account} not resolved; have {list(units)}", file=sys.stderr)
        return 2
    unit = units[a.account]
    base5m = bt._load_candles(a.data)
    print(f"[gate] {a.account} ({unit.kind}, caps dl={unit.ruleset.limits.daily_loss_pct} "
          f"sdd={unit.ruleset.limits.max_drawdown_pct}/{unit.ruleset.limits.drawdown_type}) "
          f"size=${unit.account_size_usd} sim_risks={a.risks} regime_router={a.regime_router}", file=sys.stderr)

    results = []
    runs = [(s, [s]) for s in strategies]
    if a.combined and len(strategies) > 1:
        runs.append(("__combined__", strategies))
    for label, roster in runs:
        print(f"[gate]   engine run: {label} roster={roster} symbol={a.symbol} rr={a.regime_router}", file=sys.stderr)
        try:
            summary = bt.run_system_backtest(
                base5m, roster=roster, start=a.start, end=a.end,
                initial_balance=unit.account_size_usd, risk_pct=a.base_risk_pct,
                daily_loss_pct=3.0, signal_ttl_bars=1, overrides={"symbol": a.symbol},
                refresh=False, clock_tf=a.clock_tf, flip_policy="hold",
                reentry_policy="suppress", attach_full=True,
                regime_router=a.regime_router, regime_policy_path=a.regime_policy,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[gate]   FAIL {label}: {e}", file=sys.stderr)
            results.append({"account": a.account, "strategy": label, "error": str(e)})
            continue
        ledger = summary.get("closed_trades", []) or []
        if not ledger:
            print(f"[gate]   {label}: EMPTY ledger", file=sys.stderr)
            results.append({"account": a.account, "strategy": label, "error": "empty_ledger",
                            "n_ledger_trades": 0})
            continue
        results.append(run_account(a.account, unit, label, ledger, a))

    payload = {"account": a.account, "symbol": a.symbol, "data": a.data,
               "base_risk_pct": a.base_risk_pct, "risks": a.risks,
               "fee_bps_roundtrip": a.fee_bps_roundtrip, "n_paths": a.n_paths,
               "horizon_months": a.horizon_months, "seed": a.seed,
               "regime_router": a.regime_router, "regime_policy": a.regime_policy,
               "results": results}
    Path(a.out).write_text(json.dumps(payload, indent=2, default=str))
    print(f"[gate] wrote {a.out}", file=sys.stderr)
    print(f"\n=== {a.account} rr={a.regime_router} (caps: daily {unit.ruleset.limits.daily_loss_pct*100:.0f}% / "
          f"static-DD {(unit.ruleset.limits.max_drawdown_pct or 0)*100:.0f}%) ===")
    for r in results:
        if r.get("error"):
            print(f"  {r['strategy']:24} ERROR {r['error']}")
            continue
        print(f"  {r['strategy']:24} n={r['n_ledger_trades']:>4} netR={r['net_total_r']:>8.1f} "
              f"WR={r['net_win_rate_pct']:>5.1f}% expR={r['expectancy_r']:>8.4f}")
        for rk in a.risks:
            b = r["by_risk"][str(rk)]
            print(f"      @ {rk:>4}%: maxDD% mean={b['maxdd_pct_mean']:>5.2f} p95={b['maxdd_pct_p95']:>5.2f} "
                  f"max={b['maxdd_pct_max']:>6.2f} || INTRADAY-DD mean={b['intraday_maxdd_pct_mean']:>5.2f} "
                  f"p95={b['intraday_maxdd_pct_p95']:>5.2f} max={b['intraday_maxdd_pct_max']:>6.2f} | "
                  f"P(breach)={b['p_breach']:.3f} surv={b['survival_h']:.3f} "
                  f"endRet%={b['end_return_mean_pct']:>6.1f} causes={b['breach_by_cause']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
