#!/usr/bin/env python3
"""HyroTrader pass-probability Monte Carlo (lane HYRO-STRATS, Tier-1 research).

Why a lane-local script and not ``src.prop.montecarlo.run_montecarlo``: that
module (read 2026-09-30) models a STATIC drawdown floor + a REALISED-only daily
loss on a per-trade block bootstrap. HyroTrader adds four things it cannot
express -- (1) a TRAILING daily drawdown from the day's peak *equity* (floating
P&L counts), (2) the 5-QUALIFYING-day minimum, (3) the 40%-single-day
consistency cap in phases 1-2, (4) the demo-realism haircut on flagged fills.
Sanity controls live in ``tests/test_hyro_passprob_mc.py`` (zero edge, huge
edge, monotonicity in risk) rather than as a cross-check against
``run_montecarlo``, whose realised-only daily rule is a different model.

Reads the ruleset from ``config/prop_rulesets/hyrotrader*.yaml`` (extra keys in
``PropRuleset.raw``). No network, no live-path imports, deterministic per seed.

INPUT is a candidate spec JSON (see ``docs/research/hyro-strategy-feasibility-2026-09-30.md``
Appendix A). Each candidate carries EITHER ``r_samples`` (per-trade net R, from a
committed ledger) OR a parametric ``{win_rate, win_r, loss_r}`` when only summary
stats are committed -- the output states which, because a parametric run rests
on the summary, not the trade distribution.

Model (what is and is NOT simulated -- stated, not hidden):
  * Days are UTC days. Trades per day ~ Poisson(trades_per_day); trades on a
    day run sequentially (no overlap), each opens and closes the same day with
    probability ``same_day_frac`` (else it is carried and its P&L lands on a
    later day and it is NOT a qualifying trade).
  * Sizing: each trade risks ``risk_pct`` of CURRENT balance at a stop of
    ``stop_pct`` (% of price) => notional = risk/stop.
  * Intratrade excursion is NOT in the ledgers. Assumption (labelled): a loser
    reaches -1R after an adverse-only path (peak +``loser_mfe_r``); a winner
    draws an adverse excursion ``winner_mae_r`` * U(0,2) before finishing at +R.
    These set the FLOATING-equity terms of the daily/max drawdown. Sensitivity
    on winner_mae_r is reported.
  * Qualifying trade: same-day, notional >= 5% of initial, and |pnl|/value >= 1%
    where value = notional (reading A, strict: needs a >=1% price move) or
    margin = notional/leverage (reading B).
  * Consistency (phases 1-2): each day's counted profit is capped at
    ``0.40 * target`` (the firm's own worked example, deep dive s1.5); losses
    count in full.
  * Demo-realism: a fraction ``flag_frac`` of trades (market orders) are
    flagged; their POSITIVE pnl counts 40% toward the target (equity is real).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.prop.ruleset import load_ruleset  # noqa: E402


def _draw_r(spec: Dict[str, Any], rng: np.random.Generator, state: Dict[str, Any]) -> float:
    """One trade's net R. Block-bootstrap when a ledger exists, else 2-point."""
    if spec.get("r_samples"):
        r = spec["r_samples"]
        blk = int(spec.get("block_len", 4))
        if state.get("left", 0) <= 0:
            state["i"] = int(rng.integers(0, len(r)))
            state["left"] = blk
        v = r[state["i"] % len(r)]
        state["i"] += 1
        state["left"] -= 1
        return float(v) + float(spec.get("r_shift", 0.0))
    wr, wR, lR = float(spec["win_rate"]), float(spec["win_r"]), float(spec["loss_r"])
    return wR if rng.random() < wr else -abs(lR)


