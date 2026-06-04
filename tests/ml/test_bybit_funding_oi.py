"""Tests for the Bybit funding-rate + open-interest fetcher (S-MLOPT-S11).

Mocks the exchange object (injected, not monkeypatched onto ccxt) so CI never
touches the network — same discipline as test_bybit_offvm.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ml.datasets.adapters import bybit_funding_oi as fo


def _ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


class _FakeBybit:
    """Returns the full page on the first call per endpoint, then empties."""

    def __init__(self, funding: list[dict], oi: list[dict]):
        self._funding = funding
        self._oi = oi
        self.funding_calls = 0
        self.oi_calls = 0

    def fetch_funding_rate_history(self, symbol, since=None, limit=None):
        self.funding_calls += 1
        return list(self._funding) if self.funding_calls == 1 else []

    def fetch_open_interest_history(self, symbol, timeframe, since=None, limit=None):
        self.oi_calls += 1
        return list(self._oi) if self.oi_calls == 1 else []


def test_offvm_guard_blocks_without_env(monkeypatch):
    monkeypatch.delenv("ICT_OFFVM_BUILD_HOST", raising=False)
    with pytest.raises(fo.OffVmGuardrailViolation):
        fo.fetch_funding_oi_rows(
            symbol="BTCUSDT", start="2025-01-01", end="2025-01-02",
            exchange=_FakeBybit([], []),
        )


def test_merge_funding_and_oi(monkeypatch):
    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    funding = [
        {"timestamp": _ms("2025-01-01T00:00:00Z"), "fundingRate": 0.0001},
        {"timestamp": _ms("2025-01-01T08:00:00Z"), "fundingRate": -0.0002},
        {"timestamp": _ms("2025-01-01T16:00:00Z"), "fundingRate": 0.0003},
    ]
    oi = [
        {"timestamp": _ms("2025-01-01T00:00:00Z"), "openInterestAmount": 1000.0},
        {"timestamp": _ms("2025-01-01T01:00:00Z"), "openInterestAmount": 1010.0},
        {"timestamp": _ms("2025-01-01T02:00:00Z"), "openInterestAmount": 1020.0},
    ]
    rows = fo.fetch_funding_oi_rows(
        symbol="BTCUSDT", start="2025-01-01", end="2025-01-02",
        oi_interval="1h", exchange=_FakeBybit(funding, oi),
    )
    # 3 funding + 3 OI rows, ts-sorted ascending.
    assert len(rows) == 6
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)
    funding_rows = [r for r in rows if r["funding_rate"] is not None]
    oi_rows = [r for r in rows if r["open_interest"] is not None]
    assert len(funding_rows) == 3 and len(oi_rows) == 3
    # funding rows never carry OI and vice versa.
    assert all(r["open_interest"] is None for r in funding_rows)
    assert all(r["funding_rate"] is None for r in oi_rows)
    assert funding_rows[0]["funding_rate"] == 0.0001
    assert oi_rows[-1]["open_interest"] == 1020.0
    assert all(r["symbol"] == "BTCUSDT" for r in rows)


def test_oi_value_fallback(monkeypatch):
    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    oi = [{"timestamp": _ms("2025-01-01T00:00:00Z"), "openInterestValue": 5.0e6}]
    rows = fo.fetch_funding_oi_rows(
        symbol="BTCUSDT", start="2025-01-01", end="2025-01-02",
        exchange=_FakeBybit([], oi),
    )
    assert len(rows) == 1 and rows[0]["open_interest"] == 5.0e6


def test_bad_oi_interval_raises(monkeypatch):
    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    with pytest.raises(ValueError):
        fo.fetch_funding_oi_rows(
            symbol="BTCUSDT", start="2025-01-01", end="2025-01-02",
            oi_interval="7m", exchange=_FakeBybit([], []),
        )


def test_end_before_start_raises(monkeypatch):
    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    with pytest.raises(ValueError):
        fo.fetch_funding_oi_rows(
            symbol="BTCUSDT", start="2025-01-02", end="2025-01-01",
            exchange=_FakeBybit([], []),
        )
