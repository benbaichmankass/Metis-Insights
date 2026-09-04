#!/usr/bin/env python3
"""When a demotion's TUNING BUDGET runs out, what happens to the row?

(c) of `docs/design/strategy-demote-and-tune-DESIGN.md`, which is the half the
operator agreed on 2026-09-04 ("Agree flow + fund REPAIR diagnosis") and the half
that was DESIGNED AND NOT BUILT. This module is the build.

WHY A BUDGET RATHER THAN AN OUTCOME. The design's own reasoning, restated here
because a future reader will be tempted to bound this on the RESULT instead:
`strategy_gate.py::GateThresholds.min_live_trades` is 30, and a 1d equity leg
trades roughly 4 times a year, so "wait until the gate can grade it" is a
~7.5-year wait. A bound on the outcome is therefore not a bound at all. The
budget is counted in WEEKLY SUNSET PASSES — the cadence that actually reads this
— and it expires whether or not anything was learned.

THE ONE RULE THAT MAKES IT A FORCING FUNCTION: at expiry the row **cannot stay
demoted**. It becomes `promote_proposed` or `retire_proposed`. A demotion that
can quietly persist is the failure this whole design exists to prevent — a leg
parked in shadow forever, costing evaluation budget, answering nothing.

⚠️ EVERY OUTCOME IS A PROPOSAL. Retiring or promoting a leg is Tier-3. Nothing
here writes `config/strategies.yaml`, and nothing here changes an `execution:`
field. It emits a verdict a human answers.

⚠️ SEVEN STATES, NEVER COLLAPSED — registered in the module docstring for the
same reason `exit_anchor.py` keeps `anchored`/`deferred`/`no_anchor` apart:

  within_budget          the budget is running; no exit is owed yet
  return_to_live         the gate graded PROPOSE_PROMOTE_TO_LIVE
  retire_graded          budget spent AND the gate RAN and held it below the bar
  retire_never_gradeable budget spent and the gate NEVER ran — a different and
                         more useful statement than "this leg lost money", and it
                         must never be rendered as if it were
  malformed              the row does not carry the four required fields, so no
                         budget EXISTS to be spent — never `within_budget`
  unreadable             we could not read what we needed (an unparseable date,
                         a non-integer budget) — *we did not look*, never a pass
  not_demoted            the row is not a demotion at all; this module has no
                         opinion on it

`retire_graded` and `retire_never_gradeable` both propose retirement and are
still two different findings: one says the leg was measured and failed, the other
says two months of shadow produced no measurement. Collapsing them would let a
reader conclude a leg lost money when nobody ever graded it.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

# The four fields a demotion must carry. A demotion without them is REFUSED
# rather than defaulted: a hypothesis invented after the fact is not a
# hypothesis, and a budget nobody declared cannot expire.
REQUIRED_FIELDS = ("demoted_at", "hypothesis", "lever", "tuning_budget_passes")

DEFAULT_BUDGET_PASSES = 8  # ≈ two months at the weekly sunset cadence

# The gate verdicts this module understands, from scripts/strategy_gate.py.
# ⚠️ An unrecognised verdict is `unreadable`, never "not promotable" — the
# fail-closed polarity this repo applies to every allowlist.
PROMOTE = "PROPOSE_PROMOTE_TO_LIVE"
KEEP_SHADOW = "KEEP_SHADOW"
NOT_GRADED = "HOLD_SHADOW_COLLECT_DATA"
KNOWN_VERDICTS = {PROMOTE, KEEP_SHADOW, NOT_GRADED}

STATES = (
    "within_budget",
    "return_to_live",
    "retire_graded",
    "retire_never_gradeable",
    "malformed",
    "unreadable",
    "not_demoted",
)

# Which states PROPOSE a Tier-3 move, and which propose nothing. Kept as data so
# a caller cannot re-derive it and drift.
PROPOSES = {
    "return_to_live": "promote_proposed",
    "retire_graded": "retire_proposed",
    "retire_never_gradeable": "retire_proposed",
}


def _as_date(v: Any) -> Optional[date]:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        try:
            return date.fromisoformat(v.strip()[:10])
        except ValueError:
            return None
    return None


def passes_elapsed(demoted_at: Any, today: Optional[date] = None,
                   *, pass_days: int = 7) -> Optional[int]:
    """Whole WEEKLY passes since the demotion. ``None`` when undateable.

    ⚠️ Counted from the DATE, not from a count of observed pass files. A pass
    that did not run is still a week the leg spent in shadow, and this repo has
    measured scheduled workflows firing late and skipping days
    (`probes.yml` fired 4h50m late and once rather than daily). Deriving the
    budget from files present would let a broken scheduler silently extend
    every demotion — the budget would stop being a bound.
    """
    d = _as_date(demoted_at)
    if d is None:
        return None
    today = today or datetime.now(timezone.utc).date()
    return max(0, (today - d).days // pass_days)


def resolve_exit(row: Any, *, gate_verdict: Optional[str] = None,
                 today: Optional[date] = None) -> Dict[str, Any]:
    """The pure decision. No I/O, no config read, no clock beyond ``today``.

    ``gate_verdict`` is the M7 gate's own output for this leg — this module
    RE-IMPLEMENTS NO DETECTOR, which is the anti-duplication contract the sunset
    pass already declares. ``None`` means the gate has no verdict for the leg,
    which is the ordinary state of a leg with zero closes and is exactly what
    `retire_never_gradeable` exists to describe.
    """
    out: Dict[str, Any] = {"state": None, "proposes": None, "detail": "", "passes": None}

    if not isinstance(row, dict):
        out["state"] = "unreadable"
        out["detail"] = f"row is {type(row).__name__}, not a mapping"
        return out

    if row.get("disposition") != "demote":
        out["state"] = "not_demoted"
        out["detail"] = f"disposition is {row.get('disposition')!r}"
        return out

    # ⚠️ PRESENCE, NOT TRUTHINESS. `tuning_budget_passes: 0` is PRESENT and wrong,
    # which is a different finding from absent — and a naive `or ""` test reports
    # it as missing. Caught by this module's own self-test, which is the case for
    # writing the self-test before trusting the check.
    def _absent(f: str) -> bool:
        v = row.get(f)
        if v is None:
            return True
        return isinstance(v, str) and not v.strip()

    missing = [f for f in REQUIRED_FIELDS if _absent(f)]
    if missing:
        out["state"] = "malformed"
        out["detail"] = (
            f"missing {', '.join(missing)} — a demotion without these is refused, "
            f"not defaulted: a hypothesis written after the tuning is not a "
            f"hypothesis, and a budget nobody declared cannot expire"
        )
        return out

    budget = row.get("tuning_budget_passes")
    if isinstance(budget, bool) or not isinstance(budget, int):
        # We cannot READ a budget out of this — *we did not look* territory.
        out["state"] = "unreadable"
        out["detail"] = f"tuning_budget_passes is {budget!r}, not an integer"
        return out
    if budget <= 0:
        # Readable, declared, and not a budget. A zero-pass budget would expire
        # the instant it was written, which is a declaration error rather than a
        # read failure — and the two must not be reported as the same thing.
        out["state"] = "malformed"
        out["detail"] = (
            f"tuning_budget_passes is {budget}, which is not a budget: it would "
            f"expire the moment it was declared. Give the demotion a real number "
            f"of passes (default {DEFAULT_BUDGET_PASSES}) or do not demote."
        )
        return out

    n = passes_elapsed(row.get("demoted_at"), today)
    if n is None:
        out["state"] = "unreadable"
        out["detail"] = f"demoted_at {row.get('demoted_at')!r} is not a readable date"
        return out
    out["passes"] = n

    if gate_verdict is not None and gate_verdict not in KNOWN_VERDICTS:
        out["state"] = "unreadable"
        out["detail"] = (
            f"gate verdict {gate_verdict!r} is not one of {sorted(KNOWN_VERDICTS)}; "
            f"an unrecognised verdict is NOT read as 'not promotable'"
        )
        return out

    # A promotion is available AT ANY TIME — the budget bounds how long a leg may
    # sit undecided, never how soon it may succeed.
    if gate_verdict == PROMOTE:
        out["state"] = "return_to_live"
        out["proposes"] = PROPOSES["return_to_live"]
        out["detail"] = (
            f"the M7 gate grades {PROMOTE} after {n} of {budget} pass(es). Tier-3: "
            f"proposed, never enacted."
        )
        return out

    if n < budget:
        out["state"] = "within_budget"
        out["detail"] = (
            f"{n} of {budget} pass(es) spent; gate verdict "
            f"{gate_verdict or 'none yet'}. No exit is owed."
        )
        return out

    if gate_verdict == KEEP_SHADOW:
        out["state"] = "retire_graded"
        out["proposes"] = PROPOSES["retire_graded"]
        out["detail"] = (
            f"budget spent ({n} of {budget}) and the gate RAN, holding this leg below "
            f"the bar ({KEEP_SHADOW}). It was measured and it did not clear. Tier-3."
        )
        return out

    out["state"] = "retire_never_gradeable"
    out["proposes"] = PROPOSES["retire_never_gradeable"]
    out["detail"] = (
        f"budget spent ({n} of {budget}) and the gate NEVER graded this leg "
        f"(verdict {gate_verdict or 'none'}). ⚠️ THE REASON IS 'NOT GRADEABLE', NOT "
        f"'LOST MONEY' — do not render it as performance. Two months of shadow "
        f"produced no measurement, which is itself the finding. Tier-3."
    )
    return out


def _self_test() -> int:
    """Exercise every state, plus the two that are easiest to collapse."""
    T = date(2026, 11, 3)  # 8 weeks after 2026-09-08
    base = {
        "disposition": "demote",
        "demoted_at": "2026-09-08",
        "hypothesis": "the stop is too tight for this leg's ATR regime",
        "lever": "atr_stop_mult",
        "tuning_budget_passes": 8,
    }
    fails = []

    def check(label: str, got, want) -> None:
        if got != want:
            fails.append(f"{label}: got {got!r}, wanted {want!r}")

    check("budget exactly spent, never graded",
          resolve_exit(base, gate_verdict=None, today=T)["state"],
          "retire_never_gradeable")
    check("budget spent, gate ran and held it",
          resolve_exit(base, gate_verdict=KEEP_SHADOW, today=T)["state"],
          "retire_graded")
    check("one week short of the budget",
          resolve_exit(base, gate_verdict=None, today=date(2026, 10, 27))["state"],
          "within_budget")
    check("promotion is available before the budget is spent",
          resolve_exit(base, gate_verdict=PROMOTE, today=date(2026, 9, 22))["state"],
          "return_to_live")
    check("a non-demotion row is left alone",
          resolve_exit({"disposition": "keep"}, today=T)["state"], "not_demoted")
    for f in REQUIRED_FIELDS:
        r = dict(base)
        r.pop(f)
        check(f"missing {f}", resolve_exit(r, today=T)["state"], "malformed")
    check("undateable demoted_at",
          resolve_exit({**base, "demoted_at": "soon"}, today=T)["state"], "unreadable")
    check("zero budget is a DECLARATION error, not a read failure",
          resolve_exit({**base, "tuning_budget_passes": 0}, today=T)["state"], "malformed")
    check("a zero budget is PRESENT, not missing (truthiness trap)",
          "missing" in resolve_exit({**base, "tuning_budget_passes": 0}, today=T)["detail"],
          False)
    check("a bool is not an int budget",
          resolve_exit({**base, "tuning_budget_passes": True}, today=T)["state"], "unreadable")
    check("a string budget is unreadable",
          resolve_exit({**base, "tuning_budget_passes": "8"}, today=T)["state"], "unreadable")
    check("an unknown gate verdict is unreadable, NOT 'not promotable'",
          resolve_exit(base, gate_verdict="SOMETHING_NEW", today=T)["state"], "unreadable")
    check("a row that is not a mapping",
          resolve_exit(["nope"], today=T)["state"], "unreadable")

    # THE ONE THAT MATTERS MOST: the two retire states must stay apart, and only
    # one of them may be described as a performance failure.
    graded = resolve_exit(base, gate_verdict=KEEP_SHADOW, today=T)
    never = resolve_exit(base, gate_verdict=None, today=T)
    if graded["state"] == never["state"]:
        fails.append("the two retire states collapsed into one")
    if graded["proposes"] != never["proposes"]:
        fails.append("both retire states must PROPOSE the same Tier-3 move")
    if "NOT" not in never["detail"].upper() or "gradeable" not in never["detail"].lower():
        fails.append("retire_never_gradeable does not say the reason is not-gradeable")

    # A demotion can never simply persist: past the budget, no state is a no-op.
    for v in (None, KEEP_SHADOW, NOT_GRADED):
        st = resolve_exit(base, gate_verdict=v, today=T)
        if st["state"] == "within_budget" or st["proposes"] is None:
            fails.append(f"budget spent with verdict {v!r} left the row parked "
                         f"({st['state']}) — the forcing function does not force")

    if fails:
        for f in fails:
            print(f"demote-budget SELF-TEST FAILED: {f}", file=sys.stderr)
        return 1
    print(f"demote-budget: self-test OK ({len(STATES)} states exercised)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--register", default="docs/claude/SUNSET-DISPOSITIONS.json",
                    help="grade every `demote` row in this register and print the result")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    try:
        reg = json.loads(open(args.register, encoding="utf-8").read())
        rows = reg.get("dispositions") or []
    except Exception as exc:
        print(f"demote-budget: could not read {args.register}: {exc}", file=sys.stderr)
        return 1

    demotions = [r for r in rows if isinstance(r, dict) and r.get("disposition") == "demote"]
    print(f"demote-budget: {len(rows)} row(s) in the register, {len(demotions)} demotion(s)")
    for r in demotions:
        v = resolve_exit(r)
        arrow = f" -> {v['proposes']}" if v["proposes"] else ""
        print(f"  {r.get('id')}: {v['state']}{arrow}\n      {v['detail']}")
    if not demotions:
        print("  (none — this is the correct reading today, not an empty result to "
              "explain away: no leg has been demoted under this flow yet.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
