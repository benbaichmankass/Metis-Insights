"""Tests for the ci-settled relay's grader (scripts/ops/ci_settle.py).

The grader is the whole safety argument for the relay: a session acts on its
verdict WITHOUT taking a second look, so a state that quietly means two things
is worse than no relay at all. These tests pin the seven states apart.

`scripts/ops/ci_settle.py --self-test` carries the same assertions, because
pytest is not installed in every container this runs in (the workflow runs the
self-test before trusting the grader).
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ci_settle",
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ops" / "ci_settle.py",
)
ci_settle = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(ci_settle)


OK = {"name": "a", "status": "completed", "conclusion": "success"}
BAD = {"name": "b", "status": "completed", "conclusion": "failure"}
CANCELLED = {"name": "c", "status": "completed", "conclusion": "cancelled"}
RUNNING = {"name": "d", "status": "in_progress", "conclusion": None}
CLEAN = {"mergeable_state": "clean", "head": {"sha": "deadbeef"}}
DIRTY = {"mergeable_state": "dirty", "head": {"sha": "deadbeef"}}


def grade(checks, pr=CLEAN, pr_ok=True, checks_ok=True):
    return ci_settle.summarise(
        pr=pr, pr_read_ok=pr_ok, checks=checks, checks_read_ok=checks_ok
    )


# --- the seven states, each distinguished from the one it could collapse into


def test_green_requires_at_least_one_check():
    assert grade([OK])["state"] == "green"


def test_red_is_decisive_even_while_others_run():
    assert grade([BAD, RUNNING])["state"] == "red"


def test_cancelled_is_not_green():
    """A cancelled check produced NO verdict. This repo runs
    `cancel-in-progress: true` on its required checks, so a superseded push
    leaves exactly this state and grading it green would be a false pass."""
    result = grade([OK, CANCELLED])
    assert result["state"] == "cancelled"
    assert result["state"] != "green"


def test_pending_is_not_settled():
    result = grade([OK, RUNNING])
    assert result["state"] == "pending"
    assert result["settled"] is False


def test_zero_checks_is_never_green():
    """The documented trap: zero check runs renders identically to queued and
    to all-green. CLAUDE.md records two sessions losing ~10 minutes each to it."""
    result = grade([])
    assert result["state"] == "no_checks"
    assert result["settled"] is False


def test_a_dirty_pr_explains_its_own_emptiness():
    """`dirty` must win over `no_checks`: GitHub builds pull_request runs
    against the merge ref, so the conflict is the CAUSE of the emptiness."""
    result = grade([], pr=DIRTY)
    assert result["state"] == "conflict"
    assert "merge conflict" in result["reason"]


def test_unreadable_is_not_no_checks():
    """'We could not look' and 'we looked and found nothing' are opposite
    facts. Counts are None rather than 0 for the same reason -- a zero is a
    real reading."""
    result = grade(None, checks_ok=False)
    assert result["state"] == "unreadable"
    assert result["counts"]["passing"] is None
    assert grade([])["counts"]["passing"] == 0


def test_green_but_unmergeable_says_so():
    assert "dirty" in grade([OK], pr=DIRTY)["reason"]


# --- bucketing and dedupe


@pytest.mark.parametrize(
    "check,expected",
    [
        ({"status": "completed", "conclusion": "success"}, "passing"),
        ({"status": "completed", "conclusion": "neutral"}, "passing"),
        ({"status": "completed", "conclusion": "skipped"}, "passing"),
        ({"status": "completed", "conclusion": "failure"}, "failing"),
        ({"status": "completed", "conclusion": "timed_out"}, "failing"),
        ({"status": "completed", "conclusion": "action_required"}, "failing"),
        ({"status": "completed", "conclusion": "cancelled"}, "cancelled"),
        ({"status": "completed", "conclusion": "stale"}, "cancelled"),
        ({"status": "queued", "conclusion": None}, "running"),
        # completed with no conclusion yet is NOT YET DECIDED, never passing
        ({"status": "completed", "conclusion": None}, "running"),
        ({"status": "completed", "conclusion": "weird_new_value"}, "running"),
    ],
)
def test_bucket_conclusion(check, expected):
    assert ci_settle.bucket_conclusion(check) == expected


def test_dedupe_keeps_the_newest_attempt_per_name():
    """A re-run leaves both attempts on the same head sha. Counting the stale
    one reports a red that has already been re-run green."""
    old = {"name": "x", "status": "completed", "conclusion": "failure", "started_at": "1"}
    new = {"name": "x", "status": "completed", "conclusion": "success", "started_at": "2"}
    assert len(ci_settle.dedupe_checks([old, new])) == 1
    assert grade([old, new])["state"] == "green"
    assert grade([new, old])["state"] == "green"


# --- log excerpt


def test_log_tail_is_bounded_and_keeps_the_error():
    log = "\n".join(["noise"] * 300 + ["FAILED tests/test_x.py::test_y"] + ["tail"] * 4)
    tail = ci_settle.extract_log_tail(log)
    assert len(tail) <= ci_settle.MAX_LOG_LINES
    assert any("FAILED" in line for line in tail)


def test_empty_log_stays_empty():
    """An empty excerpt must not be manufactured into content; `log_state`
    carries whether we read anything."""
    assert ci_settle.extract_log_tail("") == []


# --- the watch loop


class _StubGitHub:
    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.polls = 0

    def pull(self, _number):
        return True, CLEAN

    def check_runs(self, _sha):
        self.polls += 1
        return True, self.sequence.pop(0) if self.sequence else []

    def unresolved_threads(self, _number):
        return {"read_state": "read", "unresolved": 0}


def test_watch_stops_as_soon_as_it_settles():
    stub = _StubGitHub([[RUNNING], [RUNNING], [OK]])
    result = ci_settle.watch(
        stub, 1, timeout_s=999, poll_s=0, with_threads=False, sleeper=lambda _: None
    )
    assert result["state"] == "green"
    assert result["polls"] == 3


def test_watch_timeout_reports_pending_not_green():
    """Hitting the deadline means WE stopped waiting, never that CI stopped."""
    ticks = iter(range(20))
    stub = _StubGitHub([[RUNNING]] * 20)
    result = ci_settle.watch(
        stub, 1, timeout_s=1, poll_s=0, with_threads=False,
        sleeper=lambda _: None, clock=lambda: next(ticks),
    )
    assert result["timed_out_waiting"] is True
    assert result["state"] == "pending"
    assert result["settled"] is False


# --- observe-once: the mode meant to pair with a check_suite.completed wake


def test_observe_once_polls_exactly_once_and_claims_no_timeout():
    """`timeout_minutes: 0` waits for nothing, so it must not report
    `timed_out_waiting` -- that would claim an attempt nobody made."""
    stub = _StubGitHub([[RUNNING], [RUNNING], [OK]])
    result = ci_settle.watch(
        stub, 1, timeout_s=0, poll_s=0, with_threads=False, sleeper=lambda _: None
    )
    assert result["polls"] == 1
    assert result["mode"] == "once"
    assert result["observed_once"] is True
    assert "timed_out_waiting" not in result
    assert result["state"] == "pending"


def test_observe_once_does_not_claim_it_stopped_waiting():
    """The wait path's `pending` reason says the watcher stopped waiting. In
    `once` mode nothing waited, so that sentence would name an action no code
    path took -- UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A. Caught live on
    the first `once` run against PR #10757."""
    stub = _StubGitHub([[RUNNING]])
    result = ci_settle.watch(
        stub, 1, timeout_s=0, poll_s=0, with_threads=False, sleeper=lambda _: None
    )
    assert "stopped waiting" not in result["reason"]
    assert "SINGLE observation" in result["reason"]
    # the count itself is unchanged -- only the claim about how it was obtained
    assert result["counts"]["running"] == 1


