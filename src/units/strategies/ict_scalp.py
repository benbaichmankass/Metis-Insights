"""ICT scalp v1 — units-layer strategy adapter.

Deterministic ICT-style scalping signal. Default timeframe is 5m; the
unit itself is timeframe-agnostic — it consumes OHLCV candles + a cfg
dict and emits one order package. Switching to 1m is a config change
(``cfg["timeframe"] = "1m"``), not a code change.

Strategy summary
----------------
A scalp setup fires on the most recent closed bar when **all** of:

1. **Liquidity sweep** in the last ``sweep_lookback_bars`` bars: a bar's
   low pierced the rolling ``swing_lookback_bars``-min of prior lows (or
   high pierced the rolling-max of prior highs) by at least
   ``sweep_buffer_bps`` of price.
2. **Displacement** after the sweep: at least one bar between the
   sweep bar and the current bar (inclusive of the bar immediately
   after the sweep) has a body of size ≥ ``displacement_atr_mult``
   times the rolling ATR and is in the direction of the setup (bullish
   body for a long, bearish body for a short).
3. **Fair Value Gap (FVG)** present in the displacement leg, in the
   direction of the setup. A bullish FVG is the 3-candle pattern where
   bar[i-2].high < bar[i].low; bearish is the mirror. The FVG must
   sit between the sweep extreme and the current price so a mitigation
   pullback is geometrically possible.
4. **Mitigation** on the most recent bar: the bar's range overlaps the
   FVG (i.e. price is currently inside the imbalance or has just
   re-entered it from the displacement side), and the bar's body
   direction matches the setup (bullish body for a long, bearish for
   a short) — this is the "clean entry confirmation".

When all four conditions are present, the unit emits:

  * ``entry``     = close of the most recent bar
  * ``sl``        = sweep extreme ± ``atr_sl_buffer_mult * ATR`` (outside
                    the swept liquidity)
  * ``tp``        = entry ± ``tp_at_r * risk``
  * ``confidence`` = blended displacement strength + sweep depth + FVG
                     fill ratio, clamped to [0, 1]

Strategies are pure signal generators (see ``_base.py``): no
``dry_run`` flag, no execution awareness, no qty.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from src.runtime.shadow_adapter import with_shadow_preds
from src.units.strategies._base import (
    monitor_breakeven_sl,
    require_candles,
)


# Defaults tuned for 5m BTCUSDT. The 1m variant typically wants
# sweep_lookback_bars and swing_lookback_bars roughly 5x larger
# (to span a similar wall-clock window) and a tighter
# displacement_atr_mult, but the operator should run a backtest
# before flipping timeframe; defaults below stay at 5m.
_DEFAULTS: Dict[str, Any] = {
    # Lookback windows
    "sweep_lookback_bars": 12,       # how recent the sweep must be (≈ 1h on 5m)
    "swing_lookback_bars": 20,       # rolling window for the swept extreme
    "atr_period": 14,
    # Sweep gate
    "sweep_buffer_bps": 5.0,         # min sweep depth in bps of close
    # Displacement gate — v2 raised 1.0 → 1.3 after the 90-day backtest
    # showed v1 admitted too many “tepid” displacement bars whose
    # follow-through couldn't carry to the 1.5R TP.
    "displacement_atr_mult": 1.3,
    "min_displacement_body_to_range": 0.55,
    # FVG gate
    "min_fvg_size_bps": 2.0,         # min FVG size in bps of close
    # Mitigation gate — v2 default is “wick_rejection”: bar wicks INTO
    # the FVG and CLOSES BACK OUTSIDE with a matching-direction body.
    # This is a stronger reversal signal than v1's “any overlap with
    # matching body”, which was the dominant timeout driver in the
    # first backtest. Legacy v1 logic is available as
    # “body_inside_fvg” for back-compat / A/B comparison.
    "mitigation_mode": "wick_rejection",  # "wick_rejection" | "body_inside_fvg"
    # Entry / risk
    "atr_sl_buffer_mult": 0.20,
    "tp_at_r": 1.5,
    # HTF bias filter (v2). When ``htf_trend_filter_enabled`` is True,
    # the caller must pass cfg["htf_close"] and cfg["htf_ema"]. Trades
    # are blocked unless their direction matches the HTF bias:
    # bullish bias = htf_close > htf_ema; bearish = htf_close < htf_ema.
    # When the caller doesn't supply both, the filter logs and skips
    # (so unit tests / fixtures without HTF data still work). The
    # runtime signal builder + backtest CLI compute HTF EMA by
    # resampling the same OHLCV feed so no second data source is
    # required.
    "htf_trend_filter_enabled": True,
    # Session filter (UTC hours). When ``session_filter_enabled`` is False the
    # gate is a no-op. When True, signals only fire if the most recent bar's
    # UTC hour is in [session_start_hour, session_end_hour). Defaults span
    # London + NY (07–17 UTC) which captures both ICT kill-zones; the
    # operator can tighten or open it via cfg.
    "session_filter_enabled": False,
    "session_start_hour": 7,
    "session_end_hour": 17,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {key: cfg.get(key, default) for key, default in _DEFAULTS.items()}


def _add_atr(df: pd.DataFrame, period: int) -> pd.DataFrame:
    """Append an ``atr`` column. Pure pandas — same formula as turtle_soup."""
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


def _detect_sweep(
    df: pd.DataFrame,
    *,
    lookback_bars: int,
    swing_lookback: int,
    sweep_buffer_bps: float,
) -> Dict[str, Any]:
    """Return the most recent liquidity sweep within ``lookback_bars`` bars.

    A liquidity sweep requires BOTH (a) the bar pierces a swing extreme
    by ``sweep_buffer_bps`` of price AND (b) the same bar closes back
    inside the prior range (the “reversion” gate). Without the
    reversion gate a regular breakout bar — which makes a fresh high or
    low and keeps going — would be misclassified as a sweep, and the
    downstream displacement leg would then look for a reversal that
    never came. Same gate ordering that turtle_soup uses.

    Returns a dict with keys ``direction`` (“long” | “short” | None),
    ``index`` (positional index in ``df``), ``level`` (the swept swing
    extreme), and ``extreme`` (how far price pierced — the bar's low for
    long sweeps, high for short).
    """
    n = len(df)
    if n < swing_lookback + 2:
        return {"direction": None}

    prev_low_ref = df["low"].rolling(swing_lookback).min().shift(1)
    prev_high_ref = df["high"].rolling(swing_lookback).max().shift(1)
    buffer_long = df["close"] * (sweep_buffer_bps / 10_000.0)
    buffer_short = buffer_long

    bull_swept = df["low"] < (prev_low_ref - buffer_long)
    bull_reverted = df["close"] > prev_low_ref
    bear_swept = df["high"] > (prev_high_ref + buffer_short)
    bear_reverted = df["close"] < prev_high_ref

    bull_setup = bull_swept & bull_reverted
    bear_setup = bear_swept & bear_reverted

    start = max(0, n - lookback_bars)
    # Most-recent-first scan so we pick the freshest sweep.
    for k in range(n - 1, start - 1, -1):
        if bool(bull_setup.iloc[k]):
            return {
                "direction": "long",
                "index": int(k),
                "level": float(prev_low_ref.iloc[k]),
                "extreme": float(df["low"].iloc[k]),
            }
        if bool(bear_setup.iloc[k]):
            return {
                "direction": "short",
                "index": int(k),
                "level": float(prev_high_ref.iloc[k]),
                "extreme": float(df["high"].iloc[k]),
            }
    return {"direction": None}


def _detect_displacement(
    df: pd.DataFrame,
    *,
    sweep_idx: int,
    direction: str,
    atr_mult: float,
    min_body_to_range: float,
) -> Optional[Dict[str, Any]]:
    """Find the first displacement bar after the sweep in the setup direction.

    Bullish displacement: close > open and body ≥ atr_mult × ATR. Mirror
    for bearish. The displacement bar must close after the sweep bar
    (strictly: index > sweep_idx) and on or before the most recent bar.
    """
    n = len(df)
    if sweep_idx >= n - 1:
        return None
    for idx in range(sweep_idx + 1, n):
        atr = float(df["atr"].iloc[idx]) if pd.notna(df["atr"].iloc[idx]) else 0.0
        if atr <= 0:
            continue
        op = float(df["open"].iloc[idx])
        cl = float(df["close"].iloc[idx])
        hi = float(df["high"].iloc[idx])
        lo = float(df["low"].iloc[idx])
        body = abs(cl - op)
        rng = max(hi - lo, 1e-12)
        if body < atr_mult * atr:
            continue
        if (body / rng) < min_body_to_range:
            continue
        if direction == "long" and cl <= op:
            continue
        if direction == "short" and cl >= op:
            continue
        return {
            "index": int(idx),
            "body": body,
            "body_to_range": float(body / rng),
            "atr_at_bar": atr,
        }
    return None


def _detect_fvg_in_leg(
    df: pd.DataFrame,
    *,
    start_idx: int,
    direction: str,
    min_size_bps: float,
) -> Optional[Dict[str, Any]]:
    """Find an FVG of the matching direction inside [start_idx, last_idx].

    Bullish FVG (3-candle): df.high.iloc[i-2] < df.low.iloc[i] — the gap
    is between those two prices. Bearish FVG: df.low.iloc[i-2] >
    df.high.iloc[i].

    Returns the most recent FVG in the leg, since a fresher FVG is more
    likely to still be unmitigated.
    """
    n = len(df)
    last = None
    lo_start = max(start_idx, 2)
    for i in range(lo_start, n):
        ref_price = float(df["close"].iloc[i])
        min_size = ref_price * (min_size_bps / 10_000.0)
        h_im2 = float(df["high"].iloc[i - 2])
        l_im2 = float(df["low"].iloc[i - 2])
        l_i = float(df["low"].iloc[i])
        h_i = float(df["high"].iloc[i])
        if direction == "long" and h_im2 < l_i:
            size = l_i - h_im2
            if size >= min_size:
                last = {
                    "index": int(i),
                    "low": float(h_im2),
                    "high": float(l_i),
                    "size": float(size),
                }
        elif direction == "short" and l_im2 > h_i:
            size = l_im2 - h_i
            if size >= min_size:
                last = {
                    "index": int(i),
                    "low": float(h_i),
                    "high": float(l_im2),
                    "size": float(size),
                }
    return last


def _bar_overlaps_fvg(
    df: pd.DataFrame, *, bar_idx: int, fvg: Dict[str, Any]
) -> bool:
    bar_low = float(df["low"].iloc[bar_idx])
    bar_high = float(df["high"].iloc[bar_idx])
    return not (bar_high < fvg["low"] or bar_low > fvg["high"])


def _passes_session_filter(
    df: pd.DataFrame, *, enabled: bool, start_hour: int, end_hour: int
) -> bool:
    if not enabled:
        return True
    try:
        ts = df.index[-1]
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            # Assume UTC for naive indices — consistent with the rest of the repo.
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        hour = int(ts.hour)
    except Exception:
        # If the index is not timestamp-like, skip the filter rather than block.
        return True
    if start_hour <= end_hour:
        return start_hour <= hour < end_hour
    # Wrap-around window (e.g. 22 → 06)
    return hour >= start_hour or hour < end_hour


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build an ICT scalp v1 order package dict.

    Parameters
    ----------
    cfg : dict
        Strategy config; ``cfg["timeframe"]`` defaults to “5m”. May
        override any key in ``_DEFAULTS``. ``cfg["symbol"]`` is required
        for the package's symbol field (falls back to “BTCUSDT”).
    candles_df : pd.DataFrame
        OHLCV frame at ``cfg["timeframe"]``. Must have columns
        ``open``, ``high``, ``low``, ``close`` (volume optional).

    Returns
    -------
    dict
        Keys: symbol, direction, entry, sl, tp, confidence, meta.

    Raises
    ------
    ValueError
        When candles are absent / empty, the OHLC columns are missing,
        the frame is too short for the configured lookback windows, or
        no setup is present on the most recent bar.
    """
    candles_df = require_candles(candles_df, "ict_scalp")
    params = _resolve_params(cfg)
    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"
    timeframe = str(cfg.get("timeframe") or "5m")

    required_cols = {"open", "high", "low", "close"}
    missing_cols = required_cols - set(candles_df.columns)
    if missing_cols:
        raise ValueError(
            f"Strategy 'ict_scalp': missing OHLC columns "
            f"{sorted(missing_cols)} in candles_df."
        )

    needed = max(
        int(params["swing_lookback_bars"]),
        int(params["atr_period"]),
        int(params["sweep_lookback_bars"]),
    ) + 5
    if len(candles_df) < needed:
        raise ValueError(
            f"Strategy 'ict_scalp': need at least {needed} candles for "
            f"the configured lookback windows; got {len(candles_df)}."
        )

    if not _passes_session_filter(
        candles_df,
        enabled=bool(params["session_filter_enabled"]),
        start_hour=int(params["session_start_hour"]),
        end_hour=int(params["session_end_hour"]),
    ):
        raise ValueError(
            "Strategy 'ict_scalp': last bar outside the configured "
            "session window — non-actionable."
        )

    df = _add_atr(candles_df, int(params["atr_period"]))

    sweep = _detect_sweep(
        df,
        lookback_bars=int(params["sweep_lookback_bars"]),
        swing_lookback=int(params["swing_lookback_bars"]),
        sweep_buffer_bps=float(params["sweep_buffer_bps"]),
    )
    if sweep.get("direction") is None:
        raise ValueError(
            f"Strategy 'ict_scalp': no liquidity sweep in last "
            f"{params['sweep_lookback_bars']} bars."
        )

    direction = sweep["direction"]
    sweep_idx = int(sweep["index"])

    displacement = _detect_displacement(
        df,
        sweep_idx=sweep_idx,
        direction=direction,
        atr_mult=float(params["displacement_atr_mult"]),
        min_body_to_range=float(params["min_displacement_body_to_range"]),
    )
    if displacement is None:
        raise ValueError(
            "Strategy 'ict_scalp': sweep found but no displacement bar "
            "in setup direction after the sweep."
        )

    fvg = _detect_fvg_in_leg(
        df,
        start_idx=sweep_idx,
        direction=direction,
        min_size_bps=float(params["min_fvg_size_bps"]),
    )
    if fvg is None:
        raise ValueError(
            "Strategy 'ict_scalp': displacement leg has no qualifying FVG."
        )

    # HTF bias filter (v2). Block trades against the HTF trend when
    # configured + caller-supplied. See _DEFAULTS comment for the
    # contract; missing values skip the filter rather than block.
    if bool(params["htf_trend_filter_enabled"]):
        htf_close = cfg.get("htf_close")
        htf_ema = cfg.get("htf_ema")
        if htf_close is not None and htf_ema is not None:
            try:
                hc, he = float(htf_close), float(htf_ema)
            except (TypeError, ValueError):
                hc = he = None
            if hc is not None and he is not None:
                if direction == "long" and hc <= he:
                    raise ValueError(
                        "Strategy 'ict_scalp': HTF bias is bearish "
                        f"(close={hc:.2f} <= ema={he:.2f}); blocking long."
                    )
                if direction == "short" and hc >= he:
                    raise ValueError(
                        "Strategy 'ict_scalp': HTF bias is bullish "
                        f"(close={hc:.2f} >= ema={he:.2f}); blocking short."
                    )

    last_idx = len(df) - 1
    # Mitigation gate (v2). Two modes — see _DEFAULTS comment on
    # mitigation_mode for rationale.
    last_open = float(df["open"].iloc[last_idx])
    last_close = float(df["close"].iloc[last_idx])
    last_high = float(df["high"].iloc[last_idx])
    last_low = float(df["low"].iloc[last_idx])
    last_body_bullish = last_close > last_open
    last_body_bearish = last_close < last_open
    matches_body = (
        (direction == "long" and last_body_bullish)
        or (direction == "short" and last_body_bearish)
    )

    mitigation_mode = str(params["mitigation_mode"]).strip().lower()
    if mitigation_mode == "wick_rejection":
        # Bar must wick INTO the FVG and CLOSE OUTSIDE it.
        # Long: low <= fvg_high (wick entered the gap from above) AND
        #       close > fvg_high (closed back above the gap) AND
        #       body is bullish.
        # Short: high >= fvg_low (wick entered the gap from below) AND
        #       close < fvg_low (closed back below the gap) AND
        #       body is bearish.
        if direction == "long":
            wicked_in = last_low <= fvg["high"]
            closed_out = last_close > fvg["high"]
        else:
            wicked_in = last_high >= fvg["low"]
            closed_out = last_close < fvg["low"]
        if not (wicked_in and closed_out and matches_body):
            raise ValueError(
                "Strategy 'ict_scalp': last bar did not produce a wick "
                "rejection at the FVG (need wick-in + close-out + "
                "matching-direction body)."
            )
    elif mitigation_mode == "body_inside_fvg":
        # Legacy v1 logic kept for A/B / regression coverage. Any
        # range overlap with a matching body is enough.
        if not _bar_overlaps_fvg(df, bar_idx=last_idx, fvg=fvg) or not matches_body:
            raise ValueError(
                "Strategy 'ict_scalp': last bar did not mitigate the FVG "
                "with a matching-direction body."
            )
    else:
        raise ValueError(
            f"Strategy 'ict_scalp': unknown mitigation_mode "
            f"{mitigation_mode!r}; expected 'wick_rejection' or "
            "'body_inside_fvg'."
        )

    # Risk model
    entry = last_close
    atr_now = float(df["atr"].iloc[last_idx]) if pd.notna(df["atr"].iloc[last_idx]) else 0.0
    sl_buffer = float(params["atr_sl_buffer_mult"]) * atr_now
    if direction == "long":
        sl = sweep["extreme"] - sl_buffer
        risk = entry - sl
    else:
        sl = sweep["extreme"] + sl_buffer
        risk = sl - entry

    if risk <= 0:
        raise ValueError(
            "Strategy 'ict_scalp': non-positive risk after stop "
            "computation; skipping."
        )

    tp_at_r = float(params["tp_at_r"])
    if direction == "long":
        tp = entry + tp_at_r * risk
    else:
        tp = entry - tp_at_r * risk

    # Confidence: blend three already-gated signals so the score is
    # interpretable and bounded.
    body_to_range = float(displacement["body_to_range"])
    sweep_depth_atr = (
        abs(sweep["extreme"] - sweep["level"]) / atr_now if atr_now > 0 else 0.0
    )
    fvg_size_norm = min(float(fvg["size"]) / max(atr_now, 1e-9), 1.0) if atr_now > 0 else 0.0
    confidence = round(
        min(
            0.4 * body_to_range
            + 0.3 * min(sweep_depth_atr, 1.0)
            + 0.3 * fvg_size_norm,
            1.0,
        ),
        4,
    )

    package = {
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(float(sl), 8),
        "tp": round(float(tp), 8),
        "confidence": confidence,
        "meta": {
            "strategy_name": "ict_scalp_5m",
            "timeframe": timeframe,
            "setup_tf": timeframe,
            "mitigation_mode": mitigation_mode,
            "htf_filter_active": bool(
                params["htf_trend_filter_enabled"]
                and cfg.get("htf_close") is not None
                and cfg.get("htf_ema") is not None
            ),
            "sweep_level": float(sweep["level"]),
            "sweep_extreme": float(sweep["extreme"]),
            "sweep_idx_from_end": int(last_idx - sweep_idx),
            "displacement_idx_from_end": int(last_idx - int(displacement["index"])),
            "displacement_body_to_range": body_to_range,
            "fvg_low": float(fvg["low"]),
            "fvg_high": float(fvg["high"]),
            "fvg_size": float(fvg["size"]),
            "atr": atr_now,
            "risk_per_unit": float(risk),
        },
    }
    return with_shadow_preds(
        package,
        predictors=_resolve_shadow_predictors(cfg),
        feature_row=_build_shadow_feature_row(package),
    )


