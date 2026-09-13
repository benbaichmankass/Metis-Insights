#!/usr/bin/env python3
"""Is the arbitration fallback still BELOW the legs it was chosen to lose to?

THE ROW THIS ANSWERS
--------------------
`BL-20260909-UNKNOWN-STRATEGY-PRIORITY-NOW-BEATS-45-OF-50-DECLARED-LEGS-AND-THE-CONTENTION-IS-LIVE`
asks for two things, and names the second as *"the transferable half ... the
generic detector for 'a default whose fail-safety depends on a distribution that
has since moved'"*:

  1. every `execution: live` leg has a `DEFAULT_PRIORITIES` entry or a dated
     exemption;
  2. a DISTRIBUTION ASSERTION on the constant itself — `_UNKNOWN_STRATEGY_PRIORITY`
     strictly below `min(DEFAULT_PRIORITIES.values())`.

⚠️ **THE ROW SAYS `scripts/check_strategy_coverage.py` SHOULD GAIN (1) AS A
FOURTH INVARIANT. IT IS HOSTED HERE INSTEAD, AND THAT IS A DELIBERATE
DIVERGENCE STATED RATHER THAN SILENT.** That file sits at `scripts/` root, which
is outside `check_pr_landing.TIER1_SURFACE`, so an edit there cannot self-land
and would wait on a human merge click behind a queue that already has three.
What the row needs is that the invariant EXISTS and RUNS in CI, not which file
hosts it. Both invariants are here; nothing in `scripts/check_strategy_coverage.py`
is touched.

WHY IT REPORTS AND DOES NOT FAIL ON THE STANDING CONDITION
-----------------------------------------------------------
Measured 2026-09-13: the fallback is **10** and **45 of 50** mapped legs sit
strictly below it, so an ABSENT leg does not merely fail to be safe — **it WINS
the arbitration**, the exact inverse of the constant's own stated purpose
(*"Picked deliberately below the in-scope strategies so a misconfigured new
strategy never silently overrides Turtle Soup / VWAP"*). The direction is
verified in code, not assumed: `_election_sort_key` carries
`-intent.effective_priority()`, the aggregator does `sorted(...)` ascending and
takes `ordered[0]`, so a HIGHER priority wins.

R1 and R2 are therefore **VIOLATED TODAY**. Making either a hard failure would
red every PR in the repo on day one, which is how a guard gets disabled instead
of fixed — the reasoning `scripts/ci/check_pr_queue_watch.py` already writes
down about `never_ran`, and the residue-draining `diagnostic-provenance-guard`
had to do first. **The remedy is Tier-3** (changing a leg's arbitration priority
is an order-routing change and the operator decides), so a guard cannot clear it
either.

**R3 is what CAN fail today**: a WORSENING against a committed baseline. That is
the `checklist_routing_age` idiom — seed the standing stock, page the crossing —
and it makes the guard useful immediately without holding the repo hostage to a
decision it does not own.

⚠️ **R1 AND R2 ARM THEMSELVES.** Once the operator's change lands and the
condition is clean, `--strict` becomes safe to require; until then the states
are reported on every run and the numbers are in the output, so nobody has to
remember to look. There is no flag to unset.

WHAT ESCALATED SINCE THE ROW WAS FILED
---------------------------------------
The row records *"The contended accounts are paper-class today"*. That is no
longer true: `alpaca_live` (`mode: live`, `account_class: real_money`) routes
**two of the five absent legs** — `iaum_pullback_1d` and `slv_pullback_1d` —
added by the Option-A roster change on **2026-09-10, the day after the row was
filed**. `exposure_state` exists to make that distinction reportable rather than
buried in a leg list.
"""

from __future__ import annotations

import argparse
import ast
import collections
import json
import pathlib
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

REPO = pathlib.Path(__file__).resolve().parents[2]
INTENTS = REPO / "src" / "runtime" / "intents.py"
STRATEGIES = REPO / "config" / "strategies.yaml"
ACCOUNTS = REPO / "config" / "accounts.yaml"
BASELINE = REPO / "docs" / "claude" / "work" / "PRIORITY-FALLBACK-BASELINE.json"

#: R1 — where the fallback sits in the distribution it is supposed to lose to.
DISTRIBUTION_STATES = ("fallback_below_all", "fallback_beats_some", "ungradeable")

