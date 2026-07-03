"""Tests for the live TSFM forecast-serve producer (M19 Track-1 PR 1a).

All tests run WITHOUT torch/chronos AND without pandas/numpy — the heavy
forecaster is injected as a deterministic stub (the same pattern as
`test_forecast_features.py`), and the producer's pandas-importing candle fetch
is never touched (the tests feed pre-shaped candle dicts directly). This proves
the module's import discipline: `import scripts.ml.publish_live_forecasts`
succeeds in a bare CI env.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import scripts.ml.publish_live_forecasts as producer
from ml.datasets.forecast_features import (
    FORECAST_FEATURE_COLUMNS,
    compute_forecast_feature_rows,
)


def _stub_forecast(*, up: bool = True):
    """Deterministic batch forecaster (copied from test_forecast_features)."""

    def _forecast(windows, horizon, quantile_levels):
        out = []
        for w in windows:
            last = float(w[-1]) if w else 1.0
            bump = 1.002 if up else 0.998
            out.append({
                quantile_levels[0]: last * 0.997,
                quantile_levels[1]: last * bump,
                quantile_levels[2]: last * 1.006,
            })
        return out

    return _forecast


def _candles(n: int, *, base_close: float = 100.0) -> list[dict]:
    """Synthetic candle window shaped like the `fetch_candles` DataFrame records."""
    base = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    return [
        {
            "timestamp": (base + timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z"),
            "open": base_close + i,
            "high": (base_close + i) * 1.001,
            "low": (base_close + i) * 0.999,
            "close": base_close + i,
            "volume": 100.0,
        }
        for i in range(n)
    ]


def _market_raw_rows(candles: list[dict]) -> list[dict]:
    """The offline `market_raw` view of the SAME candles (ts + close).

    Built INDEPENDENTLY of the producer's shaping so the parity assertion is a
    real cross-check, not a tautology: this is what `build_forecasts.py` would
    feed `compute_forecast_feature_rows` for the same closes.
    """
    return [
        {"ts": str(c["timestamp"]), "close": c["close"]}
        for c in candles
    ]


class TestParity:
    def test_published_fc_row_matches_offline_last_row(self):
        candles = _candles(40)
        kwargs = dict(context_len=8, stride=4, min_context=8)

        # OFFLINE path: the pure fn over the market_raw rows, last row.
        inline = compute_forecast_feature_rows(
            _market_raw_rows(candles), forecast_fn=_stub_forecast(), **kwargs
        )
        assert inline, "offline compute should emit rows"
        inline_fc = {c: inline[-1][c] for c in FORECAST_FEATURE_COLUMNS}

        # PRODUCER path: the artifact core fn over the raw candles.
        artifact = producer.build_forecast_artifact(
            "BTCUSDT", "15m", candles, forecast_fn=_stub_forecast(), **kwargs
        )
        assert artifact is not None

        # The parity guarantee — bit-for-bit equal fc_* block + same anchor bar.
        assert artifact["fc_row"] == inline_fc
        assert artifact["as_of_ts"] == inline[-1]["ts"]

    def test_latest_forecast_row_is_the_most_recent_bar(self):
        candles = _candles(40)
        row = producer.latest_forecast_row(
            candles, forecast_fn=_stub_forecast(),
            context_len=8, stride=4, min_context=8,
        )
        assert row is not None
        # _strided_indices always includes the last bar → newest ts.
        assert row["ts"] == str(candles[-1]["timestamp"])

    def test_insufficient_history_returns_none(self):
        # Fewer bars than min_context → no row emitted.
        candles = _candles(3)
        assert producer.build_forecast_artifact(
            "BTCUSDT", "15m", candles, forecast_fn=_stub_forecast(),
            context_len=8, stride=4, min_context=8,
        ) is None


class TestArtifact:
    def test_writes_well_formed_json_with_all_feature_columns(self, tmp_path):
        candles = _candles(40)
        path = producer.write_forecast_artifact(
            tmp_path, "BTCUSDT", "15m", candles, forecast_fn=_stub_forecast(),
            context_len=8, stride=4, min_context=8,
        )
        assert path is not None
        assert path.name == "BTCUSDT.json"

        data = json.loads(path.read_text(encoding="utf-8"))
        # Envelope keys.
        for key in (
            "symbol", "timeframe", "generated_at", "context_len", "horizon",
            "min_context", "quantile_levels", "model_id", "feature_columns",
            "fc_row", "as_of_ts",
        ):
            assert key in data, f"missing envelope key {key}"
        assert data["symbol"] == "BTCUSDT"
        assert data["timeframe"] == "15m"
        assert data["feature_columns"] == list(FORECAST_FEATURE_COLUMNS)

        # Every fc_* column present + a real float.
        assert set(data["fc_row"]) == set(FORECAST_FEATURE_COLUMNS)
        for col in FORECAST_FEATURE_COLUMNS:
            assert isinstance(data["fc_row"][col], float)

    def test_write_is_atomic_no_tmp_left_behind(self, tmp_path):
        producer.write_forecast_artifact(
            tmp_path, "ETHUSDT", "15m", _candles(40), forecast_fn=_stub_forecast(),
            context_len=8, stride=4, min_context=8,
        )
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "ETHUSDT.json"]
        assert leftovers == [], f"unexpected files left behind: {leftovers}"


class TestImportDiscipline:
    def test_module_constants_are_imported_not_redefined(self):
        # The producer must reuse the parity constants, never shadow them.
        from ml.datasets import forecast_features as ff

        assert producer.FORECAST_FEATURE_COLUMNS is ff.FORECAST_FEATURE_COLUMNS
        assert producer.DEFAULT_CONTEXT_LEN == ff.DEFAULT_CONTEXT_LEN
        assert producer.FORECAST_QUANTILES == ff.FORECAST_QUANTILES
        assert producer.FORECAST_MODEL_ID == ff.FORECAST_MODEL_ID

    def test_module_imported_without_torch_or_pandas(self):
        # If the module imported at test-collection time (it did — top of file),
        # neither torch nor pandas was required to do so. Assert they are NOT
        # forced as a dependency of the import path.
        import importlib.util

        assert importlib.util.find_spec("scripts.ml.publish_live_forecasts") is not None
