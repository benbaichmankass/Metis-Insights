"""Phase-1 SIM harness tests.

Covers the deterministic, dependency-free parts in full (fill model, ledger
funnel accounting) and the engine via a stub strategy injected into
STRATEGY_UNITS so the test needs neither pandas-heavy real strategies nor live
candles. The engine's faithfulness to the LIVE intent multiplexer is asserted
by checking that it actually calls the real ``aggregate_intents`` (conflict
resolution) — not a SIM reimplementation.
"""
from __future__ import annotations

import sys
import types

import pytest

from sim.fills import BarFillModel
from sim.ledger import FunnelStage, SimLedger, SimTrade


# --------------------------------------------------------------------------
# Fill model
# --------------------------------------------------------------------------
class TestBarFillModel:
    def test_long_hits_tp(self):
        fm = BarFillModel(fee_bps_roundtrip=0.0)
        # entry 100, sl 90 (risk 10), tp 120 (2R). A bar reaching 121 hits TP.
        res = fm.resolve(
            direction="long", entry=100, sl=90, tp=120,
            future_bars=[{"ts": "t1", "high": 121, "low": 101, "close": 119}],
        )
        assert res["exit_reason"] == "tp"
        assert res["r_multiple"] == pytest.approx(2.0)

    def test_long_hits_sl(self):
        fm = BarFillModel(fee_bps_roundtrip=0.0)
        res = fm.resolve(
            direction="long", entry=100, sl=90, tp=120,
            future_bars=[{"ts": "t1", "high": 101, "low": 89, "close": 95}],
        )
        assert res["exit_reason"] == "sl"
        assert res["r_multiple"] == pytest.approx(-1.0)

    def test_ambiguous_bar_assumes_sl_first(self):
        # A bar spanning BOTH sl and tp must resolve to SL (conservative).
        fm = BarFillModel(fee_bps_roundtrip=0.0)
        res = fm.resolve(
            direction="long", entry=100, sl=90, tp=120,
            future_bars=[{"ts": "t1", "high": 125, "low": 88, "close": 110}],
        )
        assert res["exit_reason"] == "sl"
        assert res["r_multiple"] == pytest.approx(-1.0)

    def test_short_hits_tp(self):
        fm = BarFillModel(fee_bps_roundtrip=0.0)
        res = fm.resolve(
            direction="short", entry=100, sl=110, tp=80,
            future_bars=[{"ts": "t1", "high": 101, "low": 79, "close": 82}],
        )
        assert res["exit_reason"] == "tp"
        assert res["r_multiple"] == pytest.approx(2.0)

    def test_fee_reduces_r(self):
        # 100 bps round trip with risk fraction 10/100 = 0.1 => fee_r = 0.01/0.1 = 0.1
        fm = BarFillModel(fee_bps_roundtrip=100.0)
        res = fm.resolve(
            direction="long", entry=100, sl=90, tp=120,
            future_bars=[{"ts": "t1", "high": 121, "low": 101, "close": 119}],
        )
        assert res["r_multiple"] == pytest.approx(2.0 - 0.1)

    def test_unresolved_returns_none(self):
        fm = BarFillModel(fee_bps_roundtrip=0.0)
        res = fm.resolve(
            direction="long", entry=100, sl=90, tp=120,
            future_bars=[{"ts": "t1", "high": 105, "low": 95, "close": 100}],
        )
        assert res is None

    def test_timeout_closes_at_current_r(self):
        fm = BarFillModel(fee_bps_roundtrip=0.0, timeout_bars=1)
        res = fm.resolve(
            direction="long", entry=100, sl=90, tp=200,
            future_bars=[{"ts": "t1", "high": 106, "low": 99, "close": 105}],
        )
        assert res["exit_reason"] == "timeout"
        assert res["r_multiple"] == pytest.approx(0.5)  # (105-100)/10

    def test_zero_risk_returns_none(self):
        fm = BarFillModel()
        assert fm.resolve(direction="long", entry=100, sl=100, tp=120, future_bars=[]) is None


# --------------------------------------------------------------------------
# Ledger / funnel
# --------------------------------------------------------------------------
class TestSimLedger:
    def test_funnel_accumulates(self):
        lg = SimLedger()
        lg.record_stage("vwap", FunnelStage.EMITTED)
        lg.record_stage("vwap", FunnelStage.EMITTED)
        lg.record_stage("vwap", FunnelStage.SURVIVED_MUX)
        f = lg.funnel()
        assert f["vwap"]["emitted"] == 2
        assert f["vwap"]["survived_mux"] == 1
        assert f["vwap"]["filled"] == 0

    def test_summary_portfolio_and_per_strategy(self):
        lg = SimLedger()
        lg.open_trade(SimTrade("vwap", "BTCUSDT", "long", "t0", 100, 90, 120,
                               exit_ts="t1", exit=120, exit_reason="tp", r_multiple=2.0))
        lg.open_trade(SimTrade("vwap", "BTCUSDT", "long", "t2", 100, 90, 120,
                               exit_ts="t3", exit=90, exit_reason="sl", r_multiple=-1.0))
        lg.open_trade(SimTrade("turtle_soup", "BTCUSDT", "short", "t4", 100, 110, 80,
                               exit_ts="t5", exit=80, exit_reason="tp", r_multiple=2.0))
        s = lg.summary()
        assert s["portfolio"]["closed_trades"] == 3
        assert s["portfolio"]["net_r"] == pytest.approx(3.0)
        assert s["portfolio"]["wins"] == 2
        assert s["per_strategy"]["vwap"]["trades"] == 2
        assert s["per_strategy"]["vwap"]["net_r"] == pytest.approx(1.0)
        assert s["per_strategy"]["vwap"]["expectancy_r"] == pytest.approx(0.5)

    def test_open_trade_excluded_from_stats(self):
        lg = SimLedger()
        lg.open_trade(SimTrade("vwap", "BTCUSDT", "long", "t0", 100, 90, 120))  # still open
        assert lg.summary()["portfolio"]["closed_trades"] == 0
        assert len(lg.open_positions()) == 1

    def test_max_drawdown(self):
        lg = SimLedger()
        for i, r in enumerate([2.0, -1.0, -1.0, 3.0]):
            lg.open_trade(SimTrade("vwap", "BTCUSDT", "long", f"t{i}", 100, 90, 120,
                                   exit_ts=f"x{i}", exit=100, exit_reason="tp", r_multiple=r))
        # cum: 2, 1, 0, 3. peak 2 -> trough 0 => dd 2.
        assert lg.summary()["portfolio"]["max_drawdown_r"] == pytest.approx(2.0)


