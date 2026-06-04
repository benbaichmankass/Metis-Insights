"""Train/serve feature parity for the regime shadow path (S-MLOPT-S17 /
``MB-20260604-005``).

The live regime feature row (``feature_row_for_predictor``) must reproduce the
``market_features`` row the head trained on. These tests use the **builder
itself** (``MarketFeaturesBuilder``) as the parity oracle: stage a synthetic
``market_raw`` dataset, run the builder, then assert the live row computed from
the same raw OHLC (up to the same bar) equals the builder's row.

Two claims:
  1. Continuous-feature parity — ``rolling_log_return_vol`` + the four range-vol
     estimators + ``log_return`` + its two lags + ``hour_of_day``/``dayofweek``
     match the builder bit-for-bit (within float tolerance).
  2. The yz fix — a head whose frozen ``vol_feature_column`` is
     ``yang_zhang_vol`` buckets ``vol_bucket`` against the YZ value, not the
     close-to-close ``rolling_log_return_vol`` (the pre-S17 bug).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from ml.datasets.families.market_features import MarketFeaturesBuilder
from src.runtime.regime_shadow import (
    bucket_for_vol,
    feature_row_for_predictor,
    range_vol_estimators,
    rolling_log_return_vol,
)

_SYMBOL = "BTCUSDT"
_TIMEFRAME = "1h"
_VOL_WINDOW_N = 5
_FORWARD_M = 3

_CONTINUOUS_COLS = (
    "rolling_log_return_vol",
    "parkinson_vol",
    "garman_klass_vol",
    "rogers_satchell_vol",
    "yang_zhang_vol",
    "log_return",
    "log_return_lag_1",
    "log_return_lag_2",
)


class _FakePredictor:
    """Minimal stand-in carrying a frozen ``regime_spec`` (the live path reads
    ``predictor.wrapped.regime_spec`` or ``predictor.regime_spec``)."""

    def __init__(self, spec: dict):
        self.regime_spec = spec
        self.model_id = "test-regime-head"


def _ohlc_for(i: int, close: float) -> dict:
    """Distinct OHLC per bar so the range estimators are non-trivial."""
    return {
        "open": close * (1.0 + 0.0004 * ((i % 3) - 1)),
        "high": close * (1.0 + 0.0015 + 0.0003 * (i % 4)),
        "low": close * (1.0 - 0.0015 - 0.0003 * ((i + 2) % 4)),
        "close": close,
        "volume": 100.0 + i,
    }


def _stage_market_raw(tmp_path: Path, closes: list[float]) -> tuple[Path, list[dict]]:
    from datetime import datetime, timedelta, timezone

    base = datetime(2025, 3, 2, 13, 0, 0, tzinfo=timezone.utc)  # Sun 13:00 UTC
    root = tmp_path / "market_raw" / _SYMBOL / _TIMEFRAME / "v001"
    root.mkdir(parents=True, exist_ok=True)
    raw: list[dict] = []
    with (root / "data.jsonl").open("w", encoding="utf-8") as fh:
        for i, close in enumerate(closes):
            ts = (base + timedelta(hours=i)).isoformat().replace("+00:00", "Z")
            row = {"ts": ts, "symbol": _SYMBOL, "timeframe": _TIMEFRAME,
                   "source": "csv", **_ohlc_for(i, close)}
            raw.append(row)
            fh.write(json.dumps(row) + "\n")
    return root, raw


def _candles_df(raw: list[dict], upto: int) -> pd.DataFrame:
    return pd.DataFrame([
        {"timestamp": r["ts"], "open": r["open"], "high": r["high"],
         "low": r["low"], "close": r["close"], "volume": r["volume"]}
        for r in raw[: upto + 1]
    ])


def _varied_closes(n: int = 40) -> list[float]:
    closes: list[float] = []
    price = 100.0
    for i in range(n):
        # Alternating drift + a slow trend → non-degenerate vol.
        price *= 1.0 + (0.012 if i % 2 == 0 else -0.008) + 0.0005 * (i % 5)
        closes.append(price)
    return closes


def _spec(vol_feature_column: str, edges: list[float], labels: list[str]) -> dict:
    return {
        "symbol": _SYMBOL,
        "timeframe": _TIMEFRAME,
        "vol_window_n": _VOL_WINDOW_N,
        "vol_feature_column": vol_feature_column,
        "vol_bucket_edges": edges,
        "vol_bucket_labels": labels,
        "feature_column": "vol_bucket",
    }


def test_live_row_matches_builder_continuous_features(tmp_path: Path):
    closes = _varied_closes(40)
    market_raw, raw = _stage_market_raw(tmp_path, closes)
    ref_rows = list(MarketFeaturesBuilder().iter_rows(
        market_raw_path=market_raw, vol_window_n=_VOL_WINDOW_N,
        forward_window_m=_FORWARD_M, n_vol_buckets=3,
    ))
    assert ref_rows, "builder must emit rows"
    ref_by_ts = {r["ts"]: r for r in ref_rows}

    # Pick a middle emitted bar (full past window with s>0 so prev_close is real).
    target_ts = sorted(ref_by_ts)[len(ref_by_ts) // 2]
    i = next(k for k, r in enumerate(raw) if r["ts"] == target_ts)
    ref = ref_by_ts[target_ts]

    candles_df = _candles_df(raw, i)
    closes_list = [r["close"] for r in raw[: i + 1]]
    pred = _FakePredictor(_spec("rolling_log_return_vol", [0.05, 0.2],
                                ["vol_b0", "vol_b1", "vol_b2"]))
    live = feature_row_for_predictor(
        pred, {}, closes=closes_list, symbol=_SYMBOL,
        timeframe=_TIMEFRAME, candles_df=candles_df,
    )
    assert live is not None
    for col in _CONTINUOUS_COLS:
        assert live[col] == pytest.approx(ref[col], rel=1e-9, abs=1e-12), col
    assert live["hour_of_day"] == ref["hour_of_day"]
    assert live["dayofweek"] == ref["dayofweek"]


def test_yz_head_buckets_against_yang_zhang_not_close_to_close(tmp_path: Path):
    """The S17 fix: a vol_feature_column=yang_zhang_vol head must bucket the YZ
    value. With an edge placed strictly between the live YZ and rolling vols,
    bucketing the wrong axis would yield the wrong bucket."""
    closes = _varied_closes(40)
    _market_raw, raw = _stage_market_raw(tmp_path, closes)
    i = 30
    candles_df = _candles_df(raw, i)
    closes_list = [r["close"] for r in raw[: i + 1]]
    opens = [r["open"] for r in raw[: i + 1]]
    highs = [r["high"] for r in raw[: i + 1]]
    lows = [r["low"] for r in raw[: i + 1]]

    rolling = rolling_log_return_vol(closes_list, _VOL_WINDOW_N)
    yz = range_vol_estimators(opens, highs, lows, closes_list, _VOL_WINDOW_N)["yang_zhang_vol"]
    assert rolling is not None and yz > 0.0
    assert abs(yz - rolling) > 1e-6, "test needs YZ != rolling vol to discriminate"

    lo, hi = sorted((rolling, yz))
    edge = (lo + hi) / 2.0
    labels = ["vol_b0", "vol_b1"]
    pred = _FakePredictor(_spec("yang_zhang_vol", [edge], labels))
    live = feature_row_for_predictor(
        pred, {}, closes=closes_list, symbol=_SYMBOL,
        timeframe=_TIMEFRAME, candles_df=candles_df,
    )
    assert live is not None
    # The bucket must reflect the YZ value, NOT the close-to-close rolling vol.
    assert live["vol_bucket"] == bucket_for_vol(yz, [edge], labels)
    assert live["vol_bucket"] != bucket_for_vol(rolling, [edge], labels)
    # And the YZ value is emitted under its own column.
    assert live["yang_zhang_vol"] == pytest.approx(yz)


def test_legacy_close_only_path_still_works_without_candles():
    """Backwards-compat: callers that don't pass candles_df fall back to the
    pre-S17 close-only shape (vol_bucket + the vol_feature_column value)."""
    closes = _varied_closes(20)
    closes_list = [c for c in closes]
    pred = _FakePredictor(_spec("rolling_log_return_vol", [0.05, 0.2],
                                ["vol_b0", "vol_b1", "vol_b2"]))
    live = feature_row_for_predictor(
        pred, {"strategy_name": "x"}, closes=closes_list,
        symbol=_SYMBOL, timeframe=_TIMEFRAME,  # no candles_df
    )
    assert live is not None
    assert "vol_bucket" in live
    assert "rolling_log_return_vol" in live
    # The close-only fallback does NOT compute the range estimators.
    assert "yang_zhang_vol" not in live


def test_non_regime_predictor_unchanged(tmp_path: Path):
    """A predictor with no regime_spec is returned the base row untouched."""
    pred = _FakePredictor(None)  # type: ignore[arg-type]
    pred.regime_spec = None
    base = {"strategy_name": "vwap", "confidence": 0.5}
    out = feature_row_for_predictor(
        pred, base, closes=[1.0, 2.0, 3.0], symbol=_SYMBOL, timeframe=_TIMEFRAME,
    )
    assert out == base
