#!/usr/bin/env python3
"""CI guard: no COMMITTED candle file backing a rostered (symbol, timeframe)
leg may be too thin to measure anything.

MANAGER-CHECKLIST.json row E4, 2026-09-25. The class this repo already paid
for once, restated one axis over: ``check_candle_fixture_variance.py`` grades
whether a committed series MOVES (flat vs. real); this grades whether there
is ENOUGH of it. A five-row corpus can have real day-to-day variance and
still be too thin to support a single fold — "the fixture is 5,001 rows
spanning 3.5 days of 2022, so anything falling back to it produces a
confident answer about nothing" names exactly this: the defect is not that
the numbers are wrong, it is that there are not enough of them to mean
anything, and nothing at the call site says so.

WHAT IT CHECKS
--------------
Every tracked file matching ``data/<SYMBOL>_<timeframe>.csv`` (the canonical
convention ``scripts/research/m20_fleet_exit_sweep.py::resolve_data`` reads)
is counted. A file below ``--floor`` (default 500 rows) FAILS the run and is
named. A CENSUS, not a sample: the guard states how many files it counted
alongside how many failed, so a run that counted nothing can never read as a
clean pass (RULE ONE — always state the population).

``docs/reference/corpus-manifest.json`` (docs/reference/backtest-data-loading.md), when
present, is cross-checked too: a manifest entry whose claimed ``rows`` does
not match the file's ACTUAL row count fails the run under a separate finding
— a stale manifest is a documentation defect of exactly the class RULE ONE
exists for ("read the field, not the prose about it").

THE FIXTURE. ``data/backtest_candles.csv`` (the fast smoke path — it stays;
row E4 only removed reaching it BY DEFAULT, not its existence) is 5,001 rows,
comfortably above the default floor, so it needs no exemption. The
``--allow`` list exists anyway for a future smoke fixture that IS meant to be
tiny on purpose, so that case has a sanctioned, visible way to opt out
instead of an ad-hoc guard exception.

SELF-TEST. ``--self-test`` proves the guard can FAIL before its silence is
trusted: a synthetic 5-row file must be caught (positive control) and a
synthetic 600-row file must pass (negative control), plus a manifest/file
row-count mismatch must be caught on its own.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

DEFAULT_FLOOR = 500
# data/<SYMBOL>_<timeframe>.csv — the one convention
# scripts/research/m20_fleet_exit_sweep.py::resolve_data reads natively.
CANDLE_NAME_RE = re.compile(
    r"^data/[A-Za-z0-9]+_(?:5m|15m|30m|1h|2h|4h|6h|8h|12h|1d|1w)\.csv$")
MANIFEST_PATH = REPO / "docs" / "reference" / "corpus-manifest.json"


def _rel(path: Path) -> str:
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


def row_count(path: Path) -> int:
    """Data rows, excluding the header. -1 if the file cannot be read."""
    try:
        with path.open(newline="") as handle:
            reader = csv.reader(handle)
            n = sum(1 for _ in reader) - 1
            return max(n, 0)
    except (OSError, UnicodeDecodeError):
        return -1


def candle_files(root: Path) -> list[Path]:
    out = []
    for p in _tracked_files():
        rel = _rel(p)
        if CANDLE_NAME_RE.match(rel) and p.is_file():
            out.append(p)
    return out


def check(paths: list[Path], floor: int, allow: set[str]) -> tuple[list, list]:
    """Returns (counted, below_floor)."""
    counted = []
    below = []
    for p in paths:
        rel = _rel(p)
        n = row_count(p)
        counted.append((rel, n))
        if rel in allow:
            continue
        if n < 0:
            below.append((rel, n, "unreadable"))
        elif n < floor:
            below.append((rel, n, f"below floor {floor}"))
    return counted, below


def check_manifest(root: Path, floor: int) -> tuple[list[str], list[str]]:
    """(mismatches, below_floor) against CORPUS-MANIFEST.json's DECLARED rows.

    The manifest, not the CSVs, is what CI actually has: the CSVs are
    gitignored and fetched on demand (docs/reference/backtest-data-loading.md).
    So the row-floor check's primary signal is the manifest's own claim --
    ``below_floor`` -- and ``mismatches`` is the secondary check that the claim
    still agrees with a file that happens to be present on disk (e.g. right
    after a fresh fetch, or the sanctioned fixture).
    """
    manifest_path = root / "docs" / "reference" / "corpus-manifest.json"
    if not manifest_path.exists():
        return [], []
    try:
        entries = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        return ["CORPUS-MANIFEST.json is not readable/valid JSON"], []
    mismatches = []
    below_floor = []
    for e in entries.get("pairs", []) if isinstance(entries, dict) else entries:
        if not isinstance(e, dict):
            continue
        rel = e.get("file")
        claimed = e.get("rows")
        if not rel or claimed is None:
            continue
        # rows=0 with a stated `gap` reason is an HONEST disclosed gap (a
        # symbol this corpus does not cover yet), not a lie the field can
        # catch a comment telling -- do not fail the run over it, and it is
        # reported separately from a floor violation (it is a documented
        # absence, not a too-thin measurement).
        if claimed == 0 and e.get("gap"):
            continue
        if claimed < floor:
            below_floor.append(f"{rel}: manifest declares {claimed} rows (floor {floor})")
        p = root / rel
        if not p.exists():
            continue  # gitignored CSV absent from this checkout -- expected
        actual = row_count(p)
        if actual != claimed:
            mismatches.append(f"{rel}: manifest claims {claimed} rows, file has {actual}")
    return mismatches, below_floor


def self_test() -> int:
    import tempfile
    failures = []

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "data").mkdir()
        (d / "docs" / "reference").mkdir(parents=True)
        thin = d / "data" / "ZZZTEST_5m.csv"
        with thin.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for i in range(5):
                w.writerow([f"2026-01-01 00:0{i}:00+00:00", 1, 1, 1, 1, 1])

        deep = d / "data" / "ZZZTEST_1h.csv"
        with deep.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for i in range(600):
                w.writerow([f"2026-01-01T{i:04d}", 1 + i * 0.01, 1, 1, 1, 1])

        n_thin = row_count(thin)
        n_deep = row_count(deep)
        if n_thin != 5:
            failures.append(f"POSITIVE CONTROL: expected 5 rows, counted {n_thin}")
        if n_deep != 600:
            failures.append(f"NEGATIVE CONTROL: expected 600 rows, counted {n_deep}")

        _, below = check([thin, deep], DEFAULT_FLOOR, allow=set())
        below_names = {b[0] for b in below}
        if not any(n.endswith("ZZZTEST_5m.csv") for n in below_names):
            failures.append("POSITIVE CONTROL: the 5-row file was NOT flagged below floor")
        if any(n.endswith("ZZZTEST_1h.csv") for n in below_names):
            failures.append("NEGATIVE CONTROL: the 600-row file was incorrectly flagged")

        # allow-list exemption. `_rel()` reports repo-relative paths off the
        # module-level REPO, so it is monkeypatched to this tempdir for the
        # span of this one check and restored in `finally` -- otherwise a
        # tempdir file never matches a "data/..." allow-list entry and this
        # control cannot pass regardless of whether the feature works.
        global REPO
        _real_repo = REPO
        REPO = d
        try:
            _, below2 = check([thin], DEFAULT_FLOOR, allow={"data/ZZZTEST_5m.csv"})
        finally:
            REPO = _real_repo
        if below2:
            failures.append("ALLOW-LIST: an explicitly allowed thin file was still flagged")

        # manifest/file mismatch detection
        (d / "docs" / "reference" / "corpus-manifest.json").write_text(json.dumps({
            "pairs": [{"file": "data/ZZZTEST_1h.csv", "rows": 9999}]
        }))
        mism, _ = check_manifest(d, DEFAULT_FLOOR)
        if not mism:
            failures.append("MANIFEST MISMATCH: a false row count in the manifest was not caught")

        # a manifest that agrees must NOT be flagged
        (d / "docs" / "reference" / "corpus-manifest.json").write_text(json.dumps({
            "pairs": [{"file": "data/ZZZTEST_1h.csv", "rows": 600}]
        }))
        mism2, below_mf2 = check_manifest(d, DEFAULT_FLOOR)
        if mism2 or below_mf2:
            failures.append(f"MANIFEST MISMATCH: a CORRECT manifest was flagged: {mism2 or below_mf2}")

        # a disclosed gap (rows=0, a stated reason, no file) is honest, not a lie
        (d / "docs" / "reference" / "corpus-manifest.json").write_text(json.dumps({
            "pairs": [{"file": "data/ZZZTEST_NOPE_1d.csv", "rows": 0,
                      "gap": "fetch returned no rows"}]
        }))
        mism3, below_mf3 = check_manifest(d, DEFAULT_FLOOR)
        if mism3 or below_mf3:
            failures.append(f"HONEST GAP: a disclosed zero-row gap was flagged as a lie: {mism3 or below_mf3}")

        # the manifest's DECLARED row count is the primary signal (the CSV
        # itself is normally absent from a checkout -- gitignored, fetched on
        # demand), so a thin DECLARATION must fail even with no file present.
        (d / "docs" / "reference" / "corpus-manifest.json").write_text(json.dumps({
            "pairs": [{"file": "data/ZZZTEST_NOFILE_1d.csv", "rows": 12}]
        }))
        _, below_mf4 = check_manifest(d, DEFAULT_FLOOR)
        if not below_mf4:
            failures.append("MANIFEST FLOOR: a thin DECLARED row count with no file present "
                            "was not caught")

    if failures:
        print("corpus-row-floor SELF-TEST: FAIL")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("corpus-row-floor SELF-TEST: OK "
          "(5-row caught · 600-row passed · allow-list honoured · "
          "manifest mismatch caught · correct manifest passed · "
          "disclosed gap not treated as a lie · thin manifest declaration caught)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--floor", type=int, default=DEFAULT_FLOOR,
                        help=f"minimum data rows per committed candle file (default {DEFAULT_FLOOR})")
    parser.add_argument("--allow", action="append", default=[],
                        help="repo-relative path exempt from the floor (repeatable); "
                             "the fixture needs none today but this is the sanctioned "
                             "escape hatch for a future intentionally-tiny smoke file")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    allow = set(args.allow)
    paths = candle_files(REPO)
    counted, below = check(paths, args.floor, allow)

    print(f"corpus-row-floor: counted {len(counted)} committed data/<SYMBOL>_<tf>.csv "
          f"file(s) on disk against floor={args.floor}; {len(below)} below floor.")

    mismatches, manifest_below = check_manifest(REPO, args.floor)
    if MANIFEST_PATH.exists():
        declared = len(json.loads(MANIFEST_PATH.read_text()).get("pairs", []))
        print(f"corpus-row-floor: CORPUS-MANIFEST.json declares {declared} (symbol, "
              f"timeframe) pair(s); {len(manifest_below)} declare fewer than "
              f"{args.floor} rows.")
    else:
        print("corpus-row-floor: no docs/reference/corpus-manifest.json found.")

    if mismatches:
        print(f"\ncorpus-row-floor: FAIL — {len(mismatches)} CORPUS-MANIFEST.json "
              f"row(s) disagree with the file on disk (field beats comment):")
        for m in mismatches:
            print(f"  {m}")

    if manifest_below:
        print(f"\ncorpus-row-floor: FAIL — {len(manifest_below)} CORPUS-MANIFEST.json "
              f"row(s) declare too few rows to measure anything against:")
        for m in manifest_below:
            print(f"  {m}")

    if below:
        print("\ncorpus-row-floor: FAIL — a committed candle file has too few rows "
              "to measure anything against.")
        for rel, n, why in below:
            print(f"  {rel}: {n} rows ({why})")

    if not below and not mismatches and not manifest_below:
        if not counted and not MANIFEST_PATH.exists():
            print("  NOTE: nothing was counted. That is 'nothing to check', "
                  "NOT 'everything checked out'.")
        print("corpus-row-floor: OK")
        return 0

    print(
        "\nA run over a handful of rows produces a confident-looking result with "
        "no statistical basis — this is the row-count axis of the same defect "
        "check_candle_fixture_variance.py catches on the price-variance axis "
        "(MANAGER-CHECKLIST.json row E4). Fetch real history for the symbol "
        "(python3 scripts/ops/fetch_backtest_corpus.py --symbol <SYM> --timeframe <TF>; "
        "see docs/reference/backtest-data-loading.md), or if the file is a "
        "deliberate tiny smoke fixture, pass it to --allow explicitly."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
