"""A shortlist signal must carry the criterion's OWN words about its needle.

WHY THIS EXISTS
---------------
`backlog_drain_candidates.py` shortlists open rows whose criteria may already be
met. Its own docstring says it *"shortlists; it never decides"*, and the
`backlog-drain` skill records that an earlier version *"claimed 108 of 542 and
was almost entirely wrong."*

MEASURED 2026-09-13, a fresh instance of the same class with a mechanism. The
tool graded
`BL-20260912-THE-SWEEP-CORPUS-RECORDS-NO-DISPATCHED-SHA-…` as `likely_met` on
one signal: `path_added_since_filing: scripts/research/m20_corpus_schema_census.py`.
The signal is **literally true** — the file was added after the row was filed —
and it points the **wrong way**, because that row's criterion names the census in
a clause that says in terms *"DO NOT close it on the census alone — the census
REPORTS the ambiguity, it does not remove it."*

Verified against the live corpus the same day: **zero** of 1379 rows in
`docs/research/m20-sweep-corpus.jsonl` carry any sha-ish key (`run_id` is present
on all 1379, which is the positive control), so clause (1) is unmet and clause
(3) cannot be met while it is.

THE FIX IS NOT A CLASSIFIER. Detecting negation lexically would be a confident
wrong answer of its own. The signal simply carries the criterion sentence that
mentions its needle, so an `OK` the criterion itself calls insufficient can no
longer read as evidence for closing.
"""
from __future__ import annotations

from scripts.ops.backlog_drain_candidates import criterion_context

_REAL = (
    "(1) Every NEW corpus row carries the dispatched sha and the Actions run id, "
    "null where genuinely unavailable, verified by reading a row written by a real "
    "dispatched run rather than by a fixture. (2) A test asserts `measurement_key` "
    "is UNCHANGED by the addition, with a negative control that fails if the new "
    "keys are folded into it. (3) `scripts/research/m20_corpus_schema_census.py` "
    "gains a third, AUTHORITATIVE boundary basis that reads the row's own "
    "dispatched sha, and its output distinguishes rows graded from the sha from "
    "rows still graded from the two guessed boundaries. ⚠️ DO NOT close this by "
    "stamping the existing 20 rows."
)


def test_the_context_is_the_clause_that_mentions_the_needle():
    """The motivating case: the signal must surface clause (3), not silence."""
    out = criterion_context(_REAL, "scripts/research/m20_corpus_schema_census.py")
    assert "(3)" in out
    assert "AUTHORITATIVE boundary basis" in out


def test_the_context_does_not_return_an_unrelated_clause():
    """NEGATIVE CONTROL. Without this, returning the whole criterion — or clause
    (1) — would satisfy the test above by accident and teach nothing."""
    out = criterion_context(_REAL, "scripts/research/m20_corpus_schema_census.py")
    assert "(1) Every NEW corpus row" not in out
    assert "(2) A test asserts" not in out


def test_a_needle_that_is_absent_yields_nothing():
    """ABSENCE CONTROL: no context is not empty context dressed as a finding."""
    assert criterion_context(_REAL, "scripts/ops/nope.py") == ""
    assert criterion_context("", "anything") == ""


def test_a_dot_inside_a_path_does_not_split_the_sentence():
    """Every path in these criteria contains a `.`, so splitting on `.` alone
    would cut the needle in half and match nothing. This is the bug the splitter
    is written to avoid, asserted rather than assumed."""
    crit = "Some prose. The fix lands in scripts/ops/thing.py and nothing else. More prose."
    out = criterion_context(crit, "scripts/ops/thing.py")
    assert "scripts/ops/thing.py" in out
    assert "and nothing else" in out


def test_the_context_is_bounded():
    """A signal line that dumped a 4,000-character criterion is the wall this
    repo calls its own worst failure mode, one level down."""
    crit = "x" * 50 + " needle/path.py " + "y" * 4000
    out = criterion_context(crit, "needle/path.py", width=120)
    assert len(out) <= 120
    assert out.endswith("…")


def test_every_signal_the_assessor_emits_carries_a_context_key():
    """The key must exist on BOTH signal kinds, or one silently keeps the old
    bare-`OK` rendering that caused this.

    ⚠️ THE `kinds == {...}` ASSERTION IS LOAD-BEARING, NOT DECORATION. My first
    version of this test omitted it and iterated whatever signals came back —
    and the fixture, whose path was NOT backticked, produced only the
    `diag_log_name` signal. `_PATH` requires backticks. So the test passed with
    the context key deleted from the path branch: a mutation control caught it,
    which is the only reason it is written this way now. A test that iterates a
    collection must pin what is IN the collection.
    """
    import scripts.ops.backlog_drain_candidates as m

    row = {
        "id": "BL-20260101-X",
        "opened_at": "2026-01-01T00:00:00+00:00",
        "resolution_criteria": (
            "read /api/diag/log_file?name=some_soak_state and also "
            "`scripts/ops/backlog_drain_candidates.py` must exist"
        ),
    }
    out = m.assess(row, diag_src='"some_soak_state"')
    kinds = {s["kind"] for s in out["signals"]}
    assert kinds == {"diag_log_name", "path_added_since_filing"}, (
        f"the fixture stopped exercising both branches: {kinds}"
    )
    for s in out["signals"]:
        assert "context" in s, f"{s['kind']} lost its context key"
        assert s["context"], f"{s['kind']} carries an EMPTY context"
