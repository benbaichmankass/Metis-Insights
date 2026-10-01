"""ORDER-AUDIT-2 item 7 (AUD-20260927-CA-A15-run-backtest-summarize-fabricated-zeros).

``summarize`` hardcoded max_drawdown / max_drawdown_pct / sharpe_ratio /
total_pnl_pct to 0.0 on every run. They are now measured from the trade list,
or None ("not measured") where no basis exists. First test of this module.
"""
from __future__ import annotations

import math

import pytest

pd = pytest.importorskip("pandas")
from src.backtest.run_backtest import summarize  # noqa: E402

TRADES = [{"net_pnl": 100.0}, {"net_pnl": -300.0}, {"net_pnl": 50.0}, {"net_pnl": 200.0}]


def test_measured_on_a_real_trade_list():
    r = summarize(TRADES, "2026-01-01", "2026-02-01", "v", initial_capital=10_000.0)
    # equity: 10100, 9800, 9850, 10050 -> peak 10100, trough 9800
    assert r["max_drawdown"] == 300.0
    assert r["max_drawdown_pct"] == pytest.approx(300 / 10_100 * 100, abs=1e-3)
    assert r["total_pnl_pct"] == pytest.approx(0.5)
    s = pd.Series([100.0, -300.0, 50.0, 200.0])
    assert r["sharpe_ratio"] == pytest.approx(s.mean() / s.std(ddof=1), abs=1e-3)
    assert r["total_pnl"] == 50.0


def test_no_capital_basis_is_null_not_zero():
    r = summarize(TRADES, "a", "b", "v")
    assert r["max_drawdown"] == 300.0          # USD needs no basis
    assert r["max_drawdown_pct"] is None
    assert r["total_pnl_pct"] is None


def test_sharpe_unmeasurable_is_null():
    assert summarize([{"net_pnl": 5.0}], "a", "b", "v", 1000.0)["sharpe_ratio"] is None
    assert summarize([{"net_pnl": 5.0}] * 3, "a", "b", "v", 1000.0)["sharpe_ratio"] is None


def test_no_trades():
    r = summarize([], "a", "b", "v", initial_capital=10_000.0)
    assert r["max_drawdown"] == 0.0 and r["max_drawdown_pct"] == 0.0
    assert r["sharpe_ratio"] is None
    assert not any(isinstance(v, float) and math.isnan(v) for v in r.values())
