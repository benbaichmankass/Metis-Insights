#!/usr/bin/env python3
# wiring: manual-only — a one-shot research measurement a session RUNS to grade RQ-20261004-652 (confirming test for the RQ-20261004-651 SOL static-bracket candidate); its committed output is read by the unit's result row
"""RQ-20261004-652: confirming test for a prop leg's static-bracket candidate.

Grades RULE-RQ1004-652-SOL-BRACKET-CONFIRM (registered in
research/queue/RQ-20261004-652.yaml before this script existed or ran):

  C1 unseen-data replication on PRE (entries before last candle - 730d, the
     window RQ-20261004-651 never saw), with an ex-max-fold check;
  C2 walk-forward: expanding-window arm selection over the RQ-651 grid,
     evaluated on the next 365-day fold, F3..F6;
  C3 plateau: the 9-cell neighbourhood tp {1.5,2,2.5} x stop {3,3.5,4};
  C4 prop_ev_sim V2, seeds 1..5, on breakout AND tradeify_247_1step, fed PRE trades;
  C5 SOLUSD lot step 0.01 and the $10 minimum-risk floor at the candidate stop.

Same harness plumbing as prop_bracket_lever_sweep.py (RQ-651): one candle fetch
via regime_debt_matrix.run_one, then scripts/backtest_trend.py on the STATIC
bracket with the leg's own venue-aware cost stack (fee + slippage + funding).

Reproduce:
  python3 scripts/research/prop_bracket_confirm.py --leg trend_donchian_sol_prop --days 2200 --out out.json
Needs pandas + pyyaml + network (Binance Vision candles).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
from datetime import timedelta

import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(REPO, "scripts", "research"), os.path.join(REPO, "scripts"), REPO]
from prop_bracket_exit_model import STRIP, _set, _strip  # noqa: E402

# Fixed by RULE-RQ1004-652-SOL-BRACKET-CONFIRM before the run. Do not edit after a result.
GRID_651 = [(tp, sm) for tp in (2.0, 3.0, 4.0, 5.0, 6.0, 8.0) for sm in (1.5, 2.0, 2.5, 3.0, 3.5)]
PLATEAU = [(tp, sm) for tp in (1.5, 2.0, 2.5) for sm in (3.0, 3.5, 4.0)]
LIVE = (6.0, 2.5)
CAND = (2.0, 3.5)
N_FLOOR = 88
TRAIN_N_FLOOR = 30
FOLD_DAYS = 365
PRE_DAYS = 730
SEEDS = (1, 2, 3, 4, 5)
LOT_STEP = 0.01
SIM_LEG = "trend_donchian_sol_prop"
RULESETS = {
    "breakout": ["--ruleset", "config/prop_rulesets/breakout.yaml", "--sizing", "room",
                 "--room-frac", "0.33", "--risk-pct", "0.015", "--min-risk", "10"],
    "tradeify_247_1step": ["--ruleset", "config/prop_rulesets/tradeify_247_1step.yaml", "--sizing", "start",
                           "--risk-pct", "0.005", "--min-risk", "10",
                           "--lev-cap", f"{SIM_LEG}=2"],
}


def key(arm):
    return f"tp{arm[0]}_stop{arm[1]}"


def tot(rows):
    return round(sum(t["net_r"] for t in rows), 4)


def fold_of(ts, bounds):
    for i, (lo, hi) in enumerate(bounds):
        if lo <= ts < hi:
            return i
    return None


def grade_c1(trades, pre_cut, bounds):
    pre = {k: [t for t in v if t["_ts"] < pre_cut] for k, v in trades.items()}
    lv, cd = pre[key(LIVE)], pre[key(CAND)]
    pre_folds = [i for i, (lo, _) in enumerate(bounds) if lo < pre_cut]
    per_fold = {}
    for i in pre_folds:
        lo, hi = bounds[i]
        lf = tot([t for t in lv if lo <= t["_ts"] < hi])
        cf = tot([t for t in cd if lo <= t["_ts"] < hi])
        per_fold[f"F{i + 1}"] = {"live": lf, "cand": cf, "delta": round(cf - lf, 4)}
    delta = tot(cd) - tot(lv)
    max_fold = max(per_fold, key=lambda f: per_fold[f]["delta"])
    ex_max = delta - per_fold[max_fold]["delta"]
    ok = len(cd) >= N_FLOOR and tot(cd) > 0 and delta > 0 and ex_max > 0
    return {"holds": ok, "live": {"n": len(lv), "net_r": tot(lv)}, "cand": {"n": len(cd), "net_r": tot(cd)},
            "delta": round(delta, 4), "max_delta_fold": max_fold, "delta_ex_max_fold": round(ex_max, 4),
            "per_fold": per_fold}, pre


def grade_c2(trades, bounds):
    steps, oos_sel, oos_live = [], 0.0, 0.0
    for k in range(2, len(bounds)):  # F3..F6
        lo, hi = bounds[k]
        best, best_r = None, None
        for arm in GRID_651:  # ordered by tp then stop, so the first max wins ties
            tr = [t for t in trades[key(arm)] if t["_ts"] < lo]
            if len(tr) < TRAIN_N_FLOOR:
                continue
            r = tot(tr)
            if best_r is None or r > best_r:
                best, best_r = arm, r
        sel = tot([t for t in trades[key(best)] if lo <= t["_ts"] < hi])
        liv = tot([t for t in trades[key(LIVE)] if lo <= t["_ts"] < hi])
        oos_sel += sel
        oos_live += liv
        steps.append({"test_fold": f"F{k + 1}", "selected": key(best), "train_net_r": best_r,
                      "oos_selected": sel, "oos_live": liv})
    ok = oos_sel - oos_live > 0 and oos_sel > 0
    return {"holds": ok, "oos_selected": round(oos_sel, 4), "oos_live": round(oos_live, 4),
            "delta": round(oos_sel - oos_live, 4), "steps": steps}


def grade_c3(trades, pre):
    live_full, live_pre = tot(trades[key(LIVE)]), tot(pre[key(LIVE)])
    full = {key(a): tot(trades[key(a)]) for a in PLATEAU}
    prev = {key(a): tot(pre[key(a)]) for a in PLATEAU}
    beat = sum(1 for v in full.values() if v > live_full)
    med_full, med_pre = statistics.median(full.values()), statistics.median(prev.values())
    ok = beat >= 6 and med_full > live_full and med_pre > live_pre
    return {"holds": ok, "live_full": live_full, "live_pre": live_pre, "cells_beating_live_full": beat,
            "median_full": round(med_full, 4), "median_pre": round(med_pre, 4),
            "cells_full": full, "cells_pre": prev}


def run_sim(trades_path, ruleset_args, seed, out):
    cmd = [sys.executable, os.path.join(REPO, "scripts/research/prop_ev_sim.py"), "--rule", "v2",
           "--trades", f"{SIM_LEG}={trades_path}", "--seed", str(seed), "--horizon-days", "730",
           "--out", out] + ruleset_args
    subprocess.run(cmd, check=True, cwd=REPO, capture_output=True)
    d = json.load(open(out))
    path = d["results"].get("path", {})
    return {"seed": seed, "verdict": d["decision_rule_v2"]["verdict"],
            "ev_net_usd_per_life": path.get("ev_net_usd_per_life"), "p_pass_eval": path.get("p_pass_eval")}


def grade_c4(pre, wd, jobs=4):
    from concurrent.futures import ThreadPoolExecutor
    paths = {}
    for arm in (LIVE, CAND):
        p = paths[arm] = os.path.join(wd, f"pre_{key(arm)}.jsonl")
        with open(p, "w") as f:
            for t in pre[key(arm)]:
                f.write(json.dumps({k: v for k, v in t.items() if k != "_ts"}) + "\n")
    with ThreadPoolExecutor(jobs) as ex:
        futs = {(rs, arm, s): ex.submit(run_sim, paths[arm], rargs, s,
                                        os.path.join(wd, f"sim_{rs}_{key(arm)}_s{s}.json"))
                for rs, rargs in RULESETS.items() for arm in (LIVE, CAND) for s in SEEDS}
    out = {}
    ok = True
    for rs in RULESETS:
        res = {}
        for arm in (LIVE, CAND):
            seeds = [futs[(rs, arm, s)].result() for s in SEEDS]
            res[key(arm)] = {"seeds": seeds,
                             "median_ev": statistics.median(s["ev_net_usd_per_life"] for s in seeds),
                             "fails": sum(1 for s in seeds if s["verdict"] == "fail")}
        c, lv = res[key(CAND)], res[key(LIVE)]
        holds = c["fails"] == 0 and c["median_ev"] >= lv["median_ev"]
        ok = ok and holds
        out[rs] = {"holds": holds, **res}
    return {"holds": ok, "rulesets": out}


def grade_c5(rows):
    n = len(rows)
    zero, kept95, lev_ok, worst = 0, 0, 0, 1.0
    for t in rows:
        dist = abs(t["entry"] - t["sl"])
        q10 = math.floor(10.0 / dist / LOT_STEP + 1e-9) * LOT_STEP
        ideal10 = 10.0 / dist
        if q10 < LOT_STEP:
            zero += 1
        ratio = q10 / ideal10
        worst = min(worst, ratio)
        kept95 += ratio >= 0.95
        q50 = math.floor(50.0 / dist / LOT_STEP + 1e-9) * LOT_STEP
        lev_ok += q50 * t["entry"] <= 2 * 10000.0
    ok = zero == 0 and kept95 >= 0.99 * n and lev_ok >= 0.99 * n
    return {"holds": ok, "n": n, "trades_below_one_lot_at_10usd": zero,
            "share_risk_kept_ge_95pct_at_10usd": round(kept95 / n, 4), "worst_risk_ratio_at_10usd": round(worst, 4),
            "share_notional_within_2x_10k_at_50usd": round(lev_ok / n, 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leg", required=True)
    ap.add_argument("--days", type=int, default=2200)
    ap.add_argument("--out", default=None)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--reuse", action="store_true", help="reuse per-arm trade JSONL already in --workdir")
    ap.add_argument("--jobs", type=int, default=4, help="parallel prop_ev_sim runs")
    a = ap.parse_args()
    import yaml
    import regime_debt_matrix as rdm
    c = yaml.safe_load(open(os.path.join(REPO, "config/strategies.yaml")))["strategies"][a.leg]
    if (float(c["tp_r"]), float(c["atr_stop_mult"])) != LIVE:
        sys.exit(f"live levers drifted from the registered LIVE arm {LIVE}")
    wd = a.workdir or tempfile.mkdtemp()
    os.makedirs(wd, exist_ok=True)
    row = rdm.run_one(a.leg, c, wd, days=a.days)  # fetches candles
    if row.get("error"):
        sys.exit(f"harness failed: {row['error']}")
    csv = os.path.join(wd, f"{a.leg}__data.csv")
    ts = pd.to_datetime(pd.read_csv(csv, usecols=[0]).iloc[:, 0], utc=True, errors="coerce")
    first, last = ts.min(), ts.max()
    pre_cut = last - timedelta(days=PRE_DAYS)
    edges = [last - timedelta(days=FOLD_DAYS * i) for i in range(5, 0, -1)]
    bounds = list(zip([first] + edges, edges + [last + timedelta(hours=1)]))
    argv, faithful, omitted = rdm.build_harness_cmd(a.leg, c, rdm.classify(c), csv, "1h",
                                                    os.path.join(wd, "x.jsonl"), os.path.join(wd, "x.json"))
    st = _set(_strip(argv, STRIP), "--trail-mult", "1000") + ["--timeout-bars", "1000000"]
    trades = {}
    for arm in sorted(set(GRID_651) | set(PLATEAU)):
        emit = os.path.join(wd, f"{key(arm)}.jsonl")
        args = _set(_set(_set(_set(st, "--tp-r", str(arm[0])), "--atr-stop-mult", str(arm[1])),
                         "--emit-trades", emit), "--json", os.path.join(wd, f"{key(arm)}.json"))
        if not (a.reuse and os.path.exists(emit)):
            subprocess.run(args, check=True, cwd=REPO, capture_output=True)
        rows = [json.loads(ln) for ln in open(emit)]
        for t in rows:
            t["_ts"] = pd.Timestamp(t["entry_time"])
        trades[key(arm)] = rows
    c1, pre = grade_c1(trades, pre_cut, bounds)
    out = {"research_unit": "RQ-20261004-652", "decision_rule": "RULE-RQ1004-652-SOL-BRACKET-CONFIRM",
           "leg": a.leg, "days": a.days, "live_arm": key(LIVE), "candidate_arm": key(CAND),
           "fidelity": "faithful" if faithful else "approximate", "omitted_levers": omitted,
           "exit_model": "static bracket: initial ATR stop + TP=min(venue cap, tp_r*risk)",
           "window": {"first_candle": str(first), "last_candle": str(last), "pre_cutoff": str(pre_cut),
                      "folds": [{"fold": f"F{i + 1}", "start": str(lo), "end": str(hi)}
                                for i, (lo, hi) in enumerate(bounds)]},
           "provenance": "MEASURED (offline harness, venue-aware cost stack: fee + slippage + funding)"}
    if len(pre[key(LIVE)]) < N_FLOOR:
        out.update(verdict="underpowered", C1=c1)
    else:
        out["C1"] = c1
        out["C2"] = grade_c2(trades, bounds)
        out["C3"] = grade_c3(trades, pre)
        out["C4"] = grade_c4(pre, wd, a.jobs)
        out["C5"] = grade_c5(trades[key(CAND)])
        out["verdict"] = "pass" if all(out[k]["holds"] for k in ("C1", "C2", "C3", "C4", "C5")) else "fail"
    out["arms"] = {k: {"n": len(v), "net_r": tot(v),
                       "per_fold": [tot([t for t in v if lo <= t["_ts"] < hi]) for lo, hi in bounds]}
                   for k, v in trades.items()}
    s = json.dumps(out, indent=1, default=str)
    if a.out:
        open(a.out, "w").write(s + "\n")
    print(json.dumps({k: (out[k]["holds"] if isinstance(out.get(k), dict) else out.get(k))
                      for k in ("verdict", "C1", "C2", "C3", "C4", "C5")}))


if __name__ == "__main__":
    main()
