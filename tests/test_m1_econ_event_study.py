"""ROADMAP_MACRO M1 — tests for the econ-surprise → forward-price event study."""

from __future__ import annotations

import json

from scripts.macro.econ_event_study import (
    default_scorecard_path,
    effective_n,
    event_study,
    load_resolved_events,
    make_forward_return,
    release_spacing_td,
    summarize,
)


def test_default_scorecard_path_is_always_kind_suffixed():
    # No bare `econ_event_study_scorecard.json` from a default invocation — the naming
    # trap (a kind-less filename a family glob misses) is closed at the source.
    p = default_scorecard_path("eia_natgas_storage")
    assert p == "comms/macro/econ_event_study_eia_natgas_storage_scorecard.json"
    assert not p.endswith("econ_event_study_scorecard.json")
    assert default_scorecard_path("cpi_yoy").endswith("econ_event_study_cpi_yoy_scorecard.json")

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


# --------------------------------------------------- overlap correction (2026-08-01)
# The natgas 21d finding (IC −0.106, raw t=−2.98, n=789) is on WEEKLY releases against
# a 21-trading-day forward window that overlaps ~4×, so the raw t is autocorrelation-
# inflated. These pin the effective-sample deflation that turns that into an honest
# verdict.

def test_release_spacing_td_is_the_median_trading_day_gap():
    # 5 daily bars; releases on bars 0, 2, 4 → gaps of 2 trading days each → median 2.
    panel = [(f"2026-06-{d:02d}", 100.0 + d) for d in range(1, 6)]
    events = [{"date": "2026-06-01"}, {"date": "2026-06-03"}, {"date": "2026-06-05"}]
    assert release_spacing_td(events, panel) == 2.0


def test_effective_n_deflates_only_when_window_overlaps_spacing():
    # horizon <= spacing → no overlap → n_eff == n, factor 1.
    assert effective_n(100, 5, 5.0) == (1.0, 100.0)
    assert effective_n(100, 3, 5.0) == (1.0, 100.0)
    # horizon 4x the spacing → factor 4 → n_eff = n/4.
    f, ne = effective_n(800, 20, 5.0)
    assert f == 4.0 and ne == 200.0
    # unknown spacing → no correction (None n_eff), factor 1.
    assert effective_n(100, 20, None) == (1.0, None)


def test_event_study_reports_overlap_corrected_t_smaller_than_raw():
    # A 40-bar ramp; releases every 2 bars (spacing 2td) measured over a 6td window
    # that overlaps ~3× — enough events that the deflated n_eff stays >= 3.
    panel = [(d, 100.0 * (1.005 ** i)) for i, d in enumerate(
        f"2026-{m:02d}-{day:02d}" for m in (6, 7) for day in range(1, 21))]
    events = [{"date": panel[b][0], "surprise": float(b)} for b in range(0, 32, 2)]  # 16 releases
    rows = event_study(events, panel, horizons=[6], value_key="surprise")
    r = rows[0]
    assert r["overlap_factor"] > 1.0          # 6td window vs 2td spacing → overlaps
    assert r["n_eff"] < r["n"]                # effective sample deflated
    assert r["ic_t_eff"] is not None          # still enough effective sample for a t
    # honest t is strictly smaller in magnitude than the optimistic raw t
    assert abs(r["ic_t_eff"]) < abs(r["ic_t"])


def test_summarize_downgrades_a_flag_that_survives_only_on_the_raw_t():
    # The natgas shape: raw t flags (|2.98| >= 2) but the overlap-corrected t does not.
    rows = [{"horizon_days": 21, "n": 789, "ic": -0.106, "ic_t": -2.98, "ic_t_eff": -1.45}]
    s = summarize(rows, min_honest_n=12)
    assert s["verdict"] == "flagged_overlap_uncorrected_only"
    assert s["any_flagged_horizon"] is True                       # raw still flags
    assert s["any_flagged_horizon_overlap_corrected"] is False    # honest does not


def test_summarize_reports_edge_when_corrected_t_survives():
    rows = [{"horizon_days": 5, "n": 200, "ic": -0.3, "ic_t": -4.4, "ic_t_eff": -3.1}]
    s = summarize(rows, min_honest_n=12)
    assert s["verdict"] == "surprise_predicts_forward_return"
    assert s["any_flagged_horizon_overlap_corrected"] is True


def test_summarize_backcompat_falls_back_to_raw_t_when_no_corrected_t():
    # Rows without ic_t_eff (older callers) still work: verdict uses the raw t.
    rows = [{"horizon_days": 5, "n": 40, "ic": -0.45, "ic_t": -3.1}]
    s = summarize(rows, min_honest_n=12)
    assert s["verdict"] == "surprise_predicts_forward_return"
