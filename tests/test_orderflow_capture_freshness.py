"""Tests for the trainer order-flow capture freshness alarm.

⚠️ READ THIS BEFORE TREATING A GREEN RUN HERE AS EVIDENCE THE ALARM WORKS.
A harness is not the fleet. These tests pin the GRADING POLICY — that the four
states stay separate and that the alarm fires on a stale reading. What they
cannot reach is the transport: whether the SSH observation actually arrives,
whether the path on the trainer is the one the capture writes, and whether the
plant/grade round-trip survives the real host. That is what
`.github/workflows/trainer-capture-watch.yml`'s planted controls are for, and it
is why `clears_when` on OI-20260829 demands the alarm be SHOWN FIRING rather
than merely deployed.
"""

import json

import pytest

from scripts.ops.orderflow_capture_freshness import (
    ABSENT,
    ALERTING_STATES,
    FRESH,
    STALE,
    UNREADABLE,
    grade,
    grade_observations,
    main,
    self_test_controls,
)

NOW = 1788374468  # 2026-09-02T18:41:08Z — the reference clock of relay #10837
THRESHOLD = 1800


def _grade(read_state, mtime, now=NOW, threshold=THRESHOLD):
    return grade(read_state, mtime, now, threshold)


# ---------------------------------------------------------------- four states


def test_the_real_2026_09_02_observation_grades_fresh():
    """The actual measured reading: mtime 1788374401, i.e. 67s old."""
    state, age = _grade("read", 1788374401)
    assert (state, age) == (FRESH, 67)


def test_a_stale_capture_grades_stale():
    state, age = _grade("read", NOW - 3600)
    assert state == STALE
    assert age == 3600


def test_absent_is_its_own_state_not_stale():
    """`absent` and `stale` are different facts and must not be collapsed.

    A missing file may mean the path moved (a rename, a re-provision), which is
    a different repair from a wedged writer.
    """
    assert _grade("absent", None)[0] == ABSENT


@pytest.mark.parametrize("read_state", ["error", "", None, "timeout", "read "])
def test_we_could_not_look_is_unreadable_never_fresh(read_state):
    """THE LOAD-BEARING ONE.

    Collapsing `unreadable` into `fresh` makes a broken watcher indistinguishable
    from a healthy capture — the exact state the open item was filed for.
    """
    assert _grade(read_state, None)[0] == UNREADABLE


def test_a_read_claim_with_no_mtime_is_unreadable():
    """An observer claiming `read` and supplying no mtime has not given a reading.

    Trusting the claim over the missing evidence is how a broken observer reports
    green.
    """
    assert _grade("read", None)[0] == UNREADABLE


def test_a_read_claim_with_a_junk_mtime_is_unreadable():
    assert _grade("read", "not-a-number")[0] == UNREADABLE


def test_unreadable_alerts_rather_than_passing_silently():
    assert UNREADABLE in ALERTING_STATES
    assert ABSENT in ALERTING_STATES
    assert STALE in ALERTING_STATES
    assert FRESH not in ALERTING_STATES


# ------------------------------------------------------------------ boundary


def test_exactly_at_the_threshold_is_fresh_and_one_second_past_is_stale():
    assert _grade("read", NOW - THRESHOLD)[0] == FRESH
    assert _grade("read", NOW - THRESHOLD - 1)[0] == STALE


def test_future_mtime_is_not_silently_clamped():
    """Clock skew must stay visible rather than rendering as a perfect zero age."""
    state, age = _grade("read", NOW + 500)
    assert state == FRESH  # it is genuinely not stale
    assert age == -500  # ...but the caller can see something is off


def test_the_largest_historical_gap_would_have_fired():
    """2026-07-05T03:30Z -> 06:10Z, 9600s (31 bars), measured in relay #10838.

    The stream HAS stalled before, repeatedly and unobserved. A threshold that
    would not have caught the worst recorded stall would be decorative.
    """
    assert _grade("read", NOW - 9600)[0] == STALE


# ------------------------------------------------------------ planted controls


def _payload(stale_age=10800, fresh_age=8, capture_age=67):
    return {
        "now_epoch": NOW,
        "targets": [
            {"name": "capture", "role": "watched", "path": "/c",
             "read_state": "read", "mtime_epoch": NOW - capture_age},
            {"name": "control_stale", "role": "control", "path": "/s",
             "read_state": "read", "mtime_epoch": NOW - stale_age},
            {"name": "control_fresh", "role": "control", "path": "/f",
             "read_state": "read", "mtime_epoch": NOW - fresh_age},
        ],
    }


def test_healthy_controls_prove_the_alarm_fires():
    ok, failures = self_test_controls(grade_observations(_payload(), THRESHOLD))
    assert ok, failures


def test_a_silently_broken_alarm_is_caught_by_the_stale_control():
    """The case this whole mechanism exists for.

    If a refactor made every path grade `fresh`, a deployment check still passes.
    The planted stale control is what does not.
    """
    ok, failures = self_test_controls(
        grade_observations(_payload(stale_age=5), THRESHOLD)
    )
    assert not ok
    assert any("control_stale" in f for f in failures)


def test_an_alarm_that_screams_at_everything_is_caught_by_the_fresh_control():
    """Without this control, a permanently-firing alarm satisfies the stale one."""
    ok, failures = self_test_controls(
        grade_observations(_payload(fresh_age=99999), THRESHOLD)
    )
    assert not ok
    assert any("control_fresh" in f for f in failures)


def test_missing_controls_are_a_failure_not_a_pass():
    """A control that never made it back leaves the alarm UNPROVEN that run."""
    payload = _payload()
    payload["targets"] = [payload["targets"][0]]
    ok, failures = self_test_controls(grade_observations(payload, THRESHOLD))
    assert not ok
    assert len(failures) == 2


# ------------------------------------------------------------------ exit codes


def _run(tmp_path, payload):
    p = tmp_path / "obs.json"
    p.write_text(json.dumps(payload))
    return main(["--observations", str(p), "--require-controls"])


def test_exit_0_only_when_capture_fresh_and_alarm_proven(tmp_path):
    assert _run(tmp_path, _payload()) == 0


def test_exit_1_when_the_capture_is_stale(tmp_path):
    assert _run(tmp_path, _payload(capture_age=9999)) == 1


def test_exit_2_when_the_alarm_itself_misgraded(tmp_path):
    """rc 2 is a DIFFERENT escalation from rc 1: the capture's state is UNKNOWN,
    not bad. Collapsing them would page about the wrong thing."""
    assert _run(tmp_path, _payload(stale_age=5)) == 2


def test_an_unreadable_observation_file_never_reads_as_clean(tmp_path):
    """The silent-empty failure: a missing observation must not exit 0."""
    assert main([
        "--observations", str(tmp_path / "nope.json"), "--require-controls"
    ]) == 2


def test_an_empty_target_list_is_not_a_clean_bill_of_health(tmp_path):
    p = tmp_path / "obs.json"
    p.write_text(json.dumps({"now_epoch": NOW, "targets": []}))
    assert main(["--observations", str(p)]) == 1


def test_the_verdict_carries_what_it_does_not_establish(tmp_path):
    """A green must never be readable without its limit."""
    graded = grade_observations(_payload(), THRESHOLD)
    assert "Freshness is not validity" in graded["is_not"]
