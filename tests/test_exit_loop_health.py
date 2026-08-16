"""Liveness for the decoupled exit loop — section 6.1.

`heartbeat.txt` covers the exit loop today only because the loop runs INLINE: a
hang stops the tick, so the heartbeat stops. Once it moves to its own loop the
main heartbeat keeps ticking through an exit-loop wedge, which is the worst
direction for a failure to point. These tests pin the four states apart and prove
the alert latches instead of repeating.
"""
from __future__ import annotations

import json

import pytest

from src.runtime import exit_loop_health as h


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    h._reset_for_tests()
    monkeypatch.setattr(h, "_alert_state_path", lambda: tmp_path / "alert.json")
    sent: list[str] = []
    monkeypatch.setattr(h, "_send", lambda m: sent.append(m))
    yield sent
    h._reset_for_tests()


# --- the four states are genuinely four ---------------------------------------

def test_never_ran_is_not_stale():
    """A booting process must not read as a wedged one."""
    st = h.status()
    assert st["state"] == "never_ran"
    assert st["stale"] is False
    assert st["age_seconds"] is None      # not 0 — we have no age to report
    assert st["passes"] == 0


def test_fresh_after_a_pass():
    h.record_pass(28_000.0)
    st = h.status()
    assert st["state"] == "fresh" and st["stale"] is False
    assert st["last_pass_ms"] == 28_000.0


def test_stale_once_the_window_elapses(monkeypatch):
    monkeypatch.setenv(h._STALE_ENV, "180")
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    h.record_pass(28_000.0)
    t[0] = 1179.0
    assert h.status()["state"] == "fresh"
    t[0] = 1181.0
    st = h.status()
    assert st["state"] == "stale" and st["stale"] is True


def test_unknown_is_neither_healthy_nor_stale(monkeypatch):
    """An unreadable state is 'we did not look'. Reporting it as fresh would hide
    a wedge; reporting it as stale would cry wolf."""
    def _boom() -> float:
        raise RuntimeError("state unreadable")
    # stale_seconds() is called inside status(); patching it is a clean way to
    # force the failure path without breaking the fixture's own teardown.
    monkeypatch.setattr(h, "stale_seconds", _boom)
    st = h.status()
    assert st["state"] == "unknown"
    assert st["stale"] is False
    assert st["passes"] is None               # not 0 — we do not know


# --- max_pass_ms is the load-bearing statistic --------------------------------

def test_max_pass_ms_survives_a_later_faster_pass():
    """A mean that looks fine while the peak blows the 60s window IS the
    2026-06-09 incident's shape."""
    h.record_pass(55_000.0)
    h.record_pass(1_000.0)
    st = h.status()
    assert st["max_pass_ms"] == 55_000.0
    assert st["last_pass_ms"] == 1_000.0
    assert st["passes"] == 2


# --- the threshold cannot be switched off by a typo ---------------------------

@pytest.mark.parametrize("bad", ["", "0", "-1", "abc"])
def test_unusable_threshold_falls_back_rather_than_disabling(monkeypatch, bad):
    monkeypatch.setenv(h._STALE_ENV, bad)
    assert h.stale_seconds() == h._DEFAULT_STALE_S


def test_default_threshold_exceeds_the_60s_target():
    """It must not fire on a merely slow pass — the measured pass is ~28s and the
    coverage target is 60s."""
    assert h._DEFAULT_STALE_S >= 120.0


# --- the alert LATCHES (a per-tick alarm is the desensitized-alarm P1) --------

def test_alert_fires_once_on_crossing_into_stale(monkeypatch, _clean):
    sent = _clean
    monkeypatch.setenv(h._STALE_ENV, "60")
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    h.record_pass(28_000.0)
    t[0] = 1200.0
    assert h.run_exit_loop_health_check()["alerted"] is True
    assert len(sent) == 1 and "STALE" in sent[0]
    # Three more ticks in the same condition must stay silent.
    for _ in range(3):
        assert h.run_exit_loop_health_check()["alerted"] is False
    assert len(sent) == 1, f"alarm repeated: {len(sent)} sends"


