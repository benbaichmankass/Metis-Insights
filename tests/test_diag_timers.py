"""BL-20260821-NO-READ-SURFACE-FOR-TIMER-SCHEDULE — `/api/diag/timers`.

Every surface that reported systemd timers reported STATE, never SCHEDULE:
`/api/diag/services` returns `{unit, state, sub_state, active_enter_iso}`, so
`ict-exchange-fills-pull.timer` read "active" whether it fired HOURLY or DAILY.
That difference was a measured, money-path defect — a real-money trade crossed
its take-profit and was booked at `candle_at_close` because the fills store held
nothing recent enough (BL-20260821-ICTSCALP-TP-CROSSED-BOOKED-AS-ESTIMATE) —
and a relay-bound session could not read the cadence at all, only infer it from
unit files that may not match what is installed.

THE STATE THAT CARRIES THE WEIGHT is the three-way split. Most of these timers
are MONOTONIC (`OnBootSec`/`OnUnitActiveSec`), so an empty `TimersCalendar` is
the CORRECT answer for them. Collapsing "no calendar" into "could not read"
would report two-thirds of the fleet as broken; collapsing the other way would
report a genuinely unreadable timer as scheduleless. Both are pinned below.
"""
from __future__ import annotations

import pytest

from src.web.api.routers import diag


class _Req:
    headers: dict = {}


@pytest.fixture(autouse=True)
def _no_token(monkeypatch):
    monkeypatch.setattr(diag, "_require_diag_token", lambda _r: None)


def _rows(payload):
    return {r["unit"]: r for r in payload["timers"]}


def test_a_calendar_timer_reports_its_calendar(monkeypatch):
    """The motivating case: hourly vs daily must be READABLE, not inferred."""
    monkeypatch.setattr(diag, "_timer_units", lambda: ["ict-exchange-fills-pull.timer"])
    monkeypatch.setattr(diag, "_systemctl_show", lambda u, p: {
        "ict-exchange-fills-pull.timer": {
            "TimersCalendar": "{ OnCalendar=*-*-* *:07:00 ; next_elapse=... }",
            "TimersMonotonic": "",
            "NextElapseUSecRealtime": "Fri 2026-08-22 06:07:00 UTC",
            "LastTriggerUSec": "Fri 2026-08-22 05:07:00 UTC",
            "ActiveState": "active",
        }})
    r = _rows(diag.get_timers(_Req()))["ict-exchange-fills-pull.timer"]
    assert r["read_state"] == "read"
    assert r["schedule_state"] == "calendar"
    assert "OnCalendar" in r["on_calendar"]
    assert r["next_elapse_realtime"]


def test_a_monotonic_timer_is_NOT_reported_as_unreadable(monkeypatch):
    """MOST of the fleet is monotonic. An empty calendar is CORRECT for them.

    This is the assertion that stops the obvious implementation — key on
    TimersCalendar alone — from reporting two-thirds of the timers as broken.
    """
    monkeypatch.setattr(diag, "_timer_units", lambda: ["ict-liveness-watchdog.timer"])
    monkeypatch.setattr(diag, "_systemctl_show", lambda u, p: {
        "ict-liveness-watchdog.timer": {
            "TimersCalendar": "",
            "TimersMonotonic": "{ OnUnitActiveSec=1min ; next_elapse=... }",
            "NextElapseUSecMonotonic": "2min 3s",
            "ActiveState": "active",
        }})
    r = _rows(diag.get_timers(_Req()))["ict-liveness-watchdog.timer"]
    assert r["read_state"] == "read"
    assert r["schedule_state"] == "monotonic", (
        "a monotonic timer has no calendar BY DESIGN — reporting it as "
        "unreadable or scheduleless would condemn most of the fleet"
    )
    assert "OnUnitActiveSec" in r["on_monotonic"]


def test_could_not_look_is_NOT_no_schedule(monkeypatch):
    """systemctl absent/timed out => could_not_look, never an empty schedule."""
    monkeypatch.setattr(diag, "_timer_units", lambda: ["ict-git-sync.timer"])
    monkeypatch.setattr(diag, "_systemctl_show", lambda u, p: {})   # the failure shape
    payload = diag.get_timers(_Req())
    r = _rows(payload)["ict-git-sync.timer"]
    assert r["read_state"] == "could_not_look"
    assert r["schedule_state"] == "unknown"
    assert r["on_calendar"] is None and r["on_monotonic"] is None
    assert payload["summary"]["could_not_look"] == 1
    assert payload["summary"]["read"] == 0, (
        "an all-unreadable payload must not present as a read fleet"
    )


def test_a_clean_read_with_neither_spelling_is_no_schedule(monkeypatch):
    """Read fine, declares neither => a real reportable state, not a failure."""
    monkeypatch.setattr(diag, "_timer_units", lambda: ["ict-db-integrity.timer"])
    monkeypatch.setattr(diag, "_systemctl_show", lambda u, p: {
        "ict-db-integrity.timer": {"TimersCalendar": "", "TimersMonotonic": "",
                                   "ActiveState": "inactive"}})
    r = _rows(diag.get_timers(_Req()))["ict-db-integrity.timer"]
    assert r["read_state"] == "read"
    assert r["schedule_state"] == "no_schedule"


def test_the_route_covers_every_allowlisted_timer():
    """Scope is derived from _CANONICAL_UNITS, never a second hand-kept list.

    A separate list would drift from /api/diag/services, which is the exact
    "one copy of every fact" failure the work plan is correcting.
    """
    units = diag._timer_units()
    assert units, "no .timer units found in _CANONICAL_UNITS — derivation broke"
    assert all(u.endswith(".timer") for u in units)
    assert set(units) <= set(diag._CANONICAL_UNITS)
    assert "ict-exchange-fills-pull.timer" in units
