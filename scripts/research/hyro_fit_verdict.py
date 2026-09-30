#!/usr/bin/env python3
"""HyroTrader rule-fit verdict for one or more strategy legs (lane HYRO-STRATS, Tier-1).

Runs the cost-complete evidence builder (``scripts/ops/build_strategy_evidence.py``, fee +
slippage + funding via ``execution_costs``) over a chosen window, pools the emitted per-trade
ledgers, and grades them against the rule registered in
``research/queue/RQ-20260930-501.yaml`` using ``scripts/research/hyro_passprob_mc.py``.

The grading numbers are constants HERE and in the queue unit, fixed BEFORE any run:
  n floor            300 pooled trades (below -> UNDERPOWERED, never FAIL)
  gate A (edge)      pooled full-cost net R > 0
  gate B (rule-fit)  strict ruleset (config/prop_rulesets/hyrotrader.yaml), reading A,
                     risk 1.0%/trade, flag_frac 0.0 (BEST CASE: no fill is flagged), 720-day per-phase horizon
                     (the firm sets no time limit), P(pass BOTH eval phases) >= 0.25 on ALL seeds
  gate C (fragility) same, with every sampled R shifted by -0.03, P(both) >= 0.10 on ALL seeds
  0.25 is the break-even P(both) at the $59 deposit: refundable on first payout, so a failed
  attempt costs $59 and a passed one nets ~$200 (5% x $5k x 80% split) in the first 90 funded days:
  P > 59 / (200 + 59) = 0.228, rounded up.
``--from-ledger`` skips the builder and grades committed ledgers (used by the tests; it is a
smoke path, NOT a pre-registered run).

Writes ``<out>/verdict.json`` = {verdict, read_state, population, n, ...}.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics as st
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

_s = importlib.util.spec_from_file_location("hyro_mc", REPO / "scripts/research/hyro_passprob_mc.py")
mc = importlib.util.module_from_spec(_s)
_s.loader.exec_module(mc)

N_FLOOR, P_B, P_C, SHIFT_C, FLAG, RISK, CAP = 300, 0.25, 0.10, -0.03, 0.0, 1.0, 720
SEEDS = (1, 2, 3)


def _ts(x: Any) -> datetime:
    return datetime.fromisoformat(str(x).replace("Z", "+00:00"))


def spec_from_ledgers(name: str, paths: List[str]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for p in paths:
        rows += [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]
    rows.sort(key=lambda r: _ts(r["entry_time"]))
    e = [_ts(r["entry_time"]) for r in rows]
    x = [_ts(r["exit_time"]) for r in rows]
    span = max((max(x) - min(e)).days, 1)
    return dict(
        name=name, n=len(rows), span_days=span, block_len=4,
        r_samples=[float(r["net_r"]) for r in rows],
        trades_per_day=len(rows) / span,
        same_day_frac=sum(a.date() == b.date() for a, b in zip(e, x)) / len(rows),
        stop_pct=st.median(abs(r["entry"] - r["sl"]) / r["entry"] * 100 for r in rows),
    )


def grade(spec: Dict[str, Any], ruleset_path: str) -> Dict[str, Any]:
    from src.prop.ruleset import load_ruleset
    rs = load_ruleset(REPO / ruleset_path)
    n = spec["n"]
    net = sum(spec["r_samples"])
    out: Dict[str, Any] = dict(n=n, net_r=round(net, 3), span_days=spec["span_days"],
                               population=f"{spec['name']} ledger, {spec['span_days']}d, n={n}")
    if n < N_FLOOR:
        out.update(verdict="indeterminate", read_state="underpowered")
        return out
    kw = dict(n_paths=1500, risk_pct=RISK, reading="A", leverage=10.0, winner_mae_r=0.3,
              loser_mfe_r=0.3, flag_frac=FLAG)
    kw.update(p1_cap=CAP, p2_cap=CAP)
    b = [mc.run(dict(spec), rs, seed=s, **kw)["p_pass_both_phases"] for s in SEEDS]
    c = [mc.run(dict(spec, r_shift=SHIFT_C), rs, seed=s, **kw)["p_pass_both_phases"] for s in SEEDS]
    # INFORMATIONAL, not a gate: the same book if half its market fills are flagged (profit credit x0.4)
    info = mc.run(dict(spec), rs, seed=1, **dict(kw, flag_frac=0.5))["p_pass_both_phases"]
    out.update(p_both_gateB=b, p_both_gateC=c, info_p_both_if_half_of_fills_flagged=info)
    ok = net > 0 and min(b) >= P_B and min(c) >= P_C
    out.update(verdict="pass" if ok else "fail", read_state="graded",
               gates=dict(A_net_r_positive=net > 0, B_all_seeds_ge_0_25=min(b) >= P_B,
                          C_all_seeds_ge_0_10=min(c) >= P_C))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--leg", action="append", required=True)
    ap.add_argument("--days", type=int, default=1830)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ruleset", default="config/prop_rulesets/hyrotrader.yaml")
    ap.add_argument("--from-ledger", action="append", default=None,
                    help="SMOKE PATH: grade these committed ledgers instead of running the builder")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.from_ledger:
        ledgers = a.from_ledger
    else:
        cmd = [sys.executable, str(REPO / "scripts/ops/build_strategy_evidence.py"),
               "--days", str(a.days), "--out", str(out / "records"), "--workdir", str(out / "runs")]
        for leg in a.leg:
            cmd += ["--strategy", leg]
        r = subprocess.run(cmd, capture_output=True, text=True)
        (out / "builder.log").write_text(r.stdout + "\n" + r.stderr)
        ledgers = []
        for leg in a.leg:
            rec = out / "records" / f"{leg}.json"
            src = json.loads(rec.read_text()).get("source_run") if rec.exists() else None
            if not src or not Path(src).exists():
                v = dict(verdict="not_applicable", read_state="producer_failed", leg=leg,
                         population=f"{leg}: no ledger emitted (builder rc={r.returncode})")
                (out / "verdict.json").write_text(json.dumps(v, indent=1) + "\n")
                print(json.dumps(v))
                return 0
            ledgers.append(src)
    v = grade(spec_from_ledgers("+".join(a.leg), ledgers), a.ruleset)
    (out / "verdict.json").write_text(json.dumps(v, indent=1) + "\n")
    print(json.dumps(v, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
