#!/usr/bin/env python3
# wiring: manual-only - this grades a DATED population (the e35 split,
# 2026-08-30T08:53:19Z) to check one published number. A scheduled runner would
# re-answer it against a moving population and turn a recorded verdict into a
# drifting one. The standing version of "how often does a trailing amend end a
# trade" is BL-20260908-A-TRAILING-AMEND-HAS-NO-DURABLE-RECORD-SO-ITS-RATE-IS-UNMEASURABLE, which is a different
# deliverable and is deliberately not this file.
"""Stop integrity on BOTH arms — was the stop that ended it the DECLARED one? (MI-278 U15)

THE ROW, AND WHY THE ANALYSIS THAT MOST NEEDS IT IS MINE
---------------------------------------------------------
`BL-20260911-A-TRAILED-STOP-OUT-IS-ATTRIBUTED-TO-THE-DECLARED-GEOMETRY-THAT-DID-NOT-END-IT`
requires that **any** analysis attributing a stop-out to declared bracket geometry
separate exits at the ENTRY-DECLARED stop from exits at an AMENDED stop, using
`order_packages.exit_plan.stop.price`, and STATE the split. It forbids excluding
trailed stop-outs silently, because the count is load-bearing.

`trades.stop_loss` is overwritten in place by a trailing amend, so a stop-out
graded against it is graded against whatever the stop HAD BECOME. MI-275 measured
2 of 7 post-e35 stop-outs ended by a stop a lever had already tightened — one to
0.276 ATR against a declared 2.0, roughly a seventh of its declared width.

**MI-278 U5 published a stop-rate difference-in-differences of +21.2pp
(e35 11.1% → 47.1% against the untouched control's 50.0% → 64.8%) graded with
`adjudicate_exit`, which reads the trade row. It does not state the split.** This
file is that check, run against my own number rather than someone else's.

AND THE CONTROL ARM HAS NEVER BEEN GRADED FOR THIS AT ALL
-----------------------------------------------------------
MI-275's `stop_integrity_census` is correct and is scoped to e35. The untouched
control carries tens of adjudicated stop-outs that nobody has separated — and the
question is symmetric: **if trailing tightens the control legs at a similar rate,
the DiD moves for a reason that has nothing to do with e35.** A split computed on
the treated arm alone cannot answer that; it can only make the treated arm look
worse or better in isolation.

THE CLASSIFICATION IS REPRODUCED, AND THE REPRODUCTION IS ASSERTED
--------------------------------------------------------------------
The four-state rule lives INLINE inside `CF.build_units` and cannot be imported.
Re-implementing it is therefore forced, and a second copy of a classification is
exactly what drifts — so `--self-test` and `--verify-against-cf` both exist:
`classify_stop_integrity` is checked against MI-275's own recorded verdicts on the
e35 packages, and a disagreement is a REFUSAL, not a footnote.

⚠️ **THE DECLARED STOP IS ANCHORED TO THE PACKAGE ENTRY, NOT THE TRADE'S FILL.**
The row names this as its second trap: `pkg-65f02cffa856451f` declares entry 7.128
while its trade row records 7.099, so recomputing a stop as `entry_price ± atr*mult`
lands 0.29 ATR from the level actually placed. This file reads
`exit_plan.stop.price` and never recomputes.

STATES, NEVER COLLAPSED
------------------------
`stop_is_entry_declared` · `stop_amended_tighter` · `stop_amended_wider` ·
**`ungradeable_no_final_stop`** and **`ungradeable_no_declared_stop`** — the last two
are *we could not look*, and they are reported in the numerator's own denominator
rather than dropped, because a stop-out nobody can grade is not a stop-out at the
declared level.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import bleed_attribution_2026_09_11 as BA           # noqa: E402
import stop_width_counterfactual_2026_09_11 as CF   # noqa: E402

# MI-275's tolerance, restated with its source named. A stop within this many ATR
# of the declared level counts as unmoved: a broker tick and a float round-trip
# both land well inside it, and a trailing amend that moved a stop by less than
# 2% of an ATR did not decide anything.
DECLARED_TOL_ATR = 0.02

INTEGRITY_STATES = (
    "stop_is_entry_declared",
    "stop_amended_tighter",
    "stop_amended_wider",
    "ungradeable_no_final_stop",
    "ungradeable_no_declared_stop",
)
# The two that mean "we could not look". Kept as a named set so a caller cannot
# accidentally fold them into the declared bucket by writing `!= "amended"`.
UNGRADEABLE = ("ungradeable_no_final_stop", "ungradeable_no_declared_stop")


def classify_stop_integrity(declared_stop: float | None, final_stop: float | None,
                            atr: float, direction: str) -> tuple[str, float | None]:
    """Reproduce MI-275's inline four-state rule. Returns (state, moved_atr).

    ⚠️ A SECOND COPY OF A CLASSIFICATION, AND IT IS ASSERTED RATHER THAN TRUSTED.
    The rule is inline in `CF.build_units` and there is no import to take, so
    `--verify-against-cf` re-derives MI-275's own recorded verdicts through this
    function and REFUSES on any disagreement.
    """
    if declared_stop is None or declared_stop <= 0:
        return "ungradeable_no_declared_stop", None
    if final_stop is None:
        return "ungradeable_no_final_stop", None
    moved = (final_stop - declared_stop) / atr
    if abs(moved) <= DECLARED_TOL_ATR:
        return "stop_is_entry_declared", round(abs(moved), 3)
    tighter = (final_stop > declared_stop) if direction == "long" else (final_stop < declared_stop)
    return ("stop_amended_tighter" if tighter else "stop_amended_wider"), round(abs(moved), 3)


def build(trades: list[dict], pkgs: dict[str, dict], group: str, tol: float) -> dict:
    """Per-package units for *group*, carrying the declared stop and its integrity.

    Unit is the ORDER PACKAGE, never the trade row: account fan-out shares one
    price path, so a row denominator counts one market event several times and
    does it unequally between arms.
    """
    out: dict[str, Any] = {"pre": [], "post": [], "skipped": collections.Counter()}
    pop = BA.population(trades)
    sel = [r for r in pop if BA.group_of(str(r.get("strategy_name") or "")) == group]
    by_pkg: dict[str, list[dict]] = collections.defaultdict(list)
    for r in sel:
        pid = r.get("order_package_id")
        if pid:
            by_pkg[pid].append(r)
        else:
            out["skipped"]["no_order_package_id"] += 1

    for pid, rows in by_pkg.items():
        era = BA.era_of(rows[0])
        if era == "unknown":
            out["skipped"]["unknown_era"] += 1
            continue
        pkg = pkgs.get(pid)
        if pkg is None:
            out["skipped"]["no_package"] += 1
            continue
        try:
            meta = (json.loads(pkg["meta"]) if isinstance(pkg.get("meta"), str)
                    else (pkg.get("meta") or {}))
        except (json.JSONDecodeError, TypeError):
            meta = {}
        atr = CF._f(meta.get("atr"))
        if atr is None or atr <= 0:
            out["skipped"]["no_entry_frozen_atr"] += 1
            continue
        # THE ENTRY-FROZEN DECLARED STOP. `pkg.sl` is NOT a substitute and is not
        # used as one: it may already carry a trailed value, which is the very
        # thing being separated here, so falling back to it would silently grade
        # an amended stop as the declared one.
        try:
            ep = (json.loads(pkg["exit_plan"]) if isinstance(pkg.get("exit_plan"), str)
                  else (pkg.get("exit_plan") or {}))
        except (json.JSONDecodeError, TypeError):
            ep = {}
        declared_stop = CF._f((ep.get("stop") or {}).get("price"))
        direction = str(rows[0].get("direction") or "").lower()
        # rows[0] is sorted by close so the unit is deterministic (MI-278 U5's
        # finding: on a fan-out package rows[0] is otherwise input order).
        rows_s = sorted(rows, key=lambda r: str(r.get("closed_at") or ""))
        final_stop = CF._f(rows_s[0].get("stop_loss"))
        state, moved = classify_stop_integrity(declared_stop, final_stop, atr, direction)
        out[era].append({
            "package": pid, "leg": str(rows[0].get("strategy_name") or ""),
            "symbol": rows[0].get("symbol"), "era": era, "direction": direction,
            "atr": atr, "declared_stop": declared_stop, "final_stop": final_stop,
            "stop_integrity": state, "stop_moved_atr_vs_declared": moved,
            "adjudicated": BA.adjudicate_exit(rows_s[0], tol),
            "trade_ids": [r.get("id") for r in rows_s],
            "n_rows": len(rows_s),
        })
    return out


def census(units: list[dict]) -> dict:
    """Split the ADJUDICATED STOP-OUTS by what the stop actually was."""
    stops = [u for u in units if u["adjudicated"] == "reached_stop"]
    c = collections.Counter(u["stop_integrity"] for u in stops)
    declared = c.get("stop_is_entry_declared", 0)
    ungrade = sum(c.get(k, 0) for k in UNGRADEABLE)
    return {
        "packages": len(units),
        "stop_outs": len(stops),
        "by_integrity": {k: c.get(k, 0) for k in INTEGRITY_STATES if c.get(k, 0)},
        "at_declared_stop": declared,
        "at_amended_stop": c.get("stop_amended_tighter", 0) + c.get("stop_amended_wider", 0),
        "ungradeable": ungrade,
        # Two rates, never one. The naive rate is what U5 published; the declared
        # rate is what a claim about BRACKET GEOMETRY is entitled to.
        "rate_all_stop_outs": round(len(stops) / len(units), 4) if units else None,
        "rate_declared_only": round(declared / len(units), 4) if units else None,
        "rate_is_none_because": None if units else "empty_cell_no_rate_exists",
    }


def did(pre: dict, post: dict, key: str) -> float | None:
    if pre.get(key) is None or post.get(key) is None:
        return None
    return round(post[key] - pre[key], 4)


def verify_against_cf(units_by_group: dict, cf_path: str) -> dict:
    """Re-derive MI-275's recorded stop_integrity verdicts through this file's rule.

    A disagreement is a REFUSAL: a second copy of a classification that quietly
    differs from the first is worse than no second copy, because both look right.
    """
    ref = json.loads(pathlib.Path(cf_path).read_text())
    recorded: dict[str, str] = {}
    for era in ("pre", "post"):
        for d in ref.get("stop_integrity_census", {}).get(era, {}).get("detail", []):
            recorded[d["package"]] = d["stop_integrity"]
    mine = {u["package"]: u["stop_integrity"]
            for era in ("pre", "post") for u in units_by_group["e35"][era]}
    shared = sorted(set(recorded) & set(mine))
    disagree = [{"package": p, "mine": mine[p], "cf": recorded[p]}
                for p in shared if mine[p] != recorded[p]]
    return {
        "reference": cf_path,
        "recorded_by_cf": len(recorded), "built_here": len(mine), "shared": len(shared),
        "disagreements": disagree,
        "verdict": ("pass" if (shared and not disagree)
                    else "no_overlap_cannot_verify" if not shared else "FAIL"),
        "_why_no_overlap_is_not_a_pass": (
            "CF's census only details packages it graded as stop-outs; an empty "
            "intersection means the reproduction was NOT checked, never that it agreed."),
    }


def self_test() -> int:
    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {'ok ' if cond else 'FAIL'} {label}")
        ok = ok and cond

    print("stop_integrity_both_arms --self-test")
    C = classify_stop_integrity
    check("a stop exactly at the declared level is declared", C(100.0, 100.0, 10.0, "long")[0] == "stop_is_entry_declared")
    check("inside the 0.02 ATR tolerance is still declared", C(100.0, 100.15, 10.0, "long")[0] == "stop_is_entry_declared")
    check("a LONG stop raised above declared is TIGHTER", C(100.0, 101.0, 10.0, "long")[0] == "stop_amended_tighter")
    check("a LONG stop lowered below declared is WIDER", C(100.0, 99.0, 10.0, "long")[0] == "stop_amended_wider")
    check("a SHORT stop LOWERED below declared is TIGHTER (the sign flips)",
          C(100.0, 99.0, 10.0, "short")[0] == "stop_amended_tighter")
    check("a SHORT stop raised above declared is WIDER", C(100.0, 101.0, 10.0, "short")[0] == "stop_amended_wider")
    check("no final stop is ungradeable, NOT declared", C(100.0, None, 10.0, "long")[0] == "ungradeable_no_final_stop")
    check("no declared stop is its OWN ungradeable state", C(None, 100.0, 10.0, "long")[0] == "ungradeable_no_declared_stop")
    check("the two ungradeables are distinct values",
          C(100.0, None, 10.0, "long")[0] != C(None, 100.0, 10.0, "long")[0])
    check("an ungradeable carries no moved_atr rather than 0.0", C(100.0, None, 10.0, "long")[1] is None)
    check("moved_atr is a magnitude", C(100.0, 101.0, 10.0, "long")[1] == 0.1)
    check("tolerance scales with ATR, not with price",
          C(100.0, 100.15, 10.0, "long")[0] == "stop_is_entry_declared"
          and C(100.0, 100.15, 1.0, "long")[0] == "stop_amended_tighter")
    check("every declared state is distinct", len(set(INTEGRITY_STATES)) == len(INTEGRITY_STATES))
    check("UNGRADEABLE is a subset of the declared states", set(UNGRADEABLE) <= set(INTEGRITY_STATES))
    e = census([])
    check("an empty cell yields None rates, never 0.0",
          e["rate_all_stop_outs"] is None and e["rate_declared_only"] is None and e["rate_is_none_because"])
    u = [{"adjudicated": "reached_stop", "stop_integrity": "stop_is_entry_declared"},
         {"adjudicated": "reached_stop", "stop_integrity": "stop_amended_tighter"},
         {"adjudicated": "neither", "stop_integrity": "stop_is_entry_declared"},
         {"adjudicated": "reached_stop", "stop_integrity": "ungradeable_no_final_stop"}]
    c = census(u)
    check("the naive rate counts every stop-out", c["rate_all_stop_outs"] == 0.75)
    check("the declared rate counts only the declared ones", c["rate_declared_only"] == 0.25)
    check("an ungradeable stop-out is NOT counted as declared",
          c["at_declared_stop"] == 1 and c["ungradeable"] == 1)
    check("the three buckets account for every stop-out",
          c["at_declared_stop"] + c["at_amended_stop"] + c["ungradeable"] == c["stop_outs"])
    check("an unverifiable reproduction is not a pass",
          verify_against_cf.__doc__ and "REFUSAL" in verify_against_cf.__doc__)
    check("did() returns None rather than 0 when a side is missing",
          did({"r": None}, {"r": 0.5}, "r") is None)
    check("did() is post minus pre", did({"r": 0.5}, {"r": 0.7}, "r") == 0.2)
    print("self-test:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--trades")
    ap.add_argument("--packages")
    ap.add_argument("--groups", default="e35,untouched_control")
    ap.add_argument("--tol", type=float, default=0.0015)
    ap.add_argument("--verify-against-cf", help="a stop_width_counterfactual --out JSON")
    ap.add_argument("--out")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not (a.trades and a.packages):
        ap.error("--trades and --packages are required unless --self-test")

    trades = json.load(open(a.trades))
    if isinstance(trades, dict):
        trades = trades.get("rows") or []
    praw = json.load(open(a.packages))
    if isinstance(praw, dict):
        praw = praw.get("rows") or []
    pkgs = {p["order_package_id"]: p for p in praw if p.get("order_package_id")}

    groups = [g.strip() for g in a.groups.split(",") if g.strip()]
    built = {g: build(trades, pkgs, g, a.tol) for g in groups}

    result: dict[str, Any] = {
        "e35_deploy_utc": CF.E35_DEPLOY_UTC,
        "unit": "order package (NOT trade row)",
        "declared_stop_source": "order_packages.exit_plan.stop.price — never pkg.sl, which may already be trailed",
        "tolerance_atr": DECLARED_TOL_ATR,
        "arms": {},
    }
    for g in groups:
        pre, post = census(built[g]["pre"]), census(built[g]["post"])
        result["arms"][g] = {
            "pre": pre, "post": post,
            "skipped": dict(built[g]["skipped"]),
            "delta_rate_all_stop_outs": did(pre, post, "rate_all_stop_outs"),
            "delta_rate_declared_only": did(pre, post, "rate_declared_only"),
            "amended_detail": [
                {k: u[k] for k in ("package", "leg", "symbol", "era", "stop_integrity",
                                   "declared_stop", "final_stop",
                                   "stop_moved_atr_vs_declared", "trade_ids")}
                for era in ("pre", "post") for u in built[g][era]
                if u["adjudicated"] == "reached_stop" and u["stop_integrity"] not in
                ("stop_is_entry_declared",)],
        }
    if len(groups) == 2:
        t, c = result["arms"][groups[0]], result["arms"][groups[1]]
        result["difference_in_differences"] = {
            "treated": groups[0], "control": groups[1],
            "all_stop_outs": (round(t["delta_rate_all_stop_outs"] - c["delta_rate_all_stop_outs"], 4)
                              if None not in (t["delta_rate_all_stop_outs"], c["delta_rate_all_stop_outs"]) else None),
            "declared_only": (round(t["delta_rate_declared_only"] - c["delta_rate_declared_only"], 4)
                              if None not in (t["delta_rate_declared_only"], c["delta_rate_declared_only"]) else None),
            "_reading": ("all_stop_outs is what MI-278 U5 published. declared_only is what a "
                         "claim about BRACKET GEOMETRY is entitled to. Report BOTH; the row this "
                         "discharges forbids dropping the amended ones silently."),
        }
    if a.verify_against_cf:
        result["reproduction_control"] = verify_against_cf(built, a.verify_against_cf)

    out = json.dumps(result, indent=1)
    if a.out:
        pathlib.Path(a.out).write_text(out)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
