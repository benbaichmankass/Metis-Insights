"""Turtle Soup MTF v1 — units layer adapter (S-012 PR C1).

Single-frame port of ``strategies.turtle_soup_mtf_v1.TurtleSoupMTFv1``
that conforms to the ``order_package(cfg, candles_df) -> dict`` contract
in ``src/units/strategies/_base.py``.

Strategy summary
----------------
Sweep + reversal at the configured timeframe (15m by default).  The
strategy looks for a bar that:

* Pierces a recent swing extreme (low for bullish, high for bearish) by
  more than a sweep buffer, AND
* Closes back inside the prior range, AND
* Has a body-to-range ratio of at least ``min_body_to_range``.

When the most recent bar satisfies either bullish or bearish conditions,
this adapter emits a long or short ``OrderPackage`` signal.

Single-frame simplification
---------------------------
The legacy class is multi-TF (15m setup → 1m entry confirmation).  The
adapter operates on the single 15m candles_df provided by the runtime
pipeline.  Entry is the close of the trigger bar.  Stop is computed as in
the legacy class (sweep extreme ± ATR * atr_stop_mult).  Primary TP is
``tp1_at_r`` units of risk (default 1.25R).  ``tp2_at_r`` is carried in
``meta`` so downstream consumers can still trail to the secondary target.

Strategies are pure signal generators (see ``_base.py`` docstring): no
``dry_run`` flag, no execution awareness.  The dry/live decision lives in
the Accounts layer.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from src.units.strategies._base import require_candles


# Defaults mirror strategies/turtle_soup_mtf_v1.py:38-55. Any caller may
# override via cfg.get(<name>); the runtime pipeline merges
# config/strategies.yaml turtle_soup params into cfg in PR C3.
#
# 2026-05-08 — atr_stop_mult tightened 0.35 → 0.30 per the
# all-models training run (`experiments/2026-05-08-all-models-training`).
# 38-month BTCUSDT 15m backtest (Jan 2023 → Feb 2026): full-sample
# Sharpe +0.80 → +1.33, win rate 51.5 % → 56.3 %, expectancy
# +0.16 R → +0.27 R per trade, walk-forward OOS Sharpe +0.25 → +1.22
# (OOS *better* than IS — regime-robust). Cadence essentially unchanged
# (33 → 32 trades over 38 months). Mechanism: 0.35 set the stop slightly
# past the post-sweep wick, capturing legitimate sweep depth as a
# "stop run" rather than a fakeout invalidation; 0.30 trims that to
# just past the actual swing extreme. Sweep was monotonic 0.25 → 0.30 →
# 0.35 → 0.45 with a clear quality peak in the 0.25-0.30 band; 0.30 sits
# at the high-cadence edge of that band so the cadence cost is zero.
_DEFAULTS: Dict[str, Any] = {
    "sweep_lookback_15m": 60,
    "min_sweep_buffer_bps": 12,
    "min_body_to_range": 0.60,
    "atr_period": 14,
    "atr_stop_mult": 0.30,
    "tp1_at_r": 1.25,
    "tp2_at_r": 3.0,
}


def _add_atr(df: pd.DataFrame, period: int) -> pd.DataFrame:
    """Append an ``atr`` column using the same logic as the legacy class.

    Pure pandas — no pandas_ta dependency.
    """
    out = df.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            (out["high"] - out["low"]).abs(),
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(period, min_periods=period).mean()
    return out


def _detect_setup(df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
    """Append bullish_setup / bearish_setup columns.

    Mirrors ``TurtleSoupMTFv1.detect_setup``.
    """
    lookback = int(params["sweep_lookback_15m"])
    min_body_to_range = float(params["min_body_to_range"])
    sweep_buffer_bps = float(params["min_sweep_buffer_bps"])

    out = df.copy()
    out = _add_atr(out, int(params["atr_period"]))

    out["prev_high_ref"] = out["high"].rolling(lookback).max().shift(1)
    out["prev_low_ref"] = out["low"].rolling(lookback).min().shift(1)
    out["range"] = out["high"] - out["low"]
    out["body"] = (out["close"] - out["open"]).abs()
    out["body_to_range"] = np.where(out["range"] > 0, out["body"] / out["range"], 0)

    sweep_buffer = np.maximum(
        out["close"] * (sweep_buffer_bps / 10000.0),
        out["atr"].fillna(0) * 0.05,
    )

    out["bullish_setup"] = (
        (out["low"] < (out["prev_low_ref"] - sweep_buffer))
        & (out["close"] > out["prev_low_ref"])
        & (out["body_to_range"] >= min_body_to_range)
    )
    out["bearish_setup"] = (
        (out["high"] > (out["prev_high_ref"] + sweep_buffer))
        & (out["close"] < out["prev_high_ref"])
        & (out["body_to_range"] >= min_body_to_range)
    )
    return out


def _resolve_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return strategy params with cfg overrides on top of _DEFAULTS."""
    return {key: cfg.get(key, default) for key, default in _DEFAULTS.items()}


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build a Turtle Soup MTF v1 OrderPackage dict.

    Parameters
    ----------
    cfg : dict
        Strategy config from units.yaml (merged with strategies.yaml params
        and the resolved symbol by the Coordinator / pipeline).
    candles_df : pd.DataFrame
        OHLCV frame at the configured setup timeframe (15m default).
        Required — raises ValueError when absent.

    Returns
    -------
    dict
        Keys: symbol, direction, entry, sl, tp, confidence, meta.

    Raises
    ------
    ValueError
        When candles_df is absent, has too few rows for the lookback /
        ATR window, or no setup is present on the most recent bar.
    """
    candles_df = require_candles(candles_df, "turtle_soup")
    params = _resolve_params(cfg)
    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"

    needed = max(int(params["sweep_lookback_15m"]), int(params["atr_period"])) + 2
    if len(candles_df) < needed:
        raise ValueError(
            f"Strategy 'turtle_soup': need at least {needed} candles for "
            f"lookback / ATR window; got {len(candles_df)}."
        )

    enriched = _detect_setup(candles_df, params)
    last = enriched.iloc[-1]

    if bool(last.get("bullish_setup", False)):
        direction = "long"
        sweep_extreme = float(last["low"])
        level = float(last["prev_low_ref"])
    elif bool(last.get("bearish_setup", False)):
        direction = "short"
        sweep_extreme = float(last["high"])
        level = float(last["prev_high_ref"])
    else:
        raise ValueError(
            "Strategy 'turtle_soup': no setup on the latest bar (non-actionable)."
        )

    entry = float(last["close"])
    atr = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
    atr_stop_mult = float(params["atr_stop_mult"])

    if direction == "long":
        sl = min(sweep_extreme, level) - atr * atr_stop_mult
        risk = entry - sl
    else:
        sl = max(sweep_extreme, level) + atr * atr_stop_mult
        risk = sl - entry

    if risk <= 0:
        raise ValueError(
            "Strategy 'turtle_soup': non-positive risk after stop computation; "
            "skipping signal."
        )

    tp1_at_r = float(params["tp1_at_r"])
    tp2_at_r = float(params["tp2_at_r"])
    if direction == "long":
        tp = entry + tp1_at_r * risk
        tp2 = entry + tp2_at_r * risk
    else:
        tp = entry - tp1_at_r * risk
        tp2 = entry - tp2_at_r * risk

    # Confidence: blend body strength and sweep cleanliness, both already
    # gated by the setup conditions. Body-to-range is in [0, 1]; sweep
    # depth normalised to ATR is clamped to [0, 1].
    body_to_range = float(last["body_to_range"]) if pd.notna(last["body_to_range"]) else 0.0
    if atr > 0:
        sweep_depth_atr = abs(sweep_extreme - level) / atr
    else:
        sweep_depth_atr = 0.0
    confidence = round(min(0.5 * body_to_range + 0.5 * min(sweep_depth_atr, 1.0), 1.0), 4)

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(float(sl), 8),
        "tp": round(float(tp), 8),
        "confidence": confidence,
        "meta": {
            "level": level,
            "sweep_extreme": sweep_extreme,
            "atr": atr,
            "risk_per_unit": float(risk),
            "tp2": round(float(tp2), 8),
            "body_to_range": body_to_range,
            "setup_tf": str(cfg.get("timeframe", "15m")),
        },
    }


# ---------------------------------------------------------------------------
# monitor() — S-030 PR2 (architecture-audit-2026-05-02 P1-4)
# ---------------------------------------------------------------------------


def monitor(cfg, candles_df, open_pkg):
    """Re-evaluate an open turtle_soup order package against fresh candles.

    Per CLAUDE.md § Architecture rules § 2 the strategy unit monitors
    open packages. v1 logic — break-even SL after 1R; the sweep/reversal
    thesis is "the prior swing held"; once 1R has been captured the
    original invalidation level no longer needs to be defended.

    Future versions can add: opposite-sweep close (the next swing-low
    sweep on a long-bias trade), time-decay close, structure-break
    close.

    Parameters mirror ``order_package``; see ``_base.monitor_breakeven_sl``
    for the return contract.
    """
    from src.units.strategies._base import monitor_breakeven_sl
    return monitor_breakeven_sl(open_pkg, candles_df)
