#!/usr/bin/env python3
"""M31 P1 — a declared R-threshold exit lever must be REACHABLE under its own TP cap.

THE DEFECT THIS EXISTS TO STOP RECURRING
----------------------------------------
The donchian/pullback/fade/squeeze live units clamp take-profit to
``min(entry*(1+0.099), entry + tp_r*risk)``, so with ``tp_r: 50.0`` the venue
cap always binds and the highest MFE a trade can print before its TP fills is
``cap_R = 0.099 / (risk/entry)``. A lever that arms when the since-entry
favourable extreme reaches ``arm_r`` R therefore **cannot fire** on any leg
where ``arm_r > cap_R``.

Measured 2026-08-16 (`BL-20260816-TRAIL-DECAY-ARM-R-SITS-ABOVE-THE-VENUE-TP-CAP`,
PR #9588): `gld_pullback_1d` declares `trail_decay_arm_r: 5.06` against a cap_R
of 2.20-3.01 across its **complete** package history — 0 of 8. The lever is
declared, Tier-3-approved, visible in YAML, and inert. Nothing anywhere
asserted the relationship, so an inert lever was indistinguishable from an
armed one that had simply not triggered yet.

WHAT THIS GUARD CAN AND CANNOT DO
---------------------------------
CI cannot measure a leg — it has no journal and no market data. So this guard
does **not** compute reachability. It enforces that a measurement EXISTS, is
CURRENT for the value actually declared, and has been DISPOSED of:

1. every live+enabled leg declaring a reach-gate on a capped family has a
   registry entry;
2. the entry's ``arm_r`` equals the value in ``config/strategies.yaml`` — so
   changing an arm without re-measuring FAILS, which is the recurrence path;
3. a leg measured ``inert`` / ``vol_conditional`` carries an explicit
   disposition, not silence;
4. an ``unmeasured`` entry states WHY, and cannot be the resting state of a
   value someone just changed.

⚠️ **THIS IS NOT A PRESENCE-ONLY MARKER.** The `arm_r` match is what makes it
expensive to lie to: satisfying the guard by editing the registry means
restating the measured numbers next to the new value, which is the review the
guard exists to force. (The direct lesson from `new-table-wiring-guard`, whose
presence-only marker made naming a non-existent table the cheapest way to
silence a real finding.)

Usage
-----
    python3 scripts/ci/check_lever_reachability.py
    python3 scripts/ci/check_lever_reachability.py --self-test
    python3 scripts/ci/check_lever_reachability.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO / "config" / "lever_reachability.json"
STRATEGIES_PATH = REPO / "config" / "strategies.yaml"

sys.path.insert(0, str(REPO / "scripts" / "ops"))

# Levers that arm by REACHING an MFE-in-R threshold. A "below R" gate
# (stale_exit_below_r) fires in the opposite direction and is not in scope —
# grading it against a ceiling would be a category error.
REACH_GATE_KEYS = ("trail_decay_arm_r", "giveback_min_mfe_r")

# Never collapsed. `unmeasured` is NOT `reachable`, and `vol_conditional` is
# neither — a lever that arms on a third of entries is a real design choice
# that must be made deliberately, not defaulted into.
VERDICTS = {"reachable", "inert", "vol_conditional", "unmeasured"}

# `ok` asserts the lever is fine as declared. The other two are open states
# that a review can query; both require evidence of WHEN they were opened, so
# an indefinite park is visible rather than silent.
DISPOSITIONS = {"ok", "queued_tier3", "accepted_risk"}
DISPOSITIONS_REQUIRING_DATE = {"queued_tier3", "accepted_risk"}
VERDICTS_REQUIRING_DISPOSITION = {"inert", "vol_conditional"}


def _cap_applies(strategy: str):
    """Delegate to the audit's resolver — one definition, not two."""
    from lever_reachability_audit import cap_applies
    return cap_applies(strategy)


def declared_gates(strategies: Dict[str, Any]) -> List[Tuple[str, str, float]]:
    """(leg, lever, arm_r) for every live+enabled leg on a CAPPED family."""
    out: List[Tuple[str, str, float]] = []
    for name, cfg in (strategies or {}).items():
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        if cfg.get("execution", "live") != "live":
            continue
        capped, _basis = _cap_applies(name)
        if capped is not True:
            # Not established as capped => no ceiling to check against. This
            # under-claims deliberately; `cap_applies` returns None, never
            # False, for "we could not establish it".
            continue
        for key in REACH_GATE_KEYS:
            try:
                arm = float(cfg.get(key))
            except (TypeError, ValueError):
                continue
            if arm > 0:
                out.append((name, key, arm))
    return out


