"""Tests for the M1 P1-C hourly snapshot timer wrapper.

Pins:

  - ``scripts/send_hourly_now.py`` exits non-zero (EX_TEMPFAIL=75) when
    another instance already holds the flock, so a second timer firing
    that races the first never double-sends;
  - releases the lock cleanly on success, so a subsequent call works;
  - the lock path is honoured via the ``ICT_HOURLY_LOCK_PATH`` env
    override so the test does not collide with a real /tmp file.

The hourly-report assembly itself is exercised by S-022 tests; these
tests stub ``build_hourly_report`` and ``send_scheduled`` so we only
exercise the wrapper logic.
"""
from __future__ import annotations

import fcntl
import importlib
import sys
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


@pytest.fixture
def shn(monkeypatch):
    """Return a fresh import of ``send_hourly_now`` with stubbed runtime deps.

    The module imports its runtime dependencies *lazily* inside ``main``,
    so the fixture stubs them via ``sys.modules`` before we call into
    ``main``.
    """
    import send_hourly_now as mod

    importlib.reload(mod)

    fake_outcomes = type(sys)("src.runtime.outcomes")
    fake_outcomes.send_scheduled = lambda msg: None  # type: ignore[attr-defined]
    fake_hr = type(sys)("src.runtime.hourly_report")
    fake_hr.build_hourly_report = lambda **kw: "stubbed report"  # type: ignore[attr-defined]
    # send_hourly_now is now the single hourly producer: it renders ONE
    # combined (strategy + accounts) snapshot via build_combined_hourly_report
    # and sends it (via send_telegram_direct, HTML, with a send_scheduled
    # fallback), then runs the liveness watchdog. Stub all of those
    # lazily-imported deps so the wrapper-logic tests stay offline.
    fake_hr.build_combined_hourly_report = lambda **kw: "stubbed combined report"  # type: ignore[attr-defined]
    fake_hr.build_accounts_hourly_report = lambda **kw: "stubbed accounts report"  # type: ignore[attr-defined]
    fake_notify = type(sys)("src.runtime.notify")
    fake_notify.send_telegram_direct = lambda body, **kw: None  # type: ignore[attr-defined]
    fake_watchdog = type(sys)("src.runtime.liveness_watchdog")
    fake_watchdog.run_liveness_watchdog = lambda **kw: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.runtime.outcomes", fake_outcomes)
    monkeypatch.setitem(sys.modules, "src.runtime.hourly_report", fake_hr)
    monkeypatch.setitem(sys.modules, "src.runtime.notify", fake_notify)
    monkeypatch.setitem(sys.modules, "src.runtime.liveness_watchdog", fake_watchdog)

    return mod


def test_default_lock_path_is_tmp(shn):
    # Sanity: default lives under /tmp so install instructions match.
    assert str(shn.DEFAULT_LOCK_PATH).startswith("/tmp/")


def test_main_succeeds_when_lock_free(shn, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ICT_HOURLY_LOCK_PATH", str(tmp_path / "h.lock"))
    rc = shn.main()
    assert rc == 0
    # Lock file is created so the next caller has something to flock on.
    assert (tmp_path / "h.lock").exists()


def test_main_releases_lock_after_success(shn, tmp_path: Path, monkeypatch):
    """Two non-concurrent calls must both succeed.

    The first call must release the flock on the way out so the second
    call (via a fresh process or, here, a fresh ``main()``) can acquire it.
    """
    monkeypatch.setenv("ICT_HOURLY_LOCK_PATH", str(tmp_path / "h.lock"))
    assert shn.main() == 0
    assert shn.main() == 0


def test_main_returns_75_when_another_holder(shn, tmp_path: Path, monkeypatch):
    """A concurrent run must short-circuit, not block, not double-send."""
    lock_path = tmp_path / "h.lock"
    monkeypatch.setenv("ICT_HOURLY_LOCK_PATH", str(lock_path))

    # Simulate another process holding the lock.
    other = lock_path.open("a+")
    fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        sent: list[str] = []
        fake_outcomes = type(sys)("src.runtime.outcomes")
        fake_outcomes.send_scheduled = lambda msg: sent.append(msg)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "src.runtime.outcomes", fake_outcomes)

        rc = shn.main()
        assert rc == shn.LOCK_BUSY_EXIT_CODE == 75
        assert sent == [], "must NOT dispatch when another holder has the lock"
    finally:
        fcntl.flock(other.fileno(), fcntl.LOCK_UN)
        other.close()


def test_lock_path_override_is_isolated(shn, tmp_path: Path, monkeypatch):
    """Two distinct lock paths must not block each other."""
    a = tmp_path / "a.lock"
    b = tmp_path / "b.lock"

    fh_a = a.open("a+")
    fcntl.flock(fh_a.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        monkeypatch.setenv("ICT_HOURLY_LOCK_PATH", str(b))
        # Path b is unlocked, so a run targeting it must succeed even
        # though path a is busy.
        rc = shn.main()
        assert rc == 0
    finally:
        fcntl.flock(fh_a.fileno(), fcntl.LOCK_UN)
        fh_a.close()


def test_systemd_units_present_in_deploy():
    """Sanity: the timer + service files actually shipped in deploy/."""
    deploy = Path(__file__).resolve().parent.parent / "deploy"
    timer = deploy / "ict-hourly-snapshot.timer"
    service = deploy / "ict-hourly-snapshot.service"
    assert timer.exists(), f"missing {timer}"
    assert service.exists(), f"missing {service}"
    timer_text = timer.read_text()
    service_text = service.read_text()
    # Timer fires hourly with the bounded jitter the runbook documents.
    assert "OnCalendar=hourly" in timer_text
    assert "RandomizedDelaySec=" in timer_text
    # Service ExecStart points at our script and tolerates EX_TEMPFAIL.
    assert "scripts/send_hourly_now.py" in service_text
    assert "SuccessExitStatus=" in service_text and "75" in service_text
