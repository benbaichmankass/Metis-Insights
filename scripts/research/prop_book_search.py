#!/usr/bin/env python3
"""Book search for prop accounts: inactivity gap + simultaneous-loss + prop_ev_sim per book.

Executes the rule registered in research/queue/RQ-20261005-812.yaml (Tradeify) and
-813.yaml (Velotrade) BEFORE any run. Nothing here changes a threshold; the
thresholds below are copied from those units and must move only with them.

Usage:
    python3 scripts/research/prop_book_search.py --firm tradeify --legs LEG=PATH ... --out DIR
    python3 scripts/research/prop_book_search.py --self-test
"""
from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SIM = REPO / "scripts" / "research" / "prop_ev_sim.py"

MAX_GAP_DAYS = 14.0          # RULE-RQ1005-PROPEXPAND-*: book_max_gap <= 14d
LOSS_LINE_FRAC = 0.80        # no day with realised loss > 80% of the daily-loss limit
P_FLOOR = 0.70
IMPROVE_MARGIN = 0.05
SEEDS = (1, 2, 3, 4, 5)

FIRMS = {
    "tradeify": dict(ruleset="config/prop_rulesets/tradeify_247_1step.yaml", account=10000.0,
                     daily_pct=0.03, reset="22:00", caps={"ETHUSDT": 5, "SOLUSDT": 2, "XRPUSDT": 2},
                     arms=(0.005, 0.0075)),
    "velotrade": dict(ruleset="config/prop_rulesets/velotrade_classic_1step.yaml", account=5000.0,
                      daily_pct=0.04, reset="00:30", caps={"ETHUSDT": 5, "SOLUSDT": 5, "XRPUSDT": 2},
                      arms=(0.005, 0.0075)),
}


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def book_max_gap(rows: list[dict], start: datetime, end: datetime) -> float:
    """Largest gap in days between consecutive ENTRIES, including start->first and last->end."""
    ts = sorted(_ts(r["entry_time"]) for r in rows)
    pts = [start] + ts + [end]
    return max((b - a).total_seconds() for a, b in zip(pts, pts[1:])) / 86400.0


def day_key(t: datetime, reset: str) -> str:
    hh, mm = (int(x) for x in reset.split(":"))
    return (t - timedelta(hours=hh, minutes=mm)).date().isoformat()


def daily_loss_check(legs: dict[str, list[dict]], risk_pct: float, account: float,
                     daily_limit_usd: float, reset: str) -> dict:
    """Realised net-R x flat risk$ per reset-day (by EXIT time), union ledger. Static risk of
    risk_pct*account (the registered arm is flat risk, not compounding)."""
    risk_usd = risk_pct * account
    per_leg: dict[str, dict[str, float]] = {k: defaultdict(float) for k in legs}
    for leg, rows in legs.items():
        for r in rows:
            per_leg[leg][day_key(_ts(r["exit_time"]), reset)] += r["net_r"] * risk_usd
    days = sorted({d for v in per_leg.values() for d in v})
    tot = {d: sum(v.get(d, 0.0) for v in per_leg.values()) for d in days}
    worst_day = min(tot, key=tot.get) if tot else None
    worst = tot[worst_day] if worst_day else 0.0
    line = -LOSS_LINE_FRAC * daily_limit_usd
    corr = None
    names = list(legs)
    if len(names) >= 2:
        import numpy as np
        mat = np.array([[per_leg[n].get(d, 0.0) for d in days] for n in names])
        cs = [float(np.corrcoef(mat[i], mat[j])[0, 1]) for i in range(len(names)) for j in range(i + 1, len(names))]
        corr = sum(cs) / len(cs)
    return dict(risk_usd=risk_usd, worst_day=worst_day, worst_day_usd=round(worst, 2),
                days_beyond_80pct_line=sum(1 for v in tot.values() if v <= line),
                days_beyond_limit=sum(1 for v in tot.values() if v <= -daily_limit_usd),
                mean_pairwise_daily_corr=None if corr is None else round(corr, 3), n_days=len(days),
                ok=worst > line)


