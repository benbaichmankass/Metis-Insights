"""FVG range / mean-reversion — units-layer adapter
(S-STRAT-IMPROVE, complementary-strategy R&D, 2026-05-30).

Strategy summary
----------------
The range member the roster was missing. trend_donchian rides breakouts,
squeeze_breakout_4h trades the expansion, fade_breakout_4h fades a *failed*
breakout, turtle_soup fades a sweep, and vwap reverts to a *drifting* anchor
(trend-gated, no net edge). None of them trade a confirmed STATIC horizontal
range — price oscillating between fixed support & resistance where the bounce
continues (mean reversion to the range interior). This fills that gap.

It is also DELIBERATELY the opposite of ict_scalp_5m: ict_scalp uses an FVG
DIRECTIONALLY (sweep -> displacement -> FVG continuation in the breakout
direction, momentum). This uses an UNFILLED FVG as a mean-reversion S/R level
INSIDE a confirmed range — opposite intent, opposite regime.

A signal fires on the most recent closed bar when **all** of:

1. **Confirmed range** over the prior ``range_lookback`` bars: resistance =
   rolling max of highs, support = rolling min of lows (prior window only, no
   lookahead). Width must sit in [``min_width_pct``, ``max_width_pct``] of
   price (reject channels too tight to clear fees or too wide to be a range),
   and EACH boundary must have been touched >= ``min_touches`` times within
   ``touch_tol_pct`` of price (an oscillating range, not a one-touch spike).
2. **Chop regime**: ADX(``adx_period``) of the prior bar < ``adx_max``. A
   range needs no trend; this is the regime where the trend-followers are flat
   (the source of the diversification).
3. **Location**: price is inside the range AND in its lower ``third_frac``
   (long candidate) or upper ``third_frac`` (short candidate).
4. **Unfilled FVG at the boundary**: the most recent matching-direction FVG
   within ``fvg_search`` bars whose midpoint sits in the lower/upper third and
   that is still intact (no later bar CLOSED through its far edge). Bullish
   3-candle FVG = high[k-2] < low[k]; bearish = low[k-2] > high[k].
5. **Wick rejection** on the current bar at the gap (the same confirmation
   ict_scalp uses, inverted intent): long = low wicks into/below gap_high AND
   close back ABOVE it AND bullish body; short = mirror at gap_low.

On a signal the unit emits:
  * ``entry``      = close of the most recent bar
  * ``sl``         = beyond the gap AND the range boundary — long:
                     min(gap_low, support) - ``atr_stop_buffer`` x ATR; short:
                     max(gap_high, resistance) + buffer x ATR. A range BREAK
                     invalidates the thesis, so the stop sits past the boundary.
  * ``tp``         = the OPPOSITE boundary (resistance for a long, support for a
                     short) — full-range reversion. Backtest: the far boundary
                     decisively beats the midline (the tight-target/fee trap
                     that sank vwap on BTC), the same let-winners-run lever the
                     trend-followers proved.
  * ``confidence`` = blend of location depth (closer to the boundary scores
                     higher) + FVG size / ATR, clamped [0, 1].

Validation (research harness ``scripts/backtest_fvg_range.py``, 5.2yr BTCUSDT
15m 2021-01..2026-02, net-of-fee, chosen config range_lookback=48 / touches=4 /
ADX<20 / far-boundary target / stop 0.25xATR):
  * FULL 5y: +24.35R, expectancy +0.363, win-rate 50.8%, max-DD 3.0R, 57% of
    months positive — BOTH long (+15.85R) and short (+8.50R) net-positive.
  * Walk-forward (train 2021-2023 / OOS 2024-2026): the train config HOLDS
    out-of-sample and is in fact STRONGER (OOS +21.76R, exp +0.518, both sides
    positive) — no overfit decay, the opposite of fade_breakout_4h's OOS halving.
  * Robust plateau across range_lookback 40-48 x touches 4-5 x ADX 18-22.
  * Fee-robust: still +10.45R net at 15bps round-trip (2x the modelled 7.5).
  * CAVEAT (why it ships SHADOW, not live): low frequency (67 trades / 5.2y),
    and the edge is concentrated in the recent regime — train (2021-2023)
    expectancy is a modest +0.10R. The live shadow data must confirm the
    backtest before any promotion. Full evidence:
    docs/audits/fvg-range-complement-2026-05-30.md.

This adapter ports the validated entry logic from scripts/backtest_fvg_range.py
into the live ``order_package(cfg, candles_df) -> dict`` contract VERBATIM
(live-parity, the discipline fade_breakout_4h / trend_donchian follow). The
``monitor()`` implements the backtest's time-decay backstop (close at market
after ``timeout_bars``) so live exits match the simulation — no trailing, no
break-even (a premature stop would cut the wick-against-then-revert bounces the
edge depends on).

Strategies are pure signal generators (no dry/live awareness); the dry/live
decision lives in the Accounts layer per ``mode:`` / ``execution:`` in config.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from src.units.strategies._base import require_candles


# Defaults mirror the validated config from scripts/backtest_fvg_range.py +
# the walk-forward winner (docs/audits/fvg-range-complement-2026-05-30.md):
# 15m / range_lookback 48 / touches 4 / ADX<20 / far-boundary target / stop
# 0.25xATR. Any caller may override via cfg.get(<name>); the runtime builder
# merges config/strategies.yaml::fvg_range_15m params into cfg.
_DEFAULTS: Dict[str, Any] = {
    "range_lookback": 48,        # prior bars defining support/resistance (~12h on 15m)
    "atr_period": 14,
    "adx_period": 14,
    # Regime gate: only trade chop (a range needs no trend). Mirrors
    # fade_breakout_4h's ADX<20 chop gate. A 5y sweep found 18-22 all
    # net-positive at touches=4; 20 is the roster-consistent center.
    "adx_max": 20.0,
    # Width bounds as a fraction of price: reject ranges too tight to clear
    # fees (< 1.5%) or too wide to be a static range (> 12%).
    "min_width_pct": 0.015,
    "max_width_pct": 0.12,
    # Boundary-touch confirmation: each side touched >= min_touches within
    # touch_tol_pct of price over the prior window. touches=4 is the
    # edge-defining gate (a genuinely oscillating range, not a one-touch
    # spike) — touches=2/3 are marginal, 4 is where the edge appears.
    "touch_tol_pct": 0.002,
    "min_touches": 4,
    # Lower/upper fraction of the range that admits a long/short entry.
    "third_frac": 0.34,
    # Bars back to scan for an unfilled matching FVG, and the min gap size.
    "fvg_search": 24,
    "min_fvg_size_bps": 2.0,
    # Stop placed this many ATR beyond the gap / range boundary.
    "atr_stop_buffer": 0.25,
    # Time-decay backstop: close at market after this many bars if the range
    # stalls (matches the backtest timeout). Consumed by monitor().
    "timeout_bars": 48,
    # Minimum signal confidence (location depth + FVG/ATR, [0,1]). A 5y sweep
    # found NO confidence floor improves net_R (the regime gates already do
    # the filtering) — kept wired (default off) for a future regime.
    "min_confidence": 0.0,
    "timeframe": "15m",
}


def _resolve_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return strategy params with cfg overrides on top of _DEFAULTS."""
    return {key: cfg.get(key, default) for key, default in _DEFAULTS.items()}


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR as the SMA of True Range — identical formula to
    scripts/backtest_fvg_range.py::_atr so the live stop distance matches
    what was validated."""
    h, low, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's ADX — identical to scripts/backtest_fvg_range.py::_adx and
    fade_breakout_4h._adx so the live regime gate matches the validated one.
    Low ADX = chop (where a range lives). The float('nan') divide-by-zero
    guard (not pd.NA) keeps the Series numeric on flat bars — see
    fade_breakout_4h._adx for the crash this prevents."""
    h, low, c = df["high"], df["low"], df["close"]
    up = h.diff()
    down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up.clip(lower=0)
    minus_dm = ((down > up) & (down > 0)) * down.clip(lower=0)
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    return dx.ewm(alpha=alpha, adjust=False).mean()


