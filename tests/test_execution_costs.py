"""The ONE shared execution-realism cost model (src/runtime/execution_costs.py).

P1 (FAITHFUL-BACKTEST-PLATFORM-DESIGN § 3.B). Locks:
  1. fee-only default is byte-identical to the legacy `(bps/1e4)·avg_px/risk` term
     (so every existing harness/sweep is unchanged when slippage/funding are 0);
  2. funding counts the 8h perp windows the hold crosses (exact + fractional);
  3. slippage + funding add as bps-of-notional drag; negatives clamp; un-derivable
     inputs → zeroed terms, never a raise;
  4. the round-trip fee constant has ONE owner, re-exported by trade_costs/allocator_ev.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.runtime import execution_costs as ec


class TestVenueAwareCost:
    """Mandatory-cost policy (operator directive 2026-08-04): funding is PERP-ONLY.
    A flat funding default on a future/equity/fx would fabricate a cost — the
    false-drag class the venue-aware fee resolver already avoids."""

    def _reset(self):
        ec._PERP_CATEGORY_CACHE = None  # force a reload of the real instruments.yaml

    def test_crypto_perps_pay_funding(self):
        self._reset()
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
            assert ec.is_perp(sym) is True, sym
            assert ec.funding_bps_per_window_for(sym) == ec.DEFAULT_FUNDING_BPS_PER_WINDOW

    def test_non_perps_pay_no_funding(self):
        self._reset()
        # futures (IB) + equities/ETFs (alpaca spot) + fx — none pay perp funding
        for sym in ("MES", "MGC", "MHG", "SPY", "GLD", "TLT", "EURUSD"):
            assert ec.is_perp(sym) is False, sym
            assert ec.funding_bps_per_window_for(sym) == 0.0, sym

    def test_unknown_symbol_uses_usdt_heuristic(self):
        self._reset()
        assert ec.is_perp("FOOUSDT") is True      # unknown but *USDT → perp
        assert ec.is_perp("NOT_A_SYMBOL") is False
        assert ec.is_perp("") is False

    def test_slippage_default_is_uniform_nonzero(self):
        for sym in ("BTCUSDT", "MES", "GLD", "EURUSD"):
            assert ec.slippage_bps_roundtrip_for(sym) == ec.DEFAULT_SLIPPAGE_BPS_ROUNDTRIP


class TestFeeConstantSingleOwner:
    def test_reexports_agree(self):
        from src.runtime.allocator_ev import DEFAULT_FEE_BPS_ROUNDTRIP as a
        from src.runtime.trade_costs import DEFAULT_FEE_BPS_ROUNDTRIP as b
        assert a == b == ec.DEFAULT_FEE_BPS_ROUNDTRIP == 7.5


class TestFundingWindows:
    def test_exact_count_across_8h_boundaries(self):
        # 2026-01-01 01:00 → 2026-01-01 17:30 UTC crosses the 08:00 and 16:00
        # funding stamps → 2 windows.
        a = datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
        b = datetime(2026, 1, 1, 17, 30, tzinfo=timezone.utc)
        assert ec.funding_windows_crossed(a, b) == 2.0

    def test_boundary_exact_open_is_open_interval(self):
        # open exactly on a stamp, close just before the next → 0 crossed.
        a = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
        b = datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)
        assert ec.funding_windows_crossed(a, b) == 0.0

    def test_iso_strings_and_z_suffix(self):
        assert ec.funding_windows_crossed(
            "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z") == 3.0  # 08,16,00

    def test_fractional_fallback_from_hold_hours(self):
        assert ec.funding_windows_crossed(None, None, hold_hours=12.0) == 1.5

    def test_nothing_derivable_is_zero(self):
        assert ec.funding_windows_crossed(None, None) == 0.0
        assert ec.funding_windows_crossed("garbage", "garbage") == 0.0


class TestRoundtripCostR:
    def test_fee_only_default_matches_legacy_term(self):
        # legacy: (fee_bps/1e4)·((entry+exit)/2)/risk
        entry, exit_price, risk, bps = 100.0, 110.0, 4.0, 7.5
        legacy = (bps / 1.0e4) * ((entry + exit_price) / 2.0) / risk
        out = ec.roundtrip_cost_r(entry=entry, exit_price=exit_price, risk=risk,
                                  fee_bps_roundtrip=bps)
        assert abs(out["fee_r"] - legacy) < 1e-12
        assert out["slippage_r"] == 0.0 and out["funding_r"] == 0.0
        assert abs(out["total_cost_r"] - legacy) < 1e-12

    def test_slippage_and_funding_add(self):
        a = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        b = datetime(2026, 1, 1, 16, 0, tzinfo=timezone.utc)  # crosses 08,16 → 2 win
        out = ec.roundtrip_cost_r(
            entry=100.0, exit_price=100.0, risk=4.0,
            entry_time=a, exit_time=b,
            fee_bps_roundtrip=7.5, slippage_bps_roundtrip=5.0,
            funding_bps_per_window=1.0)
        per_r = 100.0 / 4.0
        assert abs(out["fee_r"] - (7.5 / 1e4) * per_r) < 1e-12
        assert abs(out["slippage_r"] - (5.0 / 1e4) * per_r) < 1e-12
        assert out["funding_windows"] == 2.0
        assert abs(out["funding_r"] - (1.0 / 1e4) * 2.0 * per_r) < 1e-12
        assert abs(out["total_cost_r"]
                   - (out["fee_r"] + out["slippage_r"] + out["funding_r"])) < 1e-12

    def test_negative_bps_clamped(self):
        out = ec.roundtrip_cost_r(entry=100.0, exit_price=100.0, risk=4.0,
                                  fee_bps_roundtrip=-9.0, slippage_bps_roundtrip=-1.0)
        assert out["fee_r"] == 0.0 and out["slippage_r"] == 0.0

    def test_bad_inputs_zeroed_never_raise(self):
        for bad in (dict(entry=None, exit_price=100.0, risk=4.0),
                    dict(entry=100.0, exit_price=100.0, risk=0.0),
                    dict(entry=100.0, exit_price=None, risk=4.0)):
            out = ec.roundtrip_cost_r(**bad)
            assert out["total_cost_r"] == 0.0


class TestRoundtripCostUsd:
    def test_fee_only_matches_notional_formula(self):
        # (7.5/1e4)*100*2*1 = 0.15 (the trade_costs estimator's contract)
        out = ec.roundtrip_cost_usd(entry_price=100.0, qty=2.0)
        assert abs(out["fee_usd"] - 0.15) < 1e-9
        assert out["slippage_usd"] == 0.0 and out["funding_usd"] == 0.0

    def test_funding_scales_notional_by_windows(self):
        out = ec.roundtrip_cost_usd(
            entry_price=100.0, qty=2.0, hold_hours=24.0,
            funding_bps_per_window=1.0, fee_bps_roundtrip=0.0)
        # 24h/8h = 3 windows; (1/1e4)*3*200 = 0.06
        assert abs(out["funding_usd"] - 0.06) < 1e-9

    def test_bad_inputs_none(self):
        out = ec.roundtrip_cost_usd(entry_price=None, qty=2.0)
        assert out["fee_usd"] is None and out["total_cost_usd"] is None
