"""Regime-model shadow enrichment (2026-05-22 wiring fix).

The regime classifier models (`{btc,mes}-regime-{5m,15m}`) key their
prediction on `vol_bucket` — a quantile bucket of the rolling
log-return volatility frozen at training time. The strategies'
signal-time feature row carries trade context (`setup_type`,
`killzone`, `confidence`, …) but no `vol_bucket`, so before this
module every regime predictor saw an absent feature, fell back to its
training marginal distribution, and emitted the SAME constant score on
every tick — the wiring gap found 2026-05-22 (all four regime models
logging a fixed score forever, so their shadow predictions carried no
information).

This module closes that gap on the live trader's observe-only shadow
path. It computes the live `vol_bucket` from the strategy's own candles
using the bucket edges + window the matching regime model froze into its
`model_state` at fit time (see `RegimeClassifierTrainer`), and only for
the regime model whose `(symbol, timeframe)` match the strategy's — a
BTC-5m model must not be scored against MES-15m candles, whose vol lives
on a completely different scale.

Gating, per predictor:
- **Not a regime model** (`regime_spec is None`) → unchanged: scored on
  the base trade-signal row. Covers journal/setup-quality shadow models.
- **Regime model, `(symbol, timeframe)` match** → scored on the base row
  enriched with the live `vol_bucket` + `rolling_log_return_vol`.
- **Regime model, mismatch** (or live vol uncomputable) → skipped, so
  the audit log isn't polluted with a meaningless cross-market score.

Pure + dependency-light (stdlib only; candles are read through a tiny
duck-typed accessor) so it is unit-testable without pandas plumbing.
`feature_row_for_predictor` is the integration entry point used by
`src.runtime.strategy_signal_builders`.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from ml.datasets.volatility_estimators import (
    _sqrt_or_zero,
    garman_klass_var,
    parkinson_var,
    rogers_satchell_var,
    yang_zhang_var,
)


def closes_from_candles(candles_df: Any) -> list[float]:
    """Extract the close series from a candles frame as a list of floats.

    Accepts a pandas DataFrame with a ``close`` column (the live path),
    a plain mapping/list-of-rows (test path), or ``None``. Any value
    that can't be coerced to a positive-or-any float is dropped — the
    vol computation tolerates gaps the same way the training family
    does (non-positive closes become ``None`` log returns).
    """
    return _column_floats(candles_df, "close")


def _column_floats(candles_df: Any, column: str) -> list[float]:
    """Extract one OHLC column from a candles frame as a list of floats.

    Shared by ``closes_from_candles`` and ``ohlc_from_candles`` — duck-typed
    like the rest of this module so it tolerates a pandas DataFrame (live
    path), a list-of-dict rows (test path), or ``None``. Non-coercible cells
    are dropped (the same gap tolerance the training family applies).
    """
    if candles_df is None:
        return []
    try:
        col = candles_df[column]
    except Exception:  # noqa: BLE001 — not subscriptable / no column
        return []
    try:
        values = col.tolist()  # pandas Series / numpy array
    except AttributeError:
        values = list(col)
    out: list[float] = []
    for v in values:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def ohlc_from_candles(candles_df: Any) -> tuple[list[float], list[float], list[float], list[float]]:
    """Extract aligned (opens, highs, lows, closes) from a candles frame.

    Returns four equal-length lists, truncated to the shortest, so a frame
    with a partial column never mis-aligns the range-vol estimators. Empty
    tuples when any column is missing / the frame is unusable — the caller
    then falls back to the close-only path.
    """
    opens = _column_floats(candles_df, "open")
    highs = _column_floats(candles_df, "high")
    lows = _column_floats(candles_df, "low")
    closes = _column_floats(candles_df, "close")
    m = min(len(opens), len(highs), len(lows), len(closes))
    if m == 0:
        return [], [], [], []
    # Truncate from the FRONT so the most-recent bars (the window we score
    # on) stay aligned even if one column has a stray leading/trailing gap.
    return opens[-m:], highs[-m:], lows[-m:], closes[-m:]


def rolling_log_return_vol(
    closes: Sequence[float], vol_window_n: int
) -> float | None:
    """Population stdev of the last ``vol_window_n`` log returns.

    Mirrors `ml.datasets.families.market_features` past-vol semantics:
    the volatility "as of" the most recent bar is the population stdev
    (`statistics.pstdev`) of the log returns over the trailing window
    ending at that bar. Non-positive closes are skipped (their log
    return is undefined), matching the family's ``None`` handling.

    Returns ``None`` when fewer than two usable log returns exist.
    """
    if vol_window_n < 2:
        return None
    # Only the LAST ``vol_window_n`` valid log returns affect the result, so
    # scan close-pairs from the END and stop once we have them — O(window)
    # instead of O(len(closes)). Rebuilding the full log-return list every bar
    # made a long backtest O(n^2); a 60k-bar sweep paid ~6 min here alone.
    # Byte-identical to the previous "compute all, take last N" form, INCLUDING
    # the non-positive-close skip (we keep scanning backwards past a skipped
    # pair), because pstdev is order-independent so the recovered set of the N
    # highest-index valid pairs is the same set the old slice kept.
    window: list[float] = []
    i = len(closes) - 1
    while i >= 1 and len(window) < vol_window_n:
        c = closes[i]
        prev = closes[i - 1]
        if prev > 0 and c > 0:
            window.append(math.log(c / prev))
        i -= 1
    if len(window) < 2:
        return None
    # We collected newest-first; reverse to chronological order so pstdev sums
    # the exact same sequence the old "log_returns[-n:]" form did. Float
    # addition isn't associative, so this reversal is what makes the result
    # BYTE-identical (not just numerically close) to the original.
    window.reverse()
    return statistics.pstdev(window)


def bucket_for_vol(
    value: float, edges: Sequence[float], labels: Sequence[str]
) -> str | None:
    """Map a vol ``value`` to its bucket label given frozen ``edges``.

    Mirror of ``market_features._bucket_for``: a value lands in bucket
    ``i`` where ``i`` is the first index with ``value <= edges[i]``,
    saturating to the last label. Returns ``None`` when ``labels`` is
    empty (a degenerate model that can't bucket).
    """
    if not labels:
        return None
    for i, cut in enumerate(edges):
        if i >= len(labels) - 1:
            break
        if value <= cut:
            return labels[i]
    return labels[-1]


# ---------------------------------------------------------------------------
# Train/serve feature parity (S-MLOPT-S17 / MB-20260604-005)
# ---------------------------------------------------------------------------
#
# The regime heads train on the ``market_features`` row — not just
# ``{vol_bucket, rolling_log_return_vol}`` but the four range-vol estimators
# (parkinson/garman_klass/rogers_satchell/yang_zhang), the log-return + its two
# lags, and the bar's hour/day-of-week. The helpers below recompute that exact
# set from the live trailing OHLC window so the served feature vector matches
# the trained one (and, for the yz heads, so ``vol_bucket`` is bucketed against
# the SAME estimator the trainer froze its edges on). Parity oracle:
# ``ml/datasets/families/market_features.py::MarketFeaturesBuilder.iter_rows``.

# market_features column names for the four range-vol estimators (the emitted
# stdev, i.e. sqrt of the estimator variance).
RANGE_VOL_COLUMNS: tuple[str, ...] = (
    "parkinson_vol",
    "garman_klass_vol",
    "rogers_satchell_vol",
    "yang_zhang_vol",
)


def log_return_series(closes: Sequence[float]) -> list[Optional[float]]:
    """``ln(close[i] / close[i-1])`` per bar, ``None`` for the first bar and any
    non-positive-price pair — byte-identical to the builder's ``log_returns``.
    """
    out: list[Optional[float]] = [None] * len(closes)
    for i in range(1, len(closes)):
        prev_c = closes[i - 1]
        curr_c = closes[i]
        if prev_c > 0 and curr_c > 0:
            out[i] = math.log(curr_c / prev_c)
    return out


def range_vol_estimators(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    vol_window_n: int,
) -> dict[str, float]:
    """The four range-vol estimators over the trailing ``vol_window_n`` bars.

    Mirrors the builder's per-bar window ``[s .. i]`` (``s = i - vol_window_n
    + 1``, here the last bar ``i = len-1``): each estimator reads the bar OHLC
    of that window, and Yang-Zhang's overnight term reads each window bar's
    prior close. Emitted as the stdev (``_sqrt_or_zero`` of the variance) under
    the ``market_features`` column names. Pure; never raises.
    """
    n = len(closes)
    s = max(0, n - vol_window_n)
    w_open = list(opens[s:n])
    w_high = list(highs[s:n])
    w_low = list(lows[s:n])
    w_close = list(closes[s:n])
    # prev_close for window bar j is closes[j-1] (None before the first bar) —
    # exactly the builder's ``w_prev_close``.
    w_prev_close: list[Optional[float]] = [
        closes[j - 1] if j - 1 >= 0 else None for j in range(s, n)
    ]
    return {
        "parkinson_vol": _sqrt_or_zero(parkinson_var(w_high, w_low)),
        "garman_klass_vol": _sqrt_or_zero(
            garman_klass_var(w_open, w_high, w_low, w_close)
        ),
        "rogers_satchell_vol": _sqrt_or_zero(
            rogers_satchell_var(w_open, w_high, w_low, w_close)
        ),
        "yang_zhang_vol": _sqrt_or_zero(
            yang_zhang_var(w_open, w_high, w_low, w_close, w_prev_close)
        ),
    }


def _bar_hour_dow(ts_value: Any) -> tuple[int, int]:
    """Map a candle's ``timestamp`` cell to ``(hour_of_day, dayofweek)`` in UTC.

    Tolerates the formats the live connectors emit: epoch milliseconds /
    seconds (Bybit/CCXT, int or float), a pandas/py ``datetime`` (IBKR), or an
    ISO-8601 string. Returns ``(0, 0)`` for anything unparseable — the same
    conservative default the builder's ``_parse_ts_hour_dow`` uses, so one bad
    cell never breaks scoring.
    """
    if ts_value is None:
        return 0, 0
    # datetime / pandas Timestamp (has hour + weekday()).
    if hasattr(ts_value, "hour") and hasattr(ts_value, "weekday"):
        try:
            dt = ts_value
            if getattr(dt, "tzinfo", None) is not None:
                dt = dt.astimezone(timezone.utc)
            return int(dt.hour), int(dt.weekday())
        except Exception:  # noqa: BLE001 — fall through to other parses
            pass
    # Numeric epoch (ms if large, else seconds).
    try:
        epoch = float(ts_value)
        if epoch > 1e11:  # milliseconds, not seconds
            epoch /= 1000.0
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        return dt.hour, dt.weekday()
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    # ISO-8601 string (trailing-Z tolerant).
    s = str(ts_value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.hour, dt.weekday()
    except ValueError:
        return 0, 0


def _latest_bar_hour_dow(candles_df: Any) -> tuple[int, int]:
    """``(hour_of_day, dayofweek)`` of the most recent bar's ``timestamp``."""
    if candles_df is None:
        return 0, 0
    try:
        col = candles_df["timestamp"]
    except Exception:  # noqa: BLE001 — no timestamp column
        return 0, 0
    try:
        values = col.tolist()
    except AttributeError:
        try:
            values = list(col)
        except TypeError:
            return 0, 0
    if not values:
        return 0, 0
    return _bar_hour_dow(values[-1])


