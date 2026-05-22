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
from typing import Any, Mapping, Sequence


def closes_from_candles(candles_df: Any) -> list[float]:
    """Extract the close series from a candles frame as a list of floats.

    Accepts a pandas DataFrame with a ``close`` column (the live path),
    a plain mapping/list-of-rows (test path), or ``None``. Any value
    that can't be coerced to a positive-or-any float is dropped — the
    vol computation tolerates gaps the same way the training family
    does (non-positive closes become ``None`` log returns).
    """
    if candles_df is None:
        return []
    # pandas DataFrame / Series-like with a "close" column.
    try:
        col = candles_df["close"]
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
    log_returns: list[float] = []
    prev: float | None = None
    for c in closes:
        if prev is not None and prev > 0 and c > 0:
            log_returns.append(math.log(c / prev))
        prev = c
    window = log_returns[-vol_window_n:]
    if len(window) < 2:
        return None
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
) -> Mapping[str, Any] | None:
    """Build the feature row a single predictor should be scored on.

    Returns:
    - ``base_row`` unchanged for non-regime predictors.
    - ``base_row`` enriched with ``vol_bucket`` + ``rolling_log_return_vol``
      for a regime predictor whose ``(symbol, timeframe)`` match the live
      signal's.
    - ``None`` (caller skips the predictor) for a regime predictor whose
      market doesn't match, or when the live vol can't be computed /
      bucketed.
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
    vol = rolling_log_return_vol(closes, window_n)
    if vol is None:
        return None
    bucket = bucket_for_vol(vol, edges, labels)
    if bucket is None:
        return None
    feature_col = str(spec.get("feature_column") or "vol_bucket")
    vol_col = str(spec.get("vol_feature_column") or "rolling_log_return_vol")
    return {**base_row, feature_col: bucket, vol_col: vol}
