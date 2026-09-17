#!/usr/bin/env python3
# wiring: manual-only - this is a READER a session runs while dispositioning a
# corpus row, not a job. It writes nothing, re-grades nothing, and has no
# schedule because "is this absent field a measurement or a loss?" is a
# question asked at read time, not a condition to watch. Registering it as a
# CI guard was considered and REJECTED: 20 of the rows it reports are
# `absent_between_boundaries`, which is `we cannot establish it` — a guard
# that failed on them would fail every PR over a state nobody can fix, and a
# guard that passed on them would report the ambiguity as clean. Its own
# failure paths are exercised by `--self-test` (12 planted controls).
"""A corpus row is missing a field — was it NOT MEASURED, or measured and DROPPED?

Those are opposite claims and the M20 sweep corpus cannot presently tell them
apart. This reader grades the distinction as far as the committed data allows,
and says plainly where it cannot.

WHY THIS EXISTS
---------------
`BL-20260816-CORPUS-CONFLICT-REDERIVE-RUNS-THE-STALE-BRANCH-EXTRACTOR`: the
sweep workflow's rebase-conflict path did `git reset --hard origin/$TARGET`
and then re-ran the extractor, so the re-derive ran the TARGET BRANCH's copy
of `m20_corpus_extract.py`. Every field added since that branch last moved was
silently absent from the rows it wrote, while the job stayed green.

That row's CODE half is fixed (the dispatched extractor is preserved to
`$RUNNER_TEMP` before any git operation) and VERIFIED on a real conflicting
run. Its DATA half asked for the affected rows to be "re-derived or explicitly
marked as schema-degraded; until then a consumer cannot distinguish 'the field
was not measured' from 'the field was measured and dropped in transit'."

⚠️ THE BLOCKER IS STRUCTURAL, NOT CLERICAL, AND THAT IS THE FINDING
-------------------------------------------------------------------
Whether a given run's extractor COULD emit a field is a property of the commit
that run was dispatched with. **The corpus records no dispatched sha.** Its
`run_id` is deliberately the sweep's own `generated_at` TIMESTAMP (see
`m20_corpus_extract.run_id_for` — that choice is correct for its own purpose,
keeping one matrix run from splitting into N), so a row cannot be joined back
to a workflow run whose `head_sha` would answer the question.

So for a row that is missing a field there are two defensible boundaries and
they disagree:

  * the CORPUS boundary — the earliest `sweep_generated_at` among rows that DO
    carry the field. Self-contained, but a feature-branch dispatch can carry a
    field before the default branch does, so this can be too late.
  * the DEFAULT-BRANCH boundary — when the field-adding commit landed on the
    default branch. Authoritative for default-branch dispatches only, and it
    needs git history this clone may not have.

Where they disagree the honest answer is `absent_between_boundaries` — *we
cannot establish it* — never a verdict picked from whichever boundary happened
to be available. Picking one silently is the unprovenanced-diagnostic
sub-class B shape (implicit input selection) this repo names in `CLAUDE.md`.

WHAT IT DOES NOT DO
-------------------
It writes nothing and re-grades nothing. Stamping "schema-degraded" onto rows
whose cause is not established would record a claim nobody measured, which is
worse than the absence it replaces.

Usage:
    python3 scripts/research/m20_corpus_schema_census.py \
        --corpus docs/research/m20-sweep-corpus.jsonl --field live_tp_reach_r_n_IS
    python3 scripts/research/m20_corpus_schema_census.py --self-test
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

CORPUS_DEFAULT = "docs/research/m20-sweep-corpus.jsonl"
TS_FIELD = "sweep_generated_at"

#: The field block that motivated this reader. Eight keys landed together in
#: #9037; any one of them answers "did this run's extractor emit the block?".
FIELD_DEFAULT = "live_tp_reach_r_n_IS"

# --- the five states, never collapsed -------------------------------------
PRESENT = "present"
#: Generated before BOTH candidate boundaries — the extractor provably could
#: not emit it. A legitimate "not measured", not a defect.
PREDATES = "absent_predates_both"
#: Generated after BOTH boundaries. It SHOULD have been emitted. Whether it was
#: dropped in transit or is genuinely N/A for this cell is **not established**
#: here — this state is the finding, not the verdict.
AFTER = "absent_after_both"
#: The two boundaries disagree about this row. **We cannot look**: the corpus
#: records no dispatched sha. Never folded into either neighbour.
BETWEEN = "absent_between_boundaries"
#: No usable timestamp — we could not place the row on either side.
UNDATEABLE = "absent_undateable"

STATES = (PRESENT, PREDATES, AFTER, BETWEEN, UNDATEABLE)

# --- how the default-branch boundary was established ----------------------
BOUNDARY_STATED = "stated"        # caller passed it
BOUNDARY_FROM_GIT = "from_git"    # derived from the naming commit
BOUNDARY_UNREADABLE = "unreadable"  # we tried and could not — NOT "there is none"


def load_rows(path: Path) -> tuple[list[dict], int]:
    """Rows, plus the count of lines that would not parse.

    A malformed line is COUNTED, never silently skipped: a census whose
    denominator quietly shrinks is the unasserted-denominator shape.
    """
    rows: list[dict] = []
    bad = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            bad += 1
    return rows, bad


def corpus_boundary(rows: list[dict], field: str) -> str | None:
    """Earliest timestamp among rows that DO carry ``field``.

    ``None`` means no row carries it at all — in which case this census has no
    positive control and must refuse rather than report every row as absent.
    """
    stamps = [str(r.get(TS_FIELD)) for r in rows
              if field in r and isinstance(r.get(TS_FIELD), str) and r.get(TS_FIELD)]
    return min(stamps) if stamps else None


def default_branch_boundary(field: str, repo: Path) -> tuple[str | None, str]:
    """When did ``field`` reach the default branch? ``(iso_or_None, state)``.

    Uses ``git log -S`` over the extractor. A shallow clone cannot answer this,
    and that reads as :data:`BOUNDARY_UNREADABLE` — *we could not look* — never
    as "the field was always there".
    """
    extractor = "scripts/research/m20_corpus_extract.py"
    try:
        out = subprocess.run(
            ["git", "log", "--reverse", "--format=%aI", "-S", field, "--", extractor],
            cwd=repo, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None, BOUNDARY_UNREADABLE
    if out.returncode != 0:
        return None, BOUNDARY_UNREADABLE
    stamps = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    if not stamps:
        return None, BOUNDARY_UNREADABLE
    return stamps[0], BOUNDARY_FROM_GIT


def _to_utc(iso: str) -> str:
    """Normalise an ISO stamp so two offsets compare correctly as strings."""
    import datetime as dt
    try:
        d = dt.datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if d.tzinfo is None:
        return d.isoformat()
    return d.astimezone(dt.timezone.utc).isoformat()


def grade_row(row: dict, field: str, corpus_b: str, default_b: str | None) -> str:
    """Grade one row into one of :data:`STATES`.

    ``default_b`` may be ``None`` (unreadable). With only one boundary there is
    no disagreement to report, so BETWEEN cannot arise — which is a narrower
    census, not a cleaner one, and the caller says so in its output.
    """
    if field in row:
        return PRESENT
    ts = row.get(TS_FIELD)
    if not isinstance(ts, str) or not ts:
        return UNDATEABLE
    t = _to_utc(ts)
    bounds = [_to_utc(corpus_b)] + ([_to_utc(default_b)] if default_b else [])
    before_all = all(t < b for b in bounds)
    after_all = all(t >= b for b in bounds)
    if before_all:
        return PREDATES
    if after_all:
        return AFTER
    return BETWEEN


def census(rows: list[dict], field: str, corpus_b: str,
           default_b: str | None) -> Counter:
    c: Counter = Counter()
    for r in rows:
        c[grade_row(r, field, corpus_b, default_b)] += 1
    return c


def _self_test() -> int:
    """Planted controls. Every state is reached, and each by construction."""
    CB = "2026-08-14T10:49:00+00:00"     # corpus boundary
    DB = "2026-08-13T15:55:20+00:00"     # default-branch boundary (earlier)
    F = "live_tp_reach_r_n_IS"
    cases = [
        ("a row carrying the field is PRESENT whatever its date",
         {TS_FIELD: "2026-08-10T00:00:00+00:00", F: 3}, PRESENT),
        ("absent + before both boundaries -> the extractor could not emit it",
         {TS_FIELD: "2026-08-12T00:00:00+00:00"}, PREDATES),
        ("absent + after both boundaries -> it should have been emitted",
         {TS_FIELD: "2026-08-16T22:30:00+00:00"}, AFTER),
        ("absent + BETWEEN the two boundaries -> we cannot establish it",
         {TS_FIELD: "2026-08-14T10:41:00+00:00"}, BETWEEN),
        ("absent + no timestamp -> undateable, never 'predates'",
         {"leg": "x"}, UNDATEABLE),
        ("absent + empty timestamp is undateable too",
         {TS_FIELD: ""}, UNDATEABLE),
        ("a row exactly ON the corpus boundary is AFTER, not between",
         {TS_FIELD: CB}, AFTER),
        ("offsets are normalised: +03:00 is compared as UTC, not as a string",
         # 2026-08-13T18:00:00+03:00 == 15:00Z, which is BEFORE DB (15:55:20Z)
         {TS_FIELD: "2026-08-13T18:00:00+03:00"}, PREDATES),
    ]
    ok = True
    for label, row, want in cases:
        got = grade_row(row, F, CB, DB)
        status = "PASS" if got == want else f"FAIL (got {got}, want {want})"
        if got != want:
            ok = False
        print(f"  self-test: {label} -> {status}")

    # With no default-branch boundary the BETWEEN state must be unreachable —
    # a narrower census, and the caller must not read its absence as agreement.
    got = grade_row({TS_FIELD: "2026-08-14T10:41:00+00:00"}, F, CB, None)
    if got == BETWEEN:
        ok = False
        print("  self-test: BETWEEN must be unreachable with one boundary -> FAIL")
    else:
        print(f"  self-test: one boundary only -> BETWEEN unreachable (got {got}): PASS")

    # NEGATIVE CONTROL on the refusal: a corpus in which NO row carries the
    # field has no positive control, so the boundary must come back None and
    # the caller must refuse rather than report every row absent.
    if corpus_boundary([{TS_FIELD: "2026-08-10T00:00:00+00:00"}], F) is not None:
        ok = False
        print("  self-test: a corpus with no carrying row must yield no boundary -> FAIL")
    else:
        print("  self-test: a corpus with no carrying row yields no boundary: PASS")
    if corpus_boundary([{TS_FIELD: "2026-08-10T00:00:00+00:00", F: 1}], F) != \
            "2026-08-10T00:00:00+00:00":
        ok = False
        print("  self-test: positive control — one carrying row sets the boundary -> FAIL")
    else:
        print("  self-test: positive control — one carrying row sets the boundary: PASS")

    # A malformed line is counted, not dropped.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "c.jsonl"
        p.write_text('{"a": 1}\nnot json\n\n{"b": 2}\n', encoding="utf-8")
        rows, bad = load_rows(p)
        if (len(rows), bad) != (2, 1):
            ok = False
            print(f"  self-test: malformed lines counted -> FAIL ({len(rows)}, {bad})")
        else:
            print("  self-test: a malformed line is COUNTED, not silently dropped: PASS")

    print("corpus-schema-census self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=CORPUS_DEFAULT)
    ap.add_argument("--field", default=FIELD_DEFAULT,
                    help="one key from the block whose presence is being graded")
    ap.add_argument("--default-branch-boundary", default=None,
                    help="ISO stamp for when the field reached the default "
                         "branch; derived from git when omitted")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    repo = Path(__file__).resolve().parents[2]
    path = Path(a.corpus)
    if not path.is_absolute():
        path = repo / path
    if not path.exists():
        print(f"::error::corpus not found: {path}", file=sys.stderr)
        return 2

    rows, bad = load_rows(path)
    if not rows:
        print(f"::error::{path} parsed to ZERO rows ({bad} malformed line(s)) — "
              "refusing to report a clean census over nothing", file=sys.stderr)
        return 2

    corpus_b = corpus_boundary(rows, a.field)
    if corpus_b is None:
        print(f"::error::NO row in {path.name} carries `{a.field}`, so this "
              "census has no positive control and every row would grade "
              "'absent'. Refusing — check the field name before reading a "
              "quiet result as a finding.", file=sys.stderr)
        return 2

    if a.default_branch_boundary:
        default_b, b_state = a.default_branch_boundary, BOUNDARY_STATED
    else:
        default_b, b_state = default_branch_boundary(a.field, repo)

    c = census(rows, a.field, corpus_b, default_b)
    total = sum(c.values())
    print(f"POPULATION: {total} row(s) from {path.name}"
          + (f" ({bad} malformed line(s) not graded)" if bad else ""))
    print(f"  field graded                : {a.field}")
    print(f"  corpus boundary             : {corpus_b}")
    print(f"  default-branch boundary     : {default_b or '(none)'}  [{b_state}]")
    if b_state == BOUNDARY_UNREADABLE:
        print("  ⚠️  the default-branch boundary could NOT be read (a shallow "
              "clone cannot answer it). This is a ONE-BOUNDARY census: "
              f"`{BETWEEN}` is unreachable, so its absence below is NOT "
              "evidence the two boundaries agree.")
    print()
    for s in STATES:
        print(f"  {s:<28} {c.get(s, 0):>6}")
    print()
    print("⚠️ `absent_after_both` is NOT a verdict of data loss — it says the "
          "extractor should have emitted the field and did not. Whether that "
          "is transit loss or a genuinely N/A cell is NOT established here, "
          "and cannot be from the committed corpus: it records no dispatched "
          "sha (`run_id` is the sweep's own timestamp, by design).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
