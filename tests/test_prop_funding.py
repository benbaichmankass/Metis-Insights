"""Unit tests for src.prop.funding — the perp-funding ledger drag (PB-20260616-004).

Pure datetime/arithmetic — no pandas, no engine, so these run anywhere.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.prop.funding import (
    apply_funding_to_ledger,
    funding_summary,
    normalize_funding_series,
)


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_normalize_accepts_bybit_ms_and_tuples():
    rows = [
        {"fundingRateTimestamp": "1672531200000", "fundingRate": "0.0001"},
        (_dt("2023-01-01 08:00:00"), 0.0002),
        {"timestamp": "2023-01-01 16:00:00", "funding_rate": -0.0001},
        {"bad": "row"},  # dropped
    ]
    out = normalize_funding_series(rows)
    assert len(out) == 3
    # sorted ascending by time
    assert out[0][0] <= out[1][0] <= out[2][0]
    assert out[0][1] == 0.0001


def test_long_pays_positive_funding_real_series():
    # one long trade, notional = entry*qty = 100*2 = 200, held across 2 funding
    # events of +0.0001 each → cost = 200 * (0.0001+0.0001) * (+1 long) = 0.04
    trades = [{
        "side": "long", "entry": 100.0, "exit": 110.0, "qty": 2.0, "pnl": 20.0,
        "entry_ts": "2023-01-01 00:00:00", "exit_ts": "2023-01-01 20:00:00",
    }]
    funding = [
        {"timestamp": "2023-01-01 00:00:00", "funding_rate": 0.0001},  # AT entry, excluded
        {"timestamp": "2023-01-01 08:00:00", "funding_rate": 0.0001},  # in window
        {"timestamp": "2023-01-01 16:00:00", "funding_rate": 0.0001},  # in window
        {"timestamp": "2023-01-02 00:00:00", "funding_rate": 0.0001},  # after exit
    ]
    funded = apply_funding_to_ledger(trades, funding_series=funding)
    assert abs(funded[0]["funding_cost"] - 0.04) < 1e-9
    assert abs(funded[0]["pnl"] - (20.0 - 0.04)) < 1e-9
    assert funded[0]["pnl_pre_funding"] == 20.0


def test_short_receives_positive_funding():
    trades = [{
        "side": "short", "entry": 100.0, "exit": 90.0, "qty": 2.0, "pnl": 20.0,
        "entry_ts": "2023-01-01 00:00:00", "exit_ts": "2023-01-01 20:00:00",
    }]
    funding = [{"timestamp": "2023-01-01 08:00:00", "funding_rate": 0.0001}]
    funded = apply_funding_to_ledger(trades, funding_series=funding)
    # short with positive rate RECEIVES → negative cost → pnl increases
    assert funded[0]["funding_cost"] < 0
    assert funded[0]["pnl"] > 20.0


def test_constant_fallback_prorates_by_hold_time():
    # 24h hold = 3 funding intervals; notional 200; const 0.0001 → 200*0.0001*3 = 0.06
    trades = [{
        "side": "long", "entry": 100.0, "exit": 105.0, "qty": 2.0, "pnl": 10.0,
        "entry_ts": "2023-01-01 00:00:00", "exit_ts": "2023-01-02 00:00:00",
    }]
    funded = apply_funding_to_ledger(trades, funding_series=None, const_rate_8h=0.0001)
    assert abs(funded[0]["funding_cost"] - 0.06) < 1e-9


def test_summary_aggregates():
    trades = [{
        "side": "long", "entry": 100.0, "exit": 105.0, "qty": 1.0, "pnl": 5.0,
        "entry_ts": "2023-01-01 00:00:00", "exit_ts": "2023-01-02 00:00:00",
    }]
    funded = apply_funding_to_ledger(trades, funding_series=None, const_rate_8h=0.0001)
    s = funding_summary(funded)
    assert s["n_trades"] == 1
    assert s["pnl_pre_funding_usd"] == 5.0
    assert s["total_funding_cost_usd"] >= 0
