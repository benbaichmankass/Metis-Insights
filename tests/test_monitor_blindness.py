"""Exit-coverage Phase 3 — monitor-blindness surfacing.

A position's PRIMARY exit is its strategy ``monitor()``; the broker SL/TP is a
backstop. When ``monitor()`` can't run (module unresolvable / no monitor() / it
raised) or candles are unavailable, that dynamic exit is "blind". One blind tick
is normal; PERSISTENT blindness is escalated from a silent log to a real alert.

Covers:
  * ``_call_strategy_monitor`` returning a ``(verdict, status)`` pair so the loop
    can tell a healthy ran-no-action tick from a couldn't-run one.
  * ``_track_monitor_blindness`` — per-package consecutive-blind-tick counter
    that alerts once past the threshold and resets on a healthy tick.
"""
from __future__ import annotations

import pytest

from src.runtime import order_monitor as om


def test_call_strategy_monitor_module_unavailable():
    """A strategy whose module can't be imported reports the blindness reason,
    not a silent None."""
    verdict, status = om._call_strategy_monitor(
        "definitely_not_a_real_strategy_xyz", {}, None, {},
    )
    assert verdict is None
    assert status == "module_unavailable"


def test_blindness_no_alert_below_threshold(monkeypatch):
    om._MONITOR_BLINDNESS.clear()
    monkeypatch.setenv("MONITOR_BLINDNESS_ALERT_TICKS", "3")
    alerts: list = []
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_monitor_blindness_alert",
        lambda **k: alerts.append(k),
    )
    # Two blind ticks, threshold 3 → no alert yet.
    for _ in range(2):
        om._track_monitor_blindness(
            pkg_id="pkg-1", strategy="mgc_trend_1h", symbol="MGC",
            blind=True, reason="candles_unavailable",
        )
    assert alerts == []
    assert om._MONITOR_BLINDNESS["pkg-1"]["count"] == 2


def test_blindness_alerts_once_at_threshold(monkeypatch):
    om._MONITOR_BLINDNESS.clear()
    monkeypatch.setenv("MONITOR_BLINDNESS_ALERT_TICKS", "2")
    alerts: list = []
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_monitor_blindness_alert",
        lambda **k: alerts.append(k),
    )
    for _ in range(4):  # well past threshold
        om._track_monitor_blindness(
            pkg_id="pkg-2", strategy="mgc_trend_1h", symbol="MGC",
            blind=True, reason="module_unavailable",
        )
    # Alert fires exactly once (one-shot per blind episode).
    assert len(alerts) == 1
    a = alerts[0]
    assert a["order_package_id"] == "pkg-2"
    assert a["reason"] == "module_unavailable"
    assert a["consecutive_ticks"] == 2
    assert om._MONITOR_BLINDNESS["pkg-2"]["alerted"] is True


def test_blindness_resets_on_healthy_tick(monkeypatch):
    om._MONITOR_BLINDNESS.clear()
    monkeypatch.setenv("MONITOR_BLINDNESS_ALERT_TICKS", "2")
    alerts: list = []
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_monitor_blindness_alert",
        lambda **k: alerts.append(k),
    )
    # One blind tick, then a healthy tick → counter cleared.
    om._track_monitor_blindness(
        pkg_id="pkg-3", strategy="x", symbol="MGC", blind=True, reason="raised",
    )
    om._track_monitor_blindness(
        pkg_id="pkg-3", strategy="x", symbol="MGC", blind=False, reason="ok",
    )
    assert "pkg-3" not in om._MONITOR_BLINDNESS
    # A fresh blind streak restarts at 1 → still below threshold, no alert.
    om._track_monitor_blindness(
        pkg_id="pkg-3", strategy="x", symbol="MGC", blind=True, reason="raised",
    )
    assert alerts == []
    assert om._MONITOR_BLINDNESS["pkg-3"]["count"] == 1


def test_blindness_no_pkg_id_is_noop(monkeypatch):
    om._MONITOR_BLINDNESS.clear()
    om._track_monitor_blindness(
        pkg_id=None, strategy="x", symbol="MGC", blind=True, reason="raised",
    )
    assert om._MONITOR_BLINDNESS == {}
