#!/usr/bin/env python3
"""Unit-A selective-flip RE-TEST runner (research, Tier-1, READ-ONLY).

Flip-heavy A/B: hold baseline vs selective under a knob grid, on a roster that
emits FREQUENT opposite-side same-symbol signals (trend_donchian trend cell +
fade_breakout_4h counter-trend fade cell on BTC; ict_scalp_5m EXCLUDED so its
huge loss can't swamp the signal).

For each run it invokes scripts.backtest_system.run_system_backtest (net-of-fee
7.5bps) and pulls:
  net_pnl, return_pct, max_drawdown_pct, return_dd_ratio, total_trades,
  win_rate_pct, and the evidence flip counters (selective_flips/holds/reentries)
  — so we can SEE the gates trip this time.

Additionally, to report net total R + intraday-DD p95 on the SAME basis as the
prior gates, it block-bootstraps the run's closed-trade ledger (src.prop.montecarlo)
at the account's 1.5% sim risk and walks intraday-DD (within-UTC-day, the live
max_dd_pct basis). 3000 paths / 12mo / seed 1234.

Pure/deterministic. No live path, no network, no order.
"""
from __future__ import annotations
import json, sys, argparse
from pathlib import Path
from statistics import median

REPO = Path("/tmp/uA")          # the Unit-A worktree (selective code lives here)
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import scripts.backtest_system as bt  # noqa: E402
from src.prop.montecarlo import ledger_to_r_sequence, _bootstrap_indices, _SECONDS_PER_DAY, _DAYS_PER_MONTH  # noqa: E402


def intraday_dd_stats(closed, *, account_size, base_risk_pct, sim_risk_pct,
                      n_paths=3000, block_len=8, horizon_months=12.0, seed=1234):
    """Net R + intraday-DD p95 over a block-bootstrap of the ledger at sim_risk_pct."""
    rs = ledger_to_r_sequence(closed, initial_balance=account_size, base_risk_pct=base_risk_pct)
    n = len(rs)
    if n == 0:
        return {"n_ledger": 0, "net_total_r": 0.0, "expectancy_r": 0.0, "intraday_dd_p95": None}
    r_vals = [t.r_multiple for t in rs]
    net_r = round(float(np.sum(r_vals)), 2)
    exp_r = round(net_r / n, 4)
    horizon_days = horizon_months * _DAYS_PER_MONTH
    gaps = [t.gap_seconds for t in rs if t.gap_seconds > 0]
    med_gap = (median(gaps) / _SECONDS_PER_DAY) if gaps else 1.0
    if med_gap <= 0:
        med_gap = 1.0
    path_trades = max(50, min(int((horizon_days / med_gap) * 1.25) + block_len, 20000))
    rng = np.random.default_rng(seed)
    risk_frac = sim_risk_pct / 100.0
    idds = []
    for _ in range(n_paths):
        idx = _bootstrap_indices(n, path_trades, block_len, rng)
        bal = float(account_size); day_high = bal; idd_max = 0.0
        elapsed = 0.0; cur_day = 0
        for ix in idx:
            t = rs[int(ix) % n]
            elapsed += t.gap_seconds / _SECONDS_PER_DAY
            nd = int(elapsed)
            if nd != cur_day:
                cur_day = nd; day_high = bal
            bal += t.r_multiple * (bal * risk_frac)
            if bal > day_high:
                day_high = bal
            idd = (day_high - bal) / day_high if day_high > 0 else 0.0
            if idd > idd_max:
                idd_max = idd
        idds.append(idd_max)
    return {"n_ledger": n, "net_total_r": net_r, "expectancy_r": exp_r,
            "intraday_dd_p95": round(100 * float(np.percentile(idds, 95)), 2)}


