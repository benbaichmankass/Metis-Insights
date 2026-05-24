"""Failed-breakout fade — units-layer adapter
(S-STRAT-IMPROVE-S9, complementary-strategy R&D, operator-approved
2026-05-24 for shadow data-collection).

Strategy summary
----------------
The literal MIRROR of the live Donchian trend-follower
(``src/units/strategies/trend_donchian.py``): where the trend-follower
BUYS a confirmed Donchian breakout, this FADES a *failed* one — a bar
that pierces beyond the prior-bar channel (a liquidity grab) but closes
back inside (rejection). Gated to chop (low ADX), with a Chandelier ATR
trailing exit — the same wide-stop-let-winners-run construction that
made the trend-follower net-positive.

Validation (research harness ``scripts/backtest_fade.py``, 6yr BTCUSDT
2020-2026, net-of-fee): the failed-breakout fade is net-NEGATIVE with a
tight target (= turtle_soup) but net-POSITIVE once the exit is a runner
(monotonic tp1r -> mid -> far -> trail). The chosen config — 4h, donchian
20, Chandelier trail 3.5, ADX<20 chop-gate — is +64.2R / 6yr, max-DD
13.9R, net-positive every year, fee-robust (still +39.8R at 15bps round
-trip, double the modelled 7.5), and crucially **uncorrelated** with the
live 2h trend-follower (monthly_corr 0.035). Equal-weight blend with the
trend-follower lifts return/drawdown 1.97 -> 3.80 and nearly halves
max-DD — the diversification payoff. Full evidence:
docs/audits/fade-breakout-complement-2026-05-24.md.

CAVEAT (why this runs in SHADOW, not live): a nested walk-forward picks
donchian-20 / trail-3.5 as the train-only (2020-2023) winner, and it
holds out-of-sample (2024-2026 +16.2R, exp 0.246) — but OOS expectancy
decays ~half vs train and the OOS profit is month-concentrated (strip
the single best month and the remainder is ~flat; 42% of OOS months
positive). Meaningfully more fragile than the trend-follower. It is
wired `execution: shadow` so it logs order packages on real ticks (data
collection) but never sends a live order, pending more live proof.

This adapter ports the validated entry/exit logic from
``scripts/backtest_fade.py`` into the live ``order_package(cfg,
candles_df) -> dict`` + ``monitor(cfg, candles_df, open_pkg)`` contract.
The ``monitor()`` trailing-stop logic is identical to the trend-follower's
(same Chandelier ratchet, same frozen-entry-ATR semantics) because both
exit on a Chandelier trail — see that module's docstring for the full
rationale on the meta-carried params and the correct-side-of-price guard.

Strategies are pure signal generators (no dry/live awareness); the
dry/live decision lives in the Accounts layer per ``mode:`` /
``execution:`` in config.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pandas as pd

from src.units.strategies._base import require_candles


# Defaults mirror the validated config from scripts/backtest_fade.py +
# the nested walk-forward train-winner (docs/audits/fade-breakout-
# complement-2026-05-24.md): 4h / donchian 20 / atr 14 / stop-buffer 0.5
# / trail 3.5 / ADX<20. ``trail_mult`` is the Chandelier distance (same
# let-winners-run lever as the trend-follower; looser trail was
# consistently better across the plateau). Any caller may override via
# cfg.get(<name>); the runtime builder merges
# config/strategies.yaml::fade_breakout_4h params into cfg.
_DEFAULTS: Dict[str, Any] = {
    "donchian": 20,
    "atr_period": 14,
    # Initial stop sits this many ATR beyond the rejection wick — the
    # fade is wrong if price makes a fresh extreme past the grab.
    "atr_stop_buffer": 0.5,
    # Minimum pierce beyond the band (in ATR) to count as a breakout
    # attempt worth fading. 0.0 = any pierce-and-reject qualifies.
    "pierce_min": 0.0,
    # Chandelier trail distance (sole profit-exit, no fixed TP).
    "trail_mult": 3.5,
    # Regime gate: only fade when ADX < this (chop). A failed breakout in
    # a strong trend tends to resolve as continuation, not reversion, so
    # the fade bleeds in high-ADX regimes — the gate keeps it in the chop
    # where it has its edge (and where the trend-follower is flat, hence
    # the complementarity).
    "adx_max": 20.0,
    "adx_period": 14,
    # No fixed profit target — the Chandelier trail is the sole
    # profit-exit (matches the backtest). ``tp`` is set this many R away
    # as a far, effectively-unreachable sentinel that still satisfies the
    # pipeline's "signal carries full SL/TP" gate.
    "tp_r": 50.0,
    "timeframe": "4h",
}


def _resolve_params(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return strategy params with cfg overrides on top of _DEFAULTS."""
    return {key: cfg.get(key, default) for key, default in _DEFAULTS.items()}


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR as the simple moving average of True Range — identical formula
    to ``scripts/backtest_fade.py::_atr`` so the live stop distance matches
    what was validated."""
    h, low, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's ADX — identical to ``scripts/backtest_fade.py::_adx`` so the
    live regime gate matches the validated one. High ADX = trending (fade
    bleeds); low ADX = chop (fade's edge)."""
    h, low, c = df["high"], df["low"], df["close"]
    up = h.diff()
    down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up.clip(lower=0)
    minus_dm = ((down > up) & (down > 0)) * down.clip(lower=0)
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, pd.NA)
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, pd.NA)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
    return dx.ewm(alpha=alpha, adjust=False).mean()


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def order_package(cfg: dict, candles_df: Optional[pd.DataFrame] = None) -> dict:
    """Build a fade_breakout_4h OrderPackage dict from the latest candles.

    Returns a dict with keys: symbol, direction, entry, sl, tp,
    confidence, meta.

    Raises ``ValueError`` (non-actionable; the runtime builder treats it
    as side="none") when candles are absent, there are too few rows for
    the windows, the regime is trending (ADX >= adx_max), or the latest
    bar is not a failed breakout.
    """
    candles_df = require_candles(candles_df, "fade_breakout_4h")
    params = _resolve_params(cfg)
    symbol = cfg.get("symbol") or cfg.get("SYMBOL") or "BTCUSDT"

    donchian = int(params["donchian"])
    atr_period = int(params["atr_period"])
    atr_stop_buffer = float(params["atr_stop_buffer"])
    pierce_min = float(params["pierce_min"])
    trail_mult = float(params["trail_mult"])
    adx_max = _coerce_float(params["adx_max"])
    adx_period = int(params["adx_period"])
    tp_r = float(params["tp_r"])
    timeframe = str(cfg.get("timeframe") or params["timeframe"])

    needed = donchian + atr_period + adx_period + 2
    if len(candles_df) < needed:
        raise ValueError(
            f"Strategy 'fade_breakout_4h': need at least {needed} candles for "
            f"the donchian({donchian}) / atr({atr_period}) / adx({adx_period}) "
            f"windows; got {len(candles_df)}."
        )

    df = candles_df.reset_index(drop=True)
    atr_series = _atr(df, atr_period)
    dc_hi = df["high"].rolling(donchian).max().shift(1)
    dc_lo = df["low"].rolling(donchian).min().shift(1)
    # ADX of the PRIOR bar (shift(1)) — matches the backtest's no-lookahead
    # gate: the entry bar's own price action decides the fade, the prior
    # bar's ADX decides the regime.
    adx_series = _adx(df, adx_period).shift(1) if adx_max is not None else None

    atr = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else 0.0
    close = float(df["close"].iloc[-1])
    bar_hi = float(df["high"].iloc[-1])
    bar_lo = float(df["low"].iloc[-1])
    hi = dc_hi.iloc[-1]
    lo = dc_lo.iloc[-1]

    if atr <= 0 or pd.isna(hi) or pd.isna(lo):
        raise ValueError(
            "Strategy 'fade_breakout_4h': ATR non-positive or Donchian channel "
            "undefined on the latest bar (non-actionable)."
        )

    # Regime gate: only fade in chop.
    adx_val: Optional[float] = None
    if adx_max is not None:
        adx_raw = adx_series.iloc[-1] if adx_series is not None else None
        adx_val = float(adx_raw) if pd.notna(adx_raw) else None
        if adx_val is None or adx_val >= adx_max:
            raise ValueError(
                f"Strategy 'fade_breakout_4h': regime not chop "
                f"(ADX={adx_val} >= {adx_max}) — non-actionable."
            )

    hi = float(hi)
    lo = float(lo)
    # Failed-breakout detection: pierce beyond the band by >= pierce_min x
    # ATR, then close back inside. Upside-failed -> short; downside -> long.
    if bar_hi >= hi + pierce_min * atr and close < hi:
        direction = "short"
        pierce_depth = (bar_hi - hi) / atr
    elif bar_lo <= lo - pierce_min * atr and close > lo:
        direction = "long"
        pierce_depth = (lo - bar_lo) / atr
    else:
        raise ValueError(
            f"Strategy 'fade_breakout_4h': no failed breakout on the latest bar "
            f"(close={close}, channel [{lo}, {hi}], bar [{bar_lo}, {bar_hi}]) "
            f"— non-actionable."
        )

    entry = close
    if direction == "short":
        sl = bar_hi + atr_stop_buffer * atr
        risk = sl - entry
        tp = entry - tp_r * risk
    else:
        sl = bar_lo - atr_stop_buffer * atr
        risk = entry - sl
        tp = entry + tp_r * risk

    if risk <= 0:
        raise ValueError(
            "Strategy 'fade_breakout_4h': non-positive risk after stop "
            "computation; skipping signal."
        )

    # Confidence: how far the breakout pierced past the channel before
    # rejecting, normalised to ATR and clamped to [0, 1]. A deep grab that
    # snapped back scores higher than a marginal poke.
    confidence = round(min(max(pierce_depth, 0.0), 1.0), 4)

    try:
        entry_time = str(df["timestamp"].iloc[-1])
    except (KeyError, IndexError):
        entry_time = None

    package = {
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(float(sl), 8),
        "tp": round(float(tp), 8),
        "confidence": confidence,
        "meta": {
            "donchian_hi": hi,
            "donchian_lo": lo,
            "donchian": donchian,
            # Entry-time ATR is FROZEN here and used by the monitor for the
            # trail distance, matching the backtest's fixed-ATR trail.
            "atr": atr,
            "atr_period": atr_period,
            "atr_stop_buffer": atr_stop_buffer,
            "pierce_min": pierce_min,
            "trail_mult": trail_mult,
            "adx": adx_val,
            "adx_max": adx_max,
            "tp_r": tp_r,
            "risk_per_unit": float(risk),
            "entry_time": entry_time,
            # Canonical key the order_monitor's ohlcv_fetcher reads to pull
            # fresh candles for monitor(). Without it the trail never updates.
            "timeframe": timeframe,
        },
    }
    return package


