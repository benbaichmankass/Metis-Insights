"""Pretrained time-series-foundation-model (TSFM) quantile-FORECAST features (M19 T0.4).

The operator's framing (M19 roadmap): *"one overall model that reads everything
… slowly building capacity to understand data as much as possible."* T0.1 turned
a frozen ``amazon/chronos-bolt-tiny`` into a **feature extractor** (embeddings);
this block (T0.4) turns the SAME frozen model into a **quantile FORECASTER** — for
each bar it runs the TSFM over the trailing close window, predicts the next
``horizon`` bar's price quantiles, and derives a small, scale-free ``fc_*`` feature
block a regime / conviction head can consume alongside the hand-engineered
features. The A/B question T0.4 answers: *does an off-the-shelf probabilistic
forecast (its predicted direction / range / skew) lift a boosting head vs
hand-engineered features alone?* — for $0, no labels, no training, no in-house
forecaster.

Design docs: ``docs/research/ai-model-strategy-roadmap-2026-07-01.md`` (M19).

### Architecture — the exact sibling of ``embedding_features`` (T0.1)

**Pure functions** here compute per-bar feature rows; a thin producer script
(``scripts/ml/build_forecasts.py``) writes them to a side-stream ``data.jsonl``
that ``market_features`` as-of joins via its optional ``forecast_path`` kwarg
(``0.0`` when omitted — every existing build is unchanged). Nothing here runs on
the live money-box: T0.4 heads stay at ``candidate`` stage (offline only), so the
Chronos / torch dependency is **trainer-side only** (added to
``requirements-backtest.txt``, lazy-imported). Promotion to ``shadow`` — which
would require the same forecast computed live (train==live parity) and hence the
dep on the live VM — is a separate, operator-gated follow-up.

### Import safety + testability

This module is **stdlib-only** at import time (like ``embedding_features``), so
``import ml.datasets.forecast_features`` works in any environment — the column
contract, the neutral default, and the price→feature conversion never need torch
or numpy. The heavy TSFM call is a **lazily-imported, injectable**
``forecast_fn``: :func:`compute_forecast_feature_rows` takes a ``forecast_fn``
(default = the real Chronos forecaster, resolved lazily on first use), so the
block's windowing / stride / as-of / neutral-default logic is fully unit-testable
with a stub forecaster and **no torch installed**.

### The features — scale-free, in log-return space vs the last context close

The raw forecaster output is a per-quantile PREDICTED PRICE for the next
``horizon`` bar. Prices are level-dependent (BTC at 80k vs MES at 5k), so we
convert every feature into **log-return space relative to the last context
close** (``ln(pred_price / last_close)``) — scale-free, comparable across
instruments and price regimes. The fixed :data:`FORECAST_FEATURE_COLUMNS`:

- ``fc_ret_med``   — median predicted log-return (``ln(q50 / last_close)``);
                      the point forecast's direction + magnitude.
- ``fc_range_rel`` — ``(q90_price − q10_price) / last_close``; a forward
                      VOLATILITY proxy (the forecast's own uncertainty band).
- ``fc_up_prob``   — a directional proxy: ``1.0`` if the median predicted
                      log-return > 0 else ``0.0`` (the sign of the point forecast).
- ``fc_skew``      — ``(q90 + q10 − 2·q50) / (q90 − q10)`` computed on the
                      predicted log-returns; ``0.0`` when the denominator ≈ 0.
                      Positive = upside-skewed forecast band.
- ``fc_q10_rel``   — q10 predicted log-return (``ln(q10 / last_close)``); the
                      downside tail of the forecast.
- ``fc_q90_rel``   — q90 predicted log-return (``ln(q90 / last_close)``); the
                      upside tail of the forecast.

### Cadence + leakage discipline

The forecast at bar ``t`` is computed from the trailing close window ending at
``t`` (``closes[t-context_len+1 .. t]``) — **past-only by construction** (the
predicted next bar is never used as an input, only turned into features). The
``market_features`` forward label spans ``[t+1 .. t+forward_window_m]`` (strictly
after ``t``); the forecast features read ONLY the past window, so they never leak
the label. To bound the (otherwise per-bar) Chronos cost, the producer may emit
at a ``stride`` (every Nth bar); the existing ``_align_asof`` carry-forward in
``market_features`` fills the gaps with the most recent past forecast — still
leakage-safe (a bar never sees a future forecast). ``None`` / a short window / a
forecaster failure → ``0.0`` (neutral) at emit time, matching the funding/OI,
microstructure, macro, cross-asset, and embedding families.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Sequence

# The pretrained TSFM used as a frozen quantile forecaster. chronos-bolt-tiny is
# 9M params and runs sub-second on CPU — the same model the T0.1 embedding block
# uses, here in its `predict_quantiles` (forecaster) mode rather than `embed`.
FORECAST_MODEL_ID: str = "amazon/chronos-bolt-tiny"

# How many bars ahead to forecast. 1 = the very next bar (the cheapest, most
# directly usable horizon for a per-bar regime/conviction head).
DEFAULT_HORIZON: int = 1

# The quantile levels requested from the forecaster. (q10, q50, q90) gives a
# point forecast (q50) + a symmetric-ish band (q10..q90) for the range / skew /
# tail features. Order matters — the compute fn reads them by value, not index.
FORECAST_QUANTILES: tuple[float, ...] = (0.1, 0.5, 0.9)

# Defaults for the producer. `context_len` = trailing bars fed to the TSFM;
# `stride` = emit every Nth bar (carry-forward fills the rest, leakage-safe);
# `min_context` = fewest trailing bars before we emit (else neutral 0.0).
# Mirrors the T0.1 embedding block so the two side-streams share cadence.
DEFAULT_CONTEXT_LEN: int = 64
DEFAULT_STRIDE: int = 4
DEFAULT_MIN_CONTEXT: int = 8


# The fixed forecast feature columns this family contributes to
# `market_features`. Single source of truth shared by the builder schema, the
# side-stream producer, and the tests. Scale-free (log-return space vs the last
# context close), so comparable across instruments and price levels.
FORECAST_FEATURE_COLUMNS: tuple[str, ...] = (
    "fc_ret_med",
    "fc_range_rel",
    "fc_up_prob",
    "fc_skew",
    "fc_q10_rel",
    "fc_q90_rel",
)


def _finite_or_zero(value: float | None) -> float:
    """``None`` / non-finite → ``0.0`` (neutral) — the feature-emit shape."""
    if value is None or not math.isfinite(value):
        return 0.0
    return float(value)


# --- Pure price-quantile → feature conversion (fully stdlib, no torch) ---------

# The neutral row (every fc_* at 0.0) — a short window / forecaster failure /
# unusable last close all degrade to this, matching the sibling families.
_NEUTRAL_ROW: dict[str, float] = {col: 0.0 for col in FORECAST_FEATURE_COLUMNS}


def _log_return(pred_price: float | None, last_close: float) -> float | None:
    """``ln(pred_price / last_close)`` — ``None`` if either is non-positive/absent."""
    if pred_price is None or not math.isfinite(pred_price) or pred_price <= 0:
        return None
    if last_close <= 0 or not math.isfinite(last_close):
        return None
    return math.log(pred_price / last_close)


def quantile_forecast_features(
    price_quantiles: Mapping[float, float] | None,
    last_close: float,
    *,
    quantile_levels: Sequence[float] = FORECAST_QUANTILES,
) -> dict[str, float]:
    """Convert one bar's predicted price quantiles → the fixed ``fc_*`` features.

    ``price_quantiles`` maps each requested quantile level (e.g. ``0.1``) to the
    forecaster's PREDICTED PRICE at the target horizon. Every feature is derived
    in **log-return space relative to ``last_close``** (the last context close),
    so the block is scale-free. A missing/``None`` mapping, a non-positive last
    close, or an unusable quantile degrades the affected feature (or the whole
    row) to the neutral ``0.0``.

    ``quantile_levels`` names the (q_lo, q_mid, q_hi) triple to read (defaults to
    :data:`FORECAST_QUANTILES` = ``(0.1, 0.5, 0.9)``). The lowest is the downside
    tail, the middle the point forecast, the highest the upside tail.
    """
    if not price_quantiles or last_close <= 0 or not math.isfinite(last_close):
        return dict(_NEUTRAL_ROW)
    levels = sorted(quantile_levels)
    if len(levels) < 3:
        # Need a low / mid / high to derive range + skew + tails.
        return dict(_NEUTRAL_ROW)
    q_lo, q_mid, q_hi = levels[0], levels[len(levels) // 2], levels[-1]

    r_lo = _log_return(price_quantiles.get(q_lo), last_close)
    r_mid = _log_return(price_quantiles.get(q_mid), last_close)
    r_hi = _log_return(price_quantiles.get(q_hi), last_close)

    ret_med = _finite_or_zero(r_mid)
    q10_rel = _finite_or_zero(r_lo)
    q90_rel = _finite_or_zero(r_hi)
    # Range as a fraction of the last close (a forward volatility proxy). Derived
    # from the PRICE quantiles directly so it is a true fractional width.
    p_lo = price_quantiles.get(q_lo)
    p_hi = price_quantiles.get(q_hi)
    range_rel: float | None = None
    if (
        p_lo is not None and p_hi is not None
        and math.isfinite(p_lo) and math.isfinite(p_hi)
    ):
        range_rel = (float(p_hi) - float(p_lo)) / last_close
    # Directional sign proxy for the point forecast.
    up_prob = 1.0 if (r_mid is not None and r_mid > 0.0) else 0.0
    # Skew of the forecast band in log-return space: (q90 + q10 − 2·q50)/(q90−q10),
    # 0.0 when the denominator ≈ 0 (degenerate band).
    skew: float | None = None
    if r_lo is not None and r_mid is not None and r_hi is not None:
        denom = r_hi - r_lo
        if abs(denom) > 1e-12:
            skew = (r_hi + r_lo - 2.0 * r_mid) / denom
        else:
            skew = 0.0
    return {
        "fc_ret_med": ret_med,
        "fc_range_rel": _finite_or_zero(range_rel),
        "fc_up_prob": up_prob,
        "fc_skew": _finite_or_zero(skew),
        "fc_q10_rel": q10_rel,
        "fc_q90_rel": q90_rel,
    }


# --- The lazily-imported real forecaster (torch/chronos, trainer-side only) ----

_PIPELINE_CACHE: dict[str, Any] = {}


def forecast_available() -> bool:
    """True when the Chronos + torch deps are importable (trainer-side)."""
    try:  # pragma: no cover - depends on the optional trainer-side dep
        import importlib.util

        return (
            importlib.util.find_spec("chronos") is not None
            and importlib.util.find_spec("torch") is not None
        )
    except Exception:  # pragma: no cover - defensive
        return False


def chronos_forecast_fn(
    model_id: str = FORECAST_MODEL_ID,
) -> Callable[[Sequence[Sequence[float]], int, Sequence[float]], list[dict[float, float]]]:
    """Build the default TSFM forecaster (lazy — imports torch/chronos on first call).

    Returns a batch function ``(windows, horizon, quantile_levels) -> per-window
    dicts`` mapping each requested quantile level to the PREDICTED PRICE at the
    last (``horizon``-th) forecast step. Each window is a 1-D close series. Raises
    ``ImportError`` with an actionable message when the optional deps are absent —
    the producer script surfaces that; the dataset build without ``forecast_path``
    never calls this. The raw-price→feature conversion (log-return / range / skew)
    lives in :func:`quantile_forecast_features`, NOT here.
    """

    def _forecast(
        windows: Sequence[Sequence[float]],
        horizon: int,
        quantile_levels: Sequence[float],
    ) -> list[dict[float, float]]:
        try:  # pragma: no cover - exercised on the trainer VM, not in CI
            import torch
            from chronos import BaseChronosPipeline
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "TSFM forecasts need the optional trainer-side deps "
                "(`pip install -r requirements-backtest.txt` → chronos-forecasting + torch). "
                f"Original error: {exc}"
            ) from exc

        pipe = _PIPELINE_CACHE.get(model_id)
        if pipe is None:  # pragma: no cover
            pipe = BaseChronosPipeline.from_pretrained(
                model_id, device_map="cpu", torch_dtype=torch.float32
            )
            _PIPELINE_CACHE[model_id] = pipe

        levels = list(quantile_levels)
        contexts = [torch.tensor(list(w), dtype=torch.float32) for w in windows]
        # `predict_quantiles` returns (quantiles, mean); quantiles is
        # (batch, horizon, len(levels)). Read the LAST forecast step per window.
        # chronos-bolt's first positional arg is ``inputs`` (aka ``context``);
        # passing it as ``context=`` raises "missing 1 required positional
        # argument: 'inputs'". Pass positionally so the batch is actually fed.
        quantiles, _mean = pipe.predict_quantiles(  # pragma: no cover
            contexts,
            prediction_length=horizon,
            quantile_levels=levels,
        )
        try:  # pragma: no cover
            q = quantiles.to(torch.float32).tolist()
        except Exception as exc:  # pragma: no cover
            raise ImportError(f"unexpected forecaster output shape: {exc}") from exc

        out: list[dict[float, float]] = []
        for i in range(len(windows)):  # pragma: no cover
            last_step = q[i][-1]  # (len(levels),) at the horizon-th step
            out.append(
                {lvl: float(last_step[j]) for j, lvl in enumerate(levels)}
            )
        return out

    return _forecast


# --- The pure producer (fully testable with an injected forecast_fn) -----------

def _strided_indices(n: int, stride: int) -> list[int]:
    """Bar indices to emit a forecast at: every ``stride``-th bar + the last.

    The as-of carry-forward in ``market_features`` fills the gaps with the most
    recent PAST forecast, so a stride never introduces leakage — it only trades
    resolution for build cost.
    """
    if stride < 1:
        raise ValueError(f"stride must be >= 1; got {stride}")
    idx = list(range(0, n, stride))
    if n > 0 and idx[-1] != n - 1:
        idx.append(n - 1)
    return idx


def compute_forecast_feature_rows(
    target_rows: Sequence[Mapping[str, Any]],
    *,
    context_len: int = DEFAULT_CONTEXT_LEN,
    stride: int = DEFAULT_STRIDE,
    min_context: int = DEFAULT_MIN_CONTEXT,
    forecast_fn: Callable[
        [Sequence[Sequence[float]], int, Sequence[float]], list[dict[float, float]]
    ] | None = None,
    horizon: int = DEFAULT_HORIZON,
    quantile_levels: Sequence[float] = FORECAST_QUANTILES,
    batch_size: int = 256,
) -> list[dict[str, Any]]:
    """Per-bar TSFM quantile-forecast feature rows for a target, keyed at the bar's ts.

    ``target_rows`` are ``market_raw``-shaped (``{ts, close, ...}``). For each
    emitted (strided) bar with at least ``min_context`` trailing closes, the
    forecaster is run over the trailing ``context_len`` window ending AT that bar
    (past-only) to predict the next ``horizon`` bar's price quantiles, which are
    converted to the fixed ``fc_*`` columns in log-return space vs the last
    context close (:func:`quantile_forecast_features`). Bars with too little
    history emit no row (the as-of join leaves them at the neutral ``0.0``
    default).

    ``forecast_fn`` defaults to the real Chronos forecaster (lazy); tests inject
    a stub so the windowing / stride / conversion logic runs without torch. Any
    forecaster failure on a batch degrades that batch's rows to neutral ``0.0``
    rather than aborting the build — a missing forecast must never crash a
    dataset build. The forecast is deterministic given the model, so no seeding
    is needed.
    """
    if context_len < 1:
        raise ValueError(f"context_len must be >= 1; got {context_len}")
    if min_context < 1:
        raise ValueError(f"min_context must be >= 1; got {min_context}")
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1; got {horizon}")
    if len(quantile_levels) < 3:
        raise ValueError(
            f"quantile_levels needs >= 3 levels (low/mid/high); got {tuple(quantile_levels)}"
        )

    tgt = sorted(target_rows, key=lambda r: str(r.get("ts", "")))
    n = len(tgt)
    if n == 0:
        return []
    bar_ts = [str(r.get("ts", "")) for r in tgt]
    closes = [
        float(r["close"]) if r.get("close") is not None else None for r in tgt
    ]

    if forecast_fn is None:
        forecast_fn = chronos_forecast_fn()

    emit_idx = [i for i in _strided_indices(n, stride) if i + 1 >= min_context]

    out_rows: list[dict[str, Any]] = []
    for start in range(0, len(emit_idx), batch_size):
        batch_idx = emit_idx[start : start + batch_size]
        windows: list[list[float]] = []
        for i in batch_idx:
            lo = max(0, i - context_len + 1)
            # Drop non-positive / None closes from the window (defensive); a
            # window that collapses below min_context after cleaning is emitted
            # neutral.
            w = [c for c in closes[lo : i + 1] if c is not None and c > 0]
            windows.append(w)

        try:
            preds = forecast_fn(windows, horizon, list(quantile_levels))
        except Exception:
            preds = None

        for k, i in enumerate(batch_idx):
            row: dict[str, Any] = {"ts": bar_ts[i]}
            price_q: Mapping[float, float] | None = None
            last_close = windows[k][-1] if windows[k] else 0.0
            if (
                preds is not None
                and k < len(preds)
                and len(windows[k]) >= min_context
                and last_close > 0
            ):
                price_q = preds[k]
            feats = quantile_forecast_features(
                price_q, last_close, quantile_levels=quantile_levels
            )
            row.update(feats)
            out_rows.append(row)
    return out_rows
