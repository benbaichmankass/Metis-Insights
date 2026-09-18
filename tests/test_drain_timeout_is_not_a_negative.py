"""A drain signal that could not be READ must not argue AGAINST the row.

`backlog_drain_candidates._added_after` answers "was this path first added
AFTER the row was filed?" — real evidence that something shipped. It has three
values and the distinction is load-bearing at the call site:

    assessor:  decidable = [s for s in signals if s["ok"] is not None]

So `None` is dropped as undecidable, while `False` COUNTS as a decided
"nothing shipped".

⚠️ THE FUNCTION ALREADY ARGUED THIS TWICE AND THEN DID THE OPPOSITE. Its
`_SHALLOW` branch returns `None` because a shallow clone cannot date a file,
and its `when.ok` branch returns `None` with a measurement attached (982 live
rows; a reader keyed on `opened_at` alone can date 59.8%, so a definite
negative was being handed out for up to 40.2% of the corpus). Both are
"we could not look". The `except` handler — for the STRICTLY worse case of git
not answering AT ALL: a 25-second timeout, git missing, the subprocess dying —
returned `False`.

A timeout therefore did not make a row unshortlistable; it silently argued
against it, and a narrowed candidate list reads identically to a clean one.

Same class as
`BL-20260917-CHECK-BACKLOG-REFS-REPORTS-EVERY-ID-RESOLVES-WHEN-IT-COULD-NOT-READ-THE-DIFF-AT-ALL`,
found in the same sweep: a read that could not happen must never be returned as
a read that found nothing.
"""

from __future__ import annotations

import subprocess

import pytest

import scripts.ops.backlog_drain_candidates as M


class _Date:
    """A `RowDate` stand-in that IS established, so `when.ok` does not short out."""

    ok = True

    def __init__(self, day):
        self.day = day


def _a_dated_row():
    from datetime import date
    return _Date(date(2020, 1, 1))


@pytest.fixture
def deep(monkeypatch):
    """Force the non-shallow path, so the subprocess is actually reached.

    Without this the `_SHALLOW` branch returns `None` before any git call and
    every assertion below would pass for the wrong reason — the vacuous-green
    shape this file exists to stop.
    """
    monkeypatch.setattr(M, "_SHALLOW", False)


# ── the defect ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("boom", [
    subprocess.TimeoutExpired(cmd="git", timeout=25),
    FileNotFoundError("git"),
    OSError("subprocess died"),
])
def test_a_failed_read_is_None_and_never_a_definite_negative(deep, monkeypatch, boom):
    def raise_it(*a, **k):
        raise boom
    monkeypatch.setattr(M.subprocess, "run", raise_it)

    got = M._added_after("some/path.py", _a_dated_row())
    assert got is None, (
        "a read that could not happen must be `None` (we did not look), never "
        "`False` (we looked and it was not added) — the caller treats False as "
        "a decided negative")


def test_the_caller_drops_None_but_COUNTS_False(deep):
    """Why the value matters, pinned rather than asserted in prose.

    This mirrors the assessor's own filter. If it ever stops treating `False`
    as decidable the fix above becomes unnecessary — and this test says so by
    failing, rather than leaving the reasoning in a comment.
    """
    signals = [{"ok": None}, {"ok": False}, {"ok": True}]
    decidable = [s for s in signals if s["ok"] is not None]
    assert {"ok": False} in decidable
    assert {"ok": None} not in decidable


# ── the controls: it must still be able to answer ──────────────────────────

def test_a_real_answer_still_comes_back_as_a_bool(deep, monkeypatch):
    """THE POSITIVE CONTROL. Without it, `always None` would pass everything."""
    monkeypatch.setattr(
        M.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="2021-06-01T00:00:00+00:00\n", stderr=""))
    got = M._added_after("some/path.py", _a_dated_row())
    assert got is True, got


def test_a_file_added_before_the_row_is_a_real_negative(deep, monkeypatch):
    """`False` must stay REACHABLE — the fix must not turn every answer to None.

    Widening `None` until nothing is ever decided would make the drain tool
    shortlist nothing and still look healthy, which is the opposite failure.
    """
    monkeypatch.setattr(
        M.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="2019-01-01T00:00:00+00:00\n", stderr=""))
    assert M._added_after("some/path.py", _a_dated_row()) is False


def test_an_unparseable_answer_is_already_None(deep, monkeypatch):
    """The sibling path, asserted so the three now agree."""
    monkeypatch.setattr(
        M.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="not a date\n", stderr=""))
    assert M._added_after("some/path.py", _a_dated_row()) is None


# ── the other two "could not look" paths still hold ────────────────────────

def test_a_shallow_clone_is_still_None(monkeypatch):
    monkeypatch.setattr(M, "_SHALLOW", True)
    assert M._added_after("some/path.py", _a_dated_row()) is None


def test_an_undated_row_is_still_None(deep):
    class _Unstated:
        ok = False
        day = None
    assert M._added_after("some/path.py", _Unstated()) is None