def test_recovery_fires_once_and_re_arms(monkeypatch, _clean):
    sent = _clean
    monkeypatch.setenv(h._STALE_ENV, "60")
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    h.record_pass(28_000.0)
    t[0] = 1200.0
    h.run_exit_loop_health_check()                  # ALERT
    h.record_pass(30_000.0)                         # loop resumes
    res = h.run_exit_loop_health_check()
    assert res["recovered"] is True
    assert any("recovered" in m for m in sent)
    # And the latch re-arms, so a second wedge alerts again.
    t[0] = 1500.0
    assert h.run_exit_loop_health_check()["alerted"] is True


def test_never_ran_never_alerts(_clean):
    """Otherwise every restart pings the operator and teaches them to ignore it."""
    sent = _clean
    for _ in range(5):
        assert h.run_exit_loop_health_check()["alerted"] is False
    assert sent == []


# --- the writer records only COMPLETED passes ---------------------------------

def test_a_hung_pass_does_not_refresh_liveness():
    """record_pass is called AFTER the pass returns, so a pass that starts and
    hangs leaves liveness aging — which is the whole condition being detected."""
    import inspect
    src = inspect.getsource(h.record_pass)
    assert "duration_ms" in src
    # Nothing has completed, so a started-but-hung pass reads never_ran/stale,
    # never fresh.
    assert h.status()["state"] == "never_ran"


def test_state_file_round_trips(tmp_path):
    h.record_pass(28_000.0)
    path = h.write_state_file(runtime_dir=str(tmp_path))
    assert path
    payload = json.loads((tmp_path / h.STATE_FILE_NAME).read_text())
    assert payload["state"] == "fresh"
    assert payload["last_pass_ms"] == 28_000.0
    assert "generated_at" in payload


# --- the REQUIREMENT is a different question from liveness --------------------
#
# BL-20260816-EXIT-EVAL-INTERVAL-AT-60S-REQUIREMENT: the loop was measured at a
# 58940.8ms worst pass (n=694) — 1.1s inside the operator's 60s requirement — and
# graded `fresh`, because `stale_threshold_s` is 180s and answers "is the loop
# alive", not "is the interval inside the requirement".

def test_requirement_threshold_is_not_the_staleness_threshold():
    """If these ever collapse to one number, the requirement stops being checked:
    at 180s a 59s interval and a 179s interval are both `fresh`."""
    assert h._DEFAULT_REQUIREMENT_S == 60.0
    assert h._DEFAULT_STALE_S != h._DEFAULT_REQUIREMENT_S
    assert h._REQUIREMENT_ENV != h._STALE_ENV


@pytest.mark.parametrize("bad", ["", "0", "-1", "abc"])
def test_unusable_requirement_falls_back_rather_than_disabling(monkeypatch, bad):
    """A typo must not widen or switch off the one check on the M20 guarantee."""
    monkeypatch.setenv(h._REQUIREMENT_ENV, bad)
    assert h.requirement_seconds() == h._DEFAULT_REQUIREMENT_S


def test_no_interval_exists_until_two_passes_complete(monkeypatch):
    """`not_measured` must not read as `within`. One pass closes no interval, so a
    process that has evaluated almost nothing cannot report compliance."""
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    assert h.status()["requirement_state"] == "not_measured"   # zero passes
    h.record_pass(20_000.0)
    st = h.status()
    assert st["requirement_state"] == "not_measured"           # one pass
    assert st["intervals_measured"] == 0
    assert st["max_interval_ms"] is None                       # not 0.0
    t[0] = 1030.0
    h.record_pass(20_000.0)
    st = h.status()
    assert st["requirement_state"] == "within"
    assert st["intervals_measured"] == 1
    assert st["max_interval_ms"] == 30_000.0


def test_interval_is_measured_not_derived_from_pass_duration(monkeypatch):
    """A stall BETWEEN the sleep and the next completion is invisible to
    `max(EXIT_LOOP_INTERVAL_SECONDS, pass_ms)` and visible here. That gap is the
    reason this is measured rather than modelled."""
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    h.record_pass(1_000.0)
    t[0] = 1090.0                       # 90s wall-clock gap...
    h.record_pass(1_000.0)              # ...around a 1s pass
    st = h.status()
    assert st["max_interval_ms"] == 90_000.0
    assert st["max_pass_ms"] == 1_000.0          # the derivation would say 30s
    assert st["requirement_state"] == "breached"


