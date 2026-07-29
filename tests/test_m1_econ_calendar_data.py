"""ROADMAP_MACRO M1 — tests for the economic-calendar parser + PIT mapper."""

from __future__ import annotations

from scripts.macro.econ_calendar_data import (
    classify_kind,
    impact_to_float,
    iter_tables,
    parse_dt,
    parse_number,
    parse_tearsheet,
    previous_original,
    to_event_rows,
)

# A compact but format-faithful tearsheet slice: the forward calendar + two
# Macroeconomic-Overview sectors, including the ROADMAP_MACRO canonical M1 case
# (EIA Natural Gas Storage Change: actual 32 vs consensus 29, +10.3% surprise).
FIXTURE = """\
*Last Updated: July 29, 2026 06:38 AM UTC*

## Economic Calendar - Upcoming Events

| Date | Event | Frequency | Impact | Consensus |
|----|-----|---------|------|---------|
| 2026-07-29 18:00 UTC | Fed Interest Rate Decision | — | HIGH | 3.75% |
| 2026-07-30 12:30 UTC | Core Personal Consumption Expenditures - Price Index (YoY) (Jun) | Monthly | HIGH | 3.3% |
| 2026-07-30 14:30 UTC | EIA Natural Gas Storage Change (Jul) | Daily | LOW | – |
| 2026-08-03 14:00 UTC | ISM Manufacturing PMI (Jul) | Monthly | HIGH | – |

## Country Comparison

| Country | CPI (YoY) |
|-------|---------|
| United States | 3.5% |

## Macroeconomic Overview

### Inflation & Prices
| Date | Indicator | Frequency | Impact | Actual | Consensus | Previous | Surprise |
|----|---------|---------|------|------|---------|--------|--------|
| 2026-07-14 12:30 UTC | Consumer Price Index (YoY) (Jun) | Monthly | HIGH | 3.50% | 3.80% | 4.20% | -7.9% |
| 2026-07-02 12:30 UTC | Nonfarm Payrolls (Jun) | Monthly | HIGH | 57 | 110 | 129 (Original 172) | -48.2% |

### Inventories & Supply Chain
| Date | Indicator | Frequency | Impact | Actual | Consensus | Previous | Surprise |
|----|---------|---------|------|------|---------|--------|--------|
| 2026-07-23 14:30 UTC | EIA Natural Gas Storage Change (Jul) | Daily | LOW | 32 | 29 | 41 | +10.3% |
| 2026-06-24 12:30 UTC | Current Account (Q1) | Quarterly | LOW | $-226.80 | $-217.50 | $-221.10 (Original $-190.70) | -4.3% |
| 2026-07-28 14:00 UTC | Consumer Confidence (Jul) | Monthly | MEDIUM | — | — | — | — |
"""


# --------------------------------------------------------------------------
# parse_number / previous_original
# --------------------------------------------------------------------------

def test_parse_number_strips_decorations():
    assert parse_number("3.75%") == 3.75
    assert parse_number("$-226.80") == -226.80
    assert parse_number("32") == 32.0
    assert parse_number("+10.3%") == 10.3
    assert parse_number("129 (Original 172)") == 129.0  # revised value, parenthetical dropped


def test_parse_number_honest_null():
    for cell in ("–", "—", "-", "", "  ", "n/a", None):
        assert parse_number(cell) is None


def test_previous_original_extracts_pre_revision():
    assert previous_original("129 (Original 172)") == 172.0
    assert previous_original("$-221.10 (Original $-190.70)") == -190.70
    assert previous_original("129") is None  # no revision annotation
    assert previous_original("–") is None


# --------------------------------------------------------------------------
# parse_dt / impact / classify_kind
# --------------------------------------------------------------------------

def test_parse_dt():
    assert parse_dt("2026-07-30 12:30 UTC") == ("2026-07-30", "2026-07-30T12:30:00Z")
    assert parse_dt("2026-07-30") == ("2026-07-30", "2026-07-30T00:00:00Z")
    assert parse_dt("nonsense") == (None, None)


def test_impact_to_float():
    assert impact_to_float("HIGH") == 0.9
    assert impact_to_float("MEDIUM") == 0.5
    assert impact_to_float("LOW") == 0.2
    assert impact_to_float("HIGH", kind="fomc") == 1.0  # fomc ceiling
    assert impact_to_float("???") is None


def test_classify_kind_curated_and_fallback():
    assert classify_kind("EIA Natural Gas Storage Change (Jul)") == "eia_natgas_storage"
    assert classify_kind("Fed Interest Rate Decision") == "fomc"
    assert classify_kind("Consumer Price Index (YoY) (Jun)") == "cpi_yoy"
    assert classify_kind("Nonfarm Payrolls (Jun)") == "nfp"
    # period/vintage qualifier stripped so month-to-month siblings share a kind
    assert classify_kind("Consumer Price Index (YoY) (Jun)") == classify_kind(
        "Consumer Price Index (YoY) (Jul)")
    # unknown → deterministic slug
    assert classify_kind("Grain Stock Report") == "grain_stock_report"


