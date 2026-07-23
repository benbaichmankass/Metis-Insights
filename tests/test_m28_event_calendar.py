"""M28 P2 — tests for the macro-event calendar feed (config → event rows)."""

from __future__ import annotations

from src.units.strategies.macro_thesis.event_calendar import (
    build_scheduled_events,
    event_id_for,
    load_events_config,
    required_series,
    resolve_scheduled_event,
)

_CFG = {
    "events": {
        "cpi": {"kind": "cpi", "entity": "macro", "metric": "cpi_index",
                "series": "CPIAUCSL", "source": "BLS", "source_url": "u",
                "direction_up": "hot", "direction_down": "cool"},
        "nfp": {"kind": "nfp", "entity": "macro", "metric": "nonfarm_payrolls",
                "series": "PAYEMS", "direction_up": "strong", "direction_down": "weak"},
        "note_only": {"kind": "note_only", "entity": "macro"},  # no series
    }
}


# --------------------------------------------------------------------------
# event_id_for
# --------------------------------------------------------------------------

def test_event_id_deterministic_and_slugged():
    a = event_id_for("cpi", "2026-08-12", "macro")
    assert a == "evt-cpi-2026-08-12-macro"
    assert event_id_for("cpi", "2026-08-12", "macro") == a       # deterministic
    # slugging: uppercase / spaces / punctuation collapse
    assert event_id_for("FOMC", "2026-09-17", "US Rates") == "evt-fomc-2026-09-17-us-rates"


# --------------------------------------------------------------------------
# required_series
# --------------------------------------------------------------------------

def test_required_series_skips_entries_without_series():
    rs = required_series(_CFG)
    assert rs == {"cpi": "CPIAUCSL", "nfp": "PAYEMS"}   # note_only has no series


# --------------------------------------------------------------------------
# build_scheduled_events
# --------------------------------------------------------------------------

def test_build_scheduled_events_one_row_per_date():
    releases = {"cpi": ["2026-08-12", "2026-09-10"], "nfp": ["2026-08-07"]}
    rows = build_scheduled_events(_CFG, releases, observed_at="2026-07-23T00:00:00Z")
    assert len(rows) == 3
    cpi = [r for r in rows if r["kind"] == "cpi"]
    assert {r["scheduled_for"] for r in cpi} == {"2026-08-12", "2026-09-10"}
    r0 = cpi[0]
    assert r0["status"] == "scheduled"
    assert r0["realized_outcome"] is None
    assert r0["event_id"] == event_id_for("cpi", r0["scheduled_for"], "macro")
    assert r0["expected"]["metric"] == "cpi_index"
    assert r0["source"] == "BLS"
    assert r0["observed_at"] == "2026-07-23T00:00:00Z"


def test_build_skips_unknown_kind():
    rows = build_scheduled_events(_CFG, {"unknown_kind": ["2026-08-01"]},
                                  observed_at="t")
    assert rows == []


def test_build_empty_releases():
    assert build_scheduled_events(_CFG, {}, observed_at="t") == []


# --------------------------------------------------------------------------
# resolve_scheduled_event
# --------------------------------------------------------------------------

def _sched(kind="cpi"):
    return build_scheduled_events(_CFG, {kind: ["2026-08-12"]},
                                  observed_at="2026-07-23T00:00:00Z")[0]


def test_resolve_is_a_new_point_in_time_row():
    sched = _sched()
    resolved = resolve_scheduled_event(
        sched, actual=305.0, prior=304.0, observed_at="2026-08-12T12:30:00Z",
        config=_CFG)
    # the original scheduled row is untouched (a NEW line, not an overwrite)
    assert sched["status"] == "scheduled"
    assert sched["realized_outcome"] is None
    # shared event_id ties the two lines together
    assert resolved["event_id"] == sched["event_id"]
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"] == "2026-08-12T12:30:00Z"
    assert resolved["observed_at"] == "2026-08-12T12:30:00Z"


def test_resolve_direction_from_change_when_no_consensus():
    sched = _sched()
    # actual > prior, no consensus -> "hot" (direction_up), surprise honest-null
    hot = resolve_scheduled_event(sched, actual=305.0, prior=304.0,
                                  observed_at="t", config=_CFG)["realized_outcome"]
    assert hot["direction"] == "hot"
    assert hot["change"] == 1.0
    assert hot["surprise"] is None
    assert hot["consensus"] is None
    cool = resolve_scheduled_event(sched, actual=303.0, prior=304.0,
                                   observed_at="t", config=_CFG)["realized_outcome"]
    assert cool["direction"] == "cool"


def test_resolve_direction_prefers_surprise_when_consensus_present():
    sched = _sched()
    # actual BELOW prior (change<0 -> would be "cool") but ABOVE consensus
    # (surprise>0 -> "hot"): surprise wins
    ro = resolve_scheduled_event(sched, actual=303.5, prior=304.0, consensus=303.0,
                                 observed_at="t", config=_CFG)["realized_outcome"]
    assert ro["surprise"] == 0.5
    assert ro["direction"] == "hot"


def test_resolve_no_prior_direction_is_null():
    sched = _sched()
    ro = resolve_scheduled_event(sched, actual=305.0, observed_at="t",
                                 config=_CFG)["realized_outcome"]
    assert ro["direction"] is None       # no prior, no consensus -> honest-null
    assert ro["change"] is None
    assert ro["actual"] == 305.0


def test_resolve_non_numeric_actual_is_honest():
    sched = _sched()
    ro = resolve_scheduled_event(sched, actual="n/a", prior=304.0, observed_at="t",
                                 config=_CFG)["realized_outcome"]
    # non-numeric actual: change/direction null, actual preserved verbatim
    assert ro["actual"] == "n/a"
    assert ro["change"] is None
    assert ro["direction"] is None


def test_resolve_without_config_still_works():
    sched = _sched()
    ro = resolve_scheduled_event(sched, actual=305.0, prior=304.0,
                                 observed_at="t")["realized_outcome"]
    # no config -> no direction labels available, but metric comes from the
    # scheduled row's `expected`, and change is still computed
    assert ro["change"] == 1.0
    assert ro["metric"] == "cpi_index"
    assert ro["direction"] is None       # no direction_up/down without config spec


# --------------------------------------------------------------------------
# config load + committed file
# --------------------------------------------------------------------------

def test_load_committed_config_is_coherent():
    cfg = load_events_config()
    events = cfg.get("events") or {}
    assert {"cpi", "nfp", "fomc", "pce", "gdp"} <= set(events)
    # every declared event has a series + direction orientation
    for kind, spec in events.items():
        assert spec.get("series")
        assert spec.get("direction_up") and spec.get("direction_down")
    # required_series covers all of them
    assert set(required_series(cfg)) == set(events)


def test_load_missing_config_is_empty():
    assert load_events_config("/nonexistent/macro_events.yaml") == {}
