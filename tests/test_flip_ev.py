"""Unit tests for the fee-aware EV gate (Unit A § 7.1(b), src/runtime/flip_ev.py).

The gate is the decisive inequality for ``FLIP_POLICY=selective``: a flip is
FOUR fills (close H + open N + close N + re-open H), so a high-confidence scalp
into a small TP against a large held trend must clear ``f·(2·notional_H +
2·notional_N)`` before it's worth displacing the trend. These tests pin the
arithmetic and the fail-safe (un-computable → never a pass).
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.runtime.flip_ev import (  # noqa: E402
    compute_flip_ev,
    flip_ev_passes,
    resolve_fee_bps_roundtrip,
    resolve_flip_ev_margin,
)


class TestResolvers:
    def test_ev_margin_default_zero(self, monkeypatch):
        monkeypatch.delenv("FLIP_EV_MARGIN_USD", raising=False)
        assert resolve_flip_ev_margin() == 0.0

    def test_ev_margin_reads_env(self, monkeypatch):
        monkeypatch.setenv("FLIP_EV_MARGIN_USD", "5.0")
        assert resolve_flip_ev_margin() == 5.0

    def test_ev_margin_settings_overrides_env(self, monkeypatch):
        monkeypatch.setenv("FLIP_EV_MARGIN_USD", "5.0")
        assert resolve_flip_ev_margin({"FLIP_EV_MARGIN_USD": 2.0}) == 2.0

    def test_ev_margin_negative_honoured(self, monkeypatch):
        # Negative margins loosen the gate — they're honoured (only parse
        # failures reset to 0).
        monkeypatch.setenv("FLIP_EV_MARGIN_USD", "-3.0")
        assert resolve_flip_ev_margin() == -3.0

    def test_ev_margin_garbage_falls_back_zero(self, monkeypatch):
        monkeypatch.setenv("FLIP_EV_MARGIN_USD", "abc")
        assert resolve_flip_ev_margin() == 0.0

    def test_fee_default_matches_backtester(self, monkeypatch):
        monkeypatch.delenv("FEE_BPS_ROUNDTRIP", raising=False)
        assert resolve_fee_bps_roundtrip() == 7.5

    def test_fee_non_positive_falls_back(self, monkeypatch):
        monkeypatch.setenv("FEE_BPS_ROUNDTRIP", "0")
        assert resolve_fee_bps_roundtrip() == 7.5


class TestComputeFlipEv:
    """Pin the § 7.1(b) inequality arithmetic."""

    def test_known_value(self):
        # P_win=0.8, scalp entry=100, sl=99, tp=104, qty=10
        #   R_N   = |104-100|*10 = 40
        #   risk_N= |100-99|*10  = 10
        #   notional_N = 100*10  = 1000
        # held_notional = 5000; fee_bps=7.5 → f = 7.5/10000/2 = 0.000375
        #   fee_cost = f*(5000 + 1000 + 1000 + 5000) = 0.000375*12000 = 4.5
        #   EV = 0.8*40 - 0.2*10 - 4.5 = 32 - 2 - 4.5 = 25.5
        fe = compute_flip_ev(
            held_notional=5000.0,
            scalp_entry=100.0, scalp_sl=99.0, scalp_tp=104.0,
            scalp_qty=10.0, scalp_confidence=0.8,
            fee_bps_roundtrip=7.5, ev_margin_usd=0.0,
        )
        assert fe.computable is True
        assert fe.reward == pytest.approx(40.0)
        assert fe.risk == pytest.approx(10.0)
        assert fe.notional_n == pytest.approx(1000.0)
        assert fe.fee_cost == pytest.approx(4.5)
        assert fe.ev == pytest.approx(25.5)

    def test_large_held_trend_makes_small_scalp_fail(self):
        # The § 7.1(b) intuition: a small scalp against a HUGE held trend loses
        # because re-entering the big trend twice costs more than the scalp earns.
        fe = compute_flip_ev(
            held_notional=1_000_000.0,       # huge trend
            scalp_entry=100.0, scalp_sl=99.5, scalp_tp=100.5,  # tiny TP
            scalp_qty=1.0, scalp_confidence=0.9,
            fee_bps_roundtrip=7.5, ev_margin_usd=0.0,
        )
        # fee on 2*held ≈ 0.000375*2_000_100 ≈ 750 >> any 1-unit scalp edge
        assert fe.computable is True
        assert fe.ev < 0
        assert flip_ev_passes(fe, ev_margin_usd=0.0) is False

    def test_clamps_confidence_above_one(self):
        fe = compute_flip_ev(
            held_notional=1000.0, scalp_entry=100.0, scalp_sl=99.0,
            scalp_tp=110.0, scalp_qty=1.0, scalp_confidence=1.5,
            fee_bps_roundtrip=7.5,
        )
        assert fe.p_win == 1.0

    def test_margin_gate(self):
        fe = compute_flip_ev(
            held_notional=5000.0, scalp_entry=100.0, scalp_sl=99.0,
            scalp_tp=104.0, scalp_qty=10.0, scalp_confidence=0.8,
            fee_bps_roundtrip=7.5,
        )
        # EV = 25.5
        assert flip_ev_passes(fe, ev_margin_usd=25.0) is True
        assert flip_ev_passes(fe, ev_margin_usd=26.0) is False

    @pytest.mark.parametrize("kwargs", [
        dict(held_notional=None, scalp_entry=100.0, scalp_sl=99.0, scalp_tp=104.0,
             scalp_qty=10.0, scalp_confidence=0.8),
        dict(held_notional=5000.0, scalp_entry=None, scalp_sl=99.0, scalp_tp=104.0,
             scalp_qty=10.0, scalp_confidence=0.8),
        dict(held_notional=5000.0, scalp_entry=100.0, scalp_sl=None, scalp_tp=104.0,
             scalp_qty=10.0, scalp_confidence=0.8),
        dict(held_notional=5000.0, scalp_entry=100.0, scalp_sl=99.0, scalp_tp=None,
             scalp_qty=10.0, scalp_confidence=0.8),
        dict(held_notional=5000.0, scalp_entry=100.0, scalp_sl=99.0, scalp_tp=104.0,
             scalp_qty=0.0, scalp_confidence=0.8),
        dict(held_notional=5000.0, scalp_entry=100.0, scalp_sl=99.0, scalp_tp=104.0,
             scalp_qty=10.0, scalp_confidence=None),
    ])
    def test_missing_input_not_computable_never_passes(self, kwargs):
        fe = compute_flip_ev(fee_bps_roundtrip=7.5, **kwargs)
        assert fe.computable is False
        # Fail-safe: an unprovable flip is NEVER a pass.
        assert flip_ev_passes(fe, ev_margin_usd=-1e9) is False

    def test_degenerate_zero_stop_not_computable(self):
        fe = compute_flip_ev(
            held_notional=5000.0, scalp_entry=100.0, scalp_sl=100.0,
            scalp_tp=104.0, scalp_qty=10.0, scalp_confidence=0.8,
        )
        assert fe.computable is False
        assert flip_ev_passes(fe) is False

    def test_nan_input_not_computable(self):
        fe = compute_flip_ev(
            held_notional=float("nan"), scalp_entry=100.0, scalp_sl=99.0,
            scalp_tp=104.0, scalp_qty=10.0, scalp_confidence=0.8,
        )
        assert fe.computable is False
