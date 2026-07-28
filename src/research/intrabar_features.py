"""M30 × M20 — per-bar IN-TRADE state features for the exit-timing head.

The M30 decision-time feature wall is now well-established: entry-side
decision-time features are ~coin-flip out-of-sample (Studies 7/9) and pooling
them across the roster hits a block-sparsity wall (Studies 1/3/4/10). The
operator's load-bearing prior — *edge lives in exit/regime* — points the same
rigor at a **different, denser information set**: the in-trade PATH. Given an
already-open position and its trajectory so far, a small set of **path** features
(running MFE/MAE in R, how far into the trade we are, how fast adverse excursion
is growing, the cushion left to the stop, how much peak profit has been given
back, in-trade realized volatility) is populated on **every bar of every trade** —
so, unlike the block-sparse decision-time vector, a per-bar panel built from it is
**dense by construction** and the out-of-sample multivariate pass finally runs at
scale.

This module is the small, **pure, unit-testable** core that turns a trade's
post-entry candle path *so far* into that feature vector, as of the last bar of
the supplied path. It is the FEATURE half of the per-bar exit panel; the LABEL
half is ``src.research.triple_barrier`` (strictly-future) and the two never
overlap — the leakage invariant the whole platform enforces.

**Strictly past-only.** Every feature is computed from ``path_candles`` =
``[entry_index+1 .. t]`` (the bars the position has been open through bar ``t``),
never a future bar. The caller (the builder) slices that window; this module only
reads it. No I/O, no network, no numpy — deterministic arithmetic over the
provided candles, so it is exhaustively testable with synthetic paths and is
Tier-1 by construction.

Definitions (long; short mirrored), risk ``R = |entry - stop|``:
  running_mfe_r   max favorable excursion so far / R      (>= 0)
  running_mae_r   max adverse   excursion so far / R      (>= 0)
  upnl_r          (close_t - entry) * dir / R             (signed mark-to-market)
  mfe_giveback_r  max(0, running_mfe_r - max(upnl_r, 0))  (peak profit surrendered)
  bars_in_trade   number of bars held so far (len(path))
  bars_in_trade_frac   bars_in_trade / expected_hold      (progress toward the time-stop)
  dist_to_stop_atr     (close_t - stop) * dir / entry_atr (cushion to the stop, ATR units)
  in_trade_vol_ratio   mean(high-low over path) / entry_atr (in-trade range vol vs entry ATR)
  dmae_dt         (running_mae_r_t - running_mae_r_{t-w}) / w   (R/bar, adverse-growth rate)
  taker_imbalance      2*taker_buy_base/volume - 1        (last bar; OFI proxy, None if absent)
  taker_imbalance_intrade   mean signed taker imbalance over the path (None if absent)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from src.research.excursions import _is_long, _num, compute_excursions

Candle = Dict[str, Any]

# The dense per-bar feature column names (all populated on every in-trade bar,
# except the two taker columns which are None when the feed carries no taker
# volume — the honest degradation for a non-Binance-vision feed).
INTRABAR_FEATURE_NAMES: List[str] = [
    "running_mfe_r",
    "running_mae_r",
    "upnl_r",
    "mfe_giveback_r",
    "bars_in_trade",
    "bars_in_trade_frac",
    "dist_to_stop_atr",
    "in_trade_vol_ratio",
    "dmae_dt",
    "taker_imbalance",
    "taker_imbalance_intrade",
]


def _taker_imbalance(candle: Any) -> Optional[float]:
    """Signed taker buy/sell imbalance ``2*taker_buy_base/volume - 1`` in [-1, 1].

    The best free order-flow-imbalance (OFI) proxy: the Binance-vision klines
    payload carries ``taker_buy_base`` (the aggressive-buy share of the bar's
    volume). ``None`` when the column is absent (Bybit klines / any feed without
    the taker split) or the volume is non-positive — an honest missing value, not
    a fabricated 0.
    """
    if not isinstance(candle, dict):
        return None
    tb = _num(candle.get("taker_buy_base"))
    vol = _num(candle.get("volume"))
    if tb is None or vol is None or vol <= 0:
        return None
    # clamp defends against a feed where taker_buy_base slightly exceeds volume
    # (rounding) so the signed value stays in [-1, 1].
    ratio = tb / vol
    ratio = 0.0 if ratio < 0 else (1.0 if ratio > 1 else ratio)
    return 2.0 * ratio - 1.0


def _mae_r(path: Sequence[Candle], *, entry, stop, side) -> Optional[float]:
    res = compute_excursions(path, entry_price=entry, stop_loss=stop, side=side)
    return res.get("mae_r")


def intrabar_features(
    path_candles: Sequence[Candle],
    *,
    entry_price: Any,
    stop_loss: Any,
    side: Any,
    entry_atr: Any,
    expected_hold_bars: float,
    dmae_window: int = 3,
    round_to: int = 6,
) -> Dict[str, Optional[float]]:
    """In-trade state features as of the LAST bar of ``path_candles``.

    ``path_candles`` are the post-entry bars ``[entry_index+1 .. t]`` in
    chronological order (so ``len == bars_in_trade``). Tolerant: missing geometry
    yields an honest ``None``-filled record, never a raise. Every value is decision
    state *as of bar t* — strictly past; never a future bar.
    """
    feats: Dict[str, Optional[float]] = {k: None for k in INTRABAR_FEATURE_NAMES}
    entry = _num(entry_price)
    stop = _num(stop_loss)
    atr = _num(entry_atr)
    is_long = _is_long(side)
    path = [c for c in path_candles if isinstance(c, dict)]
    n = len(path)
    feats["bars_in_trade"] = float(n)
    if expected_hold_bars and expected_hold_bars > 0:
        feats["bars_in_trade_frac"] = round(n / float(expected_hold_bars), round_to)
    if entry is None or is_long is None or n == 0:
        return feats

    dir_sign = 1.0 if is_long else -1.0
    last_close = _num(path[-1].get("close"))
    risk = abs(entry - stop) if stop is not None else None

    # Running excursions over the path SO FAR (reuse the canonical pure math);
    # exit_price = the current close gives the mark-to-market realized_r (upnl_r).
    exc = compute_excursions(
        path, entry_price=entry, stop_loss=stop, side=side, exit_price=last_close
    )
    feats["running_mfe_r"] = exc.get("mfe_r")
    feats["running_mae_r"] = exc.get("mae_r")
    feats["upnl_r"] = exc.get("realized_r")
    feats["mfe_giveback_r"] = exc.get("giveback_r")

    # Distance to the stop in ATR units (cushion left before the stop).
    if last_close is not None and stop is not None and atr and atr > 0:
        feats["dist_to_stop_atr"] = round((last_close - stop) * dir_sign / atr, round_to)

    # In-trade realized-range volatility, normalized by the entry ATR (unitless).
    if atr and atr > 0:
        ranges = []
        for c in path:
            hi, lo = _num(c.get("high")), _num(c.get("low"))
            if hi is not None and lo is not None:
                ranges.append(hi - lo)
        if ranges:
            feats["in_trade_vol_ratio"] = round((sum(ranges) / len(ranges)) / atr, round_to)

    # dMAE/dt — how fast adverse excursion is growing (R per bar over the window).
    # Dense by construction: 0.0 (no adverse growth measured yet) until the window
    # of history exists, so early-in-trade bars are not structurally-null.
    w = max(1, int(dmae_window))
    if risk and risk > 0:
        feats["dmae_dt"] = 0.0
        if n > w:
            mae_now = feats["running_mae_r"]
            mae_prev = _mae_r(path[:-w], entry=entry, stop=stop, side=side)
            if mae_now is not None and mae_prev is not None:
                feats["dmae_dt"] = round((mae_now - mae_prev) / w, round_to)

    # Taker buy/sell imbalance — the free OFI proxy (last bar + in-trade mean).
    last_imb = _taker_imbalance(path[-1])
    if last_imb is not None:
        feats["taker_imbalance"] = round(last_imb, round_to)
    imbs = [v for v in (_taker_imbalance(c) for c in path) if v is not None]
    if imbs:
        feats["taker_imbalance_intrade"] = round(sum(imbs) / len(imbs), round_to)

    return feats


def entry_atr_from_prewindow(
    candles: Sequence[Candle], entry_index: int, *, period: int = 14
) -> Optional[float]:
    """Mean true range over the ``period`` bars BEFORE (and up to) ``entry_index``.

    A decision-time volatility unit for the ATR-normalized features (computed
    from bars strictly at/before entry, so it never leaks future info). Tolerant:
    ``None`` when the window is unavailable/degenerate.
    """
    try:
        ei = int(entry_index)
    except (TypeError, ValueError):
        return None
    lo = max(0, ei - int(period))
    window = list(candles[lo : ei + 1])
    if len(window) < 2:
        return None
    trs: List[float] = []
    prev_close: Optional[float] = None
    for c in window:
        if not isinstance(c, dict):
            prev_close = None
            continue
        hi, lo_, cl = _num(c.get("high")), _num(c.get("low")), _num(c.get("close"))
        if hi is None or lo_ is None:
            prev_close = cl
            continue
        tr = hi - lo_
        if prev_close is not None:
            tr = max(tr, abs(hi - prev_close), abs(lo_ - prev_close))
        trs.append(tr)
        prev_close = cl
    if not trs:
        return None
    atr = sum(trs) / len(trs)
    return atr if atr > 0 else None
