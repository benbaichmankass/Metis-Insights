"""`run_guards` must not report a green over work it never selected.

WHY THIS EXISTS. Guard relevance is computed from a COMMIT RANGE
(`changed_files` diffs `origin/<base>...HEAD`), so **uncommitted work is
invisible to it**. Every guard gated on those paths is skipped, and the run
still prints *"All relevant guards passed."*

That is the same green-that-checked-nothing `changed_files` already refuses in
its error branch — reached by a different route. There the diff FAILS; here it
SUCCEEDS and is simply answering a question about *commits* when the developer
asked about their *tree*.

Measured 2026-08-13: five status flips staged in
`docs/research/exit-refinement-coverage.json`, `exit-coverage-matrix-guard`
SKIPPED, summary green. The guard only ran once the work was committed — so the
local pre-commit run, which is exactly when a developer wants the check, was the
one run that did not perform it.

THE REGISTRY IS IMPORTED, NOT RESTATED. A hand-copied glob here would be a
second definition free to drift from the real one, and then this test would pass
while the guard it names no longer watches that path.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import run_guards  # noqa: E402


def unchecked_for(dirty):
    """The fix's own selection logic, over the REAL guard registry.

    Mirrors `main()`: a guard is under-checked when it was skipped for
    relevance yet a dirty path would have selected it.
    """
    return sorted({g["name"] for g in run_guards.GUARDS
                   if not run_guards.is_relevant(g["when"], [])
                   and run_guards.is_relevant(g["when"], dirty)})


def test_worktree_files_finds_a_positive(tmp_path):
    """A probe that cannot find a dirty file proves nothing by finding none."""
    untracked = REPO / "_guards_probe_delete_me.txt"
    untracked.write_text("probe\n", encoding="utf-8")
    try:
        found = run_guards.worktree_files()
        assert "_guards_probe_delete_me.txt" in found, found
    finally:
        untracked.unlink(missing_ok=True)


def test_clean_tree_reports_nothing_unchecked():
    """The CI case. A clean tree must not produce a warning — a check that
    cries wolf on every run gets tuned out, which is the failure mode the
    desensitized-alarm rule names."""
    assert unchecked_for([]) == []


def test_dirty_matrix_would_have_selected_its_guard():
    """The measured incident, as a test.

    `exit-coverage-matrix-guard` watches the coverage matrix. Dirtying that
    file must mark it under-checked.
    """
    dirty = ["docs/research/exit-refinement-coverage.json"]
    assert "exit-coverage-matrix-guard" in unchecked_for(dirty)


@pytest.mark.parametrize("path,guard", [
    ("docs/research/exit-refinement-coverage.json", "exit-coverage-matrix-guard"),
    ("docs/claude/health-review-backlog.json", "canonical-doc-coherence"),
    ("src/runtime/provenance.py", "provenance-consumer-guard"),
])
def test_representative_paths_map_to_their_guards(path, guard):
    """Three paths from three different guards, so the check is not accidentally
    coupled to one registry entry. The first two are files this session edited
    uncommitted; the third is a `src/` path, to cover a code guard as well as
    two doc ones.

    Pairs are READ FROM THE REGISTRY, not remembered. The first draft of this
    test asserted `artifact-validity-guard` for the backlog and failed — that
    guard's `when` is `None`, so it ALWAYS runs and can never be under-selected.
    Guessing the mapping is exactly the mistake this file's docstring warns
    about one level up.
    """
    got = unchecked_for([path])
    assert guard in got, f"{path} -> {got}"


def test_an_always_on_guard_is_never_reported_unchecked():
    """A guard with `when: None` runs unconditionally, so it can never be
    under-selected — and reporting it would be a false alarm that trains the
    reader to ignore the whole block."""
    always_on = [g["name"] for g in run_guards.GUARDS if g["when"] is None]
    assert always_on, "registry has no always-on guard — this test is vacuous"
    reported = unchecked_for(["docs/research/exit-refinement-coverage.json",
                              "src/runtime/provenance.py"])
    assert not (set(always_on) & set(reported)), sorted(set(always_on) & set(reported))


def test_the_check_can_fail():
    """A guard that cannot fail proves nothing about the code it guards.

    A path no guard watches must produce an EMPTY result — otherwise
    `unchecked_for` is returning everything and the assertions above pass
    vacuously.
    """
    assert unchecked_for(["some/path/no/guard/watches.xyz"]) == []


# --------------------------------------------------------------------------
# The SECOND route to the same false green: --only names a guard, relevance
# then skips it. Nothing is dirty and no commit is missing — the caller asked
# for that guard BY NAME and it did not run, while the summary read
# "All relevant guards passed" and exit 0.
#
# These run the script end-to-end rather than unit-testing a set intersection,
# because the defect was never in the set logic — it was in what the OUTPUT
# claimed. A test of the intersection would have passed against the broken
# version.
# --------------------------------------------------------------------------

def _run(*args):
    import subprocess
    return subprocess.run(
        [sys.executable, "scripts/ci/run_guards.py", "--base", "main", *args],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )


def test_only_a_skipped_guard_does_not_claim_a_clean_bill():
    """`--only X` on an event with no diff to scope by runs ZERO guards."""
    p = _run("--event-name", "push", "--only", "exit-coverage-matrix-guard")
    assert "exit-coverage-matrix-guard" in p.stdout
    assert "YOU ASKED FOR THESE BY NAME AND THEY DID NOT RUN" in p.stdout, p.stdout[-800:]
    # The exact string that misled. It must not appear unqualified.
    assert "\nAll relevant guards passed." not in p.stdout, p.stdout[-800:]
    assert "NOT a clean bill of health" in p.stdout


def test_only_a_guard_that_actually_runs_stays_quiet():
    """The control. A warning that fires when the guard DID run would be a
    false alarm, and an alarm that always fires gets walked past — which is
    the failure mode the desensitized-alarm rule exists to kill."""
    p = _run("--all", "--only", "exit-coverage-matrix-guard")
    assert "YOU ASKED FOR THESE BY NAME" not in p.stdout, p.stdout[-800:]
    assert "All relevant guards passed." in p.stdout, p.stdout[-800:]
    assert p.returncode == 0


# --------------------------------------------------------------------------
# The THIRD route to the same false green, and the one the footer did not
# close: the HEADLINE. BL-20260903-RUN-GUARDS-PRINTS-FAIL-0-ON-A-RUN-IT-KNOWS-
# WAS-INCOMPLETE.
#
# The caveat block asserted above landed 2026-08-13 (#8948). The row was filed
# 2026-09-03 — THREE WEEKS LATER, by an author reading that very output — and
# reproduced on origin/main on 2026-09-13: a tree with one committed change
# and one uncommitted file printed
#
#     PASS 67 · FAIL 1 · COULD-NOT-RUN 0 · SKIP 40
#     ...
#     NOT SELECTED because the work is UNCOMMITTED (1) — ...
#
# The count of guards the script KNEW it had not graded appears nowhere on the
# line a reader scans. `counts_line` is a pure function precisely so what the
# headline CLAIMS is arguable here rather than only against an 8-minute run.
# --------------------------------------------------------------------------

def test_the_headline_names_what_was_not_graded():
    line = run_guards.counts_line(47, 0, 0, 38, 17)
    assert "17 NOT GRADED (uncommitted)" in line, line
    # It must be findable by a reader scanning ONE line, which is the whole
    # point — a footer under a 38-name skip list was already there and did not
    # close this.
    assert line.startswith("PASS 47 · FAIL 0"), line


def test_a_COMPLETE_run_headline_is_byte_identical_to_before():
    """THE POSITIVE CONTROL, and it is the one that matters here.

    A qualifier that renders on every run is a decoration a reader learns to
    skip — the desensitised-alarm shape this repo calls its own worst failure
    mode. The clean-run line must be exactly what it has always been.
    """
    assert (run_guards.counts_line(64, 0, 0, 38)
            == "PASS 64 · FAIL 0 · COULD-NOT-RUN 0 · SKIP 38")
    assert "NOT GRADED" not in run_guards.counts_line(64, 0, 0, 38)


def test_the_two_headlines_are_DISTINGUISHABLE():
    """The row's own criterion, stated as one assertion.

    Same pass/fail/skip counts, different completeness — the lines must differ.
    """
    complete = run_guards.counts_line(47, 0, 0, 38, 0)
    incomplete = run_guards.counts_line(47, 0, 0, 38, 17)
    assert complete != incomplete, (complete, incomplete)


def test_the_not_graded_count_is_not_folded_into_the_other_buckets():
    """`unchecked` is a SUBSET of `skipped`, not a fifth bucket.

    If a fix added it to SKIP as well, the counts would stop summing to the
    guards considered and the line would double-count. Pin the buckets.
    """
    line = run_guards.counts_line(47, 0, 0, 38, 17)
    assert "SKIP 38" in line, line
    assert "SKIP 55" not in line, line


def test_main_renders_the_headline_through_this_function():
    """Otherwise the pure function is a decoration nothing prints.

    A fix that added `counts_line` and left `main` formatting its own string
    would pass every assertion above while the real run stayed unchanged —
    the shape the register-parse fixes this session kept meeting.
    """
    src = (REPO / "scripts" / "ci" / "run_guards.py").read_text(encoding="utf-8")
    assert "print(counts_line(" in src, (
        "main() must print through counts_line(); a second inline f-string "
        "would drift from the function these tests grade")
    # EXACTLY ONE author of this line: the one inside `counts_line` itself.
    # Two would drift, and the second would be the one `main` actually prints.
    assert src.count('f"PASS {') == 1, (
        f"expected exactly one PASS-line f-string (the one inside "
        f"counts_line); found {src.count(chr(102) + chr(34) + 'PASS {')}")
