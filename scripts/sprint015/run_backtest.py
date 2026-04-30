"""S-015 backtest harness.

Pure-function: takes a strategy name + params + a list of OHLCV frames
(one per month bucket), returns deterministic metrics. Does **not**
import anything from ``src.runtime.orders`` / ``src.runtime.notify`` /
``src.bot.*``. The fill model lives here and is intentionally cheap:

* Entry at the close of the signal bar (the price the strategy "saw").
* Exit at the bar where the strategy fires the opposite side, or at
  end-of-bucket if the strategy never closes the position.
* 2 bps slippage round-trip, applied symmetrically to both legs.

This is good enough for relative comparisons (parameter A vs B) which
is all S-015 needs. Absolute P&L will differ from production fills.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

DEFAULT_SLIPPAGE_BPS = 2.0  # round-trip, split symmetrically across legs


@dataclass
class Trade:
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    side: str
    entry_price: float
    exit_price: float
    qty: float
    pnl: float


@dataclass
class FoldMetrics:
    realised_pnl: float
    n_trades: int
    win_rate: float
    sharpe: float
    max_drawdown: float
    trades: List[Trade] = field(default_factory=list)


@dataclass
class BacktestResult:
    strategy: str
    params: Dict[str, Any]
    folds: List[FoldMetrics]

    @property
    def aggregate_pnl(self) -> float:
        return float(sum(f.realised_pnl for f in self.folds))

    @property
    def aggregate_sharpe(self) -> float:
        per_fold = np.array([f.sharpe for f in self.folds], dtype=float)
        return float(per_fold.mean()) if per_fold.size else 0.0

    @property
    def aggregate_max_dd(self) -> float:
        return float(max((f.max_drawdown for f in self.folds), default=0.0))


# Strategy adapter contract: a callable
#   (frame, params) -> Iterable[{ "ts": Timestamp, "side": "buy"|"sell"|"none", "qty": float }]
# emitting one signal per bar (or only on bars where the strategy fires).
StrategyFn = Callable[[pd.DataFrame, Dict[str, Any]], Sequence[Dict[str, Any]]]


def _apply_slippage(price: float, side: str, slippage_bps: float) -> float:
    """Buy fills a hair above mid; sell fills a hair below."""
    half = slippage_bps / 2.0 / 1e4
    if side == "buy":
        return price * (1.0 + half)
    if side == "sell":
        return price * (1.0 - half)
    return price


def _apply_signals(
    frame: pd.DataFrame,
    signals: Sequence[Dict[str, Any]],
    slippage_bps: float,
) -> List[Trade]:
    """Walk the bar-by-bar signals into trades. Single-position model:
    a long signal opens a long; a sell signal closes the long *and*
    opens a short. Symmetric for the short side. Final open position
    closes at end-of-frame (mark-to-market exit at the last close)."""
    trades: List[Trade] = []
    open_side: Optional[str] = None
    open_price: Optional[float] = None
    open_ts: Optional[pd.Timestamp] = None
    open_qty: float = 0.0

    for sig in signals:
        ts = sig["ts"]
        side = sig.get("side", "none")
        qty = float(sig.get("qty", 0.0) or 0.0)
        if side not in ("buy", "sell") or qty <= 0:
            continue
        if ts not in frame.index:
            continue
        price = float(frame.loc[ts, "close"])
        if open_side is None:
            open_side = side
            open_price = _apply_slippage(price, side, slippage_bps)
            open_ts = ts
            open_qty = qty
            continue
        if side == open_side:
            # Same direction — pyramid is a no-op for the relative-PnL
            # comparisons S-015 needs; ignore the second signal.
            continue
        # Opposite-side signal: close the open leg, then optionally
        # flip into a new opposite position.
        exit_price = _apply_slippage(price, "sell" if open_side == "buy" else "buy", slippage_bps)
        sign = 1.0 if open_side == "buy" else -1.0
        pnl = sign * (exit_price - open_price) * open_qty
        trades.append(Trade(
            entry_ts=open_ts, exit_ts=ts, side=open_side,
            entry_price=open_price, exit_price=exit_price,
            qty=open_qty, pnl=pnl,
        ))
        # Flip
        open_side = side
        open_price = _apply_slippage(price, side, slippage_bps)
        open_ts = ts
        open_qty = qty

    if open_side is not None and open_ts is not None and not frame.empty:
        last_ts = frame.index[-1]
        last_close = float(frame.iloc[-1]["close"])
        exit_price = _apply_slippage(
            last_close, "sell" if open_side == "buy" else "buy", slippage_bps,
        )
        sign = 1.0 if open_side == "buy" else -1.0
        pnl = sign * (exit_price - open_price) * open_qty
        trades.append(Trade(
            entry_ts=open_ts, exit_ts=last_ts, side=open_side,
            entry_price=open_price, exit_price=exit_price,
            qty=open_qty, pnl=pnl,
        ))
    return trades


def _fold_metrics(trades: List[Trade]) -> FoldMetrics:
    if not trades:
        return FoldMetrics(0.0, 0, 0.0, 0.0, 0.0, [])
    pnl_series = np.array([t.pnl for t in trades], dtype=float)
    realised = float(pnl_series.sum())
    n = len(trades)
    win_rate = float((pnl_series > 0).sum()) / n
    std = float(pnl_series.std(ddof=0))
    sharpe = float(pnl_series.mean() / std) if std > 0 else 0.0
    cum = pnl_series.cumsum()
    peak = np.maximum.accumulate(cum)
    drawdowns = peak - cum
    max_dd = float(drawdowns.max()) if drawdowns.size else 0.0
    return FoldMetrics(
        realised_pnl=realised,
        n_trades=n,
        win_rate=win_rate,
        sharpe=sharpe,
        max_drawdown=max_dd,
        trades=trades,
    )


def run_backtest(
    strategy_name: str,
    strategy_fn: StrategyFn,
    params: Dict[str, Any],
    fold_frames: Sequence[pd.DataFrame],
    *,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
) -> BacktestResult:
    """Run *strategy_fn* on each fold's DataFrame and collect metrics.

    Each fold is a pre-loaded OHLCV frame for one or more month buckets;
    the harness does not concatenate folds — fold-wise statistics keep
    paired t-tests honest.
    """
    folds: List[FoldMetrics] = []
    for frame in fold_frames:
        if frame is None or frame.empty:
            folds.append(FoldMetrics(0.0, 0, 0.0, 0.0, 0.0, []))
            continue
        signals = list(strategy_fn(frame, params))
        trades = _apply_signals(frame, signals, slippage_bps)
        folds.append(_fold_metrics(trades))
    return BacktestResult(strategy=strategy_name, params=dict(params), folds=folds)
