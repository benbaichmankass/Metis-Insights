"""Tests for the yfinance macro fetcher (S-MLOPT-S12, Phase 2.4).

Monkeypatches the `_download` hook so CI never touches the network — same
discipline as the OHLCV yfinance adapter + the Bybit funding fetcher.
"""
from __future__ import annotations

import pytest

from ml.datasets.adapters import yfinance_macro as ym
from ml.datasets.macro_features import MACRO_FEATURE_COLUMNS


class _FakeFrame:
    """Minimal stand-in for a single-level yfinance OHLCV DataFrame."""

    def __init__(self, rows: list[tuple[str, float]]):
        # rows = [(date_iso, close), ...]
        self._rows = rows
        self.columns = ["Open", "High", "Low", "Close", "Volume"]

    def __len__(self):
        return len(self._rows)

    def iterrows(self):
        for date_iso, close in self._rows:
            yield date_iso, {"Open": close, "High": close, "Low": close,
                             "Close": close, "Volume": 0.0}


def _fake_download_factory(series_by_ticker: dict[str, list[tuple[str, float]]]):
    def _fake(*, ticker: str, start: str, end: str | None):
        return _FakeFrame(series_by_ticker.get(ticker, []))
    return _fake


def test_offvm_guard_blocks_without_env(monkeypatch):
    monkeypatch.delenv("ICT_OFFVM_BUILD_HOST", raising=False)
    with pytest.raises(ym.OffVmGuardrailViolation):
        ym.fetch_macro_rows(start="2025-01-01", end="2025-02-01")


def test_fetch_merges_and_computes(monkeypatch):
    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    dates = [f"2025-01-{i + 1:02d}T00:00:00Z" for i in range(30)]
    series = {
        "^VIX": [(d, 15.0 + i % 5) for i, d in enumerate(dates)],
        "^VIX3M": [(d, 16.0 + i % 3) for i, d in enumerate(dates)],
        "DX-Y.NYB": [(d, 100.0 + i * 0.1) for i, d in enumerate(dates)],
        "^TNX": [(d, 40.0 + i * 0.05) for i, d in enumerate(dates)],
        "^IRX": [(d, 38.0) for i, d in enumerate(dates)],
    }
    monkeypatch.setattr(ym, "_download", _fake_download_factory(series))

    rows = ym.fetch_macro_rows(start="2025-01-01", end="2025-02-01", zscore_window_n=10)
    assert rows
    for r in rows:
        assert "ts" in r
        for c in MACRO_FEATURE_COLUMNS:
            assert c in r and isinstance(r[c], float)
    # First feature row stamped one day after the first observed date (leakage lag).
    assert rows[0]["ts"] == "2025-01-02T00:00:00Z"


def test_partial_series_does_not_crash(monkeypatch):
    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    dates = [f"2025-03-{i + 1:02d}T00:00:00Z" for i in range(12)]
    # Only VIX + DXY available; rates/vix3m absent → those features degrade to 0.0.
    series = {
        "^VIX": [(d, 20.0) for d in dates],
        "DX-Y.NYB": [(d, 101.0 + i * 0.2) for i, d in enumerate(dates)],
    }
    monkeypatch.setattr(ym, "_download", _fake_download_factory(series))
    rows = ym.fetch_macro_rows(start="2025-03-01", end="2025-04-01")
    assert rows
    for r in rows:
        assert r["vix_term_slope"] == 0.0
        assert r["ust_slope_3m10y"] == 0.0
