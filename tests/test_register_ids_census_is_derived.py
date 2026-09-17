"""The census line must not assert "unique" about a register it is failing on.

⚠️ OBSERVED, NOT CONSTRUCTED. On 2026-09-17 `main` carried a duplicate register
id (fixed by PR #12381), and one run of this guard printed BOTH of these:

    docs/claude/health-review-backlog.json::items[id]: 1600 rows, unique
    ::error::A register id collided.
      - …: id 'BL-20260913-A-MISSPELLED-…' appears at rows 1596 AND 1598.

The word `unique` was a LITERAL in five report lines, printed regardless of
what `check_uniqueness` returned. The census is the part a reader skims, and it
asserted the property the guard was simultaneously failing on — UNPROVENANCED
DIAGNOSTIC OUTPUT sub-class A, a label naming a quantity no code path
established for that file.

⚠️ AND THIS FILE HAD ALREADY FIXED THE SIBLING. `_rows`'s docstring records
that an absent array once reported `"0 rows, unique"` and calls it the
collapsed-state defect the repo keeps a CI guard for. That repair made the ROW
COUNT honest and left the word `unique` a literal, so the same sentence went on
lying about the other half. The fix reached the instance, not the class.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "ci" / "check_register_ids.py"


def _load():
    spec = importlib.util.spec_from_file_location("_register_ids_census", GUARD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _tree(tmp_path, rows):
    """A tree carrying ONE declared register, with `rows` in it."""
    reg = M.REGISTERS[0]
    p = tmp_path / reg.path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({reg.array: rows}), encoding="utf-8")
    return tmp_path


def _line_for(report, reg_label):
    hits = [line for line in report if reg_label in line]
    assert len(hits) == 1, (reg_label, hits)
    return hits[0]


# ── the defect ─────────────────────────────────────────────────────────────

def test_a_colliding_register_is_NOT_censused_as_unique(tmp_path):
    reg = M.REGISTERS[0]
    _tree(tmp_path, [{"id": "BL-20260101-SAME"}, {"id": "BL-20260101-SAME"}])
    problems, report, _ = M.check(tmp_path, None)

    assert problems, "the collision must still be reported as a problem"
    line = _line_for(report, reg.label)
    assert "unique" not in line.split("NOT unique")[0], line
    assert "UNIQUENESS PROBLEM" in line, line
    # …and it must point the reader at where the detail is, not just negate.
    assert "error block below" in line, line


def test_the_census_counts_the_problems_it_found(tmp_path):
    reg = M.REGISTERS[0]
    _tree(tmp_path, [{"id": "A-1"}, {"id": "A-1"}, {"id": "B-2"}, {"id": "B-2"}])
    _, report, _ = M.check(tmp_path, None)
    assert "2 UNIQUENESS PROBLEM(S)" in _line_for(report, reg.label)


# ── the control: it must still say `unique` when it IS ─────────────────────

def test_a_clean_register_is_still_censused_as_unique(tmp_path):
    """THE POSITIVE CONTROL.

    Without it, deleting the word `unique` everywhere would pass every test
    above — and the census would stop being able to report a clean register,
    which is the opposite failure and just as useless.
    """
    reg = M.REGISTERS[0]
    _tree(tmp_path, [{"id": "BL-20260101-ONE"}, {"id": "BL-20260101-TWO"}])
    problems, report, _ = M.check(tmp_path, None)
    line = _line_for(report, reg.label)
    assert not problems, problems
    assert "unique" in line and "UNIQUENESS PROBLEM" not in line, line
    assert "2 rows" in line, line


def test_the_row_count_is_still_reported_in_both_cases(tmp_path):
    """The sibling half — the count — must not regress while fixing the word."""
    reg = M.REGISTERS[0]
    _tree(tmp_path, [{"id": "X-1"}, {"id": "X-1"}, {"id": "Y-2"}])
    _, report, _ = M.check(tmp_path, None)
    assert "3 rows" in _line_for(report, reg.label)


# ── the verdict must be derived, not re-asserted at each site ──────────────

def test_no_census_line_hardcodes_the_word_unique(tmp_path):
    """Stops the literal coming back at ONE of the five sites.

    The defect was the same word repeated across five report lines; repairing
    four of them would look fixed and still lie on whichever path the run takes
    (the `--base` variants are the ones a CI run actually reaches).

    ⚠️ IT KEYS ON THE TEMPLATE, NOT ON THE WORDS. My first version matched the
    substring `"rows, unique"` anywhere and flagged three lines that are not
    census lines at all: `_rows`'s docstring quoting the historical bad output,
    the module's own self-test asserting `"0 rows, unique"` for an EMPTY array
    (which is correct — an empty array is trivially unique), and the comment I
    had just written explaining this very defect. Explaining the bug re-created
    a finding about it, the same shape as PR #12378's docstring. The census
    template is `{len(head_rows)} rows, …`, so keying on that cannot match
    prose, and it would have caught the original defect at all five sites.
    """
    src = GUARD.read_text(encoding="utf-8")
    needle = "{len(head_rows)} rows, unique"
    offenders = [
        f"{n}: {line.strip()[:100]}"
        for n, line in enumerate(src.splitlines(), 1)
        if needle in line
    ]
    assert not offenders, (
        "a census line still hardcodes `unique` instead of using the derived "
        "verdict:\n  " + "\n  ".join(offenders))


def test_that_scan_would_have_caught_the_original_defect(tmp_path):
    """A VACUITY CONTROL: the scan above must be able to go red.

    A structural check that matches nothing is indistinguishable from a clean
    tree. This reconstructs the pre-fix template and asserts the needle finds
    it, so `no offenders` means `looked and found none` rather than `looked for
    something that cannot occur`.
    """
    pre_fix = '            report.append(f"  {reg.label}: {len(head_rows)} rows, unique{junk} "'
    assert "{len(head_rows)} rows, unique" in pre_fix
