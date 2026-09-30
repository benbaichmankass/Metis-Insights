"""`--entry-mode limit` for scripts/backtest_ict_scalp.py (RQ-20260930-502, Tier-1 harness only).

Locks: (1) market mode is byte-identical to the harness on origin/main's behaviour
(the default arm must not move); (2) the registered fill model -- a limit fills only
when price trades THROUGH it, a touch is not a fill, an unfilled signal is counted
as missed, the fill bar checks the stop but never credits the target; (3) the limit
cost model -- maker entry + taker exit fee, slippage on the exit only.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

import scripts.backtest_ict_scalp as h

K = 40          # signal bar index


def _reset():
    h.FEE_BPS_ROUNDTRIP = h.execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP
    h.SLIPPAGE_BPS_ROUNDTRIP = 0.0
    h.FUNDING_BPS_PER_WINDOW = 0.0
    h.FUNDING_WINDOW_HOURS = h.execution_costs.FUNDING_WINDOW_HOURS


def _frame(overrides=None, n=80):
    ts = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    df = pd.DataFrame({"timestamp": ts, "open": 100.0, "high": 100.4, "low": 100.1,
                       "close": 100.2, "volume": 1.0})
    for idx, kw in (overrides or {}).items():
        for col, v in kw.items():
            df.loc[idx, col] = v
    return df


@pytest.fixture()
def stub(monkeypatch):
    """order_package fires once, on bar K: long, entry 100, sl 99 (1R), tp 101.5 (1.5R)."""
    def fake(cfg, candles_df):
        if int(candles_df.index[-1]) != K:
            raise ValueError("no setup")
        return {"direction": "long", "entry": 100.0, "sl": 99.0, "tp": 101.5,
                "confidence": 1.0, "meta": {}}
    monkeypatch.setattr(h, "order_package", fake)
    _reset()
    yield
    _reset()


def _run(df, **kw):
    return h.run_backtest(df, cfg_overrides={}, timeframe="15m", symbol="ETHUSDT",
                          warmup_bars=5, timeout_bars=24, cooldown_bars=3,
                          return_trades=True, entry_mode="limit", **kw)


def test_touch_is_not_a_fill_and_missed_signal_is_counted(stub):
    df = _frame({K + 1: {"low": 100.0}, K + 2: {"low": 100.0}, K + 3: {"low": 100.0}})
    s = _run(df)
    assert s["total_trades"] == 0
    le = s["limit_entry"]
    assert le["n_signals"] == 1 and le["n_filled"] == 0 and le["n_missed"] == 1
    assert le["fill_rate"] == 0.0


def test_fill_only_when_price_trades_through_within_expiry(stub):
    # dips through on K+4: beyond the 3-bar expiry -> missed; on K+3 -> filled
    assert _run(_frame({K + 4: {"low": 99.5}}))["total_trades"] == 0
    s = _run(_frame({K + 3: {"low": 99.5}}))
    assert s["total_trades"] == 1
    assert s["_trades_full"][0].entry_index == K + 3
    assert s["_trades_full"][0].entry == 100.0            # filled AT the limit


def test_fill_bar_credits_stop_but_never_the_target(stub):
    # fill bar reaches 101.6 (>= tp) AND dips through the limit: target NOT credited there;
    # a later bar reaching the target is what exits the trade.
    df = _frame({K + 2: {"low": 99.5, "high": 101.6}, K + 5: {"high": 101.6}})
    t = _run(df)["_trades_full"][0]
    assert t.outcome == "tp_hit" and t.exit_index == K + 5
    assert t.r_multiple == 1.5
    # fill bar that also trades through the stop -> stopped out on the fill bar
    df2 = _frame({K + 2: {"low": 98.9, "high": 101.6}})
    t2 = _run(df2)["_trades_full"][0]
    assert t2.outcome == "sl_hit" and t2.exit_index == K + 2 and t2.r_multiple == -1.0


def test_limit_cost_is_maker_plus_taker_with_exit_only_slippage(stub):
    df = _frame({K + 2: {"low": 99.5}, K + 5: {"high": 101.6}})
    h.SLIPPAGE_BPS_ROUNDTRIP = 4.0
    t = _run(df)["_trades_full"][0]
    cb = h._cost_breakdown(t)
    mid = (t.entry + t.exit_price) / 2.0
    assert cb["fee_r"] == pytest.approx((7.5 / 1e4) * mid / t.risk)        # 2.0 + 5.5
    assert cb["slippage_r"] == pytest.approx((2.0 / 1e4) * mid / t.risk)   # half of 4.0


def test_market_mode_is_unchanged_by_the_new_code_path(stub):
    df = _frame({K + 1: {"high": 101.6}})
    s = h.run_backtest(df, cfg_overrides={}, timeframe="15m", symbol="ETHUSDT",
                       warmup_bars=5, timeout_bars=24, cooldown_bars=3, return_trades=True)
    assert "limit_entry" not in s
    t = s["_trades_full"][0]
    assert t.entry_index == K and "limit_entry" not in t.meta
    assert t.outcome == "tp_hit" and t.exit_index == K + 1     # target credited on the FIRST bar


def test_market_mode_byte_identical_to_pre_change_harness(tmp_path):
    """Run the real strategy on the committed fixture with the pre-change harness
    (git origin/main) and the new one; summary and emitted rows must be equal."""
    import importlib.util
    import subprocess
    src = subprocess.run(["git", "show", "origin/main:scripts/backtest_ict_scalp.py"],
                         capture_output=True, text=True)
    if src.returncode != 0:
        pytest.skip("origin/main not available")
    old_path = h.Path(h.__file__).parent / "_backtest_ict_scalp_premarket_ref.py"
    old_path.write_text(src.stdout)
    try:
        spec = importlib.util.spec_from_file_location("_ref_ict", old_path)
        ref = importlib.util.module_from_spec(spec)
        import sys
        sys.modules["_ref_ict"] = ref          # @dataclass resolves annotations via sys.modules
        spec.loader.exec_module(ref)
        if not hasattr(ref, "run_backtest"):
            pytest.skip("reference harness has no run_backtest")
        df = ref._load_candles("data/backtest_candles.csv").iloc[:4000].reset_index(drop=True)
        kw = dict(cfg_overrides={}, timeframe="5m", symbol="BTCUSDT", warmup_bars=50,
                  timeout_bars=24, cooldown_bars=3)
        for mod in (ref, h):
            mod.FEE_BPS_ROUNDTRIP = 7.5
            mod.SLIPPAGE_BPS_ROUNDTRIP = 3.0
            mod.FUNDING_BPS_PER_WINDOW = 1.0
        a_emit, b_emit = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
        a = ref.run_backtest(df, emit_path=str(a_emit), **kw)
        b = h.run_backtest(df, emit_path=str(b_emit), **kw)
        assert a == b
        assert a_emit.read_text() == b_emit.read_text()
        assert a["total_trades"] > 0, "fixture produced no trades: the equality proves nothing"
        json.dumps(a, default=str)
    finally:
        old_path.unlink(missing_ok=True)
        import sys as _s
        _s.modules.pop("_ref_ict", None)
        _reset()
