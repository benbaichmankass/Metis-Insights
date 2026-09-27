#!/usr/bin/env python3
"""W6-PROP-R3 — prop-SHAPED walk-forward parameter search (research unit RQ-20260927-005).

Question (pre-registered in research/queue/RQ-20260927-005.yaml BEFORE this ran):
can trend_donchian / htf_pullback be re-tuned with the Breakout prop objective
(E[net-$ per account life], scripts/research/prop_ev_sim.py) as the IN-SAMPLE
selection criterion so that the walk-forward OUT-OF-SAMPLE stitched leg on
BTCUSDT / ETHUSDT / SOLUSDT clears RULE-B6-PROP-EV-PER-ACCOUNT-LIFE-V2 on every
seed, and improves the current two-leg breakout_1 portfolio?

Phases (each writes into --work, and is skipped when its output exists):
  cells   run every grid cell ONCE over the full candle history with the
          canonical argv (regime_debt_matrix.build_harness_cmd). Harnesses are
          causal, so time-splitting the trade list afterwards is look-ahead-free.
  select  per (family, symbol, fold): prop_ev_sim point EV (path, 1000 lives,
          outer 0, seed 20260927) on IS trades only (exit_time < OOS start).
  stitch  write the stitched OOS leg per (family, symbol) + the baseline legs
          restricted to the OOS span.
  grade   prop_ev_sim CLI at its full default MC size, 5 seeds, alone /
          baseline+candidate / baseline.
  r2      round-2 item: trend_donchian_eth evidence at full MC size, 5 seeds.

Tier-1 research tooling: reads candles + config, writes only under --work and
the --out JSON. No config write, no live path.

# wiring: manual-only - one-shot research run for RQ-20260927-005.
"""
from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "research"))

import prop_ev_sim as pes  # noqa: E402
from regime_debt_matrix import build_harness_cmd  # noqa: E402

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
FOLDS = [  # (name, OOS start inclusive, OOS end exclusive) — anchored expanding IS from data start
    ("F1", "2023-10-01", "2024-07-01"),
    ("F2", "2024-07-01", "2025-04-01"),
    ("F3", "2025-04-01", "2026-01-01"),
    ("F4", "2026-01-01", "2026-09-25"),
]
OOS_START, OOS_END = FOLDS[0][1], FOLDS[-1][2]
SEEDS = (20260927, 1, 2, 3, 4)
IS_SEED = 20260927
MIN_IS_TRADES = 30
POWER_FLOOR_N = 88
BASELINE = ("trend_donchian_eth_prop", "trend_donchian_sol_prop")
SIZING = ["--sizing", "room", "--room-frac", "0.33", "--min-risk", "10", "--risk-pct", "0.015",
          "--costs", "breakout", "--modes", "path"]


def grid():
    cells = []
    for sym in SYMBOLS:
        for i, (dc, sm, tm, tp, st) in enumerate(itertools.product(
                (20, 40), (1.5, 2.5), (2.0, 3.5), (3.0, 6.0), (None, 12))):
            cfg = {"symbols": [sym], "donchian": dc, "atr_period": 14, "atr_stop_mult": sm,
                   "trail_mult": tm, "tp_r": tp, "min_confidence": 0.6}
            if st is not None:
                cfg.update(stale_exit_bars=st, stale_exit_below_r=0.0)
            cells.append(("trend", sym, i, cfg))
        for i, (sm, tm, tp, st) in enumerate(itertools.product(
                (1.5, 2.5), (2.0, 3.5), (3.0, 6.0), (None, 6))):
            cfg = {"symbols": [sym], "trend_lookback": 40, "pullback_lookback": 10,
                   "pullback_frac": 0.5, "atr_period": 14, "atr_stop_mult": sm,
                   "trail_mult": tm, "tp_r": tp, "min_confidence": 0.0}
            if st is not None:
                cfg.update(stale_exit_bars=st, stale_exit_below_r=0.0)
            cells.append(("pullback", sym, i, cfg))
    return cells


