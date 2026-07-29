"""ROADMAP_MACRO M1 — tests for the econ-surprise → forward-price event study."""

from __future__ import annotations

import json

from scripts.macro.econ_event_study import (
    event_study,
    load_resolved_events,
    make_forward_return,
    summarize,
)

# A tiny ascending daily-close panel: a clean +1%/day ramp so a forward return at
# horizon H over a base at index i is deterministic ((1.01**H) - 1).
_PANEL = [(f"2026-06-{d:02d}", 100.0 * (1.01 ** i)) for i, d in enumerate(range(1, 26))]


def _write_snapshots(tmp_path, rows):
    p = tmp_path / "snap.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return str(p)


def _resolved(date, *, surprise, consensus=29.0, actual=32.0, observed_at="2026-07-29T06:38:00Z", kind="eia_natgas_storage"):
    return {
        "kind": kind, "status": "resolved", "scheduled_for": date, "scheduled_at": f"{date}T14:30:00Z",
        "observed_at": observed_at,
        "realized_outcome": {"surprise": surprise, "surprise_pct": None, "actual": actual,
                             "consensus": consensus, "change": -9.0},
    }


def test_make_forward_return_uses_base_at_or_before_and_horizon_offset():
    fwd = make_forward_return(_PANEL)
    # release on a trading day → base is THAT day's close (index 0), +3 td forward
    r = fwd("2026-06-01", 3)
    assert abs(r - ((1.01 ** 3) - 1.0)) < 1e-9
    # release on a NON-trading day (before any bar) → None (precedes history)
    assert fwd("2026-05-15", 1) is None
    # release maps to a mid-panel bar; a horizon past the panel end → None (censored)
    assert fwd("2026-06-25", 5) is None


def test_forward_return_base_is_prior_close_when_release_day_absent():
    # a panel with a GAP: release falls in the gap → base is the last prior bar.
    panel = [("2026-06-01", 100.0), ("2026-06-05", 110.0), ("2026-06-08", 121.0)]
    fwd = make_forward_return(panel)
    # release 2026-06-03 (no bar) → base = 2026-06-01 close (100), +1 td = 2026-06-05 (110) → +10%
    assert abs(fwd("2026-06-03", 1) - 0.10) < 1e-9


def test_load_resolved_events_dedupes_by_date_prefers_defined_surprise_and_earliest_obs(tmp_path):
    rows = [
        _resolved("2026-07-16", surprise=-4.0, observed_at="2026-07-29T08:00:00Z"),
        # same date, EARLIER observation, also has a surprise → wins the tie (first PIT read)
        _resolved("2026-07-16", surprise=-4.0, observed_at="2026-07-29T06:38:00Z"),
        # a scheduled (not resolved) row for another date → excluded
        {"kind": "eia_natgas_storage", "status": "scheduled", "scheduled_for": "2026-08-06"},
        _resolved("2026-07-09", surprise=1.0),
        # a different kind → excluded
        _resolved("2026-07-09", surprise=99.0, kind="eia_crude_stocks"),
    ]
    events = load_resolved_events(_write_snapshots(tmp_path, rows), "eia_natgas_storage")
    assert [e["date"] for e in events] == ["2026-07-09", "2026-07-16"]  # ascending, deduped
    jul16 = next(e for e in events if e["date"] == "2026-07-16")
    assert jul16["observed_at"] == "2026-07-29T06:38:00Z"  # earliest observation kept
    assert jul16["surprise"] == -4.0


def test_event_study_positive_ic_on_varied_returns():
    # geometric panel where each successive base gives a LARGER forward return, so
    # surprise (increasing with date) and forward return co-move → IC = +1.
    panel = [(f"2026-06-{d:02d}", v) for d, v in zip(range(1, 8), [100, 101, 103, 106, 110, 116, 124])]
    events = [
        {"date": "2026-06-01", "surprise": 1.0},
        {"date": "2026-06-02", "surprise": 2.0},
        {"date": "2026-06-03", "surprise": 3.0},
    ]
    rows = event_study(events, panel, horizons=[1], value_key="surprise")
    assert rows[0]["n"] == 3
    assert rows[0]["ic"] == 1.0                # perfectly rank-correlated
    assert rows[0]["sign_hit_rate"] == 1.0     # all same-sign


def test_event_study_skips_releases_missing_the_value_key():
    events = [
        {"date": "2026-06-01", "surprise": None},   # no surprise → dropped from n
        {"date": "2026-06-02", "surprise": 2.0},
        {"date": "2026-06-03", "surprise": 3.0},
        {"date": "2026-06-04", "surprise": 4.0},
    ]
    rows = event_study(events, _PANEL, horizons=[1], value_key="surprise")
    assert rows[0]["n"] == 3   # the None-surprise release is excluded, not zero-filled


def test_summarize_caps_at_insufficient_history_for_small_n():
    rows = [{"horizon_days": 3, "n": 6, "ic": -0.9, "ic_t": -3.0}]
    s = summarize(rows, min_honest_n=12)
    assert s["verdict"] == "insufficient_history"
    assert s["sufficient_history"] is False
    assert s["any_flagged_horizon"] is True         # still reports the flagged lead
    assert s["strongest_ic"] == -0.9


def test_summarize_reports_edge_when_history_sufficient():
    rows = [
        {"horizon_days": 1, "n": 40, "ic": -0.1, "ic_t": -0.6},
        {"horizon_days": 5, "n": 40, "ic": -0.45, "ic_t": -3.1},
    ]
    s = summarize(rows, min_honest_n=12)
    assert s["verdict"] == "surprise_predicts_forward_return"
    assert s["sufficient_history"] is True
    assert s["strongest_ic_horizon_days"] == 5


def test_summarize_no_edge_when_flat():
    rows = [{"horizon_days": 5, "n": 40, "ic": 0.02, "ic_t": 0.2}]
    s = summarize(rows, min_honest_n=12)
    assert s["verdict"] == "no_edge_at_tested_horizons"


def test_load_resolved_events_missing_file_is_empty():
    assert load_resolved_events("/no/such/snapshots.jsonl", "eia_natgas_storage") == []