# --------------------------------------------------------------------------
# Engine — drives the REAL aggregate_intents through a stub strategy
# --------------------------------------------------------------------------
def _make_stub_strategy_module(signal_for_bar):
    """Build a fake unit module exposing order_package(cfg, candles_df=...).

    ``signal_for_bar(n_bars)`` returns an order_package dict or raises
    ValueError (no setup), keyed on how many bars of history it sees.
    """
    mod = types.ModuleType("sim_stub_strategy")

    def order_package(cfg, candles_df=None):
        n = 0 if candles_df is None else len(candles_df)
        return signal_for_bar(n)

    mod.order_package = order_package
    return mod


@pytest.fixture
def patch_strategy_units(monkeypatch):
    """Register stub strategies into STRATEGY_UNITS for the engine."""
    import sim.engine as engine

    registered = {}

    def register(name, module):
        modname = f"sim_stub_{name}"
        sys.modules[modname] = module
        registered[name] = modname
        new_map = dict(engine.STRATEGY_UNITS)
        new_map[name] = modname
        monkeypatch.setattr(engine, "STRATEGY_UNITS", new_map)

    yield register
    for modname in registered.values():
        sys.modules.pop(modname, None)


def _candles(n, base=100.0):
    # Flat-ish candles; the stub strategies drive signals, not price geometry.
    return [{"ts": f"2021-01-01T00:{i:02d}:00Z", "open": base, "high": base + 5,
             "low": base - 5, "close": base, "volume": 1.0} for i in range(n)]


class TestEngineIntegration:
    def test_no_signals_means_empty_funnel(self, patch_strategy_units):
        from sim.engine import run_replay

        def no_setup(n):
            raise ValueError("no setup")

        patch_strategy_units("vwap", _make_stub_strategy_module(no_setup))
        ledger = run_replay(candles=_candles(40), strategies=["vwap"],
                            warmup_bars=5, max_concurrent_per_symbol=1)
        assert ledger.summary()["portfolio"]["closed_trades"] == 0
        assert ledger.funnel().get("vwap", {}).get("emitted", 0) == 0

    def test_conflict_uses_real_priority(self, patch_strategy_units):
        """turtle_soup (pri 50) must beat vwap (pri 40) in aggregate_intents.

        Both stubs emit on EVERY bar but opposite directions. The real
        multiplexer must pick turtle_soup. This asserts the engine drives the
        live priority map, not a SIM copy.
        """
        from sim.engine import run_replay

        # turtle_soup: long, tp far away so it never resolves (stays open),
        # which lets us read the funnel after exactly one fill.
        def ts_long(n):
            return {"symbol": "BTCUSDT", "direction": "long", "entry": 100,
                    "sl": 90, "tp": 1000, "confidence": 0.9, "meta": {}}

        def vwap_short(n):
            return {"symbol": "BTCUSDT", "direction": "short", "entry": 100,
                    "sl": 110, "tp": 1, "confidence": 0.9, "meta": {}}

        patch_strategy_units("turtle_soup", _make_stub_strategy_module(ts_long))
        patch_strategy_units("vwap", _make_stub_strategy_module(vwap_short))

        ledger = run_replay(candles=_candles(40), strategies=["turtle_soup", "vwap"],
                            warmup_bars=5, max_concurrent_per_symbol=1)
        f = ledger.funnel()
        # Both emit on the first decision bar.
        assert f["turtle_soup"]["emitted"] >= 1
        assert f["vwap"]["emitted"] >= 1
        # Only turtle_soup survives the multiplexer + fills (higher priority).
        assert f["turtle_soup"]["survived_mux"] >= 1
        assert f["turtle_soup"]["filled"] >= 1
        assert f["vwap"]["survived_mux"] == 0
        assert f["vwap"]["filled"] == 0

    def test_single_position_gate_blocks_concurrent(self, patch_strategy_units):
        """A long with an unreachable TP stays open, so no 2nd position opens."""
        from sim.engine import run_replay

        def ts_long_open(n):
            return {"symbol": "BTCUSDT", "direction": "long", "entry": 100,
                    "sl": 90, "tp": 100000, "confidence": 0.9, "meta": {}}

        patch_strategy_units("turtle_soup", _make_stub_strategy_module(ts_long_open))
        ledger = run_replay(candles=_candles(40), strategies=["turtle_soup"],
                            warmup_bars=5, max_concurrent_per_symbol=1)
        # Exactly one fill — the open position blocks all subsequent bars.
        assert ledger.funnel()["turtle_soup"]["filled"] == 1
        assert len(ledger.open_positions()) == 1
