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
# +0.16 R → +0.27 R per trade, walk-forward OOS Sharpe +0.25 → +1.22.
#
# 2026-05-09 — turtle-soup-signal-fix: cadence pair (C2+C3) shipped on
# top of the 0.30 ATR-stop baseline. Operator symptom was zero signals
# over recent sessions; Phase-1 diagnosis showed inherent backtest
# cadence at production params was ~0.9 trades/month → ~65 % chance of
# zero in any random 14-day window. Phase-2 sweep
# (`experiments/2026-05-08-all-models-training/results/phase2_sweep.json`)
# tested cadence-loosening combinations on the same Jan 2023 → Feb 2026
# window:
#
#     variant                trades  E[R]    Sharpe  IS-Sh  OOS-Sh
#     baseline (shipped)        32   +0.266  +1.33   +0.53  +1.22
#     C2 buffer 12→10           43   +0.204  +1.18   +0.27  +2.33  fail (E[R] -23 %)
#     C2b buffer 12→8           45   +0.074  +0.44   -0.00  +1.01  fail (Sharpe collapse)
#     C3 tp1 1.25→1.0           28   +0.283  +1.54   +0.62  +63.5  fail (cadence -13 %)
#     C2+C3 (shipped)           37   +0.241  +1.49   +0.72  +2.48  pass
#     C2b+C3                    41   -0.145  -0.94   -1.03  +1.00  fail (negative)
#
# C2+C3 is the only variant that lifted cadence (+16 %) with expectancy
# essentially flat (-0.025 R, within noise on n=37) and Sharpe IS+OOS
# both up. The pair must ship together — C2 alone fails the expectancy
# gate, C3 alone fails the cadence gate. Mechanism: the buffer
# loosening admits cleaner mid-depth sweeps that 12 bps was rejecting;
# the tighter TP banks profits faster and frees the cooldown earlier
# so subsequent setups can fire.
#
# 2026-05-09 — setup_lookback_bars: structural cadence fix orthogonal
# to the threshold tweak above. The pre-fix detector inspected only
# the most recent bar (iloc[-1]); the production pipeline ticks faster
# than 15 m, so on most ticks iloc[-1] was a forming bar whose body /
# range hadn't crystalised yet. K=4 covers a 1-hour scan-back — enough
# to catch a setup that closed within the last hour without
# reactivating stale signals (the strategy_monocle gate downstream
# guards against re-emitting the same setup). The backtest scanner
# already assumes full bar visibility, so this knob's cadence lift is
# not measurable via the harness; it closes the production-vs-
# backtest gap rather than moving the backtest itself.
_DEFAULTS: Dict[str, Any] = {
    "sweep_lookback_15m": 60,
    "min_sweep_buffer_bps": 10,
    "min_body_to_range": 0.60,
    "atr_period": 14,
    "atr_stop_mult": 0.30,
    "tp1_at_r": 1.00,
    "tp2_at_r": 3.0,
    "setup_lookback_bars": 4,
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
    """Append bullish_setup / bearish_setup columns plus per-stage gate
    columns for telemetry.

    Per-stage columns (``bull_swept``, ``bull_reverted``, ``bear_swept``,
    ``bear_reverted``, ``body_ok``) let ``order_package`` and the audit
    layer count which gate is rejecting each candidate without
    re-evaluating the maths. Setup logic mirrors
    ``TurtleSoupMTFv1.detect_setup``.
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

    out["bull_swept"] = out["low"] < (out["prev_low_ref"] - sweep_buffer)
    out["bull_reverted"] = out["close"] > out["prev_low_ref"]
    out["bear_swept"] = out["high"] > (out["prev_high_ref"] + sweep_buffer)
    out["bear_reverted"] = out["close"] < out["prev_high_ref"]
    out["body_ok"] = out["body_to_range"] >= min_body_to_range

    out["bullish_setup"] = out["bull_swept"] & out["bull_reverted"] & out["body_ok"]
    out["bearish_setup"] = out["bear_swept"] & out["bear_reverted"] & out["body_ok"]
    return out


def _stage_rejection_counts(enriched: pd.DataFrame, window_bars: int) -> Dict[str, int]:
    """Count per-stage rejections over the last ``window_bars`` bars.

    Buckets are mutually exclusive and ordered by gate position:
      * ``swept``                       — bars whose price pierced a swing extreme
      * ``passed_sweep_failed_revert``  — swept bars that didn't close back inside
      * ``passed_revert_failed_body``   — swept+reverted bars whose body was too small
      * ``full_setup``                  — bars satisfying every gate (= candidates)
    """
    if window_bars <= 0:
        window_bars = 1
    win = enriched.iloc[-window_bars:]
    swept = (win["bull_swept"].fillna(False) | win["bear_swept"].fillna(False))
    swept_count = int(swept.sum())
    after_revert = (
        (win["bull_swept"].fillna(False) & win["bull_reverted"].fillna(False))
        | (win["bear_swept"].fillna(False) & win["bear_reverted"].fillna(False))
    )
    after_revert_count = int(after_revert.sum())
    full = (win["bullish_setup"].fillna(False) | win["bearish_setup"].fillna(False))
    full_count = int(full.sum())
    return {
        "window_bars": int(len(win)),
        "swept": swept_count,
        "passed_sweep_failed_revert": swept_count - after_revert_count,
        "passed_revert_failed_body": after_revert_count - full_count,
        "full_setup": full_count,
    }


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

    # Scan the last K bars (most-recent first) for the freshest setup.
    # Pre-fix this checked only iloc[-1], which lost setups that closed
    # between pipeline ticks; see _DEFAULTS comment block for full
    # rationale. The strategy_monocle gate downstream prevents
    # re-emitting the same setup once an order is open.
    setup_window = max(1, int(params.get("setup_lookback_bars", 1)))
    setup_window = min(setup_window, len(enriched))

    last = None
    bars_back = None
    for k in range(setup_window):
        bar = enriched.iloc[-1 - k]
        if bool(bar.get("bullish_setup", False)) or bool(bar.get("bearish_setup", False)):
            last = bar
            bars_back = k
            break

    stage_rejections = _stage_rejection_counts(enriched, setup_window)

    if last is None:
        err = ValueError(
            f"Strategy 'turtle_soup': no setup in the last {setup_window} bars "
            f"(non-actionable)."
        )
        err.stage_rejections = stage_rejections  # type: ignore[attr-defined]
        raise err

    if bool(last.get("bullish_setup", False)):
        direction = "long"
        sweep_extreme = float(last["low"])
        level = float(last["prev_low_ref"])
    else:
        direction = "short"
        sweep_extreme = float(last["high"])
        level = float(last["prev_high_ref"])

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
            # Canonical key the order_monitor's ohlcv_fetcher reads to
            # pull fresh candles for monitor(). Without it the fetcher
            # short-circuits to None, monitor() never receives candles,
            # and the BE-SL verdict can't fire — positions sit open
            # until the +30 min watchdog cascades them.
            "timeframe": str(cfg.get("timeframe", "15m")),
            "bars_back_of_setup": int(bars_back or 0),
            "stage_rejections": stage_rejections,
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
