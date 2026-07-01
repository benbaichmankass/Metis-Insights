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
- `funding_oi_path`  (path, default None) — optional directory holding a
  funding-rate + open-interest side-stream (`data.jsonl`, rows
  `{ts, symbol, funding_rate?, open_interest?}`, produced by
  `scripts/ml/fetch_funding_oi.py`). When given, the funding/OI feature
  columns (S-MLOPT-S11) are computed from an as-of (past-only) aligned
  window of that stream; when omitted they emit `0.0` (default-preserving —
  every non-crypto build is unchanged).
- `funding_window_n` (int, default 168) — past window (in bars) for the
  funding-rate z-score + open-interest change features. ~1 week on 1h bars.
- `microstructure_path` (path, default None) — optional directory holding a
  per-bar order-flow side-stream (`data.jsonl`, rows
  `{ts, symbol, ofi, buy_vol, sell_vol, rel_spread_mean, microprice_dev}`,
  captured forward by `scripts/ml/orderflow_capture.py`). When given, the
  order-flow feature columns (S-MLOPT-S10) are computed from an as-of (past-only)
  aligned window; when omitted they emit `0.0` (default-preserving).
- `microstructure_window_n` (int, default 50) — past window (in bars) for the
  OFI z-score + VPIN features.
- `macro_path` (path, default None) — optional directory holding a daily
  cross-asset/macro side-stream (`data.jsonl`, rows `{ts, vix_level, vix_zscore,
  vix_term_slope, dxy_zscore, dxy_return, ust10y_level, ust_slope_3m10y}`,
  produced by `scripts/ml/fetch_macro.py`). The features are **pre-computed at
  daily cadence and one-day-lagged** by the producer (see `macro_features.py`),
  so this builder only as-of carries each column forward onto the bars — no
  re-windowing. When given, the macro conditioning columns (S-MLOPT-S12, MES
  focus) are populated; when omitted they emit `0.0` (default-preserving). Meant
  for MES (a macro-driven index instrument); harmless on any other symbol.
- `cross_asset_path` (path, default None) — optional directory holding a
  peer-asset conditioning side-stream (`data.jsonl`, rows `{ts, xa_peer1_ret,
  xa_peer1_ret_lag1, …, xa_breadth_up}`, produced by
  `scripts/ml/build_cross_asset.py` from peer `market_raw`). Pre-computed at the
  TARGET's bar cadence by the producer (see `cross_asset_features.py`), so this
  builder only as-of carries each column forward onto the bars — no re-windowing.
  When given, the cross-asset conditioning columns (S-CROSS-ASSET-PROBE) are
  populated; when omitted they emit `0.0` (default-preserving). Meant for the
  "does peer-asset info predict this asset?" A/B (ETH ← BTC/SOL); harmless on any
  other symbol.