def csv_for(sym: str) -> str:
    return str(REPO / "data" / "ohlcv" / f"{sym}_1h_w6r3.csv")


def cell_stem(work: Path, fam: str, sym: str, i: int) -> Path:
    return work / "cells" / f"{fam}_{sym}_{i:02d}"


def _run_cell(args):
    work, fam, sym, i, cfg = args
    stem = cell_stem(work, fam, sym, i)
    if Path(str(stem) + ".jsonl").exists():
        return fam, sym, i, "cached"
    argv, faithful, omitted = build_harness_cmd(
        f"{fam}_{sym}_{i}", cfg, fam, csv_for(sym), "1h" if fam == "trend" else "2h",
        str(stem) + ".jsonl", str(stem) + ".json")
    if not faithful:
        raise RuntimeError(f"unfaithful argv for {fam} {sym} {i}: {omitted}")
    r = subprocess.run(argv, capture_output=True, text=True, cwd=REPO)
    if r.returncode != 0:
        raise RuntimeError(f"{fam} {sym} {i}: {r.stderr[-1500:]}")
    Path(str(stem) + ".argv").write_text(" ".join(argv[1:]) + "\n")
    return fam, sym, i, "ran"


def load(path: Path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def ts(v):
    return pes._parse_ts(v)


def _day(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def is_rows(rows, oos_start):
    cut = _day(oos_start)
    return [r for r in rows if ts(r["exit_time"]) < cut]


def oos_rows(rows, a, b):
    lo, hi = _day(a), _day(b)
    return [r for r in rows if lo <= ts(r["entry_time"]) < hi]


RULES = None


def _point_ev(rows):
    rules = pes.PropRules.from_yaml(pes.DEFAULT_RULESET)
    costs = pes.CostConfig(mode="breakout", commission_bps_rt=8.0, slippage_bps_rt=3.0,
                           swap_daily=0.00033, swap_model="dxtrade")
    cfg = pes.SimConfig(risk_pct=0.015, sizing="room", room_frac=0.33, min_risk_usd=10.0,
                        start_balance=None, funded_start=rules.funded_start, approval_days=1.0,
                        horizon_days=730.0, block_days=30,
                        first_payout_refund=rules.first_payout_refund)
    trades, _, hist_days = pes.build_trades({"cell": rows}, costs)
    hist = pes.History(trades, hist_days)
    res = pes.run(hist, rules, cfg, modes=("path",), n_lives=1000, outer=0, seed=IS_SEED)
    return res["path"]["ev_net_usd_per_life"], res["path"]["ev_net_usd_per_life_mc_se"]


def _select_job(args):
    work, fam, sym, i, fold, oos_start = args
    rows = is_rows(load(Path(str(cell_stem(work, fam, sym, i)) + ".jsonl")), oos_start)
    if len(rows) < MIN_IS_TRADES:
        return fam, sym, i, fold, len(rows), None, None
    ev, se = _point_ev(rows)
    return fam, sym, i, fold, len(rows), ev, se


def net_r_breakout(rows):
    costs = pes.CostConfig(mode="breakout", commission_bps_rt=8.0, slippage_bps_rt=3.0,
                           swap_daily=0.00033, swap_model="dxtrade")
    if not rows:
        return 0.0
    trades, _, _ = pes.build_trades({"x": rows}, costs)
    return round(sum(t.net_r for t in trades), 3)


def run_prop(legs: dict, seed: int, out: Path, lives=4000, outer=100, lpo=200):
    if out.exists():
        return json.loads(out.read_text())
    argv = [sys.executable, str(REPO / "scripts/research/prop_ev_sim.py")]
    for leg, p in legs.items():
        argv += ["--trades", f"{leg}={p}"]
    argv += SIZING + ["--lives", str(lives), "--outer", str(outer), "--lives-per-outer", str(lpo),
                      "--seed", str(seed), "--out", str(out)]
    r = subprocess.run(argv, capture_output=True, text=True, cwd=REPO)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-1500:])
    return json.loads(out.read_text())


