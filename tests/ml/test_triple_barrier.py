"""Tests for the triple-barrier labeler + CUSUM sampler (S-MLOPT-S5)."""
from __future__ import annotations

import math

from ml.datasets.labeling import (
    BarrierConfig,
    cusum_events,
    label_event,
)
from ml.datasets.labeling.triple_barrier import log_prices


def _flat(prices):
    # build degenerate OHLC where high==low==close==price for clean barrier math
    return list(prices), list(prices), list(prices)


def test_cusum_up_and_down_events():
    # Cumulative series: a long climb, then a long drop.
    vals = [0.0, 0.3, 0.6, 1.1, 1.0, 0.6, 0.1, -0.4]
    events = cusum_events(vals, threshold=1.0)
    sides = [s for _, s in events]
    assert 1 in sides  # an up-breach
    assert -1 in sides  # a down-breach
    # All indices are valid and strictly increasing.
    idxs = [i for i, _ in events]
    assert idxs == sorted(idxs)
    assert all(0 < i < len(vals) for i in idxs)


def test_cusum_no_event_below_threshold():
    vals = [0.0, 0.1, 0.0, 0.1, 0.0]
    assert cusum_events(vals, threshold=1.0) == []


def test_label_event_take_profit_long():
    # Price jumps up enough to hit the +2% TP at bar 2.
    highs = [100, 101, 103, 104]
    lows = [100, 99.5, 100.5, 101]
    closes = [100, 100.5, 102, 103]
    cfg = BarrierConfig(pt_mult=1.0, sl_mult=1.0, max_holding=5)
    out = label_event(highs, lows, closes, entry_idx=1, entry_price=100.0,
                      direction=1, vol=0.02, config=cfg)
    assert out is not None
    assert out.barrier == "tp"
    assert out.label == 1
    assert out.r_multiple > 0
    assert math.isclose(out.exit_price, 102.0)  # 100 * (1 + 1*0.02)


def test_label_event_stop_loss_long():
    highs = [100, 100.5, 100.2]
    lows = [100, 99.0, 97.5]
    closes = [100, 99.5, 98.0]
    cfg = BarrierConfig(pt_mult=1.0, sl_mult=1.0, max_holding=5)
    out = label_event(highs, lows, closes, entry_idx=1, entry_price=100.0,
                      direction=1, vol=0.02, config=cfg)
    assert out is not None
    assert out.barrier == "sl"
    assert out.label == -1
    assert out.r_multiple < 0


def test_label_event_adverse_first_on_straddle():
    # A single bar straddles BOTH barriers — must resolve to the stop (sl).
    highs = [100, 200]   # high blows through TP
    lows = [100, 50]     # low blows through SL on the same bar
    closes = [100, 100]
    cfg = BarrierConfig(pt_mult=1.0, sl_mult=1.0, max_holding=3)
    out = label_event(highs, lows, closes, entry_idx=1, entry_price=100.0,
                      direction=1, vol=0.02, config=cfg)
    assert out is not None
    assert out.barrier == "sl"


def test_label_event_timeout():
    # Neither barrier touched within max_holding → timeout at the horizon close.
    highs = [100, 100.3, 100.4, 100.5]
    lows = [100, 99.8, 99.7, 99.9]
    closes = [100, 100.1, 100.2, 100.3]
    cfg = BarrierConfig(pt_mult=5.0, sl_mult=5.0, max_holding=2)
    out = label_event(highs, lows, closes, entry_idx=1, entry_price=100.0,
                      direction=1, vol=0.02, config=cfg)
    assert out is not None
    assert out.barrier == "timeout"
    assert out.exit_idx == 3  # entry_idx 1 + max_holding 2


def test_label_event_short_take_profit():
    # Short candidate: price falls → TP for a short.
    highs = [100, 100.2, 99.0]
    lows = [100, 99.0, 97.5]
    closes = [100, 99.5, 98.0]
    cfg = BarrierConfig(pt_mult=1.0, sl_mult=1.0, max_holding=5)
    out = label_event(highs, lows, closes, entry_idx=1, entry_price=100.0,
                      direction=-1, vol=0.02, config=cfg)
    assert out is not None
    assert out.barrier == "tp"
    assert out.label == 1
    assert math.isclose(out.exit_price, 98.0)  # 100 * (1 - 0.02)


def test_label_event_slippage_reduces_return():
    highs = [100, 103]
    lows = [100, 99.5]
    closes = [100, 102]
    base = BarrierConfig(pt_mult=1.0, sl_mult=1.0, max_holding=2, slippage=0.0)
    slip = BarrierConfig(pt_mult=1.0, sl_mult=1.0, max_holding=2, slippage=0.001)
    out0 = label_event(highs, lows, closes, entry_idx=1, entry_price=100.0,
                       direction=1, vol=0.02, config=base)
    out1 = label_event(highs, lows, closes, entry_idx=1, entry_price=100.0,
                       direction=1, vol=0.02, config=slip)
    assert out1.ret < out0.ret  # slippage is charged against the fill


def test_label_event_rejects_nonpositive_vol():
    h, low, c = _flat([100, 101, 102])
    cfg = BarrierConfig()
    assert label_event(h, low, c, entry_idx=1, entry_price=100.0,
                       direction=1, vol=0.0, config=cfg) is None


def test_log_prices_handles_bad_tick():
    lp = log_prices([100.0, 0.0, 110.0])
    # The zero tick carries the prior log forward rather than crashing.
    assert math.isclose(lp[0], math.log(100.0))
    assert math.isclose(lp[1], math.log(100.0))
    assert math.isclose(lp[2], math.log(110.0))
