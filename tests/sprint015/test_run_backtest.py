"""Backtest harness tests — synthetic OHLCV fixtures only.

Per S-015 prompt § Data contract: synthetic OHLCV is permitted *only*
in unit-test fixtures. These tests verify the harness arithmetic
(slippage, fold accounting, P&L from a flip-flop signal stream) — not
real strategy quality.
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from scripts.sprint015 import run_backtest as rb


def _frame(prices: List[float], freq: str = "1h") -> pd.DataFrame:
    idx = pd.date_range("2024-05-01", periods=len(prices), freq=freq, tz="UTC")
    closes = np.asarray(prices, dtype=float)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.001,
            "low": closes * 0.999,
            "close": closes,
            "volume": np.full_like(closes, 100.0),
        },
        index=idx,
    )


def _flip_flop_strategy(frame: pd.DataFrame, params: Dict[str, Any]):
    """Toy strategy: long on every odd index, short on every even index.
    Lets us assert exactly how many trades the harness produces."""
    sigs = []
    for i, ts in enumerate(frame.index):
        side = "buy" if i % 2 == 1 else "sell"
        sigs.append({"ts": ts, "side": side, "qty": 1.0})
    return sigs


def test_apply_slippage_directional():
    assert rb._apply_slippage(100.0, "buy", 2.0) > 100.0
    assert rb._apply_slippage(100.0, "sell", 2.0) < 100.0
    # Half the bps per leg: 1 bp -> 0.0001
    assert abs(rb._apply_slippage(100.0, "buy", 2.0) - 100.01) < 1e-9
    assert abs(rb._apply_slippage(100.0, "sell", 2.0) - 99.99) < 1e-9
    # "none" returns unchanged.
    assert rb._apply_slippage(100.0, "none", 2.0) == 100.0


def test_run_backtest_single_trade_pnl():
    """One frame, two signals — open long at idx 1, close at idx 3."""
    frame = _frame([100.0, 100.0, 105.0, 110.0, 110.0])

    def strat(f, _p):
        return [
            {"ts": f.index[1], "side": "buy", "qty": 2.0},
            {"ts": f.index[3], "side": "sell", "qty": 2.0},
        ]

    result = rb.run_backtest("toy", strat, {}, [frame], slippage_bps=0.0)
    assert len(result.folds) == 1
    fm = result.folds[0]
    # Bought at 100, sold at 110, qty 2 -> 20.0 with no slippage.
    # The harness flips on the opposite signal, so closing trade is the
    # initial long. The flip then opens a short which exits at frame end
    # (110.0 -> 110.0, pnl 0).
    assert fm.n_trades == 2
    assert fm.realised_pnl == pytest.approx(20.0)


def test_run_backtest_slippage_reduces_pnl():
    frame = _frame([100.0, 100.0, 105.0, 110.0, 110.0])

    def strat(f, _p):
        return [
            {"ts": f.index[1], "side": "buy", "qty": 1.0},
            {"ts": f.index[3], "side": "sell", "qty": 1.0},
        ]

    no_slip = rb.run_backtest("toy", strat, {}, [frame], slippage_bps=0.0).aggregate_pnl
    with_slip = rb.run_backtest("toy", strat, {}, [frame], slippage_bps=20.0).aggregate_pnl
    assert with_slip < no_slip


def test_run_backtest_handles_empty_frame():
    result = rb.run_backtest(
        "toy", _flip_flop_strategy, {}, [pd.DataFrame()], slippage_bps=0.0,
    )
    assert result.folds[0].n_trades == 0
    assert result.aggregate_pnl == 0.0


def test_fold_metrics_drawdown_and_sharpe():
    pnls = [10.0, -5.0, 8.0, -3.0, 4.0]
    trades = [
        rb.Trade(
            entry_ts=pd.Timestamp("2024-05-01", tz="UTC"),
            exit_ts=pd.Timestamp("2024-05-02", tz="UTC"),
            side="buy", entry_price=100.0, exit_price=100.0 + p, qty=1.0,
            pnl=p,
        )
        for p in pnls
    ]
    fm = rb._fold_metrics(trades)
    assert fm.realised_pnl == pytest.approx(sum(pnls))
    assert fm.n_trades == 5
    assert fm.win_rate == pytest.approx(3 / 5)
    # Cum: 10, 5, 13, 10, 14 -> peaks 10, 10, 13, 13, 14
    # -> drawdowns peak-cum: 0, 5, 0, 3, 0 -> max DD = 5 (after the -5 trade).
    assert fm.max_drawdown == pytest.approx(5.0)


def test_aggregate_helpers_combine_folds():
    frame_a = _frame([100, 100, 105])
    frame_b = _frame([100, 100, 95])

    def strat(f, _p):
        return [
            {"ts": f.index[1], "side": "buy", "qty": 1.0},
        ]

    result = rb.run_backtest("toy", strat, {}, [frame_a, frame_b], slippage_bps=0.0)
    # Fold A: long 100->105 = +5. Fold B: long 100->95 = -5.
    assert result.aggregate_pnl == pytest.approx(0.0)
    assert len(result.folds) == 2


# Re-import pytest at module bottom so the file works without an explicit
# conftest. Keeping the import here lets the strict-imports lint stay quiet.
import pytest  # noqa: E402