# ---------------------------------------------------------------------------
# Shadow-predictor helpers (WS7 audit-only)
# ---------------------------------------------------------------------------


def _resolve_shadow_predictors(cfg: Dict[str, Any]) -> list:
    """Pick the shadow predictors for this tick from cfg.

    Same 3-mode resolution priority as turtle_soup._resolve_shadow_predictors
    (S-AI-WS7-PART-5): explicit plural injection wins, then singular
    legacy injection, then config-driven shadow_model_ids resolved via
    the registry-backed factory.
    """
    if "_shadow_predictors" in cfg:
        return list(cfg["_shadow_predictors"] or [])
    single = cfg.get("_shadow_predictor")
    if single is not None:
        return [single]
    ids = cfg.get("shadow_model_ids") or []
    if not ids:
        return []
    from ml.registry.model_registry import ModelRegistry
    from ml.shadow.factory import (
        DEFAULT_LOG_PATH,
        DEFAULT_REGISTRY_ROOT,
        resolve_predictors,
    )

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
    """Project the order package to a signal-time feature dict.

    Shared surface with other strategies (strategy_name, setup_type,
    timeframe, direction, confidence, atr, body_to_range) plus
    ict_scalp-specific features (sweep_depth_atr, fvg_size_norm,
    displacement_idx_from_end). Outcome columns (pnl, r_multiple) are
    excluded by construction.
    """
    meta = package.get("meta") or {}
    atr = float(meta.get("atr", 0.0) or 0.0)
    sweep_extreme = float(meta.get("sweep_extreme", 0.0) or 0.0)
    sweep_level = float(meta.get("sweep_level", 0.0) or 0.0)
    fvg_size = float(meta.get("fvg_size", 0.0) or 0.0)
    sweep_depth_atr = abs(sweep_extreme - sweep_level) / atr if atr > 0 else 0.0
    fvg_size_norm = min(fvg_size / max(atr, 1e-9), 1.0) if atr > 0 else 0.0
    return {
        "strategy_name":             "ict_scalp_5m",
        "setup_type":                meta.get("setup_tf", ""),
        "timeframe":                 meta.get("timeframe", ""),
        "direction":                 package.get("direction", ""),
        "confidence":                float(package.get("confidence", 0.0) or 0.0),
        "atr":                       atr,
        "body_to_range":             float(meta.get("displacement_body_to_range", 0.0) or 0.0),
        "sweep_depth_atr":           sweep_depth_atr,
        "fvg_size_norm":             fvg_size_norm,
        "displacement_idx_from_end": int(meta.get("displacement_idx_from_end", 0) or 0),
    }


# ---------------------------------------------------------------------------
# monitor() — break-even-after-1R, same contract as turtle_soup / vwap
# ---------------------------------------------------------------------------


def monitor(cfg, candles_df, open_pkg):
    """Re-evaluate an open ict_scalp package against fresh candles.

    v1 monitor: trail SL to break-even once price has moved 1R in the
    trade's favour. Delegates to ``monitor_breakeven_sl`` so the
    behaviour matches the rest of the strategy roster.
    """
    if candles_df is None:
        return None
    return monitor_breakeven_sl(open_pkg, candles_df)
