#!/usr/bin/env python3
"""How much does a lever's verdict depend on WHERE the IS/OOS boundary falls?

WHY THIS EXISTS (measured 2026-08-19, `BL-20260819-SWEEP-VERDICT-NOT-TESTED-FOR-SPLIT-SENSITIVITY`).

`m20_fleet_exit_sweep` reports one binary verdict per cell, computed at ONE
split, with no measure of how stable that verdict is. On `sol_pullback_2h`,
holding the corpus and the commit fixed and moving only `--split-target-oos`
from 50 to 35:

    target 50   split 2025-06-14   base+delta OOS  +1.1645   PASS
    target 35   split 2025-10-04   base+delta OOS  -1.6908   FAIL

`dOOS` differs **5.14x** on the split choice alone, and `gb1R_afterMFE1R` flips
the same way. There is no principled reason to prefer 35 over 50 — so the
verdict was a function of an arbitrary methodological choice, and downstream a
PASS and a coin-flip are **indistinguishable**. That is how a 5.14x swing
reached a Tier-3 declare proposal, and it was caught only because the coverage
matrix happened to have used the other target.

This is the fold-dispersion problem (`m20_dispersion_rate`,
`BL-20260815-EVIDENCE-ROWS-DO-NOT-RECORD-FOLD-OFFSET`) one axis over: that one
was found and fixed for FOLD offsets and left unmeasured on the IS/OOS axis.

ONE HARNESS RUN, NOT N
----------------------
Re-running the sweep per split target is N full harness passes. It is not
needed, because **the walk-forward folds are split-INVARIANT** — they are
calendar-year based, and the two runs above produced byte-identical folds
(2021 +2.303 · 2022 -2.8333 · 2023 +8.0772 · 2024 -1.1441 · 2025 +4.7968 ·
2026 +4.8897). Only the IS/OOS gate moves. So one `--emit-trades` run per arm
is partitioned here at every candidate boundary.

THE GATE IS THE LIVE ONE, NOT A COPY
------------------------------------
Verdicts come from `m20_fleet_exit_sweep.beats` by import. A second
implementation of a decision predicate is the drift this repo keeps paying for
— and this session already produced one (`regime_policy_coverage`'s first draft
re-implemented the cell test and reported 0 governed legs of 47, because PyYAML
resolves the literal `off` to `False`).

⚠️ **THE METRICS ARE RE-DERIVED, AND THAT IS CHECKED, NOT ASSUMED.**
`net_total_r` and `max_drawdown_r` are recomputed here from the emitted rows —
a genuine second implementation of what the harness reports, and exactly the
risk the paragraph above warns about. It is not avoidable (partitioning at a
new boundary means aggregating a subset the harness never aggregated), so
instead it is **falsifiable**: `verify_against_harness` recomputes over the FULL
population and compares to the harness's own reported figures. A mismatch beyond
tolerance makes `analyse` **refuse to report**, rather than emitting a
dispersion band built on a metric that does not reproduce.

Observe-only. Reads JSONL. No DB, no socket, no order path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

#: Candidate OOS-trade targets to sweep. Chosen to bracket the two the fleet
#: sweep and the coverage matrix actually used (35 and 50) rather than to
#: flatter either; a band that excluded the disagreeing pair would be useless.
DEFAULT_TARGETS = (30, 35, 40, 45, 50, 60)

#: Metrics must reproduce the harness this closely or nothing is reported.
TOLERANCE_R = 1e-3


def _f(row: Dict[str, Any], key: str) -> Optional[float]:
    try:
        v = float(row[key])
    except (KeyError, TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def load_rows(path: str) -> List[Dict[str, Any]]:
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _sorted_by_exit(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Chronological by exit. A drawdown is path-dependent, so the ORDER is part
    of the metric — aggregating an unordered subset would silently report a
    different number than the same trades in time order."""
    return sorted(rows, key=lambda r: str(r.get("exit_time") or r.get("entry_time") or ""))


def metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """`net_total_r` / `max_drawdown_r` / `total_trades` over one population.

    Mirrors the harness's own aggregation. `verify_against_harness` is what
    makes that claim checkable rather than asserted.
    """
    ordered = _sorted_by_exit(rows)
    nets = [_f(r, "net_r") for r in ordered]
    graded = [n for n in nets if n is not None]
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for n in graded:
        cum += n
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {
        "net_total_r": round(sum(graded), 4),
        "max_drawdown_r": round(mdd, 4),
        "total_trades": len(ordered),
        # Rows we could not read are COUNTED, never silently dropped from a
        # denominator the caller believes is complete.
        "rows_ungradeable": len(ordered) - len(graded),
    }


