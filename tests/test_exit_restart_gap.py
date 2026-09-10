"""THE INTERVAL THE PROMISE COVERS AND THE INSTRUMENT EXCLUDES BY CONSTRUCTION.

M20 guarantees no live trade goes 60s without re-evaluation.
`exit_loop_health.max_interval_ms` grades that from a module global that resets
on every restart, and the live trader restarts on every merge to `main` — 12
processes in ~8.3h, measured 2026-09-09. So the gap from the last completed pass
of process N to the first of process N+1 is measured by NOBODY, and a deploy is
exactly when the trader is least likely to be evaluating exits.

These tests pin the five states apart, and pin the two ways this could be wrong
in the REASSURING direction: a rotated log yielding an arbitrary within-process
interval wearing a restart-gap label, and a negative gap clamped to zero.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import exit_interval_soak as s
from src.runtime import exit_restart_gap as g


def _row(proc, at, *, first=False, **extra):
    r = {"process_started_utc": proc, "logged_at_utc": at,
         "first_pass_of_process": first, "requirement_s": 60.0}
    r.update(extra)
    return r


_T0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)


def _at(seconds):
    return (_T0 + timedelta(seconds=seconds)).isoformat()


def _boundary(gap_s, *, first=True):
    """Two processes whose boundary is exactly `gap_s` seconds wide.

    Built with real datetime arithmetic rather than string formatting: an
    earlier version interpolated `{30 + gap_s}` into the seconds field and
    produced `10:00:105` for every gap over 29s, which is not a timestamp. The
    grader correctly refused it — the fixture was the bug.
    """
    return [
        _row("P1", _at(0), first=True),
        _row("P1", _at(30)),
        _row("P2", _at(30 + gap_s), first=first),
        _row("P2", _at(60 + gap_s)),
    ]


# --- the five states are genuinely five ---------------------------------------

def test_one_process_is_not_measured_never_within():
    """A log with one process in it has not demonstrated compliance — it has
    demonstrated nothing. THIS is the state that must never read as `within`."""
    r = g.grade_restart_gaps([_row("P1", "2026-09-09T10:00:00+00:00", first=True),
                              _row("P1", "2026-09-09T10:00:30+00:00")])
    assert r["restart_gap_state"] == g.RESTART_GAP_NOT_MEASURED
    assert r["restart_gap_state"] != g.RESTART_GAP_WITHIN
    assert r["processes_seen"] == 1
    assert r["max_gap_ms"] is None          # not 0.0


def test_no_rows_at_all_is_not_measured():
    r = g.grade_restart_gaps([])
    assert r["restart_gap_state"] == g.RESTART_GAP_NOT_MEASURED
    assert r["gaps_measured"] == 0


def test_a_clean_restart_gap_is_within():
    """The measured 2026-09-09 shape: 11 gaps at 25.3–53.9s, 0 of 11 over 60s."""
    r = g.grade_restart_gaps(_boundary(25))
    assert r["restart_gap_state"] == g.RESTART_GAP_WITHIN
    assert r["gaps_measured"] == 1
    assert r["max_gap_ms"] == 25_000.0
    assert r["breaches"] == 0


def test_a_long_restart_gap_breaches():
    r = g.grade_restart_gaps(_boundary(75))
    assert r["restart_gap_state"] == g.RESTART_GAP_BREACHED
    assert r["breaches"] == 1
    assert r["gaps"][0]["over_requirement"] is True


def test_a_restart_gap_can_near_miss():
    r = g.grade_restart_gaps(_boundary(55))
    assert r["restart_gap_state"] == g.RESTART_GAP_NEAR_MISS
    assert r["gaps"][0]["over_requirement"] is False    # the promise was KEPT
    assert r["max_gap_ratio"] == round(55_000.0 / 60_000.0, 4)


def test_a_breach_is_never_downgraded_to_a_near_miss():
    """ORDER CONTROL, the same one `exit_loop_health` needs: if the band were
    tested first every breach would render as a warning."""
    r = g.grade_restart_gaps(_boundary(61))
    assert r["restart_gap_state"] == g.RESTART_GAP_BREACHED


# --- the two ways this could be wrong in the REASSURING direction -------------

def test_a_rotated_log_is_ungradeable_not_a_short_within():
    """THE REGRESSION THAT MATTERS. If the successor's earliest surviving row is
    not a genuine first pass, the "gap" computed from it is an arbitrary
    WITHIN-process interval — systematically SHORT, i.e. it would manufacture a
    clean `within` out of a truncated file."""
    rows = _boundary(75, first=False)       # a real 75s gap, but the marker is gone
    r = g.grade_restart_gaps(rows)
    assert r["restart_gap_state"] == g.RESTART_GAP_UNKNOWN
    assert r["restart_gap_state"] != g.RESTART_GAP_WITHIN
    assert r["ungradeable_gaps"] == 1
    assert r["gaps"][0]["gap_state"] == g.GAP_UNGRADEABLE
    assert r["gaps"][0]["gap_ms"] is None   # no number is offered at all
    # and the control: the SAME rows with the marker present do grade, and breach
    assert (g.grade_restart_gaps(_boundary(75))["restart_gap_state"]
            == g.RESTART_GAP_BREACHED)


def test_an_overlapping_boundary_is_named_not_clamped_to_zero():
    """The old process can complete a pass during handover. Clamping that to a
    0ms gap would report perfect coverage from an ordering we did not establish."""
    rows = [
        _row("P1", "2026-09-09T10:00:00+00:00", first=True),
        _row("P1", "2026-09-09T10:00:40+00:00"),      # P1's last is AFTER P2's first
        _row("P2", "2026-09-09T10:00:30+00:00", first=True),
        _row("P2", "2026-09-09T10:01:00+00:00"),
    ]
    r = g.grade_restart_gaps(rows)
    assert r["overlapping_gaps"] == 1
    assert r["gaps"][0]["gap_state"] == g.GAP_OVERLAPPING
    assert r["gaps"][0]["gap_ms"] == -10_000.0        # recorded, not hidden
    assert r["gaps"][0]["over_requirement"] is None   # not graded, not False
    assert r["max_gap_ms"] is None                    # excluded from the max
    assert r["restart_gap_state"] == g.RESTART_GAP_UNKNOWN


def test_an_unparseable_stamp_never_becomes_a_zero_gap():
    rows = [
        _row("P1", "2026-09-09T10:00:00+00:00", first=True),
        _row("P1", "not-a-timestamp"),
        _row("P2", "2026-09-09T10:01:15+00:00", first=True),
    ]
    r = g.grade_restart_gaps(rows)
    assert r["unattributed_rows"] == 1
    # P1's last READABLE stamp is 10:00:00, so the gap is 75s and it breaches —
    # the unreadable row is dropped from the population, never treated as 0.
    assert r["restart_gap_state"] == g.RESTART_GAP_BREACHED


def test_a_row_with_no_process_identity_is_counted_not_silently_dropped():
    rows = _boundary(25) + [_row(None, "2026-09-09T10:02:00+00:00")]
    r = g.grade_restart_gaps(rows)
    assert r["unattributed_rows"] == 1
    assert r["processes_seen"] == 2


# --- ordering, scoping, populations -------------------------------------------

def test_processes_are_ordered_by_when_they_ran_not_by_key():
    """The key identifies a process; the log stamp says when it ran, comes off
    the same clock as the gap, and survives a key that sorts oddly."""
    rows = [
        _row("zzz-earlier", "2026-09-09T10:00:00+00:00", first=True),
        _row("zzz-earlier", "2026-09-09T10:00:30+00:00"),
        _row("aaa-later", "2026-09-09T10:01:00+00:00", first=True),
    ]
    r = g.grade_restart_gaps(rows)
    assert r["gaps"][0]["from_process"] == "zzz-earlier"
    assert r["gaps"][0]["to_process"] == "aaa-later"
    assert r["max_gap_ms"] == 30_000.0


def test_only_to_process_narrows_to_this_process_boundary():
    rows = (_boundary(25)
            + [_row("P3", "2026-09-09T10:03:00+00:00", first=True)])
    whole = g.grade_restart_gaps(rows)
    assert whole["boundaries_seen"] == 2
    mine = g.grade_restart_gaps(rows, only_to_process="P2")
    assert mine["boundaries_seen"] == 1
    assert mine["gaps"][0]["to_process"] == "P2"


def test_a_boundary_absent_from_the_population_is_not_measured():
    """A live process asking about a boundary the tail does not contain gets
    `not_measured`, never `within`."""
    r = g.grade_restart_gaps(_boundary(25), only_to_process="P9")
    assert r["restart_gap_state"] == g.RESTART_GAP_NOT_MEASURED


def test_every_result_states_its_population():
    r = g.grade_restart_gaps(_boundary(25), population="every row on disk")
    assert r["population"] == "every row on disk"
    assert r["rows_seen"] == 4
    assert r["requirement_s"] == 60.0


def test_the_requirement_is_honoured_from_the_env(monkeypatch):
    """One owner for the requirement — imported from `exit_loop_health`, not
    re-derived, so the grade and the live gate can never disagree."""
    monkeypatch.setenv("EXIT_EVAL_MAX_INTERVAL_SECONDS", "20")
    r = g.grade_restart_gaps(_boundary(25))
    assert r["requirement_s"] == 20.0
    assert r["restart_gap_state"] == g.RESTART_GAP_BREACHED


# --- the bounded tail read, and the live entry point --------------------------

@pytest.fixture
def _log(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "soak_log_path", lambda: tmp_path / s.SOAK_LOG_NAME)
    return tmp_path / s.SOAK_LOG_NAME


def test_tail_returns_the_last_rows_in_file_order(_log):
    _log.write_text("\n".join(json.dumps({"i": i}) for i in range(200)) + "\n")
    rows = s.read_tail_records(limit=5)
    assert [r["i"] for r in rows] == [195, 196, 197, 198, 199]


def test_tail_skips_a_torn_line_rather_than_failing(_log):
    _log.write_text(json.dumps({"i": 1}) + "\n{not json\n" + json.dumps({"i": 2}) + "\n")
    assert [r["i"] for r in s.read_tail_records(limit=10)] == [1, 2]


def test_tail_on_a_missing_log_is_empty_not_an_error(_log):
    assert s.read_tail_records() == []


def test_grade_this_process_reads_the_boundary_off_the_real_log(_log):
    for r in _boundary(75):
        s.record_exit_interval(r)
    out = g.grade_this_process("P2")
    assert out["restart_gap_state"] == g.RESTART_GAP_BREACHED
    assert out["gaps"][0]["to_process"] == "P2"
    assert "bounded tail" in out["population"]


def test_grade_this_process_without_an_identity_is_not_measured(_log):
    out = g.grade_this_process(None)
    assert out["restart_gap_state"] == g.RESTART_GAP_NOT_MEASURED


def test_the_soak_summary_carries_the_cross_process_gap(_log):
    for r in _boundary(75):
        s.record_exit_interval(r)
    env = s.read_soak_records(limit=10)
    gap = env["summary"]["restart_gap"]
    assert gap["restart_gap_state"] == g.RESTART_GAP_BREACHED
    assert gap["population"] == "every row on disk"
    # computed over the WHOLE file, not the returned page
    assert gap["rows_seen"] == 4
