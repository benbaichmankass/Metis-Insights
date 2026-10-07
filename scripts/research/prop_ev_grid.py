#!/usr/bin/env python3
# wiring: research/queue/RQ-20260928-011/-012/-013/-015 via .github/workflows/research-script-run.yml
"""Run a (leg x arm x seed) grid of ``prop_ev_sim.py`` and grade it against a
named pre-registered rule shape, writing ``verdict.json`` for the runner.

WHY THIS EXISTS (lane RQ-RUN, 2026-09-28)
-----------------------------------------
The four B6 prop units (RQ-20260928-011/-012/-013/-015) each declared
``run.workflow: "none — session-local, scripts/research/prop_ev_sim.py ..."``:
a grid of 15-50 sim invocations that a session was expected to type out by
hand and grade by eye. That is exactly the work that waits on tokens. This
script is the grid + the grading, so the token-free runner
(``scripts/research/script_run.py``) can run the unit end to end and land a
verdict the unit's own rule text defines.

It does NOT re-implement the simulation: every cell is one
``python3 scripts/research/prop_ev_sim.py`` subprocess with the arm's
arguments appended, and the numbers graded are the sim's own reported fields
(``results.<mode>.ev_net_usd_per_life``, ``p_net_positive``,
``p_alive_at_horizon``). The grid is the only thing added.

LEG RESOLUTION — recorded, never assumed
----------------------------------------
``--leg LEG`` resolves to the per-trade rows beside the leg's committed
evidence record (``comms/strategy_evidence/<leg>.json`` ->
``cost_stack.source`` -> ``<...>__trades.jsonl``), which is the same
population ``prop_ev_sim --book`` scores. ``--leg LEG=PATH`` pins a file.
The resolved path and its row count are written into ``grid.json`` and the
record's population string, because "verify the latest at run time and
record which was used" is in every one of the four units' designs.

THE THREE GRADE SHAPES (each is one unit's rule, mapped to E5 verdicts)
-----------------------------------------------------------------------
``pass_all_seeds`` (RQ-20260928-012 / -013), per leg:
    n < n_floor                              -> indeterminate  (UNDERPOWERED)
    ev <= 0 on ANY seed                      -> fail
    ev > 0 on ALL seeds AND p >= p_floor ALL -> pass
    otherwise                                -> indeterminate  (NULL: marginal)
``cushion_vs_flat`` (RQ-20260928-011), per leg, arms named A, B and C-*:
    best C (highest mean ev) beats A on ev AND has >= A's survival on
    ALL seeds                                -> pass           (CUSHION_FIT_WINS)
    A or B undominated by best C on any seed -> fail           (FLAT_HOLDS)
    otherwise                                -> indeterminate  (NULL)
``reproduce`` (RQ-20260928-015), per leg, against ``--target LEG=ev:p``:
    |ev - ev*|/|ev*| <= tol AND |p - p*|/p* <= tol on every seed
                                             -> pass           (REPRODUCED)
    otherwise                                -> fail           (NOT_REPRODUCED)
``none``: run the grid, write grid.json, no verdict.json (a session grades).

The unit-level verdict is ``--overall all`` (every leg passes) or ``any`` (at
least one leg passes; RQ-20260928-013's WORTH_BUILDING); a leg that is
``indeterminate`` never counts as a pass. The bucket names in parentheses are
written into each record's note so the E5 verdict and the unit's own
vocabulary can be read side by side.

Tier-1 research tooling: reads committed evidence rows + config, writes only
under ``--out``. No config write, no live path.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parents[2]
SIM = "scripts/research/prop_ev_sim.py"
GRADES = ("pass_all_seeds", "cushion_vs_flat", "reproduce", "none")


def _resolve_leg(spec: str, *, repo: Path) -> Tuple[str, Path, str]:
    """`LEG` or `LEG=PATH` -> (leg, path, how)."""
    if "=" in spec:
        leg, raw = spec.split("=", 1)
        return leg, (repo / raw), "pinned by --leg"
    rec_path = repo / "comms" / "strategy_evidence" / f"{spec}.json"
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    bt = repo / rec["cost_stack"]["source"]
    rows = bt.with_name(bt.name.replace("__bt.json", "__trades.jsonl"))
    return spec, rows, f"evidence record {rec_path.relative_to(repo).as_posix()} -> cost_stack.source"


def _count_rows(path: Path) -> int:
    n = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                n += 1
    return n


def _run_cell(*, sim: str, repo: Path, leg: str, rows: Path, arm: str, arm_args: List[str],
              common: List[str], seed: int, out: Path) -> Dict[str, Any]:
    argv = [sys.executable, sim, "--trades", f"{leg}={rows.relative_to(repo).as_posix()}",
            *common, *arm_args, "--seed", str(seed), "--out", str(out)]
    started = time.time()
    proc = subprocess.run(argv, cwd=str(repo), capture_output=True, text=True)
    cell: Dict[str, Any] = {"leg": leg, "arm": arm, "seed": seed, "exit_code": proc.returncode,
                            "seconds": round(time.time() - started, 1),
                            "argv": argv[1:], "out": out.relative_to(repo).as_posix() if out.is_relative_to(repo) else str(out)}
    if proc.returncode != 0:
        cell["stderr_tail"] = (proc.stderr or proc.stdout or "")[-2000:]
    return cell


def _read_cell(out: Path, mode: str) -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(out.read_text(encoding="utf-8"))
        r = d["results"][mode]
        return {"ev": float(r["ev_net_usd_per_life"]), "ev_se": float(r.get("ev_net_usd_per_life_mc_se", 0.0)),
                "p_net_positive": float(r["p_net_positive"]),
                "p_alive_at_horizon": float(r.get("p_alive_at_horizon", 0.0)),
                # None (not 0) when an older sim output lacks the key: "not measured".
                "p_pass_by_days": r.get("p_pass_by_days"),
                "rule_v2_verdict": (d.get("decision_rule_v2") or {}).get("verdict")}
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ── grading (pure; the self-test drives these) ───────────────────────────────
def grade_pass_all_seeds(cells: Dict[str, Dict[int, Dict[str, float]]], *, n: int,
                         n_floor: int, p_floor: float) -> Tuple[str, str]:
    """cells: arm -> seed -> stats (one arm expected)."""
    stats = [s for arm in cells.values() for s in arm.values()]
    if n < n_floor:
        return "indeterminate", f"UNDERPOWERED: n={n} < floor {n_floor}"
    if any(s["ev"] <= 0 for s in stats):
        return "fail", "FAIL: ev_net_usd_per_life <= 0 on at least one seed"
    if all(s["p_net_positive"] >= p_floor for s in stats):
        return "pass", f"PASS: ev > 0 and P(net>0) >= {p_floor} on all {len(stats)} seeds"
    return "indeterminate", f"NULL: ev > 0 on all seeds but P(net>0) < {p_floor} on at least one"


def grade_cushion_vs_flat(cells: Dict[str, Dict[int, Dict[str, float]]]) -> Tuple[str, str]:
    a, b = cells.get("A"), cells.get("B")
    cs = {k: v for k, v in cells.items() if k.startswith("C")}
    if not a or not cs:
        return "indeterminate", "NULL: arm A or a C-* arm is missing"
    best = max(cs, key=lambda k: sum(s["ev"] for s in cs[k].values()) / max(1, len(cs[k])))
    c = cs[best]
    seeds = sorted(set(a) & set(c))
    if not seeds:
        return "indeterminate", "NULL: no seed shared by A and the best C arm"

    def dominates(x: Dict[int, Dict[str, float]], y: Dict[int, Dict[str, float]], seed: int) -> bool:
        return x[seed]["ev"] > y[seed]["ev"] and x[seed]["p_alive_at_horizon"] >= y[seed]["p_alive_at_horizon"]

    if all(dominates(c, a, s) for s in seeds):
        return "pass", f"CUSHION_FIT_WINS: {best} beats A on ev with >= survival on all {len(seeds)} seeds"
    undominated = [s for s in seeds if not dominates(c, a, s)]
    if b:
        undominated += [s for s in sorted(set(b) & set(c)) if not dominates(c, b, s)]
    if undominated:
        return "fail", f"FLAT_HOLDS: A or B undominated by {best} on seed(s) {sorted(set(undominated))}"
    return "indeterminate", "NULL: mixed across seeds"


def grade_reproduce(cells: Dict[str, Dict[int, Dict[str, float]]], *, target_ev: float,
                    target_p: float, tol: float) -> Tuple[str, str]:
    stats = [s for arm in cells.values() for s in arm.values()]
    if not stats:
        return "indeterminate", "NULL: no cells"
    bad = []
    for s in stats:
        d_ev = abs(s["ev"] - target_ev) / max(abs(target_ev), 1e-9)
        d_p = abs(s["p_net_positive"] - target_p) / max(abs(target_p), 1e-9)
        if d_ev > tol or d_p > tol:
            bad.append(f"ev {s['ev']:+.2f} vs {target_ev:+.2f} ({d_ev:.0%}), "
                       f"p {s['p_net_positive']:.4f} vs {target_p:.4f} ({d_p:.0%})")
    if bad:
        return "fail", f"NOT_REPRODUCED outside ±{tol:.0%}: " + "; ".join(bad)
    return "pass", f"REPRODUCED within ±{tol:.0%} on {len(stats)} seed(s)"


def overall(leg_verdicts: Dict[str, str], how: str) -> str:
    vs = list(leg_verdicts.values())
    if not vs:
        return "indeterminate"
    if how == "any":
        return "pass" if any(v == "pass" for v in vs) else ("fail" if all(v == "fail" for v in vs) else "indeterminate")
    if all(v == "pass" for v in vs):
        return "pass"
    if all(v == "fail" for v in vs):
        return "fail"
    return "indeterminate"


# ── main ─────────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--leg", action="append", default=[], help="LEG or LEG=PATH (repeatable)")
    ap.add_argument("--arm", action="append", default=[],
                    help='NAME="<extra prop_ev_sim args>" (repeatable); default A="--sizing balance --risk-pct 0.015"')
    ap.add_argument("--common", default="--ruleset config/prop_rulesets/breakout.yaml --costs breakout --funded-start fresh",
                    help="args appended to every cell")
    ap.add_argument("--seeds", default="1,2,3,4,5")
    ap.add_argument("--mode", default="path", help="which results block to grade (path|stop|bridge|realized)")
    ap.add_argument("--grade", choices=GRADES, default="none")
    ap.add_argument("--overall", choices=("all", "any"), default="all")
    ap.add_argument("--p-floor", type=float, default=0.70)
    ap.add_argument("--n-floor", type=int, default=88)
    ap.add_argument("--target", action="append", default=[], help="LEG=ev:p for --grade reproduce")
    ap.add_argument("--tolerance", type=float, default=0.10)
    ap.add_argument("--jobs", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    ap.add_argument("--out", required=False, help="output directory (verdict.json, grid.json, sims/)")
    ap.add_argument("--sim", default=SIM, help="(tests) the simulator script to run")
    ap.add_argument("--repo", default=str(_REPO), help="(tests) repo root")
    ap.add_argument("--note", default="", help="free text carried into the record's note")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    if not args.out or not args.leg:
        ap.error("--out and at least one --leg are required")

    repo = Path(args.repo).resolve()
    out = Path(args.out)
    if not out.is_absolute():
        out = repo / out
    sims = out / "sims"
    sims.mkdir(parents=True, exist_ok=True)
    arms: Dict[str, List[str]] = {}
    for spec in (args.arm or ['A=--sizing balance --risk-pct 0.015']):
        name, _, rest = spec.partition("=")
        arms[name.strip()] = shlex.split(rest)
    common = shlex.split(args.common)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    targets: Dict[str, Tuple[float, float]] = {}
    for t in args.target:
        leg, _, val = t.partition("=")
        ev, _, p = val.partition(":")
        targets[leg] = (float(ev), float(p))

    legs: Dict[str, Dict[str, Any]] = {}
    for spec in args.leg:
        leg, rows, how = _resolve_leg(spec, repo=repo)
        if not rows.is_file():
            print(f"::error::{leg}: trades file {rows} not found ({how})", file=sys.stderr)
            return 2
        legs[leg] = {"trades_file": rows.relative_to(repo).as_posix(), "resolved_by": how,
                     "n_trades": _count_rows(rows), "path": rows}

    jobs = [(leg, arm, seed) for leg in legs for arm in arms for seed in seeds]
    print(f"prop_ev_grid: {len(legs)} leg(s) x {len(arms)} arm(s) x {len(seeds)} seed(s) = "
          f"{len(jobs)} cells, {args.jobs} parallel", flush=True)
    cells: List[Dict[str, Any]] = []
    with cf.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = [ex.submit(_run_cell, sim=args.sim, repo=repo, leg=leg, rows=legs[leg]["path"], arm=arm,
                          arm_args=arms[arm], common=common, seed=seed,
                          out=sims / f"{leg}__{arm}__seed{seed}.json")
                for leg, arm, seed in jobs]
        for f in futs:
            c = f.result()
            c["stats"] = _read_cell(repo / c["out"], args.mode) if c["exit_code"] == 0 else None
            print(f"  {c['leg']:<28} {c['arm']:<8} seed={c['seed']} exit={c['exit_code']} "
                  f"{('ev=%+.2f p=%.4f' % (c['stats']['ev'], c['stats']['p_net_positive'])) if c['stats'] else 'NO STATS'} "
                  f"({c['seconds']}s)", flush=True)
            cells.append(c)

    grid = {"tool": "scripts/research/prop_ev_grid.py", "sim": args.sim, "mode": args.mode,
            "common": common, "arms": arms, "seeds": seeds, "grade": args.grade, "overall": args.overall,
            "legs": {k: {kk: vv for kk, vv in v.items() if kk != "path"} for k, v in legs.items()},
            "cells": cells}
    (out / "grid.json").write_text(json.dumps(grid, indent=2) + "\n", encoding="utf-8")

    failed = [c for c in cells if c["exit_code"] != 0 or c["stats"] is None]
    if failed:
        print(f"::error::{len(failed)} of {len(cells)} cells failed or produced no stats", file=sys.stderr)
        for c in failed[:5]:
            print(f"  {c['leg']} {c['arm']} seed={c['seed']}: exit={c['exit_code']} "
                  f"{(c.get('stderr_tail') or '')[-300:]}", file=sys.stderr)
        return 1
    if args.grade == "none":
        print("prop_ev_grid: grade=none — grid.json written, no verdict.json (a session grades)")
        return 0

    records: List[Dict[str, Any]] = []
    leg_verdicts: Dict[str, str] = {}
    for leg, info in legs.items():
        by_arm: Dict[str, Dict[int, Dict[str, float]]] = {}
        for c in cells:
            if c["leg"] == leg:
                by_arm.setdefault(c["arm"], {})[c["seed"]] = c["stats"]
        if args.grade == "pass_all_seeds":
            v, why = grade_pass_all_seeds(by_arm, n=info["n_trades"], n_floor=args.n_floor, p_floor=args.p_floor)
        elif args.grade == "cushion_vs_flat":
            v, why = grade_cushion_vs_flat(by_arm)
        else:
            if leg not in targets:
                v, why = "indeterminate", "NULL: no --target for this leg"
            else:
                v, why = grade_reproduce(by_arm, target_ev=targets[leg][0], target_p=targets[leg][1], tol=args.tolerance)
        leg_verdicts[leg] = v
        table = {arm: {str(seed): s for seed, s in sorted(seeds_.items())} for arm, seeds_ in by_arm.items()}
        records.append({
            "verdict": v, "read_state": "measured",
            "population": f"{leg}: {info['n_trades']} committed trades from {info['trades_file']} "
                          f"({info['resolved_by']}); prop_ev_sim {args.mode}-mode over "
                          f"{len(by_arm)} arm(s) x {len(seeds)} seed(s), {' '.join(common)}",
            "n": info["n_trades"],
            "measurement": {"leg": leg, "trades_file": info["trades_file"], "arms": table},
            "note": why + (f" | {args.note}" if args.note else ""),
        })
    top = overall(leg_verdicts, args.overall)
    verdict = {
        "verdict": top, "read_state": "measured",
        "population": f"{len(legs)} leg(s) [{', '.join(legs)}], {sum(i['n_trades'] for i in legs.values())} "
                      f"committed trades in total; grade={args.grade}, overall={args.overall}",
        "n": sum(i["n_trades"] for i in legs.values()),
        "measurement": {"leg_verdicts": leg_verdicts, "grade": args.grade, "overall": args.overall,
                        "cells": len(cells)},
        "note": "; ".join(f"{leg}: {r['note'].split(' | ')[0]}" for leg, r in zip(legs, records))
                + (f" | {args.note}" if args.note else ""),
        "records": records,
    }
    (out / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    print(f"prop_ev_grid: overall={top} " + json.dumps(leg_verdicts))
    return 0


# ── self-test ────────────────────────────────────────────────────────────────
def _self_test() -> int:
    import tempfile

    # Pure grading first.
    c = {"A": {1: {"ev": 100, "p_net_positive": 0.8, "p_alive_at_horizon": 0.1},
               2: {"ev": 90, "p_net_positive": 0.75, "p_alive_at_horizon": 0.1}}}
    assert grade_pass_all_seeds(c, n=200, n_floor=88, p_floor=0.7)[0] == "pass"
    assert grade_pass_all_seeds(c, n=50, n_floor=88, p_floor=0.7)[0] == "indeterminate"
    assert grade_pass_all_seeds(c, n=200, n_floor=88, p_floor=0.78)[0] == "indeterminate"
    c["A"][2]["ev"] = -1
    assert grade_pass_all_seeds(c, n=200, n_floor=88, p_floor=0.7)[0] == "fail"

    a = {1: {"ev": 100, "p_net_positive": .8, "p_alive_at_horizon": .10}, 2: {"ev": 100, "p_net_positive": .8, "p_alive_at_horizon": .10}}
    b = {1: {"ev": 90, "p_net_positive": .8, "p_alive_at_horizon": .10}, 2: {"ev": 90, "p_net_positive": .8, "p_alive_at_horizon": .10}}
    c_win = {1: {"ev": 120, "p_net_positive": .8, "p_alive_at_horizon": .12}, 2: {"ev": 110, "p_net_positive": .8, "p_alive_at_horizon": .10}}
    c_lose = {1: {"ev": 120, "p_net_positive": .8, "p_alive_at_horizon": .12}, 2: {"ev": 95, "p_net_positive": .8, "p_alive_at_horizon": .10}}
    c_worse_surv = {1: {"ev": 120, "p_net_positive": .8, "p_alive_at_horizon": .05}, 2: {"ev": 110, "p_net_positive": .8, "p_alive_at_horizon": .10}}
    assert grade_cushion_vs_flat({"A": a, "B": b, "C-0.33": c_win})[0] == "pass"
    assert grade_cushion_vs_flat({"A": a, "B": b, "C-0.33": c_lose})[0] == "fail"
    assert grade_cushion_vs_flat({"A": a, "B": b, "C-0.33": c_worse_surv})[0] == "fail"
    assert grade_cushion_vs_flat({"A": a, "B": b, "C-0.25": c_lose, "C-0.5": c_win})[0] == "pass"  # best C picked
    assert grade_cushion_vs_flat({"A": a})[0] == "indeterminate"

    r = {"A": {1: {"ev": 900, "p_net_positive": 0.85, "p_alive_at_horizon": 0}}}
    assert grade_reproduce(r, target_ev=883, target_p=0.8477, tol=0.10)[0] == "pass"
    assert grade_reproduce(r, target_ev=883, target_p=0.70, tol=0.10)[0] == "fail"
    assert grade_reproduce(r, target_ev=400, target_p=0.85, tol=0.10)[0] == "fail"
    assert overall({"x": "pass", "y": "indeterminate"}, "any") == "pass"
    assert overall({"x": "pass", "y": "indeterminate"}, "all") == "indeterminate"
    assert overall({"x": "fail", "y": "fail"}, "any") == "fail"
    assert overall({}, "all") == "indeterminate"

    # End to end against a stub simulator whose numbers depend on the arm and seed.
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / "scripts" / "research").mkdir(parents=True)
        stub = repo / "scripts" / "research" / "stub_sim.py"
        stub.write_text(
            "import json,sys\na=sys.argv\nseed=int(a[a.index('--seed')+1]);out=a[a.index('--out')+1]\n"
            "siz=a[a.index('--sizing')+1] if '--sizing' in a else 'balance'\n"
            "ev={'balance':100,'start':90,'room':130}[siz]+seed\n"
            "json.dump({'results':{'path':{'ev_net_usd_per_life':ev,'ev_net_usd_per_life_mc_se':1,"
            "'p_net_positive':0.8,'p_alive_at_horizon':0.1}},'decision_rule_v2':{'verdict':'pass'}},open(out,'w'))\n")
        ev = repo / "comms" / "strategy_evidence" / "runs" / "d"
        ev.mkdir(parents=True)
        rows = ev / "legx__trades.jsonl"
        rows.write_text("".join('{"x":%d}\n' % i for i in range(100)))
        (ev / "legx__bt.json").write_text("{}")
        (repo / "comms" / "strategy_evidence" / "legx.json").write_text(json.dumps(
            {"cost_stack": {"source": "comms/strategy_evidence/runs/d/legx__bt.json"}}))
        out = repo / "out"
        rc = main(["--leg", "legx", "--arm", "A=--sizing balance --risk-pct 0.015", "--arm", "B=--sizing start",
                   "--arm", "C-0.33=--sizing room --room-frac 0.33", "--seeds", "1,2", "--grade", "cushion_vs_flat",
                   "--common", "", "--out", str(out), "--sim", "scripts/research/stub_sim.py", "--repo", str(repo),
                   "--jobs", "2"])
        assert rc == 0, rc
        v = json.loads((out / "verdict.json").read_text())
        assert v["verdict"] == "pass" and v["records"][0]["n"] == 100, v
        assert v["records"][0]["population"].startswith("legx: 100 committed trades from comms/strategy_evidence/runs/d/legx__trades.jsonl")
        g = json.loads((out / "grid.json").read_text())
        assert len(g["cells"]) == 6 and g["legs"]["legx"]["resolved_by"].startswith("evidence record")
        # pass_all_seeds with a pinned path and the n floor
        rc = main(["--leg", f"legy={rows.relative_to(repo)}", "--seeds", "1,2,3", "--grade", "pass_all_seeds",
                   "--n-floor", "88", "--common", "", "--out", str(repo / "out2"),
                   "--sim", "scripts/research/stub_sim.py", "--repo", str(repo)])
        assert rc == 0
        v = json.loads((repo / "out2" / "verdict.json").read_text())
        assert v["verdict"] == "pass" and v["records"][0]["note"].startswith("PASS"), v
        # a failing simulator -> non-zero exit and NO verdict.json
        stub.write_text("import sys; sys.exit(7)\n")
        rc = main(["--leg", "legx", "--seeds", "1", "--grade", "pass_all_seeds", "--common", "",
                   "--out", str(repo / "out3"), "--sim", "scripts/research/stub_sim.py", "--repo", str(repo)])
        assert rc == 1 and not (repo / "out3" / "verdict.json").exists()
        assert (repo / "out3" / "grid.json").exists()
    print("prop_ev_grid self-test OK")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
