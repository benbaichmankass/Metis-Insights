"""Tests for per-bar regime scoring (S-MLOPT-S13 / Phase 3.1).

Covers ``src.runtime.regime_bar_scoring.emit_regime_bar_predictions``: it
scores every shadow-stage REGIME head on its own (symbol, timeframe) bar
cadence, deduped to one record per closed bar, observe-only, never raising —
independent of any actionable signal (closes MB-20260529-001).

The predictor list + candle fetcher + dedup cache are injectable so the path
is exercised without a registry or live market data.
"""
from __future__ import annotations

import json

import pytest

from ml.predictors.base import Predictor
from ml.predictors.shadow import ShadowPredictor
from src.runtime.regime_bar_scoring import (
    _last_bar_timestamp,
    emit_regime_bar_predictions,
    regime_bar_scoring_enabled,
)


class _RegimeBase(Predictor):
    """A minimal regime predictor: carries a regime_spec, scores on vol_bucket."""

    def __init__(self, regime_spec):
        self.regime_spec = regime_spec
        self.calls: list = []

    def predict(self, row):
        self.calls.append(dict(row))
        # Deterministic, just so the score column is non-constant per bucket.
        return float(len(str(row.get("vol_bucket") or "")))


class _PlainBase(Predictor):
    """A non-regime predictor (no regime_spec) — must be ignored per-bar."""

    def __init__(self):
        self.calls: list = []

    def predict(self, row):
        self.calls.append(dict(row))
        return 1.0


def _spec(symbol="BTCUSDT", timeframe="5m", edges=(0.001, 0.002), window=20):
    return {
        "feature_column": "vol_bucket",
        "vol_feature_column": "rolling_log_return_vol",
        "vol_window_n": window,
        "vol_bucket_edges": list(edges),
        "vol_bucket_labels": ["vol_b0", "vol_b1", "vol_b2"],
        "symbol": symbol,
        "timeframe": timeframe,
    }


def _regime_predictor(model_id, spec, log_path):
    return ShadowPredictor(
        _RegimeBase(spec), model_id=model_id, stage="shadow", log_path=log_path,
    )


def _candles(n=30, start=100.0, step=1.001, last_ts=1_000):
    """A list-of-dict candle frame the duck-typed accessors accept."""
    rows = []
    price = start
    for i in range(n):
        rows.append(
            {
                "timestamp": last_ts - (n - 1 - i),
                "open": price,
                "high": price * 1.001,
                "low": price * 0.999,
                "close": price,
                "volume": 10.0,
            }
        )
        price *= step
    # Duck-typed frame: __getitem__ returns a column list.
    return _FrameLike(rows)


class _FrameLike:
    def __init__(self, rows):
        self._rows = rows

    def __getitem__(self, key):
        return [r[key] for r in self._rows]


class TestEnabled:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("REGIME_BAR_SCORING_DISABLED", raising=False)
        assert regime_bar_scoring_enabled() is True

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE", "On"])
    def test_disabled_values(self, monkeypatch, val):
        monkeypatch.setenv("REGIME_BAR_SCORING_DISABLED", val)
        assert regime_bar_scoring_enabled() is False

    def test_disabled_short_circuits(self, monkeypatch, tmp_path):
        monkeypatch.setenv("REGIME_BAR_SCORING_DISABLED", "1")
        pred = _regime_predictor(
            "btc-regime-5m", _spec(), tmp_path / "shadow.jsonl"
        )
        n = emit_regime_bar_predictions(
            predictors=[pred],
            fetch_fn=lambda s, t: _candles(),
            seen={},
        )
        assert n == 0
        assert pred.wrapped.calls == []


class TestLastBarTimestamp:
    def test_reads_last(self):
        assert _last_bar_timestamp(_candles(last_ts=42)) == 42

    def test_none_inputs(self):
        assert _last_bar_timestamp(None) is None
        assert _last_bar_timestamp(_FrameLike([])) is None
        assert _last_bar_timestamp(object()) is None


