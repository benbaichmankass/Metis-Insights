"""Tests for ``order_monitor._normalize_closed_at_iso`` — the guard that
stops Bybit's epoch-ms ``updatedTime`` from leaking into the canonical ISO
``trades.closed_at`` column (BL-20260617-CLOSEDAT-EPOCH-LEAK; live offender
trade #2631 had closed_at="1781693762762").
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.runtime.order_monitor import _normalize_closed_at_iso


def test_epoch_ms_string_to_iso():
    out = _normalize_closed_at_iso("1781693762762")
    assert out is not None
    assert "-" in out and "T" in out  # ISO-shaped, not a bare integer
    assert datetime.fromisoformat(out) == datetime.fromtimestamp(
        1781693762.762, tz=timezone.utc
    )


def test_iso_passthrough():
    iso = "2026-06-17T16:23:33+00:00"
    assert _normalize_closed_at_iso(iso) == datetime.fromisoformat(iso).isoformat()


def test_sqlite_timestamp_normalized_to_iso():
    out = _normalize_closed_at_iso("2026-06-17 16:23:33")
    assert out is not None
    parsed = datetime.fromisoformat(out)
    assert (parsed.year, parsed.month, parsed.day) == (2026, 6, 17)
    assert parsed.tzinfo is not None  # tz-aware (UTC assumed for SQLite stamps)


def test_epoch_seconds_string_to_iso():
    out = _normalize_closed_at_iso("1781693762")
    assert out is not None
    assert datetime.fromisoformat(out) == datetime.fromtimestamp(
        1781693762, tz=timezone.utc
    )


def test_none_empty_and_garbage_return_none():
    assert _normalize_closed_at_iso(None) is None
    assert _normalize_closed_at_iso("") is None
    assert _normalize_closed_at_iso("   ") is None
    assert _normalize_closed_at_iso("not-a-date-zzz") is None
    assert _normalize_closed_at_iso("0") is None