def test_a_breach_latches_into_the_grade(monkeypatch):
    """The requirement is written against the MAX, so a later compliant interval
    does not un-breach the process — the trade that went unevaluated still did."""
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    h.record_pass(1_000.0)
    t[0] = 1075.0
    h.record_pass(1_000.0)
    assert h.status()["requirement_state"] == "breached"
    t[0] = 1085.0
    h.record_pass(1_000.0)
    st = h.status()
    assert st["requirement_state"] == "breached"
    assert st["interval_breaches"] == 1
    assert st["max_interval_ms"] == 75_000.0


def test_fresh_and_breached_coexist(monkeypatch):
    """The exact condition that was invisible: the loop is ALIVE and the
    requirement is MISSED. If one field had to carry both, this is the case it
    would lose."""
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    h.record_pass(1_000.0)
    t[0] = 1070.0
    h.record_pass(1_000.0)
    st = h.status()
    assert st["state"] == "fresh" and st["stale"] is False
    assert st["requirement_state"] == "breached"


def test_unknown_never_reports_compliance(monkeypatch):
    """A read failure must not be able to say `within`."""
    def _boom() -> float:
        raise RuntimeError("state unreadable")
    monkeypatch.setattr(h, "stale_seconds", _boom)
    st = h.status()
    assert st["state"] == "unknown"
    assert st["requirement_state"] == "unknown"
    assert st["intervals_measured"] is None      # not 0 — we do not know


# --- the breach alert fires once per PROCESS ---------------------------------

def test_breach_alerts_once_per_process(monkeypatch, _clean):
    sent = _clean
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    h.record_pass(1_000.0)
    t[0] = 1070.0
    h.record_pass(1_000.0)
    res = h.run_exit_loop_health_check()
    assert res["requirement_alerted"] is True
    assert len(sent) == 1 and "INTERVAL BREACHED" in sent[0]
    for _ in range(4):
        assert h.run_exit_loop_health_check()["requirement_alerted"] is False
    assert len(sent) == 1, f"alarm repeated: {len(sent)} sends"


def test_a_new_process_breach_alerts_again(monkeypatch, _clean):
    """`max_interval_ms` resets on restart, so a latch that ignored process
    identity would go silent after the first breach ever — and the trader
    restarts often (three processes in the 2026-08-15/16 window alone)."""
    sent = _clean
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    h.record_pass(1_000.0)
    t[0] = 1070.0
    h.record_pass(1_000.0)
    h.run_exit_loop_health_check()
    assert len(sent) == 1
    h._reset_for_tests()                      # a restart: new process identity
    t[0] = 2000.0
    h.record_pass(1_000.0)
    t[0] = 2070.0
    h.record_pass(1_000.0)
    assert h.run_exit_loop_health_check()["requirement_alerted"] is True
    assert len(sent) == 2


def test_stale_recovery_does_not_re_arm_the_breach_alert(monkeypatch, _clean):
    """The two latches share one state file; a recovery write must carry the
    breach latch forward or the same process alerts twice."""
    sent = _clean
    monkeypatch.setenv(h._STALE_ENV, "60")
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    h.record_pass(1_000.0)
    t[0] = 1070.0
    h.record_pass(1_000.0)                    # breach + still fresh
    h.run_exit_loop_health_check()            # breach ALERT
    breach_sends = [m for m in sent if "INTERVAL BREACHED" in m]
    assert len(breach_sends) == 1
    t[0] = 1200.0
    h.run_exit_loop_health_check()            # goes STALE -> writes alert state
    h.record_pass(1_000.0)
    h.run_exit_loop_health_check()            # RECOVERS -> writes alert state
    assert [m for m in sent if "INTERVAL BREACHED" in m] == breach_sends


def test_state_file_carries_the_requirement_grade(tmp_path, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    h.record_pass(1_000.0)
    t[0] = 1070.0
    h.record_pass(1_000.0)
    h.write_state_file(runtime_dir=str(tmp_path))
    payload = json.loads((tmp_path / h.STATE_FILE_NAME).read_text())
    assert payload["requirement_state"] == "breached"
    assert payload["requirement_s"] == 60.0
    assert payload["max_interval_ms"] == 70_000.0