def run_one(base5m, roster, *, flip_policy, conf_thr, age_hours, ev_margin, args):
    # set the module-level selective knobs the harness reads
    bt._SELECTIVE_CONF_THRESHOLD = float(conf_thr)
    bt._SELECTIVE_MIN_AGE_HOURS = float(age_hours)
    if hasattr(bt, "_SELECTIVE_EV_MARGIN"):
        bt._SELECTIVE_EV_MARGIN = float(ev_margin)
    summary = bt.run_system_backtest(
        base5m, roster=roster, start=args.start, end=args.end,
        initial_balance=args.account_size, risk_pct=args.base_risk_pct,
        daily_loss_pct=3.0, signal_ttl_bars=1, overrides={"symbol": args.symbol},
        refresh=False, clock_tf=args.clock_tf, flip_policy=flip_policy,
        reentry_policy="suppress", attach_full=True,
    )
    ev = summary.get("evidence") or {}
    closed = summary.get("closed_trades") or []
    boot = intraday_dd_stats(closed, account_size=args.account_size,
                             base_risk_pct=args.base_risk_pct, sim_risk_pct=args.sim_risk_pct)
    return {
        "flip_policy": flip_policy, "conf_thr": conf_thr, "age_hours": age_hours,
        "ev_margin": ev_margin,
        "net_pnl": summary.get("net_pnl"), "return_pct": summary.get("return_pct"),
        "max_drawdown_pct": summary.get("max_drawdown_pct"),
        "return_dd_ratio": summary.get("return_dd_ratio"),
        "total_trades": summary.get("total_trades"), "win_rate_pct": summary.get("win_rate_pct"),
        "selective_flips": ev.get("selective_flips"), "selective_holds": ev.get("selective_holds"),
        "selective_reentries": ev.get("selective_reentries"),
        "selective_reentry_skips": ev.get("selective_reentry_skips"),
        **boot,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--roster", default="trend_donchian,fade_breakout_4h")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--data", default="/home/ubuntu/ict-trader-data/btc_5m.parquet")
    p.add_argument("--clock-tf", default="1h")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--account-size", type=float, default=10000.0)
    p.add_argument("--base-risk-pct", type=float, default=0.3)
    p.add_argument("--sim-risk-pct", type=float, default=1.5)
    p.add_argument("--conf-thresholds", default="0.10,0.15,0.20")
    p.add_argument("--ages", default="0,4")
    p.add_argument("--ev-margin", type=float, default=0.0)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    roster = [s.strip() for s in a.roster.split(",") if s.strip()]
    thrs = [float(x) for x in a.conf_thresholds.split(",") if x.strip()]
    ages = [float(x) for x in a.ages.split(",") if x.strip()]

    base5m = bt._load_candles(a.data)
    rows = []
    # HOLD baseline (single run; knobs irrelevant)
    print("[uA] hold baseline", file=sys.stderr)
    rows.append(run_one(base5m, roster, flip_policy="hold", conf_thr=0.0, age_hours=0.0,
                        ev_margin=a.ev_margin, args=a))
    # SELECTIVE grid
    for thr in thrs:
        for age in ages:
            print(f"[uA] selective thr={thr} age={age} ev_margin={a.ev_margin}", file=sys.stderr)
            rows.append(run_one(base5m, roster, flip_policy="selective", conf_thr=thr,
                                age_hours=age, ev_margin=a.ev_margin, args=a))

    payload = {"roster": roster, "symbol": a.symbol, "data": a.data, "clock_tf": a.clock_tf,
               "start": a.start, "end": a.end, "account_size": a.account_size,
               "base_risk_pct": a.base_risk_pct, "sim_risk_pct": a.sim_risk_pct,
               "ev_margin": a.ev_margin, "rows": rows}
    Path(a.out).write_text(json.dumps(payload, indent=2, default=str))
    print(f"[uA] wrote {a.out}", file=sys.stderr)
    # table
    h = rows[0]
    print(f"\n=== Unit A re-test: roster={roster} {a.symbol} {a.clock_tf} (net 7.5bps; sim risk {a.sim_risk_pct}%) ===")
    print(f"  {'policy/knobs':32} {'netPnL':>9} {'ret%':>7} {'maxDD%':>7} {'r/DD':>6} {'trd':>5} {'WR%':>5} {'flips':>6} {'holds':>6} {'netR':>8} {'intraDDp95':>10}")
    for r in rows:
        if r["flip_policy"] == "hold":
            lbl = "HOLD (baseline)"
        else:
            lbl = f"selective thr={r['conf_thr']} age={r['age_hours']}"
        print(f"  {lbl:32} {str(r['net_pnl']):>9} {str(r['return_pct']):>7} {str(r['max_drawdown_pct']):>7} "
              f"{str(r['return_dd_ratio']):>6} {str(r['total_trades']):>5} {str(r['win_rate_pct']):>5} "
              f"{str(r['selective_flips']):>6} {str(r['selective_holds']):>6} {str(r['net_total_r']):>8} {str(r['intraday_dd_p95']):>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