# ---------------------------------------------------------------------------
# monitor() — live Chandelier ATR trailing stop
#
# Identical mechanics to trend_donchian.monitor(): SL-cross close, far-TP
# sentinel close, then a since-entry Chandelier ratchet using the frozen
# entry-time ATR. Reads every trail parameter from open_pkg["meta"] because
# run_monitor_tick passes cfg={} in production. See trend_donchian.py for
# the full design rationale.
# ---------------------------------------------------------------------------


def _since_entry(candles_df: pd.DataFrame, open_pkg: Dict[str, Any]) -> pd.DataFrame:
    """Restrict the candle window to bars at/after the package entry time
    (the Chandelier trail tracks the extreme SINCE ENTRY). Falls back to
    the full frame when the entry time / timestamp column is unavailable —
    the correct-side-of-price guard still prevents an instant stop-out."""
    meta = open_pkg.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta) if meta else {}
        except Exception:  # noqa: BLE001
            meta = {}
    entry_ts = (meta.get("entry_time") if isinstance(meta, dict) else None) or \
        open_pkg.get("created_at")
    if entry_ts is None or "timestamp" not in getattr(candles_df, "columns", []):
        return candles_df
    try:
        ts = pd.to_datetime(candles_df["timestamp"], utc=True, errors="coerce")
        cutoff = pd.to_datetime(entry_ts, utc=True, errors="coerce")
        if pd.isna(cutoff):
            return candles_df
        filtered = candles_df[ts >= cutoff]
        return filtered if len(filtered) > 0 else candles_df
    except Exception:  # noqa: BLE001
        return candles_df


