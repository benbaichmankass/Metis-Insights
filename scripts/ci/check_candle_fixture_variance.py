#!/usr/bin/env python3
"""CI guard: no COMMITTED candle file may carry a degenerate price series.

MI-157, 2026-09-07. The RECURRENCE half — per ``CLAUDE.md``, a finding without
a permanent detector recurs, and this one already had a written record that did
not stop it: ``BL-20260820-PLACEHOLDER-CANDLE-FIXTURES-CARRY-NO-MARKER``
measured the same five flat files on 2026-08-20 and they were still flat, still
committed, and still being read 18 days later when MI-151 tripped over one
while doing something else.

WHAT IT CHECKS
--------------
Every **tracked** file that looks like candle data (``.csv`` / ``.jsonl`` /
``.parquet`` carrying a ``close`` column) is graded by the one owner,
``scripts/candle_variance.py::grade_close_variance``. A ``degenerate`` file
fails the run and is named. This is a CENSUS, not a sample: the guard reports
how many files it examined alongside how many failed, so a run that graded
nothing can never read as a clean pass.

THREE STATES, NEVER COLLAPSED (``docs/CLAUDE-RULES-CANONICAL.md`` § "Collapsed
states"). ``non_degenerate`` passes. ``degenerate`` fails. ``not_gradeable`` —
fewer than 2 usable closes, so no return series *exists* — is **reported and
does not fail**, because "we could not look" is not "we looked and it was
fine", and a 1-row corpus is a different problem from a flat one.

WHY IT IS STDLIB-ONLY. It runs inside the fast static-guard job
(``scripts/ci/run_guards.py``), which does not install pandas. Parquet is
therefore graded only when ``pyarrow`` happens to be importable, and is
otherwise reported as ``skipped_no_reader`` — **announced, never silent**, per
that harness's own rule that a guard which quietly declines to run is the
"green that checked nothing".

SELF-TEST. ``--self-test`` proves the guard can FAIL before its silence is
trusted: it grades a synthetic flat series (must be caught) and a synthetic
real one (must pass). A guard demonstrated only on the negative case is not
demonstrated — that requirement is written into
``BL-20260820-PLACEHOLDER-CANDLE-FIXTURES-CARRY-NO-MARKER``'s own
done_condition.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from candle_variance import grade_close_variance  # noqa: E402

CANDLE_SUFFIXES = (".csv", ".jsonl", ".parquet")

# A file is candle data if it carries a `close` column. Nothing is matched on
# filename: `data/ict_validate_manifest.csv` is named like a data file and is a
# manifest, while a real corpus may be named anything at all.
CLOSE_COLUMN = "close"


def _rel(path: Path) -> str:
    """Repo-relative when possible; absolute otherwise.

    ``Path.relative_to`` RAISES for a path outside the repo, and the guard
    accepts explicit paths (that is how its negative control is exercised), so
    a bare ``relative_to`` crashes the run precisely on the failure branch —
    the one path that must always be reachable.
    """
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    return [REPO / name for name in out.split("\0") if name]


def _closes_from_csv(path: Path):
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return None
        lowered = {name.lower(): name for name in reader.fieldnames}
        if CLOSE_COLUMN not in lowered:
            return None
        key = lowered[CLOSE_COLUMN]
        return [row.get(key) for row in reader]


def _closes_from_jsonl(path: Path):
    closes, saw_close = [], False
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                return None
            if not isinstance(row, dict):
                return None
            if CLOSE_COLUMN in row:
                saw_close = True
                closes.append(row[CLOSE_COLUMN])
    return closes if saw_close else None


def _closes_from_parquet(path: Path):
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return "skipped_no_reader"
    table = pq.read_table(path)
    names = {name.lower(): name for name in table.column_names}
    if CLOSE_COLUMN not in names:
        return None
    return table.column(names[CLOSE_COLUMN]).to_pylist()


def collect(paths) -> tuple[list, list, list]:
    """Grade every candle-shaped file. Returns (graded, skipped, not_candle)."""
    graded, skipped, not_candle = [], [], []
    for path in paths:
        if path.suffix.lower() not in CANDLE_SUFFIXES or not path.is_file():
            continue
        try:
            if path.suffix.lower() == ".csv":
                closes = _closes_from_csv(path)
            elif path.suffix.lower() == ".jsonl":
                closes = _closes_from_jsonl(path)
            else:
                closes = _closes_from_parquet(path)
        except (OSError, UnicodeDecodeError) as exc:
            skipped.append((path, f"unreadable: {exc}"))
            continue
        if closes == "skipped_no_reader":
            skipped.append((path, "parquet: pyarrow not importable in this job"))
            continue
        if closes is None:
            not_candle.append(path)
            continue
        graded.append((path, grade_close_variance(closes)))
    return graded, skipped, not_candle


def self_test() -> int:
    """Prove the guard can fail before its silence is trusted."""
    failures = []

    flat = grade_close_variance([95000.0] * 300)
    if flat.state != "degenerate":
        failures.append(f"NEGATIVE CONTROL: flat series graded {flat.state!r}, expected 'degenerate'")

    real = grade_close_variance([100.0, 101.5, 99.25, 103.0, 102.5])
    if real.state != "non_degenerate":
        failures.append(f"POSITIVE CONTROL: varying series graded {real.state!r}, expected 'non_degenerate'")

    short = grade_close_variance([100.0])
    if short.state != "not_gradeable":
        failures.append(f"THIRD STATE: 1-row series graded {short.state!r}, expected 'not_gradeable'")

    # A real corpus that happens to be committed must not trip the guard.
    corpus = REPO / "data" / "backtest_candles.csv"
    if corpus.exists():
        graded, _, _ = collect([corpus])
        if not graded:
            failures.append("POSITIVE CONTROL: data/backtest_candles.csv was not graded at all")
        elif graded[0][1].state != "non_degenerate":
            failures.append(
                f"POSITIVE CONTROL: data/backtest_candles.csv graded "
                f"{graded[0][1].state!r} — the guard would fail a REAL corpus"
            )

    if failures:
        print("candle-fixture-variance SELF-TEST: FAIL")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("candle-fixture-variance SELF-TEST: OK "
          "(flat caught · varying passed · 1-row held as not_gradeable · real corpus passed)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true",
                        help="prove the guard can fail, then exit")
    parser.add_argument("paths", nargs="*",
                        help="optional explicit paths; default is every tracked file")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    targets = [Path(p) for p in args.paths] if args.paths else _tracked_files()
    graded, skipped, _ = collect(targets)

    degenerate = [(p, g) for p, g in graded if g.state == "degenerate"]
    ungradeable = [(p, g) for p, g in graded if g.state == "not_gradeable"]

    # ALWAYS STATE THE POPULATION (CLAUDE.md, binding on every quantitative
    # claim). A guard that prints only its failures cannot be told apart from
    # one that examined nothing.
    print(f"candle-fixture-variance: graded {len(graded)} committed candle file(s); "
          f"{len(degenerate)} degenerate, {len(ungradeable)} not_gradeable, "
          f"{len(skipped)} skipped.")

    for path, reason in skipped:
        print(f"  SKIPPED  {_rel(path)}: {reason}")
    for path, grade in ungradeable:
        print(f"  UNGRADED {_rel(path)}: {grade.detail}")

    if not degenerate:
        if not graded:
            print("  NOTE: zero candle files were graded. That is 'nothing to check', "
                  "NOT 'everything checked out'.")
        print("candle-fixture-variance: OK")
        return 0

    print("\ncandle-fixture-variance: FAIL — a committed candle file has no price variation.")
    for path, grade in degenerate:
        print(f"  {_rel(path)}: {grade.detail}")
    print(
        "\nA backtest over a constant-price series produces confident numbers that "
        "mean nothing — every return is exactly 0.0, so expectancy, correlation and "
        "rho are undefined rather than weak.\n"
        "Either commit real fetched candle history "
        "(scripts/ops/fetch_backtest_candles.py), or do not commit the file at all "
        "and let the consumer fail loudly on its absence. Do NOT commit a "
        "placeholder: that is exactly MI-157."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
