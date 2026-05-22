"""Tests for the `ibkr_offvm` market_raw adapter (MES intraday via IB gateway).

`_historical_bars` is monkeypatched so CI never imports ib_insync or opens
an IB socket.
"""
from __future__ import annotations

import pytest

from ml.datasets.adapters import IBKRHistoricalMarketRawAdapter, list_adapters
from ml.datasets.adapters.base import CANONICAL_SCHEMA
from ml.datasets.adapters.ibkr_offvm import IB_HIST_ENV, IBHistoricalGuardViolation


def _bars():
    return [
        {"ts": "2024-01-02T14:35:00Z", "open": 4700.0, "high": 4705.0, "low": 4699.0, "close": 4703.0, "volume": 1200.0},
        {"ts": "2024-01-02T14:30:00Z", "open": 4698.0, "high": 4702.0, "low": 4696.0, "close": 4700.0, "volume": 900.0},
        # duplicate boundary bar (chunk overlap) — must be de-duped
        {"ts": "2024-01-02T14:35:00Z", "open": 4700.0, "high": 4705.0, "low": 4699.0, "close": 4703.0, "volume": 1200.0},
    ]


class TestGuard:
    def test_refuses_without_opt_in(self, monkeypatch):
        monkeypatch.delenv(IB_HIST_ENV, raising=False)
        with pytest.raises(IBHistoricalGuardViolation):
            list(IBKRHistoricalMarketRawAdapter().iter_bars(
                symbol="MES", timeframe="5m", start="2024-01-01"))


class TestFetch:
    def _patch(self, monkeypatch, bars, captured=None):
        def fake(cls, **kw):
            if captured is not None:
                captured.update(kw)
            return bars
        monkeypatch.setattr(
            IBKRHistoricalMarketRawAdapter, "_historical_bars", classmethod(fake))

    def test_canonical_sorted_deduped(self, monkeypatch):
        monkeypatch.setenv(IB_HIST_ENV, "1")
        self._patch(monkeypatch, _bars())
        rows = list(IBKRHistoricalMarketRawAdapter().iter_bars(
            symbol="MES", timeframe="5m", start="2024-01-01", end="2024-02-01"))
        assert [r["ts"] for r in rows] == [
            "2024-01-02T14:30:00Z", "2024-01-02T14:35:00Z"]  # sorted + de-duped
        r0 = rows[0]
        assert r0["symbol"] == "MES"
        assert r0["timeframe"] == "5m"
        assert r0["source"] == "ibkr_offvm"
        for name, expected in CANONICAL_SCHEMA.items():
            assert isinstance(r0[name], expected)

    def test_passes_barsize_and_client_id(self, monkeypatch):
        monkeypatch.setenv(IB_HIST_ENV, "1")
        captured: dict = {}
        self._patch(monkeypatch, _bars(), captured)
        list(IBKRHistoricalMarketRawAdapter().iter_bars(
            symbol="MES", timeframe="15m", start="2024-01-01", client_id=450, port=4002))
        assert captured["bar_size"] == "15 mins"
        assert captured["client_id"] == 450
        assert captured["port"] == 4002
        assert captured["symbol"] == "MES"

    def test_unknown_timeframe_raises(self, monkeypatch):
        monkeypatch.setenv(IB_HIST_ENV, "1")
        with pytest.raises(ValueError, match="unsupported timeframe"):
            list(IBKRHistoricalMarketRawAdapter().iter_bars(
                symbol="MES", timeframe="2h", start="2024-01-01"))

    def test_inverted_window_raises(self, monkeypatch):
        monkeypatch.setenv(IB_HIST_ENV, "1")
        with pytest.raises(ValueError, match="must be after start"):
            list(IBKRHistoricalMarketRawAdapter().iter_bars(
                symbol="MES", timeframe="5m", start="2024-02-01", end="2024-01-01"))


def test_registry_includes_ibkr():
    assert "ibkr_offvm" in list_adapters()
