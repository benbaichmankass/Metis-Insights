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
    # groups span the wide panel — equities, commodities, credit, the fuller
    # rates curve, AND fx (the C2 breadth point; fx added 2026-07-03).
    groups = {block["group"] for block in panel.values()}
    assert {"equity", "commodity", "credit", "rates", "fx"} <= groups
    # the fx + commodity breadth additions are present by name (gold as its vol
    # index — the LBMA gold fixing series were discontinued by FRED)
    names = {block["name"] for block in panel.values()}
    assert {"usdjpy", "eurusd", "gbpusd", "gold_vol", "natgas"} <= names
    # the 2026-07-04 T1.2 Phase-3 breadth widening (fuller curve / vol / dollar):
    assert {"vix", "broad_dollar", "ust10y", "breakeven5y", "ig_credit_oas"} <= names


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


def test_discontinued_series_is_skipped_not_fatal(monkeypatch):
    """A single 404 (discontinued id) must skip that series, not zero the corpus."""
    import urllib.error

    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    dates = [("2020-01-01", "1.0")]
    good = _fake_download_factory({sid: dates for sid in fc.CORPUS_SERIES})

    def _fake(*, series_id: str, start: str, end: str | None):
        if series_id == "GVZCLS":  # simulate a discontinued/404 upstream id
            raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)  # type: ignore[arg-type]
        return good(series_id=series_id, start=start, end=end)

    monkeypatch.setattr(fm, "_download", _fake)
    panel = fc.fetch_fred_corpus_series(start="2020-01-01", end="2020-02-01")
    # the good series survive; the dead one is recorded under _skipped, not fatal
    assert "GVZCLS" not in panel
    assert "SP500" in panel and panel["SP500"]["rows"] == [{"date": "2020-01-01", "value": 1.0}]
    assert "GVZCLS" in panel["_skipped"]
    assert len(panel) == len(fc.CORPUS_SERIES)  # (N-1 good series) + the _skipped key
