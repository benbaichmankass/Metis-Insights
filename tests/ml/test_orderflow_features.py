"""Tests for the order-flow / microstructure estimators (S-MLOPT-S10)."""
from __future__ import annotations

import math

from ml.datasets.orderflow_features import (
    _finite_or_zero,
    bulk_volume_classification,
    microprice,
    order_flow_imbalance,
    relative_spread,
    vpin,
)


def test_microprice_between_bid_and_ask():
    mp = microprice(100.0, 1.0, 101.0, 1.0)
    assert math.isclose(mp, 100.5)  # equal sizes → mid


def test_microprice_weights_toward_larger_opposite_size():
    # Large ask_size pulls the micro-price toward the bid.
    mp = microprice(100.0, 1.0, 101.0, 9.0)
    assert 100.0 < mp < 100.5


def test_microprice_degenerate():
    assert microprice(0.0, 1.0, 101.0, 1.0) is None
    assert microprice(100.0, 0.0, 101.0, 0.0) is None


def test_relative_spread():
    assert math.isclose(relative_spread(100.0, 101.0), 1.0 / 100.5, rel_tol=1e-9)
    assert relative_spread(101.0, 100.0) is None  # crossed
    assert relative_spread(0.0, 1.0) is None


def test_ofi_rising_bid_is_positive():
    # Bid price rises with size → strong positive (buy) pressure.
    snaps = [
        {"bid": 100.0, "bid_size": 5.0, "ask": 101.0, "ask_size": 5.0},
        {"bid": 100.5, "bid_size": 6.0, "ask": 101.0, "ask_size": 5.0},
    ]
    assert order_flow_imbalance(snaps) > 0


def test_ofi_rising_ask_is_positive():
    # Best ask rises (ask liquidity consumed/withdrawn) → upward pressure → +OFI.
    snaps = [
        {"bid": 100.0, "bid_size": 5.0, "ask": 101.0, "ask_size": 5.0},
        {"bid": 100.0, "bid_size": 5.0, "ask": 101.5, "ask_size": 6.0},
    ]
    assert order_flow_imbalance(snaps) > 0


def test_ofi_falling_bid_is_negative():
    # Bid price falls (bids withdrawn) → sell pressure → negative OFI.
    snaps = [
        {"bid": 100.0, "bid_size": 5.0, "ask": 101.0, "ask_size": 5.0},
        {"bid": 99.5, "bid_size": 5.0, "ask": 101.0, "ask_size": 5.0},
    ]
    assert order_flow_imbalance(snaps) < 0


def test_ofi_flat_book_zero():
    snaps = [
        {"bid": 100.0, "bid_size": 5.0, "ask": 101.0, "ask_size": 5.0},
        {"bid": 100.0, "bid_size": 5.0, "ask": 101.0, "ask_size": 5.0},
    ]
    assert order_flow_imbalance(snaps) == 0.0


def test_ofi_too_few_snapshots():
    assert order_flow_imbalance([{"bid": 100.0, "ask": 101.0}]) is None
    assert order_flow_imbalance([]) is None


def test_bvc_split_sums_to_volume_and_skews_with_price():
    dps = [2.0, -2.0, 0.0]
    vols = [10.0, 10.0, 10.0]
    buys, sells = bulk_volume_classification(dps, vols, sigma=1.0)
    for b, s, v in zip(buys, sells, vols):
        assert math.isclose(b + s, v, rel_tol=1e-9)
    assert buys[0] > sells[0]   # strong up move → mostly buys
    assert buys[1] < sells[1]   # strong down move → mostly sells
    assert math.isclose(buys[2], sells[2])  # flat → 50/50


def test_bvc_zero_sigma_is_5050():
    buys, sells = bulk_volume_classification([5.0], [10.0], sigma=0.0)
    assert math.isclose(buys[0], 5.0) and math.isclose(sells[0], 5.0)


def test_vpin_all_one_sided_is_one():
    # Every bucket fully buy → toxicity 1.0.
    assert math.isclose(vpin([10.0, 10.0], [0.0, 0.0]), 1.0)


def test_vpin_balanced_is_zero():
    assert math.isclose(vpin([5.0, 5.0], [5.0, 5.0]), 0.0)


def test_vpin_empty_is_none():
    assert vpin([0.0], [0.0]) is None
    assert vpin([], []) is None


def test_finite_or_zero():
    assert _finite_or_zero(None) == 0.0
    assert _finite_or_zero(float("nan")) == 0.0
    assert _finite_or_zero(float("inf")) == 0.0
    assert _finite_or_zero(1.5) == 1.5