def verify_against_harness(
    rows: List[Dict[str, Any]], reported: Dict[str, Any], *, tol: float = TOLERANCE_R,
) -> Dict[str, Any]:
    """Does the re-derivation reproduce the harness's own full-population figures?

    THE POSITIVE CONTROL FOR THIS WHOLE FILE. Without it the dispersion band is
    a confident set of numbers from an unvalidated second implementation — the
    same shape as the `mfe_r >= cap_r` predicate that returned `p=0.0` earlier
    today over a corpus containing 85 take-profits.
    """
    got = metrics(rows)
    out = {"ok": True, "checked": [], "tolerance_r": tol}
    for key in ("net_total_r", "max_drawdown_r", "total_trades"):
        want = reported.get(key)
        if want is None:
            out["checked"].append({"key": key, "state": "not_supplied"})
            continue
        delta = abs(float(got[key]) - float(want))
        agree = delta <= (tol if key != "total_trades" else 0)
        out["ok"] = out["ok"] and agree
        out["checked"].append({"key": key, "derived": got[key], "harness": want,
                               "delta": round(delta, 6), "agree": agree})
    if not any(c.get("state") != "not_supplied" for c in out["checked"]):
        out["ok"] = False
        out["why"] = "nothing_supplied_to_verify_against"
    return out


def split_at_oos_target(rows: List[Dict[str, Any]], target: int) -> Optional[str]:
    """The exit_time boundary that leaves ~`target` trades out of sample.

    Returns `None` when the population cannot supply the target — reported as
    `insufficient_population`, never silently clamped to the whole book.
    """
    ordered = _sorted_by_exit(rows)
    if target <= 0 or target >= len(ordered):
        return None
    boundary = ordered[len(ordered) - target]
    return str(boundary.get("exit_time") or boundary.get("entry_time") or "") or None


def partition(rows: List[Dict[str, Any]], split: str):
    def key(r):
        return str(r.get("exit_time") or r.get("entry_time") or "")
    return ([r for r in rows if key(r) < split], [r for r in rows if key(r) >= split])


def analyse(
    base_rows: List[Dict[str, Any]],
    cell_rows: List[Dict[str, Any]],
    *,
    targets=DEFAULT_TARGETS,
    base_reported: Optional[Dict[str, Any]] = None,
    min_oos_trades: Optional[int] = None,
) -> Dict[str, Any]:
    """Verdict across a band of IS/OOS boundaries. Refuses on a failed check."""
    from scripts.research.m20_fleet_exit_sweep import MIN_OOS_TRADES, beats

    floor = MIN_OOS_TRADES if min_oos_trades is None else min_oos_trades
    out: Dict[str, Any] = {"targets": list(targets), "min_oos_trades": floor}

    if base_reported is not None:
        v = verify_against_harness(base_rows, base_reported)
        out["harness_agreement"] = v
        if not v["ok"]:
            out["state"] = "refused"
            out["why"] = ("re-derived metrics do NOT reproduce the harness on the "
                          "full population; a dispersion band from an unvalidated "
                          "aggregation would be a confident wrong answer")
            return out
    else:
        out["harness_agreement"] = {"ok": False, "state": "not_supplied"}
        out["state"] = "refused"
        out["why"] = ("no harness figures supplied to verify the re-derivation "
                      "against — pass --base-reported; an unchecked second "
                      "implementation is exactly what this file exists to avoid")
        return out

    rows = []
    for t in targets:
        split = split_at_oos_target(base_rows, t)
        if split is None:
            rows.append({"target": t, "state": "insufficient_population"})
            continue
        b_is, b_oos = partition(base_rows, split)
        c_is, c_oos = partition(cell_rows, split)
        bm_is, bm_oos = metrics(b_is), metrics(b_oos)
        cm_is, cm_oos = metrics(c_is), metrics(c_oos)
        thin = bm_oos["total_trades"] < floor
        passed = bool(beats(cm_is, bm_is) and beats(cm_oos, bm_oos))
        rows.append({
            "target": t, "split": split, "state": "graded",
            "base_is_n": bm_is["total_trades"], "base_oos_n": bm_oos["total_trades"],
            "below_oos_floor": thin,
            "d_net_r_is": round(cm_is["net_total_r"] - bm_is["net_total_r"], 4),
            "d_net_r_oos": round(cm_oos["net_total_r"] - bm_oos["net_total_r"], 4),
            "base_plus_delta_oos": round(cm_oos["net_total_r"], 4),
            "is_oos_pass": passed,
        })
    graded = [r for r in rows if r["state"] == "graded"]
    verdicts = {r["is_oos_pass"] for r in graded}
    out["rows"] = rows
    out["graded"] = len(graded)
    # Three states, never collapsed: a band that never graded is NOT "stable".
    if not graded:
        out["state"] = "not_measured"
        out["split_sensitive"] = None
    else:
        out["state"] = "measured"
        out["split_sensitive"] = len(verdicts) > 1
        out["pass_fraction"] = round(
            sum(1 for r in graded if r["is_oos_pass"]) / len(graded), 4)
    return out