def check(strategies: Dict[str, Any], registry: Dict[str, Any]) -> List[str]:
    """Return a list of violations; empty means pass."""
    entries = {(e.get("strategy"), e.get("lever")): e
               for e in (registry.get("levers") or [])
               if isinstance(e, dict)}
    problems: List[str] = []

    for leg, lever, arm in declared_gates(strategies):
        entry = entries.get((leg, lever))
        if entry is None:
            problems.append(
                f"{leg}/{lever}: declared arm_r={arm} with NO entry in "
                f"config/lever_reachability.json — a reach-gate on a capped "
                f"family must record whether its arm is reachable")
            continue

        try:
            recorded = float(entry.get("arm_r"))
        except (TypeError, ValueError):
            problems.append(f"{leg}/{lever}: registry arm_r is not a number")
            continue
        if abs(recorded - arm) > 1e-9:
            problems.append(
                f"{leg}/{lever}: config declares arm_r={arm} but the registry "
                f"measured arm_r={recorded} — the value changed without a "
                f"re-measurement. Re-measure against the leg's cap_R and "
                f"update the entry.")

        verdict = entry.get("verdict")
        if verdict not in VERDICTS:
            problems.append(
                f"{leg}/{lever}: verdict {verdict!r} not in {sorted(VERDICTS)}")
            continue

        disposition = entry.get("disposition")
        if verdict in VERDICTS_REQUIRING_DISPOSITION:
            if disposition not in DISPOSITIONS:
                problems.append(
                    f"{leg}/{lever}: verdict={verdict} requires an explicit "
                    f"disposition in {sorted(DISPOSITIONS)}, got "
                    f"{disposition!r}")
            elif disposition == "ok":
                problems.append(
                    f"{leg}/{lever}: verdict={verdict} cannot carry "
                    f"disposition='ok' — a lever that cannot reliably fire is "
                    f"either corrected (Tier-3) or recorded as "
                    f"queued_tier3/accepted_risk")
        if disposition in DISPOSITIONS_REQUIRING_DATE and not entry.get("opened_at"):
            problems.append(
                f"{leg}/{lever}: disposition={disposition} requires "
                f"`opened_at` so an indefinite park is visible")

        if verdict == "unmeasured":
            if not entry.get("unmeasured_reason"):
                problems.append(
                    f"{leg}/{lever}: verdict='unmeasured' requires "
                    f"`unmeasured_reason` — 'we did not look' must say why")
        else:
            try:
                n = int(entry.get("observations"))
            except (TypeError, ValueError):
                n = -1
            if n < 1:
                problems.append(
                    f"{leg}/{lever}: verdict={verdict} claims a measurement "
                    f"but `observations` is {entry.get('observations')!r} — a "
                    f"verdict over no observations is not a measurement")
            if not entry.get("basis"):
                problems.append(
                    f"{leg}/{lever}: verdict={verdict} must name its `basis` "
                    f"(e.g. order_packages/risk_per_unit) so a reader knows "
                    f"which denominator produced it")

    return problems


def _load(path: Path) -> Dict[str, Any]:
    import yaml
    doc = yaml.safe_load(path.read_text()) or {}
    return doc.get("strategies", doc)


def _self_test() -> int:
    """Prove each failure path fires. A guard whose negative is never
    exercised is indistinguishable from one that always passes."""
    base_cfg = {"xrp_pullback_2h": {"enabled": True, "execution": "live",
                                    "trail_decay_arm_r": 4.49}}

    def reg(**over):
        e = {"strategy": "xrp_pullback_2h", "lever": "trail_decay_arm_r",
             "arm_r": 4.49, "verdict": "reachable", "observations": 30,
             "basis": "order_packages/risk_per_unit"}
        e.update(over)
        return {"levers": [e]}

    cases = [
        ("missing entry", base_cfg, {"levers": []}, "NO entry"),
        ("arm drifted", base_cfg, reg(arm_r=2.0), "without a re-measurement"),
        ("bad verdict", base_cfg, reg(verdict="fine"), "not in"),
        ("inert as ok", base_cfg,
         reg(verdict="inert", disposition="ok"), "cannot carry"),
        ("inert no disposition", base_cfg, reg(verdict="inert"), "requires an explicit"),
        ("queued without date", base_cfg,
         reg(verdict="inert", disposition="queued_tier3"), "requires `opened_at`"),
        ("unmeasured without reason", base_cfg,
         reg(verdict="unmeasured"), "must say why"),
        ("verdict over zero observations", base_cfg,
         reg(observations=0), "not a measurement"),
        ("no basis", base_cfg, reg(basis=None), "must name its `basis`"),
    ]
    failures = 0
    for label, cfg, registry, expect in cases:
        got = check(cfg, registry)
        if not any(expect in p for p in got):
            print(f"  SELF-TEST FAIL [{label}]: expected {expect!r}, got {got}")
            failures += 1
        else:
            print(f"  ok  [{label}]")

    clean = check(base_cfg, reg())
    if clean:
        print(f"  SELF-TEST FAIL [clean passes]: {clean}")
        failures += 1
    else:
        print("  ok  [clean passes]")

    print(f"self-test: {len(cases) + 1 - failures}/{len(cases) + 1} passed")
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    strategies = _load(STRATEGIES_PATH)
    try:
        registry = json.loads(REGISTRY_PATH.read_text())
    except FileNotFoundError:
        print(f"::error::{REGISTRY_PATH.relative_to(REPO)} is missing — every "
              f"declared R-threshold lever needs a reachability record")
        return 1
    except json.JSONDecodeError as exc:
        print(f"::error::{REGISTRY_PATH.relative_to(REPO)} is not valid JSON: {exc}")
        return 1

    gates = declared_gates(strategies)
    problems = check(strategies, registry)

    if a.json:
        print(json.dumps({"gates": [{"strategy": s, "lever": k, "arm_r": v}
                                    for s, k, v in gates],
                          "violations": problems}, indent=2))
        return 1 if problems else 0

    print(f"lever-reachability: {len(gates)} declared reach-gate(s) on capped "
          f"live legs")
    for s, k, v in gates:
        print(f"  {s:26} {k:22} arm_r={v}")
    if problems:
        print()
        for p in problems:
            print(f"::error::lever-reachability: {p}")
        return 1
    print("\nOK — every declared reach-gate has a current, disposed measurement.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