def _phase(spec, rs, *, target, days_cap, rng, risk_pct, initial, dd_daily, dd_max,
           consistency, flag_frac, reading, leverage, winner_mae_r, loser_mfe_r,
           need_qual, capped_day_share=0.40):
    """Walk one phase. Returns dict(result, days, qual_days, bal)."""
    bal = initial
    prog = 0.0                        # counted progress toward target (haircut + cap applied)
    day_prog: Dict[int, float] = {}
    qual = set()
    state: Dict[str, Any] = {}
    tpd = float(spec["trades_per_day"])
    p_same = float(spec.get("same_day_frac", 1.0))
    stop = float(spec["stop_pct"]) / 100.0
    floor = initial * (1 - dd_max)
    for day in range(days_cap):
        day_open = bal
        day_peak = bal
        n = rng.poisson(tpd)
        for _ in range(n):
            R = _draw_r(spec, rng, state)
            risk = risk_pct / 100.0 * bal
            notional = risk / stop
            pnl = R * risk
            if R >= 0:
                worst = bal - winner_mae_r * rng.uniform(0, 2) * risk
                best = bal + pnl
            else:
                worst = bal + pnl
                best = bal + loser_mfe_r * risk
            # floating-equity checks (trailing daily from day peak; static max).
            # EVENT ORDER matters: a winner's adverse excursion (worst) happens BEFORE
            # its final peak, a loser's small favourable excursion (best) happens
            # BEFORE its trough. Crediting a winner's peak first measured a +5R win
            # as a 5% intraday drawdown (fixed 2026-09-30, caught on the trend ledger).
            if R < 0:
                day_peak = max(day_peak, best)
            if day_peak - worst > dd_daily * initial + 1e-9:
                return dict(result="breach_daily", days=day + 1, qual=len(qual), bal=bal)
            if worst <= floor + 1e-9:
                return dict(result="breach_max", days=day + 1, qual=len(qual), bal=bal)
            bal += pnl
            day_peak = max(day_peak, best, bal)
            same = rng.random() < p_same
            value = notional if reading == "A" else notional / leverage
            if same and notional >= 0.05 * initial and value > 0 and abs(pnl) / value >= 0.01:
                qual.add(day)
            credit = pnl
            if pnl > 0 and rng.random() < flag_frac:
                credit = 0.4 * pnl
            day_prog[day] = day_prog.get(day, 0.0) + credit
        # settle the day: cap this day's counted profit at 40% of target (phases 1-2)
        d = day_prog.get(day, 0.0)
        if consistency and d > capped_day_share * target:
            day_prog[day] = capped_day_share * target
        prog = sum(day_prog.values())
        if prog >= target and len(qual) >= need_qual:
            return dict(result="pass", days=day + 1, qual=len(qual), bal=bal)
    return dict(result="timeout", days=days_cap, qual=len(qual), bal=bal)