class TestEmit:
    def test_scores_regime_head_and_writes_log(self, tmp_path):
        log = tmp_path / "shadow.jsonl"
        pred = _regime_predictor("btc-regime-5m", _spec(), log)
        seen = {}
        n = emit_regime_bar_predictions(
            predictors=[pred],
            fetch_fn=lambda s, t: _candles(last_ts=1234),
            seen=seen,
        )
        assert n == 1
        assert len(pred.wrapped.calls) == 1
        row = pred.wrapped.calls[0]
        # Reused feature_row_for_predictor → carries the live bucket + marker.
        assert "vol_bucket" in row
        assert row["event_source"] == "per_bar"
        assert row["symbol"] == "BTCUSDT"
        # The shadow audit log got exactly one record.
        lines = log.read_text().strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["model_id"] == "btc-regime-5m"
        assert rec["stage"] == "shadow"
        assert rec["feature_row"]["event_source"] == "per_bar"
        # Dedup state updated to this bar.
        assert seen["btc-regime-5m"] == 1234

    def test_dedup_same_bar_scored_once(self, tmp_path):
        pred = _regime_predictor("btc-regime-5m", _spec(), tmp_path / "s.jsonl")
        seen = {}
        kwargs = dict(
            predictors=[pred],
            fetch_fn=lambda s, t: _candles(last_ts=999),
            seen=seen,
        )
        assert emit_regime_bar_predictions(**kwargs) == 1
        # Same bar timestamp → second call is a no-op.
        assert emit_regime_bar_predictions(**kwargs) == 0
        assert len(pred.wrapped.calls) == 1

    def test_new_bar_scores_again(self, tmp_path):
        pred = _regime_predictor("btc-regime-5m", _spec(), tmp_path / "s.jsonl")
        seen = {}
        assert (
            emit_regime_bar_predictions(
                predictors=[pred], fetch_fn=lambda s, t: _candles(last_ts=1),
                seen=seen,
            )
            == 1
        )
        # A later closed bar → scored again.
        assert (
            emit_regime_bar_predictions(
                predictors=[pred], fetch_fn=lambda s, t: _candles(last_ts=2),
                seen=seen,
            )
            == 1
        )
        assert len(pred.wrapped.calls) == 2

    def test_non_regime_predictor_ignored(self, tmp_path):
        plain = ShadowPredictor(
            _PlainBase(), model_id="setup-quality", stage="shadow",
            log_path=tmp_path / "s.jsonl",
        )
        n = emit_regime_bar_predictions(
            predictors=[plain],
            fetch_fn=lambda s, t: _candles(),
            seen={},
        )
        assert n == 0
        assert plain.wrapped.calls == []

    def test_fetch_failure_skips_head(self, tmp_path):
        pred = _regime_predictor("btc-regime-5m", _spec(), tmp_path / "s.jsonl")

        def _boom(symbol, timeframe):
            raise RuntimeError("market data down")

        n = emit_regime_bar_predictions(
            predictors=[pred], fetch_fn=_boom, seen={},
        )
        assert n == 0
        assert pred.wrapped.calls == []

    def test_none_candles_skips_head(self, tmp_path):
        pred = _regime_predictor("btc-regime-5m", _spec(), tmp_path / "s.jsonl")
        n = emit_regime_bar_predictions(
            predictors=[pred], fetch_fn=lambda s, t: None, seen={},
        )
        assert n == 0

    def test_multiple_heads_scored_per_own_bar(self, tmp_path):
        log = tmp_path / "s.jsonl"
        btc5 = _regime_predictor(
            "btc-regime-5m", _spec(symbol="BTCUSDT", timeframe="5m"), log
        )
        mes15 = _regime_predictor(
            "mes-regime-15m", _spec(symbol="MES", timeframe="15m"), log
        )

        def _fetch(symbol, timeframe):
            # Each market returns its own bar timestamp.
            return _candles(last_ts=hash((symbol, timeframe)) & 0xFFFF)

        n = emit_regime_bar_predictions(
            predictors=[btc5, mes15], fetch_fn=_fetch, seen={},
        )
        assert n == 2
        assert len(btc5.wrapped.calls) == 1
        assert len(mes15.wrapped.calls) == 1

    def test_one_head_failure_does_not_block_others(self, tmp_path):
        good = _regime_predictor("btc-regime-5m", _spec(), tmp_path / "s.jsonl")
        bad = _regime_predictor(
            "mes-regime-15m", _spec(symbol="MES", timeframe="15m"),
            tmp_path / "s.jsonl",
        )

        def _fetch(symbol, timeframe):
            if symbol == "MES":
                raise RuntimeError("ibkr down")
            return _candles(last_ts=7)

        n = emit_regime_bar_predictions(
            predictors=[bad, good], fetch_fn=_fetch, seen={},
        )
        # The MES fetch failed but BTC still scored.
        assert n == 1
        assert len(good.wrapped.calls) == 1

    def test_never_raises_on_internal_error(self, tmp_path):
        # A predictor whose model_id access path is fine but whose spec yields
        # an uncomputable vol (single close) → row is None → skipped, n=0.
        pred = _regime_predictor("btc-regime-5m", _spec(window=20), tmp_path / "s.jsonl")
        n = emit_regime_bar_predictions(
            predictors=[pred],
            fetch_fn=lambda s, t: _FrameLike([{"timestamp": 1, "close": 100.0,
                                               "open": 100.0, "high": 100.0,
                                               "low": 100.0, "volume": 1.0}]),
            seen={},
        )
        assert n == 0
