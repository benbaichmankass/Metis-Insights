"""M28 — tests for the signal-construction toolkit (pure transforms, no network).

Covers each unexplored construction dimension: D1 transforms (change/z/divergence/
detrend), D2 conditioning, D3 cross-section, D4 composite — plus the load-bearing
check that a transformed series flows through the UNCHANGED build_percentile_snapshots
emit path (same valuation-snapshot schema the P4/horizon gate grades).
"""

from __future__ import annotations

import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import signal_constructions as sc  # noqa: E402


def _days(n, start=1):
    return [f"2024-01-{d:02d}" for d in range(start, start + n)]


# ---- D1: pct_change ------------------------------------------------------

def test_pct_change_absolute_and_relative():
    s = list(zip(_days(4), [10.0, 12.0, 9.0, 9.0]))
    assert sc.pct_change_series(s) == [("2024-01-02", 2.0), ("2024-01-03", -3.0), ("2024-01-04", 0.0)]
    rel = sc.pct_change_series(s, relative=True)
    assert rel[0] == ("2024-01-02", 0.2)                       # (12-10)/10
    assert abs(rel[1][1] - (-3.0 / 12.0)) < 1e-12
    # periods=2 looks back two steps
    assert sc.pct_change_series(s, periods=2)[0] == ("2024-01-03", -1.0)  # 9-10


def test_pct_change_zero_denominator_skipped_when_relative():
    s = list(zip(_days(3), [0.0, 5.0, 6.0]))
    rel = sc.pct_change_series(s, relative=True)
    assert [d for d, _ in rel] == ["2024-01-03"]               # 01-02 dropped (prev=0)


# ---- D1: zscore ----------------------------------------------------------

def test_zscore_is_trailing_and_leakage_safe():
    # constant then a jump; the jump's z uses only the trailing window (past+self).
    s = list(zip(_days(6), [1.0, 1.0, 1.0, 1.0, 1.0, 5.0]))
    z = sc.zscore_series(s, lookback=90, min_history=3)
    # first emitted point is the 3rd (min_history), constant window → sd 0 → skipped
    # until the jump; the jump row must be present and positive.
    last = dict(z)["2024-01-06"]
    assert last > 0
    # a flat prefix (sd 0) emits nothing
    assert all(d != "2024-01-01" for d, _ in z)


# ---- D1: divergence ------------------------------------------------------

def test_divergence_is_zgap_on_common_dates():
    a = list(zip(_days(40), [float(i) for i in range(40)]))          # rising
    b = list(zip(_days(40), [float(40 - i) for i in range(40)]))     # falling
    dv = sc.divergence_series(a, b, lookback=90, min_history=30)
    # a rising vs b falling → z_a - z_b is positive at the end
    assert dv[-1][1] > 0
    # only intersecting, min_history-satisfied dates
    assert all(d >= "2024-01-30" for d, _ in dv)


# ---- D1: detrend ---------------------------------------------------------

def test_detrend_is_value_minus_trailing_mean():
    s = list(zip(_days(5), [1.0, 1.0, 1.0, 1.0, 5.0]))
    d = sc.detrend_series(s, lookback=90, min_history=3)
    assert dict(d)["2024-01-05"] == 5.0 - (1 + 1 + 1 + 1 + 5) / 5   # 5 - 1.8 = 3.2


# ---- D4: composite -------------------------------------------------------

def test_composite_weighted_mean_on_common_dates():
    a = list(zip(_days(3), [1.0, 2.0, 3.0]))
    b = list(zip(_days(3), [3.0, 2.0, 1.0]))
    eq = sc.composite_series([a, b])
    assert dict(eq)["2024-01-01"] == 2.0 and dict(eq)["2024-01-02"] == 2.0
    w = sc.composite_series([a, b], weights=[3.0, 1.0])
    assert dict(w)["2024-01-01"] == (3 * 1.0 + 1 * 3.0) / 4         # 1.5
    # mismatched weights raise
    try:
        sc.composite_series([a, b], weights=[1.0])
        assert False
    except ValueError:
        pass


# ---- D2: conditioning ----------------------------------------------------

def _snap(as_of, cheap_score):
    return {"symbol": "X", "metric": "m", "value": 1.0, "cheap_score": cheap_score,
            "label": "cheap", "as_of": as_of, "observed_at": as_of, "inputs": {}, "note": ""}


def test_condition_neutralizes_when_gate_fails_or_missing():
    snaps = [_snap("2024-01-01", 0.9), _snap("2024-01-02", 0.9), _snap("2024-01-03", 0.9)]
    gate = [("2024-01-01", 10.0), ("2024-01-02", -10.0)]           # 01-03 absent
    out = sc.condition_snapshots(snaps, gate, predicate=lambda g: g > 0)
    by = {r["as_of"]: r for r in out}
    assert by["2024-01-01"]["cheap_score"] == 0.9                  # gate passed → kept
    assert by["2024-01-01"]["inputs"]["conditioned"]["passed"] is True
    assert by["2024-01-02"]["cheap_score"] == 0.5                  # gate failed → neutral
    assert by["2024-01-03"]["cheap_score"] == 0.5                  # gate missing → neutral
    assert by["2024-01-02"]["label"] == "fair"
    # original rows untouched
    assert snaps[1]["cheap_score"] == 0.9


# ---- D3: cross-section ---------------------------------------------------

def test_cross_sectional_ranks_symbols_against_each_other():
    series = {
        "A": [("2024-01-01", 1.0), ("2024-01-02", 5.0)],
        "B": [("2024-01-01", 2.0), ("2024-01-02", 4.0)],
        "C": [("2024-01-01", 3.0), ("2024-01-02", 3.0)],
    }
    rows = sc.cross_sectional_snapshots(series, "erp", higher_is_cheaper=True, min_symbols=3)
    d1 = {r["symbol"]: r for r in rows if r["as_of"] == "2024-01-01"}
    # higher_is_cheaper: C (highest, 3.0) is cheapest that day
    assert d1["C"]["cheap_score"] > d1["B"]["cheap_score"] > d1["A"]["cheap_score"]
    assert d1["C"]["label"] == "cheap"
    assert d1["A"]["n_history"] == 3                               # cross-section width
    assert d1["A"]["observed_at"] == "2024-01-01"                  # PIT bare date


def test_cross_sectional_skips_thin_dates():
    series = {"A": [("2024-01-01", 1.0)], "B": [("2024-01-01", 2.0)]}
    assert sc.cross_sectional_snapshots(series, "m", min_symbols=3) == []   # only 2 symbols


# ---- integration: a transformed series flows through the SAME emit path --

def test_transformed_series_grades_through_build_percentile_snapshots():
    import crypto_signals_data as cd  # the unchanged emit path

    raw = list(zip([f"2024-{m:02d}-01" for m in range(1, 13)] + ["2024-12-15"],
                   [0.0001 * i for i in range(12)] + [0.02]))       # gentle rise + spike
    changed = sc.pct_change_series(raw)                             # D1 transform
    snaps = cd.build_percentile_snapshots(
        "BTCUSDT", "funding_change", changed, lookback=90, min_history=3,
        higher_is_cheaper=False, note="D1 change",
    )
    assert snaps, "transformed series must still emit valuation-schema rows"
    for k in ("symbol", "metric", "value", "cheap_score", "percentile", "n_history",
              "higher_is_cheaper", "observed_at", "as_of"):
        assert k in snaps[0]
    assert snaps[0]["metric"] == "funding_change"
