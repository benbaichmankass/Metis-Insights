"""Tests for the FRED wide-corpus adapter (M19 corpus C2). Stdlib, network monkeypatched."""
from __future__ import annotations

import pytest

from ml.datasets.adapters import fred_corpus as fc
from ml.datasets.adapters import fred_macro as fm


def _fred_csv(series_id: str, rows: list[tuple[str, str]]) -> str:
    return f"observation_date,{series_id}\n" + "\n".join(f"{d},{v}" for d, v in rows) + "\n"


def _fake_download_factory(csv_by_series: dict[str, list[tuple[str, str]]]):
    # fred_corpus reuses fred_macro._daily_values → fred_macro._download; patch there.
    def _fake(*, series_id: str, start: str, end: str | None):
        return _fred_csv(series_id, csv_by_series.get(series_id, []))

    return _fake


def test_offvm_guard_blocks_without_env(monkeypatch):
    monkeypatch.delenv("ICT_OFFVM_BUILD_HOST", raising=False)
    with pytest.raises(fc.OffVmGuardrailViolation):
        fc.fetch_fred_corpus_series(start="2020-01-01", end="2020-02-01")


def test_fetch_groups_and_shape(monkeypatch):
    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    dates = [f"2020-01-{i + 1:02d}" for i in range(8)]
    csv_by_series = {sid: [(d, f"{100.0 + i:.2f}") for i, d in enumerate(dates)] for sid in fc.CORPUS_SERIES}
    monkeypatch.setattr(fm, "_download", _fake_download_factory(csv_by_series))

    panel = fc.fetch_fred_corpus_series(start="2020-01-01", end="2020-02-01")
    assert set(panel) == set(fc.CORPUS_SERIES)
    # each block carries name/group/rows, rows ascending {date,value}
    for fred_id, block in panel.items():
        name, group = fc.CORPUS_SERIES[fred_id]
        assert block["name"] == name and block["group"] == group
        assert [r["date"] for r in block["rows"]] == dates
        assert all(isinstance(r["value"], float) for r in block["rows"])
    # groups span more than macro/rates (the C2 point)
    groups = {block["group"] for block in panel.values()}
    assert {"equity", "commodity", "credit", "rates"} <= groups


def test_custom_series_replaces_default(monkeypatch):
    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    csv_by_series = {"GDP": [("2020-01-01", "21000.0")]}
    monkeypatch.setattr(fm, "_download", _fake_download_factory(csv_by_series))
    panel = fc.fetch_fred_corpus_series(
        start="2020-01-01", end="2020-02-01", series={"GDP": ("gdp", "macro")}
    )
    # unlike fred_macro's additive override, C2's `series=` REPLACES the catalog
    assert set(panel) == {"GDP"}
    assert panel["GDP"]["rows"][0] == {"date": "2020-01-01", "value": 21000.0}


def test_missing_series_is_empty_not_crash(monkeypatch):
    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    # only SP500 has data; the rest come back empty
    csv_by_series = {"SP500": [("2020-01-01", "3200.0")]}
    monkeypatch.setattr(fm, "_download", _fake_download_factory(csv_by_series))
    panel = fc.fetch_fred_corpus_series(start="2020-01-01", end="2020-02-01")
    assert panel["SP500"]["rows"] == [{"date": "2020-01-01", "value": 3200.0}]
    assert panel["DCOILWTICO"]["rows"] == []
