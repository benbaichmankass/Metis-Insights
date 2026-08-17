"""The DURABLE half of exit-loop observability.

`exit_loop_health`'s `max_interval_ms` lives in a module global that is never
reloaded, so it is scoped to one process — and the trader redeploys off `main`
via `ict-git-sync` (five OBSERVED processes in ~10h, measured 2026-08-16 from
`process_started_utc`, not from merge times). A max over a short
window is systematically LOW, which makes the in-memory grade most reassuring
exactly when the system is busiest. These tests pin the properties that fix
buys, especially the one that motivated it: the record must survive a restart.
"""
from __future__ import annotations

import json

import pytest

from src.runtime import exit_interval_soak as s
from src.runtime import exit_loop_health as h


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "soak_log_path", lambda: tmp_path / s.SOAK_LOG_NAME)
    h._reset_for_tests()
    yield tmp_path
    h._reset_for_tests()


# --- the property the whole module exists for -------------------------------

def test_max_survives_a_process_restart(_iso, monkeypatch):
    """THE regression. A restart empties every in-memory accumulator; the max
    must remain recoverable from the data or it is lost on every deploy."""
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])

    h.record_pass(1_000.0)                    # process A, first pass
    t[0] = 1055.0
    h.record_pass(1_000.0)                    # 55s interval — the peak
    assert h.status()["max_interval_ms"] == 55_000.0

    h._reset_for_tests()                      # <-- a deploy
    assert h.status()["max_interval_ms"] is None, "in-memory max is gone, as expected"

    t[0] = 2000.0
    h.record_pass(1_000.0)                    # process B
    t[0] = 2031.0
    h.record_pass(1_000.0)                    # a quiet 31s interval
    assert h.status()["max_interval_ms"] == 31_000.0   # B alone would report this

    # The durable record still knows about the 55s peak from process A.
    summary = s.read_soak_records()["summary"]
    assert summary["max_interval_ms"] == 55_000.0
    assert summary["processes_seen"] == 2
    assert summary["intervals_measured"] == 2


def test_first_pass_of_a_process_is_not_a_zero_interval(_iso, monkeypatch):
    """A first pass closes no interval. Recording it as 0 would invent a sample
    and drag any mean down; omitting the row would hide the process boundary and
    let the restart gap read as a real interval."""
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    h.record_pass(1_000.0)
    rows = s.read_soak_records()["records"]
    assert len(rows) == 1
    assert rows[0]["interval_ms"] is None          # not 0.0
    assert rows[0]["first_pass_of_process"] is True
    assert rows[0]["over_requirement"] is None    # not False — nothing to grade
    assert s.read_soak_records()["summary"]["intervals_measured"] == 0


def test_breach_verdict_is_frozen_at_write_time(_iso, monkeypatch):
    """The requirement is env-configurable, so a reader recomputing it against
    today's value would silently re-grade history."""
    monkeypatch.setenv(h._REQUIREMENT_ENV, "60")
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    h.record_pass(1_000.0)
    t[0] = 1070.0
    h.record_pass(1_000.0)
    rec = [r for r in s.read_soak_records()["records"] if r["interval_ms"]][0]
    assert rec["over_requirement"] is True and rec["requirement_s"] == 60.0
    # Widening the requirement now must NOT retro-absolve the recorded breach.
    monkeypatch.setenv(h._REQUIREMENT_ENV, "300")
    assert s.read_soak_records()["summary"]["breaches"] == 1


def test_summary_is_over_the_whole_file_not_the_returned_page(_iso, monkeypatch):
    """A cross-process max truncated to the newest N would reintroduce exactly
    the windowing bias this file removes, in the reader instead of the writer."""
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    h.record_pass(1_000.0)
    t[0] = 1058.0
    h.record_pass(1_000.0)              # the 58s peak, oldest measured row
    for i in range(20):
        t[0] += 31.0
        h.record_pass(1_000.0)
    out = s.read_soak_records(limit=3)
    assert out["count"] == 3                                  # page is small
    assert out["summary"]["max_interval_ms"] == 58_000.0      # summary is not
    assert out["summary"]["intervals_measured"] == 21


