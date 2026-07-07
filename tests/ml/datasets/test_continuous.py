"""Tests for the roll-adjusted continuous futures builder (ml/datasets/continuous.py).

The load-bearing property: back-adjustment REMOVES the contract-roll gap so the
continuous series moves only when the market moves — the whole reason a
breakout/trend backtest on native futures needs it (a spliced series reads the
roll gap as a breakout). We prove it on synthetic per-contract series with a
known roll offset.
"""
from __future__ import annotations

import pytest

from ml.datasets.adapters.base import CANONICAL_COLUMNS
from ml.datasets.continuous import (
    ContinuousBuildError,
    build_continuous,
    group_bars_by_contract,
)


def _bar(ts: str, close: float, *, spread: float = 0.5, vol: float = 10.0) -> dict:
    """An OHLC bar centred on `close` (o/h/l bracket it so shifting is visible)."""
    return {
        "ts": ts,
        "open": close - spread,
        "high": close + spread,
        "low": close - spread,
        "close": close,
        "volume": vol,
    }


def _t(day: int) -> str:
    return f"2026-01-{day:02d}T00:00:00Z"


# --- two-contract additive roll -------------------------------------------------

def _two_contracts_with_gap():
    # Near 202601: days 1..4 closes 100,101,102,103
    near = {"month": "202601", "bars": [_bar(_t(d), 100 + i) for i, d in enumerate([1, 2, 3, 4])]}
    # Far 202602: days 3..6 closes 110,111,112,113 (overlaps near at days 3,4)
    far = {"month": "202602", "bars": [_bar(_t(d), 110 + i) for i, d in enumerate([3, 4, 5, 6])]}
    return [far, near]  # deliberately unsorted to prove the builder orders them


def test_panama_removes_the_roll_gap():
    rows = build_continuous(_two_contracts_with_gap(), symbol="MGC", timeframe="1d")
    closes = {r["ts"]: r["close"] for r in rows}
    # Roll ts = last common bar (day 4). gap = far(103->111)=... far@day4 close 111,
    # near@day4 close 103 -> +8 applied to the WHOLE near segment.
    assert closes[_t(1)] == pytest.approx(108.0)  # 100 + 8
    assert closes[_t(4)] == pytest.approx(111.0)  # 103 + 8 (near, adjusted)
    assert closes[_t(5)] == pytest.approx(112.0)  # far, un-adjusted (front anchor)
    assert closes[_t(6)] == pytest.approx(113.0)
    # The continuous series steps +1 across the roll boundary (day4->day5),
    # NOT the raw +9 splice jump (103 -> 112).
    assert closes[_t(5)] - closes[_t(4)] == pytest.approx(1.0)


def test_front_contract_prices_are_unadjusted():
    rows = build_continuous(_two_contracts_with_gap(), symbol="MGC", timeframe="1d")
    front = {r["ts"]: r for r in rows if r["ts"] in (_t(5), _t(6))}
    # Front (newest) contract keeps real tape prices, incl. o/h/l.
    assert front[_t(5)]["high"] == pytest.approx(112.5)
    assert front[_t(5)]["low"] == pytest.approx(111.5)


def test_whole_bar_shifts_together_panama():
    rows = build_continuous(_two_contracts_with_gap(), symbol="MGC", timeframe="1d")
    day1 = next(r for r in rows if r["ts"] == _t(1))
    # o/h/l/c all shifted by the same +8; the bar's internal geometry is intact.
    assert day1["open"] == pytest.approx(107.5)
    assert day1["high"] == pytest.approx(108.5)
    assert day1["low"] == pytest.approx(107.5)
    assert day1["close"] == pytest.approx(108.0)


def test_no_duplicate_timestamps_across_overlap():
    rows = build_continuous(_two_contracts_with_gap(), symbol="MGC", timeframe="1d")
    ts = [r["ts"] for r in rows]
    assert ts == sorted(ts)               # ascending
    assert len(ts) == len(set(ts))        # the day3/day4 overlap is de-duped
    assert ts == [_t(d) for d in (1, 2, 3, 4, 5, 6)]


def test_output_is_canonical_market_raw_shape():
    rows = build_continuous(_two_contracts_with_gap(), symbol="MGC", timeframe="1d")
    for r in rows:
        assert set(r) == set(CANONICAL_COLUMNS)
    assert rows[0]["symbol"] == "MGC.c"    # default continuous token
    assert rows[0]["source"] == "ibkr_continuous"
    assert rows[0]["timeframe"] == "1d"


def test_out_symbol_override():
    rows = build_continuous(_two_contracts_with_gap(), symbol="MGC",
                            timeframe="1d", out_symbol="MGC_CONT")
    assert all(r["symbol"] == "MGC_CONT" for r in rows)


# --- three-contract cumulative adjustment --------------------------------------