def _find_range_fvg(
    h, lo_arr, c, i: int, *, direction: str, fvg_search: int,
    min_fvg_size_bps: float, S: float, R: float, width: float,
    third_frac: float,
) -> Optional[Tuple[float, float]]:
    """Most recent UNFILLED FVG of ``direction`` whose midpoint sits in the
    lower/upper ``third_frac`` of the range, scanning the last ``fvg_search``
    bars. Returns (gap_low, gap_high) or None.

    "Unfilled" = since the gap formed at bar k, no later bar (k+1..i-1) has
    CLOSED through the gap's far edge: bullish gap (support) keeps an intact
    floor (no later close < gap_low); bearish gap (resistance) keeps an intact
    cap (no later close > gap_high). VERBATIM port of the backtest helper.
    """
    lo_search = max(2, i - fvg_search)
    lower_third = S + width * third_frac
    upper_third = R - width * third_frac
    for k in range(i, lo_search - 1, -1):
        if direction == "long":
            g_lo = h[k - 2]
            g_hi = lo_arr[k]
            if not (g_lo < g_hi):
                continue
            size_bps = (g_hi - g_lo) / c[k] * 10_000.0 if c[k] > 0 else 0.0
            if size_bps < min_fvg_size_bps:
                continue
            mid = (g_lo + g_hi) / 2.0
            if mid > lower_third:
                continue
            if k < i and np.any(c[k + 1:i] < g_lo):
                continue
            return float(g_lo), float(g_hi)
        else:
            g_hi = lo_arr[k - 2]
            g_lo = h[k]
            if not (g_hi > g_lo):
                continue
            size_bps = (g_hi - g_lo) / c[k] * 10_000.0 if c[k] > 0 else 0.0
            if size_bps < min_fvg_size_bps:
                continue
            mid = (g_lo + g_hi) / 2.0
            if mid < upper_third:
                continue
            if k < i and np.any(c[k + 1:i] > g_hi):
                continue
            return float(g_lo), float(g_hi)
    return None


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build a fvg_range_15m OrderPackage dict from the latest candles.

    Returns a dict with keys: symbol, direction, entry, sl, tp, confidence,
    meta.

    Raises ``ValueError`` (non-actionable; the runtime builder treats it as
    side="none") when candles are absent / too few, the regime is not a
    confirmed chop range, price is not at a range boundary, there is no
    intact matching FVG, or the current bar is not a wick-rejection at it.

    The logic is a VERBATIM port of scripts/backtest_fvg_range.py::run_backtest's
    per-bar entry block, evaluated on the most recent closed bar (index -1),
    so live signals match the validated simulation.
    """
    candles_df = require_candles(candles_df, "fvg_range_15m")
    params = _resolve_params(cfg)
    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"

    range_lookback = int(params["range_lookback"])
    atr_period = int(params["atr_period"])
    adx_period = int(params["adx_period"])
    adx_max = float(params["adx_max"])
    min_width_pct = float(params["min_width_pct"])
    max_width_pct = float(params["max_width_pct"])
    touch_tol_pct = float(params["touch_tol_pct"])
    min_touches = int(params["min_touches"])
    third_frac = float(params["third_frac"])
    fvg_search = int(params["fvg_search"])
    min_fvg_size_bps = float(params["min_fvg_size_bps"])
    atr_stop_buffer = float(params["atr_stop_buffer"])
    timeout_bars = int(params["timeout_bars"])
    min_confidence = float(params["min_confidence"])
    timeframe = str(cfg.get("timeframe") or params["timeframe"])

    required_cols = {"open", "high", "low", "close"}
    missing_cols = required_cols - set(candles_df.columns)
    if missing_cols:
        raise ValueError(
            f"Strategy 'fvg_range_15m': missing OHLC columns "
            f"{sorted(missing_cols)} in candles_df."
        )

    needed = max(range_lookback, atr_period, adx_period) + 3
    if len(candles_df) < needed:
        raise ValueError(
            f"Strategy 'fvg_range_15m': need at least {needed} candles for "
            f"the range({range_lookback}) / atr({atr_period}) / adx({adx_period}) "
            f"windows; got {len(candles_df)}."
        )

    df = candles_df.reset_index(drop=True)
    df["atr"] = _atr(df, atr_period)
    # Boundaries from the PRIOR range_lookback bars only (shift(1)) — no
    # lookahead; the current bar's own action decides the bounce.
    df["range_hi"] = df["high"].rolling(range_lookback).max().shift(1)
    df["range_lo"] = df["low"].rolling(range_lookback).min().shift(1)
    df["adx"] = _adx(df, adx_period).shift(1)

    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    lo_arr = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    atr_arr = df["atr"].to_numpy(dtype=float)
    rhi = df["range_hi"].to_numpy(dtype=float)
    rlo = df["range_lo"].to_numpy(dtype=float)
    adx_arr = df["adx"].to_numpy(dtype=float)

    i = len(df) - 1
    atr = atr_arr[i]
    R = rhi[i]
    S = rlo[i]
    if not (atr > 0) or np.isnan(R) or np.isnan(S):
        raise ValueError(
            "Strategy 'fvg_range_15m': ATR non-positive or range undefined on "
            "the latest bar (non-actionable)."
        )
    width = R - S
    price = c[i]
    wfrac = width / price if price > 0 else 0.0
    if wfrac < min_width_pct or wfrac > max_width_pct:
        raise ValueError(
            f"Strategy 'fvg_range_15m': range width {wfrac:.4f} of price outside "
            f"[{min_width_pct}, {max_width_pct}] — non-actionable."
        )

    adx_i = adx_arr[i]
    if np.isnan(adx_i) or adx_i >= adx_max:
        raise ValueError(
            f"Strategy 'fvg_range_15m': regime not chop (ADX={adx_i} >= "
            f"{adx_max}) — non-actionable."
        )

    if price <= S or price >= R:
        raise ValueError(
            "Strategy 'fvg_range_15m': price already outside the range "
            "(broken out) — non-actionable."
        )

    lower_third = S + width * third_frac
    upper_third = R - width * third_frac
    if price <= lower_third:
        direction = "long"
    elif price >= upper_third:
        direction = "short"
    else:
        raise ValueError(
            "Strategy 'fvg_range_15m': price in the middle of the range "
            "(no boundary edge) — non-actionable."
        )

    w0 = i - range_lookback
    tol = price * touch_tol_pct
    win_hi = h[w0:i]
    win_lo = lo_arr[w0:i]
    touches_R = int(np.count_nonzero(win_hi >= (R - tol)))
    touches_S = int(np.count_nonzero(win_lo <= (S + tol)))
    if touches_R < min_touches or touches_S < min_touches:
        raise ValueError(
            f"Strategy 'fvg_range_15m': range not confirmed (touches R={touches_R}/"
            f"S={touches_S} < {min_touches}) — non-actionable."
        )

    fvg = _find_range_fvg(
        h, lo_arr, c, i, direction=direction, fvg_search=fvg_search,
        min_fvg_size_bps=min_fvg_size_bps, S=S, R=R, width=width,
        third_frac=third_frac,
    )
    if fvg is None:
        raise ValueError(
            "Strategy 'fvg_range_15m': no intact matching FVG in the "
            f"{'lower' if direction == 'long' else 'upper'} third — "
            "non-actionable."
        )
    g_lo, g_hi = fvg

    bull_body = c[i] > o[i]
    bear_body = c[i] < o[i]
    if direction == "long":
        wicked_in = lo_arr[i] <= g_hi
        closed_out = c[i] > g_hi
        if not (wicked_in and closed_out and bull_body):
            raise ValueError(
                "Strategy 'fvg_range_15m': latest bar did not produce a bullish "
                "wick-rejection at the FVG (need wick-in + close-out + bull body)."
            )
    else:
        wicked_in = h[i] >= g_lo
        closed_out = c[i] < g_lo
        if not (wicked_in and closed_out and bear_body):
            raise ValueError(
                "Strategy 'fvg_range_15m': latest bar did not produce a bearish "
                "wick-rejection at the FVG (need wick-in + close-out + bear body)."
            )

    entry = price
    if direction == "long":
        sl = min(g_lo, S) - atr_stop_buffer * atr
        risk = entry - sl
        tp = R                       # opposite boundary (full-range reversion)
    else:
        sl = max(g_hi, R) + atr_stop_buffer * atr
        risk = sl - entry
        tp = S
    if risk <= 0:
        raise ValueError(
            "Strategy 'fvg_range_15m': non-positive risk after stop "
            "computation; skipping signal."
        )
    # Degenerate-target guard (mirrors the backtest skip).
    if (direction == "long" and tp <= entry) or (direction == "short" and tp >= entry):
        raise ValueError(
            "Strategy 'fvg_range_15m': opposite-boundary target on the wrong "
            "side of entry — non-actionable."
        )

    if direction == "long":
        loc = (lower_third - price) / (width * third_frac) if width > 0 else 0.0
    else:
        loc = (price - upper_third) / (width * third_frac) if width > 0 else 0.0
    loc = min(max(loc, 0.0), 1.0)
    fvg_size_norm = min((g_hi - g_lo) / atr, 1.0) if atr > 0 else 0.0
    confidence = round(min(0.6 * loc + 0.4 * fvg_size_norm, 1.0), 4)
    if confidence < min_confidence:
        raise ValueError(
            f"Strategy 'fvg_range_15m': confidence {confidence} below "
            f"min_confidence {min_confidence} — non-actionable."
        )

    try:
        entry_time = str(df["timestamp"].iloc[-1])
    except (KeyError, IndexError):
        entry_time = None

    package = {
        "symbol": symbol,
        "direction": direction,
        "entry": round(float(entry), 8),
        "sl": round(float(sl), 8),
        "tp": round(float(tp), 8),
        "confidence": confidence,
        "meta": {
            "strategy_name": "fvg_range_15m",
            "timeframe": timeframe,
            "range_hi": float(R),
            "range_lo": float(S),
            "range_width_pct": round(float(wfrac), 6),
            "range_touches_hi": touches_R,
            "range_touches_lo": touches_S,
            # FVG geometry — surfaced under the canonical keys the dashboard
            # signals route reads to DRAW the gap zone (same as ict_scalp).
            "fvg_low": float(g_lo),
            "fvg_high": float(g_hi),
            "fvg_size": float(g_hi - g_lo),
            "adx": float(adx_i),
            "adx_max": adx_max,
            "atr": float(atr),
            "atr_period": atr_period,
            "risk_per_unit": float(risk),
            # Consumed by monitor() for the time-decay close (matches the
            # backtest timeout). entry_time/timeframe let monitor count bars.
            "timeout_bars": timeout_bars,
            "entry_time": entry_time,
        },
    }
    return package


# ---------------------------------------------------------------------------
# monitor() — time-decay backstop (matches the backtest timeout)
# ---------------------------------------------------------------------------


def monitor(cfg, candles_df, open_pkg):
    """Re-evaluate an open fvg_range_15m package against fresh candles.

    The order already carries a fixed SL (beyond the gap/range boundary) and
    TP (opposite boundary), so TP/SL fire naturally. monitor() adds ONLY the
    backtest's time-decay backstop: once ``timeout_bars`` bars have elapsed
    since entry without TP/SL, close at market (the range stalled). No trailing
    and no break-even — a premature protective stop would cut the
    wick-against-then-revert bounces the edge depends on, diverging from the
    validated simulation.

    Returns ``{"action": "close", "reason": "time_decay"}`` when the timeout
    has elapsed, else ``None`` (no change).
    """
    if candles_df is None or len(candles_df) == 0:
        return None
    meta = open_pkg.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta) if meta else {}
        except Exception:  # noqa: BLE001
            meta = {}
    if not isinstance(meta, dict):
        return None
    try:
        timeout_bars = int(meta.get("timeout_bars", _DEFAULTS["timeout_bars"]))
    except (TypeError, ValueError):
        timeout_bars = int(_DEFAULTS["timeout_bars"])
    if timeout_bars <= 0:
        return None
    entry_ts = meta.get("entry_time") or open_pkg.get("created_at")
    if entry_ts is None or "timestamp" not in getattr(candles_df, "columns", []):
        return None
    try:
        ts = pd.to_datetime(candles_df["timestamp"], utc=True, errors="coerce")
        cutoff = pd.to_datetime(entry_ts, utc=True, errors="coerce")
        if pd.isna(cutoff):
            return None
        bars_since = int((ts > cutoff).sum())
    except Exception:  # noqa: BLE001
        return None
    if bars_since >= timeout_bars:
        return {"action": "close", "reason": "time_decay"}
    return None