# --------------------------------------------------------------------------
# iter_tables / parse_tearsheet
# --------------------------------------------------------------------------

def test_iter_tables_tags_sections():
    tables = list(iter_tables(FIXTURE))
    h2s = {t[0] for t in tables}
    assert "Economic Calendar - Upcoming Events" in h2s
    assert "Macroeconomic Overview" in h2s
    # the overview sector tables carry their H3
    h3s = {t[1] for t in tables if (t[0] or "").lower() == "macroeconomic overview"}
    assert {"Inflation & Prices", "Inventories & Supply Chain"} <= h3s


def test_parse_tearsheet_upcoming():
    parsed = parse_tearsheet(FIXTURE, country="US")
    assert parsed["country"] == "US"
    up = {e["kind"]: e for e in parsed["upcoming"]}
    assert set(up) >= {"fomc", "core_pce_yoy", "eia_natgas_storage", "ism_manufacturing"}
    fomc = up["fomc"]
    assert fomc["scheduled_for"] == "2026-07-29"
    assert fomc["scheduled_at"] == "2026-07-29T18:00:00Z"
    assert fomc["consensus"] == 3.75
    assert fomc["impact_score"] == 1.0  # fomc ceiling
    # a "–" consensus is honest-null, not 0
    assert up["eia_natgas_storage"]["consensus"] is None


def test_parse_tearsheet_released_surprise_is_pit():
    parsed = parse_tearsheet(FIXTURE, country="US")
    rel = {e["kind"]: e for e in parsed["released"]}
    gas = rel["eia_natgas_storage"]  # the canonical M1 case
    assert gas["actual"] == 32.0
    assert gas["consensus"] == 29.0
    assert gas["previous"] == 41.0
    assert gas["surprise_pct"] == 10.3
    assert gas["section"] == "Inventories & Supply Chain"
    # revised previous keeps its pre-revision original for reference
    nfp = rel["nfp"]
    assert nfp["previous"] == 129.0
    assert nfp["previous_original"] == 172.0
    # a fully-empty release row is all honest-null (not fabricated 0s)
    cc = rel["consumer_confidence"]
    assert cc["actual"] is None and cc["consensus"] is None and cc["surprise_pct"] is None


# --------------------------------------------------------------------------
# to_event_rows — the macro_events-schema PIT mapping
# --------------------------------------------------------------------------

def test_to_event_rows_schema_and_surprise():
    parsed = parse_tearsheet(FIXTURE, country="US")
    rows = to_event_rows(parsed, observed_at="2026-07-29T06:38:00Z")
    by_status: dict[str, list[dict]] = {"scheduled": [], "resolved": []}
    for r in rows:
        by_status[r["status"]].append(r)
        assert r["observed_at"] == "2026-07-29T06:38:00Z"  # every row PIT-stamped
        assert r["event_id"].startswith("evt-")
        assert r["source"] == "bigdata:fxstreet"
    assert by_status["scheduled"] and by_status["resolved"]

    gas = next(r for r in by_status["resolved"] if r["kind"] == "eia_natgas_storage")
    ro = gas["realized_outcome"]
    assert ro["actual"] == 32.0 and ro["consensus"] == 29.0
    assert ro["surprise"] == 3.0            # actual − consensus, computed PIT
    assert ro["surprise_pct"] == 10.3       # vendor's relative surprise preserved
    assert ro["change"] == 32.0 - 41.0      # actual − prior


def test_to_event_rows_scheduled_has_no_outcome():
    parsed = parse_tearsheet(FIXTURE, country="US")
    rows = to_event_rows(parsed, observed_at="t")
    sched = next(r for r in rows if r["status"] == "scheduled" and r["kind"] == "fomc")
    assert sched["realized_outcome"] is None
    assert sched["expected"]["consensus"] == 3.75


def test_scheduled_and_resolved_share_event_id():
    # A release that appears both as an upcoming (before print) and a resolved
    # (after print) on the same date shares one event_id — the PIT linkage.
    md = FIXTURE.replace(
        "| 2026-07-23 14:30 UTC | EIA Natural Gas Storage Change (Jul) | Daily | LOW | 32 | 29 | 41 | +10.3% |",
        "| 2026-07-30 14:30 UTC | EIA Natural Gas Storage Change (Jul) | Daily | LOW | 30 | 29 | 32 | +3.4% |",
    )
    rows = to_event_rows(parse_tearsheet(md, country="US"), observed_at="t")
    gas = [r for r in rows if r["kind"] == "eia_natgas_storage"]
    ids = {r["event_id"] for r in gas if r["scheduled_for"] == "2026-07-30"}
    assert len(ids) == 1  # scheduled + resolved on 2026-07-30 → same id


def test_empty_and_garbage_inputs():
    assert parse_tearsheet("", country="US") == {"country": "US", "upcoming": [], "released": []}
    assert to_event_rows({"country": "US", "upcoming": [], "released": []}, observed_at="t") == []
    assert parse_tearsheet("no tables here\njust prose", country="US")["upcoming"] == []