def _self_test() -> int:
    fails = []

    def check(name, got, want):
        if got != want:
            fails.append(f"{name}: got {got!r} want {want!r}")

    def tr(t, net):
        return {"exit_time": t, "net_r": net}

    base = [tr(f"2025-{m:02d}-01", -1.0) for m in range(1, 13)]
    check("metrics sums net_r", metrics(base)["net_total_r"], -12.0)
    check("metrics counts trades", metrics(base)["total_trades"], 12)
    # Drawdown is path-dependent: same trades, different order, different mdd.
    up_then_down = [tr("2025-01-01", 5.0), tr("2025-02-01", -3.0)]
    down_then_up = [tr("2025-01-01", -3.0), tr("2025-02-01", 5.0)]
    check("mdd after a peak", metrics(up_then_down)["max_drawdown_r"], 3.0)
    check("mdd from a cold start", metrics(down_then_up)["max_drawdown_r"], 3.0)
    check("ungradeable rows counted",
          metrics(base + [{"exit_time": "2025-13-01"}])["rows_ungradeable"], 1)

    # The verification gate — the positive control.
    good = verify_against_harness(base, {"net_total_r": -12.0, "total_trades": 12})
    bad = verify_against_harness(base, {"net_total_r": -99.0, "total_trades": 12})
    check("verification passes on agreement", good["ok"], True)
    check("verification FAILS on disagreement", bad["ok"], False)
    check("nothing to verify against is not a pass",
          verify_against_harness(base, {})["ok"], False)

    # analyse refuses without harness figures, and refuses on a bad check.
    check("refuses with no harness figures",
          analyse(base, base)["state"], "refused")
    check("refuses on a failed check",
          analyse(base, base, base_reported={"net_total_r": -99.0})["state"], "refused")

    # A cell that beats base everywhere is stable; one that flips is flagged.
    cell_good = [tr(f"2025-{m:02d}-01", -0.5) for m in range(1, 13)]
    rep = {"net_total_r": -12.0, "max_drawdown_r": 12.0, "total_trades": 12}
    res = analyse(base, cell_good, targets=(3, 4, 5), base_reported=rep,
                  min_oos_trades=1)
    check("stable cell is not split_sensitive", res["split_sensitive"], False)
    check("stable cell passes everywhere", res["pass_fraction"], 1.0)

    # FLIP CONTROL. The first fixture I wrote here did NOT flip — the cell never
    # improved IS at any boundary, so every target failed for the same reason and
    # the control passed vacuously while proving nothing. A control that cannot
    # produce the state it is testing for is the defect, not the evidence.
    # This one is better everywhere EXCEPT the last three trades:
    #   target 3 -> OOS is exactly those three, cell -3.6 vs base -3.0  -> FAIL
    #   target 9 -> they are diluted across a wider OOS, -6.6 vs -9.0   -> PASS
    cell_flip = [tr(f"2025-{m:02d}-01", -0.5) for m in range(1, 10)] + \
                [tr(f"2025-{m:02d}-01", -1.2) for m in range(10, 13)]
    res2 = analyse(base, cell_flip, targets=(3, 9), base_reported=rep,
                   min_oos_trades=1)
    by_t = {r["target"]: r["is_oos_pass"] for r in res2["rows"]}
    check("the narrow boundary FAILS", by_t[3], False)
    check("the wide boundary PASSES", by_t[9], True)
    check("flipping cell IS split_sensitive", res2["split_sensitive"], True)
    check("...and the pass fraction reports how split-dependent", res2["pass_fraction"], 0.5)

    check("a target the population cannot supply is reported",
          analyse(base, base, targets=(500,), base_reported=rep)["rows"][0]["state"],
          "insufficient_population")
    check("...and an all-ungraded band is not_measured, never 'stable'",
          analyse(base, base, targets=(500,), base_reported=rep)["split_sensitive"],
          None)

    for f in fails:
        print("FAIL", f)
    print(f"self-test: {18 - len(fails)}/18 passed")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", help="JSONL of BASE trades (--emit-trades)")
    ap.add_argument("--cell", help="JSONL of CELL trades (--emit-trades)")
    ap.add_argument("--base-reported", help='JSON {"net_total_r":…,"max_drawdown_r":…,"total_trades":…} as the harness printed it')
    ap.add_argument("--targets", help="comma-separated OOS targets")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not (a.base and a.cell):
        print("error: --base and --cell are required", file=sys.stderr)
        return 2
    targets = tuple(int(x) for x in a.targets.split(",")) if a.targets else DEFAULT_TARGETS
    rep = json.loads(a.base_reported) if a.base_reported else None
    res = analyse(load_rows(a.base), load_rows(a.cell),
                  targets=targets, base_reported=rep)
    print(json.dumps(res, indent=2))
    return 0 if res.get("state") == "measured" else 1


if __name__ == "__main__":
    sys.exit(main())