- `embedding_path` (path, default None) — optional directory holding a
  pretrained-TSFM embedding side-stream (`data.jsonl`, rows `{ts, tsfm_emb_0,
  …, tsfm_emb_N}`, produced by `scripts/ml/build_embeddings.py` from the target
  `market_raw`). Each row is a frozen Chronos-Bolt encoder embedding over the
  bar's trailing close window, mean-pooled + seeded-random-projected to a fixed
  width — **past-only** by the producer, so this builder only as-of carries each
  column forward onto the bars. When given, the TSFM embedding columns (M19
  T0.1) are populated; when omitted they emit `0.0` (default-preserving). Meant
  for the "does a learned representation lift a boosting head?" A/B; harmless on
  any symbol.

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
| `parkinson_vol` / `garman_klass_vol` / `rogers_satchell_vol` / `yang_zhang_vol` | float | Range-based vol estimators (S-MLOPT-S9) over the same past window as `rolling_log_return_vol`, emitted as a stdev. Past-only. |
| `funding_rate` | float | As-of (past-only) perp funding rate at bar `t`; `0.0` when no `funding_oi_path`. |
| `funding_rate_zscore` | float | Z-score of `funding_rate` over the past `funding_window_n` bars (the funding-extreme signal). |
| `funding_rate_abs_z` | float | `abs(funding_rate_zscore)` — extreme magnitude (the research-favoured "signal is in the extremes" reading). |
| `open_interest_change` | float | Log change of the as-of open interest over the past `funding_window_n` bars. |
| `open_interest_change_zscore` | float | Z-score of the latest OI first-difference over the window (extreme-of-change). |
| `ofi` / `ofi_zscore` | float | As-of Cont order-flow-imbalance level + its z-score over `microstructure_window_n` bars (S-MLOPT-S10); `0.0` when no `microstructure_path`. |
| `vix_level` / `vix_zscore` / `vix_term_slope` / `dxy_zscore` / `dxy_return` / `ust10y_level` / `ust_slope_3m10y` | float | As-of daily cross-asset/macro conditioning (S-MLOPT-S12, MES focus): VIX level + z + term-structure slope (VIX/VIX3M−1, >0=backwardation/stress), DXY z + short return, 10y yield level + 3m-10y slope. Pre-computed + one-day-lagged by `fetch_macro.py`; `0.0` when no `macro_path`. |
| `vpin` | float | Volume-synchronised flow toxicity over the trailing buy/sell-volume buckets. |
| `order_imbalance` | float | Per-bar taker `(buy_vol − sell_vol)/(buy_vol + sell_vol)`. |
| `rel_spread_mean` / `microprice_dev` | float | As-of mean relative spread + signed micro-price lean for the bar. |
| `forward_log_return` | float | `ln(close[t + forward_window_m] / close[t])`. |
| `forward_log_return_vol` | float | stdev of `log_return` over `[t + 1 .. t + forward_window_m]` (strictly after `t`). |
| `regime_label` | str | One of `"range"`, `"volatile"` — derived from forward stats (2-class since S-ML-REGIME-CLASSIFIER-FIX). |
| `trend_regime_label` | str | One of `"chop"`, `"transitional"`, `"trending"` — the **trend**-axis label (S-MLOPT-S15) from the Kaufman efficiency ratio of the forward window `[t+1 .. t+forward_window_m]`. The taxonomy the regime-router policy table keys on; target for the phase-4 trend-detector model. |
| `direction_label` | str | One of `"up"`, `"down"`, (`"flat"` only when `direction_threshold>0`) — the **directional** label (S-CROSS-ASSET-PROBE step 3): sign of `forward_log_return` over `[t+1 .. t+forward_window_m]`, with an optional `direction_threshold` dead-band. Forward-only (never a feature); leak-safe by the same window separation as `regime_label`. Target for the directional cross-asset A/B. |
| `source` | str | Copied from `market_raw` (the upstream adapter name). |

## Leakage discipline

Bar `t`'s features (`log_return`, `rolling_log_return_vol`,
`vol_bucket`) read ONLY bars in the inclusive window
`[t - vol_window_n + 1 .. t]`. Bar `t`'s labels
(`regime_label`, `trend_regime_label`, `forward_log_return*`) read
ONLY bars in the strictly-future window `[t + 1 .. t + forward_window_m]`.
The two windows do not overlap, so features cannot leak the label.

A trainer consuming this family MUST NOT include
`forward_log_return`, `forward_log_return_vol`, `regime_label`, or
`trend_regime_label` itself as features. The first two are forward-
looking derivatives of the labels and are exposed in the
dataset purely for analysis / sanity-checking; the last two are
the labels.

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
from ..funding_oi_features import (
    _finite_or_zero,
    change_zscore,
    extreme_magnitude,
    log_change,
    rolling_zscore,
)
from ..cross_asset_features import CROSS_ASSET_FEATURE_COLUMNS
from ..embedding_features import EMBEDDING_FEATURE_COLUMNS
from ..macro_features import MACRO_FEATURE_COLUMNS
from ..metadata import LeakageStatus
from ..labeling.trend_regime import efficiency_ratio, trend_regime_label
from ..orderflow_features import vpin as _vpin
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


