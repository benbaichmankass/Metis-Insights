#!/usr/bin/env python3
"""PROP-EVIDENCE-BRACKET: grade a prop leg under the exits the Breakout bracket ACTUALLY performs.

The committed evidence records (comms/strategy_evidence/<leg>.json, fidelity "faithful")
model every exit lever the leg's YAML declares: chandelier trail, stale_stop,
trail-decay. The live Breakout path places ONE broker-side bracket (SL + TP at entry) and
never modifies it (src/prop/breakout_ticket.py: "Do not manage the exit -- the broker-side
bracket is the exit"; the package is stamped terminal 'emitted', so order_monitor never
trails it; prop_executor.modify_bracket is only used by _contain to RESTORE the original
SL/TP). So the bracket-faithful exit model is: initial ATR stop + TP, nothing else.

This script runs scripts/backtest_trend.py twice on the same candles -- (base) the leg's
declared levers, (static) trail/stale/decay off, no time exit -- and writes both summaries.
Reproduce:  python3 scripts/research/prop_bracket_exit_model.py --leg trend_donchian_eth_prop
Needs pandas + network (Binance Vision candles). Costs: the harness's own venue-aware stack.
"""
from __future__ import annotations

import argparse, json, os, subprocess, sys, tempfile
from collections import defaultdict

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(REPO, "scripts", "research"), os.path.join(REPO, "scripts"), REPO]
STRIP = {"--stale-exit-bars", "--stale-exit-below-r", "--trail-decay-arm-r",
         "--trail-decay-stall-bars", "--trail-decay-tight-mult"}


def _strip(a, flags):
    out, i = [], 0
    while i < len(a):
        if a[i] in flags:
            i += 2
            continue
        out.append(a[i]); i += 1
    return out


def _set(a, k, v):
    a = a[:]; a[a.index(k) + 1] = v
    return a


def _summ(path):
    rows = [json.loads(l) for l in open(path)]
    g = defaultdict(lambda: [0, 0.0])
    for t in rows:
        g[t["exit_reason"]][0] += 1; g[t["exit_reason"]][1] += t["net_r"]
    xs = sorted(rows, key=lambda t: t["entry_time"]); q = len(xs) // 4
    folds = [round(sum(t["net_r"] for t in (xs[i * q:(i + 1) * q] if i < 3 else xs[3 * q:])), 2) for i in range(4)]
    return {"n": len(rows), "net_r": round(sum(t["net_r"] for t in rows), 2),
            "net_r_fee_only": round(sum(t["net_r_fee_only"] for t in rows), 2),
            "by_exit": {k: {"n": v[0], "net_r": round(v[1], 2)} for k, v in g.items()},
            "quarter_net_r": folds,
            "window": [xs[0]["entry_time"], xs[-1]["exit_time"]]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leg", required=True)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--out", default=None)
    ap.add_argument("--workdir", default=None)
    a = ap.parse_args()
    import yaml
    import regime_debt_matrix as rdm
    cfg = yaml.safe_load(open(os.path.join(REPO, "config/strategies.yaml")))
    c = cfg.get("strategies", cfg)[a.leg]
    wd = a.workdir or tempfile.mkdtemp()
    row = rdm.run_one(a.leg, c, wd, days=a.days)           # fetches candles, runs the declared-lever baseline
    if row.get("error"):
        sys.exit(f"harness failed: {row['error']}")
    csv = os.path.join(wd, f"{a.leg}__data.csv")
    argv, _, _ = rdm.build_harness_cmd(a.leg, c, rdm.classify(c), csv, "1h",
                                       os.path.join(wd, "base.jsonl"), os.path.join(wd, "base.json"))
    st = _strip(argv, STRIP)
    st = _set(st, "--trail-mult", "1000") + ["--timeout-bars", "1000000"]
    st = _set(_set(st, "--emit-trades", os.path.join(wd, "static.jsonl")), "--json", os.path.join(wd, "static.json"))
    for args in (argv, st):
        subprocess.run(args, check=True, cwd=REPO, capture_output=True)
    out = {"leg": a.leg, "exit_model_note": "static = SL + TP only (what the Breakout bracket performs); "
           "base = declared levers (what the committed evidence record models)",
           "provenance": "MEASURED (offline harness, venue-aware cost stack); window and candle source in 'window'",
           "base": _summ(os.path.join(wd, "base.jsonl")), "static": _summ(os.path.join(wd, "static.jsonl"))}
    s = json.dumps(out, indent=1)
    if a.out:
        open(a.out, "w").write(s + "\n")
    print(s)


if __name__ == "__main__":
    main()
