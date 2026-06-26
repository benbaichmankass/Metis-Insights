"""Tests for src.analysis.trade_reconstruction.first_touch_outcome (pure)."""
from src.analysis.trade_reconstruction import (
    first_touch_outcome,
    reconstruct_record,
    _bars_from_candles,
)


def test_long_hits_tp_first():
    # entry 100, sl 95, tp 110. Price rises into TP without touching SL.
    bars = [(102, 99), (108, 101), (111, 106)]  # (high, low)
    r = first_touch_outcome("long", 100, 95, 110, bars)
    assert r.outcome == "tp" and r.label == "reconstructed_win"
    assert r.bars_to_resolution == 2 and not r.ambiguous
    assert abs(r.r_multiple - 2.0) < 1e-9  # reward 10 / risk 5


def test_long_hits_sl_first():
    bars = [(101, 99), (100, 94), (112, 90)]
    r = first_touch_outcome("long", 100, 95, 110, bars)
    assert r.outcome == "sl" and r.label == "reconstructed_loss"
    assert r.bars_to_resolution == 1 and r.r_multiple == -1.0


def test_short_hits_tp_first():
    # short entry 100, sl 105, tp 90. Price falls into TP.
    bars = [(101, 98), (99, 89)]
    r = first_touch_outcome("short", 100, 105, 90, bars)
    assert r.outcome == "tp" and r.bars_to_resolution == 1


def test_short_hits_sl_first():
    bars = [(106, 99)]
    r = first_touch_outcome("short", 100, 105, 90, bars)
    assert r.outcome == "sl" and r.r_multiple == -1.0


def test_intrabar_straddle_pessimistic_defaults_to_sl():
    # one bar touches BOTH tp(110) and sl(95)
    bars = [(112, 94)]
    r = first_touch_outcome("long", 100, 95, 110, bars, pessimistic=True)
    assert r.outcome == "sl" and r.ambiguous
    r2 = first_touch_outcome("long", 100, 95, 110, bars, pessimistic=False)
    assert r2.outcome == "tp" and r2.ambiguous


def test_open_when_neither_touched():
    bars = [(101, 99), (102, 98)]
    r = first_touch_outcome("long", 100, 95, 110, bars)
    assert r.outcome == "open" and r.label == "open_at_window_end"
    assert r.bars_to_resolution is None


def test_buy_sell_aliases_and_bad_direction():
    assert first_touch_outcome("buy", 100, 95, 110, [(111, 100)]).outcome == "tp"
    assert first_touch_outcome("sell", 100, 105, 90, [(106, 100)]).outcome == "sl"
    assert first_touch_outcome("sideways", 100, 95, 110, [(111, 90)]).outcome == "open"


def test_bars_from_candles_filters_after_ts_and_ms_norm():
    candles = [
        {"timestamp": 1000, "high": 1, "low": 0},
        {"timestamp": 2000, "high": 2, "low": 1},
        {"timestamp": 3000, "high": 3, "low": 2},
    ]
    bars = _bars_from_candles(candles, after_ts=1500)
    assert bars == [(2.0, 1.0), (3.0, 2.0)]
    # epoch-ms entry vs epoch-s candles still compares correctly
    candles_ms = [{"time": 1_700_000_000_000, "high": 5, "low": 4}]
    bars2 = _bars_from_candles(candles_ms, after_ts=1_699_999_999)
    assert bars2 == [(5.0, 4.0)]


def test_reconstruct_record_uses_injected_fetch_fn():
    rec = {"symbol": "SOLUSDT", "direction": "long", "entry_price": 100,
           "stop_loss": 95, "take_profit_1": 110, "created_at": "2026-06-25T00:00:00Z"}

    def fake_fetch(sym, tf, lim):
        return [
            {"timestamp": 1_600_000_000, "high": 101, "low": 99},   # before entry — filtered by ts? entry ts later
            {"timestamp": 4_000_000_000, "high": 111, "low": 100},  # after — hits tp
        ]

    res = reconstruct_record(rec, fetch_fn=fake_fetch)
    assert res is not None and res.outcome == "tp"


def test_reconstruct_record_none_without_bracket():
    rec = {"symbol": "SOLUSDT", "direction": "long", "entry_price": 100}
    assert reconstruct_record(rec, fetch_fn=lambda *a: []) is None
