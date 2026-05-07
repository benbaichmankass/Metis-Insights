"""Tests for src/runtime/outcomes.py — S-022 PR1.

Covers:
  * Severity routing (info → no telegram; warn → log only; error/critical → telegram)
  * Per-fingerprint rate limit (5 min default)
  * CRITICAL bypasses per-fingerprint limit but still counts to hourly cap
  * Hourly cap suppresses further telegram alerts
  * Telegram-send failure falls through to outcomes_pending.jsonl
  * Suppressed-count is appended to the next message that gets through
  * AlertsQueue receives every report regardless of telegram outcome
  * report() never raises — even when AlertsQueue and Telegram both blow up
  * send_scheduled() bypasses both rate limits
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

for _mod in ("dotenv",):
    sys.modules.setdefault(_mod, MagicMock())

from src.runtime.outcomes import Level, _Config, _reset_for_tests, report, send_scheduled


@pytest.fixture
def tmp_paths(tmp_path: Path) -> _Config:
    cfg = _Config(
        rate_limit_window_s=300.0,
        hourly_cap=30,
        outcomes_log=tmp_path / "outcomes.jsonl",
        pending_queue=tmp_path / "outcomes_pending.jsonl",
    )
    _reset_for_tests(cfg)
    yield cfg
    _reset_for_tests()


@pytest.fixture
def clear_alerts():
    from src.units.dashboards.alerts import clear_alerts as _clear

    _clear()
    yield
    _clear()


# ---------------------------------------------------------------------------
# Severity routing
# ---------------------------------------------------------------------------


def test_info_does_not_persist_or_telegram(tmp_paths: _Config, clear_alerts):
    with patch("src.runtime.notify.send_via_alert_manager") as send:
        report("tick", "ok", level=Level.INFO, foo="bar")
    send.assert_not_called()
    assert not tmp_paths.outcomes_log.exists()


def test_warn_persists_but_no_telegram(tmp_paths: _Config, clear_alerts):
    with patch("src.runtime.notify.send_via_alert_manager") as send:
        report("db_write", "stale", level=Level.WARN, reason="lock contended")
    send.assert_not_called()
    lines = tmp_paths.outcomes_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["level"] == "warn"
    assert rec["status"] == "stale"


def test_error_sends_telegram_and_persists(tmp_paths: _Config, clear_alerts):
    with patch("src.runtime.notify.send_via_alert_manager") as send:
        report("order_submit", "failed_exchange", level=Level.ERROR,
               reason="bybit 503", symbol="BTCUSDT")
    send.assert_called_once()
    msg = send.call_args[0][0]
    assert "[ERROR]" in msg
    assert "order_submit" in msg
    assert "failed_exchange" in msg
    assert "bybit 503" in msg
    assert tmp_paths.outcomes_log.exists()


def test_critical_persists_and_sends(tmp_paths: _Config, clear_alerts):
    with patch("src.runtime.notify.send_via_alert_manager") as send:
        report("tick", "exception", level=Level.CRITICAL, reason="KeyError")
    send.assert_called_once()
    assert "[CRITICAL]" in send.call_args[0][0]


# ---------------------------------------------------------------------------
# Rate limit — per-fingerprint
# ---------------------------------------------------------------------------


def test_same_fingerprint_within_window_suppressed(tmp_paths: _Config, clear_alerts):
    with patch("src.runtime.notify.send_via_alert_manager") as send:
        report("order_submit", "failed_exchange", level=Level.ERROR, reason="bybit 503")
        report("order_submit", "failed_exchange", level=Level.ERROR, reason="bybit 503")
        report("order_submit", "failed_exchange", level=Level.ERROR, reason="bybit 503")
    assert send.call_count == 1


def test_different_fingerprint_not_suppressed(tmp_paths: _Config, clear_alerts):
    with patch("src.runtime.notify.send_via_alert_manager") as send:
        report("order_submit", "failed_exchange", level=Level.ERROR, reason="bybit 503")
        report("order_submit", "failed_exchange", level=Level.ERROR, reason="bybit 504")
        report("db_write", "failed", level=Level.ERROR, reason="locked")
    assert send.call_count == 3


def test_suppressed_count_appended_to_next_message(tmp_paths: _Config, clear_alerts):
    with patch("src.runtime.notify.send_via_alert_manager") as send, \
            patch("src.runtime.outcomes.time.time") as now:
        now.return_value = 1000.0
        report("x", "fail", level=Level.ERROR, reason="r")
        now.return_value = 1010.0
        report("x", "fail", level=Level.ERROR, reason="r")  # suppressed
        now.return_value = 1020.0
        report("x", "fail", level=Level.ERROR, reason="r")  # suppressed
        # Move past the window
        now.return_value = 1400.0
        report("x", "fail", level=Level.ERROR, reason="r")  # sends, with suppress count
    assert send.call_count == 2
    second = send.call_args_list[1][0][0]
    assert "+2 suppressed" in second


def test_critical_bypasses_per_fingerprint_limit(tmp_paths: _Config, clear_alerts):
    with patch("src.runtime.notify.send_via_alert_manager") as send:
        report("tick", "exception", level=Level.CRITICAL, reason="KeyError")
        report("tick", "exception", level=Level.CRITICAL, reason="KeyError")
        report("tick", "exception", level=Level.CRITICAL, reason="KeyError")
    assert send.call_count == 3


# ---------------------------------------------------------------------------
# Rate limit — hourly cap
# ---------------------------------------------------------------------------


def test_hourly_cap_suppresses_further(tmp_path: Path, clear_alerts):
    cfg = _Config(
        rate_limit_window_s=0.0,  # disable per-fingerprint dedup
        hourly_cap=3,
        outcomes_log=tmp_path / "outcomes.jsonl",
        pending_queue=tmp_path / "pending.jsonl",
    )
    _reset_for_tests(cfg)
    try:
        with patch("src.runtime.notify.send_via_alert_manager") as send:
            for i in range(5):
                report(f"x{i}", "fail", level=Level.ERROR, reason=f"r{i}")
        assert send.call_count == 3
    finally:
        _reset_for_tests()


def test_hourly_cap_caps_critical_too(tmp_path: Path, clear_alerts):
    cfg = _Config(
        rate_limit_window_s=0.0,
        hourly_cap=2,
        outcomes_log=tmp_path / "outcomes.jsonl",
        pending_queue=tmp_path / "pending.jsonl",
    )
    _reset_for_tests(cfg)
    try:
        with patch("src.runtime.notify.send_via_alert_manager") as send:
            for i in range(4):
                report(f"x{i}", "fail", level=Level.CRITICAL, reason=f"r{i}")
        assert send.call_count == 2
    finally:
        _reset_for_tests()


# ---------------------------------------------------------------------------
# Fallback queue when Telegram fails
# ---------------------------------------------------------------------------


def test_telegram_failure_falls_through_to_pending_queue(
    tmp_paths: _Config, clear_alerts
):
    def _boom(_msg: str) -> None:
        raise RuntimeError("network down")

    with patch("src.runtime.notify.send_via_alert_manager", side_effect=_boom):
        report("order_submit", "failed_exchange", level=Level.ERROR, reason="bybit 503")

    assert tmp_paths.pending_queue.exists()
    lines = tmp_paths.pending_queue.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert "[ERROR]" in entry["message"]
    assert entry["scheduled"] is False


# ---------------------------------------------------------------------------
# AlertsQueue integration
# ---------------------------------------------------------------------------


def test_alerts_queue_receives_every_report(tmp_paths: _Config, clear_alerts):
    from src.units.dashboards.alerts import list_alerts

    with patch("src.runtime.notify.send_via_alert_manager"):
        report("a", "ok", level=Level.INFO)
        report("b", "warn", level=Level.WARN, reason="r")
        report("c", "fail", level=Level.ERROR, reason="r")
    items = list_alerts()
    assert len(items) == 3
    assert {a["level"] for a in items} == {"info", "warn", "error"}


# ---------------------------------------------------------------------------
# Robustness — never raises
# ---------------------------------------------------------------------------


def test_report_never_raises_when_alerts_and_telegram_both_break(
    tmp_paths: _Config, clear_alerts
):
    with patch(
        "src.units.dashboards.alerts.push_alert", side_effect=RuntimeError("boom")
    ), patch(
        "src.runtime.notify.send_via_alert_manager", side_effect=RuntimeError("boom")
    ):
        # Must not raise
        rec = report("x", "fail", level=Level.ERROR, reason="r")
    assert rec["action"] == "x"


def test_report_accepts_string_level(tmp_paths: _Config, clear_alerts):
    with patch("src.runtime.notify.send_via_alert_manager") as send:
        report("x", "fail", level="error", reason="r")
    send.assert_called_once()


# ---------------------------------------------------------------------------
# Scheduled messages
# ---------------------------------------------------------------------------


def test_scheduled_bypasses_rate_limit(tmp_path: Path, clear_alerts):
    cfg = _Config(
        rate_limit_window_s=300.0,
        hourly_cap=2,  # would block ERROR after 2
        outcomes_log=tmp_path / "outcomes.jsonl",
        pending_queue=tmp_path / "pending.jsonl",
    )
    _reset_for_tests(cfg)
    try:
        with patch("src.runtime.notify.send_via_alert_manager") as send:
            # Fill the hourly cap with errors
            report("x", "fail", level=Level.ERROR, reason="r1")
            report("y", "fail", level=Level.ERROR, reason="r2")
            # Scheduled should still go through despite cap
            send_scheduled("hourly summary")
            send_scheduled("another hourly")
        assert send.call_count == 4
    finally:
        _reset_for_tests()


def test_scheduled_falls_through_to_pending_on_failure(tmp_path: Path, clear_alerts):
    cfg = _Config(
        outcomes_log=tmp_path / "outcomes.jsonl",
        pending_queue=tmp_path / "pending.jsonl",
    )
    _reset_for_tests(cfg)
    try:
        with patch(
            "src.runtime.notify.send_via_alert_manager", side_effect=RuntimeError("net")
        ):
            send_scheduled("hourly summary")
        lines = cfg.pending_queue.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["scheduled"] is True
        assert entry["message"] == "hourly summary"
    finally:
        _reset_for_tests()
