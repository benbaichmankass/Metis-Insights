"""Tests for the keyless FRED macro fetcher (M19 corpus C0).

Monkeypatches the `_download` hook so CI never touches the network — same
discipline as the yfinance macro adapter it mirrors. The adapter + the compute it
feeds are stdlib-only, so these run without pandas/numpy.
"""
from __future__ import annotations

import pytest

from ml.datasets.adapters import fred_macro as fm
from ml.datasets.macro_features import MACRO_FEATURE_COLUMNS


def _fred_csv(series_id: str, rows: list[tuple[str, str]]) -> str:
    """A minimal FRED `fredgraph.csv` body: header + `date,value` rows."""
    lines = [f"observation_date,{series_id}"]
    lines += [f"{date},{value}" for date, value in rows]
    return "\n".join(lines) + "\n"


def _fake_download_factory(csv_by_series: dict[str, list[tuple[str, str]]]):
    def _fake(*, series_id: str, start: str, end: str | None):
        return _fred_csv(series_id, csv_by_series.get(series_id, []))

    return _fake


def test_offvm_guard_blocks_without_env(monkeypatch):
    monkeypatch.delenv("ICT_OFFVM_BUILD_HOST", raising=False)
    with pytest.raises(fm.OffVmGuardrailViolation):
        fm.fetch_fred_macro_rows(start="2025-01-01", end="2025-02-01")


def test_fetch_merges_and_computes(monkeypatch):
    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    dates = [f"2025-01-{i + 1:02d}" for i in range(30)]
    csv_by_series = {
        "VIXCLS": [(d, f"{15.0 + i % 5:.2f}") for i, d in enumerate(dates)],
        "VXVCLS": [(d, f"{16.0 + i % 3:.2f}") for i, d in enumerate(dates)],
        "DTWEXBGS": [(d, f"{100.0 + i * 0.1:.3f}") for i, d in enumerate(dates)],
        "DGS10": [(d, f"{4.0 + i * 0.01:.2f}") for i, d in enumerate(dates)],
        "DGS3MO": [(d, "3.80") for d in dates],
    }
    monkeypatch.setattr(fm, "_download", _fake_download_factory(csv_by_series))

    rows = fm.fetch_fred_macro_rows(start="2025-01-01", end="2025-02-01", zscore_window_n=10)
    assert rows
    for r in rows:
        assert "ts" in r
        for c in MACRO_FEATURE_COLUMNS:
            assert c in r and isinstance(r[c], float)
    # First feature row stamped one day after the first observed date (leakage lag).
    assert rows[0]["ts"] == "2025-01-02T00:00:00Z"
    # Rates leg actually populated (the headline motivation).
    assert any(r["ust10y_level"] != 0.0 for r in rows)
    assert any(r["ust_slope_3m10y"] != 0.0 for r in rows)


def test_missing_period_token_skipped(monkeypatch):
    """FRED marks a holiday/missing reading with a bare '.', which we skip."""
    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    dates = [f"2025-02-{i + 1:02d}" for i in range(15)]
    # DGS10 has a '.' hole mid-series; it must not crash or become 0-valued.
    dgs10 = [(d, "4.10" if i != 5 else ".") for i, d in enumerate(dates)]
    csv_by_series = {
        "VIXCLS": [(d, "18.00") for d in dates],
        "DTWEXBGS": [(d, f"{101.0 + i * 0.2:.3f}") for i, d in enumerate(dates)],
        "DGS10": dgs10,
        "DGS3MO": [(d, "4.00") for d in dates],
    }
    monkeypatch.setattr(fm, "_download", _fake_download_factory(csv_by_series))
    rows = fm.fetch_fred_macro_rows(start="2025-02-01", end="2025-03-01")
    assert rows
    # The hole date (2025-02-06 close → stamped 02-07) has no DGS10 reading, so its
    # ust10y_level falls back to neutral 0.0 — but the surrounding days are populated.
    populated = [r for r in rows if r["ust10y_level"] != 0.0]
    assert populated


def test_partial_series_does_not_crash(monkeypatch):
    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    dates = [f"2025-03-{i + 1:02d}" for i in range(12)]
    # Only VIX + DXY available; rates/vix3m absent → those features degrade to 0.0.
    csv_by_series = {
        "VIXCLS": [(d, "20.00") for d in dates],
        "DTWEXBGS": [(d, f"{101.0 + i * 0.2:.3f}") for i, d in enumerate(dates)],
    }
    monkeypatch.setattr(fm, "_download", _fake_download_factory(csv_by_series))
    rows = fm.fetch_fred_macro_rows(start="2025-03-01", end="2025-04-01")
    assert rows
    for r in rows:
        assert r["vix_term_slope"] == 0.0
        assert r["ust_slope_3m10y"] == 0.0


def test_fetch_raw_series_shape(monkeypatch):
    """The raw-series helper returns ascending {date,value} rows per series (corpus feed)."""
    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    dates = [f"2025-05-{i + 1:02d}" for i in range(6)]
    csv_by_series = {
        "DGS10": [(d, f"{4.0 + i * 0.01:.2f}") for i, d in enumerate(dates)],
        "VIXCLS": [(d, "18.00") for d in dates],
    }
    monkeypatch.setattr(fm, "_download", _fake_download_factory(csv_by_series))
    # `series=` merges with DEFAULT_SERIES (caller overrides win) — so all default names
    # are present; the ones with no mocked CSV come back empty.
    raw = fm.fetch_fred_raw_series(start="2025-05-01", end="2025-06-01")
    assert set(raw) == set(fm.DEFAULT_SERIES)
    assert [r["date"] for r in raw["ust10y"]] == dates
    assert all(isinstance(r["value"], float) for r in raw["ust10y"])
    assert raw["vix"][0] == {"date": "2025-05-01", "value": 18.0}
    assert raw["dxy"] == []  # no mocked CSV → empty, not a crash


def test_fetch_raw_series_offvm_guarded(monkeypatch):
    monkeypatch.delenv("ICT_OFFVM_BUILD_HOST", raising=False)
    with pytest.raises(fm.OffVmGuardrailViolation):
        fm.fetch_fred_raw_series(start="2025-01-01", end="2025-02-01")


def test_header_rename_read_positionally(monkeypatch):
    """Older FRED exports use a 'DATE' header; we read col 0/1 positionally."""
    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")

    def _fake(*, series_id: str, start: str, end: str | None):
        # Deliberately non-standard header name.
        body = "DATE,VALUE\n" + "\n".join(
            f"2025-04-{i + 1:02d},{4.2 + i * 0.01:.2f}" for i in range(10)
        )
        return body + "\n"

    monkeypatch.setattr(fm, "_download", _fake)
    rows = fm.fetch_fred_macro_rows(start="2025-04-01", end="2025-05-01")
    assert rows
    assert all("ts" in r for r in rows)