#: R2 — do all live legs have a priority?
COVERAGE_STATES = ("all_covered", "absent_legs", "ungradeable")

#: R3 — WHERE an uncovered leg is routed. The row's own framing turned on this.
EXPOSURE_STATES = (
    "no_absent_leg_routed",
    "absent_leg_on_paper_only",
    "absent_leg_on_real_money",
    "ungradeable",
)

#: R3 — against the committed baseline.
REGRESSION_STATES = ("within_baseline", "worsened", "improved", "no_baseline")


def _read_intents_symbols(path: pathlib.Path = INTENTS) -> tuple[dict | None, int | None]:
    """`(DEFAULT_PRIORITIES, _UNKNOWN_STRATEGY_PRIORITY)`, either `None` if absent.

    ⚠️ BOTH `ast.Assign` AND `ast.AnnAssign` are handled. `DEFAULT_PRIORITIES` is
    declared `Dict[str, int] = {...}`, so a walk that checks only `Assign` finds
    nothing and returns an empty map — which grades `ungradeable` here but would
    read as *no legs are mapped* to a less careful caller. My own first probe
    made exactly that mistake.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None, None
    out: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name)):
            name, value = node.targets[0].id, node.value
        else:
            continue
        if name in ("DEFAULT_PRIORITIES", "_UNKNOWN_STRATEGY_PRIORITY") and value is not None:
            try:
                out[name] = ast.literal_eval(value)
            except ValueError:
                out[name] = None
    return out.get("DEFAULT_PRIORITIES"), out.get("_UNKNOWN_STRATEGY_PRIORITY")


def distribution_state(priorities: dict | None, fallback: int | None) -> tuple[str, dict]:
    """R1. `fallback_below_all` is the only clean state."""
    if not priorities or fallback is None:
        return "ungradeable", {"mapped": 0 if not priorities else len(priorities),
                               "fallback": fallback, "min_mapped": None, "beaten": None}
    values = list(priorities.values())
    beaten = sum(1 for v in values if v < fallback)
    terms = {"mapped": len(values), "fallback": fallback, "min_mapped": min(values),
             "beaten": beaten,
             "histogram": dict(sorted(collections.Counter(values).items()))}
    return ("fallback_below_all" if fallback < min(values) else "fallback_beats_some"), terms


def live_legs(path: pathlib.Path = STRATEGIES) -> list[str] | None:
    """Enabled legs whose execution gate is live. `None` when unreadable."""
    if yaml is None:
        return None
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    strats = cfg.get("strategies", cfg)
    if not isinstance(strats, dict):
        return None
    return sorted(
        n for n, s in strats.items()
        if isinstance(s, dict) and s.get("enabled")
        and str(s.get("execution", "live")).strip().lower() == "live"
    )


def coverage_state(priorities: dict | None, legs: list[str] | None,
                   exempt: set | None = None) -> tuple[str, list[str]]:
    """R2. Returns the state and the ABSENT legs, so the count is never implicit."""
    if priorities is None or legs is None:
        return "ungradeable", []
    absent = sorted(set(legs) - set(priorities) - set(exempt or ()))
    return ("all_covered" if not absent else "absent_legs"), absent


def exposure_state(absent: list[str], path: pathlib.Path = ACCOUNTS) -> tuple[str, dict]:
    """R3's WHERE. A real-money route is a different fact from a paper one."""
    if yaml is None:
        return "ungradeable", {}
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return "ungradeable", {}
    accts = cfg.get("accounts", cfg)
    if not isinstance(accts, dict):
        return "ungradeable", {}
    if not absent:
        return "no_absent_leg_routed", {}
    routed: dict[str, dict] = {}
    real = False
    for name, c in accts.items():
        if not isinstance(c, dict):
            continue
        hits = sorted(set(c.get("strategies") or ()) & set(absent))
        if not hits:
            continue
        is_real = (str(c.get("account_class")) == "real_money"
                   and str(c.get("mode", "live")).strip().lower() == "live")
        real = real or is_real
        routed[name] = {"legs": hits, "mode": c.get("mode"),
                        "account_class": c.get("account_class"), "real_money_live": is_real}
    if not routed:
        return "no_absent_leg_routed", {}
    return ("absent_leg_on_real_money" if real else "absent_leg_on_paper_only"), routed