def min_hold_seconds(rows: list[dict]) -> float:
    return min((_ts(r["exit_time"]) - _ts(r["entry_time"])).total_seconds() for r in rows)


def sim_cell(firm: dict, legs: dict[str, Path], risk: float, seed: int, out: Path) -> dict:
    argv = [sys.executable, str(SIM)]
    for k, p in legs.items():
        argv += ["--trades", f"{k}={p}"]
    for k in legs:
        sym = load(Path(legs[k]))[0]["symbol"]
        argv += ["--lev-cap", f"{k}={firm['caps'][sym]}"]
    argv += ["--ruleset", firm["ruleset"], "--no-hedge", "--swap-model", "prorated", "--funded-start", "fresh",
             "--sizing", "balance", "--risk-pct", str(risk), "--modes", "path", "--seed", str(seed), "--out", str(out)]
    p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
    if p.returncode != 0:
        return dict(error=p.stderr[-400:])
    d = json.loads(out.read_text())
    blk = (d.get("results") or {}).get("path") or {}
    return dict(ev=blk.get("ev_net_usd_per_life"), p=blk.get("p_net_positive"), raw_keys=sorted(blk)[:12])


def grade(cells: dict[int, dict], gap_ok: bool, loss_ok: bool, legs_ok: bool, incumbent_minp: float | None) -> str:
    if any("error" in c or c.get("ev") is None for c in cells.values()):
        return "NOT_APPLICABLE"
    evs = [c["ev"] for c in cells.values()]
    ps = [c["p"] for c in cells.values()]
    if not legs_ok:
        return "NULL"
    if min(evs) <= 0:
        return "FAIL"
    if gap_ok and loss_ok and min(ps) >= P_FLOOR:
        return "PASS"
    if gap_ok and loss_ok and incumbent_minp is not None and min(ps) >= incumbent_minp + IMPROVE_MARGIN:
        return "INACTIVITY_SAFE_IMPROVEMENT"
    return "NULL"


def books(admitted: dict[str, str]) -> list[tuple[str, ...]]:
    """admitted: leg -> symbol. All non-empty sets with at most one leg per symbol."""
    by_sym: dict[str, list[str]] = defaultdict(list)
    for leg, sym in admitted.items():
        by_sym[sym].append(leg)
    choices = [[None] + sorted(v) for _, v in sorted(by_sym.items())]
    out = []
    for combo in itertools.product(*choices):
        b = tuple(x for x in combo if x)
        if b:
            out.append(b)
    return out


