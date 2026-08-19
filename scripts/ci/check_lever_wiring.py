#!/usr/bin/env python3
"""Can every shipped exit lever be RUN, GRADED, and SEEN?

The generalisation of four separate findings on 2026-08-18, each of which was a
capability that existed while the thing meant to consume it did not know:

* `closed_pnl_from_fills` — a broker-truth PnL reader with ZERO production
  callers (`BL-20260818-IB-BROKER-PNL-READER-HAS-NO-CALLER`).
* `attach_ib_target` — an allowlisted operator action whose script had never
  executed (`BL-20260818-ATTACH-IB-TARGET-HAS-NEVER-RUN`).
* `rr_floor` — shipped in BOTH backtest harnesses while
  `m20_fleet_exit_sweep.py`, the only thing that applies the Path A/B gate,
  had no cell for it. Implemented, measurable, ungradeable
  (`BL-20260818-FLEET-SWEEP-CANNOT-GRADE-THE-RR-FLOOR-LEVER`).
* both exit-coverage audits detecting capability by grepping ONE module, so
  moving a lever to a shared module read as LOSING it — on a real-money family
  (`BL-20260818-CAPABILITY-AUDITS-GREP-ONE-FILE-AND-MISS-SHARED-LEVERS`).

`provenance-consumer-guard` already asserts this shape for a declared FIELD: a
key that gains a writer and no reader fails CI, because a signal written and
never read is worse than a missing one — reviewers see it and assume something
acts on it. There was no equivalent for a LEVER, and the four rows above are
what that absence cost in one day.

THE THREE LEGS, and why each is separately load-bearing
-------------------------------------------------------
**RUNNABLE** — at least one live unit module can actually execute it. A lever
only the harness has is a book production cannot trade, which is the
live-vs-train complaint running backwards, and it makes any passing sweep cell
unactionable: declaring it produces an ORPHANED DECLARE, a YAML key nothing
reads.

**GRADEABLE** — `m20_fleet_exit_sweep.cells_for` emits a cell for it. A lever
nothing grades cannot clear a gate, so it can never be declared on evidence,
so shipping it changed nothing. This is the leg `rr_floor` failed.

**VISIBLE** — `exit_mechanism_coverage.module_implements` sees it. That audit
is what tells an operator which mechanisms a leg has; a lever it cannot see is
reported as coverage the fleet does not have, and the error points the wrong
way — toward building something that already exists.

A lever failing ANY leg is reported with WHICH leg failed, because the remedies
differ completely: RUNNABLE is unit work, GRADEABLE is a sweep cell, VISIBLE is
a detector fix.

NOT THE SAME AS `harness-lever-coupling-guard`
----------------------------------------------
That guard starts from a **YAML key on an enabled strategy** and asks whether the
debt matrix classifies it (PLAIN / LEVER_FLAG / _UNREPLAYABLE / UNMODELLED), so
an unclassified key cannot silently degrade a fidelity row. This one starts from
a **lever** and asks whether its consumers exist. The directions are opposite and
neither subsumes the other: `rr_floor` is out of the coupling guard's scope
ENTIRELY, because no enabled strategy declares it as a key — which is precisely
why it could ship into two harnesses, gain a sweep cell, and still be unrunnable
live without anything noticing.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not check that a lever is DECLARED on any leg. Declaring is a Tier-3
value decision that must rest on sweep evidence, and a guard demanding one
would push exactly the cosmetic declares
`BL-20260730-DONCHIAN-COSMETIC-SHORT-CELLS` warns about. Runnable-graded-visible
is the plumbing; whether to use it stays the operator's.

Usage
-----
    python3 scripts/ci/check_lever_wiring.py
    python3 scripts/ci/check_lever_wiring.py --self-test
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
_UNITS = REPO / "src" / "units" / "strategies"

#: Every lever the system ships. `sweep_lever` is the `matrix_lever` string
#: `cells_for` tags its cells with; `probe_family` is a family whose harness
#: carries the lever, used to ask for a cell.
LEVERS: Dict[str, Dict[str, Any]] = {
    "stale_stop": {"sweep_lever": "stale_stop", "probe_family": "donchian"},
    "giveback_stop": {"sweep_lever": "giveback_stop", "probe_family": "donchian"},
    "trail_decay": {"sweep_lever": "trail_decay", "probe_family": "donchian"},
    # BACKTEST-ONLY TODAY, and this guard is what established that: 23
    # occurrences in each harness, ZERO in any live unit module. So a PASSING
    # rr_floor sweep cell could not be declared — it would be an orphaned
    # declare, the very condition the stale_stop/giveback extraction fixed for
    # the pullback family on the same day. The exemption is RECORDED AND
    # PRINTED rather than the lever being dropped from the table, because a
    # lever missing from LEVERS is invisible to the guard, which is the failure
    # mode this file exists to prevent. Clear it by implementing the lever in
    # the units — NOT by adding it to exit_mechanism_coverage.MECHANISMS, which
    # would make it merely *look* visible while still being unrunnable.
    "rr_floor": {"sweep_lever": "rr_floor", "probe_family": "pullback",
                 "runnable_exempt":
                     "backtest-only; no live unit implements it "
                     "(BL-20260818-RR-FLOOR-IS-BACKTEST-ONLY-AND-CANNOT-BE-DECLARED)"},
    # exit_head is REGISTERED and expected to be runnable+visible, but it is
    # scored from an ML artifact rather than swept as a parameter cell, so it
    # is exempt from GRADEABLE. Recorded as an explicit exemption rather than
    # omitted — a lever missing from this table is invisible to the guard, and
    # that is exactly the failure mode being guarded against.
    "exit_head": {"sweep_lever": None, "probe_family": "donchian",
                  "gradeable_exempt": "scored from an ML artifact, not a swept cell"},
}

#: The live TP clamp. rr_floor is structurally unmeasurable without it, so the
#: gradeable probe must ask under production geometry or it would report a
#: correctly-inert cell as a missing one.
LIVE_TP_CAP = 0.099


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _unit_sources() -> Dict[str, str]:
    return {p.stem: p.read_text() for p in _UNITS.glob("*.py")}


def assess() -> List[Dict[str, Any]]:
    emc = _load("_lw_emc", "scripts/ops/exit_mechanism_coverage.py")
    sweep = _load("_lw_sweep", "scripts/research/m20_fleet_exit_sweep.py")
    units = _unit_sources()

    rows: List[Dict[str, Any]] = []
    for lever, spec in LEVERS.items():
        row: Dict[str, Any] = {"lever": lever, "failures": []}

        # VISIBLE — and it doubles as RUNNABLE, because `module_implements`
        # follows the shared-module import. Which unit is recorded, so a
        # failure names something actionable rather than just "no".
        seen = [n for n, src in units.items()
                if lever in emc.MECHANISMS and emc.module_implements(src, lever)]
        row["visible_in_units"] = sorted(seen)
        if spec.get("runnable_exempt"):
            row["runnable"] = f"exempt: {spec['runnable_exempt']}"
        elif lever in emc.MECHANISMS and not seen:
            row["failures"].append(
                "VISIBLE: exit_mechanism_coverage sees no unit implementing it")
        elif lever not in emc.MECHANISMS:
            row["failures"].append(
                "VISIBLE: not in exit_mechanism_coverage.MECHANISMS at all")

        # GRADEABLE
        if spec.get("gradeable_exempt"):
            row["gradeable"] = f"exempt: {spec['gradeable_exempt']}"
        else:
            cfg = {"timeframe": "2h", "symbols": ["BTCUSDT"], "trail_mult": 3.0}
            cells = sweep.cells_for(cfg, spec["probe_family"], skipped=[],
                                    tp_cap_pct=LIVE_TP_CAP)
            tags = [t for t, lev, _ in cells if lev == spec["sweep_lever"]]
            row["gradeable"] = tags
            if not tags:
                row["failures"].append(
                    f"GRADEABLE: m20_fleet_exit_sweep.cells_for emits no "
                    f"'{spec['sweep_lever']}' cell for family "
                    f"'{spec['probe_family']}' under the live TP cap")
        rows.append(row)
    return rows


def _self_test() -> int:
    """Planted controls. A guard that cannot fail proves nothing.

    The negative is the load-bearing one: it plants the EXACT defect this file
    exists for — a lever a unit can run with no cell in the sweep that grades
    it — and requires the guard to catch it.
    """
    checks = []
    rows = {r["lever"]: r for r in assess()}
    checks.append(("positive: stale_stop is runnable, gradeable and visible",
                   rows["stale_stop"]["failures"] == []))
    checks.append(("positive: rr_floor is gradeable since 2026-08-18",
                   bool(rows["rr_floor"]["gradeable"])))
    checks.append(("positive: the shared extraction is followed (2+ units see stale_stop)",
                   len(rows["stale_stop"]["visible_in_units"]) >= 2))
    checks.append(("positive: exit_head is exempt from GRADEABLE, not failing it",
                   "exempt" in str(rows["exit_head"]["gradeable"])))

    # NEGATIVE CONTROL: an ungradeable lever must be caught.
    saved = LEVERS.get("_planted")
    LEVERS["_planted"] = {"sweep_lever": "no_such_lever", "probe_family": "donchian"}
    try:
        planted = {r["lever"]: r for r in assess()}["_planted"]
        caught = any("GRADEABLE" in f for f in planted["failures"])
    finally:
        LEVERS.pop("_planted", None)
        if saved:
            LEVERS["_planted"] = saved
    checks.append(("negative: a lever with no sweep cell IS caught", caught))
    checks.append(("positive: rr_floor's backtest-only status is recorded, not hidden",
                   "exempt" in str(rows["rr_floor"].get("runnable", ""))))

    ok = sum(bool(p) for _, p in checks)
    for label, passed in checks:
        print(f"  {'ok  ' if passed else 'FAIL'} {label}")
    print(f"self-test: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    rows = assess()
    bad = [r for r in rows if r["failures"]]
    for r in rows:
        mark = "FAIL" if r["failures"] else "ok  "
        note = r.get("runnable") or ""
        print(f"  {mark} {r['lever']:16s} units={len(r['visible_in_units'])} "
              f"cells={r['gradeable']}")
        if note:
            print(f"       ! {note}")
        for f in r["failures"]:
            print(f"       - {f}")
    if bad:
        print(f"\nlever-wiring-guard: {len(bad)} lever(s) not fully wired.")
        print("A lever that cannot be RUN is a book production cannot trade; one "
              "that cannot be GRADED can never be declared on evidence; one that "
              "is not VISIBLE is reported as coverage the fleet does not have.")
        return 1
    print(f"lever-wiring-guard: clean ({len(rows)} levers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