def test_wait_mode_still_says_the_watcher_stopped_waiting():
    """The counterpart: in wait mode that sentence is TRUE and must survive."""
    ticks = iter(range(20))
    stub = _StubGitHub([[RUNNING]] * 20)
    result = ci_settle.watch(
        stub, 1, timeout_s=1, poll_s=0, with_threads=False,
        sleeper=lambda _: None, clock=lambda: next(ticks),
    )
    assert "stopped waiting" in result["reason"]


def test_observe_once_still_reports_a_settled_state_when_there_is_one():
    stub = _StubGitHub([[OK]])
    result = ci_settle.watch(
        stub, 1, timeout_s=0, poll_s=0, with_threads=False, sleeper=lambda _: None
    )
    assert result["state"] == "green"
    assert result["settled"] is True
    # It settled rather than gave up, so the give-up marker must be absent.
    assert "observed_once" not in result


def test_the_per_suite_trap_grades_as_pending_not_green():
    """The condition BL-20260821-CHECK-SUITE-EVENT-IS-PER-SUITE-NOT-PER-PR
    describes, and the reason this grader exists alongside the wake.

    Three of this repo's four required checks come back passing from their own
    suites while the fourth is still running. A `check_suite.completed` success
    fires for each finished suite; acting on one merges on a partial required
    set. Reproduced live as run 3 on PR #10757.
    """
    guards = {"name": "guards", "status": "completed", "conclusion": "success"}
    collect = {"name": "pytest-collect", "status": "completed", "conclusion": "success"}
    inventory = {"name": "repo-inventory", "status": "completed", "conclusion": "success"}
    run = {"name": "pytest-run", "status": "in_progress", "conclusion": None}
    result = grade([guards, collect, inventory, run])
    assert result["state"] == "pending"
    assert result["settled"] is False
    assert result["counts"] == {
        "passing": 3, "failing": 0, "cancelled": 0, "running": 1
    }


def test_self_test_entrypoint_passes():
    assert ci_settle.self_test() == 0