def _grade_job(args):
    legs, seed, out = args
    d = run_prop(legs, seed, Path(out))
    p = d["results"]["path"]
    return out, {"ev_point": p["ev_net_usd_per_life"], "ev_se": p["ev_net_usd_per_life_mc_se"],
                 **{k: p["evidence_ci"][k] for k in ("ev_net_usd_p5", "ev_net_usd_p50",
                                                     "ev_net_usd_p95", "p_ev_positive")},
                 "p_pass_eval": p["p_pass_eval"], "v2": d["decision_rule_v2"]["verdict"],
                 "n_trades": d["inputs"]["n_trades"], "legs": d["inputs"]["legs"]}


def _grade_job_light(args):
    legs, seed, out = args
    d = run_prop(legs, seed, Path(out), lives=1000, outer=30, lpo=100)
    p = d["results"]["path"]
    return out, {"ev_point": p["ev_net_usd_per_life"], "n_trades": d["inputs"]["n_trades"],
                 **{k: p["evidence_ci"][k] for k in ("ev_net_usd_p5", "ev_net_usd_p50", "ev_net_usd_p95")}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="/tmp/w6r3")
    ap.add_argument("--out", default=str(REPO / "docs/research/b6-prop-ev/w6-prop-shaped-search-2026-09-27.json"))
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--phase", default="all")
    a = ap.parse_args(argv)
    work = Path(a.work)
    (work / "cells").mkdir(parents=True, exist_ok=True)
    (work / "grade").mkdir(parents=True, exist_ok=True)
    phases = {"cells", "select", "stitch", "grade", "r2"} if a.phase == "all" else set(a.phase.split(","))
    cells = grid()
    by_key = {(f, s, i): c for f, s, i, c in cells}
    pool = ProcessPoolExecutor(a.jobs)

    if "cells" in phases:
        import yaml
        strat = yaml.safe_load((REPO / "config/strategies.yaml").read_text())["strategies"]
        jobs = [(work, f, s, i, c) for f, s, i, c in cells]
        jobs += [(work, "trend", strat[b]["symbols"][0], 90 + k, strat[b]) for k, b in enumerate(BASELINE)]
        for r in pool.map(_run_cell, jobs):
            pass
        print(f"cells: {len(jobs)} done", file=sys.stderr)

    sel_path = work / "selection.json"
    if "select" in phases and not sel_path.exists():
        jobs = [(work, f, s, i, fn, fs) for f, s, i, _ in cells for fn, fs, _ in FOLDS]
        rows = list(pool.map(_select_job, jobs, chunksize=4))
        sel_path.write_text(json.dumps(rows))
        print(f"select: {len(rows)} IS sims", file=sys.stderr)
    sel = json.loads(sel_path.read_text()) if sel_path.exists() else []

    summary = {"unit": "RQ-20260927-005", "rule": "RULE-RQ0927-005-PROP-SHAPED-WF",
               "folds": FOLDS, "seeds": SEEDS, "candidates": {}}
    cand_paths = {}
    if "stitch" in phases or "grade" in phases:
        for fam in ("trend", "pullback"):
            for sym in SYMBOLS:
                key = f"{fam}_{sym}"
                picks, stitched, per_fold = [], [], []
                for fn, fs, fe in FOLDS:
                    cand = [r for r in sel if r[0] == fam and r[1] == sym and r[3] == fn and r[5] is not None]
                    cand.sort(key=lambda r: (-r[5], r[2]))
                    best = cand[0]
                    i = best[2]
                    orows = oos_rows(load(Path(str(cell_stem(work, fam, sym, i)) + ".jsonl")), fs, fe)
                    for r in orows:
                        r["leg"] = key
                    stitched += orows
                    per_fold.append({"fold": fn, "oos": [fs, fe], "cell": i, "params": by_key[(fam, sym, i)],
                                     "is_n": best[4], "is_ev_point": best[5], "is_ev_se": best[6],
                                     "is_rank2_ev": cand[1][5] if len(cand) > 1 else None,
                                     "oos_n": len(orows), "oos_net_r_breakout": net_r_breakout(orows)})
                    picks.append(i)
                stitched.sort(key=lambda r: r["entry_time"])
                p = work / "grade" / f"cand_{key}.jsonl"
                p.write_text("".join(json.dumps(r) + "\n" for r in stitched))
                cand_paths[key] = p
                summary["candidates"][key] = {"per_fold": per_fold, "oos_n": len(stitched),
                                              "oos_net_r_breakout": net_r_breakout(stitched)}
        base_paths = {}
        for k, b in enumerate(BASELINE):
            sym = "ETHUSDT" if "eth" in b else "SOLUSDT"
            rows = oos_rows(load(Path(str(cell_stem(work, "trend", sym, 90 + k)) + ".jsonl")), OOS_START, OOS_END)
            p = work / "grade" / f"base_{b}.jsonl"
            p.write_text("".join(json.dumps(r) + "\n" for r in rows))
            base_paths[b] = p
            summary.setdefault("baseline", {})[b] = {"oos_n": len(rows), "oos_net_r_breakout": net_r_breakout(rows)}

    if "grade" in phases:
        jobs = []
        for s in SEEDS:
            jobs.append(({b: str(p) for b, p in base_paths.items()}, s, str(work / "grade" / f"BASE_s{s}.json")))
            for key, p in cand_paths.items():
                jobs.append(({key: str(p)}, s, str(work / "grade" / f"{key}_alone_s{s}.json")))
                jobs.append(({key: str(p), **{b: str(q) for b, q in base_paths.items()}}, s,
                             str(work / "grade" / f"{key}_plusbase_s{s}.json")))
        res = dict(pool.map(_grade_job, jobs))
        for s in SEEDS:
            summary.setdefault("baseline_grades", {})[str(s)] = res[str(work / "grade" / f"BASE_s{s}.json")]
        for key, c in summary["candidates"].items():
            c["alone"] = {str(s): res[str(work / "grade" / f"{key}_alone_s{s}.json")] for s in SEEDS}
            c["plus_base"] = {str(s): res[str(work / "grade" / f"{key}_plusbase_s{s}.json")] for s in SEEDS}
            a_pass = all(c["alone"][str(s)]["ev_net_usd_p5"] > 0 for s in SEEDS)
            port = all(c["plus_base"][str(s)]["ev_net_usd_p5"] > summary["baseline_grades"][str(s)]["ev_net_usd_p5"]
                       and c["plus_base"][str(s)]["ev_point"] > summary["baseline_grades"][str(s)]["ev_point"]
                       for s in SEEDS)
            fail = any(c["alone"][str(s)]["ev_net_usd_p95"] < 0 for s in SEEDS)
            mrob = all(c["alone"][str(s)]["p_ev_positive"] >= 0.99 for s in SEEDS)
            if c["oos_n"] < POWER_FLOOR_N:
                v = "UNDERPOWERED"
            elif a_pass and port:
                v = "PASS" + ("" if mrob else " (primary rule; not multiplicity_robust)")
            elif a_pass:
                v = "STANDALONE_ONLY"
            elif fail:
                v = "FAIL"
            else:
                v = "NULL_INDETERMINATE"
            c["alone_seeds_p5_pos"] = sum(c["alone"][str(s)]["ev_net_usd_p5"] > 0 for s in SEEDS)
            c["portfolio_seeds_improved"] = sum(
                c["plus_base"][str(s)]["ev_net_usd_p5"] > summary["baseline_grades"][str(s)]["ev_net_usd_p5"]
                and c["plus_base"][str(s)]["ev_point"] > summary["baseline_grades"][str(s)]["ev_point"]
                for s in SEEDS)
            c["verdict"] = v

    if "r2" in phases:
        ev = REPO / "comms/strategy_evidence"
        def rows_of(leg):
            rec = json.loads((ev / f"{leg}.json").read_text())
            return str(REPO / rec["source_run"]) if rec.get("source_run") else None
        tde = str(REPO / "comms/strategy_evidence/runs/2026-09-25/trend_donchian_eth__trades.jsonl")
        base = {b: rows_of(b) for b in BASELINE}
        jobs = []
        for s in SEEDS:
            jobs.append(({"trend_donchian_eth": tde}, s, str(work / "grade" / f"R2_alone_s{s}.json")))
            jobs.append(({"trend_donchian_eth": tde, **base}, s, str(work / "grade" / f"R2_plusbase_s{s}.json")))
        res = dict(pool.map(_grade_job, jobs))
        summary["round2_trend_donchian_eth_full_mc"] = {
            "inputs": {"trend_donchian_eth": tde, **base},
            "alone": {str(s): res[str(work / "grade" / f"R2_alone_s{s}.json")] for s in SEEDS},
            "plus_base": {str(s): res[str(work / "grade" / f"R2_plusbase_s{s}.json")] for s in SEEDS},
        }

    # DESCRIPTIVE ONLY, added AFTER the registered grade ran (never enters the verdict):
    # per-fold alone grades (light MC, one seed) and the POST-HOC replacement arm.
    if "perfold" in phases:
        jobs = []
        for key in ("trend_BTCUSDT", "trend_ETHUSDT", "trend_SOLUSDT",
                    "pullback_BTCUSDT", "pullback_ETHUSDT", "pullback_SOLUSDT"):
            rows = load(work / "grade" / f"cand_{key}.jsonl")
            for fn, fs, fe in FOLDS:
                p = work / "grade" / f"fold_{key}_{fn}.jsonl"
                p.write_text("".join(json.dumps(r) + "\n" for r in oos_rows(rows, fs, fe)))
                jobs.append(({key: str(p)}, IS_SEED, str(work / "grade" / f"PF_{key}_{fn}.json")))
        res = dict(pool.map(_grade_job_light, jobs))
        summary["perfold_alone_descriptive"] = {k.split("/")[-1]: v for k, v in res.items()}
    if "posthoc" in phases:
        eth = work / "grade" / "cand_trend_ETHUSDT.jsonl"
        sol = work / "grade" / "base_trend_donchian_sol_prop.jsonl"
        jobs = [({"trend_ETHUSDT": str(eth), "trend_donchian_sol_prop": str(sol)}, s,
                 str(work / "grade" / f"POSTHOC_replace_s{s}.json")) for s in SEEDS]
        res = dict(pool.map(_grade_job, jobs))
        summary["posthoc_replacement_descriptive"] = {str(s): res[str(work / "grade" / f"POSTHOC_replace_s{s}.json")]
                                                      for s in SEEDS}
    if a.out and phases & {"perfold", "posthoc"} and not phases & {"grade", "r2"}:
        prev = json.loads(Path(a.out).read_text())
        for k in ("perfold_alone_descriptive", "posthoc_replacement_descriptive"):
            if k in summary:
                prev[k] = summary[k]
        Path(a.out).write_text(json.dumps(prev, indent=1, default=str) + "\n")

    if a.out and "grade" in phases:
        prev = json.loads(Path(a.out).read_text()) if Path(a.out).exists() else {}
        prev.update(summary)
        Path(a.out).write_text(json.dumps(prev, indent=1, default=str) + "\n")
    elif a.out and "r2" in phases:
        prev = json.loads(Path(a.out).read_text()) if Path(a.out).exists() else {}
        prev["round2_trend_donchian_eth_full_mc"] = summary["round2_trend_donchian_eth_full_mc"]
        Path(a.out).write_text(json.dumps(prev, indent=1, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
