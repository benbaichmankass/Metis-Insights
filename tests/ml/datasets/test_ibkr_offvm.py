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

    def test_resolves_exchange_per_symbol(self, monkeypatch):
        """MES->CME, MGC/MHG->COMEX; an unknown symbol keeps the passed exchange.

        Regression for the metals sleeve: an MGC/MHG pull was sent to CME
        (the default) and IBKR returned Error 200 "No security definition",
        so the metals sleeve could never be backfilled on its native contract.
        """
        monkeypatch.setenv(IB_HIST_ENV, "1")
        for symbol, expected in [("MES", "CME"), ("MGC", "COMEX"), ("MHG", "COMEX")]:
            captured: dict = {}
            self._patch(monkeypatch, _bars(), captured)
            list(IBKRHistoricalMarketRawAdapter().iter_bars(
                symbol=symbol, timeframe="1h", start="2024-01-01"))
            assert captured["exchange"] == expected, symbol
            assert captured["symbol"] == symbol
        # an unknown symbol falls back to the caller-supplied exchange
        captured = {}
        self._patch(monkeypatch, _bars(), captured)
        list(IBKRHistoricalMarketRawAdapter().iter_bars(
            symbol="ZZZ", timeframe="1h", start="2024-01-01", exchange="NYMEX"))
        assert captured["exchange"] == "NYMEX"

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


# ---------------------------------------------------------------------------
# Real _historical_bars over a fake ib_insync — regression for the DAILY pull.
# IB daily bars (formatDate=2) return a datetime.date, not a datetime; the
# old _to_dt called date.replace(tzinfo=...) which raises TypeError. This was
# never hit until the native-MES 1d pull (2026-06-01). Also covers the
# per-request resilience: one hung/dead contract must not abort the stitch.
# ---------------------------------------------------------------------------
import sys  # noqa: E402
import types  # noqa: E402
from datetime import date, datetime, timezone  # noqa: E402


class _FakeBar:
    def __init__(self, d, o, h, lo, c, v):
        self.date = d  # a datetime.date for daily bars (the bug trigger)
        self.open, self.high, self.low, self.close, self.volume = o, h, lo, c, v


class _FakeContract:
    def __init__(self, expiry):
        self.lastTradeDateOrContractMonth = expiry


class _FakeDetails:
    def __init__(self, contract):
        self.contract = contract


def _install_fake_ib(monkeypatch, *, hist_side_effects):
    """Inject a fake ib_insync whose reqHistoricalData yields per-call results.

    `hist_side_effects` is a list consumed FIFO; each element is either a list
    of bars to return or an Exception instance to raise (simulating a timeout).
    """
    calls = {"n": 0}

    class _FakeIB:
        def connect(self, *a, **k):
            self._connected = True

        def isConnected(self):
            return True

        def reqMarketDataType(self, *a, **k):
            return None

        def reqContractDetails(self, *a, **k):
            # one dated MES contract is enough to drive the stitch loop
            return [_FakeDetails(_FakeContract("20240315"))]

        def reqHistoricalData(self, *a, **k):
            i = calls["n"]
            calls["n"] += 1
            eff = hist_side_effects[i] if i < len(hist_side_effects) else []
            if isinstance(eff, Exception):
                raise eff
            return eff

        def disconnect(self):
            self._connected = False

    mod = types.ModuleType("ib_insync")
    mod.IB = _FakeIB
    mod.Future = lambda *a, **kw: _FakeContract(kw.get("lastTradeDateOrContractMonth", ""))
    monkeypatch.setitem(sys.modules, "ib_insync", mod)
    return calls


def _run_hist(**overrides):
    params = dict(
        symbol="MES", exchange="CME", currency="USD", bar_size="1 day",
        chunk_days=365, start_dt=datetime(2020, 1, 1, tzinfo=timezone.utc),
        end_dt=datetime(2024, 12, 31, tzinfo=timezone.utc), host="127.0.0.1",
        port=4002, client_id=450, what_to_show="TRADES", use_rth=False,
        pause_s=0, max_contracts=4,
    )
    params.update(overrides)
    return IBKRHistoricalMarketRawAdapter._historical_bars(**params)


def test_daily_date_bars_do_not_raise(monkeypatch):
    """A daily bar whose .date is a datetime.date is promoted to midnight UTC."""
    daily = [
        _FakeBar(date(2024, 1, 2), 4700, 4710, 4695, 4705, 1000),
        _FakeBar(date(2024, 1, 3), 4705, 4720, 4700, 4715, 1100),
    ]
    # return the bars once, then [] so the stitch loop terminates
    _install_fake_ib(monkeypatch, hist_side_effects=[daily, []])
    out = _run_hist()
    ts = sorted(r["ts"] for r in out)
    assert ts == ["2024-01-02T00:00:00Z", "2024-01-03T00:00:00Z"]  # midnight UTC


def test_request_timeout_on_one_contract_is_swallowed(monkeypatch):
    """A reqHistoricalData timeout must not abort the whole pull (resilience)."""
    _install_fake_ib(monkeypatch, hist_side_effects=[TimeoutError("paced out")])
    out = _run_hist()  # must not raise
    assert out == []