def test_cumulative_offset_across_three_contracts():
    a = {"month": "202601", "bars": [_bar(_t(d), 100 + i) for i, d in enumerate([1, 2, 3, 4])]}
    b = {"month": "202602", "bars": [_bar(_t(d), 110 + i) for i, d in enumerate([3, 4, 5, 6])]}
    c = {"month": "202603", "bars": [_bar(_t(d), 120 + i) for i, d in enumerate([5, 6, 7, 8])]}
    rows = build_continuous([a, b, c], symbol="MGC", timeframe="1d")
    closes = {r["ts"]: r["close"] for r in rows}
    # A→B gap +8 @day4, B→C gap +8 @day6 → A cum +16, B cum +8, C cum 0.
    assert closes[_t(1)] == pytest.approx(116.0)  # 100 + 16
    assert closes[_t(4)] == pytest.approx(119.0)  # 103 + 16
    assert closes[_t(5)] == pytest.approx(120.0)  # B 112 + 8
    assert closes[_t(7)] == pytest.approx(122.0)  # C 122 + 0 (front)
    # Continuous everywhere: strictly +1 per day, no roll jumps.
    ordered = [closes[_t(d)] for d in range(1, 9)]
    assert ordered == pytest.approx([116, 117, 118, 119, 120, 121, 122, 123])


# --- ratio method ---------------------------------------------------------------

def test_ratio_preserves_front_and_scales_back():
    rows = build_continuous(_two_contracts_with_gap(), symbol="MGC",
                            timeframe="1d", method="ratio")
    closes = {r["ts"]: r["close"] for r in rows}
    factor = 111.0 / 103.0  # far/near at the day-4 roll
    assert closes[_t(5)] == pytest.approx(112.0)          # front unchanged
    assert closes[_t(1)] == pytest.approx(100.0 * factor)  # older scaled up
    assert closes[_t(4)] == pytest.approx(103.0 * factor)


def test_none_method_is_a_plain_splice():
    # method="none" = today's adapter behaviour: no gap removal.
    rows = build_continuous(_two_contracts_with_gap(), symbol="MGC",
                            timeframe="1d", method="none")
    closes = {r["ts"]: r["close"] for r in rows}
    assert closes[_t(4)] == pytest.approx(103.0)   # near, un-shifted
    assert closes[_t(5)] == pytest.approx(112.0)   # far -> the +9 splice jump remains
    assert closes[_t(5)] - closes[_t(4)] == pytest.approx(9.0)


# --- degradation / edges --------------------------------------------------------

def test_no_overlap_falls_back_to_no_op_offset():
    # Two contracts that never share a timestamp: honest degradation (no
    # fabricated adjustment), boundary at the far contract's first bar.
    near = {"month": "202601", "bars": [_bar(_t(d), 100 + i) for i, d in enumerate([1, 2])]}
    far = {"month": "202602", "bars": [_bar(_t(d), 200 + i) for i, d in enumerate([5, 6])]}
    rows = build_continuous([near, far], symbol="MGC", timeframe="1d")
    closes = {r["ts"]: r["close"] for r in rows}
    assert closes[_t(1)] == pytest.approx(100.0)  # near un-adjusted (offset 0)
    assert closes[_t(5)] == pytest.approx(200.0)  # far un-adjusted
    assert [r["ts"] for r in rows] == [_t(1), _t(2), _t(5), _t(6)]


def test_empty_and_single_contract():
    assert build_continuous([], symbol="MGC", timeframe="1d") == []
    one = {"month": "202601", "bars": [_bar(_t(1), 100), _bar(_t(2), 101)]}
    rows = build_continuous([one], symbol="MGC", timeframe="1d")
    assert [r["close"] for r in rows] == [100.0, 101.0]  # single = un-adjusted


def test_unknown_method_rejected():
    with pytest.raises(ContinuousBuildError):
        build_continuous(_two_contracts_with_gap(), symbol="MGC",
                         timeframe="1d", method="bogus")


def test_bar_missing_ts_rejected():
    bad = [{"month": "202601", "bars": [{"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]}]
    with pytest.raises(ContinuousBuildError):
        build_continuous(bad, symbol="MGC", timeframe="1d")


# --- group_bars_by_contract -----------------------------------------------------

def test_group_bars_by_contract_reshapes_and_sorts():
    tagged = [
        {"ts": _t(4), "contract": "202601", "open": 1, "high": 1, "low": 1, "close": 103, "volume": 1},
        {"ts": _t(1), "contract": "202601", "open": 1, "high": 1, "low": 1, "close": 100, "volume": 1},
        {"ts": _t(5), "contract": "202602", "open": 1, "high": 1, "low": 1, "close": 112, "volume": 1},
        {"ts": _t(9), "open": 1, "high": 1, "low": 1, "close": 999, "volume": 1},  # untagged -> dropped
    ]
    groups = group_bars_by_contract(tagged)
    assert [g["month"] for g in groups] == ["202601", "202602"]
    # bars within a contract are ts-ascending
    assert [b["ts"] for b in groups[0]["bars"]] == [_t(1), _t(4)]
    assert sum(len(g["bars"]) for g in groups) == 3  # untagged bar dropped


def test_group_then_build_end_to_end():
    tagged = []
    for i, d in enumerate([1, 2, 3, 4]):
        tagged.append({"ts": _t(d), "contract": "202601", "open": 100 + i, "high": 100 + i,
                       "low": 100 + i, "close": 100 + i, "volume": 1})
    for i, d in enumerate([3, 4, 5, 6]):
        tagged.append({"ts": _t(d), "contract": "202602", "open": 110 + i, "high": 110 + i,
                       "low": 110 + i, "close": 110 + i, "volume": 1})
    groups = group_bars_by_contract(tagged)
    rows = build_continuous(groups, symbol="MGC", timeframe="1d")
    closes = [r["close"] for r in rows]
    assert closes == pytest.approx([108, 109, 110, 111, 112, 113])  # continuous
