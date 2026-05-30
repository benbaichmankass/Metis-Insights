"""Phase-3 SIM tests — decision-attrition.

Pure analysis over ledger trades; no model loading, so fully deterministic.
"""
from __future__ import annotations

import pytest

from sim.attrition import compute_attrition, _MIN_FUNNEL_VOLUME
from sim.ledger import SimTrade


def _trade(strategy="vwap", r=1.0, scores=None, factor=1.0):
    return SimTrade(
        strategy, "BTCUSDT", "long", "t", 100, 90, 120,
        exit_ts="x", exit=120, exit_reason="tp", r_multiple=r,
        model_factor=factor, r_multiple_model=r * factor,
        model_scores=scores or {},
    )


class TestComputeAttrition:
    def test_funnel_scored_counts_decisions_with_score(self):
        trades = [
            _trade(scores={"m": 0.9}),
            _trade(scores={"m": 0.1}, factor=0.5),
            _trade(scores={}),  # model didn't score this one
        ]
        rep = compute_attrition(trades, bearish_threshold=0.35)
        assert rep["m"]["funnel_scored"] == 2  # third has no score for m

    def test_attrition_ratio_vs_eval_n(self):
        trades = [_trade(scores={"m": 0.9}) for _ in range(10)]
        rep = compute_attrition(trades, bearish_threshold=0.35,
                                eval_n_by_model={"m": 1000})
        assert rep["m"]["funnel_scored"] == 10
        assert rep["m"]["eval_n"] == 1000
        assert rep["m"]["attrition_ratio"] == pytest.approx(0.01)

    def test_attrition_ratio_none_without_eval_n(self):
        rep = compute_attrition([_trade(scores={"m": 0.9})], bearish_threshold=0.35)
        assert rep["m"]["attrition_ratio"] is None

    def test_bearish_and_influenced(self):
        trades = [
            _trade(r=-1.0, scores={"m": 0.1}, factor=0.5),  # bearish + downsized => influenced
            _trade(r=2.0, scores={"m": 0.2}, factor=1.0),   # bearish but NOT downsized (quorum unmet)
            _trade(r=2.0, scores={"m": 0.9}, factor=1.0),   # bullish
        ]
        rep = compute_attrition(trades, bearish_threshold=0.35)
        assert rep["m"]["bearish"] == 2
        assert rep["m"]["influenced"] == 1
        # flagged-trade net_r = -1.0 + 2.0 = 1.0 (positive => flags winners, bad)
        assert rep["m"]["bearish_net_r"] == pytest.approx(1.0)

    def test_readiness_insufficient_volume(self):
        trades = [_trade(scores={"m": 0.1}, r=-1.0, factor=0.5) for _ in range(5)]
        rep = compute_attrition(trades, bearish_threshold=0.35,
                                eval_n_by_model={"m": 5000})
        msg = rep["m"]["readiness"]
        assert "insufficient funnel volume" in msg
        assert "overstates" in msg  # eval_n >> funnel_scored

    def test_readiness_flags_losers_is_good(self):
        # Enough volume, model bearish on losers (negative flagged net_r).
        trades = [_trade(scores={"m": 0.1}, r=-1.0, factor=0.5)
                  for _ in range(_MIN_FUNNEL_VOLUME + 5)]
        rep = compute_attrition(trades, bearish_threshold=0.35)
        assert "flags losers (good)" in rep["m"]["readiness"]

    def test_readiness_flags_winners_is_bad(self):
        trades = [_trade(scores={"m": 0.1}, r=2.0, factor=0.5)
                  for _ in range(_MIN_FUNNEL_VOLUME + 5)]
        rep = compute_attrition(trades, bearish_threshold=0.35)
        assert "flags winners (bad)" in rep["m"]["readiness"]

    def test_never_bearish_verdict(self):
        trades = [_trade(scores={"m": 0.9}) for _ in range(_MIN_FUNNEL_VOLUME + 5)]
        rep = compute_attrition(trades, bearish_threshold=0.35)
        assert "never bearish" in rep["m"]["readiness"]

    def test_open_and_unscored_trades_excluded(self):
        open_t = SimTrade("vwap", "BTCUSDT", "long", "t", 100, 90, 120,
                          model_scores={"m": 0.1})  # still open (no exit)
        rep = compute_attrition([open_t], bearish_threshold=0.35)
        assert rep == {}

    def test_multiple_models_independent(self):
        trades = [
            _trade(scores={"a": 0.1, "b": 0.9}, r=-1.0, factor=0.5),
            _trade(scores={"a": 0.9, "b": 0.1}, r=2.0, factor=0.5),
        ]
        rep = compute_attrition(trades, bearish_threshold=0.35)
        assert rep["a"]["bearish"] == 1 and rep["a"]["bearish_net_r"] == pytest.approx(-1.0)
        assert rep["b"]["bearish"] == 1 and rep["b"]["bearish_net_r"] == pytest.approx(2.0)
