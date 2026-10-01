#!/usr/bin/env python3
"""P2 round 2 — screen ONE (Breakout-listed symbol x corpus-harness family) cell.

Tier-1 offline research. Reads config/strategies.yaml, fetches public candles
(data.binance.vision), runs an existing full-cost-stack harness through
`regime_debt_matrix.run_one` with a DONOR leg's config re-pointed at the new
symbol, then re-prices the trades under Breakout's cost stack with
`prop_ev_grid.py`. Writes only under --workdir. Touches no config/ or src/.

Pre-registered rule (research/queue/RQ-20261001-*; registered before any run):
  Gate A  n >= 88 AND pooled harness net_r > 0 AND net_r > 0 in BOTH
          chronological halves of the window  (harness net_r is net of
          fee + slippage + funding, D1-verified).
  Gate B  (only if A) prop_ev_grid path-mode on arms bal015 and room033,
          5 seeds, tool-default MC, --costs breakout (8 bps commission +
          3 bps slippage round trip, 0.033%/day swap, DXTrade model):
          an arm PASSES iff ev_net_usd_per_life > 0 AND P(net>0) >= 0.70 on
          ALL 5 seeds.
  Verdict PASS       Gate A and (bal015 or room033) PASS
          FAIL       n >= 88 and (Gate A fails on pooled net_r <= 0, or a Gate B arm has ev <= 0
                     on any seed with no arm passing)
          NULL       Gate A passes, no arm passes, ev > 0 on all seeds somewhere
          UNDERPOWERED n < 88
          NOT_APPLICABLE no data / harness failed (read_state says which)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "research"))

DONORS = {
    "trend": "trend_donchian_eth_prop",  # 1h, the breakout_1 live shape
    "ict15": "ict_scalp_xrp_15m",
    "pb2": "xrp_pullback_2h",
    "ict5": "ict_scalp_xrp_5m",  # only used with --trades (committed 5m evidence)
}
ARMS = {
    "bal015": "--sizing balance --risk-pct 0.015",
    "room033": "--sizing room --risk-pct 0.015 --room-frac 0.33",
}


def _trades(path: Path):
    rows = []
    for ln in path.read_text().splitlines():
        if ln.strip():
            rows.append(json.loads(ln))
    return rows


_MAP = {"PASS": "pass", "FAIL": "fail", "NULL": "indeterminate",
        "UNDERPOWERED": "indeterminate", "NOT_APPLICABLE": "not_applicable"}


def _finish(wd: Path, name: str, out: dict) -> None:
    """Write the E5-shaped verdict.json script_run.py lands, plus a full sidecar."""
    (wd / f"{name}__detail.json").write_text(json.dumps(out, indent=1, default=str))
    v = out.get("verdict", "NOT_APPLICABLE")
    rs = out.get("read_state", "producer_failed")
    note = v
    if v == "FAIL" and out.get("why"):
        note += f": {out['why']}"
    if out.get("arm_verdicts"):
        note += f" | arms {out['arm_verdicts']}"
    if out.get("harness_error"):
        note += f" | {str(out['harness_error'])[:200]}"
    rec = {
        "verdict": _MAP.get(v, "not_applicable"), "read_state": rs,
        "population": (f"{name}: {out.get('n')} harness trades, {out.get('days')}d window "
                       f"{out.get('first_entry')} -> {out.get('last_entry')}, donor {out.get('donor')}"),
        "n": out.get("n"),
        "measurement": {k: out.get(k) for k in ("net_r_pooled", "net_r_half1", "net_r_half2",
                                                "arm_verdicts", "fidelity", "omitted_levers")},
        "note": note,
    }
    (wd / "verdict.json").write_text(json.dumps(rec, indent=1, default=str))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="e.g. DOGEUSDT")
    ap.add_argument("--family", required=True, choices=sorted(DONORS) + ["ict5"])
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--trades", default=None,
                    help="reuse a committed trades.jsonl (skips fetch + harness); --symbol/--family still label the cell")
    ap.add_argument("--census-only", action="store_true",
                    help="run the harness and print n only; no Gate A/B statistic")
    a = ap.parse_args()

    import yaml
    import regime_debt_matrix as rdm

    donor = DONORS[a.family]
    doc = yaml.safe_load((REPO / "config/strategies.yaml").read_text())
    cfg = dict(doc.get("strategies", doc)[donor])
    cfg["symbols"] = [a.symbol]
    name = f"p2_{a.family}_{a.symbol.lower()}"
    wd = Path(a.workdir)
    wd.mkdir(parents=True, exist_ok=True)

    if a.trades:
        name = f"p2_{a.family}_{a.symbol.lower()}"
        (wd / f"{name}__trades.jsonl").write_text(Path(a.trades).read_text())
        row = {"fidelity": "committed-evidence", "omitted_levers": None}
    else:
        row = rdm.run_one(name, cfg, str(wd), days=a.days)
    out = {"cell": name, "symbol": a.symbol, "family": a.family, "donor": donor,
           "days": a.days, "harness_error": row.get("error"),
           "fidelity": row.get("fidelity"), "omitted_levers": row.get("omitted_levers")}
    tf = wd / f"{name}__trades.jsonl"
    if row.get("error") or not tf.exists():
        out.update(read_state="producer_failed", verdict="NOT_APPLICABLE")
        _finish(wd, name, out)
        print(json.dumps(out))
        return 0

    trades = _trades(tf)
    n = len(trades)
    out["n"] = n
    if a.census_only:
        print(json.dumps({"cell": name, "n": n}))
        return 0
    trades.sort(key=lambda r: r["entry_time"])
    mid = n // 2
    h1 = sum(r["net_r"] for r in trades[:mid])
    h2 = sum(r["net_r"] for r in trades[mid:])
    pooled = h1 + h2
    out.update(read_state="measured", net_r_pooled=round(pooled, 3),
               net_r_half1=round(h1, 3), net_r_half2=round(h2, 3),
               first_entry=trades[0]["entry_time"] if n else None,
               last_entry=trades[-1]["entry_time"] if n else None)
    if n < 88:
        out["verdict"] = "UNDERPOWERED"
    elif not (pooled > 0 and h1 > 0 and h2 > 0):
        out["verdict"] = "FAIL"
        out["why"] = "Gate A: harness net_r not positive pooled and in both halves"
    else:
        for ak, args in ARMS.items():
            od = wd / f"grid_{ak}"
            subprocess.run(
                [sys.executable, str(REPO / "scripts/research/prop_ev_grid.py"),
                 "--leg", f"{name}={tf}", "--arm", f"A={args}",
                 "--common", "--ruleset config/prop_rulesets/breakout.yaml --costs breakout "
                             "--funded-start fresh --swap-model dxtrade",
                 "--seeds", "1,2,3,4,5", "--grade", "pass_all_seeds", "--p-floor", "0.70",
                 "--n-floor", "88", "--out", str(od), "--jobs", str(a.jobs)],
                cwd=REPO, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        verdicts = {}
        for ak in ARMS:
            vj = wd / f"grid_{ak}" / "verdict.json"
            verdicts[ak] = json.loads(vj.read_text())["verdict"] if vj.exists() else None
        out["arm_verdicts"] = verdicts
        # prop_ev_grid grades: pass | fail | indeterminate (NULL). Map to the rule's words.
        if any(v == "pass" for v in verdicts.values()):
            out["verdict"] = "PASS"
        elif all(v is None for v in verdicts.values()):
            out.update(verdict="NOT_APPLICABLE", read_state="producer_failed")
        elif any(v == "indeterminate" for v in verdicts.values()):
            out["verdict"] = "NULL"
        else:
            out["verdict"] = "FAIL"
    _finish(wd, name, out)
    print(json.dumps({k: out.get(k) for k in
                      ("cell", "n", "net_r_pooled", "net_r_half1", "net_r_half2", "verdict")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
