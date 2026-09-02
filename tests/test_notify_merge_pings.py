"""The per-merge ping — `scripts/notify_on_pull.py` Source 4.

The operator asked three times in one day to be told when a PR merges, and
nothing implemented it: `scripts/ops/work_phase_ping.py` pings on a work
object's `lifecycle` and has no concept of a PR, so a range containing five
merges reported `No pingable events`.

What these assert is the part a passing import cannot: that a merge in the
pulled range PRODUCES a message, that every merge in it is NAMED, that a
truncation SAYS SO, and that an unreadable range is not reported as a quiet day.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("requests")  # notify_on_pull imports it at module scope

_spec = importlib.util.spec_from_file_location(
    "_notify_merge_under_test", ROOT / "scripts" / "notify_on_pull.py"
)
nop = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = nop
_spec.loader.exec_module(nop)


# --- the detector -----------------------------------------------------------

def test_squash_merge_subject_is_the_shape_main_actually_uses():
    # MEASURED 2026-09-02: 500 of the last 500 commits on origin/main.
    got = nop._parse_merge_subject("chore(ops): queue the daily work digest (#10854)")
    assert got == ("10854", "chore(ops): queue the daily work digest")


def test_true_merge_commit_shape_is_handled_defensively():
    got = nop._parse_merge_subject("Merge pull request #123 from user/branch")
    assert got is not None and got[0] == "123"


def test_a_pr_number_that_is_not_at_the_end_is_not_a_merge():
    # "fix #123 in the parser" mentions a PR; it did not land one.
    assert nop._parse_merge_subject("fix (#123) in the parser") is None


def test_a_plain_subject_is_not_a_merge():
    assert nop._parse_merge_subject("wip: local scratch") is None


# --- the message ------------------------------------------------------------

def _pings(monkeypatch, subjects):
    monkeypatch.setattr(nop, "_commit_subjects_or_none", lambda a, b: subjects)
    return nop._merge_pings("aaaaaaaa", "bbbbbbbb")


def test_one_merge_produces_one_message_naming_it(monkeypatch):
    out = _pings(monkeypatch, [("sha1", "feat: a thing (#10801)")])
    assert len(out) == 1
    priority, body = out[0]
    assert priority == "normal"
    assert "1 PR merged to main" in body
    assert "#10801 feat: a thing" in body


def test_every_merge_in_the_window_is_named_not_just_counted(monkeypatch):
    subjects = [(f"sha{i}", f"feat: thing {i} (#{1000 + i})") for i in range(5)]
    _p, body = _pings(monkeypatch, subjects)[0]
    for i in range(5):
        assert f"#{1000 + i}" in body, "a roll-up must not drop a merge"
    assert "5 PRs merged to main" in body


def test_merges_are_listed_oldest_first(monkeypatch):
    # git log is newest-first; the operator reads it as a timeline.
    subjects = [("s2", "second (#2)"), ("s1", "first (#1)")]
    _p, body = _pings(monkeypatch, subjects)[0]
    assert body.index("#1 first") < body.index("#2 second")


def test_truncation_says_so_and_never_silently_drops(monkeypatch):
    n = nop.MERGE_PING_MAX_LISTED + 7
    subjects = [(f"s{i}", f"t{i} (#{i})") for i in range(n)]
    _p, body = _pings(monkeypatch, subjects)[0]
    assert f"{n} PRs merged to main" in body
    assert "and 7 more not listed" in body
    assert "compare" in body, "the overflow must point somewhere that has all of them"


def test_automation_merges_are_counted_not_hidden(monkeypatch):
    subjects = [
        ("s1", "chore(ops): move settled PR rows into settled_prs[] (auto) (#1)"),
        ("s2", "feat: a real change (#2)"),
    ]
    _p, body = _pings(monkeypatch, subjects)[0]
    assert "(1 automated)" in body
    assert "#1 chore(ops)" in body, "an automated merge is still a merge; do not filter"


def test_a_long_subject_is_truncated_but_the_pr_number_survives(monkeypatch):
    long = "x" * 400
    _p, body = _pings(monkeypatch, [("s", f"{long} (#9999)")])[0]
    assert "#9999" in body
    assert max(len(line) for line in body.splitlines()) < 200


# --- the three states -------------------------------------------------------

def test_nothing_merged_produces_no_message(monkeypatch):
    assert _pings(monkeypatch, [("s", "not a merge subject")]) == []


def test_an_unreadable_range_is_not_reported_as_a_quiet_day(monkeypatch, caplog):
    monkeypatch.setattr(nop, "_commit_subjects_or_none", lambda a, b: None)
    with caplog.at_level("WARNING"):
        assert nop._merge_pings("unknown", "bbbbbbbb") == []
    assert any("could not read the commit range" in r.message for r in caplog.records), (
        "'we did not look' must not render identically to 'nothing merged'"
    )


def test_commit_subjects_or_none_distinguishes_unknown_pre():
    # The historical helper collapses this to []; the new one must not.
    assert nop._commit_subjects_or_none("unknown", "HEAD") is None
    assert nop._commit_subjects("unknown", "HEAD") == []


# --- the wiring -------------------------------------------------------------

def test_collect_pings_actually_calls_the_merge_source(monkeypatch):
    """A source nothing calls is indistinguishable from one that never fires."""
    monkeypatch.setattr(nop, "_blocker_pings", lambda a, b: [])
    monkeypatch.setattr(nop, "_training_workflow_pings", lambda a, b: [])
    monkeypatch.setattr(nop, "_drain_pending_pings", lambda *a, **k: [])
    monkeypatch.setattr(nop, "_load_delivered_hashes", lambda p: set())
    monkeypatch.setattr(nop, "_diff_touched_checkpoint_log", lambda a, b: False)
    monkeypatch.setattr(
        nop, "_commit_subjects_or_none", lambda a, b: [("s", "feat: x (#4242)")]
    )
    out = nop.collect_pings("aaaa", "bbbb")
    assert len(out) == 1 and "#4242" in out[0][1]
