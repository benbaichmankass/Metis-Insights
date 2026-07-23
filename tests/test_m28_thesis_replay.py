"""M28 P4 — tests for the point-in-time thesis replay driver (no-lookahead)."""

from __future__ import annotations

from src.units.strategies.macro_thesis.thesis_backtest import run_thesis_backtest
from src.units.strategies.macro_thesis.thesis_replay import (
    add_days_iso,
    as_of_snapshot_rows,
    build_replay_entries,
)

_CFG = {"min_conviction": 0.4, "account": "alpaca_options_paper",
        "express_as": "debit_vertical", "universe": []}


def _snap(symbol, metric, cheap_score, label, observed_at, value=1.0):
    return {"symbol": symbol, "metric": metric, "value": value, "cheap_score": cheap_score,
            "label": label, "z_score": 0.0, "percentile": 0.5, "n_history": 100,
            "higher_is_cheaper": True, "observed_at": observed_at}


# --------------------------------------------------------------------------
# as_of_snapshot_rows — strict past-only, latest-per-key
# --------------------------------------------------------------------------

def test_as_of_excludes_future_observations():
    recs = [
        _snap("TLT", "m", 0.9, "cheap", "2026-07-20T00:00:00Z"),
        _snap("TLT", "m", 0.1, "rich", "2026-07-25T00:00:00Z"),   # future vs as_of
    ]
    rows = as_of_snapshot_rows(recs, "2026-07-23T00:00:00Z")
    assert len(rows) == 1
    assert rows[0]["observed_at"] == "2026-07-20T00:00:00Z"   # only the past one


def test_as_of_takes_latest_known_per_key():
    recs = [
        _snap("TLT", "m", 0.6, "cheap", "2026-07-18T00:00:00Z"),
        _snap("TLT", "m", 0.9, "cheap", "2026-07-22T00:00:00Z"),   # newer, still past
        _snap("GLD", "m", 0.8, "cheap", "2026-07-19T00:00:00Z"),
    ]
    rows = {(r["symbol"], r["metric"]): r for r in as_of_snapshot_rows(recs, "2026-07-23T00:00:00Z")}
    assert rows[("TLT", "m")]["cheap_score"] == 0.9   # latest past TLT
    assert rows[("GLD", "m")]["cheap_score"] == 0.8


def test_as_of_drops_rows_missing_keys():
    recs = [{"symbol": "X", "observed_at": "t"},          # no metric
            {"metric": "m", "observed_at": "t"},          # no symbol
            _snap("TLT", "m", 0.9, "cheap", "2026-07-20T00:00:00Z")]
    rows = as_of_snapshot_rows(recs, "2026-07-23T00:00:00Z")
    assert [(r["symbol"], r["metric"]) for r in rows] == [("TLT", "m")]


# --------------------------------------------------------------------------
# add_days_iso
# --------------------------------------------------------------------------

def test_add_days_iso():
    assert add_days_iso("2026-07-23T00:00:00Z", 7) == "2026-07-30T00:00:00Z"
    assert add_days_iso("2026-07-23", 1) == "2026-07-24T00:00:00Z"   # bare date accepted
    assert add_days_iso("2026-07-30T00:00:00Z", -7) == "2026-07-23T00:00:00Z"


# --------------------------------------------------------------------------
# build_replay_entries — the point-in-time replay (NO LOOKAHEAD)
# --------------------------------------------------------------------------

def _price_at(prices):
    def fn(symbol, date_iso):
        return prices.get((symbol, date_iso))
    return fn


def test_replay_forms_entries_from_asof_reads():
    recs = [_snap("TLT", "m", 0.95, "cheap", "2026-07-20T00:00:00Z")]   # cheap → long
    prices = {("TLT", "2026-07-23T00:00:00Z"): 100.0,
              ("TLT", "2026-07-30T00:00:00Z"): 108.0}
    entries = build_replay_entries(recs, _price_at(prices),
                                   rebalance_dates=["2026-07-23T00:00:00Z"],
                                   cfg=_CFG, horizon_days=7)
    assert len(entries) == 1
    e = entries[0]
    assert e["symbol"] == "TLT" and e["direction"] == "long"
    assert e["entry_price"] == 100.0 and e["exit_price"] == 108.0
    assert e["hold_days"] == 7.0
    # feeds the scorer end-to-end
    card = run_thesis_backtest(entries)
    assert card["n"] == 1 and card["win_rate"] == 1.0


def test_replay_no_lookahead_a_future_read_forms_no_thesis():
    # the cheap read is observed AFTER the rebalance date → must not be used
    recs = [_snap("TLT", "m", 0.95, "cheap", "2026-07-25T00:00:00Z")]
    prices = {("TLT", "2026-07-23T00:00:00Z"): 100.0,
              ("TLT", "2026-07-30T00:00:00Z"): 108.0}
    entries = build_replay_entries(recs, _price_at(prices),
                                   rebalance_dates=["2026-07-23T00:00:00Z"],
                                   cfg=_CFG, horizon_days=7)
    assert entries == []   # the future snapshot is invisible as-of the rebalance date


def test_replay_drops_thesis_when_price_missing():
    recs = [_snap("TLT", "m", 0.95, "cheap", "2026-07-20T00:00:00Z")]
    prices = {("TLT", "2026-07-23T00:00:00Z"): 100.0}   # no exit price
    entries = build_replay_entries(recs, _price_at(prices),
                                   rebalance_dates=["2026-07-23T00:00:00Z"],
                                   cfg=_CFG, horizon_days=7)
    assert entries == []


def test_replay_multi_date_walk():
    recs = [
        _snap("TLT", "m", 0.95, "cheap", "2026-07-15T00:00:00Z"),
        _snap("SLV", "m", 0.05, "rich", "2026-07-20T00:00:00Z"),   # only visible at the 2nd date
    ]
    prices = {
        ("TLT", "2026-07-16T00:00:00Z"): 100.0, ("TLT", "2026-07-23T00:00:00Z"): 105.0,
        ("SLV", "2026-07-23T00:00:00Z"): 50.0, ("SLV", "2026-07-30T00:00:00Z"): 46.0,
        ("TLT", "2026-07-30T00:00:00Z"): 110.0,
    }
    entries = build_replay_entries(
        recs, _price_at(prices),
        rebalance_dates=["2026-07-16T00:00:00Z", "2026-07-23T00:00:00Z"],
        cfg=_CFG, horizon_days=7)
    by = {e["symbol"] for e in entries}
    # date-1 sees only TLT; date-2 sees TLT + SLV → both symbols represented
    assert "TLT" in by and "SLV" in by
    # SLV never appears at the first rebalance date (observed 2026-07-20)
    slv_dates = {e["as_of"] for e in entries if e["symbol"] == "SLV"}
    assert slv_dates == {"2026-07-23T00:00:00Z"}
