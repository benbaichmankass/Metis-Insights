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
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd

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
# 2026-05-03 operator directive (CP-2026-05-03-20): the strategy must
# preserve a **risk:reward of 1:2** (reward is twice risk) at the entry
# boundary while delivering a higher cadence of order packages. To honour
# both: ENTRY_STD_THRESHOLD reverted from 2.0σ to 1.0σ (above) AND
# SL_STD_MULT_DEFAULT halved from 1.0 to 0.5 here. Result at the boundary:
# reward = 1.0 × std_dev, risk = 0.5 × std_dev → reward:risk = 2:1
# (i.e. risk:reward = 1:2). Operators tuning either value must move the
# other in lock-step or the R:R contract drifts. Tunable per call via the
# ``sl_std_mult`` arg to ``build_vwap_signal`` or the matching entry in
# ``config/strategies.yaml`` (consumed by the pipeline-side
# vwap_signal_builder when it wires it through).
SL_STD_MULT_DEFAULT = 0.5


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


def build_vwap_signal(
    candles_df: pd.DataFrame,
    symbol: str,
    sl_std_mult: float = SL_STD_MULT_DEFAULT,
    htf_close: Optional[float] = None,
    htf_ema: Optional[float] = None,
    htf_band_pct: float = HTF_BAND_PCT_DEFAULT,
    timeframe: Optional[str] = None,
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
    and ``SL_STD_MULT_DEFAULT = 0.5`` together give risk:reward = 1:2
    (reward = 2 × risk) at the entry boundary, while raising cadence
    versus the prior 2.0σ / 1.0 mult configuration. Deeper excursions
    carry proportionally better R:R.
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

    logger.info(
        "VWAP signal: symbol=%s vwap=%.4f price=%.4f std=%.4f deviation=%.2f side=%s",
        symbol, vwap, current_price, std_dev, deviation, side,
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
    if side == "buy":
        stop_loss = entry_price - sl_std_mult * std_dev
    else:  # "sell"
        stop_loss = entry_price + sl_std_mult * std_dev

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
    """
    candles_df = require_candles(candles_df, "vwap")

    if _has_open_vwap_package():
        raise ValueError(
            "Strategy 'vwap': linked open package already exists; "
            "deferring entry until monitor() closes it."
        )

    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"

    signal = build_vwap_signal(candles_df, symbol=symbol)

    side = signal.get("side", "none")
    direction = side_to_direction(side)  # raises ValueError when side=="none"

    entry = last_close(candles_df)

    # Attempt VWAP-to-price TP; fall back to percentage-based.
    try:
        # Phase 1: keep the order-package adapter aligned with
        # build_vwap_signal so the dispatch path's VWAP and the
        # strategy unit's VWAP are computed from the same window.
        window = _session_anchor_slice(candles_df)
        vwap = compute_vwap(window)
        typical_price = (window["high"] + window["low"] + window["close"]) / 3.0
        std_dev = float(typical_price.std())

        if direction == "long":
            tp = vwap
            risk = entry - tp
            sl = entry + risk if risk > 0 else entry * 0.98
        else:
            tp = vwap
            risk = tp - entry
            sl = entry - risk if risk > 0 else entry * 1.02

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
    return {
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(float(sl), 8),
        "tp": round(float(tp), 8),
        "confidence": round(confidence, 4),
        "meta": {**meta, "signal": signal},
    }


# ---------------------------------------------------------------------------
# monitor() — S-047 T4: close on TP/SL/VWAP-cross/time-decay
# ---------------------------------------------------------------------------


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
        translate to a reduce-only ``close_open_position`` call,
        ``sl`` updates rewrite the package row (and, when
        ``MONITOR_APPLY_TO_EXCHANGE`` is on, the live order's stop).
        The strategy unit never touches the exchange directly.
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

    if vwap_live is not None and vwap_live != tp:
        if direction == "long" and current_price >= vwap_live:
            return {"action": "close", "reason": "vwap_cross", "exit_price": current_price}
        if direction == "short" and current_price <= vwap_live:
            return {"action": "close", "reason": "vwap_cross", "exit_price": current_price}

    # 4. Time-decay.
    cfg_dict = cfg if isinstance(cfg, dict) else {}
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
    #    >= 1R in our favour the original invalidation level no longer
    #    needs to be defended; sliding SL to entry locks in the gain
    #    while leaving the position open to ride toward TP/VWAP. Last
    #    in the priority chain so a close verdict (TP/SL/VWAP-cross/
    #    time-decay) always wins on the same tick — BE never converts
    #    a would-be exit into "still in market". The shared helper in
    #    ``_base`` is idempotent: once SL is at entry the next +1R
    #    tick is a no-op rather than re-writing the same value.
    return monitor_breakeven_sl(open_pkg, candles_df)
