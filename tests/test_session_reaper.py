"""Tests for the session reaper — the external recorder of what a dead session
left behind (`WO-20260901-PHASE-E` mechanism 3).

⚠️ A PASSING TEST DOES NOT SATISFY PHASE E AND THESE TESTS DO NOT CLAIM TO.
The object's done-condition is explicit: *"Killing a session mid-work loses
nothing, DEMONSTRATED by killing one. Not asserted from a test."* What these
pin is the GRADING POLICY — that the reaper cannot quietly collapse a state it
must keep apart. The demonstration is a separate artifact.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "session_reaper", REPO / "scripts/ops/session_reaper.py")
reaper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reaper)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
FRESH = (NOW - timedelta(minutes=5)).isoformat()
OLD = (NOW - timedelta(minutes=600)).isoformat()

ORIGIN = {
    "heads": {"claude/live": "a", "claude/quiet": "b", "claude/stray": "c"},
    "merged_prs": {"10654"},
    "head_count": 3,
    "remote_read": True,
}


def g(row, **kw):
    return reaper.grade_row(row, kw.pop("origin", ORIGIN), NOW, **kw)


def test_self_test_passes():
    """The in-module self-test is what CI runs before a live reap."""
    assert reaper._self_test() == 0


def test_fresh_branch_is_active():
    assert g({"session_id": "s", "branches": ["claude/live"]},
             commit_times={"claude/live": FRESH})["state"] == "active"


def test_quiet_branch_says_the_work_is_safe_not_lost():
    """The whole point of the kill test: a stalled session's work is ON ORIGIN.

    `stalled_with_work` must never read as loss — it is the SUCCESS state of a
    kill, and a reaper that phrased it as a failure would train a reader to
    treat a recoverable session as a lost one.
    """
    v = g({"session_id": "s", "branches": ["claude/quiet"]},
          commit_times={"claude/quiet": OLD})
    assert v["state"] == "stalled_with_work"
    assert "not been lost" in v["why"]


def test_landed_requires_a_merged_pr_not_merely_a_missing_branch():
    """An absent branch alone is ambiguous; only a merged PR resolves it."""
    assert g({"session_id": "s", "branches": ["claude/gone"],
              "prs": ["Metis-Insights#10654"]})["state"] == "landed"
    assert g({"session_id": "s", "branches": ["claude/gone"],
              "prs": ["Metis-Insights#99999"]})["state"] == "no_landing_evidence"


def test_no_landing_evidence_is_never_phrased_as_lost():
    """⚠️ The registry records PRs BY HAND. A row whose work landed under a PR
    nobody wrote down grades here too, so this state cannot claim loss.
    Measured on the live registry: at least one of the 7 rows in this state
    (`claude/two-way-telegram-decisions`) has work that DID land, as #10789.
    """
    v = g({"session_id": "s", "branches": ["claude/gone"]})
    assert v["state"] == "no_landing_evidence"
    assert "NOT a claim the work was lost" in v["why"]
    assert "lost" not in v["state"]


@pytest.mark.parametrize("branches,why", [
    (["claude/x (expected; not yet pushed)"], "prose in a branches array"),
    (["(rotating — one PR head at a time)"], "prose in a branches array"),
    ([], "no branch named at all"),
    (["ict-trader-dashboard:claude/spa"], "another repo's branch"),
])
def test_ungradeable_rows_are_unreadable_never_a_pass(branches, why):
    """*We could not look* must never render as a clean grade. All four of these
    shapes occur in the live registry."""
    assert g({"session_id": "s", "branches": branches})["state"] == "unreadable", why


def test_an_unreadable_origin_grades_unreadable_not_mass_loss():
    """A failed `ls-remote` must not report every session as having lost its work.

    This is the `silent-empty-guard` shape on the consumer side: an empty read
    treated as a confident negative.
    """
    dead = {**ORIGIN, "remote_read": False, "heads": {}}
    assert g({"session_id": "s", "branches": ["claude/live"]},
             origin=dead)["state"] == "unreadable"


def test_undateable_branch_never_grades_active():
    """A branch we hold but cannot date is `stalled_with_work` — the honest
    floor. Grading it `active` would assert an observation nobody made."""
    assert g({"session_id": "s", "branches": ["claude/live"]},
             commit_times={"claude/live": None})["state"] == "stalled_with_work"


# ------------------------------------------------------------------ recovery


def test_a_row_that_recorded_no_branch_is_still_graded_on_its_real_work():
    """The registry is a spawn-time record nothing updates: 35 of 67 live rows
    name NO branch. The `Claude-Session:` commit trailer recovers 16 of them."""
    v = g({"session_id": "s9", "branches": []},
          commit_times={"claude/quiet": OLD},
          attributed={"s9": ["claude/quiet"]})
    assert v["state"] == "stalled_with_work"
    assert v["branch_source"] == "recovered_from_commit_trailer"
    assert v["recovered_branches"] == ["claude/quiet"]


def test_recovery_is_a_separate_axis_so_the_registrys_failure_stays_visible():
    """⚠️ If recovery were folded into `state`, a healthy-looking run would hide
    the registry failing on half its rows. Two axes, never one."""
    assert set(reaper.BRANCH_SOURCE_STATES) == {
        "registry_declared", "recovered_from_commit_trailer", "none_found"}
    assert g({"session_id": "sB", "branches": ["claude/live"]},
             commit_times={"claude/live": FRESH},
             attributed={"sB": ["claude/stray"]})["branch_source"] == "registry_declared"
    assert g({"session_id": "sA", "branches": []},
             attributed={})["branch_source"] == "none_found"


# ------------------------------------------------------------------ coverage


def test_coverage_declares_what_the_reaper_cannot_see():
    """A reaper whose blind spots are unstated is worse than none — a successor
    reads its clean report as coverage."""
    cov = reaper.coverage([{"session_id": "s", "branches": ["claude/live"]}], ORIGIN)
    assert cov["unpushed_work"] == "invisible_by_construction"
    assert cov["liveness"] == "not_observed"
    # An unregistered branch is COUNTED...
    assert cov["unregistered_claude_branches"] == 2
    # ...and deliberately not turned into a ratio over a population the module
    # cannot define.
    assert "ratio" in cov["unregistered_caveat"]
    assert not any(k.endswith("_pct") or k.endswith("_ratio") for k in cov)


def test_states_are_declared_once_so_a_caller_can_branch_exhaustively():
    assert set(reaper.REAPER_STATES) == {
        "active", "stalled_with_work", "landed", "no_landing_evidence", "unreadable"}


def test_an_unregistered_sessions_work_is_still_attributable_to_a_session_id():
    """⚠️ THE CASE A REGISTRY-KEYED REAPER MISSES EXACTLY WHEN IT MATTERS.

    `SESSIONS.json` has been measured incomplete twice (3 of 6 absent on
    2026-09-01; 26 of 55 on 2026-09-02, 17 carrying the manager's own id as
    parent). Reading the commit trailer instead of the registry is what turns
    "184 branches nobody claims" into "this named session left this work".

    This is the shape the MI-70 kill demonstrated live: the killed subject
    (`session_01NN97cVYW5dmiNNXHfsu7Nn`) was deliberately never registered, and
    the reaper located its branch by session id 62 s after the kill.
    """
    cov = reaper.coverage(
        [{"session_id": "registered", "branches": ["claude/live"]}],
        ORIGIN,
        unregistered_owners={"session_01DEAD": ["claude/stray"]},
    )
    assert cov["unregistered_but_attributable_sessions"] == 1
    assert cov["unregistered_owner_map"]["session_01DEAD"] == ["claude/stray"]


def test_attribution_does_not_claim_to_know_what_the_work_was_for():
    """`owns_object` lives only in the registry. Recovery locates the work and
    leaves it unattached to any work object — stated, not glossed."""
    cov = reaper.coverage([], ORIGIN, unregistered_owners={"s": ["claude/stray"]})
    assert "never WHAT it was for" in cov["unregistered_attribution_caveat"]
    assert "not a substitute for registering" in cov["unregistered_attribution_caveat"]


def test_zero_attributable_is_not_reported_as_zero_unregistered():
    """A branch whose commits carry no trailer is still COUNTED as unregistered.

    Collapsing "we could not attribute it" into "there is nothing there" is the
    silent-empty shape on the consumer side.
    """
    cov = reaper.coverage([], ORIGIN, unregistered_owners={})
    assert cov["unregistered_claude_branches"] == 3
    assert cov["unregistered_but_attributable_sessions"] == 0
