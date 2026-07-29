"""ROADMAP_MACRO M1 — tests for the FMP economic-calendar source + capture path."""

from __future__ import annotations

import json

from scripts.macro.econ_calendar_data import to_event_rows
from scripts.macro.econ_calendar_fmp import (
    fetch_economic_calendar,
    normalize_fmp,
    write_capture,
)

# A representative FMP /api/v3/economic_calendar JSON slice (documented field
# shape: date/country/event/previous/estimate/actual/change/impact). Mixes a
# printed release (actual present) with a forward event (actual null) and a
# non-US row that the country filter drops.
FMP_ROWS = [
    {"date": "2026-07-23 14:30:00", "country": "US", "event": "EIA Natural Gas Stocks Change",
     "previous": 41, "estimate": 29, "actual": 32, "change": -9, "impact": "Low"},
    {"date": "2026-07-14 12:30:00", "country": "US", "event": "Inflation Rate YoY",
     "previous": 4.2, "estimate": 3.8, "actual": 3.5, "change": -0.7, "impact": "High"},
    {"date": "2026-07-30 18:00:00", "country": "US", "event": "Fed Interest Rate Decision",
     "previous": 3.75, "estimate": 3.75, "actual": None, "change": None, "impact": "High"},
    {"date": "2026-07-30 11:00:00", "country": "EU", "event": "Inflation Rate YoY",
     "previous": 2.1, "estimate": 2.0, "actual": None, "change": None, "impact": "High"},
]


def test_normalize_fmp_splits_released_and_upcoming_and_filters_country():
    parsed = normalize_fmp(FMP_ROWS, countries={"US"}, country="US")
    assert parsed["country"] == "US"
    # EU row dropped by the country filter
    assert all(e["country"] == "US" for e in parsed["upcoming"] + parsed["released"])
    rel = {e["kind"]: e for e in parsed["released"]}
    up = {e["kind"]: e for e in parsed["upcoming"]}
    # actual present → released; actual null → upcoming
    assert set(rel) == {"eia_natgas_storage", "cpi_yoy"}
    assert set(up) == {"fomc"}
    # FMP naming maps to the SAME canonical kinds as the FXStreet tearsheet
    gas = rel["eia_natgas_storage"]
    assert gas["actual"] == 32.0 and gas["consensus"] == 29.0 and gas["previous"] == 41.0
    assert gas["scheduled_at"] == "2026-07-23T14:30:00Z"
    # fomc ceiling weight, pre-release consensus captured PIT
    assert up["fomc"]["impact_score"] == 1.0 and up["fomc"]["consensus"] == 3.75


def test_normalize_fmp_feeds_to_event_rows_unchanged():
    # the whole point: FMP output drops straight into the shared PIT mapper
    parsed = normalize_fmp(FMP_ROWS, countries={"US"}, country="US")
    rows = to_event_rows(parsed, observed_at="2026-07-29T06:38:00Z")
    gas = next(r for r in rows if r["kind"] == "eia_natgas_storage")
    assert gas["status"] == "resolved"
    assert gas["realized_outcome"]["surprise"] == 3.0   # actual − consensus, PIT
    assert gas["observed_at"] == "2026-07-29T06:38:00Z"
    assert gas["source"] == "bigdata:fxstreet"  # (source label lives on the row schema)


def test_normalize_fmp_honest_null_and_garbage():
    rows = [
        {"date": "2026-07-30 12:30:00", "country": "US", "event": "Some Event",
         "previous": None, "estimate": None, "actual": None, "impact": None},
        {"date": "bad", "country": "US", "event": "X", "actual": 1},   # bad date → skipped
        {"country": "US", "event": "Y", "actual": 1},                  # no date → skipped
        "not a dict",
    ]
    parsed = normalize_fmp(rows, countries={"US"})
    assert len(parsed["upcoming"]) == 1 and parsed["released"] == []
    e = parsed["upcoming"][0]
    assert e["consensus"] is None and e["impact_score"] is None


def test_fetch_economic_calendar_injected_urlopen():
    calls = {}

    class _Resp:
        def __init__(self, body):
            self._b = body
        def read(self):
            return json.dumps(self._b).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(url, timeout=0):
        calls["url"] = url
        return _Resp(FMP_ROWS)

    out = fetch_economic_calendar("2026-07-01", "2026-07-31", api_key="TESTKEY", urlopen=fake_urlopen)
    assert isinstance(out, list) and len(out) == 4
    assert "from=2026-07-01" in calls["url"] and "apikey=TESTKEY" in calls["url"]


def test_fetch_requires_key():
    import pytest
    with pytest.raises(RuntimeError):
        fetch_economic_calendar("2026-07-01", "2026-07-31", api_key=None, urlopen=lambda *a, **k: None)


def test_write_capture_and_producer_consumes_fmp_json(tmp_path):
    def fake_urlopen(url, timeout=0):
        class _R:
            def read(self_):
                return json.dumps(FMP_ROWS).encode()
            def __enter__(self_):
                return self_
            def __exit__(self_, *a):
                return False
        return _R()

    caps = tmp_path / "caps"
    summ = write_capture(
        out_dir=caps, countries=["US"], observed_at="2026-07-29T06:38:00Z",
        api_key="K", urlopen=fake_urlopen,
    )
    cap = list(caps.glob("*.fmp.json"))
    assert len(cap) == 1 and cap[0].name == "US-20260729T063800Z.fmp.json"
    assert summ["fetched_rows"] == 4

    # the producer reads the .fmp.json capture through the SAME PIT pipeline
    from scripts.macro.econ_calendar_produce import produce
    snap = tmp_path / "snap.jsonl"
    s = produce(captures_dir=caps, snapshots_path=snap, upcoming_path=None)
    kinds = {json.loads(ln)["kind"] for ln in snap.read_text().splitlines()}
    assert {"eia_natgas_storage", "cpi_yoy", "fomc"} <= kinds
    assert s["resolved_with_surprise"] >= 1