def run(spec: Dict[str, Any], rs, *, n_paths: int, seed: int, risk_pct: float, reading: str,
        leverage: float, winner_mae_r: float, loser_mfe_r: float, flag_frac: float,
        p1_cap: int = 180, p2_cap: int = 180, funded_days: int = 90) -> Dict[str, Any]:
    rng = np.random.default_rng(seed)
    initial = float(rs.account_size_usd)
    t1 = (rs.evaluation.profit_target_pct or 0.10) * initial
    t2 = float(rs.raw.get("phase2_target_pct", 0.05)) * initial
    dd_d = float(rs.limits.daily_loss_pct)
    dd_m = float(rs.limits.max_drawdown_pct)
    need = int(rs.evaluation.min_trading_days)
    kw = dict(risk_pct=risk_pct, initial=initial, dd_daily=dd_d, dd_max=dd_m,
              flag_frac=flag_frac, reading=reading, leverage=leverage,
              winner_mae_r=winner_mae_r, loser_mfe_r=loser_mfe_r)
    out = dict(b_daily=0, b_max=0, p1=0, p1_breach=0, p1_timeout=0, both=0, p2_breach=0, p2_timeout=0,
               d1=[], d2=[], fund_surv=0, fund_ret=[], q1=[])
    for _ in range(n_paths):
        r1 = _phase(spec, rs, target=t1, days_cap=p1_cap, rng=rng, consistency=True,
                    need_qual=need, **kw)
        out["q1"].append(r1["qual"])
        if r1["result"] == "breach_daily":
            out["b_daily"] += 1
        elif r1["result"] == "breach_max":
            out["b_max"] += 1
        if r1["result"] != "pass":
            out["p1_breach" if r1["result"].startswith("breach") else "p1_timeout"] += 1
            continue
        out["p1"] += 1
        out["d1"].append(r1["days"])
        r2 = _phase(spec, rs, target=t2, days_cap=p2_cap, rng=rng, consistency=True,
                    need_qual=need, **kw)
        if r2["result"] != "pass":
            out["p2_breach" if r2["result"].startswith("breach") else "p2_timeout"] += 1
            continue
        out["both"] += 1
        out["d2"].append(r2["days"])
        # funded: no target, no consistency, no qualifying-day need; survive `funded_days`
        rf = _phase(spec, rs, target=1e18, days_cap=funded_days, rng=rng, consistency=False,
                    need_qual=0, **kw)
        if rf["result"] == "timeout":
            out["fund_surv"] += 1
        out["fund_ret"].append(rf["bal"] / initial - 1.0)
    n = float(n_paths)
    both = max(out["both"], 1)
    med = lambda a: float(np.median(a)) if a else None  # noqa: E731
    return {
        "n_paths": n_paths, "cap_days": p1_cap, "risk_pct": risk_pct, "reading": reading,
        "winner_mae_r": winner_mae_r, "flag_frac": flag_frac,
        "p_pass_phase1": round(out["p1"] / n, 4),
        "p_phase1_breach": round(out["p1_breach"] / n, 4),
        "p_phase1_breach_daily_trailing": round(out["b_daily"] / n, 4),
        "p_phase1_breach_max_static": round(out["b_max"] / n, 4),
        "p_phase1_timeout_at_cap": round(out["p1_timeout"] / n, 4),
        "p_pass_both_phases": round(out["both"] / n, 4),
        "p_phase2_breach_given_p1": round(out["p2_breach"] / max(out["p1"], 1), 4),
        "median_days_phase1": med(out["d1"]), "median_days_phase2": med(out["d2"]),
        "median_qualifying_days_at_phase1_end": med(out["q1"]),
        "p_funded_survive_90d_given_funded": round(out["fund_surv"] / both, 4),
        "funded_90d_return_mean_given_funded": (round(float(np.mean(out["fund_ret"])), 4)
                                                if out["fund_ret"] else None),
        "n_funded_paths": out["both"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--spec", required=True, help="candidate spec JSON (list of candidates)")
    ap.add_argument("--ruleset", default="config/prop_rulesets/hyrotrader.yaml")
    ap.add_argument("--paths", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=20260930)
    ap.add_argument("--risks", default="0.5,1.0")
    ap.add_argument("--readings", default="A,B")
    ap.add_argument("--leverage", type=float, default=10.0)
    ap.add_argument("--winner-mae-r", default="0.3")
    ap.add_argument("--loser-mfe-r", type=float, default=0.3)
    ap.add_argument("--flag-fracs", default="0.0,1.0")
    ap.add_argument("--r-shifts", default="0.0",
                    help="edge-haircut sensitivity: added to every sampled R (e.g. -0.03)")
    ap.add_argument("--cap-days", type=int, default=180,
                    help="per-phase horizon (the firm sets NO time limit; timeouts are not failures)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rs = load_ruleset(REPO / a.ruleset if not Path(a.ruleset).is_absolute() else a.ruleset)
    cands = json.loads(Path(a.spec).read_text())
    rows: List[Dict[str, Any]] = []
    for c in cands:
        for risk in [float(x) for x in a.risks.split(",")]:
            for rd in a.readings.split(","):
                for mae in [float(x) for x in a.winner_mae_r.split(",")]:
                    for ff in [float(x) for x in a.flag_fracs.split(",")]:
                        for shift in [float(x) for x in a.r_shifts.split(",")]:
                          c2 = dict(c, r_shift=shift)
                          res = run(c2, rs, n_paths=a.paths, seed=a.seed, risk_pct=risk, reading=rd,
                                  leverage=a.leverage, winner_mae_r=mae,
                                  loser_mfe_r=a.loser_mfe_r, flag_frac=ff,
                                  p1_cap=a.cap_days, p2_cap=a.cap_days)
                          res.update(candidate=c["name"], ruleset=rs.ruleset, r_shift=shift,
                                     basis=("ledger n=%d" % len(c["r_samples"]) if c.get("r_samples")
                                            else "summary-stats(parametric)"))
                          rows.append(res)
    txt = json.dumps(rows, indent=1)
    if a.out:
        Path(a.out).write_text(txt + "\n")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
