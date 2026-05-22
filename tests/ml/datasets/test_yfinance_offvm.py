"""Tests for the `yfinance_offvm` market_raw adapter (MES history intake).

Mirrors the Bybit adapter tests: env-gate, canonical row shape, ticker
defaulting, MultiIndex flattening, NaN-row skipping. `_download` is
monkeypatched so CI never touches the network.
"""
from __future__ import annotations

import pandas as pd
import pytest

from ml.datasets.adapters import (
    YFinanceOffvmMarketRawAdapter,
    list_adapters,
)
from ml.datasets.adapters.base import CANONICAL_SCHEMA
from ml.datasets.adapters.bybit_offvm import OFFVM_ENV, OffVmGuardrailViolation


def _frame(multiindex: bool = False, with_nan: bool = False) -> pd.DataFrame:
    idx = pd.to_datetime(
        ["2020-01-02", "2020-01-03", "2020-01-06"], utc=True
    )
    data = {
        "Open": [3200.0, 3210.0, 3220.0],
        "High": [3250.0, 3260.0, 3270.0],
        "Low": [3180.0, 3190.0, 3200.0],
        "Close": [3240.0, 3255.0, (float("nan") if with_nan else 3265.0)],
        "Volume": [100000.0, 120000.0, 110000.0],
    }
    df = pd.DataFrame(data, index=idx)
    if multiindex:
        df.columns = pd.MultiIndex.from_product([df.columns, ["ES=F"]])
    return df


class TestEnvGate:
    def test_refuses_without_env(self, monkeypatch):
        monkeypatch.delenv(OFFVM_ENV, raising=False)
        with pytest.raises(OffVmGuardrailViolation):
            list(
                YFinanceOffvmMarketRawAdapter().iter_bars(
                    symbol="MES", timeframe="1d", start="2020-01-01"
                )
            )

    def test_refuses_with_wrong_env(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "nope")
        with pytest.raises(OffVmGuardrailViolation):
            list(
                YFinanceOffvmMarketRawAdapter().iter_bars(
                    symbol="MES", timeframe="1d", start="2020-01-01"
                )
            )


class TestFetch:
    def _patch_download(self, monkeypatch, frame, captured=None):
        def fake(cls, *, ticker, interval, start, end):
            if captured is not None:
                captured.update(
                    {"ticker": ticker, "interval": interval, "start": start, "end": end}
                )
            return frame

        monkeypatch.setattr(
            YFinanceOffvmMarketRawAdapter,
            "_download",
            classmethod(fake),
        )

    def test_canonical_rows(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        self._patch_download(monkeypatch, _frame())
        rows = list(
            YFinanceOffvmMarketRawAdapter().iter_bars(
                symbol="MES", timeframe="1d", start="2020-01-01", end="2020-01-07"
            )
        )
        assert len(rows) == 3
        assert rows[0]["ts"] == "2020-01-02T00:00:00Z"
        assert rows[0]["symbol"] == "MES"
        assert rows[0]["timeframe"] == "1d"
        assert rows[0]["open"] == 3200.0
        assert rows[0]["close"] == 3240.0
        assert rows[0]["volume"] == 100000.0
        assert rows[0]["source"] == "yfinance_offvm"
        for name, expected in CANONICAL_SCHEMA.items():
            assert isinstance(rows[0][name], expected)

    def test_default_ticker_is_es_future(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        captured: dict = {}
        self._patch_download(monkeypatch, _frame(), captured)
        list(
            YFinanceOffvmMarketRawAdapter().iter_bars(
                symbol="MES", timeframe="1d", start="2020-01-01"
            )
        )
        assert captured["ticker"] == "ES=F"
        assert captured["interval"] == "1d"

    def test_explicit_ticker_override(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        captured: dict = {}
        self._patch_download(monkeypatch, _frame(), captured)
        list(
            YFinanceOffvmMarketRawAdapter().iter_bars(
                symbol="MES", timeframe="1d", start="2020-01-01", ticker="^GSPC"
            )
        )
        assert captured["ticker"] == "^GSPC"

    def test_multiindex_flattened(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        self._patch_download(monkeypatch, _frame(multiindex=True))
        rows = list(
            YFinanceOffvmMarketRawAdapter().iter_bars(
                symbol="MES", timeframe="1d", start="2020-01-01"
            )
        )
        assert len(rows) == 3
        assert rows[1]["close"] == 3255.0

    def test_nan_rows_skipped(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        self._patch_download(monkeypatch, _frame(with_nan=True))
        rows = list(
            YFinanceOffvmMarketRawAdapter().iter_bars(
                symbol="MES", timeframe="1d", start="2020-01-01"
            )
        )
        assert len(rows) == 2  # the NaN-close row is dropped

    def test_empty_frame_yields_nothing(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        self._patch_download(monkeypatch, pd.DataFrame())
        rows = list(
            YFinanceOffvmMarketRawAdapter().iter_bars(
                symbol="MES", timeframe="1d", start="2020-01-01"
            )
        )
        assert rows == []

    def test_unknown_timeframe_raises(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        with pytest.raises(ValueError, match="unsupported timeframe"):
            list(
                YFinanceOffvmMarketRawAdapter().iter_bars(
                    symbol="MES", timeframe="3h", start="2020-01-01"
                )
            )


def test_registry_includes_yfinance():
    assert "yfinance_offvm" in list_adapters()
