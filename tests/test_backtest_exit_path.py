"""The backtest harness's EXIT effectuation (P2 · unified engine).

`tests/test_monitor_verdict.py` pins what a verdict MEANS. This pins what the
harness DOES with it — the half that used to be re-implemented at the call site
and silently narrowed:

  * a full close now fills at the verdict's ``exit_price`` (was: the bar close);
  * a ``close_qty_pct < 1`` partial books only the scaled-out portion and leaves
    a RUNNER open (was: closed 100%, so the runner never existed in backtest);
  * ``next_tp`` rolls the runner's target forward (was: ignored, so even a
    working partial would have exited at the target it just took profit at);
  * a monitor that RAISES is counted, not silently read as "no action".

Driven through the real ``run_system_backtest`` loop with a synthetic strategy
registered in ``ROSTER`` — the loop, the position bookkeeping and the summary
are the live harness code, only the strategy is a stub. That way the test fails
if the effectuation regresses, not merely if the interpreter does.

Synthetic PRICES on purpose: this asserts mechanism (qty, fills, counts), never
a PnL result, so it needs no market data and makes no claim about one.
"""
from __future__ import annotations

import os
import sys
import types

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.backtest_system as bs  # noqa: E402


#: `generate_signal_stream` warms up 260 bars OF THE RESAMPLED FRAME before it
#: calls the strategy at all, so a 15m roster entry needs >260*3 five-minute
#: bars to emit anything. Sized well past that: an under-sized fixture yields
#: zero trades, and "0 trades, no errors" reads exactly like a clean pass.
_MIN_5M_BARS = 260 * 3 + 200


def _flat_candles(n: int = _MIN_5M_BARS, price: float = 100.0) -> pd.DataFrame:
    """A perfectly flat 5m series: no bar can ever touch an SL/TP, so EVERY
    exit in these tests is necessarily monitor-driven. That isolation is the
    point — an intrabar SL fill would confound the assertion."""
    ts = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": price, "high": price,
                         "low": price, "close": price, "volume": 1.0})


def _register(monkeypatch, tmp_path, name: str, monitor_fn, *,
              entry=100.0, sl=90.0, tp=130.0):
    """Register a synthetic single-signal strategy in the harness ROSTER."""
    mod_name = f"_bt_stub_{name}"
    mod = types.ModuleType(mod_name)
    state = {"emitted": False}

    # NB the harness calls this as `order_package(cfg, candles_df=window)` —
    # the keyword is part of the contract, and a mismatched signature is
    # swallowed by the sweep's broad `except Exception: continue`, which
    # presents as "the strategy emitted no signals".
    def order_package(cfg, candles_df):
        # one signal, on the first bar the harness offers
        if state["emitted"]:
            raise ValueError("no signal")
        state["emitted"] = True
        return {"direction": "long", "entry": entry, "sl": sl, "tp": tp,
                "confidence": 0.7, "meta": {"setup_type": "stub"}}

    mod.order_package = order_package
    mod.monitor = monitor_fn
    monkeypatch.setitem(sys.modules, mod_name, mod)
    monkeypatch.setitem(bs.ROSTER, name, {"module": mod_name, "tf": "15m"})
    monkeypatch.setattr(bs, "_load_strategy_cfg", lambda _n: {})
    # keep the parquet signal cache out of the repo and out of other tests
    monkeypatch.setattr(bs, "_cache_key",
                        lambda *a, **k: tmp_path / f"{name}.parquet")


def _run(name: str) -> dict:
    return bs.run_system_backtest(
        _flat_candles(), roster=[name], start=None, end=None,
        initial_balance=10_000.0, risk_pct=1.0, daily_loss_pct=100.0,
        signal_ttl_bars=2, overrides={}, refresh=True, clock_tf="15m")


def test_partial_close_leaves_a_runner_and_rolls_the_target(monkeypatch, tmp_path):
    """turtle_soup's shape. Before P2 this booked ONE full-size trade and the
    runner — the part that earns the trend — did not exist in backtest."""
    calls = {"n": 0}

    def monitor(cfg, candles, open_pkg):
        calls["n"] += 1
        if calls["n"] == 3:
            return {"action": "close", "close_qty_pct": 0.5,
                    "next_tp": 150.0, "exit_price": 110.0, "reason": "tp1"}
        if calls["n"] == 6:
            return {"action": "close", "exit_price": 120.0, "reason": "tp2"}
        return None

    _register(monkeypatch, tmp_path, "stub_partial", monitor)
    s = _run("stub_partial")

    assert s["total_trades"] == 2, "expected a scale-out AND a runner"
    assert s["by_exit_reason"].get("tp1") == 1
    assert s["by_exit_reason"].get("tp2") == 1, "the runner never closed"