def _load_funding_oi_rows(funding_oi_path: Path) -> list[dict[str, Any]]:
    """Load the optional funding/OI side-stream (`data.jsonl` in the dir).

    Rows are ``{ts, symbol, funding_rate?, open_interest?}`` produced by
    ``scripts/ml/fetch_funding_oi.py`` (Bybit V5). Tolerant of a missing dir /
    file → ``[]`` (the funding/OI feature columns then emit 0.0, exactly as when
    ``funding_oi_path`` is omitted).
    """
    data_path = Path(funding_oi_path) / "data.jsonl"
    if not data_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with data_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            rows.append(json.loads(line))
    rows.sort(key=lambda r: r.get("ts", ""))
    return rows


def _align_asof(
    bar_ts: list[str],
    series_ts: list[str],
    series_val: list[float | None],
) -> list[float | None]:
    """As-of (past-only) align ``series`` onto ``bar_ts``.

    ``out[i]`` is the most recent ``series_val`` whose ``series_ts`` is ``<=
    bar_ts[i]`` (carry-forward; ``None`` until the first observation at/before
    the bar). Both inputs must be ascending in ts. Leakage-safe: a bar never
    sees a funding/OI observation timestamped after it.
    """
    out: list[float | None] = [None] * len(bar_ts)
    j = 0
    last: float | None = None
    m = len(series_ts)
    for i, bts in enumerate(bar_ts):
        while j < m and series_ts[j] <= bts:
            if series_val[j] is not None:
                last = series_val[j]
            j += 1
        out[i] = last
    return out


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
    # yang_zhang). v4 (S-MLOPT-S11): adds the crypto funding-rate + open-interest
    # features (funding_rate / funding_rate_zscore / funding_rate_abs_z /
    # open_interest_change / open_interest_change_zscore) — populated from the
    # optional `funding_oi_path` side-stream, 0.0 when it is absent. v5
    # (S-MLOPT-S10): adds the order-flow / microstructure features (ofi /
    # ofi_zscore / vpin / order_imbalance / rel_spread_mean / microprice_dev) —
    # populated from the optional `microstructure_path` side-stream, 0.0 when it
    # is absent. v6 (S-MLOPT-S12): adds the cross-asset/macro conditioning
    # features (vix_level / vix_zscore / vix_term_slope / dxy_zscore / dxy_return
    # / ust10y_level / ust_slope_3m10y) for MES — populated from the optional
    # daily `macro_path` side-stream (pre-computed + one-day-lagged by the
    # producer), 0.0 when it is absent. v8 (S-CROSS-ASSET-PROBE): adds the
    # cross-asset / peer-asset conditioning features (xa_peer{1,2}_* + breadth)
    # for the "does peer-asset info predict this asset?" A/B (ETH ← BTC/SOL) —
    # populated from the optional `cross_asset_path` side-stream (computed at the
    # target's bar cadence, past-only), 0.0 when it is absent. v9
    # (S-CROSS-ASSET-PROBE step 3): adds the directional forward label
    # `direction_label` (sign of forward_log_return, optional dead-band) — a
    # forward-only label like regime_label, target for the directional A/B.
    # v10 (M19 T0.1): adds the pretrained-TSFM embedding features
    # (`tsfm_emb_0..N`) — populated from the optional `embedding_path`
    # side-stream (a frozen Chronos-Bolt encoder embedding, mean-pooled +
    # seeded-random-projected, past-only by the producer), 0.0 when it is
    # absent. Earlier baselines that only read `vol_bucket` /
    # `rolling_log_return_vol` are unaffected by the wider schema;
    # builder_version is metadata-only (it does not gate dataset path
    # resolution).
    builder_version: ClassVar[str] = "v10"
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
        # Crypto funding-rate + open-interest features (S-MLOPT-S11). Computed
        # over an as-of (past-only) aligned window of the optional funding/OI
        # side-stream — 0.0 on every row when no `funding_oi_path` is supplied.
        # The signal is in the EXTREMES (z-score / |z|), not the level — funding
        # is a trailing byproduct of momentum. Leakage-safe by construction.
        "funding_rate": float,
        "funding_rate_zscore": float,
        "funding_rate_abs_z": float,
        "open_interest_change": float,
        "open_interest_change_zscore": float,
        # Order-flow / microstructure features (S-MLOPT-S10). Computed over an
        # as-of (past-only) aligned window of the optional `market_microstructure`
        # side-stream (per-bar OFI / taker buy-sell volume / spread / micro-price
        # lean captured forward by scripts/ml/orderflow_capture.py) — 0.0 on every
        # row when no `microstructure_path` is supplied. Leakage-safe by
        # construction.
        "ofi": float,
        "ofi_zscore": float,
        "vpin": float,
        "order_imbalance": float,
        "rel_spread_mean": float,
        "microprice_dev": float,
        # Cross-asset / macro conditioning features (S-MLOPT-S12, MES focus).
        # As-of carried from the optional daily `macro_path` side-stream (already
        # one-day-lagged + windowed by the producer) — 0.0 on every row when no
        # `macro_path` is supplied. Leakage-safe by construction (see
        # macro_features.py § cadence + leakage discipline).
        "vix_level": float,
        "vix_zscore": float,
        "vix_term_slope": float,
        "dxy_zscore": float,
        "dxy_return": float,
        "ust10y_level": float,
        "ust_slope_3m10y": float,
        # Cross-asset / peer-asset conditioning features (S-CROSS-ASSET-PROBE).
        # As-of carried from the optional `cross_asset_path` side-stream (already
        # computed at the target's bar cadence + past-only by the producer) —
        # 0.0 on every row when no `cross_asset_path` is supplied. Leakage-safe
        # by construction (see cross_asset_features.py § cadence + leakage).
        **{col: float for col in CROSS_ASSET_FEATURE_COLUMNS},
        # Pretrained-TSFM embedding features (M19 T0.1). As-of carried from the
        # optional `embedding_path` side-stream (a frozen Chronos-Bolt encoder
        # embedding, mean-pooled + seeded-random-projected to a fixed width by
        # the producer, past-only) — 0.0 on every row when no `embedding_path`
        # is supplied. Leakage-safe by construction (see embedding_features.py
        # § cadence + leakage discipline).
        **{col: float for col in EMBEDDING_FEATURE_COLUMNS},
        "hour_of_day": int,
        "dayofweek": int,
        "log_return_lag_1": float,
        "log_return_lag_2": float,
        "forward_log_return": float,
        "forward_log_return_vol": float,
        "regime_label": str,
        "trend_regime_label": str,
        # Directional forward label (S-CROSS-ASSET-PROBE step 3): sign of
        # forward_log_return with an optional dead-band. Forward-only label —
        # never a feature; leak-safe by the same window separation as
        # regime_label.
        "direction_label": str,
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
        direction_threshold: float = 0.0,
        trend_chop_max: float = 0.30,
        trend_trend_min: float = 0.55,
        n_vol_buckets: int = 3,
        funding_oi_path: Path | str | None = None,
        funding_window_n: int = 168,
        microstructure_path: Path | str | None = None,
        microstructure_window_n: int = 50,
        macro_path: Path | str | None = None,
        cross_asset_path: Path | str | None = None,
        embedding_path: Path | str | None = None,
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
        if funding_window_n < 2:
            raise ValueError(
                f"funding_window_n must be >= 2; got {funding_window_n}"
            )
        if microstructure_window_n < 2:
            raise ValueError(
                f"microstructure_window_n must be >= 2; got {microstructure_window_n}"
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

        # Funding-rate + open-interest side-stream (S-MLOPT-S11), as-of aligned
        # onto the bars (past-only carry-forward). All-`None` (→ feature 0.0)
        # when no `funding_oi_path` is given — keeping the default build behaviour
        # and every non-crypto symbol unchanged.
        bar_ts = [str(r.get("ts", "")) for r in rows]
        funding_aligned: list[float | None] = [None] * n
        oi_aligned: list[float | None] = [None] * n
        if funding_oi_path is not None:
            fo_rows = _load_funding_oi_rows(Path(funding_oi_path))
            if fo_rows:
                fo_ts = [str(r.get("ts", "")) for r in fo_rows]
                fr_val = [
                    (float(r["funding_rate"]) if r.get("funding_rate") is not None else None)
                    for r in fo_rows
                ]
                oi_val = [
                    (float(r["open_interest"]) if r.get("open_interest") is not None else None)
                    for r in fo_rows
                ]
                funding_aligned = _align_asof(bar_ts, fo_ts, fr_val)
                oi_aligned = _align_asof(bar_ts, fo_ts, oi_val)

        # Order-flow / microstructure side-stream (S-MLOPT-S10), as-of aligned
        # onto the bars (past-only carry-forward). All-`None` (→ feature 0.0)
        # when no `microstructure_path` is given.
        ofi_aligned: list[float | None] = [None] * n
        buy_aligned: list[float | None] = [None] * n
        sell_aligned: list[float | None] = [None] * n
        spread_aligned: list[float | None] = [None] * n
        microdev_aligned: list[float | None] = [None] * n
        if microstructure_path is not None:
            ms_rows = _load_funding_oi_rows(Path(microstructure_path))
            if ms_rows:
                ms_ts = [str(r.get("ts", "")) for r in ms_rows]

                def _col(name: str) -> list[float | None]:
                    return [
                        (float(r[name]) if r.get(name) is not None else None)
                        for r in ms_rows
                    ]

                ofi_aligned = _align_asof(bar_ts, ms_ts, _col("ofi"))
                buy_aligned = _align_asof(bar_ts, ms_ts, _col("buy_vol"))
                sell_aligned = _align_asof(bar_ts, ms_ts, _col("sell_vol"))
                spread_aligned = _align_asof(bar_ts, ms_ts, _col("rel_spread_mean"))
                microdev_aligned = _align_asof(bar_ts, ms_ts, _col("microprice_dev"))

        # Cross-asset / macro side-stream (S-MLOPT-S12), as-of aligned onto the
        # bars. The producer already computed + one-day-lagged each column, so we
        # just carry each forward (no windowing here). All-`None` (→ feature 0.0)
        # when no `macro_path` is given.
        macro_aligned: dict[str, list[float | None]] = {
            col: [None] * n for col in MACRO_FEATURE_COLUMNS
        }
        if macro_path is not None:
            mac_rows = _load_funding_oi_rows(Path(macro_path))
            if mac_rows:
                mac_ts = [str(r.get("ts", "")) for r in mac_rows]
                for col in MACRO_FEATURE_COLUMNS:
                    vals = [
                        (float(r[col]) if r.get(col) is not None else None)
                        for r in mac_rows
                    ]
                    macro_aligned[col] = _align_asof(bar_ts, mac_ts, vals)

        # Cross-asset / peer-asset side-stream (S-CROSS-ASSET-PROBE), as-of
        # aligned onto the bars. The producer already computed each column at the
        # target's bar cadence (past-only), so we just carry each forward. The
        # producer keys rows at the target grid, so the as-of match is exact.
        # All-`None` (→ feature 0.0) when no `cross_asset_path` is given.
        xa_aligned: dict[str, list[float | None]] = {
            col: [None] * n for col in CROSS_ASSET_FEATURE_COLUMNS
        }
        if cross_asset_path is not None:
            xa_rows = _load_funding_oi_rows(Path(cross_asset_path))
            if xa_rows:
                xa_ts = [str(r.get("ts", "")) for r in xa_rows]
                for col in CROSS_ASSET_FEATURE_COLUMNS:
                    vals = [
                        (float(r[col]) if r.get(col) is not None else None)
                        for r in xa_rows
                    ]
                    xa_aligned[col] = _align_asof(bar_ts, xa_ts, vals)

        # Pretrained-TSFM embedding side-stream (M19 T0.1), as-of aligned onto
        # the bars. The producer emits an embedding row at (possibly strided)
        # bars, each computed from the trailing close window ending at that bar
        # (past-only); we carry each column forward so a bar sees only the most
        # recent PAST embedding. All-`None` (→ feature 0.0) when no
        # `embedding_path` is given.
        emb_aligned: dict[str, list[float | None]] = {
            col: [None] * n for col in EMBEDDING_FEATURE_COLUMNS
        }
        if embedding_path is not None:
            emb_rows = _load_funding_oi_rows(Path(embedding_path))
            if emb_rows:
                emb_ts = [str(r.get("ts", "")) for r in emb_rows]
                for col in EMBEDDING_FEATURE_COLUMNS:
                    vals = [
                        (float(r[col]) if r.get(col) is not None else None)
                        for r in emb_rows
                    ]
                    emb_aligned[col] = _align_asof(bar_ts, emb_ts, vals)

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
        # Forward trend-regime label (S-MLOPT-S15): chop/transitional/trending
        # by the Kaufman efficiency ratio of the SAME forward window the vol
        # label uses — a future-only label (never a feature), leak-safe by the
        # same window separation as `regime_label`.
        forward_trend_labels: list[str | None] = [None] * n
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
                forward_trend_labels[i] = trend_regime_label(
                    efficiency_ratio(forward_window_clean),
                    chop_max=trend_chop_max,
                    trend_min=trend_trend_min,
                )

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
            # Funding/OI feature window: the inclusive past window
            # `[i-funding_window_n+1 .. i]` of the as-of-aligned series.
            fs = max(0, i - funding_window_n + 1)
            funding_w = funding_aligned[fs : i + 1]
            oi_w = oi_aligned[fs : i + 1]
            funding_z = rolling_zscore(funding_w)
            # Order-flow / microstructure feature window (S-MLOPT-S10): the
            # inclusive past window `[i-microstructure_window_n+1 .. i]` of the
            # as-of-aligned series. `ofi_zscore` over the OFI window; `vpin` over
            # the trailing buy/sell volume buckets; `order_imbalance` per-bar.
            ms = max(0, i - microstructure_window_n + 1)
            ofi_w = ofi_aligned[ms : i + 1]
            buy_w = [v for v in buy_aligned[ms : i + 1] if v is not None]
            sell_w = [v for v in sell_aligned[ms : i + 1] if v is not None]
            bv_i = buy_aligned[i] if buy_aligned[i] is not None else 0.0
            sv_i = sell_aligned[i] if sell_aligned[i] is not None else 0.0
            order_imbalance = (
                (bv_i - sv_i) / (bv_i + sv_i) if (bv_i + sv_i) > 0 else None
            )
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
                "funding_rate": _finite_or_zero(funding_aligned[i]),
                "funding_rate_zscore": _finite_or_zero(funding_z),
                "funding_rate_abs_z": _finite_or_zero(extreme_magnitude(funding_z)),
                "open_interest_change": _finite_or_zero(log_change(oi_w)),
                "open_interest_change_zscore": _finite_or_zero(change_zscore(oi_w)),
                "ofi": _finite_or_zero(ofi_aligned[i]),
                "ofi_zscore": _finite_or_zero(rolling_zscore(ofi_w)),
                "vpin": _finite_or_zero(_vpin(buy_w, sell_w)),
                "order_imbalance": _finite_or_zero(order_imbalance),
                "rel_spread_mean": _finite_or_zero(spread_aligned[i]),
                "microprice_dev": _finite_or_zero(microdev_aligned[i]),
                "vix_level": _finite_or_zero(macro_aligned["vix_level"][i]),
                "vix_zscore": _finite_or_zero(macro_aligned["vix_zscore"][i]),
                "vix_term_slope": _finite_or_zero(macro_aligned["vix_term_slope"][i]),
                "dxy_zscore": _finite_or_zero(macro_aligned["dxy_zscore"][i]),
                "dxy_return": _finite_or_zero(macro_aligned["dxy_return"][i]),
                "ust10y_level": _finite_or_zero(macro_aligned["ust10y_level"][i]),
                "ust_slope_3m10y": _finite_or_zero(macro_aligned["ust_slope_3m10y"][i]),
                **{
                    col: _finite_or_zero(xa_aligned[col][i])
                    for col in CROSS_ASSET_FEATURE_COLUMNS
                },
                **{
                    col: _finite_or_zero(emb_aligned[col][i])
                    for col in EMBEDDING_FEATURE_COLUMNS
                },
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
                "trend_regime_label": forward_trend_labels[i] or "chop",
                "direction_label": (
                    "up" if forward_lr > direction_threshold
                    else "down" if forward_lr < -direction_threshold
                    else "flat"
                ),
                "source": str(rows[i].get("source", "")),
            }