def build_parity_feature_row(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    vol_window_n: int,
    *,
    rolling_vol: float,
    hour_of_day: int,
    dayofweek: int,
) -> dict[str, Any]:
    """Assemble the live regime feature superset for the LATEST bar.

    The keys match the ``market_features`` schema the heads train on:
    ``rolling_log_return_vol`` + the four range-vol estimators +
    ``log_return`` + ``log_return_lag_1/2`` + ``hour_of_day`` + ``dayofweek``.
    ``rolling_vol`` is passed in (already computed + parity-checked None by the
    caller). Side-stream features (funding/microstructure/macro) are NOT
    emitted — no live regime head selects them, and a no-side-stream training
    build emits them as ``0.0`` anyway. Pure; never raises.
    """
    lrs = log_return_series(closes)
    row: dict[str, Any] = {
        "rolling_log_return_vol": rolling_vol,
        "log_return": lrs[-1] if lrs and lrs[-1] is not None else 0.0,
        "log_return_lag_1": lrs[-2] if len(lrs) >= 2 and lrs[-2] is not None else 0.0,
        "log_return_lag_2": lrs[-3] if len(lrs) >= 3 and lrs[-3] is not None else 0.0,
        "hour_of_day": int(hour_of_day),
        "dayofweek": int(dayofweek),
    }
    row.update(range_vol_estimators(opens, highs, lows, closes, vol_window_n))
    return row


