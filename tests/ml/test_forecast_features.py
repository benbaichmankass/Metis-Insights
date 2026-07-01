"""Tests for the pure pretrained-TSFM quantile-forecast feature block (M19 T0.4).

All tests run WITHOUT torch/chronos — the heavy forecaster is injected as a stub,
mirroring the block's design (the real Chronos call is lazy + injectable so the
windowing / stride / as-of / neutral-default logic is CI-testable).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

from ml.datasets.forecast_features import (
    FORECAST_FEATURE_COLUMNS,
    _finite_or_zero,
    _strided_indices,
    compute_forecast_feature_rows,
    quantile_forecast_features,
)


def _bar_rows(n: int, *, base_close: float = 100.0) -> list[dict]:
    base = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    return [
        {
            "ts": (base + timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
            "symbol": "BTCUSDT",
            "close": base_close + i,
        }
        for i in range(n)
    ]


def _stub_forecast(*, up: bool = True):
    """Deterministic batch forecaster.

    Per window, returns price quantiles as a small fixed offset from the last
    context close, so the derived fc_* features are non-trivial and predictable.
    `up=True` biases the median above the last close (a positive fc_ret_med).
    """

    def _forecast(windows, horizon, quantile_levels):
        out = []
        for w in windows:
            last = float(w[-1]) if w else 1.0
            bump = 1.002 if up else 0.998
            # q10 below, q50 at `bump`, q90 above — a slightly asymmetric band.
            out.append({
                quantile_levels[0]: last * 0.997,
                quantile_levels[1]: last * bump,
                quantile_levels[2]: last * 1.006,
            })
        return out

    return _forecast


class TestColumnContract:
    def test_columns_are_the_fixed_six(self):
        assert FORECAST_FEATURE_COLUMNS == (
            "fc_ret_med",
            "fc_range_rel",
            "fc_up_prob",
            "fc_skew",
            "fc_q10_rel",
            "fc_q90_rel",
        )
        assert len(FORECAST_FEATURE_COLUMNS) == 6

    def test_finite_or_zero(self):
        assert _finite_or_zero(None) == 0.0
        assert _finite_or_zero(float("nan")) == 0.0
        assert _finite_or_zero(float("inf")) == 0.0
        assert _finite_or_zero(1.5) == 1.5


class TestQuantileForecastFeatures:
    def test_log_return_space_and_up_prob(self):
        last = 100.0
        feats = quantile_forecast_features(
            {0.1: 99.0, 0.5: 101.0, 0.9: 103.0}, last,
            quantile_levels=(0.1, 0.5, 0.9),
        )
        assert set(feats) == set(FORECAST_FEATURE_COLUMNS)
        assert math.isclose(feats["fc_ret_med"], math.log(101.0 / 100.0))
        assert math.isclose(feats["fc_q10_rel"], math.log(99.0 / 100.0))
        assert math.isclose(feats["fc_q90_rel"], math.log(103.0 / 100.0))
        assert math.isclose(feats["fc_range_rel"], (103.0 - 99.0) / 100.0)
        assert feats["fc_up_prob"] == 1.0  # median above last close

    def test_down_forecast_up_prob_zero(self):
        feats = quantile_forecast_features(
            {0.1: 96.0, 0.5: 98.0, 0.9: 101.0}, 100.0,
        )
        assert feats["fc_up_prob"] == 0.0
        assert feats["fc_ret_med"] < 0.0

    def test_none_or_bad_last_close_is_neutral(self):
        assert quantile_forecast_features(None, 100.0) == {
            c: 0.0 for c in FORECAST_FEATURE_COLUMNS
        }
        assert quantile_forecast_features({0.1: 1, 0.5: 1, 0.9: 1}, 0.0) == {
            c: 0.0 for c in FORECAST_FEATURE_COLUMNS
        }

    def test_skew_is_zero_on_degenerate_band(self):
        # q10==q90 in log-return space → denom ≈ 0 → fc_skew == 0.0.
        feats = quantile_forecast_features(
            {0.1: 100.0, 0.5: 100.0, 0.9: 100.0}, 100.0,
        )
        assert feats["fc_skew"] == 0.0
        assert feats["fc_range_rel"] == 0.0


class TestStridedIndices:
    def test_includes_last_bar(self):
        assert _strided_indices(20, 4) == [0, 4, 8, 12, 16, 19]

    def test_stride_one_is_every_bar(self):
        assert _strided_indices(5, 1) == [0, 1, 2, 3, 4]


class TestComputeForecastRows:
    def test_emits_at_strided_indices_meeting_min_context(self):
        rows = _bar_rows(20)
        out = compute_forecast_feature_rows(
            rows, context_len=8, stride=4, min_context=8, forecast_fn=_stub_forecast()
        )
        # Strided indices {0,4,8,12,16,19}; only those with i+1>=8 emit → {8,12,16,19}.
        assert [r["ts"] for r in out] == [rows[i]["ts"] for i in (8, 12, 16, 19)]

    def test_row_has_ts_plus_fixed_width_features(self):
        out = compute_forecast_feature_rows(
            _bar_rows(20), context_len=8, stride=4, min_context=8,
            forecast_fn=_stub_forecast(),
        )
        for r in out:
            assert set(r) == {"ts", *FORECAST_FEATURE_COLUMNS}
            assert len(r) == 1 + len(FORECAST_FEATURE_COLUMNS)

    def test_deterministic(self):
        rows = _bar_rows(20)
        a = compute_forecast_feature_rows(
            rows, context_len=8, stride=4, min_context=8, forecast_fn=_stub_forecast()
        )
        b = compute_forecast_feature_rows(
            rows, context_len=8, stride=4, min_context=8, forecast_fn=_stub_forecast()
        )
        assert a == b

    def test_forecaster_failure_degrades_to_neutral_zeros(self):
        def _boom(windows, horizon, quantile_levels):
            raise RuntimeError("no gpu on this box")

        out = compute_forecast_feature_rows(
            _bar_rows(20), context_len=8, stride=4, min_context=8, forecast_fn=_boom
        )
        assert out, "rows are still emitted (neutral) on forecaster failure"
        assert all(
            all(r[c] == 0.0 for c in FORECAST_FEATURE_COLUMNS) for r in out
        )

    def test_past_only_window_never_reaches_the_future(self):
        # The stub forecasts a fixed +0.2% median off the LAST context close; a
        # window ending at bar i uses closes[max(0,i-context_len+1)..i], so the
        # median forecast can only ever depend on closes[i] (the last close) —
        # never a future bar. We prove that by checking fc_ret_med equals the
        # log-return the stub bump implies for closes[i].
        rows = _bar_rows(30)
        out = compute_forecast_feature_rows(
            rows, context_len=8, stride=1, min_context=8, forecast_fn=_stub_forecast()
        )
        by_ts = {r["ts"]: r for r in out}
        for i, row in enumerate(rows):
            r = by_ts.get(row["ts"])
            if r is None:
                continue
            expected = math.log(1.002)  # bump vs the last close, independent of i
            assert math.isclose(r["fc_ret_med"], expected, rel_tol=1e-9, abs_tol=1e-12)

    def test_nonzero_features_emitted(self):
        out = compute_forecast_feature_rows(
            _bar_rows(20), context_len=8, stride=4, min_context=8,
            forecast_fn=_stub_forecast(),
        )
        assert any(
            any(r[c] != 0.0 for c in FORECAST_FEATURE_COLUMNS) for r in out
        )

    def test_empty_input(self):
        assert compute_forecast_feature_rows([], forecast_fn=_stub_forecast()) == []


def _stage_market_raw(tmp_path: Path, closes: list[float]) -> Path:
    base = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    root = tmp_path / "market_raw" / "BTCUSDT" / "1h" / "v001"
    root.mkdir(parents=True, exist_ok=True)
    with (root / "data.jsonl").open("w", encoding="utf-8") as fh:
        for i, c in enumerate(closes):
            ts = (base + timedelta(hours=i)).isoformat().replace("+00:00", "Z")
            fh.write(json.dumps({
                "ts": ts, "symbol": "BTCUSDT", "timeframe": "1h",
                "open": c, "high": c * 1.001, "low": c * 0.999,
                "close": c, "volume": 100.0, "source": "test",
            }) + "\n")
    return root


def _stage_forecast_sidestream(tmp_path: Path, market_raw: Path) -> Path:
    """Produce the fc_* side-stream from the staged market_raw via the stub."""
    rows = [
        json.loads(line)
        for line in (market_raw / "data.jsonl").read_text().splitlines()
    ]
    fc_rows = compute_forecast_feature_rows(
        rows, context_len=8, stride=4, min_context=8, forecast_fn=_stub_forecast()
    )
    out = tmp_path / "forecasts" / "BTCUSDT" / "1h" / "v001"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "data.jsonl").open("w", encoding="utf-8") as fh:
        for r in fc_rows:
            fh.write(json.dumps(r) + "\n")
    return out


class TestMarketFeaturesIntegration:
    def _closes(self, n: int = 60) -> list[float]:
        # Mildly varying closes so the base regime pipeline emits complete rows.
        return [100.0 + (i % 7) - (i % 3) * 0.5 + i * 0.1 for i in range(n)]

    def test_without_forecast_path_columns_are_zero(self, tmp_path):
        from ml.datasets.families.market_features import MarketFeaturesBuilder

        mr = _stage_market_raw(tmp_path, self._closes())
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=mr, vol_window_n=5, forward_window_m=3,
        ))
        assert rows, "baseline build should emit rows"
        for r in rows:
            for c in FORECAST_FEATURE_COLUMNS:
                assert r[c] == 0.0

    def test_with_forecast_path_columns_are_asof_carried(self, tmp_path):
        from ml.datasets.families.market_features import MarketFeaturesBuilder

        closes = self._closes()
        mr = _stage_market_raw(tmp_path, closes)
        fc = _stage_forecast_sidestream(tmp_path, mr)
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=mr, vol_window_n=5, forward_window_m=3,
            forecast_path=fc,
        ))
        assert rows
        # At least one emitted market_features row must carry a non-zero forecast
        # feature (as-of carried from the side-stream) — proof the merge works.
        assert any(
            any(r[c] != 0.0 for c in FORECAST_FEATURE_COLUMNS) for r in rows
        )
        # Every forecast column present on every row (schema completeness).
        for r in rows:
            for c in FORECAST_FEATURE_COLUMNS:
                assert c in r
