"""The active-close marker is TIME-bounded, not tick-bounded.

DECOUPLE PREREQUISITE. `_TICK_ACTIVE_CLOSE_SYMBOLS` was a per-tick set: the exit
loop WROTE it and the reconcilers READ it, sound only while both ran inside one
`run_monitor_tick`. Moving the exit loop to its own cadence breaks that without
failing loudly — the protective re-arm just quietly resumes fighting an in-flight
close, which is BL-20260708-ALPACA-REARM-VS-CLOSE-FIGHT coming back.

These cover the property the split depends on: a marker written by the exit half
is visible to a reconciler half running LATER, and expires on its own.
"""
from __future__ import annotations

import importlib

import pytest

om = importlib.import_module("src.runtime.order_monitor")


@pytest.fixture(autouse=True)
def _clean():
    om._TICK_ACTIVE_CLOSE_AT.clear()
    yield
    om._TICK_ACTIVE_CLOSE_AT.clear()


def test_marker_is_visible_after_it_is_set():
    om.mark_active_close("alpaca_paper", "QQQ")
    assert om.is_active_close("alpaca_paper", "QQQ")


def test_marker_survives_across_passes():
    """THE decouple-enabling property. No per-tick clear may wipe it — the exit
    half's marker has to outlive its own pass to reach the reconciler half."""
    om.mark_active_close("alpaca_paper", "QQQ")
    # Two full monitor passes' worth of unrelated activity.
    om.mark_active_close("bybit_2", "XRPUSDT")
    om.is_active_close("ib_paper", "MHG")
    assert om.is_active_close("alpaca_paper", "QQQ"), (
        "marker did not survive — a per-tick clear has been reintroduced")


def test_symbol_is_matched_case_insensitively():
    om.mark_active_close("alpaca_paper", "qqq")
    assert om.is_active_close("alpaca_paper", "QQQ")


def test_an_unmarked_key_is_not_active():
    om.mark_active_close("alpaca_paper", "QQQ")
    assert not om.is_active_close("alpaca_paper", "SPY")
    assert not om.is_active_close("alpaca_live", "QQQ")


def test_marker_expires_after_the_window(monkeypatch):
    """A close that FAILS permanently must resume being re-armed — an
    un-re-armed naked position is the condition the sweep exists to correct, so
    the suppression is deliberately temporary."""
    monkeypatch.setenv("ACTIVE_CLOSE_WINDOW_S", "60")
    t = [1000.0]
    monkeypatch.setattr(om.time, "monotonic", lambda: t[0])
    om.mark_active_close("alpaca_paper", "QQQ")
    t[0] = 1059.0
    assert om.is_active_close("alpaca_paper", "QQQ"), "expired early"
    t[0] = 1061.0
    assert not om.is_active_close("alpaca_paper", "QQQ"), "did not expire"


def test_expired_keys_are_pruned_so_the_map_cannot_grow_with_uptime(monkeypatch):
    monkeypatch.setenv("ACTIVE_CLOSE_WINDOW_S", "10")
    t = [0.0]
    monkeypatch.setattr(om.time, "monotonic", lambda: t[0])
    for i in range(50):
        t[0] = float(i)
        om.mark_active_close("acct", f"SYM{i}")
    t[0] = 1000.0
    om.is_active_close("acct", "SYM0")      # triggers the prune
    assert om._TICK_ACTIVE_CLOSE_AT == {}, "expired keys were not pruned"


@pytest.mark.parametrize("bad", ["", "0", "-5", "abc", "none"])
def test_an_unusable_window_falls_back_rather_than_disabling(monkeypatch, bad):
    """A typo must not silently switch suppression OFF — that would re-enable
    the re-arm fight (the EXPOSURE_SOAK_SECONDS discipline)."""
    monkeypatch.setenv("ACTIVE_CLOSE_WINDOW_S", bad)
    assert om._active_close_window_s() == om._ACTIVE_CLOSE_WINDOW_S_DEFAULT


def test_default_window_covers_a_slow_close():
    """IB_CLOSE_CONFIRM_S is 6s and Alpaca's cancel-then-flatten is slower, so
    the default has to clear both by a wide margin."""
    assert om._ACTIVE_CLOSE_WINDOW_S_DEFAULT >= 60.0


def test_concurrent_marking_does_not_lose_an_entry():
    """Two threads = the whole point of the decouple. `dict` mutation is
    GIL-atomic but the prune is a read-modify-write, so the lock is load-bearing."""
    import threading
    def worker(lo: int) -> None:
        for i in range(lo, lo + 200):
            om.mark_active_close("acct", f"S{i}")
    threads = [threading.Thread(target=worker, args=(n * 200,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(om._TICK_ACTIVE_CLOSE_AT) == 800
