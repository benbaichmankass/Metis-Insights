"""ROADMAP_MACRO M1 — tests for the FXStreet economic-calendar source + capture path."""

from __future__ import annotations

import json

from scripts.macro.econ_calendar_data import to_event_rows
from scripts.macro.econ_calendar_fxstreet import (
    fetch_calendar,
    normalize_fxstreet,
    write_capture,
)

# Real FXStreet `eventDates` rows (schema captured by the source probe) — two
# released bill-auctions (actual, no consensus) + a synthetic-but-shape-faithful
# released CPI row (actual+consensus+revised, to exercise surprise) + a forward
# event (no actual → upcoming) + a non-US row the country filter drops.
FXS_ROWS = [
    {"id": "a", "eventId": "e1", "dateUtc": "2026-07-20T15:30:00Z", "actual": 3.73,
     "revised": None, "consensus": None, "previous": 3.76, "name": "3-Month Bill Auction",
     "countryCode": "US", "currencyCode": "USD", "unit": "%", "volatility": "LOW"},
    {"id": "b", "eventId": "e2", "dateUtc": "2026-07-20T15:30:00Z", "actual": 3.835,
     "revised": None, "consensus": None, "previous": 3.86, "name": "6-Month Bill Auction",
     "countryCode": "US", "currencyCode": "USD", "unit": "%", "volatility": "LOW"},
    {"id": "c", "eventId": "e3", "dateUtc": "2026-07-14T12:30:00Z", "actual": 3.5,
     "revised": 4.3, "consensus": 3.8, "previous": 4.2, "name": "Consumer Price Index (YoY)",
     "countryCode": "US", "currencyCode": "USD", "unit": "%", "volatility": "HIGH"},
    {"id": "d", "eventId": "e4", "dateUtc": "2026-07-30T18:00:00Z", "actual": None,
     "revised": None, "consensus": 3.75, "previous": 3.75, "name": "Fed Interest Rate Decision",
     "countryCode": "US", "currencyCode": "USD", "unit": "%", "volatility": "HIGH"},
    {"id": "x", "eventId": "e5", "dateUtc": "2026-07-30T09:00:00Z", "actual": None,
     "revised": None, "consensus": 2.0, "previous": 2.1, "name": "Inflation Rate YoY",
     "countryCode": "DE", "currencyCode": "EUR", "unit": "%", "volatility": "HIGH"},
]


def test_normalize_splits_released_upcoming_and_filters_country():
    parsed = normalize_fxstreet(FXS_ROWS, countries={"US"}, country="US")
    assert parsed["country"] == "US"
    assert all(e["country"] == "US" for e in parsed["upcoming"] + parsed["released"])  # DE dropped
    rel = {e["kind"]: e for e in parsed["released"]}
    up = {e["kind"]: e for e in parsed["upcoming"]}
    # actual present → released; actual null → upcoming
    assert "cpi_yoy" in rel and "fomc" in up
    cpi = rel["cpi_yoy"]
    assert cpi["actual"] == 3.5 and cpi["consensus"] == 3.8 and cpi["previous"] == 4.2
    assert cpi["previous_original"] == 4.3          # FXStreet `revised` carried through
    assert cpi["scheduled_at"] == "2026-07-14T12:30:00Z"
    assert up["fomc"]["consensus"] == 3.75
    assert up["fomc"]["impact_score"] == 1.0        # fomc ceiling
    # a LOW-volatility auction maps to the low impact weight
    assert rel["3_month_bill_auction"]["impact_score"] == 0.2


def test_normalize_feeds_to_event_rows_with_pit_surprise():
    parsed = normalize_fxstreet(FXS_ROWS, countries={"US"}, country="US")
    rows = to_event_rows(parsed, observed_at="2026-07-29T06:38:00Z")
    cpi = next(r for r in rows if r["kind"] == "cpi_yoy")
    assert cpi["status"] == "resolved"
    ro = cpi["realized_outcome"]
    assert ro["actual"] == 3.5 and ro["consensus"] == 3.8
    assert abs(ro["surprise"] - (3.5 - 3.8)) < 1e-9   # surprise on the never-revised consensus
    assert cpi["observed_at"] == "2026-07-29T06:38:00Z"
    fomc = next(r for r in rows if r["kind"] == "fomc" and r["status"] == "scheduled")
    assert fomc["realized_outcome"] is None and fomc["expected"]["consensus"] == 3.75


def test_fetch_calendar_injected_urlopen_sends_headers_and_range():
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

    def fake_urlopen(req, timeout=0):
        calls["url"] = req.full_url
        calls["headers"] = req.headers
        return _Resp(FXS_ROWS)

    out = fetch_calendar("2026-07-19", "2026-08-05", urlopen=fake_urlopen)
    assert isinstance(out, list) and len(out) == 5
    assert "eventDates/2026-07-19T00:00:00Z/2026-08-05T00:00:00Z" in calls["url"]
    # the FXStreet endpoint needs the Origin/Referer to serve the payload
    assert any("fxstreet.com" in str(v) for v in calls["headers"].values())


def test_fetch_calendar_degrades_to_empty():
    def boom(req, timeout=0):
        raise OSError("network down")
    assert fetch_calendar("2026-07-19", "2026-08-05", urlopen=boom) == []


def test_write_capture_and_producer_consumes_fxstreet_json(tmp_path):
    def fake_urlopen(req, timeout=0):
        class _R:
            def read(self_):
                return json.dumps(FXS_ROWS).encode()
            def __enter__(self_):
                return self_
            def __exit__(self_, *a):
                return False
        return _R()

    caps = tmp_path / "caps"
    summ = write_capture(out_dir=caps, countries=["US"], observed_at="2026-07-29T06:38:00Z",
                         urlopen=fake_urlopen)
    cap = list(caps.glob("*.fxstreet.json"))
    assert len(cap) == 1 and cap[0].name == "US-20260729T063800Z.fxstreet.json"
    assert summ["fetched_rows"] == 5

    # the producer reads the .fxstreet.json capture through the SAME PIT pipeline
    from scripts.macro.econ_calendar_produce import produce
    snap = tmp_path / "snap.jsonl"
    s = produce(captures_dir=caps, snapshots_path=snap, upcoming_path=None)
    kinds = {json.loads(ln)["kind"] for ln in snap.read_text().splitlines()}
    assert {"cpi_yoy", "fomc"} <= kinds
    assert s["resolved_with_surprise"] >= 1
