#!/usr/bin/env python3
# wiring: manual-only — a one-shot research measurement a session RUNS to grade RQ-20261004-651 (static-bracket tp_r / atr_stop_mult grid on a prop leg); its committed output is read by the unit's result row
"""RQ-20261004-651: static-bracket lever grid for a prop leg.

Same plumbing as prop_bracket_exit_model.py (one candle fetch via
regime_debt_matrix.run_one, then scripts/backtest_trend.py on the STATIC
bracket: trail off, stale/decay off, no time exit, the harness's own
venue-aware cost stack), but run once per (tp_r, atr_stop_mult) arm of the
grid fixed in research/queue/RQ-20261004-651.yaml, and graded against that
unit's rule: pool / older-year / recent-year net R versus the live arm.

Reproduce:
  python3 scripts/research/prop_bracket_lever_sweep.py --leg trend_donchian_sol_prop --days 730 --out out.json
Needs pandas + network (Binance Vision candles).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta

import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(REPO, "scripts", "research"), os.path.join(REPO, "scripts"), REPO]
from prop_bracket_exit_model import STRIP, _set, _strip  # noqa: E402

# Fixed by RULE-RQ1004-651-SOL-BRACKET-LEVER before the run. Do not edit after a result.
TP_R_GRID = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0)
STOP_GRID = (1.5, 2.0, 2.5, 3.0, 3.5)
N_FLOOR = 88
MIN_DELTA_POOL = 5.0
RECENT_DAYS = 365


def _arm_stats(rows, cutoff):
    def tot(xs):
        return round(sum(t["net_r"] for t in xs), 4)
    recent = [t for t in rows if pd.Timestamp(t["entry_time"]) >= cutoff]
    older = [t for t in rows if pd.Timestamp(t["entry_time"]) < cutoff]
    return {"n": len(rows), "net_r": tot(rows),
            "net_r_fee_only": round(sum(t["net_r_fee_only"] for t in rows), 4),
            "older": {"n": len(older), "net_r": tot(older)},
            "recent": {"n": len(recent), "net_r": tot(recent)}}


def grade(arms, live_key):
    live = arms[live_key]
    if live["n"] < N_FLOOR:
        return {"verdict": "underpowered", "qualifying": [], "candidate": None}
    qual = []
    for k, a in arms.items():
        if k == live_key:
            continue
        d_pool = a["net_r"] - live["net_r"]
        d_old = a["older"]["net_r"] - live["older"]["net_r"]
        d_rec = a["recent"]["net_r"] - live["recent"]["net_r"]
        a["delta"] = {"pool": round(d_pool, 4), "older": round(d_old, 4), "recent": round(d_rec, 4)}
        if (a["n"] >= N_FLOOR and a["net_r"] > 0 and d_pool >= MIN_DELTA_POOL
                and d_old > 0 and d_rec > 0 and a["recent"]["net_r"] > 0):
            qual.append(k)
    qual.sort(key=lambda k: -arms[k]["delta"]["pool"])
    return {"verdict": "pass" if qual else "fail", "qualifying": qual,
            "candidate": qual[0] if qual else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leg", required=True)
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--out", default=None)
    ap.add_argument("--workdir", default=None)
    a = ap.parse_args()
    import yaml
    import regime_debt_matrix as rdm
    c = yaml.safe_load(open(os.path.join(REPO, "config/strategies.yaml")))["strategies"][a.leg]
    live_key = f"tp{float(c['tp_r'])}_stop{float(c['atr_stop_mult'])}"
    wd = a.workdir or tempfile.mkdtemp()
    row = rdm.run_one(a.leg, c, wd, days=a.days)  # fetches candles
    if row.get("error"):
        sys.exit(f"harness failed: {row['error']}")
    csv = os.path.join(wd, f"{a.leg}__data.csv")
    ts = pd.read_csv(csv, usecols=[0]).iloc[:, 0]
    last = pd.to_datetime(ts, utc=True, errors="coerce").max()
    if pd.isna(last):
        last = pd.to_datetime(pd.to_numeric(ts, errors="coerce").max(), unit="ms", utc=True)
    cutoff = last - timedelta(days=RECENT_DAYS)
    argv, faithful, omitted = rdm.build_harness_cmd(a.leg, c, rdm.classify(c), csv, "1h",
                                                    os.path.join(wd, "x.jsonl"), os.path.join(wd, "x.json"))
    st = _set(_strip(argv, STRIP), "--trail-mult", "1000") + ["--timeout-bars", "1000000"]
    arms = {}
    for tp in TP_R_GRID:
        for sm in STOP_GRID:
            key = f"tp{tp}_stop{sm}"
            emit = os.path.join(wd, f"{key}.jsonl")
            args = _set(_set(_set(_set(st, "--tp-r", str(tp)), "--atr-stop-mult", str(sm)),
                             "--emit-trades", emit), "--json", os.path.join(wd, f"{key}.json"))
            subprocess.run(args, check=True, cwd=REPO, capture_output=True)
            rows = [json.loads(ln) for ln in open(emit)]
            arms[key] = {"tp_r": tp, "atr_stop_mult": sm, **_arm_stats(rows, cutoff)}
    if live_key not in arms:
        sys.exit(f"live arm {live_key} is not on the registered grid")
    g = grade(arms, live_key)
    out = {"research_unit": "RQ-20261004-651", "decision_rule": "RULE-RQ1004-651-SOL-BRACKET-LEVER",
           "leg": a.leg, "days": a.days, "live_arm": live_key, "fidelity": "faithful" if faithful else "approximate",
           "omitted_levers": omitted, "exit_model": "static bracket: initial ATR stop + TP=min(venue cap, tp_r*risk)",
           "window": {"last_candle": str(last), "recent_cutoff": str(cutoff)},
           "provenance": "MEASURED (offline harness, venue-aware cost stack: fee + slippage + funding)",
           **g, "arms": arms}
    s = json.dumps(out, indent=1)
    if a.out:
        open(a.out, "w").write(s + "\n")
    print(json.dumps({k: out[k] for k in ("verdict", "candidate", "qualifying", "live_arm")}))


if __name__ == "__main__":
    main()
