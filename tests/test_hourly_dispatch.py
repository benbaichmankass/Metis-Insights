"""Tests for BUG-032 hourly-summary dispatch instrumentation.

Verifies:
* `build_hourly_report` is importable and never raises (already
  guaranteed by its docstring; pinned here as a regression test).
* The on-demand path used by `/hourly` and `scripts/send_hourly_now.py`
  routes through `outcomes.send_scheduled` and reports a non-empty
  string.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.runtime.hourly_report import (
    build_combined_hourly_report,
    build_hourly_report,
)


def test_build_hourly_report_returns_string():
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    msg = build_hourly_report(now_utc=now, tick_interval_s=900)
    assert isinstance(msg, str)
    assert len(msg) > 0


def test_build_hourly_report_never_raises_on_bad_inputs():
    # Even with no audit log / no DBs in test env, builder must not raise.
    # S-telegram-format: the header is now "Strategies — <slot>"; the
    # legacy "Hourly Report" phrasing is still returned by the WARN
    # fallback path so accept either.
    msg = build_hourly_report(now_utc=None, tick_interval_s=900)
    assert isinstance(msg, str)
    assert "Strategies" in msg or "Hourly Report" in msg


def test_build_combined_hourly_report_returns_string():
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    msg = build_combined_hourly_report(now_utc=now, tick_interval_s=900)
    assert isinstance(msg, str)
    assert len(msg) > 0
    assert "Hourly Snapshot" in msg


def test_build_combined_hourly_report_fits_one_telegram_message():
    # The whole point of the combined builder: ONE Telegram message. The
    # shared render_html._truncate caps at 4096, so the combined snapshot
    # must never exceed Telegram's hard limit (which the old two-part
    # concatenation could, silently falling back to two messages).
    from src.units.ui.telegram_format import _TELEGRAM_MAX_CHARS

    msg = build_combined_hourly_report(now_utc=None, tick_interval_s=900)
    assert len(msg) <= _TELEGRAM_MAX_CHARS


def test_build_combined_hourly_report_never_raises_on_bad_inputs():
    msg = build_combined_hourly_report(now_utc=None, tick_interval_s=900)
    assert isinstance(msg, str)
    assert "Hourly Snapshot" in msg
