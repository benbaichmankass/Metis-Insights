#!/usr/bin/env python3
"""Every committed research result is ADMISSIBLE, and every result file is REACHABLE.

WHY THIS EXISTS, AND WHY IT IS NOT JUST `research_result.py --validate`
-----------------------------------------------------------------------
`scripts/research/research_result.py` is the PRODUCER's contract: it refuses to
write a record that does not carry its unit, its decision rule, its population,
its n, its read-state and its commit sha. That is the right place for the rule —
and it is worth exactly as much as the guarantee that everything in the store
went through it.

This guard validates the ARTIFACT, not the producer. That ordering is the local
convention and it is load-bearing: `docs/CLAUDE-RULES-CANONICAL.md` § "WHAT
ENFORCES THIS RULE" says *"validate the artifact, not the script that produced
it — then any bad producer is caught regardless of author."* A record
hand-written into `research/results/` by a session, or written by a second
producer somebody adds next month, is caught here and is invisible to the
producer's own self-test.

WHAT IT CHECKS
--------------
  R1  every `research/results/**/*.jsonl` line is an admissible record, by
      `research_result.validate()` — ONE implementation, imported, never
      re-typed here. Two copies of "what a result must carry" is how the two
      drift.
  R2  the path agrees with the record: `research/results/<unit>/<run_id>.jsonl`
      must hold records whose `research_unit` and `produced_by.run_id` match.
      The path IS the index — a record filed under the wrong unit is a result
      no later session can find by the only key they hold.
  R3  the store holds no file that is not `.jsonl` (outside README.md), so
      `assert_rows_landed.py` — which is line-oriented — can read anything the
      store contains.

⚠️ THE POPULATION IS THE FILESYSTEM, NOT THE DIFF. A diff-scoped version would
pass vacuously on nearly every PR, which is a green that checked nothing —
the same reasoning `check_soak_registered.py` gives for scanning the whole tree.
The store is small by construction (one small file per run), so this is cheap.

⚠️ AN EMPTY STORE IS REPORTED, NOT PASSED SILENTLY. `files=0` prints on every
run. A guard whose population quietly went to zero reads identically to a clean
one in every CI log, and that is the silent-empty class this repo has paid for
repeatedly.

Tier-1: reads committed files. No live path, no network, no VM.

Run:
    python3 scripts/ci/check_research_results.py
    python3 scripts/ci/check_research_results.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "research"))

from research_result import (  # noqa: E402
    UNATTRIBUTED,
    validate,
)

STORE = REPO / "research" / "results"


def scan(root: Path) -> Tuple[List[str], int, int]:
    """Return (problems, files_scanned, records_scanned). Pure over `root`."""
    problems: List[str] = []
    files = 0
    records = 0
    if not root.exists():
        return ([f"the result store {root} does not exist — a guard whose "
                 f"population is missing has checked NOTHING, which is not a "
                 f"pass"], 0, 0)

    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root).as_posix()
        if path.name == "README.md":
            continue
        if path.suffix != ".jsonl":
            problems.append(
                f"R3 {rel}: the store holds only `.jsonl` (plus README.md). "
                f"`assert_rows_landed.py` reads this store line-by-line, so a "
                f"whole-file JSON object here is unreadable to the landing "
                f"assertion that makes a result count as landed.")
            continue

        files += 1
        parts = path.relative_to(root).parts
        if len(parts) != 2:
            problems.append(
                f"R2 {rel}: expected `research/results/<unit>/<run_id>.jsonl`, "
                f"got depth {len(parts)}. The path IS the index.")
            continue
        dir_unit, run_id = parts[0], path.stem

        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not lines:
            problems.append(
                f"R1 {rel}: EMPTY. A run with nothing to say still has a "
                f"read_state; an empty file says nothing and is "
                f"indistinguishable from a landing that silently lost its rows.")
            continue

        for i, line in enumerate(lines, 1):
            records += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append(f"R1 {rel}:{i}: not JSON — {exc}")
                continue
            if not isinstance(rec, dict):
                problems.append(f"R1 {rel}:{i}: not a JSON object")
                continue
            for problem in validate(rec):
                problems.append(f"R1 {rel}:{i}: {problem}")

            want_dir = rec.get("research_unit") or UNATTRIBUTED
            if str(want_dir) != dir_unit:
                problems.append(
                    f"R2 {rel}:{i}: filed under `{dir_unit}/` but the record "
                    f"names research_unit={rec.get('research_unit')!r} "
                    f"(expected directory `{want_dir}`). A result filed under "
                    f"the wrong unit is one no later session can find by the "
                    f"only key it holds.")
            got_run = str(((rec.get("produced_by") or {}).get("run_id")) or "")
            if got_run and got_run != run_id:
                problems.append(
                    f"R2 {rel}:{i}: filename run id `{run_id}` disagrees with "
                    f"produced_by.run_id `{got_run}`.")

    return problems, files, records


# --------------------------------------------------------------------- self-test
def _self_test() -> int:
    """A planted POSITIVE and planted NEGATIVES, on a throwaway store.

    The positive is not decoration: a scanner that rejected everything would
    score a perfect run on the negatives alone, and a scanner that stopped
    matching would score a perfect run on the positive alone. Both directions
    have to be exercised or the guard's green means nothing.
    """
    failures = 0
    good = {
        "schema_version": 1,
        "research_unit": "RQ-20260922-002",
        "power_state": None,
        "decision_rule": {"id": "RULE-RQ0922-002-FOLD-ROBUSTNESS",
                          "registered_at": "2026-09-22"},
        "produced_by": {"workflow": "w.yml", "run_id": "42", "run_attempt": "1",
                        "run_url": None, "commit_sha": "abc123", "tool": "t.py"},
        "population": {"description": "the 8 legs passing the four-clause bar", "n": 8},
        "verdict": "fail",
        "read_state": "measured",
        "measurement": {"fold_concentrated": 5},
        "artifact": {"store": "comms/strategy_evidence/", "locator": "…",
                     "rows_landed": 8},
        "note": "",
        "generated_at": "2026-09-22T00:00:00Z",
    }

    def run_case(label: str, plant, must_fail: bool, expect: str = "") -> None:
        nonlocal failures
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plant(root)
            problems, files, records = scan(root)
            failed = bool(problems)
            ok = failed == must_fail
            if ok and must_fail and expect:
                ok = any(expect in p for p in problems)
            print(f"  {'PASS' if ok else 'FAIL'}  {label}")
            if not ok:
                failures += 1
                print(f"        problems={problems!r} files={files} records={records}")

    def _put(root: Path, unit: str, run: str, rec) -> None:
        d = root / unit
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{run}.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")

    # THE POSITIVE CONTROL.
    run_case("positive control: a well-formed store is CLEAN",
             lambda r: _put(r, "RQ-20260922-002", "42", good), must_fail=False)

    # NEGATIVE: an inadmissible record (the collapsed-state invariant).
    bad_collapsed = dict(good, read_state="producer_failed")
    run_case("negative: a collapsed verdict/read_state record is CAUGHT",
             lambda r: _put(r, "RQ-20260922-002", "42", bad_collapsed),
             must_fail=True, expect="cannot carry a verdict")

    # NEGATIVE: a fabricated zero where we could not look.
    bad_zero = dict(good, read_state="not_attempted", verdict="not_applicable",
                    population={"description": "d", "n": 0})
    run_case("negative: a fabricated n=0 on an unlooked-at run is CAUGHT",
             lambda r: _put(r, "RQ-20260922-002", "42", bad_zero),
             must_fail=True, expect="population.n: null")

    # NEGATIVE: filed under the wrong unit — the record is admissible, the
    # PATH is the defect. This is the one `--validate` alone cannot see.
    run_case("negative: a record filed under the WRONG unit directory is CAUGHT",
             lambda r: _put(r, "RQ-20260922-099", "42", good),
             must_fail=True, expect="filed under")

    # NEGATIVE: run id in the filename disagrees with the record.
    run_case("negative: a filename run id that disagrees with the record is CAUGHT",
             lambda r: _put(r, "RQ-20260922-002", "77", good),
             must_fail=True, expect="disagrees with")

    # NEGATIVE: a whole-file JSON object, which the landing assertion cannot read.
    def plant_json(root: Path) -> None:
        d = root / "RQ-20260922-002"
        d.mkdir(parents=True, exist_ok=True)
        (d / "42.json").write_text(json.dumps(good), encoding="utf-8")
    run_case("negative: a non-.jsonl file in the store is CAUGHT",
             plant_json, must_fail=True, expect="R3")

    # NEGATIVE: an empty file — a landing that lost its rows.
    def plant_empty(root: Path) -> None:
        d = root / "RQ-20260922-002"
        d.mkdir(parents=True, exist_ok=True)
        (d / "42.jsonl").write_text("", encoding="utf-8")
    run_case("negative: an EMPTY result file is CAUGHT", plant_empty,
             must_fail=True, expect="EMPTY")

    # NEGATIVE: a missing store reports a finding rather than a vacuous pass.
    with tempfile.TemporaryDirectory() as td:
        problems, files, _ = scan(Path(td) / "nope")
        ok = bool(problems) and files == 0
        print(f"  {'PASS' if ok else 'FAIL'}  negative: a MISSING store is a "
              f"finding, not a vacuous pass")
        if not ok:
            failures += 1

    # POSITIVE: an unattributed run is legal and lands under _unattributed/.
    run_case("positive control: an _unattributed/ run is CLEAN",
             lambda r: _put(r, UNATTRIBUTED, "42",
                            dict(good, research_unit=None)), must_fail=False)

    print(f"research-results-guard --self-test: {failures} failure(s)")
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    problems, files, records = scan(STORE)
    # Printed on EVERY run, both terms, so a population that quietly went to
    # zero is visible in the log rather than inferable from it.
    print(f"research-results-guard: files={files} records={records} "
          f"problems={len(problems)}")
    for p in problems:
        print(f"::error::research-results-guard: {p}")
    if problems:
        return 1
    print("research-results-guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
