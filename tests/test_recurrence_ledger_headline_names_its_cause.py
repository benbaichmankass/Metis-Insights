"""`recurrence-ledger-guard` must headline a finding with the cause it tested.

⚠️ REPRODUCED, NOT CONSTRUCTED. Against `main` @`a374d3dd9` on 2026-09-17, over
a ledger in which EVERY class names an executable prevention and whose only
fault is a duplicate id:

    ::error::docs/claude/RECURRENCE-LEDGER.json — a repeated mistake with no
      prevention is a lesson nobody learned:
      - RC-SAME: duplicate id

Nothing about preventions was established on that run. UNPROVENANCED DIAGNOSTIC
OUTPUT sub-class A — a message naming a cause no code path tested. **9 of the
guard's 13 rules are not about preventions at all**, and the worst is a ledger
that could not be READ: the run learned nothing whatsoever and reported that a
class had no prevention.

⚠️ **THE HARM IS THE REMEDY.** `no prevention` says *build a guard*; a
malformed row says *fix the row's fields*; an unreadable file says *restore it,
nothing was checked*. One headline sent all three readers to build a guard.

⚠️ **A SECOND DEFECT IN THE SAME SENTENCE:** it hardcoded
`docs/claude/RECURRENCE-LEDGER.json` while `--path` pointed elsewhere — naming
a file it had not read, alongside a cause it had not tested.

This file is the END-TO-END half: `--self-test` covers `check()`'s tagging and
`group_by_headline` directly, and only a real subprocess run proves what
`main()` PRINTS, which is the surface a CI reader sees.

⚠️ The fixture ids read `RC-…`, never `BL-…`: `scripts/ops/check_backlog_refs.py`
scans added lines for register ids, so an invented `BL-`-shaped id in a test
resolves to no filed row and is reported as a dangling tracking reference.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "ci" / "check_recurrence_ledger.py"

PREVENTION = "no prevention is a lesson nobody learned"
MALFORMED = "a class ROW is malformed"
UNREADABLE = "could NOT BE READ"

GOOD = {
    "id": "RC-ALPHA", "title": "t",
    "first_seen": "2026-01-01", "last_seen": "2026-02-01",
    "occurrences": 2, "evidence": ["a", "b"],
    # a real, existing, executable prevention — so NO class here lacks one
    "prevention": "scripts/ci/check_recurrence_ledger.py fails when it recurs",
}


def _run(tmp_path: Path, classes):
    p = tmp_path / "ledger.json"
    p.write_text(json.dumps({"schema_version": 1, "classes": classes}),
                 encoding="utf-8")
    proc = subprocess.run([sys.executable, str(GUARD), "--path", str(p)],
                          capture_output=True, text=True, cwd=REPO)
    return proc.returncode, proc.stdout, p


# ── the defect ─────────────────────────────────────────────────────────────

def test_a_duplicate_id_is_not_headlined_as_a_missing_prevention(tmp_path):
    """The reproduction, end to end through `main`."""
    rc, out, _ = _run(tmp_path, [GOOD, dict(GOOD)])
    assert rc == 1, out
    assert "duplicate id" in out, out
    assert PREVENTION not in out, (
        "a malformed row is filed under the missing-prevention headline — "
        "every class in this ledger names an executable prevention:\n" + out)
    assert MALFORMED in out, out


def test_the_malformed_headline_does_not_send_the_reader_to_build_a_guard(tmp_path):
    """Naming the cause is half; the reader acts on the REMEDY."""
    _, out, _ = _run(tmp_path, [GOOD, dict(GOOD)])
    headline = next(ln for ln in out.splitlines() if MALFORMED in ln)
    assert "fix the row's fields" in headline, headline
    assert "NOT to go and build a guard" in headline, headline


def test_an_unreadable_ledger_says_nothing_was_checked(tmp_path):
    """The worst case: the file was never read, so no rule ran at all."""
    missing = tmp_path / "nope.json"
    proc = subprocess.run([sys.executable, str(GUARD), "--path", str(missing)],
                          capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 1, proc.stdout
    assert UNREADABLE in proc.stdout, proc.stdout
    assert PREVENTION not in proc.stdout, (
        "a ledger that could not be read was reported as a class with no "
        "prevention — nothing was checked:\n" + proc.stdout)


def test_every_headline_names_the_path_it_actually_read(tmp_path):
    """THE SECOND DEFECT: the old line hardcoded the canonical path."""
    _, out, p = _run(tmp_path, [GOOD, dict(GOOD)])
    headline = next(ln for ln in out.splitlines() if ln.startswith("::error::"))
    assert str(p) in headline, headline
    assert "docs/claude/RECURRENCE-LEDGER.json" not in headline, headline


# ── the positive controls ──────────────────────────────────────────────────

def test_a_genuinely_missing_prevention_still_gets_that_headline(tmp_path):
    """THE POSITIVE CONTROL.

    Without it, deleting the prevention headline outright would satisfy every
    assertion above while destroying the rule this guard exists for.
    """
    rc, out, _ = _run(tmp_path, [{**GOOD, "prevention": None}])
    assert rc == 1, out
    assert PREVENTION in out, out
    assert MALFORMED not in out, (
        "a pure missing-prevention run also printed the malformed headline — "
        "a heading asserting a cause nothing established:\n" + out)


def test_a_clean_ledger_prints_no_error_headline_at_all(tmp_path):
    rc, out, _ = _run(tmp_path, [GOOD])
    assert rc == 0, out
    assert "::error::" not in out, out
    assert "recurrence-ledger-guard: OK" in out, out


def test_a_run_tripping_both_prints_both_headlines(tmp_path):
    """Splitting must not make one cause hide the other."""
    rc, out, _ = _run(
        tmp_path, [GOOD, dict(GOOD), {**GOOD, "id": "RC-BETA", "prevention": None}])
    assert rc == 1, out
    assert MALFORMED in out and PREVENTION in out, out
    assert out.index(MALFORMED) < out.index(PREVENTION), (
        "the print order is fixed so a multi-cause run is stable:\n" + out)
