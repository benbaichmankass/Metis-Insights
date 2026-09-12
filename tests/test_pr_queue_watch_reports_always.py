"""The PR-queue escalation channel must not sit downstream of housekeeping.

WHAT THIS PINS
──────────────
`pr-queue-watch.yml` computes an escalation and reports it by FAILING the
`Report the verdict` step -- that failure IS the channel, per the step's own
comment, and `claude-run-failure-alert.yml` carries it to the operator.

Without `if: always()` that step is SKIPPED whenever an earlier step fails. In
practice the earlier step is `Land the receipt on main`, which opens its own PR
and fails when that PR does not merge in time -- i.e. exactly when the queue
this watcher exists to report on is backed up. The escalation is then computed,
printed into the digest, and never reported.

STATE THE POPULATION. Measured 2026-09-12 over the COMPLETE run history of the
workflow -- 38 scheduled runs, 2026-09-02 to 2026-09-12:

    Report the verdict SKIPPED                                 9 of 38
      ... of which the digest carried a TRUE escalation        5
          4 latency escalations (2026-09-08 / 2026-09-09)
          1 conflict escalation (2026-09-12T11:14:26Z),
            naming #11916 and #11919 as GREEN AND UNMERGEABLE

⚠️ The run went red in all five, and that is the trap rather than the
mitigation: a swallowed page and a failed receipt reach the operator as the
same generic workflow failure.

EVIDENCE THIS PROBE CAN GO RED: `test_planted_defect_*` deletes the `always()`
from an in-memory copy and asserts the guard below fails on it, after first
asserting the plant actually landed. `test_negative_control_*` makes an edit
that changes nothing semantic and shows the guard stays quiet.
"""
from __future__ import annotations

import pathlib
import re

import pytest
import yaml

WORKFLOW = (pathlib.Path(__file__).resolve().parents[1]
            / ".github" / "workflows" / "pr-queue-watch.yml")

#: Steps whose whole purpose is to REPORT a verdict. A reporter that can be
#: skipped by an unrelated failure is not a reporter.
REPORTING_STEPS = ("Report the verdict", "Report the BLOCKED-LANE verdict")


def _steps(text: str) -> list[dict]:
    doc = yaml.safe_load(text)
    jobs = doc["jobs"]
    out: list[dict] = []
    for job in jobs.values():
        out += list(job.get("steps") or [])
    return out


def _by_name(text: str, name: str) -> dict:
    for s in _steps(text):
        if s.get("name") == name:
            return s
    raise AssertionError(f"step {name!r} not found — was it renamed?")


def assert_reporters_always(text: str) -> None:
    """The guard. Raises AssertionError when a reporter can be skipped."""
    for name in REPORTING_STEPS:
        step = _by_name(text, name)
        cond = str(step.get("if") or "").strip()
        assert "always()" in cond, (
            f"{name!r} carries `if: {cond or '(none)'}` — it will be SKIPPED "
            f"when any earlier step fails, which is exactly when the queue is "
            f"backed up. Measured: 5 of 38 scheduled runs computed a TRUE "
            f"escalation that was never reported for this reason."
        )


def test_both_verdict_steps_report_unconditionally():
    assert_reporters_always(WORKFLOW.read_text())


def test_the_escalation_step_really_is_downstream_of_a_step_that_can_fail():
    """The premise, asserted rather than assumed.

    If `Land the receipt on main` were moved after the reporters, `always()`
    would stop being load-bearing and this test should be revisited rather
    than silently kept.
    """
    names = [s.get("name") for s in _steps(WORKFLOW.read_text())]
    assert "Land the receipt on main (auto-merge PR)" in names
    assert names.index("Land the receipt on main (auto-merge PR)") < \
        names.index("Report the verdict")


def test_an_absent_assessment_is_not_read_as_a_quiet_queue():
    """`always()` means the step can run with no code at all — *we did not
    look*. That must not fall through to a clean reading."""
    body = _by_name(WORKFLOW.read_text(), "Report the verdict")["run"]
    # ⚠️ MATCH THE CASE ARM, NOT ANY PAIR OF QUOTES. A bare `'""' in body`
    # passes on the `case "${{ ... }}" in` line itself, so deleting the arm
    # left this test GREEN — measured while planting it.
    arm = re.search(r'^\s*""\)', body, re.M)
    assert arm, (
        "no empty-code arm: with `always()` the step can run when the assess "
        "step never recorded a code, and an unhandled empty must not read as "
        "a quiet queue"
    )
    assert "did not look" in body.lower()


def test_failing_the_job_is_still_the_channel():
    """`always()` must not have been paid for by softening the escalation."""
    body = _by_name(WORKFLOW.read_text(), "Report the verdict")["run"]
    assert "exit 1" in body
    assert "PR-QUEUE ESCALATION" in body


# ---------------------------------------------------------------------------
# PLANT + NEGATIVE CONTROL
# ---------------------------------------------------------------------------

def test_planted_defect_removing_always_FAILS_the_guard():
    text = WORKFLOW.read_text()
    assert_reporters_always(text)  # PRE-RED check: clean before planting

    # Anchor on SHAPE, not on comment prose: the verdict step is the only one
    # whose `if: always()` is immediately followed by the digest `cat`.
    marker = "        if: always()\n        run: |\n          cat /tmp/digest.txt\n"
    assert text.count(marker) == 1, (
        f"THE PLANT DID NOT LAND: expected exactly one verdict-step anchor, "
        f"found {text.count(marker)}. This probe is inert — fix it."
    )
    planted = text.replace(
        marker, "        run: |\n          cat /tmp/digest.txt\n", 1)

    assert planted != text, "THE PLANT DID NOT LAND — the text is unchanged."
    assert "always()" not in str(
        _by_name(planted, "Report the verdict").get("if") or ""), \
        "THE PLANT DID NOT LAND — the step still carries always()."

    with pytest.raises(AssertionError, match="SKIPPED"):
        assert_reporters_always(planted)


def test_negative_control_a_semantically_inert_edit_stays_green():
    """An edit that touches the same step without removing the condition must
    not trip the guard — otherwise the probe reds on any change rather than on
    the loss of the property."""
    text = WORKFLOW.read_text()
    inert = text.replace("if: always()", "if: ${{ always() }}")
    assert inert != text, "THE CONTROL DID NOT APPLY — nothing was rewritten."
    assert_reporters_always(inert)