def monitor(cfg, candles_df, open_pkg):
    """Re-evaluate an open fade_breakout_4h package against fresh candles.

    Close-path priority (first match wins), then the trailing ratchet:
    (1) SL-cross full close, (2) far-sentinel TP-cross full close,
    (3) Chandelier trail ratchet at ``extreme ∓ trail_mult × ATR`` using
    the since-entry extreme + frozen entry-time ATR, returned as
    ``{"sl": new_sl}`` only when it tightens the stop AND sits on the
    correct side of price. Otherwise ``None``.
    """
    if candles_df is None or len(candles_df) == 0:
        return None
    try:
        current_price = float(candles_df["close"].iloc[-1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None

    try:
        sl = float(open_pkg["sl"])
        direction = str(open_pkg["direction"]).lower()
    except (KeyError, TypeError, ValueError):
        return None
    if direction not in ("long", "short"):
        return None

    meta = open_pkg.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta) if meta else {}
        except Exception:  # noqa: BLE001
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    cfg_dict = cfg if isinstance(cfg, dict) else {}

    # 1. SL-cross close.
    if direction == "long" and current_price <= sl:
        return {"action": "close", "reason": "sl_cross", "exit_price": current_price}
    if direction == "short" and current_price >= sl:
        return {"action": "close", "reason": "sl_cross", "exit_price": current_price}

    # 2. TP-cross close (far sentinel; rarely fires).
    tp = _coerce_float(open_pkg.get("tp"))
    if tp is not None:
        if direction == "long" and current_price >= tp:
            return {"action": "close", "reason": "tp_cross", "exit_price": current_price}
        if direction == "short" and current_price <= tp:
            return {"action": "close", "reason": "tp_cross", "exit_price": current_price}

    # 3. Chandelier trail ratchet.
    atr = _coerce_float(meta.get("atr"))
    if atr is None or atr <= 0:
        period = int(
            meta.get("atr_period")
            or cfg_dict.get("atr_period")
            or _DEFAULTS["atr_period"]
        )
        try:
            atr = float(_atr(candles_df, period).iloc[-1])
        except Exception:  # noqa: BLE001
            return None
    if atr is None or atr <= 0:
        return None

    trail_mult = (
        _coerce_float(meta.get("trail_mult"))
        or _coerce_float(cfg_dict.get("trail_mult"))
        or float(_DEFAULTS["trail_mult"])
    )

    window = _since_entry(candles_df, open_pkg)
    try:
        if direction == "long":
            ext = float(window["high"].max())
            candidate = ext - trail_mult * atr
            if candidate > sl and candidate < current_price:
                return {"sl": round(candidate, 8)}
        else:
            ext = float(window["low"].min())
            candidate = ext + trail_mult * atr
            if candidate < sl and candidate > current_price:
                return {"sl": round(candidate, 8)}
    except (KeyError, ValueError, TypeError):
        return None

    return None