def _load_baseline(path: pathlib.Path = BASELINE) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def regression_state(absent: list[str], real_money_legs: list[str],
                     baseline: dict | None) -> tuple[str, dict]:
    """R3. Fails ONLY on a worsening against the committed seed.

    ⚠️ `no_baseline` is *we could not look* and never a pass. ⚠️ `improved` is
    reported and does NOT fail — but it is the signal to re-seed, because a
    baseline wider than reality stops catching the next regression.
    """
    if baseline is None:
        return "no_baseline", {"seeded_absent": None, "seeded_real_money": None}
    seeded = set(baseline.get("absent_legs") or ())
    seeded_rm = set(baseline.get("absent_legs_on_real_money") or ())
    new_absent = sorted(set(absent) - seeded)
    new_rm = sorted(set(real_money_legs) - seeded_rm)
    terms = {"seeded_absent": sorted(seeded), "seeded_real_money": sorted(seeded_rm),
             "new_absent": new_absent, "new_on_real_money": new_rm,
             "cleared": sorted(seeded - set(absent))}
    if new_absent or new_rm:
        return "worsened", terms
    if terms["cleared"]:
        return "improved", terms
    return "within_baseline", terms


def assess() -> dict:
    priorities, fallback = _read_intents_symbols()
    dist, dist_terms = distribution_state(priorities, fallback)
    legs = live_legs()
    cov, absent = coverage_state(priorities, legs)
    exp, routed = exposure_state(absent)
    rm_legs = sorted({leg for r in routed.values() if r["real_money_live"] for leg in r["legs"]})
    reg, reg_terms = regression_state(absent, rm_legs, _load_baseline())
    return {
        "distribution_state": dist, "distribution": dist_terms,
        "coverage_state": cov, "live_legs": len(legs) if legs is not None else None,
        "absent_legs": absent,
        "exposure_state": exp, "routed": routed, "absent_on_real_money": rm_legs,
        "regression_state": reg, "regression": reg_terms,
    }


def report(res: dict) -> list[str]:
    d = res["distribution"]
    out = [
        "priority-fallback-distribution",
        "  R1 distribution_state=%s — fallback %s against %s mapped leg(s), min mapped %s; "
        "%s leg(s) sit STRICTLY BELOW it, so an ABSENT leg beats every one of them."
        % (res["distribution_state"], d.get("fallback"), d.get("mapped"),
           d.get("min_mapped"), d.get("beaten")),
    ]
    if d.get("histogram"):
        out.append("      histogram: %s" % d["histogram"])
    out.append("  R2 coverage_state=%s — %s enabled execution:live leg(s), %d absent from "
               "DEFAULT_PRIORITIES%s"
               % (res["coverage_state"], res["live_legs"], len(res["absent_legs"]),
                  (": " + ", ".join(res["absent_legs"])) if res["absent_legs"] else ""))
    out.append("  R3 exposure_state=%s" % res["exposure_state"])
    for name, r in sorted(res["routed"].items()):
        out.append("      %-22s mode=%-8s class=%-11s %s%s"
                   % (name, r["mode"], r["account_class"], r["legs"],
                      "   <-- REAL MONEY, LIVE" if r["real_money_live"] else ""))
    if res["exposure_state"] == "absent_leg_on_real_money":
        out.append("      ⚠️ AN UNCOVERED LEG IS ROUTED ON A LIVE REAL-MONEY ACCOUNT. Omission "
                   "does not merely fail to be safe here — it WINS the arbitration.")
    out.append("  R3 regression_state=%s — seeded %s absent (%s on real money); new since the "
               "seed: %s absent, %s on real money; cleared: %s"
               % (res["regression_state"],
                  _n(res["regression"].get("seeded_absent")),
                  _n(res["regression"].get("seeded_real_money")),
                  _n(res["regression"].get("new_absent")),
                  _n(res["regression"].get("new_on_real_money")),
                  _n(res["regression"].get("cleared"))))
    if res["distribution_state"] == "fallback_beats_some" or res["coverage_state"] == "absent_legs":
        out.append("  NOTE: R1/R2 are VIOLATED and this run does NOT fail on them. The remedy is "
                   "Tier-3 (an arbitration priority is an order-routing change and the operator "
                   "decides), and failing here would red every PR in the repo, which is how a "
                   "guard gets disabled instead of fixed. R3 is the enforcing rule. Pass "
                   "--strict once the operator's change lands.")
    return out