def _self_test() -> int:
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = [dict(entry_time=(t0 + timedelta(days=d)).isoformat(), exit_time=(t0 + timedelta(days=d, hours=2)).isoformat(),
                 net_r=-1.0, symbol="ETHUSDT") for d in (1, 3, 20)]
    assert abs(book_max_gap(rows, t0, t0 + timedelta(days=22)) - 17) < 1e-6
    bs = books({"a": "ETHUSDT", "b": "ETHUSDT", "c": "SOLUSDT"})
    assert ("a", "c") in bs and ("a", "b") not in bs and len(bs) == 5, bs
    dl = daily_loss_check({"a": rows[:1], "b": rows[:1]}, 0.005, 10000.0, 300.0, "22:00")
    assert dl["worst_day_usd"] == -100.0 and dl["ok"], dl
    dl2 = daily_loss_check({"a": rows[:1], "b": rows[:1], "c": rows[:1], "d": rows[:1], "e": rows[:1]}, 0.005, 10000.0, 300.0, "22:00")
    assert not dl2["ok"], dl2
    g = grade({s: dict(ev=5, p=0.75) for s in SEEDS}, True, True, True, 0.3)
    assert g == "PASS" and grade({s: dict(ev=5, p=0.40) for s in SEEDS}, True, True, True, 0.3) == "INACTIVITY_SAFE_IMPROVEMENT"
    assert grade({s: dict(ev=5, p=0.40) for s in SEEDS}, False, True, True, 0.3) == "NULL"
    print("self-test ok")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--firm", choices=sorted(FIRMS))
    ap.add_argument("--legs", nargs="+", metavar="LEG=PATH", help="ADMITTED legs only")
    ap.add_argument("--window", nargs=2, metavar=("START", "END"), help="override window (default: min/max over all legs' data)")
    ap.add_argument("--out", help="output dir")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--no-sim", action="store_true", help="gap + daily-loss only")
    ap.add_argument("--incumbent", help="leg name whose solo book is the baseline (same arm); default: best solo book")
    ap.add_argument("--exploratory", action="store_true",
                    help="legs are NOT all ADMITTED (e.g. approximate fidelity): a PASS is relabelled EXPLORATORY_PASS and is never a proposal")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    firm = FIRMS[a.firm]
    legs = {k: Path(v) for k, v in (x.split("=", 1) for x in a.legs)}
    rows = {k: load(REPO / p) for k, p in legs.items()}
    sym = {k: r[0]["symbol"] for k, r in rows.items()}
    allts = [_ts(r["entry_time"]) for rr in rows.values() for r in rr]
    start, end = (min(allts), max(allts))
    if a.window:
        start, end = _ts(a.window[0]), _ts(a.window[1])
    out = Path(a.out)
    (out / "sims").mkdir(parents=True, exist_ok=True)
    limit = firm["daily_pct"] * firm["account"]
    report = dict(firm=a.firm, ruleset=firm["ruleset"], window=[start.isoformat(), end.isoformat()], legs={
        k: dict(symbol=sym[k], n=len(rows[k]), trades_file=str(legs[k]), min_hold_s=min_hold_seconds(rows[k]),
                solo_max_gap_days=round(book_max_gap(rows[k], start, end), 2)) for k in legs}, books=[])
    bl = books(sym)
    tasks = []
    for b in bl:
        union = [r for k in b for r in rows[k]]
        gap = round(book_max_gap(union, start, end), 2)
        for risk in firm["arms"]:
            dl = daily_loss_check({k: rows[k] for k in b}, risk, firm["account"], limit, firm["reset"])
            ent = dict(book=list(b), risk=risk, max_gap_days=gap, gap_ok=gap <= MAX_GAP_DAYS,
                       min_hold_s=min_hold_seconds(union), daily_loss=dl, cells={})
            report["books"].append(ent)
            if not a.no_sim and ent["gap_ok"] and dl["ok"]:   # the sim cannot rescue a book that fails (G) or (D)
                for s in SEEDS:
                    tasks.append((ent, s))
    def run(t):
        ent, s = t
        name = "+".join(ent["book"]) + f"__r{ent['risk']}__s{s}.json"
        ent["cells"][s] = sim_cell(firm, {k: legs[k] for k in ent["book"]}, ent["risk"], s, out / "sims" / name)
    with ThreadPoolExecutor(a.jobs) as ex:
        list(ex.map(run, tasks))
    def minp(e):
        ps = [c.get("p") for c in e["cells"].values() if c.get("p") is not None]
        return min(ps) if len(ps) == len(SEEDS) else None
    for ent in report["books"]:
        solos = [e for e in report["books"] if len(e["book"]) == 1 and e["risk"] == ent["risk"] and minp(e) is not None]
        if a.incumbent:
            solos = [e for e in solos if e["book"] == [a.incumbent]]
        inc = max((minp(e) for e in solos), default=None)
        ent["incumbent_min_p"] = inc
        ent["min_p"] = minp(ent)
        if ent["cells"]:
            ent["mean_ev"] = round(sum(c["ev"] for c in ent["cells"].values() if c.get("ev") is not None) / len(SEEDS), 2)
            v = grade(ent["cells"], ent["gap_ok"], ent["daily_loss"]["ok"], True, inc)
            ent["verdict"] = ("EXPLORATORY_" + v) if (a.exploratory and v in ("PASS", "INACTIVITY_SAFE_IMPROVEMENT")) else v
        else:
            ent["verdict"] = "NULL_" + ("GAP" if not ent["gap_ok"] else "DAILY_LOSS" if not ent["daily_loss"]["ok"] else "NOT_RUN")
    (out / "book_search.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({"books": len(report["books"]), "sim_cells": len(tasks)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
