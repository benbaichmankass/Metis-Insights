#!/usr/bin/env python3
"""strategy-decision-record guard — C4 of the operating-layer build (Phase F).

WHAT THIS ANSWERS
-----------------
Does the DECISION RECORD (``config/strategy_changelog.json``) still agree with
the GATE it is a record of (``config/strategies.yaml``)?

An execution flip is a Tier-3 decision. It is written in two places — the gate
that *acts* and the record that *explains why* — and nothing has ever compared
them. So the record forked from the gate and nobody noticed for six weeks.

THE LIVE INSTANCE, AND THE READING IT INVITES THAT IS WRONG
-----------------------------------------------------------
``BL-20260901-DECISION-RECORD-SAYS-SHADOW-WHILE-CONFIG-SAYS-LIVE-SQUEEZE-BREAKOUT-4H``
is filed as *"squeeze_breakout_4h runs LIVE today against a written record
saying it was demoted for a 0% win rate over 60 closes"*. That sentence is TRUE
and it reads as an ungoverned live leg. It is not one.

MEASURED, ``config/strategies.yaml`` lines 305-340 read 2026-09-08 at
``a0ec22ce``: the leg is ``execution: live`` and the YAML **documents its own
authorization** — "RE-PROMOTED shadow -> live 2026-06-23 (operator pre-approved
2026-06-01, gated on debounce verify now satisfied — PERF-20260601-005)",
routed to ``bybit_1`` (demo/paper) ONLY, explicitly removed from real-money
``bybit_2`` at the demotion. The changelog's newest ``squeeze_breakout_4h``
entry is the ``2026-06-01`` demotion and stops there.

**So the divergence is a MISSING RECORD, not an unapproved live leg, and there
is no real-money exposure in it.** Per *field beats comment* the gate is the
truth and the RECORD is what gets fixed. This guard therefore names
``strategies.yaml`` as authoritative in every divergence message and routes the
remedy to the record — because a guard that merely says "these disagree" points
the next session at the gate, and "reconciling" it would mean flipping a live
execution gate on inference, which that backlog row forbids in BOTH directions.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
**It does not read the 53 legacy entries' English.** MEASURED against
``config/strategy_changelog.json`` on 2026-09-08: 53 entries across 28
strategies, spanning 2026-05-06 -> 2026-07-28, and **all 53 carry exactly
``{date, ref, summary}``** — the verdict exists only as prose
("DEMOTED execution: live -> shadow ..."). Pattern-matching that is sub-class
**A** of the diagnostic-provenance defect (*the label names a quantity the
accessor does not return*), and a guard confidently wrong on the entries it
misses is worse than none.

The 53 are **counted and reported as ungradeable**. They are never guessed at
and never back-filled: back-filling would assert an observation nobody made.

THE FIVE GRADES, NEVER COLLAPSED
--------------------------------
``agrees``        the latest structured verdict matches the gate.
``diverges``      the latest structured verdict contradicts the gate — THE FINDING.
``unrecorded``    the strategy HAS changelog entries and none carries a
                  structured verdict. **We did not look.** This is NOT
                  ``agrees``; folding it there is the whole defect, because it
                  would report a fleet as reconciled that nobody has compared.
``no_record``     the strategy has no changelog entry at all. Distinct from
                  ``unrecorded``: there is a record and it is silent on this leg
                  vs there is no record of this leg at all — different remedies
                  (type the next entry vs write a first one).
``not_in_config`` the changelog names a leg ``strategies.yaml`` does not have
                  (retired or renamed). Reported, never failed — a record of a
                  retired leg is correct history, not drift.

WHY IT PASSES WHILE COVERAGE IS ZERO, ON PURPOSE
------------------------------------------------
Today **zero** entries carry a structured verdict, so this guard grades 0 of 55
and exits 0. That is the accurate reading, not a bug, and failing instead would
red every PR in the repo on day one — which is how a guard gets disabled rather
than fixed (the ``check_pr_queue_watch.py`` precedent, which grades
``never_ran`` and passes deliberately).

**It ARMS ITSELF.** The moment one entry carries ``execution_verdict``, that
strategy becomes gradeable and a divergence on it FAILS. There is no flag to
unset and no list to remember to update.

EXIT CODES
----------
``0`` no divergence (coverage still reported)
``1`` findings — a divergence, or a malformed ``execution_verdict`` value
``2`` COULD NOT CHECK — an input is missing or unreadable. Deliberately
      distinct from ``1``: *"we could not look"* is neither a pass nor a
      finding, and reporting a failure-to-check as defects is the sin
      ``check_workflow_shell.py`` exits 2 to avoid.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHANGELOG = _REPO_ROOT / "config" / "strategy_changelog.json"
_STRATEGIES = _REPO_ROOT / "config" / "strategies.yaml"

# The field a NEW changelog entry carries. One typed field, deliberately — the
# barrier to actually using it is the thing that decides whether coverage ever
# rises above zero, and every extra mandatory key raises that barrier.
VERDICT_FIELD = "execution_verdict"

# The closed vocabulary. `no_execution_change` is load-bearing and is NOT the
# same as omitting the field: it says "we looked, and this decision did not
# touch the execution gate" (a param sweep, a geometry change), where omission
# says "nobody recorded a verdict". Collapsing them would make every param
# change silently assert the gate is unchanged.
VERDICT_LIVE = "live"
VERDICT_SHADOW = "shadow"
VERDICT_DISABLED = "disabled"
VERDICT_NO_CHANGE = "no_execution_change"
VALID_VERDICTS = (VERDICT_LIVE, VERDICT_SHADOW, VERDICT_DISABLED, VERDICT_NO_CHANGE)

# The verdicts that actually assert a gate state. `no_execution_change` is
# valid and gradeable-as-a-field but says nothing about the gate, so it can
# never produce agreement or divergence.
GATE_VERDICTS = (VERDICT_LIVE, VERDICT_SHADOW, VERDICT_DISABLED)

GRADE_AGREES = "agrees"
GRADE_DIVERGES = "diverges"
GRADE_UNRECORDED = "unrecorded"
GRADE_NO_RECORD = "no_record"
GRADE_NOT_IN_CONFIG = "not_in_config"
ALL_GRADES = (
    GRADE_AGREES,
    GRADE_DIVERGES,
    GRADE_UNRECORDED,
    GRADE_NO_RECORD,
    GRADE_NOT_IN_CONFIG,
)


class CouldNotCheck(Exception):
    """An input could not be read. Exits 2, never 1 — see module docstring."""


# ---------------------------------------------------------------------------
# The gate side
# ---------------------------------------------------------------------------

def _norm_execution(value: Any) -> str:
    """Normalise ``execution`` — delegated to the CANONICAL owner.

    ``src/strategy_registry.py::_norm_execution`` is the one definition of what
    an ``execution`` value means (unknown/missing -> ``live``, the permissive
    default the Prime Directive requires). A second copy here is exactly how
    the two would drift, which is the reasoning
    ``silent_refusal_alert``/``execution_diagnostics`` already applies to
    "what counts as a declared skip".

    If the import fails we REFUSE rather than re-deriving the default, because
    a locally-invented default that happened to disagree would silently
    manufacture divergences on a live gate.
    """
    try:
        # sys.path[0] is this script's directory, not the repo root, so the
        # repo-root package is not importable without this. (`python3 -c`
        # happens to work from the repo root and a script does not — a
        # difference that makes "it imported when I tried it by hand"
        # misleading.)
        if str(_REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(_REPO_ROOT))
        from src.strategy_registry import _norm_execution as canonical
    except Exception as exc:  # pragma: no cover - exercised by --self-test path
        raise CouldNotCheck(
            f"cannot import the canonical execution normaliser "
            f"(src.strategy_registry._norm_execution): {exc}. Refusing to "
            f"re-derive the default locally — a second copy would drift."
        ) from exc
    return canonical(value)


def gate_verdict(cfg: Dict[str, Any]) -> str:
    """The gate's own current verdict for one strategy block.

    ``enabled: false`` wins over ``execution`` because a disabled leg does not
    run at all — reporting it as ``live`` because ``execution: live`` is still
    written beside it would describe a gate that is not acting.
    """
    if cfg.get("enabled") is False:
        return VERDICT_DISABLED
    return _norm_execution(cfg.get("execution"))


def load_strategies(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    p = path or _STRATEGIES
    if not p.exists():
        raise CouldNotCheck(f"{p} does not exist")
    try:
        import yaml
    except Exception as exc:
        raise CouldNotCheck(f"PyYAML unavailable: {exc}") from exc
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise CouldNotCheck(f"{p} is unreadable: {exc}") from exc
    strategies = raw.get("strategies") if isinstance(raw, dict) else None
    if not isinstance(strategies, dict):
        raise CouldNotCheck(f"{p} has no `strategies:` mapping")
    return {k: v for k, v in strategies.items() if isinstance(v, dict)}


# ---------------------------------------------------------------------------
# The record side
# ---------------------------------------------------------------------------

def load_changelog(path: Optional[Path] = None) -> Dict[str, List[Dict[str, Any]]]:
    p = path or _CHANGELOG
    if not p.exists():
        raise CouldNotCheck(f"{p} does not exist")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CouldNotCheck(f"{p} is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise CouldNotCheck(f"{p} is not a JSON object keyed by strategy")
    return {k: v for k, v in data.items() if isinstance(v, list)}


def malformed_verdicts(
    changelog: Dict[str, List[Dict[str, Any]]]
) -> List[Tuple[str, str, Any]]:
    """Entries whose ``execution_verdict`` is present but not in the vocabulary.

    This IS a real finding and fails: an unrecognised value is a typo in a
    Tier-3 record, and silently ignoring it would be the presence-only override
    the `new-table-wiring-guard` lesson forbids — the cheapest way to satisfy
    the guard must not be to write something it cannot read.
    """
    out: List[Tuple[str, str, Any]] = []
    for name, entries in sorted(changelog.items()):
        for entry in entries:
            if not isinstance(entry, dict) or VERDICT_FIELD not in entry:
                continue
            value = entry.get(VERDICT_FIELD)
            if value not in VALID_VERDICTS:
                out.append((name, str(entry.get("date", "?")), value))
    return out


def latest_recorded_verdict(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The newest entry asserting a GATE state, by ``date``.

    Entries are sorted rather than assumed ordered: the file is hand-edited and
    "the first element is newest" is a convention, not an invariant. An entry
    with an unparseable/absent date sorts last so it can never silently become
    "the latest" and mask a real one.
    """
    dated: List[Tuple[str, Dict[str, Any]]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get(VERDICT_FIELD) not in GATE_VERDICTS:
            continue
        date = entry.get("date")
        dated.append((date if isinstance(date, str) else "", entry))
    if not dated:
        return None
    dated.sort(key=lambda pair: pair[0])
    return dated[-1][1]


# ---------------------------------------------------------------------------
# Grading — a pure function, so the policy is arguable in tests rather than
# against a live gate (the `protection_reassert.py` discipline).
# ---------------------------------------------------------------------------

def grade(
    strategies: Dict[str, Dict[str, Any]],
    changelog: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for name in sorted(strategies):
        entries = changelog.get(name)
        if not entries:
            rows.append({"strategy": name, "grade": GRADE_NO_RECORD,
                         "gate": gate_verdict(strategies[name]), "recorded": None,
                         "recorded_date": None, "entries": 0})
            continue
        newest = latest_recorded_verdict(entries)
        if newest is None:
            rows.append({"strategy": name, "grade": GRADE_UNRECORDED,
                         "gate": gate_verdict(strategies[name]), "recorded": None,
                         "recorded_date": None, "entries": len(entries)})
            continue
        gate = gate_verdict(strategies[name])
        recorded = newest.get(VERDICT_FIELD)
        rows.append({
            "strategy": name,
            "grade": GRADE_AGREES if recorded == gate else GRADE_DIVERGES,
            "gate": gate,
            "recorded": recorded,
            "recorded_date": newest.get("date"),
            "recorded_ref": newest.get("ref"),
            "entries": len(entries),
        })

    for name in sorted(set(changelog) - set(strategies)):
        rows.append({"strategy": name, "grade": GRADE_NOT_IN_CONFIG, "gate": None,
                     "recorded": None, "recorded_date": None,
                     "entries": len(changelog[name])})

    return rows


def coverage(rows: List[Dict[str, Any]],
             changelog: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """The reported denominator. A grade without one is not a claim."""
    counts = {g: 0 for g in ALL_GRADES}
    for row in rows:
        counts[row["grade"]] += 1
    in_config = sum(counts[g] for g in ALL_GRADES if g != GRADE_NOT_IN_CONFIG)
    gradeable = counts[GRADE_AGREES] + counts[GRADE_DIVERGES]
    total_entries = sum(len(v) for v in changelog.values())
    typed_entries = sum(
        1
        for entries in changelog.values()
        for e in entries
        if isinstance(e, dict) and e.get(VERDICT_FIELD) in VALID_VERDICTS
    )
    return {
        "counts": counts,
        "config_strategies": in_config,
        "gradeable": gradeable,
        "gradeable_pct": (100.0 * gradeable / in_config) if in_config else 0.0,
        "total_entries": total_entries,
        "typed_entries": typed_entries,
        "untyped_entries": total_entries - typed_entries,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def render(rows: List[Dict[str, Any]], cov: Dict[str, Any], verbose: bool) -> str:
    counts = cov["counts"]
    lines: List[str] = []
    lines.append(
        "strategy-decision-record: graded {gradeable} of {total} config "
        "strategies ({pct:.1f}%) — the rest are UNGRADEABLE, not agreed".format(
            gradeable=cov["gradeable"], total=cov["config_strategies"],
            pct=cov["gradeable_pct"],
        )
    )
    lines.append(
        "  agrees {a} · diverges {d} · unrecorded {u} · no_record {n} · "
        "not_in_config {x}".format(
            a=counts[GRADE_AGREES], d=counts[GRADE_DIVERGES],
            u=counts[GRADE_UNRECORDED], n=counts[GRADE_NO_RECORD],
            x=counts[GRADE_NOT_IN_CONFIG],
        )
    )
    lines.append(
        "  entries: {typed} of {total} carry `{field}`; {untyped} predate the "
        "field and are ungradeable BY CONSTRUCTION (never guessed, never "
        "back-filled)".format(
            typed=cov["typed_entries"], total=cov["total_entries"],
            untyped=cov["untyped_entries"], field=VERDICT_FIELD,
        )
    )

    diverging = [r for r in rows if r["grade"] == GRADE_DIVERGES]
    for row in diverging:
        lines.append("")
        lines.append(
            "DIVERGENCE · {s}: the record's newest verdict says `{rec}` "
            "(dated {date}) but config/strategies.yaml acts `{gate}`.".format(
                s=row["strategy"], rec=row["recorded"],
                date=row["recorded_date"], gate=row["gate"],
            )
        )
        lines.append(
            "  config/strategies.yaml IS AUTHORITATIVE (field beats record). "
            "The remedy is to bring the RECORD up to date — add an entry "
            "carrying `{field}: {gate}` naming the decision that set it."
            .format(field=VERDICT_FIELD, gate=row["gate"])
        )
        # ⚠️ The backlog id is kept on ONE line deliberately. Wrapping it across
        # a string concatenation truncates it for `check_backlog_refs.py`, which
        # then reads as a reference to a row that was never filed — "tracked by
        # nobody while reading as tracked". That guard caught exactly this here,
        # which is the truncated-id failure CLAUDE.md's RULE ONE ledger names.
        lines.append(
            "  ⚠️ DO NOT flip the execution gate to match the record. Both "
            "directions are Tier-3 and need explicit operator approval; "
            "flipping on inference is what this row forbids: "
            "BL-20260901-DECISION-RECORD-SAYS-SHADOW-WHILE-CONFIG-SAYS-LIVE-SQUEEZE-BREAKOUT-4H"
        )

    if verbose:
        lines.append("")
        for row in rows:
            lines.append(
                "  {g:<14} {s:<32} gate={gate!s:<8} recorded={rec!s:<8} "
                "entries={n}".format(
                    g=row["grade"], s=row["strategy"], gate=row["gate"],
                    rec=row["recorded"], n=row["entries"],
                )
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Self-test — exercises every grade AND the failure path, so a guard that
# stopped matching cannot read as a clean pass.
# ---------------------------------------------------------------------------

def _self_test() -> int:
    failures: List[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    strategies = {
        "leg_agrees": {"enabled": True, "execution": "live"},
        "leg_diverges": {"enabled": True, "execution": "live"},
        "leg_unrecorded": {"enabled": True, "execution": "shadow"},
        "leg_no_record": {"enabled": True, "execution": "live"},
        "leg_disabled": {"enabled": False, "execution": "live"},
    }
    changelog = {
        "leg_agrees": [{"date": "2026-01-01", "ref": "r", "summary": "s",
                        VERDICT_FIELD: VERDICT_LIVE}],
        "leg_diverges": [{"date": "2026-01-01", "ref": "r", "summary": "s",
                          VERDICT_FIELD: VERDICT_SHADOW}],
        "leg_unrecorded": [{"date": "2026-01-01", "ref": "r", "summary": "s"}],
        "leg_disabled": [{"date": "2026-01-01", "ref": "r", "summary": "s",
                          VERDICT_FIELD: VERDICT_DISABLED}],
        "retired_leg": [{"date": "2026-01-01", "ref": "r", "summary": "s"}],
    }
    rows = {r["strategy"]: r for r in grade(strategies, changelog)}

    check("agrees", rows["leg_agrees"]["grade"], GRADE_AGREES)
    check("diverges", rows["leg_diverges"]["grade"], GRADE_DIVERGES)
    check("unrecorded", rows["leg_unrecorded"]["grade"], GRADE_UNRECORDED)
    check("no_record", rows["leg_no_record"]["grade"], GRADE_NO_RECORD)
    check("not_in_config", rows["retired_leg"]["grade"], GRADE_NOT_IN_CONFIG)
    # enabled:false wins over execution:live — a disabled leg does not run.
    check("disabled_gate", rows["leg_disabled"]["grade"], GRADE_AGREES)
    check("disabled_verdict", rows["leg_disabled"]["gate"], VERDICT_DISABLED)

    # `no_execution_change` never asserts a gate state, so a leg whose only
    # typed entry is a param change stays UNRECORDED — it must not read as
    # agreement with whatever the gate happens to say.
    rows2 = {r["strategy"]: r for r in grade(
        {"leg": {"enabled": True, "execution": "live"}},
        {"leg": [{"date": "2026-01-01", VERDICT_FIELD: VERDICT_NO_CHANGE}]},
    )}
    check("no_execution_change_is_not_agreement",
          rows2["leg"]["grade"], GRADE_UNRECORDED)

    # The newest GATE verdict wins even when a later param-only entry exists.
    rows3 = {r["strategy"]: r for r in grade(
        {"leg": {"enabled": True, "execution": "shadow"}},
        {"leg": [
            {"date": "2026-01-01", VERDICT_FIELD: VERDICT_LIVE},
            {"date": "2026-03-01", VERDICT_FIELD: VERDICT_SHADOW},
            {"date": "2026-04-01", VERDICT_FIELD: VERDICT_NO_CHANGE},
        ]},
    )}
    check("newest_gate_verdict_wins", rows3["leg"]["grade"], GRADE_AGREES)
    check("newest_gate_verdict_date", rows3["leg"]["recorded_date"], "2026-03-01")

    # Order in the file is a convention, not an invariant.
    rows4 = {r["strategy"]: r for r in grade(
        {"leg": {"enabled": True, "execution": "live"}},
        {"leg": [
            {"date": "2026-05-01", VERDICT_FIELD: VERDICT_LIVE},
            {"date": "2026-02-01", VERDICT_FIELD: VERDICT_SHADOW},
        ]},
    )}
    check("unsorted_file_still_grades", rows4["leg"]["grade"], GRADE_AGREES)

    # The failure path is exercised, not just the clean one.
    bad = malformed_verdicts({"leg": [{"date": "2026-01-01",
                                       VERDICT_FIELD: "SHADOW"}]})
    check("malformed_detected", len(bad), 1)
    check("valid_not_flagged",
          len(malformed_verdicts({"leg": [{"date": "d",
                                           VERDICT_FIELD: VERDICT_LIVE}]})), 0)
    check("absent_field_not_flagged",
          len(malformed_verdicts({"leg": [{"date": "d", "summary": "s"}]})), 0)

    cov = coverage(grade(strategies, changelog), changelog)
    check("coverage_gradeable", cov["gradeable"], 3)
    check("coverage_config_strategies", cov["config_strategies"], 5)
    check("coverage_typed_entries", cov["typed_entries"], 3)
    check("coverage_untyped_entries", cov["untyped_entries"], 2)

    if failures:
        print("strategy-decision-record --self-test: FAIL")
        for f in failures:
            print(f"  {f}")
        return 1
    print("strategy-decision-record --self-test: OK (14 assertions)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--verbose", action="store_true",
                        help="print a line per strategy, not just divergences")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    try:
        strategies = load_strategies()
        changelog = load_changelog()
        rows = grade(strategies, changelog)
    except CouldNotCheck as exc:
        print(f"strategy-decision-record: COULD NOT CHECK — {exc}")
        print("  Nothing was compared. This is neither a pass nor a finding.")
        return 2

    cov = coverage(rows, changelog)
    print(render(rows, cov, args.verbose))

    bad = malformed_verdicts(changelog)
    for name, date, value in bad:
        print(f"MALFORMED · {name} ({date}): `{VERDICT_FIELD}: {value!r}` is not "
              f"one of {list(VALID_VERDICTS)}")

    diverging = sum(1 for r in rows if r["grade"] == GRADE_DIVERGES)
    if diverging or bad:
        print(f"\nstrategy-decision-record: FAIL "
              f"({diverging} divergence(s), {len(bad)} malformed)")
        return 1
    print("\nstrategy-decision-record: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