def regime_spec_of(predictor: Any) -> Mapping[str, Any] | None:
    """Read a predictor's regime spec, defensively.

    The live path holds `ShadowPredictor` wrappers; the regime metadata
    lives on the wrapped base predictor as ``regime_spec``. Returns
    ``None`` for any predictor that isn't a regime model (journal /
    setup-quality models, or regime models trained before edges were
    frozen into state).
    """
    wrapped = getattr(predictor, "wrapped", None)
    spec = getattr(wrapped, "regime_spec", None)
    if spec is None:
        # The predictor might itself be the base (no ShadowPredictor wrap).
        spec = getattr(predictor, "regime_spec", None)
    return spec if isinstance(spec, Mapping) else None


def _norm(s: Any) -> str:
    return str(s or "").strip().upper()


def feature_row_for_predictor(
    predictor: Any,
    base_row: Mapping[str, Any],
    *,
    closes: Sequence[float],
    symbol: str,
    timeframe: str,
    candles_df: Any = None,
    cross_asset_row: Mapping[str, Any] | None = None,
    forecast_row: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    """Build the feature row a single predictor should be scored on.

    Returns:
    - ``base_row`` unchanged for non-regime predictors.
    - ``base_row`` enriched with the FULL ``market_features`` superset the head
      trained on for a regime predictor whose ``(symbol, timeframe)`` match —
      ``vol_bucket`` + ``rolling_log_return_vol`` + the four range-vol
      estimators + ``log_return`` + ``log_return_lag_1/2`` +
      ``hour_of_day``/``dayofweek`` (S-MLOPT-S17 train/serve parity,
      ``MB-20260604-005``). The ``vol_bucket`` is bucketed against the value of
      the estimator named by the head's frozen ``vol_feature_column`` (so the
      yz heads bucket ``yang_zhang_vol``, not close-to-close vol).
    - ``None`` (caller skips the predictor) for a regime predictor whose
      market doesn't match, or when the live vol can't be computed / bucketed.

    ``candles_df`` carries the OHLC + timestamps the parity row needs; the live
    callers always pass it. When it is absent / lacks OHLC (older callers,
    tests), the row degrades to the pre-S17 close-only shape
    (``{vol_bucket, vol_feature_column: rolling_vol}``) so behaviour is
    backwards-compatible.
    """
    spec = regime_spec_of(predictor)
    if spec is None:
        return base_row  # non-regime model: unchanged behaviour

    if _norm(spec.get("symbol")) != _norm(symbol):
        return None
    if _norm(spec.get("timeframe")) != _norm(timeframe):
        return None

    labels = list(spec.get("vol_bucket_labels") or [])
    edges = [float(e) for e in (spec.get("vol_bucket_edges") or [])]
    window_n = int(spec.get("vol_window_n") or 20)
    if not labels:
        return None
    feature_col = str(spec.get("feature_column") or "vol_bucket")
    vol_col = str(spec.get("vol_feature_column") or "rolling_log_return_vol")

    # Skip-condition parity: the builder drops any bar without a full past
    # window, i.e. ``rolling_log_return_vol`` (its ``past_vol``) is None.
    rolling_vol = rolling_log_return_vol(closes, window_n)
    if rolling_vol is None:
        return None

    # Parity path — full ``market_features`` superset from the live OHLC window.
    opens, highs, lows, ohlc_closes = ohlc_from_candles(candles_df)
    if ohlc_closes:
        hour_of_day, dayofweek = _latest_bar_hour_dow(candles_df)
        parity = build_parity_feature_row(
            opens, highs, lows, ohlc_closes, window_n,
            rolling_vol=rolling_vol, hour_of_day=hour_of_day, dayofweek=dayofweek,
        )
        # Bucket against the estimator the trainer froze its edges on. For the
        # v2 heads that is ``rolling_log_return_vol``; for the yz heads it is
        # ``yang_zhang_vol`` — using the close-to-close value there (the
        # pre-S17 bug) bucketed against yz-unit edges and mis-placed the bucket.
        vol_value = parity.get(vol_col, rolling_vol)
        bucket = bucket_for_vol(float(vol_value), edges, labels)
        if bucket is None:
            return None
        # Cross-asset peer-feature block (S-CROSS-ASSET-PROBE D2a). Merged for
        # the cross-asset regime head; non-cross-asset heads project only their
        # own feature columns, so the extra ``xa_*`` keys are inert for them.
        xa = dict(cross_asset_row) if cross_asset_row else {}
        # Live TSFM quantile-forecast block (M19 Track-1 PR 1b). Merged the same
        # way as the ``xa`` block, so the ``fc_*`` columns land on the row
        # exactly like the offline ``market_features`` forecast join. Inert for
        # non-forecast heads (they project only their own feature columns, so the
        # extra ``fc_*`` keys are ignored). ``None`` → the head's fc_* columns
        # stay missing (NaN) — the honest degraded state.
        fc = dict(forecast_row) if forecast_row else {}
        return {**base_row, **parity, **xa, **fc, feature_col: bucket}

    # Legacy close-only fallback (no OHLC available): pre-S17 behaviour.
    bucket = bucket_for_vol(rolling_vol, edges, labels)
    if bucket is None:
        return None
    return {**base_row, feature_col: bucket, vol_col: rolling_vol}
