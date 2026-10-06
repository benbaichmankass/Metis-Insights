#!/usr/bin/env python3
"""VELOTRADE-FIT — which roster, at which risk, can pass Velotrade's CLASSIC 1-Step $5k evaluation?

Executes the rule registered in research/queue/RQ-20261006-002.yaml
(RULE-RQ1006-VELOTRADE-FIT) BEFORE any run. Nothing here changes a threshold;
the constants below are copied from that unit and move only with it.

Per (book, risk arm) this runs scripts/research/prop_ev_sim.py under
config/prop_rulesets/velotrade_classic_1step.yaml with VELOTRADE's own cost
stack (commission 0.03% per side = 6 bps round trip, slippage 3 bps round trip,
swap 0.05% of notional per 00:30-UTC crossing — the `dxtrade` model), one seed
per cell, and grades the cell on four registered clauses:

  (G) inactivity: union-ledger max entry gap <= 14 d  (prop_book_search.book_max_gap)
  (D) daily loss: no 00:30-reset day of realised net-R x risk$ beyond 80% of $200
  (S) edge:       ev_net_usd_per_life > 0 AND p_net_positive >= 0.70 on every seed
  (Q) evaluation: p_pass_eval >= 0.50 on every seed (the median account life passes)

and reports — never grades on — the qualifying-day measurement the simulator
now exposes (results.path.qualifying_days): P(target reached), P(pass | target
reached), the share of lives that reached +10% and still never passed, and the
median days from target to pass.

Usage:
    python3 scripts/research/velotrade_fit_sweep.py --unit RQ-20261006-002 --out comms/research/RQ-20261006-002
    python3 scripts/research/velotrade_fit_sweep.py --self-test
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SIM = REPO / "scripts" / "research" / "prop_ev_sim.py"
RULESET = "config/prop_rulesets/velotrade_classic_1step.yaml"
TOOL = "scripts/research/velotrade_fit_sweep.py"

RULE_ID = "RULE-RQ1006-VELOTRADE-FIT"
RULE_REGISTERED_AT = "2026-10-06"
MAX_GAP_DAYS = 14.0
LOSS_LINE_FRAC = 0.80
P_NET_FLOOR = 0.70
P_PASS_FLOOR = 0.50
SEEDS = (1, 2, 3, 4, 5)
RISKS = (0.005, 0.01, 0.015)
ACCOUNT = 5000.0
DAILY_LIMIT_USD = 0.04 * ACCOUNT          # $200
DAY_RESET = "00:30"
LEV_CAPS = {"ETHUSDT": 5, "SOLUSDT": 5, "XRPUSDT": 2}
# Velotrade cost stack (ruleset header, first-party 2026-10-04): 0.03%/side commission,
# 0.05%/night swap at 00:30 UTC. Slippage is the harness default (3 bps rt), unchanged.
COSTS = ["--commission-bps-rt", "6", "--slippage-bps-rt", "3", "--swap-daily", "0.0005", "--swap-model", "dxtrade"]
DEFAULT_LEGS = {
    "trend_donchian_eth_prop": "comms/strategy_evidence/runs/2026-10-05-730d/trend_donchian_eth_prop__trades.jsonl",
    "trend_donchian_sol_prop": "comms/strategy_evidence/runs/2026-10-05-730d/trend_donchian_sol_prop__trades.jsonl",
}
CLASS_ORDER = {"PASS": 0, "EVAL_FIT": 1, "NULL": 2, "FAIL": 3, "NOT_APPLICABLE": 4}


def _load_book_search():
    spec = importlib.util.spec_from_file_location("prop_book_search", REPO / "scripts" / "research" / "prop_book_search.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sim_cell(legs: dict[str, Path], symbols: dict[str, str], risk: float, seed: int, out: Path,
             lives: int, outer: int, lives_per_outer: int) -> dict:
    argv = [sys.executable, str(SIM)]
    for k, p in legs.items():
        argv += ["--trades", f"{k}={p}", "--lev-cap", f"{k}={LEV_CAPS[symbols[k]]}"]
    argv += ["--ruleset", RULESET, "--no-hedge", "--funded-start", "fresh", "--sizing", "balance",
             "--risk-pct", str(risk), "--modes", "path", "--seed", str(seed),
             "--lives", str(lives), "--outer", str(outer), "--lives-per-outer", str(lives_per_outer),
             *COSTS, "--out", str(out)]
    d = None
    if out.exists():
        # resume: a cell already simulated on disk is re-read, not re-run, so a re-invocation
        # after an interrupted sweep (or a driver-only change) costs seconds, not CPU-hours
        try:
            d = json.loads(out.read_text())
        except ValueError:
            d = None
    if d is None:
        p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, check=False)
        if p.returncode != 0:
            return {"error": p.stderr[-400:]}
        d = json.loads(out.read_text())
    blk = d["results"]["path"]
    return {
        "ev": blk["ev_net_usd_per_life"], "ev_mc_se": blk["ev_net_usd_per_life_mc_se"],
        "p_net": blk["p_net_positive"], "p_pass": blk["p_pass_eval"],
        "ev_p5": blk["evidence_ci"]["ev_net_usd_p5"], "ev_p95": blk["evidence_ci"]["ev_net_usd_p95"],
        "p_pass_p5": blk["evidence_ci"]["p_pass_eval_p5"],
        "days_to_pass_p50": (blk["days_to_pass"] or {}).get("p50"),
        "days_to_pass_p90": (blk["days_to_pass"] or {}).get("p90"),
        "lifetime_days_mean": blk["lifetime_days"]["mean"],
        # the 730d horizon truncates an UNLIMITED-time evaluation: a life still in eval at the
        # horizon counts as not passed, so P(pass) is a 2-year lower bound where this is large
        "p_alive_at_horizon": blk["p_alive_at_horizon"],
        "p_death_before_first_payout": blk["p_death_before_first_payout"],
        "death_causes": blk["death_causes"],
        "qualifying_days": blk["qualifying_days"],
        "payout_deferred_qual_days_per_life": round(blk["counters"].get("payout_deferred_qual_days", 0) / blk["n_lives"], 2),
        "pass_deferred_qual_days_per_life": round(blk["counters"].get("pass_deferred_qual_days", 0) / blk["n_lives"], 2),
        "mean_payouts_per_life": blk["mean_payouts_per_life"],
        "n_lives": blk["n_lives"],
    }


def grade(cells: dict, gap_ok: bool, loss_ok: bool) -> tuple[str, str]:
    """The registered rule. Returns (class, binding clause or '')."""
    if any("error" in c for c in cells.values()) or len(cells) != len(SEEDS):
        return "NOT_APPLICABLE", "sim"
    evs = [c["ev"] for c in cells.values()]
    pn = [c["p_net"] for c in cells.values()]
    pp = [c["p_pass"] for c in cells.values()]
    if min(evs) <= 0:
        return "FAIL", "S:ev"
    q_ok, s_ok = min(pp) >= P_PASS_FLOOR, min(pn) >= P_NET_FLOOR
    if gap_ok and loss_ok and s_ok and q_ok:
        return "PASS", ""
    if gap_ok and loss_ok and q_ok:
        return "EVAL_FIT", "S:p_net"
    # the first failing clause, in the registered order Q, S, D, G
    for name, ok in (("Q:p_pass", q_ok), ("S:p_net", s_ok), ("D:daily_loss", loss_ok), ("G:gap", gap_ok)):
        if not ok:
            return "NULL", name
    return "NULL", "?"


def rank_key(ent: dict) -> tuple:
    # class, then min-seed P(net>0) desc, then mean EV desc, then LOWER risk first
    return (CLASS_ORDER[ent["verdict"]], -(ent.get("min_p_net") or -1.0), -(ent.get("mean_ev") or -1e9), ent["risk"])


def _self_test() -> int:
    good = {s: {"ev": 10.0, "p_net": 0.75, "p_pass": 0.6} for s in SEEDS}
    assert grade(good, True, True) == ("PASS", "")
    assert grade({s: dict(good[s], p_net=0.5) for s in SEEDS}, True, True) == ("EVAL_FIT", "S:p_net")
    assert grade({s: dict(good[s], p_pass=0.4) for s in SEEDS}, True, True) == ("NULL", "Q:p_pass")
    assert grade({s: dict(good[s], p_pass=0.4, p_net=0.5) for s in SEEDS}, True, True) == ("NULL", "Q:p_pass")
    assert grade(good, True, False) == ("NULL", "D:daily_loss")
    assert grade(good, False, True) == ("NULL", "G:gap")
    assert grade({s: dict(good[s], ev=(-1.0 if s == 3 else 10.0)) for s in SEEDS}, True, True) == ("FAIL", "S:ev")
    assert grade({s: good[s] for s in SEEDS[:4]}, True, True)[0] == "NOT_APPLICABLE"
    a = {"verdict": "EVAL_FIT", "min_p_net": 0.5, "mean_ev": 100, "risk": 0.01}
    b = {"verdict": "EVAL_FIT", "min_p_net": 0.5, "mean_ev": 100, "risk": 0.005}
    c = {"verdict": "NULL", "min_p_net": 0.9, "mean_ev": 900, "risk": 0.005}
    assert [e["risk"] for e in sorted([a, b, c], key=rank_key)] == [0.005, 0.01, 0.005]
    print("self-test ok")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--unit", default="RQ-20261006-002")
    ap.add_argument("--legs", nargs="*", metavar="LEG=PATH", default=[f"{k}={v}" for k, v in DEFAULT_LEGS.items()])
    ap.add_argument("--risks", nargs="*", type=float, default=list(RISKS))
    ap.add_argument("--seeds", nargs="*", type=int, default=list(SEEDS))
    ap.add_argument("--lives", type=int, default=2000)
    ap.add_argument("--outer", type=int, default=50)
    ap.add_argument("--lives-per-outer", type=int, default=100)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", help="artifact dir, e.g. comms/research/<unit>")
    ap.add_argument("--run-id", default="velotrade-fit-002")
    ap.add_argument("--emit-result", action="store_true", help="land research/results/<unit>/<run_id>.jsonl")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.out:
        ap.error("--out is required")
    bs = _load_book_search()
    legs = {k: Path(v) for k, v in (x.split("=", 1) for x in a.legs)}
    rows = {k: bs.load(REPO / p) for k, p in legs.items()}
    sym = {k: r[0]["symbol"] for k, r in rows.items()}
    allts = [bs._ts(r["entry_time"]) for rr in rows.values() for r in rr]
    start, end = min(allts), max(allts)
    out = Path(a.out)
    (out / "sims").mkdir(parents=True, exist_ok=True)
    report = {
        "research_unit": a.unit, "decision_rule": {"id": RULE_ID, "registered_at": RULE_REGISTERED_AT},
        "ruleset": RULESET, "costs": COSTS, "window": [start.isoformat(), end.isoformat()],
        "sim_size": {"lives": a.lives, "outer": a.outer, "lives_per_outer": a.lives_per_outer, "seeds": a.seeds},
        "legs": {k: {"symbol": sym[k], "n": len(rows[k]), "trades_file": str(legs[k]),
                     "min_hold_s": bs.min_hold_seconds(rows[k]),
                     "solo_max_gap_days": round(bs.book_max_gap(rows[k], start, end), 2)} for k in legs},
        "cells": [],
    }
    tasks = []
    for b in bs.books(sym):
        union = [r for k in b for r in rows[k]]
        gap = round(bs.book_max_gap(union, start, end), 2)
        for risk in a.risks:
            dl = bs.daily_loss_check({k: rows[k] for k in b}, risk, ACCOUNT, DAILY_LIMIT_USD, DAY_RESET)
            ent = {"book": list(b), "risk": risk, "max_gap_days": gap, "gap_ok": gap <= MAX_GAP_DAYS,
                   "min_hold_s": bs.min_hold_seconds(union), "daily_loss": dl, "seeds": {}}
            report["cells"].append(ent)
            for s in a.seeds:
                tasks.append((ent, s))

    def run(t):
        ent, s = t
        name = "+".join(ent["book"]) + f"__r{ent['risk']}__s{s}.json"
        ent["seeds"][s] = sim_cell({k: legs[k] for k in ent["book"]}, sym, ent["risk"], s, out / "sims" / name,
                                   a.lives, a.outer, a.lives_per_outer)

    with ThreadPoolExecutor(a.jobs) as ex:
        list(ex.map(run, tasks))
    for ent in report["cells"]:
        cells = ent["seeds"]
        ok = [c for c in cells.values() if "error" not in c]
        if ok:
            ent["min_p_net"] = min(c["p_net"] for c in ok)
            ent["min_p_pass"] = min(c["p_pass"] for c in ok)
            ent["mean_ev"] = round(sum(c["ev"] for c in ok) / len(ok), 2)
            ent["mean_p_pass"] = round(sum(c["p_pass"] for c in ok) / len(ok), 4)
            ent["qual_gate"] = {
                "mean_p_target_reached": round(sum(c["qualifying_days"]["p_target_reached_eval"] for c in ok) / len(ok), 4),
                "mean_p_target_reached_not_passed": round(sum(c["qualifying_days"]["p_target_reached_not_passed"] for c in ok) / len(ok), 4),
                "days_target_to_pass_p50": [c["qualifying_days"]["days_target_to_pass"] and c["qualifying_days"]["days_target_to_pass"]["p50"] for c in ok],
                "payout_deferred_qual_days_per_life": [c["payout_deferred_qual_days_per_life"] for c in ok],
            }
        ent["verdict"], ent["binding_clause"] = grade(cells, ent["gap_ok"], ent["daily_loss"]["ok"])
    ranked = sorted(report["cells"], key=rank_key)
    best = ranked[0]
    report["ranking"] = [{"book": e["book"], "risk": e["risk"], "verdict": e["verdict"], "binding_clause": e["binding_clause"],
                          "min_p_net": e.get("min_p_net"), "min_p_pass": e.get("min_p_pass"), "mean_ev": e.get("mean_ev")}
                         for e in ranked]
    report["recommendation"] = (
        {"roster": best["book"], "risk_pct": best["risk"], "class": best["verdict"]}
        if best["verdict"] in ("PASS", "EVAL_FIT") else
        {"roster": None, "class": best["verdict"], "binding_constraint": best["binding_clause"],
         "best_cell": {"book": best["book"], "risk": best["risk"]}})
    (out / "fit.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({"cells": len(report["cells"]), "sim_cells": len(tasks), "recommendation": report["recommendation"]}))

    if a.emit_result:
        sys.path.insert(0, str(REPO / "scripts" / "research"))
        import research_result as rr
        verdict = {"PASS": "pass", "FAIL": "fail"}.get(best["verdict"], "indeterminate")
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
        n = sum(len(r) for r in rows.values())
        rec = rr.build(
            research_unit=a.unit, decision_rule_id=RULE_ID, decision_rule_registered_at=RULE_REGISTERED_AT,
            verdict=verdict, read_state="measured", power_state="powered",
            population_description=(f"730d committed trade records ({', '.join(f'{k} n={len(rows[k])}' for k in rows)}), "
                                    f"{len(report['cells'])} (book x risk) cells x {len(a.seeds)} seeds of prop_ev_sim path mode, "
                                    f"{a.lives} lives + {a.outer}x{a.lives_per_outer} evidence bootstrap per cell, Velotrade cost stack"),
            n=n, workflow="session-local", run_id=a.run_id, commit_sha=sha, tool=TOOL,
            measurement={"recommendation": report["recommendation"], "ranking": report["ranking"][:6],
                         "summary": "; ".join(f"{'+'.join(e['book'])} @{e['risk']}: {e['verdict']} "
                                              f"(minP(net>0) {e.get('min_p_net')}, minP(pass) {e.get('min_p_pass')}, EV {e.get('mean_ev')})"
                                              for e in ranked[:4])},
            artifact_store=str(out), artifact_locator="fit.json (per-seed cells under sims/)",
            rows_landed=len(report["cells"]),
            note=(f"{RULE_ID}: best class {best['verdict']}"
                  + (f", binding clause {best['binding_clause']}" if best["binding_clause"] else "")
                  + ". Classes: PASS = G+D+S+Q; EVAL_FIT = G+D+Q with EV>0 but P(net>0)<0.70 (not a pass; "
                    "the roster is expected to clear the evaluation, the account-life EV bar is unmet); "
                    "NULL/FAIL otherwise. Does not edit config/accounts.yaml."),
        )
        path = rr.write([rec], research_unit=a.unit, run_id=a.run_id)
        print(f"landed result: {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
