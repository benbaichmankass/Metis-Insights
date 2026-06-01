"""ADX-14 regime detector (PERF-20260601-002 step 2, phase 1).

The single source of truth for the regime classifier the regime router uses,
the regime-roster matrix used to bucket trades, and the live fade/fvg
strategies already gate on. Output is purely informational at phase 1 — no
intent decision changes; the goal is to LOG the regime stream per tick so we
can confirm it matches the matrix's base rates (chop ~30% / transitional
~19% / trending ~51% on 1h BTC).

Consolidates two pre-existing identical ADX implementations:
  * ``scripts/research/regime_matrix.py::_adx`` — research side; what the
    2026-06-01 regime-roster matrix was tagged with.
  * ``src/units/strategies/fade_breakout_4h.py::_adx`` (and the matching
    ``fvg_range_15m._adx``) — live entry gates.

Both compute Wilder's ADX over a configurable period; they only differ in
the divide-by-zero NaN literal (``np.nan`` vs ``float("nan")``, equivalent
under pandas float-dtype). The fade/fvg unit files KEEP their own local
``_adx`` for now (they're Tier-3 strategy code; refactoring them to import
this module would be a separate Tier-3 PR). New code should call
``detect_regime`` here.

API
---
``detect_regime(candles_df, *, adx_period=14) -> {regime, adx, source}``
    Pure function. Takes an OHLC(V) frame, returns the latest bar's
    classification. ``regime`` is one of ``"chop" | "transitional" |
    "trending" | "unknown"``; ``adx`` is the float ADX-14 value (NaN when
    unknown); ``source`` is the detector identifier (currently
    ``"adx-14"``; a future shadow-model integration will swap it for
    ``"classifier-v0"``).

``regime_label(adx_value)``
    The threshold mapping, exported separately so callers can label a
    pre-computed ADX without re-running the detector.

``wilder_adx(df, period=14)``
    The full ADX series (not just the latest bar) — handy for backtests /
    matrix tooling. Same primitive ``regime_matrix.py`` uses.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

# ADX cut-points (mirrors the regime-roster matrix and the live fade/fvg
# entry gates). <20 = chop/range, 20-25 = transitional, >=25 = trending.
CHOP_MAX_ADX: float = 20.0
TREND_MIN_ADX: float = 25.0

# Detector source tag — propagated into the audit row so the consumer can
# tell which detector emitted the label. Phase-2 will add "classifier-v0"
# once the shadow regime model is validated against ADX-14.
_SOURCE_ADX_14 = "adx-14"

# Minimum bars to compute a meaningful Wilder EWMA at the requested period.
# Wilder's ADX needs roughly 2x the period to converge from a cold start;
# below that the first bars carry warmup noise. 30 = comfortable margin for
# the live default period=14.
_MIN_BARS = 30


def wilder_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ADX(period) over the full frame.

    Returns a Series aligned to ``df.index``. The implementation matches
    ``src/units/strategies/fade_breakout_4h._adx`` (live entry gate) and
    ``scripts/research/regime_matrix._adx`` (research matrix) bit-for-bit
    except for the dtype-preserving NaN choice — see module docstring.

    ``period`` defaults to 14 (the value live + research both use); kept
    as a kwarg in case a future shadow-model integration wants a different
    smoothing horizon.
    """
    h, low, c = df["high"], df["low"], df["close"]
    up = h.diff()
    down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up.clip(lower=0)
    minus_dm = ((down > up) & (down > 0)) * down.clip(lower=0)
    pc = c.shift(1)
    tr = pd.concat([(h - low), (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    # Divide-by-zero guard: replace with float NaN to preserve float dtype
    # (pd.NA upcasts to object and breaks the trailing ewm — see the
    # 2026-05-25 live crash that prompted the fade_breakout_4h fix).
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False).mean()


def regime_label(adx_value: Optional[float]) -> str:
    """Map a single ADX value to a regime label.

    Mirrors ``scripts/research/regime_matrix._regime`` so the live
    detector and the offline matrix tag the same bar identically.
    """
    if adx_value is None:
        return "unknown"
    try:
        a = float(adx_value)
    except (TypeError, ValueError):
        return "unknown"
    if a != a:  # NaN
        return "unknown"
    if a < CHOP_MAX_ADX:
        return "chop"
    if a < TREND_MIN_ADX:
        return "transitional"
    return "trending"


def detect_regime(
    candles_df: Optional[pd.DataFrame],
    *,
    adx_period: int = 14,
) -> Dict[str, Any]:
    """Classify the LATEST bar of ``candles_df`` by ADX-14 regime.

    Phase 1 of the regime router (observability only, no enforcement).
    Returns ``{"regime": <label>, "adx": <float|None>, "source": "adx-14"}``.

    Behaviour:
      * ``candles_df`` ``None`` / empty / missing required OHLC columns
        → ``regime="unknown"``, ``adx=None``. Never raises — the detector
        is logging-only at phase 1 and must not break the tick loop.
      * Fewer than ``_MIN_BARS`` rows → still computes (Wilder converges
        from a cold start) but the result carries warmup noise; callers
        should expect that.
      * The series is computed over the whole frame; only the LAST value
        is returned (the regime at the bar that just closed). Strategies
        normally pass their own candles (per-strategy TF), so the regime
        is computed at that strategy's timeframe — matches how the matrix
        was measured.

    Pure function; no I/O, no logging.
    """
    if candles_df is None:
        return {"regime": "unknown", "adx": None, "source": _SOURCE_ADX_14}
    if not hasattr(candles_df, "columns"):
        return {"regime": "unknown", "adx": None, "source": _SOURCE_ADX_14}
    if len(candles_df) == 0:
        return {"regime": "unknown", "adx": None, "source": _SOURCE_ADX_14}
    needed = {"high", "low", "close"}
    if not needed.issubset(set(candles_df.columns)):
        return {"regime": "unknown", "adx": None, "source": _SOURCE_ADX_14}
    try:
        series = wilder_adx(candles_df, period=adx_period)
        latest = series.iloc[-1]
    except Exception:  # noqa: BLE001 — observability-only, never break the tick
        return {"regime": "unknown", "adx": None, "source": _SOURCE_ADX_14}
    if pd.isna(latest):
        return {"regime": "unknown", "adx": None, "source": _SOURCE_ADX_14}
    adx = float(latest)
    return {"regime": regime_label(adx), "adx": round(adx, 4), "source": _SOURCE_ADX_14}
