"""`market_features` dataset family (S-AI-WS5-B-PART-2 PR 2B).

Derives engineered features + a 3-class regime label from a
canonical `market_raw` dataset. Operator picked "new family"
over "extend market_raw" so that `market_raw` stays canonical
OHLCV-only and `market_features` carries the leakage discipline
+ engineered columns separately (matches the WS5-B-PART-1
architectural principle that `market_raw` carries no labels).

## Inputs

The builder reads rows from an existing `market_raw` dataset.
Pass `market_raw_path=<path-to-market_raw-dataset-dir>` (the
directory holding `data.jsonl` + `metadata.json`) via
`iter_rows_kwargs`.

Knobs (all kwargs):
- `vol_window_n`     (int, default 20) — past window for the rolling
  log-return vol feature. Larger = smoother feature.
- `forward_window_m` (int, default 5) — forward window for the
  regime label. Larger = more "regime"-flavored labels.
- `vol_threshold`    (float, default 0.003) — forward-window vol
  cutoff above which the regime is classified as "volatile".
  Calibrated to ≈p50 of forward_vol on BTCUSDT 1h so the two
  classes are balanced and vol_autocorrelation makes each bucket
  non-trivially predictable (vol_b0→range, vol_b1/b2→volatile).
- `trend_threshold`  (float, default 0.005) — abs forward-window
  log return above which the (non-volatile) regime is classified
  as "trend".
- `n_vol_buckets`    (int, default 3) — feature buckets for the
  rolling vol (quantile-based). Bucket labels: `vol_b0`..`vol_bK-1`,
  where `vol_b0` is lowest. Bucket count is configurable so a
  follow-up can experiment with finer discretisation.

## Schema

| Field | Type | Notes |
|---|---|---|
| `ts` | str | Bar timestamp (copied from `market_raw`). |
| `symbol` | str | Copied from `market_raw`. |
| `timeframe` | str | Copied from `market_raw`. |
| `log_return` | float | `ln(close[t] / close[t-1])`. |
| `rolling_log_return_vol` | float | stdev of `log_return` over the window `[t - vol_window_n + 1 .. t]` (inclusive of bar `t`). |
| `vol_bucket` | str | Quantile bucket of `rolling_log_return_vol` over the entire dataset. |
| `hour_of_day` | int | UTC hour of `ts`, 0..23. Non-leaking (known at bar close). |
| `dayofweek` | int | UTC day-of-week of `ts`, 0=Monday..6=Sunday. Non-leaking. |
| `log_return_lag_1` | float | `log_return[t-1]`. Past-only — non-leaking. `NaN` represented as 0.0 for the earliest bar where the lag is undefined (handled like other early-window incomplete rows). |
| `log_return_lag_2` | float | `log_return[t-2]`. Same handling. |
| `forward_log_return` | float | `ln(close[t + forward_window_m] / close[t])`. |
| `forward_log_return_vol` | float | stdev of `log_return` over `[t + 1 .. t + forward_window_m]` (strictly after `t`). |
| `regime_label` | str | One of `"range"`, `"volatile"` — derived from forward stats (2-class since S-ML-REGIME-CLASSIFIER-FIX). |
| `source` | str | Copied from `market_raw` (the upstream adapter name). |

## Leakage discipline

Bar `t`'s features (`log_return`, `rolling_log_return_vol`,
`vol_bucket`) read ONLY bars in the inclusive window
`[t - vol_window_n + 1 .. t]`. Bar `t`'s label
(`regime_label`, `forward_log_return*`) reads ONLY bars in the
strictly-future window `[t + 1 .. t + forward_window_m]`. The
two windows do not overlap, so features cannot leak the label.

A trainer consuming this family MUST NOT include
`forward_log_return`, `forward_log_return_vol`, or
`regime_label` itself as features. The first two are forward-
looking derivatives of the label and are exposed in the
dataset purely for analysis / sanity-checking; the third is
the label.

`vol_bucket` thresholds are quantile-derived from the full
dataset (train + eval combined) at build time. For research-only
baselines this is acceptable.

Live-scoring freeze (2026-05-22): `RegimeClassifierTrainer` now
reconstructs the bucket edges from the train rows it is fit on
(the largest raw `rolling_log_return_vol` in each bucket is that
bucket's upper cut point) and freezes them into `model_state`, so
the live shadow path (`src/runtime/regime_shadow.py`) can bucket a
tick's rolling vol against the SAME edges the model trained on.
This is the train-split-only freeze the follow-up below called
for — but only for the live path. The eval split is still bucketed
against the build-time full-dataset quantiles, so the train-split
eval-leakage cleanup remains a follow-up.

Metadata stamps `leakage_test_status: passed` because the
window separation is guaranteed by construction (the forward
window starts strictly after `t`).
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

from ..builder import DatasetBuilder
from ..metadata import LeakageStatus
from ..volatility_estimators import (
    _sqrt_or_zero,
    garman_klass_var,
    parkinson_var,
    rogers_satchell_var,
    yang_zhang_var,
)

_FAMILY = "market_features"

REGIME_LABELS: tuple[str, ...] = ("range", "volatile")


def _label_regime(
    forward_log_return: float,
    forward_vol: float,
    *,
    trend_threshold: float,
    vol_threshold: float,
) -> str:
    """Map (forward_log_return, forward_vol) → regime class.

    2-class scheme (S-ML-REGIME-CLASSIFIER-FIX, 2026-05-20):
      1. forward_vol > vol_threshold → "volatile"
      2. else → "range"

    The original 3-class scheme (trend / range / volatile) was
    collapsed because ``vol_bucket`` (the sole feature) cannot
    separate "trend" from "range" — in every bucket, "trend" is
    outnumbered by "range" or "volatile", so the modal-class
    predictor never predicted "trend" → f1_trend = 0.0 in all
    training runs since 2026-05-14.

    ``trend_threshold`` is kept in the signature for backward
    compatibility but is no longer used.
    """
    if forward_vol > vol_threshold:
        return "volatile"
    return "range"


def _bucket_label(idx: int, n_buckets: int) -> str:
    return f"vol_b{idx}"


def _quantile_buckets(
    values: list[float], n_buckets: int
) -> tuple[list[float], list[str]]:
    """Compute bucket boundaries + labels.

    Returns (boundaries, labels) where boundaries is a sorted list
    of `n_buckets - 1` cut points. A value `v` lands in bucket
    `i` where `i = first index such that v <= boundaries[i]`,
    saturating to the highest bucket. Labels are
    `vol_b0`..`vol_b{n_buckets-1}`.
    """
    if n_buckets < 2:
        raise ValueError(f"n_vol_buckets must be >= 2; got {n_buckets}")
    if not values:
        return [], [_bucket_label(i, n_buckets) for i in range(n_buckets)]
    sorted_vals = sorted(values)
    boundaries: list[float] = []
    n = len(sorted_vals)
    for i in range(1, n_buckets):
        # Quantile cut at i / n_buckets.
        idx = max(0, min(n - 1, int(round(i * n / n_buckets)) - 1))
        boundaries.append(sorted_vals[idx])
    labels = [_bucket_label(i, n_buckets) for i in range(n_buckets)]
    return boundaries, labels


def _bucket_for(value: float, boundaries: list[float], labels: list[str]) -> str:
    for i, cut in enumerate(boundaries):
        if value <= cut:
            return labels[i]
    return labels[-1]


def _parse_ts_hour_dow(ts_str: str) -> tuple[int, int]:
    """Parse a bar's ISO-8601 ``ts`` into ``(hour_of_day, dayofweek)``.

    Tolerant of the trailing-``Z`` form (``2026-01-01T12:00:00Z``) +
    explicit offsets. Returns ``(0, 0)`` for an unparseable ts so a single
    malformed row doesn't blow up the cycle — the bar still emits with
    defaulted time features and the trainer's leakage gate (and the row
    count delta) is unaffected.
    """
    if not ts_str:
        return 0, 0
    s = ts_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 0, 0
    return dt.hour, dt.weekday()


def _load_market_raw_rows(market_raw_path: Path) -> list[dict[str, Any]]:
    data_path = market_raw_path / "data.jsonl"
    if not data_path.is_file():
        raise FileNotFoundError(
            f"market_raw data.jsonl not found at {data_path}; "
            "build a market_raw dataset first via "
            "`python -m ml.datasets build market_raw ...`"
        )
    rows: list[dict[str, Any]] = []
    with data_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


class MarketFeaturesBuilder(DatasetBuilder):
    family: ClassVar[str] = _FAMILY
    # v2: adds hour_of_day, dayofweek, log_return_lag_{1,2} (Phase-2 feature
    # expansion for the v2 LightGBM regime heads). v3 (S-MLOPT-S9): adds the
    # range-based vol estimators (parkinson/garman_klass/rogers_satchell/
    # yang_zhang). Earlier baselines that only read `vol_bucket` /
    # `rolling_log_return_vol` are unaffected by the wider schema; builder_version
    # is metadata-only (it does not gate dataset path resolution).
    builder_version: ClassVar[str] = "v3"
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.PASSED
    label_version: ClassVar[str] = "regime-3class-v1"
    schema: ClassVar[Mapping[str, type]] = {
        "ts": str,
        "symbol": str,
        "timeframe": str,
        "log_return": float,
        "rolling_log_return_vol": float,
        "vol_bucket": str,
        # Range-based vol estimators over the SAME past window as
        # `rolling_log_return_vol` (S-MLOPT-S9). Each uses the bar OHLC (not just
        # close-to-close), so it estimates vol more efficiently — the regime
        # heads can select whichever separates `volatile` best. Emitted as a
        # stdev (sqrt of the estimator variance) for comparability. Past-only →
        # leakage-safe by construction.
        "parkinson_vol": float,
        "garman_klass_vol": float,
        "rogers_satchell_vol": float,
        "yang_zhang_vol": float,
        "hour_of_day": int,
        "dayofweek": int,
        "log_return_lag_1": float,
        "log_return_lag_2": float,
        "forward_log_return": float,
        "forward_log_return_vol": float,
        "regime_label": str,
        "source": str,
    }

    def iter_rows(
        self,
        *,
        market_raw_path: Path | str,
        vol_window_n: int = 20,
        forward_window_m: int = 5,
        vol_threshold: float = 0.003,
        trend_threshold: float = 0.005,
        n_vol_buckets: int = 3,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        if vol_window_n < 2:
            raise ValueError(f"vol_window_n must be >= 2; got {vol_window_n}")
        if forward_window_m < 2:
            raise ValueError(
                f"forward_window_m must be >= 2; got {forward_window_m}"
            )
        if vol_threshold < 0:
            raise ValueError(f"vol_threshold must be >= 0; got {vol_threshold}")
        if trend_threshold < 0:
            raise ValueError(
                f"trend_threshold must be >= 0; got {trend_threshold}"
            )

        rows = _load_market_raw_rows(Path(market_raw_path))
        rows.sort(key=lambda r: r.get("ts", ""))
        n = len(rows)
        if n < vol_window_n + forward_window_m + 1:
            # Need at least one bar with a full past + forward window.
            return

        # OHLC arrays for the range-based vol estimators (S-MLOPT-S9).
        opens = [float(r.get("open", 0.0) or 0.0) for r in rows]
        highs = [float(r.get("high", 0.0) or 0.0) for r in rows]
        lows = [float(r.get("low", 0.0) or 0.0) for r in rows]
        closes = [float(r.get("close", 0.0) or 0.0) for r in rows]

        log_returns: list[float | None] = [None] * n
        for i in range(1, n):
            prev_close = float(rows[i - 1]["close"])
            curr_close = float(rows[i]["close"])
            if prev_close <= 0 or curr_close <= 0:
                log_returns[i] = None
                continue
            log_returns[i] = math.log(curr_close / prev_close)

        # First pass: compute past + forward stats per bar so we can
        # quantile-bucket the past vol consistently across the file.
        past_vols: list[float | None] = [None] * n
        forward_log_returns: list[float | None] = [None] * n
        forward_vols: list[float | None] = [None] * n
        for i in range(n):
            past_window = log_returns[max(0, i - vol_window_n + 1) : i + 1]
            past_window_clean = [v for v in past_window if v is not None]
            if len(past_window_clean) >= vol_window_n - 1:
                # `vol_window_n - 1` because log_return at i=0 is None.
                # Tolerate that single missing value when computing the
                # earliest valid past-vol bar.
                if len(past_window_clean) >= 2:
                    past_vols[i] = statistics.pstdev(past_window_clean)

            forward_idx = i + forward_window_m
            if forward_idx >= n:
                continue
            close_t = float(rows[i]["close"])
            close_fwd = float(rows[forward_idx]["close"])
            if close_t <= 0 or close_fwd <= 0:
                continue
            forward_log_returns[i] = math.log(close_fwd / close_t)
            forward_window = log_returns[i + 1 : forward_idx + 1]
            forward_window_clean = [v for v in forward_window if v is not None]
            if len(forward_window_clean) >= 2:
                forward_vols[i] = statistics.pstdev(forward_window_clean)

        # Quantile-bucket past vol across all complete entries.
        complete_past_vols = [v for v in past_vols if v is not None]
        boundaries, bucket_labels = _quantile_buckets(
            complete_past_vols, n_vol_buckets
        )

        for i in range(n):
            past_vol = past_vols[i]
            forward_lr = forward_log_returns[i]
            forward_vol = forward_vols[i]
            log_ret = log_returns[i]
            if (
                past_vol is None
                or forward_lr is None
                or forward_vol is None
                or log_ret is None
            ):
                # Skip incomplete rows. The first vol_window_n-1 bars
                # lack a full past window; the last forward_window_m
                # bars lack a full forward window.
                continue
            ts_str = str(rows[i]["ts"])
            hour_of_day, dayofweek = _parse_ts_hour_dow(ts_str)
            # Lag log returns: NaN-safe (0.0 when the bar predates the
            # earliest valid lag). Same conservative handling as the
            # earliest past-vol bars — feature is informational only when
            # the lag exists.
            lag_1 = log_returns[i - 1] if i - 1 >= 0 else None
            lag_2 = log_returns[i - 2] if i - 2 >= 0 else None
            # Range-based vol estimators over the SAME inclusive past window as
            # `rolling_log_return_vol` (`[s .. i]`) — past-only, leakage-safe.
            # YZ's overnight term needs each window bar's prior close.
            s = max(0, i - vol_window_n + 1)
            w_open, w_high, w_low, w_close = (
                opens[s : i + 1], highs[s : i + 1],
                lows[s : i + 1], closes[s : i + 1],
            )
            w_prev_close = [closes[j - 1] if j - 1 >= 0 else None
                            for j in range(s, i + 1)]
            yield {
                "ts": ts_str,
                "symbol": str(rows[i].get("symbol", "")),
                "timeframe": str(rows[i].get("timeframe", "")),
                "log_return": float(log_ret),
                "rolling_log_return_vol": float(past_vol),
                "vol_bucket": _bucket_for(past_vol, boundaries, bucket_labels),
                "parkinson_vol": _sqrt_or_zero(parkinson_var(w_high, w_low)),
                "garman_klass_vol": _sqrt_or_zero(
                    garman_klass_var(w_open, w_high, w_low, w_close)),
                "rogers_satchell_vol": _sqrt_or_zero(
                    rogers_satchell_var(w_open, w_high, w_low, w_close)),
                "yang_zhang_vol": _sqrt_or_zero(
                    yang_zhang_var(w_open, w_high, w_low, w_close, w_prev_close)),
                "hour_of_day": int(hour_of_day),
                "dayofweek": int(dayofweek),
                "log_return_lag_1": float(lag_1) if lag_1 is not None else 0.0,
                "log_return_lag_2": float(lag_2) if lag_2 is not None else 0.0,
                "forward_log_return": float(forward_lr),
                "forward_log_return_vol": float(forward_vol),
                "regime_label": _label_regime(
                    forward_lr,
                    forward_vol,
                    trend_threshold=trend_threshold,
                    vol_threshold=vol_threshold,
                ),
                "source": str(rows[i].get("source", "")),
            }
