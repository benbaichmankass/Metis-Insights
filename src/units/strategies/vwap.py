"""VWAP strategy — units layer (S-008 PR #121, S-012 PR C5 self-contained).

Pure VWAP mean-reversion signal builder + ``order_package`` adapter. Before
S-012 PR C5 the helpers (``compute_vwap``, ``build_vwap_signal``,
``ENTRY_STD_THRESHOLD``) lived in ``strategies/vwap_signal_builder.py``.
That module has been removed; everything now lives here so the
production strategy directory is exactly one path.

Public surface
--------------
- ``ENTRY_STD_THRESHOLD`` — module constant; std-dev threshold for entry.
- ``compute_vwap(df)`` — pure VWAP scalar from an OHLCV frame.
- ``build_vwap_signal(df, symbol)`` — pipeline-shape signal dict
  (``{symbol, side, entry_price, stop_loss, take_profit, meta}``);
  never raises on bad data — returns side="none" with a logged reason.
  Used by the runtime pipeline.
- ``order_package(cfg, candles_df)`` — units-layer adapter conforming to
  the contract in ``src/units/strategies/_base.py``. Used by the
  Coordinator dispatch path.

Strategies are pure signal generators (see ``_base.py`` docstring): no
``dry_run`` flag, no execution awareness, **no qty** — quantity is an
account-side decision (S-026 G1) computed by the per-account
RiskManager from balance + risk rules, not a strategy output.

Shadow-mode hook (S-AI-WS7-PART-3)
----------------------------------
``order_package`` threads its return value through
``src.runtime.shadow_adapter.with_shadow_pred`` unconditionally. When
``cfg["_shadow_predictor"]`` is set, the adapter calls the predictor
on a signal-time feature row built by ``_build_shadow_feature_row``
and emits a JSONL audit line; when the field is absent, the adapter
is a single-branch passthrough. Per the WS7 non-negotiable, the
predictor's score CANNOT influence the returned package — this is
verified by ``with_shadow_pred``'s defence-in-depth tests + the
``test_vwap_shadow.py`` integration tests in this sprint.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from src.runtime.shadow_adapter import with_shadow_preds
from src.units.strategies._base import (
    derive_sl_tp,
    last_close,
    monitor_breakeven_sl,
    require_candles,
    side_to_direction,
)

logger = logging.getLogger(__name__)

# Minimum candles needed for a meaningful VWAP reading.
MIN_CANDLES = 2

# Phase 1 of the 2026-05-07-vwap-accuracy training run (PR #481).
# When candles carry a ``timestamp`` column, ``build_vwap_signal``
# anchors the VWAP / σ window at the most recent UTC midnight rather
# than using the full caller-supplied lookback. Backtest on 365 days
# of real BTCUSDT 5m data: anchored VWAP raises Sharpe +2.74 → +3.47
# (+0.72), win rate 31.0 % → 33.3 %, expectancy +0.16 R → +0.21 R,
# while leaving cadence essentially unchanged (950 → 962 trades).
# The trade-off is a slightly worse max DD (-34 R → -52 R) caused by
# noisier σ on the small early-session sample. To bound that
# trade-off we fall back to the full lookback when the session slice
# would have fewer than ``SESSION_MIN_BARS`` bars (early-session) or
# carry no volume. See
# ``experiments/2026-05-07-vwap-accuracy/RECOMMENDATIONS.md`` § 5m
# re-run for the full numbers, walk-forward, and the Phase 2 plan
# (4 h-EMA-200 ±1 % HTF gate, queued in milestone-state.md).
#
# 2026-05-08 hotfix: raised from 5 to 50 after live observation.
# At 5 bars the slice σ is computed from a 25 min sample at 5m TF —
# small enough that |deviation_std| collapses below the 1.0σ entry
# threshold for ~the first 4 h of every UTC day, silently suppressing
# signals the rolling-window code would have caught. Operator-observed
# symptom: VWAP went quiet from 03:00 +0300 (= 00:00 UTC) onward on
# 2026-05-08, immediately after PR #481 deployed. Reproduced offline
# with 5/45 ticks flipping side between old and new in the 0.5–2 h
# post-midnight window. 50 bars ≈ 4 h of 5m data — long enough for σ
# to stabilise. Behaviour outside the post-midnight window is
# unchanged because the slice equals the full lookback there. The
# Phase 2 (S-050) HTF gate plan is unaffected; that decision still
# waits on 30 days of live metrics.
SESSION_MIN_BARS = 50

# S-047 T4: monitor() time-decay close window. The mean-reversion thesis
# is "price returns to vwap within a session"; if the trade has been open
# for longer than this window without hitting TP/SL/VWAP-cross, the
# thesis has already played out one way or the other and the position
# should be flattened so capital recycles into the next signal. 240 min
# (4 hours) covers ~half a US trading session at the 5m TF; operators can
# tune via the ``monitor_hold_window_minutes`` field on the strategy cfg
# (or ``config/strategies.yaml`` under ``strategies.vwap``) without
# editing source.
MONITOR_HOLD_WINDOW_MINUTES = 240

# 2026-05-15 vwap_cross micro-exit gates. Live observation: of 6 closed
# vwap trades on 2026-05-14, 3 exited via vwap_cross at R-capture
# 0.009-0.085 — net loss after Bybit's 0.055% taker fee. Mechanism:
# when VWAP drifts *to* price rather than price reverting *to* VWAP,
# ``current_price >= vwap_live`` fires within a bar or two and the
# trade is booked as a tiny "win" the mean-reversion thesis never
# actually delivered. The gates below suppress those exits:
#
#   * MIN_R_FOR_VWAP_CROSS — vwap_cross is allowed only when the
#     trade has captured at least this many R-multiples (where
#     R = |entry - sl|). 0.25R chosen to cover Bybit linear taker
#     fees (~0.11% round-trip on the 0.5σ stop distance we now run)
#     with margin to spare. Operators can override via
#     ``config/strategies.yaml`` ``min_r_for_vwap_cross``. Set to 0
#     to restore v1 "any cross closes" behaviour.
#
#   * MIN_HOLD_MINUTES_FOR_VWAP_CROSS — vwap_cross is allowed only
#     after the trade has been open at least this long. 10 min ≈ two
#     5m bars on the strategy timeframe, which is the minimum window
#     for a mean-reversion to actually develop. Operator-tunable via
#     ``min_hold_minutes_for_vwap_cross``. Set to 0 to disable.
#
# SL / TP / time_decay close paths are unaffected by these gates —
# they continue to operate as before. The gates can only suppress
# vwap_cross from firing; they never convert a would-be-closed
# trade into a no-action without an alternative close path being
# available later in the priority chain.
MIN_R_FOR_VWAP_CROSS_DEFAULT = 0.25
MIN_HOLD_MINUTES_FOR_VWAP_CROSS_DEFAULT = 10.0

# Minimum standard-deviation bands required to call a reversion signal.
# Price must deviate at least this many std-devs from VWAP to be actionable.
#
# 2026-05-03: raised from 1.0σ to 2.0σ following the
# `2026-05-03-vwap-improvement` training run (PR #350 — RECOMMENDATIONS,
# merged d52a816). The 1.0σ default produced an unprofitable backtest
# (Sharpe -0.12, expectancy -0.002 R, max DD -21 R over 365 d BTCUSDT 5m,
# 946 trades). The 2.0σ threshold flipped the same window to Sharpe 1.71,
# expectancy +0.044 R, max DD -5 R, 336 trades — see
# `experiments/2026-05-03-vwap-improvement/RECOMMENDATIONS.md`. The
# threshold sweep (1.0 / 1.5 / 2.0 / 2.5) showed monotonic improvement
# up to 2.0σ with a small regression at 2.5σ, so 2.0σ is a clean local
# maximum and not overfit to the sample.
#
# 2026-05-03 operator override (CP-2026-05-03-20): reverted to 1.0σ to
# raise order-package cadence. Operator accepted the documented R/R
# trade-off (1:1 at the entry boundary instead of 2:1) in exchange for
# more frequent fills. The 2.0σ value remains the backtest-optimal Sharpe
# point; if the live cadence/PnL profile underperforms expectations,
# the right next move is a fresh threshold sweep on out-of-sample data,
# not silently bumping the constant back up.
#
# 2026-05-17 set to 1.0σ following the post-incident validation backtest
# (issue #1370, full table in `experiments/2026-05-17-post-incident-
# validation/SUMMARY.md`). With the HTF 4h ±2% gate present, the
# threshold/SL ablation across 3.16 years of BTCUSDT 5m data showed:
#
#   variant                           | Total R | Sharpe | Win % | DD R
#   ----------------------------------|---------|--------|-------|------
#   V_1175_htf_only (1.0σ, SL 0.5σ)   | +411.8  | +2.82  | 26.2% | -55.2
#   V_1175_1183_htf_sl (1.0σ, SL 0.75)| +148.7  | +1.34  | 33.1% | -76.7
#   V_PROD (1.5σ, SL 0.75)            | +133.1  | +1.38  | 30.7% | -52.5
#
# The 2026-05-15 #1200 sweep that justified 1.5σ was run **without** the
# HTF gate; once the HTF gate is in place the picture flips and 1.0σ
# dominates by 3× on total R. This isn't reverting a wrong decision —
# both decisions optimised correctly for the regime they were measured
# in. SL widening (PR #1183) hurts in both regimes.
#
# Prior values for the record: 1.0σ (2026-05-03 directive, default) →
# 2.0σ briefly during the 2026-05-09 cadence audit → 1.0σ again later
# 2026-05-09 → 1.5σ on 2026-05-15 (PR #1205) → 1.0σ on 2026-05-17 (this
# revert).
ENTRY_STD_THRESHOLD = 1.0

# Internal alias retained for backwards-compatible imports.
_ENTRY_STD_THRESHOLD = ENTRY_STD_THRESHOLD

# G5 (CP-2026-05-02-12, operator picked option (a)): VWAP must populate
# entry / stop_loss / take_profit on every actionable signal so the
# multi-account dispatch fast-path can fan it out. Operator directive:
# "the trade package should always include entry/sl/tp levels".
#
# Mean-reversion logic:
#   * entry      = current candle close (the price at which the signal fires)
#   * take_profit = VWAP (the mean-reversion target — by definition price
#                  deviates from VWAP and we expect it to revert)
#   * stop_loss   = entry ± SL_STD_MULT × std_dev
#                   - BUY  (entry < vwap): SL = entry - SL_STD_MULT * std_dev (further below)
#                   - SELL (entry > vwap): SL = entry + SL_STD_MULT * std_dev (further above)
#
# Risk/reward at entry: reward = (vwap - entry) for BUY, (entry - vwap)
# for SELL. That's |deviation_std| × std_dev. Risk = SL_STD_MULT × std_dev.
# So R/R (reward:risk) at the entry boundary equals
# ENTRY_STD_THRESHOLD / SL_STD_MULT.
#
# Per the 2026-05-03 operator directive (CP-2026-05-03-20): preserve
# risk:reward of 1:2 at the entry boundary. At ENTRY_STD_THRESHOLD=1.0σ
# and SL_STD_MULT_DEFAULT=0.5σ, reward = 1.0 × std_dev / risk = 0.5 ×
# std_dev → reward:risk = 2:1 (risk:reward = 1:2). Operators tuning
# either value must move the other in lock-step or the R:R contract
# drifts. Tunable per call via the ``sl_std_mult`` arg to
# ``build_vwap_signal`` or the matching entry in
# ``config/strategies.yaml`` (consumed by vwap_signal_builder).
#
# 2026-05-17 reverted to 0.5σ following the post-incident validation
# backtest (issue #1370). PR #1183 had widened 0.5 → 0.75 on 2026-05-12
# to combat live "noise stops" before mean-reversion played out, but
# the 3.16-year backtest with HTF gate present showed the SL widening
# costs ~63% of total R (V_1175_htf_only +411 R vs V_1175_1183_htf_sl
# +148 R). The ATR-based floor in build_vwap_signal still provides
# the noise guard PR #1183 sought, without the R:R contract drift.
#
# 2026-05-19 param sweep (S-VWAP-SWEEP-DISPATCH, issue #1569): 12-combo
# ENTRY×SL sweep over 16 windows × 14 days ranked SL=0.3 across the entire
# ENTRY grid. ENTRY=1.0/SL=0.3 mean_total_r=+4.88 vs SL=0.5 at -0.46
# (rank 1 vs rank 9 of 12). Tighter stops cut losing long trades shorter,
# which explains the gain. R:R at ENTRY=1.0/SL=0.3 is 3.33:1 — a deliberate
# relaxation of the 2026-05-03 2:1 target, justified by empirical sweep data.
# TIER-3: Ben must approve before this value is deployed to the live bot.
SL_STD_MULT_DEFAULT = 0.3


# Phase 2 of the 2026-05-07-vwap-accuracy training run + the
# 2026-05-08-all-models-training validation: HTF EMA trend gate.
# When the runtime supplies an HTF close + EMA pair (default: 4h
# EMA-200), the strategy rejects mean-reversion fades that point
# against the higher-timeframe trend. The gate band is
# operator-configurable via ``config/strategies.yaml`` under
# ``strategies.vwap.htf_trend_filter`` — runtime falls back to this
# default when the config map is missing.
#
# 2026-05-08 run set the band to 0.020 (vs 0.010 in the original
# Phase-2 design): full-sample Sharpe rose +0.35 with a 13 % cadence
# recovery vs band 0.010 on a 38-month BTCUSDT 5m dataset, walk-
# forward IS/OOS both stable. See
# ``experiments/2026-05-08-all-models-training/RECOMMENDATIONS.md``
# § "VWAP — full results" for the band sweep.
#
# Gate semantics (BUY = mean-reversion long, SELL = short):
#   * BUY  blocked when ``htf_close < htf_ema * (1 - band_pct)``
#     — strong downtrend; fades into a falling knife.
#   * SELL blocked when ``htf_close > htf_ema * (1 + band_pct)``
#     — strong uptrend; fades get run over.
#   * Within the ±band region (consolidation / mild counter-trend
#     pullback), both sides pass through unchanged.
HTF_BAND_PCT_DEFAULT = 0.02

# Recent-context filter — replaces the disabled 4h EMA-200 HTF gate.
# At 5m TF the EMA-200 on 4h candles looks back ~33 days — far too slow
# for a scalper reacting to a 2-day down-move.
#
# Operator directive 2026-05-13: largest context window is 24h and the
# strategy should be weighted towards more recent data, not a flat daily
# label. Implementation:
#   - Fetch ≤24 1h candles (24h max lookback).
#   - Use an exponentially-weighted mean (EWM half-life = ¼ window) so
#     the most recent hours dominate the signal; bars from the start of
#     the window contribute minimally.
#   - Compare the EWM-weighted recent price to the start of the window
#     as the directional reference.
#   - The result is INFORMATIONAL ONLY: neither buy nor sell is blocked.
#     Mean-reversion longs are valid in a downtrend; shorts are valid in
#     an uptrend. The context is surfaced in signal meta for operator
#     monitoring and future confidence-weighting analysis.
RECENT_CONTEXT_NEUTRAL_BAND_PCT_DEFAULT = 0.003  # ±0.3% treated as neutral


def _compute_recent_context(
    candles_df: pd.DataFrame,
    neutral_band_pct: float = RECENT_CONTEXT_NEUTRAL_BAND_PCT_DEFAULT,
) -> dict:
    """Compute a recency-weighted short-term trend from a ≤24h candle window.

    Uses an exponential half-life of ¼ the window length so the most
    recent bars dominate and early bars provide diminishing context.
    Compares the EWM-weighted current price to the oldest close in the
    window to measure directional momentum with recency emphasis.

    Returns a dict with:
        trend   — "up", "down", "flat", or "unknown"
        pct     — float EWM-weighted percentage change from window open
    """
    if (
        candles_df is None
        or not isinstance(candles_df, pd.DataFrame)
        or len(candles_df) < 2
        or "close" not in candles_df.columns
    ):
        return {"trend": "unknown", "pct": 0.0}

    close = candles_df["close"].astype(float)
    close_start = float(close.iloc[0])

    if close_start <= 0:
        return {"trend": "unknown", "pct": 0.0}

    # EWM halflife = ¼ of the window so the last quarter dominates.
    halflife = max(len(close) / 4.0, 1.0)
    ewm_price = float(close.ewm(halflife=halflife, adjust=True).mean().iloc[-1])
    pct_change = (ewm_price - close_start) / close_start

    if pct_change > neutral_band_pct:
        trend = "up"
    elif pct_change < -neutral_band_pct:
        trend = "down"
    else:
        trend = "flat"

    return {"trend": trend, "pct": pct_change}


def compute_vwap(candles_df: pd.DataFrame) -> float:
    """Return VWAP for the supplied candle window.

    Raises ValueError with a clear, non-secret message if the data is
    insufficient or degenerate (zero volume).
    """
    if not isinstance(candles_df, pd.DataFrame) or candles_df.empty:
        raise ValueError(
            "VWAP computation requires a non-empty DataFrame. "
            "Check that market data was fetched correctly."
        )

    required = {"high", "low", "close", "volume"}
    missing = required - set(candles_df.columns)
    if missing:
        raise ValueError(
            f"VWAP computation: candle DataFrame is missing columns: {sorted(missing)}. "
            "Expected columns: high, low, close, volume."
        )

    if len(candles_df) < MIN_CANDLES:
        raise ValueError(
            f"VWAP requires at least {MIN_CANDLES} candles, got {len(candles_df)}. "
            "Ensure the timeframe and lookback window are configured correctly."
        )

    total_volume = candles_df["volume"].sum()
    if total_volume <= 0:
        raise ValueError(
            "VWAP cannot be computed: total volume across all candles is zero or negative. "
            "Check that the candle data contains valid volume information."
        )

    typical_price = (
        candles_df["high"] + candles_df["low"] + candles_df["close"]
    ) / 3.0
    return float((typical_price * candles_df["volume"]).sum() / total_volume)


def _no_trade(symbol: str, reason: str) -> Dict[str, Any]:
    """Standardised no-trade signal for invalid or insufficient candle data."""
    logger.warning("VWAP no-trade for %s: %s", symbol, reason)
    return {
        "symbol": symbol,
        "side": "none",
        "meta": {
            "strategy_name": "vwap",
            "reason": reason,
        },
    }


def _session_anchor_slice(candles_df: pd.DataFrame) -> pd.DataFrame:
    """Return ``candles_df`` truncated to the current UTC-day session.

    Phase 1 of the 2026-05-07-vwap-accuracy adoption. The slice covers
    bars whose timestamp is at or after the most recent UTC midnight
    (≤ the latest bar). When the slice would be too thin or carry no
    volume, the full lookback is returned so callers fall through to
    the rolling-window VWAP semantics that pre-dated this change.

    Falls back to the input when:
        * ``timestamp`` column is missing.
        * Timestamps cannot be coerced to UTC datetimes.
        * The slice would have fewer than ``SESSION_MIN_BARS`` bars
          (early in the session — σ would be too noisy).
        * The slice would carry zero or negative total volume.

    Tests in ``tests/test_vwap_strategy.py`` lock the fallback paths.
    """
    if "timestamp" not in candles_df.columns:
        return candles_df
    try:
        ts = pd.to_datetime(candles_df["timestamp"], utc=True, errors="coerce")
    except (ValueError, TypeError):
        return candles_df
    if ts.isna().all():
        return candles_df
    last_ts = ts.iloc[-1]
    if pd.isna(last_ts):
        return candles_df
    session_start = last_ts.floor("D")
    sliced = candles_df.loc[ts >= session_start]
    if len(sliced) < SESSION_MIN_BARS:
        return candles_df
    if "volume" in sliced.columns and float(sliced["volume"].sum()) <= 0:
        return candles_df
    return sliced


def _compute_atr(candles_df: pd.DataFrame, period: int = 14) -> float:
    """Average True Range over the trailing ``period`` bars.

    Returns 0.0 when data is insufficient (< 2 rows) so callers can
    treat a zero ATR as "no floor applies" without branching.
    """
    if len(candles_df) < 2:
        return 0.0
    high = candles_df["high"]
    low = candles_df["low"]
    close = candles_df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return float(tr.iloc[1:].tail(period).mean())


def build_vwap_signal(
    candles_df: pd.DataFrame,
    symbol: str,
    sl_std_mult: float = SL_STD_MULT_DEFAULT,
    htf_close: Optional[float] = None,
    htf_ema: Optional[float] = None,
    htf_band_pct: float = HTF_BAND_PCT_DEFAULT,
    timeframe: Optional[str] = None,
    recent_context_candles_df: Optional[pd.DataFrame] = None,
    recent_context_neutral_band_pct: float = RECENT_CONTEXT_NEUTRAL_BAND_PCT_DEFAULT,
) -> Dict[str, Any]:
    """Compute a VWAP mean-reversion signal from OHLCV candle data.

    Returns a signal dict with keys:
        symbol, side,
        entry_price, stop_loss, take_profit  (only when side != "none"),
        meta.

    S-026 G1: this strategy package is the trade *idea*, not the order.
    No ``qty`` field is produced — quantity is decided per-account by
    the RiskManager from balance + risk rules.

    * side='buy'  when price is at least ENTRY_STD_THRESHOLD std-devs *below*
                  VWAP (mean-reversion long).
    * side='sell' when price is at least ENTRY_STD_THRESHOLD std-devs *above*
                  VWAP (mean-reversion short).
    * side='none' when price is near VWAP or data is insufficient.

    Invalid candle data (empty, missing volume column, zero/negative total
    volume) returns a no-trade signal instead of raising — the tick completes
    safely.

    G5 (CP-2026-05-02-12, operator option (a)): when the signal IS
    actionable, ``entry_price`` / ``stop_loss`` / ``take_profit`` are
    populated at the top level so the pipeline's multi-account dispatch
    fast-path (``_signal_carries_full_sltp``) accepts the signal and
    fans it out per account. Per-account ``RiskManager.dry_run``
    (operator directive 2026-05-03) is the only dry/live toggle that
    runs against the dispatched signal.

    SL/TP rules:
        entry = current candle close
        TP    = VWAP (mean-reversion target)
        SL    = entry - sl_std_mult * std_dev   (BUY — further below entry)
                entry + sl_std_mult * std_dev   (SELL — further above entry)

    Per the 2026-05-03 operator directive: ``ENTRY_STD_THRESHOLD = 1.0``
    and ``SL_STD_MULT_DEFAULT`` together define R:R at the entry boundary.
    The 2026-05-19 param sweep (issue #1569) set ``SL_STD_MULT_DEFAULT = 0.3``,
    giving reward:risk = 3.33:1 — tighter stops cut losing long trades
    shorter while preserving mean-reversion upside on the short side.
    """
    if not isinstance(candles_df, pd.DataFrame) or candles_df.empty:
        return _no_trade(symbol, "VWAP skipped: candle data is empty or invalid")

    if "volume" not in candles_df.columns:
        return _no_trade(symbol, "VWAP skipped: candle data is empty or invalid")

    if candles_df["volume"].sum() <= 0:
        return _no_trade(symbol, "VWAP skipped: total candle volume is zero or negative")

    # Phase 1: anchor at most-recent UTC midnight when the timestamp
    # data permits; otherwise the full caller-supplied lookback.
    window = _session_anchor_slice(candles_df)
    anchor = "session" if len(window) < len(candles_df) else "rolling"

    vwap = compute_vwap(window)
    current_price = float(window["close"].iloc[-1])

    typical_price = (
        window["high"] + window["low"] + window["close"]
    ) / 3.0
    std_dev = float(typical_price.std())

    deviation = (current_price - vwap) / std_dev if std_dev > 0 else 0.0

    if deviation <= -ENTRY_STD_THRESHOLD:
        side = "buy"
        reason = f"price {current_price:.4f} is {abs(deviation):.2f} std-devs below VWAP {vwap:.4f}"
    elif deviation >= ENTRY_STD_THRESHOLD:
        side = "sell"
        reason = f"price {current_price:.4f} is {deviation:.2f} std-devs above VWAP {vwap:.4f}"
    else:
        side = "none"
        reason = f"price {current_price:.4f} within {ENTRY_STD_THRESHOLD} std-dev of VWAP {vwap:.4f} — no signal"

    # Phase 2 HTF trend gate. When the runtime supplies htf_close + htf_ema,
    # block fades pointing against a strong higher-timeframe trend.
    htf_blocked = False
    if side != "none" and htf_close is not None and htf_ema is not None and htf_ema > 0:
        if side == "buy" and htf_close < htf_ema * (1.0 - htf_band_pct):
            htf_blocked = True
        elif side == "sell" and htf_close > htf_ema * (1.0 + htf_band_pct):
            htf_blocked = True
        if htf_blocked:
            side = "none"
            reason = (
                f"htf_trend_block: side={'buy' if deviation < 0 else 'sell'} "
                f"htf_close={htf_close:.4f} htf_ema={htf_ema:.4f} band={htf_band_pct:.4f}"
            )

    # Recent context — recency-weighted short-term trend from ≤24h of 1h candles.
    # Does NOT block either side: mean-reversion longs are valid in downtrends
    # and shorts in uptrends. Surfaced in meta for operator monitoring and
    # future confidence-weighting analysis.
    recent_ctx = _compute_recent_context(
        recent_context_candles_df, neutral_band_pct=recent_context_neutral_band_pct,
    ) if recent_context_candles_df is not None else {"trend": "unknown", "pct": 0.0}

    logger.info(
        "VWAP signal: symbol=%s vwap=%.4f price=%.4f std=%.4f deviation=%.2f side=%s recent_context=%s",
        symbol, vwap, current_price, std_dev, deviation, side, recent_ctx["trend"],
    )

    # BUG-043: confidence must be threaded through to the order package
    # so the journal records a real conviction value (not 0.0). Same
    # formula as ``order_package()`` below — magnitude of the std-dev
    # deviation, normalised to ENTRY_STD_THRESHOLD, capped at 1.0.
    confidence = round(min(abs(deviation) / ENTRY_STD_THRESHOLD, 1.0), 4)

    base_meta = {
        "strategy_name": "vwap",
        "vwap": vwap,
        "current_price": current_price,
        "std_dev": std_dev,
        "deviation_std": deviation,
        "confidence": confidence,
        "reason": reason,
        "vwap_anchor": anchor,
        "vwap_window_bars": len(window),
        "recent_context": recent_ctx["trend"],
        "recent_context_pct": round(float(recent_ctx["pct"]), 6),
    }
    # The order_monitor's ohlcv_fetcher reads ``timeframe`` off the
    # package's meta to fetch fresh candles for monitor() — without it
    # _build_monitor_ohlcv_fetcher short-circuits to None and monitor()
    # never receives candles, so no TP/SL/VWAP-cross/time-decay close
    # ever fires (positions sit open until the watchdog cascades them
    # at +30 min). Caller threads the configured timeframe through.
    if timeframe:
        base_meta["timeframe"] = str(timeframe)
    if htf_close is not None and htf_ema is not None:
        base_meta["htf_close"] = float(htf_close)
        base_meta["htf_ema"] = float(htf_ema)
        base_meta["htf_band_pct"] = float(htf_band_pct)
        base_meta["htf_blocked"] = bool(htf_blocked)

    if side == "none":
        return {
            "symbol": symbol,
            "side": "none",
            "confidence": confidence,
            "meta": base_meta,
        }

    # G5 — populate entry/sl/tp so multi-account dispatch can fan this out.
    entry_price = current_price
    take_profit = vwap
    # ATR floor: stop must be at least 1 ATR away from entry so a single
    # noisy candle cannot immediately trigger sl_cross on a valid signal.
    atr = _compute_atr(window)
    sl_distance = sl_std_mult * std_dev
    if atr > 0:
        sl_distance = max(sl_distance, atr)
    if side == "buy":
        stop_loss = entry_price - sl_distance
    else:  # "sell"
        stop_loss = entry_price + sl_distance

    return {
        "symbol": symbol,
        "side": side,
        "entry_price": float(entry_price),
        "stop_loss": float(stop_loss),
        "take_profit": float(take_profit),
        "confidence": confidence,
        "meta": {
            **base_meta,
            "sl_std_mult": sl_std_mult,
            "atr": round(float(atr), 8),
            "sl_distance": round(float(sl_distance), 8),
            "entry_price": float(entry_price),
            "stop_loss": float(stop_loss),
            "take_profit": float(take_profit),
        },
    }


def _has_open_vwap_package() -> bool:
    """Best-effort self-suppression check — is there already an open
    + linked vwap order package in the trade journal?

    Mirrors the pipeline-level strategy-monocle gate
    (``src.runtime.pipeline._has_open_package_for_strategy``), pulled
    inside the strategy module so a bypassed gate (DB read failure,
    linked_trade_id wiring regression) doesn't re-open the floodgates
    of duplicate entries every tick. Belt-and-braces, not a
    replacement.

    Best-effort: any failure (missing DB file, schema mismatch,
    sqlite locked) returns ``False`` so a journal outage degrades
    to "no defence-in-depth" rather than "strategy stops generating
    signals". The pipeline gate remains the primary line of defence.
    Honours ``TRADE_JOURNAL_DB`` so tests using ``tmp_path`` journals
    don't cross-contaminate with the production file.
    """
    try:
        import os as _os
        from src.units.db.database import Database
        from src.utils.paths import repo_root as _repo_root

        db_path = _os.environ.get("TRADE_JOURNAL_DB") or _os.path.join(
            str(_repo_root()), "trade_journal.db"
        )
        if not _os.path.exists(db_path):
            return False
        db = Database(db_path=db_path)
        rows = db.get_order_packages_by_strategy(
            "vwap", status="open", linked_only=True, limit=1,
        )
        return bool(rows)
    except Exception:  # noqa: BLE001
        return False


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build a VWAP OrderPackage dict.

    Parameters
    ----------
    cfg : dict
        Strategy config from units.yaml.
    candles_df : pd.DataFrame
        OHLCV frame.  Required — raises ValueError when absent.

    Returns
    -------
    dict
        Keys: symbol, direction, entry, sl, tp, confidence, meta.

    Raises
    ------
    ValueError
        When candles_df is absent, signal is non-actionable
        (side="none"), or a linked open vwap package already exists
        (self-suppression — see ``_has_open_vwap_package``). The
        pipeline catches ValueError as "no actionable signal" and
        records the tick as flat without dispatching.

    Notes
    -----
    Shadow-prediction call placement: the open-package gate moved
    AFTER signal building + shadow prediction in S-AI-WS7-FU-REGISTRY-WIRE.
    Before that fix, the gate aborted the function at line 667 before
    `with_shadow_preds` was reached, so any time the strategy held an
    open package (including stuck/orphaned ones) the shadow harness
    saw zero signals — defeating the whole purpose of the
    observe-only contract. The new order keeps the open-package
    self-suppression intact but lets shadow models observe every
    actionable signal evaluation regardless of order-placement
    outcome.
    """
    candles_df = require_candles(candles_df, "vwap")

    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"

    signal = build_vwap_signal(candles_df, symbol=symbol)

    side = signal.get("side", "none")
    direction = side_to_direction(side)  # raises ValueError when side=="none"

    entry = last_close(candles_df)

    # Attempt VWAP-to-price TP/SL; fall back to percentage-based.
    try:
        # Phase 1: keep the order-package adapter aligned with
        # build_vwap_signal so the dispatch path's VWAP and the
        # strategy unit's VWAP are computed from the same window.
        window = _session_anchor_slice(candles_df)
        vwap = compute_vwap(window)
        typical_price = (window["high"] + window["low"] + window["close"]) / 3.0
        std_dev = float(typical_price.std())

        # Use SL/TP from build_vwap_signal directly. The prior formula
        # computed `risk = entry - tp` for longs, which is always negative
        # (entry < vwap for a valid long), so `risk > 0` never fired — sl
        # always fell to the 2% fallback and the std-dev SL was silently
        # ignored. Reading from signal ensures order_package() and
        # build_vwap_signal() stay in lock-step.
        tp = float(signal.get("take_profit", vwap))
        sl = float(signal.get("stop_loss", entry * (0.98 if direction == "long" else 1.02)))

        # Confidence: deviation in std-dev units capped at 1.0
        if std_dev > 0:
            deviation = abs(entry - vwap) / std_dev
            confidence = min(deviation / ENTRY_STD_THRESHOLD, 1.0)
        else:
            confidence = 0.5

    except Exception:
        sl, tp = derive_sl_tp(entry, direction)
        confidence = 0.5

    meta = signal.get("meta") or {}
    package = {
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(float(sl), 8),
        "tp": round(float(tp), 8),
        "confidence": round(confidence, 4),
        "meta": {**meta, "signal": signal},
    }

    # Emit shadow predictions on every actionable signal evaluation,
    # regardless of whether the open-package gate below lets the
    # order through. `with_shadow_preds` is called for its
    # side-effect (predictor.predict → ShadowPredictor writes the
    # audit log); the returned `package` is byte-identical to the
    # input.
    with_shadow_preds(
        package,
        predictors=_resolve_shadow_predictors(cfg),
        feature_row=_build_shadow_feature_row(package),
    )

    # Open-package self-suppression, enforced AFTER the shadow
    # prediction so the observer captures every signal regardless of
    # the order-placement outcome. The pipeline catches this
    # ValueError as "no actionable signal" and records the tick as
    # flat without dispatching the order.
    if _has_open_vwap_package():
        raise ValueError(
            "Strategy 'vwap': linked open package already exists; "
            "deferring entry until monitor() closes it."
        )

    return package


def _resolve_shadow_predictors(cfg: Dict[str, Any]) -> list:
    """Pick the shadow predictors for this tick from cfg.

    Resolution order (first non-empty wins):

    1. ``cfg["_shadow_predictors"]`` — explicit plural injection
       (test path; also accepted in production as a pre-resolved
       cache from the dispatcher).
    2. ``cfg["_shadow_predictor"]`` — single-predictor injection
       (PART-3 backwards-compat for tests). Wrapped in a list.
    3. ``cfg["shadow_model_ids"]`` resolved via
       ``ml.shadow.factory.resolve_predictors`` against the
       registry root in ``cfg["_shadow_registry_root"]`` (defaults
       to ``ml.shadow.factory.DEFAULT_REGISTRY_ROOT``). Per-model
       errors are logged and skipped — one bad model_id never
       breaks the others, never breaks the strategy tick.
    4. Empty list → no shadow side-channel.
    """
    if "_shadow_predictors" in cfg:
        return list(cfg["_shadow_predictors"] or [])
    single = cfg.get("_shadow_predictor")
    if single is not None:
        return [single]
    ids = cfg.get("shadow_model_ids") or []
    if not ids:
        return []
    # Lazy import keeps ml.shadow off the strategy's hot import path
    # for the no-shadow case.
    from ml.shadow.factory import (
        DEFAULT_LOG_PATH,
        DEFAULT_REGISTRY_ROOT,
        resolve_predictors,
    )
    from ml.registry.model_registry import ModelRegistry

    registry_root = Path(
        cfg.get("_shadow_registry_root") or DEFAULT_REGISTRY_ROOT
    )
    log_path = Path(cfg.get("_shadow_log_path") or DEFAULT_LOG_PATH)
    return resolve_predictors(
        ids,
        ModelRegistry(registry_root),
        log_path=log_path,
    )


def _build_shadow_feature_row(package: Dict[str, Any]) -> Dict[str, Any]:
    """Project a vwap order package into a signal-time feature row.

    Only signal-time fields go into the row — `entry`, `sl`, `tp` are
    set at signal time and are fair game; `pnl` / `pnl_percent` /
    `r_multiple` would be outcomes (not present here yet, but listed
    for clarity). The row matches the WS5-C / WS5-D feature surface
    so a shadow-mode predictor trained on either family can score
    vwap signals natively.
    """
    meta = package.get("meta") or {}
    return {
        "strategy_name": "vwap",
        "symbol": package.get("symbol", ""),
        "direction": package.get("direction", ""),
        "confidence": float(package.get("confidence") or 0.0),
        "setup_type": str(meta.get("setup_type") or ""),
        "killzone": str(meta.get("killzone") or ""),
        "bias": str(meta.get("bias") or ""),
    }


# ---------------------------------------------------------------------------
# monitor() — S-047 T4: close on TP/SL/VWAP-cross/time-decay
# ---------------------------------------------------------------------------


def _vwap_cross_gates_pass(
    open_pkg: Dict[str, Any],
    current_price: float,
    sl: float,
    direction: str,
    cfg: Dict[str, Any],
) -> bool:
    """Return True when vwap_cross is allowed to close the trade.

    Two independent gates — both must pass:

      1. **R-capture** — trade has moved at least
         ``cfg["min_r_for_vwap_cross"]`` R-multiples in the trade's
         favour, where ``R = |entry - sl|``. Default 0.25R. Stops
         "VWAP drifted to price" exits at sub-fee R-capture
         (live observation 2026-05-14).

      2. **Hold time** — trade has been open at least
         ``cfg["min_hold_minutes_for_vwap_cross"]`` minutes. Default
         10 min (≈ two 5m bars). Stops same-bar / next-bar closes
         before a mean-reversion has had time to develop.

    Either gate set to ``0`` (or non-positive) disables that gate.
    Setting both to 0 restores v1 "any cross closes" behaviour.

    Falls through to v1 behaviour (returns True) when the data needed
    to evaluate a gate is missing or malformed — better to close on
    the cross than crash the monitor tick. The other close paths
    (SL / TP / time_decay) continue to operate regardless of these
    gates.
    """
    try:
        min_r = float(cfg.get("min_r_for_vwap_cross", MIN_R_FOR_VWAP_CROSS_DEFAULT))
    except (TypeError, ValueError):
        min_r = MIN_R_FOR_VWAP_CROSS_DEFAULT
    try:
        min_hold = float(
            cfg.get("min_hold_minutes_for_vwap_cross", MIN_HOLD_MINUTES_FOR_VWAP_CROSS_DEFAULT)
        )
    except (TypeError, ValueError):
        min_hold = MIN_HOLD_MINUTES_FOR_VWAP_CROSS_DEFAULT

    if min_r > 0:
        try:
            entry = float(open_pkg["entry"])
        except (KeyError, TypeError, ValueError):
            return True
        risk = abs(entry - sl)
        if risk <= 0:
            return True
        if direction == "long":
            r_captured = (current_price - entry) / risk
        else:
            r_captured = (entry - current_price) / risk
        if r_captured < min_r:
            return False

    if min_hold > 0:
        opened_at = _parse_created_at(open_pkg.get("created_at"))
        if opened_at is not None:
            age_minutes = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60.0
            if age_minutes < min_hold:
                return False

    return True


def _parse_created_at(raw: Any) -> Optional[datetime]:
    """Parse the ``created_at`` field from an order_packages row.

    The DB unit writes ``datetime.now(timezone.utc).isoformat()``. Naïve
    timestamps are interpreted as UTC — the trade journal never writes
    local times. Returns ``None`` on any decode failure so a malformed
    row is treated as "no opinion" (no time-decay close fires) rather
    than crashing the monitor tick.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def monitor(cfg, candles_df, open_pkg):
    """Re-evaluate an open vwap order package against fresh candles.

    Per CLAUDE.md § Architecture rules § 2 the strategy unit *monitors*
    open packages — generates updates while a trade is live. The
    monitor loop (S-030 PR3, ``src/runtime/order_monitor.py``) calls
    this hook on every heartbeat tick for each open package; the
    strategy decides whether to do nothing or close.

    S-047 T4 close paths (in priority order — first match wins):

    1. **TP-cross** — current candle close has reached the package's
       ``tp``. Long: ``close >= tp``. Short: ``close <= tp``. The TP
       was placed at the entry-time VWAP per ``build_vwap_signal``,
       so this also covers the "price returned to entry-VWAP" case.
    2. **SL-cross** — current close has hit the package's ``sl``.
       Long: ``close <= sl``. Short: ``close >= sl``.
    3. **VWAP-cross** — the structural mean-reversion invariant. The
       live VWAP is recomputed from the supplied candles each tick;
       the trade was opened because price had deviated from VWAP, so
       once price crosses back through the live VWAP line the original
       thesis has played out. Long entries close when the live close
       reaches or exceeds live VWAP (price reverted from below);
       shorts close when the live close reaches or falls below live
       VWAP. Skipped when ``tp == vwap_live`` (TP-cross already handles
       it) so the verdict carries the more specific reason.
    4. **Time-decay** — open longer than
       ``cfg.get("monitor_hold_window_minutes", MONITOR_HOLD_WINDOW_MINUTES)``.
       The mean-reversion thesis is session-bounded; if none of the
       above fired within the hold window, capital should recycle.
    5. **SL-to-break-even** — defence-in-depth fallback. When price
       has moved >= 1R in our favour but none of the close paths
       fired, slide SL to entry to lock in partial profit. Delegates
       to ``_base.monitor_breakeven_sl`` so vwap and turtle_soup
       share the rule. Last in the priority chain so any close
       verdict above wins on the same tick.

    Returns ``None`` (no-action) when nothing triggers or the inputs
    are invalid.

    Parameters
    ----------
    cfg : dict
        Strategy config (e.g. ``{"monitor_hold_window_minutes": 180}``).
        ``None`` / missing keys fall back to the module defaults.
    candles_df : pandas.DataFrame
        Fresh OHLCV at the strategy's timeframe. Must carry ``high``,
        ``low``, ``close``, ``volume`` columns for VWAP-cross.
    open_pkg : dict
        Current order package row from the DB unit's order_packages
        table — keys consulted: ``entry``, ``sl``, ``tp``, ``direction``,
        ``symbol``, ``created_at``.

    Returns
    -------
    None | dict
        ``None`` for no-action. Close paths return
        ``{"action": "close", "reason": str, "exit_price": float}``;
        the BE path returns ``{"sl": float}``. Both shapes are
        consumed by ``order_monitor._apply_update`` — close verdicts
        translate to a reduce-only ``close_open_position`` call;
        ``sl`` updates rewrite the package row and the live order's
        stop on the exchange. The strategy unit never touches the
        exchange directly.
    """
    if candles_df is None or len(candles_df) == 0:
        return None
    try:
        current_price = float(candles_df["close"].iloc[-1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None

    try:
        sl = float(open_pkg["sl"])
        tp = float(open_pkg["tp"])
        direction = str(open_pkg["direction"]).lower()
    except (KeyError, TypeError, ValueError):
        return None

    if direction not in ("long", "short"):
        return None

    # 1. TP-cross.
    if direction == "long" and current_price >= tp:
        return {"action": "close", "reason": "tp_cross", "exit_price": current_price}
    if direction == "short" and current_price <= tp:
        return {"action": "close", "reason": "tp_cross", "exit_price": current_price}

    # 2. SL-cross.
    if direction == "long" and current_price <= sl:
        return {"action": "close", "reason": "sl_cross", "exit_price": current_price}
    if direction == "short" and current_price >= sl:
        return {"action": "close", "reason": "sl_cross", "exit_price": current_price}

    # 3. VWAP-cross — recompute live VWAP. Phase 1: anchor on the
    # current UTC session so the live VWAP shares its anchor with
    # the entry-time VWAP that build_vwap_signal computed. Skip
    # silently when the data can't carry a meaningful VWAP (no
    # volume column, zero volume, etc.) rather than fail the
    # monitor tick.
    try:
        vwap_live = compute_vwap(_session_anchor_slice(candles_df))
    except (ValueError, KeyError):
        vwap_live = None

    cfg_dict = cfg if isinstance(cfg, dict) else {}

    if vwap_live is not None and vwap_live != tp:
        cross_triggered = (
            (direction == "long" and current_price >= vwap_live)
            or (direction == "short" and current_price <= vwap_live)
        )
        if cross_triggered and _vwap_cross_gates_pass(
            open_pkg, current_price, sl, direction, cfg_dict,
        ):
            return {"action": "close", "reason": "vwap_cross", "exit_price": current_price}

    # 4. Time-decay.
    hold_minutes = cfg_dict.get("monitor_hold_window_minutes", MONITOR_HOLD_WINDOW_MINUTES)
    try:
        hold_minutes = float(hold_minutes)
    except (TypeError, ValueError):
        hold_minutes = MONITOR_HOLD_WINDOW_MINUTES

    if hold_minutes > 0:
        opened_at = _parse_created_at(open_pkg.get("created_at"))
        if opened_at is not None:
            now_utc = datetime.now(timezone.utc)
            age_minutes = (now_utc - opened_at).total_seconds() / 60.0
            if age_minutes >= hold_minutes:
                return {
                    "action": "close",
                    "reason": "time_decay",
                    "exit_price": current_price,
                }

    # 5. SL-to-break-even — defence-in-depth fallback that runs only
    #    when none of the four close paths fire. Once price has moved
    #    >= ``cfg["be_at_r"]`` × 1R in our favour the original
    #    invalidation level no longer needs to be defended; sliding
    #    SL to entry locks in the gain while leaving the position
    #    open to ride toward TP/VWAP. Last in the priority chain so a
    #    close verdict (TP/SL/VWAP-cross/time-decay) always wins on
    #    the same tick — BE never converts a would-be exit into
    #    "still in market". The shared helper in ``_base`` is
    #    idempotent: once SL is at entry the next +1R tick is a
    #    no-op rather than re-writing the same value.
    #
    #    ``cfg["be_at_r"]`` is operator-tunable via
    #    ``config/strategies.yaml`` (defaults to 1.0R, matching the
    #    pre-2026-05-13 hard-coded behaviour). Setting it lower
    #    (e.g. 0.5) protects faster on shallow mean-reversion runs
    #    that retrace before reaching VWAP; setting it higher
    #    (e.g. 1.5) lets the trade run further before the stop is
    #    raised. Invalid / non-positive values fall back to 1.0.
    try:
        be_at_r = float(cfg_dict.get("be_at_r", 1.0))
    except (TypeError, ValueError):
        be_at_r = 1.0
    if be_at_r <= 0:
        be_at_r = 1.0
    # ``be_offset_bps`` (2026-05-18): basis-point offset above entry
    # (long) / below entry (short) when SL trails to break-even, so
    # the close clears Bybit's round-trip fees instead of netting a
    # scratch loss. Default 0 preserves pre-this-PR behaviour for
    # configs that haven't opted in. See `_base.monitor_breakeven_sl`
    # docstring for the operator rationale.
    try:
        be_offset_bps = float(cfg_dict.get("be_offset_bps", 0.0))
    except (TypeError, ValueError):
        be_offset_bps = 0.0
    if be_offset_bps < 0:
        be_offset_bps = 0.0
    return monitor_breakeven_sl(
        open_pkg, candles_df,
        one_r_threshold=be_at_r,
        be_offset_bps=be_offset_bps,
    )
