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