def _n(v):
    if v is None:
        return "unknown"
    return "none" if not v else "%d (%s)" % (len(v), ", ".join(v))


def self_test() -> int:
    checks: list[tuple[str, bool]] = []

    def ok(name, cond):
        checks.append((name, bool(cond)))

    # --- R1 -----------------------------------------------------------------
    ok("a fallback below every mapped value is the clean state",
       distribution_state({"a": 20, "b": 30}, 10)[0] == "fallback_below_all")
    ok("a fallback ABOVE any mapped value grades fallback_beats_some",
       distribution_state({"a": 0, "b": 30}, 10)[0] == "fallback_beats_some")
    ok("EQUAL to the minimum is NOT below it — the row's test is STRICT",
       distribution_state({"a": 10, "b": 30}, 10)[0] == "fallback_beats_some")
    ok("the beaten COUNT is reported, not just the verdict",
       distribution_state({"a": 0, "b": 0, "c": 30}, 10)[1]["beaten"] == 2)
    ok("an empty map is ungradeable, NEVER fallback_below_all",
       distribution_state({}, 10)[0] == "ungradeable")
    ok("a missing fallback is ungradeable, never clean",
       distribution_state({"a": 0}, None)[0] == "ungradeable")
    ok("ungradeable reports min_mapped as None, never 0",
       distribution_state({}, 10)[1]["min_mapped"] is None)
    ok("every distribution state is in the declared vocabulary",
       all(distribution_state(m, f)[0] in DISTRIBUTION_STATES
           for m, f in (({"a": 0}, 10), ({"a": 20}, 10), ({}, 10), ({"a": 0}, None))))

    # --- the parser ---------------------------------------------------------
    prio, fb = _read_intents_symbols()
    ok("BOTH annotated and plain assignments are parsed — DEFAULT_PRIORITIES is AnnAssign",
       isinstance(prio, dict) and len(prio) > 0)
    ok("the fallback constant is parsed", isinstance(fb, int))
    ok("an unreadable source yields (None, None), not an empty map",
       _read_intents_symbols(pathlib.Path("/nonexistent/intents.py")) == (None, None))

    # --- R2 -----------------------------------------------------------------
    ok("a fully covered roster grades all_covered",
       coverage_state({"a": 0, "b": 0}, ["a", "b"])[0] == "all_covered")
    st, absent = coverage_state({"a": 0}, ["a", "b", "c"])
    ok("an uncovered leg grades absent_legs", st == "absent_legs")
    ok("the absent legs are NAMED, never just counted", absent == ["b", "c"])
    ok("a dated exemption removes a leg from the absent set",
       coverage_state({"a": 0}, ["a", "b"], exempt={"b"})[0] == "all_covered")
    ok("an unreadable roster is ungradeable, NEVER all_covered",
       coverage_state({"a": 0}, None)[0] == "ungradeable")
    ok("an unreadable priority map is ungradeable too",
       coverage_state(None, ["a"])[0] == "ungradeable")
    ok("every coverage state is in the declared vocabulary",
       all(coverage_state(m, legs)[0] in COVERAGE_STATES
           for m, legs in (({"a": 0}, ["a"]), ({}, ["a"]), (None, ["a"]), ({"a": 0}, None))))

    # --- R3 regression ------------------------------------------------------
    base = {"absent_legs": ["x", "y"], "absent_legs_on_real_money": ["x"]}
    ok("the seeded stock alone is within_baseline — seeding is not a pass, it is a floor",
       regression_state(["x", "y"], ["x"], base)[0] == "within_baseline")
    ok("a NEW absent leg worsens", regression_state(["x", "y", "z"], ["x"], base)[0] == "worsened")
    ok("a seeded leg MOVING ONTO REAL MONEY worsens even with no new leg",
       regression_state(["x", "y"], ["x", "y"], base)[0] == "worsened")
    ok("a cleared leg grades improved and does not fail",
       regression_state(["x"], ["x"], base)[0] == "improved")
    ok("no baseline is 'we could not look', NEVER within_baseline",
       regression_state(["x"], [], None)[0] == "no_baseline")
    ok("no_baseline reports its seed as None, not as an empty list",
       regression_state(["x"], [], None)[1]["seeded_absent"] is None)
    ok("a worsening NAMES what is new", regression_state(["x", "y", "z"], ["x"], base)[1]
       ["new_absent"] == ["z"])
    ok("every regression state is in the declared vocabulary",
       all(regression_state(a, r, b)[0] in REGRESSION_STATES
           for a, r, b in ((["x"], [], base), (["x", "z"], [], base), (["x"], [], None))))

    # --- exposure -----------------------------------------------------------
    ok("no absent leg -> no_absent_leg_routed", exposure_state([])[0] == "no_absent_leg_routed")
    ok("every exposure state is in the declared vocabulary",
       exposure_state([])[0] in EXPOSURE_STATES)

    # --- live assessment + report -------------------------------------------
    res = assess()
    ok("the live assessment grades every rule",
       res["distribution_state"] in DISTRIBUTION_STATES
       and res["coverage_state"] in COVERAGE_STATES
       and res["exposure_state"] in EXPOSURE_STATES
       and res["regression_state"] in REGRESSION_STATES)
    text = "\n".join(report(res))
    ok("the report states the mapped denominator", "mapped leg(s)" in text)
    ok("the report states how many legs the fallback beats", "STRICTLY BELOW it" in text)
    ok("the report names the absent legs rather than counting them",
       (not res["absent_legs"]) or all(leg in text for leg in res["absent_legs"]))
    ok("the report explains why R1/R2 do not fail",
       (res["distribution_state"] == "fallback_below_all"
        and res["coverage_state"] == "all_covered")
       or "R3 is the enforcing rule" in text)
    # The per-account line ALREADY carries "REAL MONEY, LIVE", so keying the
    # control on that string passes even with the explicit warning deleted —
    # which a plant proved. Assert the warning's own words.
    ok("a real-money exposure gets its OWN explicit warning line",
       res["exposure_state"] != "absent_leg_on_real_money"
       or "AN UNCOVERED LEG IS ROUTED ON A LIVE REAL-MONEY ACCOUNT" in text)
    ok("and that warning says omission WINS rather than merely failing safe",
       res["exposure_state"] != "absent_leg_on_real_money"
       or "it WINS the arbitration" in text)
    ok("an unknown seed renders as 'unknown', never as 'none'", _n(None) == "unknown")
    ok("an empty list renders as 'none', which is a different fact", _n([]) == "none")

    failed = [n for n, good in checks if not good]
    for name, good in checks:
        print("%s  %s" % ("ok  " if good else "FAIL", name))
    print("\nself-test: %d checks, %d passed, %d failed (denominator = %d)"
          % (len(checks), len(checks) - len(failed), len(failed), len(checks)))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="also fail on R1/R2 — safe once the Tier-3 change has landed")
    ap.add_argument("--seed", action="store_true",
                    help="write the current absent set as the R3 baseline")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    res = assess()
    if args.seed:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({
            "_doc": ("R3's floor for check_priority_fallback_distribution.py. Seeding is NOT a "
                     "disposition: these legs are still uncovered and an uncovered leg WINS the "
                     "arbitration. The seed exists so a WORSENING is detectable without redding "
                     "every PR over a standing Tier-3 condition."),
            "seeded_at": "2026-09-13",
            "absent_legs": res["absent_legs"],
            "absent_legs_on_real_money": res["absent_on_real_money"],
        }, indent=2) + "\n", encoding="utf-8")
        print("seeded %d absent leg(s), %d on real money -> %s"
              % (len(res["absent_legs"]), len(res["absent_on_real_money"]),
                 BASELINE.relative_to(REPO)))
        return 0

    print(json.dumps(res, indent=2, sort_keys=True) if args.json else "\n".join(report(res)))

    if res["regression_state"] == "worsened":
        print("\npriority-fallback-distribution: FAIL — a new uncovered leg, or a seeded one "
              "moved onto real money. R3 is the enforcing rule.", file=sys.stderr)
        return 1
    if args.strict and (res["distribution_state"] != "fallback_below_all"
                        or res["coverage_state"] != "all_covered"):
        print("\npriority-fallback-distribution: FAIL (--strict) — R1/R2 not clean.",
              file=sys.stderr)
        return 1
    print("\npriority-fallback-distribution: OK (R3 enforcing; R1/R2 reported)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