def test_partial_splits_the_quantity_and_conserves_it(monkeypatch, tmp_path):
    """The scaled-out leg and the runner must SPLIT the position. Booking each
    at full size would double the size (and the risk) the run reports.

    Sizing is the live formula: risk_usd / stop_distance
    = (10_000 * 1%) / |100 - 90| = 10.0 units, so a 50% scale-out is 5.0 + 5.0.
    """
    booked: list[dict] = []
    original = bs._ClosedTrade

    def _record(**kw):
        booked.append(kw)
        return original(**kw)

    monkeypatch.setattr(bs, "_ClosedTrade", _record)

    calls = {"n": 0}

    def monitor(cfg, candles, open_pkg):
        calls["n"] += 1
        if calls["n"] == 3:
            return {"action": "close", "close_qty_pct": 0.5,
                    "exit_price": 110.0, "reason": "tp1"}
        if calls["n"] == 6:
            return {"action": "close", "exit_price": 110.0, "reason": "tp2"}
        return None

    _register(monkeypatch, tmp_path, "stub_half", monitor)
    _run("stub_half")

    assert len(booked) == 2, f"expected scale-out + runner, got {len(booked)}"
    scale_out, runner = booked
    assert scale_out["reason"] == "tp1" and runner["reason"] == "tp2"
    assert scale_out["qty"] == pytest.approx(5.0)
    assert runner["qty"] == pytest.approx(5.0)
    # conservation: the two legs sum to exactly the position that was opened
    assert scale_out["qty"] + runner["qty"] == pytest.approx(10.0)


def test_full_close_fills_at_the_verdict_exit_price(monkeypatch, tmp_path):
    """4 of 9 roster monitors emit exit_price. The harness used the bar close,
    so every trail-stop exit was booked at the wrong price. Flat series at
    100.0 with an exit_price of 110.0 makes the two unmistakable."""
    calls = {"n": 0}

    def monitor(cfg, candles, open_pkg):
        calls["n"] += 1
        if calls["n"] == 3:
            return {"action": "close", "exit_price": 110.0, "reason": "trail"}
        return None

    _register(monkeypatch, tmp_path, "stub_exitpx", monitor)
    s = _run("stub_exitpx")
    assert s["total_trades"] == 1
    # entry 100 -> exit 110 on a flat-100 series: PnL can only be positive if
    # the verdict price was honoured. At the bar close it would be ~0 minus fees.
    assert s["net_pnl"] > 0, (
        "exit booked at the bar close, not the verdict's exit_price")


def test_a_raising_monitor_is_counted_not_swallowed(monkeypatch, tmp_path):
    """A broken exit path must not read as a quiet one — that is the
    silent-empty class, and it would make a fidelity run look clean."""
    def monitor(cfg, candles, open_pkg):
        raise RuntimeError("boom")

    _register(monkeypatch, tmp_path, "stub_boom", monitor)
    s = _run("stub_boom")
    me = s["monitor_errors"]
    assert me["total"] > 0, "a raising monitor was silently swallowed"
    assert me["by_owner"].get("stub_boom") == me["total"]
    assert "RuntimeError: boom" in me["examples"]["stub_boom"]


def test_clean_run_reports_zero_monitor_errors(monkeypatch, tmp_path):
    """The honest denominator: absent errors must be an explicit zero, so a
    reader can tell 'the exit path ran clean' from 'nobody counted'."""
    def monitor(cfg, candles, open_pkg):
        return None

    _register(monkeypatch, tmp_path, "stub_quiet", monitor)
    s = _run("stub_quiet")
    assert s["monitor_errors"]["total"] == 0


def test_sl_and_tp_in_one_verdict_both_apply(monkeypatch, tmp_path):
    """Latent today (no roster monitor emits both) — pinned at the harness
    level too, because the old `elif` chain lived here, not in the interpreter."""
    calls = {"n": 0}
    seen = {}

    def monitor(cfg, candles, open_pkg):
        calls["n"] += 1
        if calls["n"] == 2:
            return {"sl": 95.0, "tp": 140.0}
        if calls["n"] == 4:
            seen["sl"] = open_pkg["sl"]
            seen["tp"] = open_pkg["tp"]
            return {"action": "close", "reason": "done"}
        return None

    _register(monkeypatch, tmp_path, "stub_both", monitor)
    _run("stub_both")
    assert seen.get("sl") == pytest.approx(95.0)
    assert seen.get("tp") == pytest.approx(140.0), "tp dropped by an elif chain"
