#!/usr/bin/env python3
"""ONE result record, in ONE schema, in ONE committed location — R1 made executable.

WHY THIS EXISTS
---------------
MEASURED 2026-09-22 by reading `.github/workflows/`: of the research-producing
workflows, the overwhelming majority end their run with
`actions/upload-artifact` plus a `gh issue comment`. **An artifact EXPIRES and
an issue comment is NOT queryable**, so a result that cost real runner minutes
exists only until it doesn't, and no later session can `grep` for it.

`docs/research/RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md` § R1 already asked
for this and called it *"the foundation"*. R2 (`scripts/ci/assert_rows_landed.py`)
shipped — landing is part of the run — but R2 can only assert that *some* rows
reached `main`. It cannot assert that what landed is a RESULT: that it names the
question it answers, the rule it was graded against, what it was measured over,
and whether anyone actually looked. That is this module.

⚠️ THIS IS NOT A SECOND CORPUS AND MUST NOT BECOME ONE.
The per-leg measurement rows stay where they are — `docs/research/*-corpus.jsonl`
is the canonical store for what a sweep MEASURED, and this record **points at
them** (`artifact.store` + `artifact.locator`) rather than copying them.
Duplicating a measurement into a second store is how the two drift; the record
here is the *verdict about* a run, which is a different fact with a different
lifetime — the same reasoning `research_disposition.py` gives for keeping the
disposition ledger separate from the corpora it reads.

THE LAYOUT, AND WHY IT IS ONE FILE PER (unit, run)
--------------------------------------------------
    research/results/<research_unit>/<run_id>.jsonl
    research/results/_unattributed/<run_id>.jsonl   # not queue-dispatched

* **Greppable by the thing a reader actually holds.** A session picks up
  `research/queue/RQ-20260922-007.yaml` and finds its answers one directory
  over, by name. `comms/strategy_evidence/` is the precedent that works for
  exactly this reason: the key is in the PATH, so finding a record needs no
  index and no query engine.
* **No shared file, so no merge conflict.** Two runs landing concurrently touch
  two different paths. An append-only shared JSONL would collide on its last
  line every time — the failure that produced the archived registers, and the
  reason `docs/claude/work/PIPELINE.jsonl` is append-only-never-an-array.
* **JSONL, not JSON, although a file usually holds one line.** That is what lets
  `scripts/ci/assert_rows_landed.py` — the EXISTING owner of "did my rows reach
  `main`" — read it verbatim, with `--min-rows` carrying real meaning for a
  per-leg fan-out (RQ-20260922-002 wants 8, RQ-20260922-006 wants 12: a result
  covering fewer is a defect, not a partial success). A single `json.dump`
  object would have needed a second, parallel landing assertion.

THE NON-COLLAPSE RULE, WHICH IS THE POINT OF THE SCHEMA
--------------------------------------------------------
`docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states": *"we did not look"* and
*"we looked and found nothing"* must be distinguishable. So `verdict` and
`read_state` are two fields and are never folded:

    read_state = measured        the producer ran and the numbers are real
    read_state = no_data         it ran, and the population was EMPTY — we looked
    read_state = producer_failed it ran and broke — we looked, and it broke
    read_state = not_attempted   it did not run — WE DID NOT LOOK

and the invariants `validate()` enforces:

  * `read_state: measured`  -> `verdict` is a real verdict AND `population.n`
    is an integer. n may be 0: "we looked and found nothing" is a measurement.
  * anything else           -> `verdict` MUST be `not_applicable` AND
    `population.n` MUST be `null`, never 0. A fabricated zero asserts a measured
    empty sample and drags any aggregate to a value never observed.

WHAT ELSE A ROW MUST CARRY, AND WHO ASKED FOR IT
------------------------------------------------
  research_unit + decision_rule.id   so the result can be matched to the rule
                                     REGISTERED BEFORE THE RUN. That pairing is
                                     the entire safety property behind the
                                     automated ladder (root `CLAUDE.md` § "The
                                     daily sync, and standing authorizations":
                                     *"a committed evidence record, named
                                     harness, stated n, net of the full cost
                                     stack, clearing a rule registered before
                                     the run"*).
  population.description + .n        every quantitative claim in this repo names
                                     what it was measured over
                                     (§ "Always state the population").
  produced_by.commit_sha + .run_id   so it is reproducible, and so the record
                                     says WHERE THE MEASUREMENT LIVES.
  power_state                        the dispatcher-COMPUTED safety label.
                                     `accruing` means the unit declared up front
                                     that it cannot answer yet, so these numbers
                                     must never be read as a test result.

⚠️ `research_unit: null` MEANS NOT-QUEUE-DISPATCHED. It does NOT mean "this was
a test", and it is not a licence to skip the other fields. A manually-fired run
still measured something over some population.

Tier-1: writes one JSONL file under `research/results/`. No live path, no order
path, no VM, no network.

Run:
    python3 scripts/research/research_result.py --self-test
    python3 scripts/research/research_result.py --emit --help
    python3 scripts/research/research_result.py --validate research/results/<unit>/<run>.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
RESULTS_ROOT = REPO / "research" / "results"

SCHEMA_VERSION = 1

#: The directory a run with no queue unit lands under. A real, named state —
#: never the empty string, which would put results at the store root and make
#: "not dispatched" indistinguishable from a path bug.
UNATTRIBUTED = "_unattributed"

#: Closed set. `not_applicable` is NOT a verdict about the world; it is the
#: only legal verdict when `read_state` says we could not look, and
#: `validate()` refuses it in any other combination.
VERDICTS = ("pass", "fail", "no_action_warranted", "indeterminate", "not_applicable")

#: Closed set, ordered "we looked" first. See the module docstring.
READ_STATES = ("measured", "no_data", "producer_failed", "not_attempted")

#: The three read-states that mean the numbers are not a measurement of the
#: world. Named once so the rule below and the guard cannot drift apart.
NON_MEASURED = tuple(s for s in READ_STATES if s != "measured")

_UNIT_RE = re.compile(r"^RQ-\d{8}-\d{3}$")
_RUN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def unit_dir(research_unit: Optional[str]) -> str:
    """Directory segment for a unit id, or UNATTRIBUTED when there is none."""
    return research_unit if research_unit else UNATTRIBUTED


def result_path(research_unit: Optional[str], run_id: str,
                root: Optional[Path] = None) -> Path:
    base = Path(root) if root is not None else RESULTS_ROOT
    return base / unit_dir(research_unit) / f"{run_id}.jsonl"


def validate(record: Dict[str, Any]) -> List[str]:
    """Return every problem with ONE record. Empty list == admissible.

    A PURE FUNCTION over the record, deliberately: the policy is then arguable
    in tests rather than against a live landing step
    (`src/runtime/protection_reassert.py` is the local precedent).
    """
    problems: List[str] = []

    def req(key: str) -> Any:
        if key not in record:
            problems.append(f"missing required field `{key}`")
            return None
        return record[key]

    if record.get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"schema_version is {record.get('schema_version')!r}, expected "
            f"{SCHEMA_VERSION}. A record written by an older producer is not "
            f"silently upgraded — regenerate it rather than reading old fields "
            f"as current.")

    unit = record.get("research_unit", ...)
    if unit is ...:
        problems.append(
            "missing required field `research_unit` — it must be present and "
            "explicitly null for a run that was not queue-dispatched. Absent "
            "and null are different statements and only one of them is legal.")
    elif unit is not None and not _UNIT_RE.match(str(unit)):
        problems.append(f"research_unit {unit!r} is not an RQ-YYYYMMDD-NNN id")

    rule = record.get("decision_rule")
    if not isinstance(rule, dict):
        problems.append("`decision_rule` must be an object")
    else:
        if not rule.get("id"):
            problems.append(
                "`decision_rule.id` is empty — a result that cannot name the "
                "rule it was graded against cannot be matched to a rule "
                "registered BEFORE the run, which is the whole safety property")
        if not rule.get("registered_at"):
            problems.append("`decision_rule.registered_at` is empty")

    produced = record.get("produced_by")
    if not isinstance(produced, dict):
        problems.append("`produced_by` must be an object")
    else:
        for key in ("workflow", "run_id", "commit_sha", "tool"):
            if not produced.get(key):
                problems.append(
                    f"`produced_by.{key}` is empty — without it the result is "
                    f"not reproducible and does not say where it came from")
        run_id = str(produced.get("run_id") or "")
        if run_id and not _RUN_RE.match(run_id):
            problems.append(f"produced_by.run_id {run_id!r} is not path-safe")

    pop = record.get("population")
    if not isinstance(pop, dict):
        problems.append("`population` must be an object")
        pop = {}
    elif not str(pop.get("description") or "").strip():
        problems.append(
            "`population.description` is empty — § 'Always state the "
            "population': a number without its basis is not a finding")

    verdict = req("verdict")
    if verdict is not None and verdict not in VERDICTS:
        problems.append(f"verdict {verdict!r} not in {list(VERDICTS)}")

    read_state = req("read_state")
    if read_state is not None and read_state not in READ_STATES:
        problems.append(f"read_state {read_state!r} not in {list(READ_STATES)}")

    # ---- the non-collapse invariant -------------------------------------
    n = pop.get("n", ...)
    if n is ...:
        problems.append(
            "`population.n` must be PRESENT — explicitly null when we could "
            "not look. Absent collapses 'no sample' into 'field forgotten'.")
        n = None
    if read_state == "measured":
        if verdict == "not_applicable":
            problems.append(
                "read_state=measured with verdict=not_applicable: the producer "
                "ran and measured something, so there IS a verdict to state. "
                "`not_applicable` is reserved for the states where we could "
                "not look.")
        if not isinstance(n, int) or isinstance(n, bool):
            problems.append(
                f"read_state=measured requires an integer `population.n`, got "
                f"{n!r}. n=0 is legal and means we LOOKED AND FOUND NOTHING; "
                f"null means we could not look, and they are opposite facts.")
    elif read_state in NON_MEASURED:
        if verdict != "not_applicable":
            problems.append(
                f"read_state={read_state!r} with verdict={verdict!r}: a run "
                f"that did not produce a measurement cannot carry a verdict "
                f"about the world. Use verdict=not_applicable.")
        if n is not None:
            problems.append(
                f"read_state={read_state!r} requires `population.n: null`, got "
                f"{n!r}. Never fabricate the reassuring value — a 0 here "
                f"asserts a measured empty sample and drags any aggregate to a "
                f"value nobody observed.")

    art = record.get("artifact")
    if not isinstance(art, dict):
        problems.append("`artifact` must be an object (store/locator/rows_landed)")
    elif read_state == "measured" and not str(art.get("store") or "").strip():
        problems.append(
            "`artifact.store` is empty on a measured result — § 'A MEASURED "
            "must say WHERE THE MEASUREMENT LIVES'. If the measurement is "
            "genuinely unreachable, say THAT in artifact.locator rather than "
            "leaving the store blank.")

    if not str(record.get("generated_at") or "").strip():
        problems.append("`generated_at` is empty")

    return problems


# collapsed-state: not_applicable — THIS FILE BRANCHES ON NO POWER STATE AT
# ALL, and the match is a token collision rather than a finding. The contract
# `research_queue.power_state` keys its consumers on `\bpower_state\b`, which
# this module matches because it CARRIES that field through opaquely — a kwarg,
# a dict key, a CLI flag, a fixture value — from the dispatcher that computed it
# to the committed record, deliberately never reading it. The `not_applicable`
# the guard then sees is NOT a power state at all: it is a member of this
# module's own `VERDICTS` tuple, an unrelated closed set that happens to share
# the word.
#
# So the true statement is NOT "this site legitimately sees only one state" —
# it is that none of the seven is seen here, because a pass-through must not
# interpret the label it preserves. `power_state` is the GATE's computed safety
# verdict; a second opinion on it, formed in a module with no basis for one, is
# exactly the drift the dispatcher's own comment refuses when it declines to let
# a unit hand-declare its power state.
def build(*, research_unit: Optional[str], decision_rule_id: str,
          decision_rule_registered_at: str, verdict: str, read_state: str,
          population_description: str, n: Optional[int], workflow: str,
          run_id: str, run_attempt: str = "", run_url: str = "",
          commit_sha: str = "", tool: str = "",
          power_state: str = "", measurement: Optional[Dict[str, Any]] = None,
          artifact_store: str = "", artifact_locator: str = "",
          rows_landed: Optional[int] = None,
          note: str = "") -> Dict[str, Any]:
    """Assemble a record. Does NOT validate — the caller does, and refuses."""
    return {
        "schema_version": SCHEMA_VERSION,
        "research_unit": research_unit or None,
        "power_state": power_state or None,
        "decision_rule": {
            "id": decision_rule_id,
            "registered_at": decision_rule_registered_at,
        },
        "produced_by": {
            "workflow": workflow,
            "run_id": run_id,
            "run_attempt": run_attempt or None,
            "run_url": run_url or None,
            "commit_sha": commit_sha,
            "tool": tool,
        },
        # ⚠️ `n` OMITTED (the `...` sentinel) LEAVES `population.n` ABSENT so
        # `validate()` can say "you forgot it". Defaulting a missing n to None
        # here would launder a forgotten field into a deliberate "we could not
        # look" — two different statements, and only one of them is honest.
        "population": ({"description": population_description}
                       if n is Ellipsis
                       else {"description": population_description, "n": n}),
        "verdict": verdict,
        "read_state": read_state,
        "measurement": measurement if measurement is not None else {},
        "artifact": {
            "store": artifact_store,
            "locator": artifact_locator,
            "rows_landed": rows_landed,
        },
        "note": note,
        "generated_at": _utc_now(),
    }


def write(records: List[Dict[str, Any]], *, research_unit: Optional[str],
          run_id: str, root: Optional[Path] = None) -> Path:
    """Validate every record, then write them as one JSONL file. Refuses on any
    problem — a landing step that writes an inadmissible record has rebuilt the
    unreadable-result problem one directory down."""
    if not records:
        raise ValueError(
            "refusing to write an EMPTY result file. A run with nothing to say "
            "still has a read_state — emit a record saying so.")
    problems: List[str] = []
    for i, rec in enumerate(records, 1):
        for p in validate(rec):
            problems.append(f"record {i}: {p}")
    if problems:
        raise ValueError("inadmissible result record(s):\n  - " + "\n  - ".join(problems))
    path = result_path(research_unit, run_id, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
    return path


# --------------------------------------------------------------------------- CLI
def _coerce_n(raw: Any) -> Any:
    """`""`/`None`/`"null"` -> None; anything else -> int, or the raw value so
    `validate()` reports it rather than this function raising."""
    if raw is None or (isinstance(raw, str) and raw.strip().lower() in ("", "null")):
        return None
    if isinstance(raw, bool):
        return raw          # deliberately passed through: validate() refuses it
    if isinstance(raw, int):
        return raw
    try:
        return int(str(raw))
    except ValueError:
        return raw


def _emit_many(args: argparse.Namespace) -> int:
    """Several records in ONE file, for a producer with a per-leg fan-out.

    ⚠️ THIS IS WHAT MAKES `--min-rows` MEAN ANYTHING. A sweep over twelve legs
    that lands one summary row cannot be asserted against a fan-out width, so a
    run that silently dropped four legs looks identical to one that covered all
    twelve. RQ-20260922-002 (8 legs) and RQ-20260922-006 (12) both say so in
    terms: read in one process from one checkout, fewer is a DEFECT, not a
    partial success.

    The per-record file carries only what VARIES per leg; the unit, the decision
    rule and the provenance are shared and come from the flags, so they cannot
    disagree between rows of one run.
    """
    raw = Path(args.records_file).read_text(encoding="utf-8")
    records: List[Dict[str, Any]] = []
    for i, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            part = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"::error::--records-file line {i} is not JSON — {exc}",
                  file=sys.stderr)
            return 1
        if not isinstance(part, dict):
            print(f"::error::--records-file line {i} is not a JSON object",
                  file=sys.stderr)
            return 1
        records.append(build(
            research_unit=args.research_unit or None,
            decision_rule_id=args.decision_rule_id,
            decision_rule_registered_at=args.decision_rule_registered_at,
            verdict=str(part.get("verdict", "")),
            read_state=str(part.get("read_state", "")),
            population_description=str(part.get("population", "")),
            n=_coerce_n(part.get("n", ...)) if "n" in part else ...,
            workflow=args.workflow, run_id=args.run_id,
            run_attempt=args.run_attempt, run_url=args.run_url,
            commit_sha=args.commit_sha, tool=args.tool,
            power_state=args.power_state,
            measurement=part.get("measurement") or {},
            artifact_store=str(part.get("artifact_store") or args.artifact_store),
            artifact_locator=str(part.get("artifact_locator") or args.artifact_locator),
            rows_landed=_coerce_n(part.get("rows_landed")),
            note=str(part.get("note") or args.note),
        ))
    try:
        path = write(records, research_unit=args.research_unit or None,
                     run_id=args.run_id,
                     root=Path(args.root) if args.root else None)
    except ValueError as exc:
        print(f"::error::research_result: {exc}", file=sys.stderr)
        return 1
    _report_path(path, args.research_unit or None)
    return 0


def _report_path(path: Path, research_unit: Optional[str]) -> None:
    try:
        rel = path.relative_to(REPO).as_posix()
    except ValueError:
        rel = str(path)
    print(rel)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"path={rel}\n")
            fh.write(f"unit_dir={unit_dir(research_unit)}\n")


def _emit(args: argparse.Namespace) -> int:
    if args.records_file:
        return _emit_many(args)
    measurement: Dict[str, Any] = {}
    if args.measurement_file:
        raw = Path(args.measurement_file).read_text(encoding="utf-8")
        measurement = json.loads(raw)
        if not isinstance(measurement, dict):
            print("::error::--measurement-file must hold a JSON OBJECT", file=sys.stderr)
            return 1
    n: Optional[int]
    if args.n == "" or args.n is None or args.n.lower() == "null":
        n = None
    else:
        try:
            n = int(args.n)
        except ValueError:
            print(f"::error::--n must be an integer or 'null', got {args.n!r}",
                  file=sys.stderr)
            return 1
    rows_landed = None if not args.rows_landed else int(args.rows_landed)
    rec = build(
        research_unit=args.research_unit or None,
        decision_rule_id=args.decision_rule_id,
        decision_rule_registered_at=args.decision_rule_registered_at,
        verdict=args.verdict, read_state=args.read_state,
        population_description=args.population, n=n,
        workflow=args.workflow, run_id=args.run_id,
        run_attempt=args.run_attempt, run_url=args.run_url,
        commit_sha=args.commit_sha, tool=args.tool,
        power_state=args.power_state, measurement=measurement,
        artifact_store=args.artifact_store, artifact_locator=args.artifact_locator,
        rows_landed=rows_landed, note=args.note,
    )
    try:
        path = write([rec], research_unit=rec["research_unit"],
                     run_id=args.run_id, root=Path(args.root) if args.root else None)
    except ValueError as exc:
        print(f"::error::research_result: {exc}", file=sys.stderr)
        return 1
    _report_path(path, rec["research_unit"])
    return 0


def _validate_file(path: str) -> int:
    p = Path(path)
    if not p.exists():
        print(f"::error::{path} does not exist", file=sys.stderr)
        return 1
    bad = 0
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"{path}:{i}: not JSON — {exc}")
            bad += 1
            continue
        for problem in validate(rec):
            print(f"{path}:{i}: {problem}")
            bad += 1
    print(f"research_result: {path} — {bad} problem(s)")
    return 1 if bad else 0


def _good_record() -> Dict[str, Any]:
    return build(
        research_unit="RQ-20260922-007", decision_rule_id="RULE-RQ0922-007-EXIT-HEAD-ATTRIBUTION",
        decision_rule_registered_at="2026-09-22", verdict="fail", read_state="measured",
        population_description="trend_donchian BTCUSDT 1h OOS trades, 1830d window",
        n=118, workflow="research-exit-head-build.yml", run_id="1234567890",
        commit_sha="deadbeef", tool="scripts/research/exit_head_build.py",
        artifact_store="docs/research/m20-exit-head-rounds.jsonl",
        artifact_locator="row run_id=1234567890",
    )



def _self_test() -> int:
    """Planted POSITIVE (an admissible record must pass) and NEGATIVE controls
    (each planted defect must be caught). A check that cannot go red is the
    state half this register describes."""
    failures = 0

    def case(label: str, record: Dict[str, Any], must_fail: bool,
             expect_substr: str = "") -> None:
        nonlocal failures
        problems = validate(record)
        failed = bool(problems)
        ok = failed == must_fail
        if ok and must_fail and expect_substr:
            ok = any(expect_substr in p for p in problems)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures += 1
            print(f"        problems={problems!r} must_fail={must_fail} "
                  f"expect={expect_substr!r}")

    # --- THE POSITIVE CONTROL. Without it a validator that rejects everything
    #     would score a perfect run on the negatives below.
    case("positive control: a well-formed measured record is ADMISSIBLE",
         _good_record(), must_fail=False)

    # --- THE NEGATIVE CONTROLS, one per invariant this schema exists for.
    r = _good_record()
    r["read_state"] = "producer_failed"
    case("negative: producer_failed keeping a real verdict is REFUSED",
         r, must_fail=True, expect_substr="cannot carry a verdict")

    r = _good_record()

    r["read_state"] = "not_attempted"

    r["verdict"] = "not_applicable"
    case("negative: not_attempted with a fabricated n=0 is REFUSED",
         {**r, "population": {"description": "d", "n": 0}},
         must_fail=True, expect_substr="population.n: null")

    r = _good_record()

    r["population"] = {"description": "d", "n": None}
    case("negative: measured with n=null is REFUSED",
         r, must_fail=True, expect_substr="requires an integer")

    r = _good_record()

    r["population"] = {"description": "  ", "n": 5}
    case("negative: an empty population description is REFUSED",
         r, must_fail=True, expect_substr="population.description")

    r = _good_record()

    r["decision_rule"] = {"id": "", "registered_at": "2026-09-22"}
    case("negative: a result naming no decision rule is REFUSED",
         r, must_fail=True, expect_substr="decision_rule.id")

    r = _good_record()

    r["produced_by"] = dict(r["produced_by"], commit_sha="")
    case("negative: a result naming no commit sha is REFUSED",
         r, must_fail=True, expect_substr="produced_by.commit_sha")

    r = _good_record()

    r.pop("research_unit")
    case("negative: research_unit ABSENT (vs explicitly null) is REFUSED",
         r, must_fail=True, expect_substr="explicitly null")

    # --- and the state that MUST be admissible, because refusing it is how a
    #     producer gets pushed into fabricating a zero.
    r = _good_record()
    r["population"] = {"description": "d", "n": 0}
    case("positive control: measured with n=0 ('we looked and found nothing') "
         "is ADMISSIBLE", r, must_fail=False)

    r = _good_record()
    r["research_unit"] = None
    case("positive control: research_unit=null (not queue-dispatched) is "
         "ADMISSIBLE", r, must_fail=False)

    # --- the OMITTED-n sentinel: build(n=...) must leave the field ABSENT so
    #     validate() can say "you forgot it", rather than laundering a
    #     forgotten field into a deliberate "we could not look".
    omitted = build(research_unit=None, decision_rule_id="R", decision_rule_registered_at="2026-09-22",
                    verdict="pass", read_state="measured",
                    population_description="d", n=Ellipsis, workflow="w",
                    run_id="1", commit_sha="a", tool="t", artifact_store="s")
    ok = "n" not in omitted["population"]
    print(f"  {'PASS' if ok else 'FAIL'}  build(n=...) leaves population.n ABSENT")
    if not ok:
        failures += 1
    case("negative: an ABSENT population.n (vs explicitly null) is REFUSED",
         omitted, must_fail=True, expect_substr="must be PRESENT")

    # --- write() must REFUSE rather than write an inadmissible file.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        bad = _good_record()
        bad["read_state"] = "producer_failed"
        try:
            write([bad], research_unit="RQ-20260922-007", run_id="x", root=Path(td))
            print("  FAIL  write() accepted an inadmissible record")
            failures += 1
        except ValueError:
            print("  PASS  write() REFUSES an inadmissible record")
        try:
            write([], research_unit=None, run_id="x", root=Path(td))
            print("  FAIL  write() accepted an EMPTY record list")
            failures += 1
        except ValueError:
            print("  PASS  write() REFUSES an empty record list")
        p = write([_good_record()], research_unit="RQ-20260922-007",
                  run_id="99", root=Path(td))
        back = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
        ok = len(back) == 1 and back[0]["research_unit"] == "RQ-20260922-007"
        print(f"  {'PASS' if ok else 'FAIL'}  a written record reads back as written")
        if not ok:
            failures += 1
        ok = p.parent.name == "RQ-20260922-007"
        print(f"  {'PASS' if ok else 'FAIL'}  the unit id is in the PATH (greppable)")
        if not ok:
            failures += 1
        p2 = write([_good_record()], research_unit=None, run_id="98", root=Path(td))
        ok = p2.parent.name == UNATTRIBUTED
        print(f"  {'PASS' if ok else 'FAIL'}  an unattributed run lands under "
              f"{UNATTRIBUTED}/, not at the store root")
        if not ok:
            failures += 1

    print(f"research_result --self-test: {failures} failure(s)")
    return 1 if failures else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--validate", metavar="PATH")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--root", default="", help="Store root (tests only).")
    ap.add_argument("--research-unit", default="")
    ap.add_argument("--power-state", default="")
    ap.add_argument("--decision-rule-id", default="")
    ap.add_argument("--decision-rule-registered-at", default="")
    ap.add_argument("--verdict", default="")
    ap.add_argument("--read-state", default="")
    ap.add_argument("--population", default="")
    ap.add_argument("--n", default="")
    ap.add_argument("--workflow", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--run-attempt", default="")
    ap.add_argument("--run-url", default="")
    ap.add_argument("--commit-sha", default="")
    ap.add_argument("--tool", default="")
    ap.add_argument("--measurement-file", default="")
    ap.add_argument("--records-file", default="",
                    help="JSONL of per-record parts (verdict/read_state/"
                         "population/n/measurement/...) for a per-leg fan-out. "
                         "Overrides the single-record flags.")
    ap.add_argument("--artifact-store", default="")
    ap.add_argument("--artifact-locator", default="")
    ap.add_argument("--rows-landed", default="")
    ap.add_argument("--note", default="")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.validate:
        return _validate_file(args.validate)
    if args.emit:
        return _emit(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