def test_a_torn_line_does_not_fail_the_read(_iso):
    p = _iso / s.SOAK_LOG_NAME
    p.write_text('{"interval_ms": 100.0, "requirement_s": 60.0}\n{"broken\n', encoding="utf-8")
    out = s.read_soak_records()
    assert out["present"] is True and out["summary"]["rows"] == 1


def test_absent_log_is_present_false_not_an_error(_iso):
    out = s.read_soak_records()
    assert out["present"] is False and out["count"] == 0 and out["records"] == []


def test_writer_failure_never_raises_into_the_exit_loop(_iso, monkeypatch):
    """This runs on the money loop's own thread. An unwritable disk must lose an
    observation and nothing else."""
    def _boom():
        raise OSError("disk gone")
    monkeypatch.setattr(s, "soak_log_path", _boom)
    h.record_pass(1_000.0)          # must not raise
    assert h.status()["passes"] == 1


def test_record_is_json_serialisable(_iso, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    h.record_pass(1_234.5)
    t[0] = 1040.0
    h.record_pass(2_345.6)
    raw = (_iso / s.SOAK_LOG_NAME).read_text(encoding="utf-8").strip().split("\n")
    assert len(raw) == 2
    for line in raw:
        obj = json.loads(line)
        assert "logged_at_utc" in obj and "process_started_utc" in obj


# ---------------------------------------------------------------------------
# The READ SURFACE. #9627 shipped the writer + `read_soak_records` and
# registered only `/api/diag/log_file?name=exit_interval_soak`, which returns a
# raw TAIL of lines and never the `summary`. So `summary.max_interval_ms` — the
# cross-process maximum the whole module exists to produce — was computed on
# every read and reachable by nobody
# (BL-20260816-EXIT-INTERVAL-SOAK-SUMMARY-HAS-NO-READ-SURFACE).
#
# It was caught during live verification: establishing the cross-process max
# meant paging the entire file out through the diag tail and re-implementing the
# aggregation by hand — which is exactly the reader-side windowing bias
# `read_soak_records` computes over the whole file to avoid.
#
# No sibling soak route has a mount test either, which is part of why a missing
# one was invisible. This asserts the mount rather than the handler body.
# ---------------------------------------------------------------------------


def test_exit_interval_soak_route_is_declared_and_mounted():
    """The summary must be reachable over HTTP, not just computable in-process.

    Asserted in two halves, and deliberately NOT by importing the FastAPI app:
    that import needs `email-validator`, which is absent in some environments, so
    a test written against it would SKIP rather than fail — and a skipped mount
    test is how a missing route stays invisible in the first place. The source
    check is environment-independent and catches the exact failure mode: a router
    written and never wired in.
    """
    from pathlib import Path

    from src.web.api.routers import exit_interval as route_mod

    declared = {getattr(r, "path", None) for r in route_mod.router.routes}
    assert "/api/bot/exit-interval/soak" in declared, declared

    main_src = Path("src/web/api/main.py").read_text(encoding="utf-8")
    assert "exit_interval as exit_interval_router" in main_src, "router not imported"
    assert "include_router(exit_interval_router.router)" in main_src, "router not mounted"


def test_exit_interval_soak_route_returns_the_summary(tmp_path, monkeypatch):
    """Mounting is not enough: the route must return `summary`, which is the
    field a `log_file` tail structurally cannot provide."""
    from src.web.api.routers import exit_interval as route_mod

    for pms, ivms in ((10.0, None), (20.0, 55000.0)):
        s.record_exit_interval(s.build_exit_interval_record(
            interval_ms=ivms, pass_ms=pms, requirement_s=60.0,
            process_started_utc="2026-01-01T00:00:00+00:00",
        ))
    out = route_mod.exit_interval_soak(limit=50, breached_only=False)

    assert out["present"] is True
    assert out["summary"]["max_interval_ms"] == 55000.0
    assert out["summary"]["intervals_measured"] == 1
    assert out["summary"]["processes_seen"] == 1
