#!/usr/bin/env python3
# wiring: manual-only — a bounding ARGUMENT over one committed artifact, not a
# sweep and not a fleet job. It reads MI-312's per-leg JSON and recomputes the
# ceiling on how much INDEPENDENT content any arm-vs-arm control on the
# non-clamping (ict_scalp / fvg) family can contain. Deliberately unscheduled:
# its only input changes when a research unit lands, so a cron would re-emit an
# identical verdict daily and teach a reader that the file moving means
# something. Re-run it if MI-312's artifact is regenerated or a leg joins the
# family.
"""MI-318 — can an arm-vs-arm control on the scalp family be independent at all?

MI-312 shipped the ``no_tp`` arm and its positive control PASSED on 8 of 8 legs
within 0.71 pp — and said plainly that this is not the good news it looks like:
measured per trade, predicted and observed disagree on **21 of 7,338 joined
trades (0.29%)**, so the control is very nearly a quantity compared with itself.
Its residual question, queued to this lane, is the one it did not answer:
**is a STRONGER control available for this family at all, or is the weakness a
property of the family rather than of MI-312's design?**

This answers it as a BOUND rather than a new measurement, which is why it
commissions nothing.

THE ARGUMENT, stated before the numbers so it can be checked rather than
trusted:

1. ``no_tp`` (target removed) is the LARGEST perturbation any target change can
   make. You cannot change a target more than by deleting it.
2. Exit time is MONOTONE in target distance. For a trade, with ``other`` the
   first stop/break-even/timeout exit:
       exit(T) = min(first touch of T, other)
   so for any targets ``T_near <= T_far``:
       exit(T_near) <= exit(T_far) <= exit(no_tp) = other
   Every candidate target's exit therefore sits BETWEEN the live arm's and the
   ``no_tp`` arm's, per trade.
3. Entries advance through ``next_eligible_idx``, a function of exit time, so
   the entry sequence under any candidate target is likewise sandwiched between
   the two measured arms.
4. Hence the entry-set divergence any candidate target can produce is bounded by
   the divergence ALREADY MEASURED between live and ``no_tp``.

⚠️ **STEP 4 IS A BOUNDING ARGUMENT, NOT A PROOF OF SET-OVERLAP MONOTONICITY.**
Exit times are provably sandwiched (step 2); that entry-SET overlap is therefore
monotone in target distance is the natural reading and is not formally derived
here — the entry sequence is a recursion, and a recursion whose steps are
bounded need not have monotone set-overlap at every index. What the numbers below
establish without that caveat is the EXTREME case: at the largest perturbation
available, the two books still share ~all of their entries. That is measured, and
it is the load-bearing half.

⚠️ **WHAT THIS DOES NOT SAY.** It does not say MI-312 is wrong, that the scalp
distribution is unusable, or that the family cannot be studied. It says one
specific family of controls — compare two target arms and check the predicted
hit-rate against the observed one — cannot be made independent by choosing a
better target to compare against.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MI312 = ROOT / "docs/research/mi312-scalp-target-arms-2026-09-18.json"

#: Above this shared-entry fraction, the two arms are the same book for
#: practical purposes and a control between them is self-comparison.
#: CHOSEN, not tuned: 0.95 leaves 5% of entries free to differ, which is ~17x
#: the per-trade disagreement MI-312 actually measured (0.29%). Every leg here
#: clears it by a wide margin, so no verdict turns on the exact value -- and
#: that is stated rather than left for a reader to discover.
INDEPENDENCE_FLOOR = 0.95

#: MI-312 § 2.1, CITED not recomputed: the emits it was measured from live under
#: runtime_logs/ and are gitignored, so this figure cannot be re-derived here.
#: Carried so the bound below can be read against the observed value.
MI312_MEASURED_DISAGREEMENT = (21, 7338)


def load(path: pathlib.Path | None = None) -> dict:
    p = path or MI312
    if not p.exists():
        raise SystemExit(
            f"MI-312's artifact is absent at {p}. It landed with #12515; this unit "
            "reads it rather than regenerating it, and refuses instead of guessing."
        )
    return json.loads(p.read_text())


def grade_leg(row: dict) -> dict:
    """Per leg: the ceiling on independent content in an arm-vs-arm control.

    Four never-collapsed states. ``not_gradeable`` is *we could not look* and is
    never folded into either verdict.
    """
    ov = row.get("entry_overlap")
    pairs = row.get("control_n_pairs")
    if ov is None or pairs is None:
        state, bound = "not_gradeable", None
    elif not isinstance(ov, (int, float)) or not (0.0 <= ov <= 1.0):
        state, bound = "not_gradeable", None
    else:
        bound = 1.0 - float(ov)
        state = (
            "cannot_be_independent"
            if ov >= INDEPENDENCE_FLOOR
            else "may_be_independent"
        )
    return {
        "leg": row.get("leg"),
        "n_live": row.get("n_live"),
        "n_no_tp": row.get("n_no_tp"),
        "entry_overlap": ov,
        "control_n_pairs": pairs,
        "control_delta_pp": row.get("control_delta_pp"),
        # The ceiling on the fraction of the control that is NOT a quantity
        # compared with itself, at the LARGEST perturbation available.
        "independent_fraction_ceiling": bound,
        "state": state,
    }


def analyse(path: pathlib.Path | None = None) -> dict:
    doc = load(path)
    rows = [grade_leg(r) for r in doc["legs"]]
    gradeable = [r for r in rows if r["state"] != "not_gradeable"]
    ovs = [r["entry_overlap"] for r in gradeable]
    n_cannot = sum(1 for r in gradeable if r["state"] == "cannot_be_independent")
    verdict = (
        "arm_vs_arm_cannot_be_independent"
        if gradeable and n_cannot == len(gradeable)
        else ("mixed" if gradeable else "not_gradeable")
    )
    return {
        "unit": "MI-318",
        "source": str((path or MI312).relative_to(ROOT)),
        "source_generated_at": doc.get("generated_at"),
        "independence_floor": INDEPENDENCE_FLOOR,
        "population": (
            "all legs in MI-312's committed artifact; entry_overlap is live-arm vs "
            "no_tp-arm, i.e. the LARGEST perturbation a target change can make"
        ),
        "n_legs": len(rows),
        "n_gradeable": len(gradeable),
        "entry_overlap_min": min(ovs) if ovs else None,
        "entry_overlap_max": max(ovs) if ovs else None,
        "entry_overlap_mean": (sum(ovs) / len(ovs)) if ovs else None,
        "independent_fraction_ceiling_max": (1.0 - min(ovs)) if ovs else None,
        "total_joined_pairs": sum(r["control_n_pairs"] or 0 for r in gradeable),
        "mi312_measured_disagreement": {
            "n": MI312_MEASURED_DISAGREEMENT[0],
            "of": MI312_MEASURED_DISAGREEMENT[1],
            "rate": MI312_MEASURED_DISAGREEMENT[0] / MI312_MEASURED_DISAGREEMENT[1],
            "basis": "CITED from MI-312 § 2.1 — its emits are gitignored, so this "
            "cannot be recomputed here and is not presented as if it were",
        },
        "verdict": verdict,
        "legs": rows,
    }


def selftest() -> int:
    checks, fails = 0, []

    def ok(cond, label):
        nonlocal checks
        checks += 1
        if not cond:
            fails.append(label)

    res = analyse()
    ok(res["n_legs"] == 8, f"8 legs in MI-312's artifact, got {res['n_legs']}")
    ok(res["n_gradeable"] == 8, "all 8 gradeable")
    ok(res["verdict"] == "arm_vs_arm_cannot_be_independent", "verdict is the refusal")
    ok(
        res["entry_overlap_min"] >= INDEPENDENCE_FLOOR,
        "even the MINIMUM clears the floor",
    )

    # --- NEGATIVE CONTROL 1: the verdict must be reachable in the OTHER
    # direction. A grader that can only say "cannot be independent" proves
    # nothing about this family.
    lo = grade_leg({"leg": "synthetic", "entry_overlap": 0.10, "control_n_pairs": 100})
    ok(
        lo["state"] == "may_be_independent",
        "NEG1: a low-overlap leg grades may_be_independent",
    )
    ok(
        abs(lo["independent_fraction_ceiling"] - 0.90) < 1e-9,
        "NEG1b: ceiling is 1-overlap",
    )

    # --- NEGATIVE CONTROL 2: *we did not look* is never a verdict.
    for bad in (
        {"leg": "x"},
        {"leg": "x", "entry_overlap": None, "control_n_pairs": 5},
        {"leg": "x", "entry_overlap": 1.5, "control_n_pairs": 5},
        {"leg": "x", "entry_overlap": "0.99", "control_n_pairs": 5},
    ):
        ok(grade_leg(bad)["state"] == "not_gradeable", f"NEG2: {bad} is not_gradeable")
    ok(
        grade_leg({"leg": "x"})["independent_fraction_ceiling"] is None,
        "NEG2b: an ungradeable leg reports None, never 0.0",
    )

    # --- NEGATIVE CONTROL 3: the floor must be load-bearing in principle but
    # NOT decide this dataset. If every leg sits within a hair of the floor the
    # verdict would be an artifact of the threshold; it does not.
    ok(
        res["entry_overlap_min"] - INDEPENDENCE_FLOOR > 0.03,
        "NEG3: the minimum clears the floor by a real margin, so no verdict turns on it",
    )

    # --- NEGATIVE CONTROL 4: the cited figure must be declared as cited.
    ok(
        "CITED" in res["mi312_measured_disagreement"]["basis"],
        "NEG4: the un-recomputable figure says so",
    )
    ok(
        res["mi312_measured_disagreement"]["rate"] < 0.01,
        "NEG4b: cited rate is under 1%",
    )

    # --- the bound must be consistent with the measured disagreement: the
    # ceiling cannot be BELOW what was actually observed, or one of them is wrong.
    ok(
        res["independent_fraction_ceiling_max"]
        >= res["mi312_measured_disagreement"]["rate"],
        "the ceiling is not below the observed disagreement",
    )

    print(f"mi318 selftest: {checks - len(fails)}/{checks} passed")
    for f in fails:
        print("  FAIL:", f)
    return 1 if fails else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--write", metavar="PATH")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(selftest())
    res = analyse()
    if args.write:
        pathlib.Path(args.write).write_text(
            json.dumps(res, indent=1, sort_keys=True) + "\n"
        )
        print(f"wrote {args.write}")
    print(f"verdict: {res['verdict']}")
    print(
        f"entry_overlap over {res['n_gradeable']} legs: "
        f"min {res['entry_overlap_min']:.4f} max {res['entry_overlap_max']:.4f} "
        f"mean {res['entry_overlap_mean']:.4f}"
    )
    print(
        f"independent-content ceiling: {res['independent_fraction_ceiling_max'] * 100:.2f}% "
        f"(observed disagreement, cited: "
        f"{res['mi312_measured_disagreement']['rate'] * 100:.2f}%)"
    )


if __name__ == "__main__":
    main()
