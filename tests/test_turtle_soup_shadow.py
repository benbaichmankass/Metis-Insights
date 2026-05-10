"""Tests for turtle_soup.order_package shadow-mode integration
(S-AI-WS7-PART-5).

Mirrors `tests/test_vwap_shadow.py` — same surface, different
strategy. Verifies:

- Package keys are unchanged when no shadow predictor is wired.
- Singular `_shadow_predictor` (PART-3 backwards-compat) still works.
- Plural `_shadow_predictors` injection runs every predictor.
- Config-driven `shadow_model_ids` resolves through the registry-
  backed factory and runs concurrent predictors with per-tick
  audit lines.
- A failing predictor never breaks the package or other predictors.
- Stage gate refuses unpromoted models without crashing the tick.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any, Mapping
from unittest import mock

import pytest

# Same matplotlib stub as the existing vwap shadow tests use.
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from ml.predictors import ConstantPredictor, Predictor, ShadowPredictor  # noqa: E402
from src.units.strategies.turtle_soup import (  # noqa: E402
    _build_shadow_feature_row,
    order_package,
)


def _flat_frame(n: int = 80, base: float = 50_000.0) -> pd.DataFrame:
    rng = pd.date_range("2026-04-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "open": np.full(n, base),
            "high": np.full(n, base + 100.0),
            "low": np.full(n, base - 100.0),
            "close": np.full(n, base + 50.0),
            "volume": np.full(n, 1.0),
        },
        index=rng,
    )


def _bullish_sweep_frame(n: int = 80, base: float = 50_000.0) -> pd.DataFrame:
    """Last bar pierces the rolling-min low and closes back above it.
    Identical fixture to `tests/test_s012_turtle_soup.py` so we know
    the deterministic detector emits a long package on this frame.
    """
    df = _flat_frame(n, base).copy()
    last = df.index[-1]
    df.loc[last, "low"] = base - 500.0
    df.loc[last, "high"] = base + 100.0
    df.loc[last, "open"] = base - 400.0
    df.loc[last, "close"] = base + 50.0
    return df


class _CountingPredictor(Predictor):
    def __init__(self, score: float = 0.5) -> None:
        self._score = score
        self.calls: list[Mapping[str, Any]] = []

    def predict(self, row: Mapping[str, Any]) -> float:
        self.calls.append(dict(row))
        return self._score


class _BrokenPredictor(Predictor):
    def predict(self, row: Mapping[str, Any]) -> float:
        raise RuntimeError("model state corrupted")


_PACKAGE_KEYS = sorted(
    ["symbol", "direction", "entry", "sl", "tp", "confidence", "meta"]
)


class TestTurtleSoupShadowIntegration:
    def test_no_predictor_keys_unchanged(self):
        package = order_package({"symbol": "BTCUSDT"}, _bullish_sweep_frame())
        assert sorted(package.keys()) == _PACKAGE_KEYS
        assert "shadow_score" not in package
        assert "model_id" not in package

    def test_singular_predictor_called(self, tmp_path: Path):
        inner = _CountingPredictor(score=0.42)
        predictor = ShadowPredictor(
            inner, model_id="ts-shadow-v0", stage="shadow",
            log_path=tmp_path / "audit.jsonl",
        )
        package = order_package(
            {"symbol": "BTCUSDT", "_shadow_predictor": predictor},
            _bullish_sweep_frame(),
        )
        assert sorted(package.keys()) == _PACKAGE_KEYS
        assert len(inner.calls) == 1
        row = inner.calls[0]
        assert row["strategy_name"] == "turtle_soup"
        assert row["direction"] == package["direction"]
        assert row["confidence"] == package["confidence"]
        # Outcome columns must NOT leak into the feature row.
        assert "pnl" not in row
        assert "r_multiple" not in row

    def test_audit_log_emitted(self, tmp_path: Path):
        log = tmp_path / "audit.jsonl"
        predictor = ShadowPredictor(
            ConstantPredictor(state={"constant": 0.5}),
            model_id="ts-shadow-v0", stage="shadow", log_path=log,
        )
        order_package(
            {"symbol": "BTCUSDT", "_shadow_predictor": predictor},
            _bullish_sweep_frame(),
        )
        lines = log.read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["model_id"] == "ts-shadow-v0"
        assert record["stage"] == "shadow"
        assert "score" in record

    def test_broken_predictor_does_not_crash(self, tmp_path: Path):
        broken = ShadowPredictor(
            _BrokenPredictor(), model_id="ts-broken", stage="shadow",
            log_path=tmp_path / "broken.jsonl",
        )
        package = order_package(
            {"symbol": "BTCUSDT", "_shadow_predictor": broken},
            _bullish_sweep_frame(),
        )
        assert sorted(package.keys()) == _PACKAGE_KEYS


class TestTurtleSoupConfigDrivenShadow:
    """PART-5: config-driven multi-predictor wiring on turtle_soup,
    same contract as PART-4's vwap surface."""

    def _register_model(
        self,
        root: Path,
        model_id: str,
        stage: str = "shadow",
    ) -> Path:
        from ml.registry.model_registry import ModelRegistry

        state_path = root / f"{model_id}_state.json"
        state_path.write_text(
            json.dumps(
                {
                    "trainer": "ml.trainers.constant_baseline."
                               "ConstantPredictionTrainer",
                    "constant": 0.5,
                }
            )
        )
        registry_root = root / "registry-store"
        registry = ModelRegistry(registry_root)
        registry.register(
            model_id=model_id,
            manifest={"manifest_version": "v1"},
            model_state_path=str(state_path),
            metrics={"mae": 0.1},
            code_revision="x",
        )
        ladder = [
            "candidate", "backtest_approved", "shadow",
            "advisory", "limited_live", "live_approved",
        ]
        for step in ladder:
            registry.promote_stage(
                model_id, step, by="op", reason=f"to-{step}",
            )
            if step == stage:
                break
        return registry_root

    def test_shadow_model_ids_resolves_three_concurrently(
        self, tmp_path: Path
    ):
        registry_root = self._register_model(tmp_path, "wr-baseline")
        self._register_model(tmp_path, "rmult-baseline")
        self._register_model(tmp_path, "slip-baseline")
        log_path = tmp_path / "shadow_audit.jsonl"
        cfg = {
            "symbol": "BTCUSDT",
            "shadow_model_ids": [
                "wr-baseline", "rmult-baseline", "slip-baseline",
            ],
            "_shadow_registry_root": str(registry_root),
            "_shadow_log_path": str(log_path),
        }
        package = order_package(cfg, _bullish_sweep_frame())
        assert sorted(package.keys()) == _PACKAGE_KEYS
        lines = [
            json.loads(line)
            for line in log_path.read_text().splitlines()
            if line
        ]
        assert len(lines) == 3
        assert {entry["model_id"] for entry in lines} == {
            "wr-baseline", "rmult-baseline", "slip-baseline",
        }

    def test_unpromoted_model_skipped_not_crashed(self, tmp_path: Path):
        registry_root = self._register_model(tmp_path, "ok-shadow")
        self._register_model(tmp_path, "stuck-research", stage="research_only")
        log_path = tmp_path / "shadow_audit.jsonl"
        cfg = {
            "symbol": "BTCUSDT",
            "shadow_model_ids": ["ok-shadow", "stuck-research"],
            "_shadow_registry_root": str(registry_root),
            "_shadow_log_path": str(log_path),
        }
        package = order_package(cfg, _bullish_sweep_frame())
        assert sorted(package.keys()) == _PACKAGE_KEYS
        lines = [
            json.loads(line)
            for line in log_path.read_text().splitlines()
            if line
        ]
        assert len(lines) == 1
        assert lines[0]["model_id"] == "ok-shadow"

    def test_empty_shadow_model_ids_no_op(self):
        cfg = {"symbol": "BTCUSDT", "shadow_model_ids": []}
        package = order_package(cfg, _bullish_sweep_frame())
        assert sorted(package.keys()) == _PACKAGE_KEYS

    def test_plural_injection_path(self, tmp_path: Path):
        log_a = tmp_path / "a.jsonl"
        log_b = tmp_path / "b.jsonl"
        a = ShadowPredictor(
            ConstantPredictor(state={"constant": 0.1}),
            model_id="m-a", stage="shadow", log_path=log_a,
        )
        b = ShadowPredictor(
            ConstantPredictor(state={"constant": 0.9}),
            model_id="m-b", stage="shadow", log_path=log_b,
        )
        cfg = {"symbol": "BTCUSDT", "_shadow_predictors": [a, b]}
        order_package(cfg, _bullish_sweep_frame())
        assert len(log_a.read_text().splitlines()) == 1
        assert len(log_b.read_text().splitlines()) == 1


class TestBuildShadowFeatureRow:
    def test_includes_strategy_specific_fields(self):
        package = {
            "symbol": "BTCUSDT", "direction": "long", "entry": 50_050.0,
            "sl": 49_650.0, "tp": 50_450.0, "confidence": 0.72,
            "meta": {
                "atr": 220.5, "body_to_range": 0.78,
                "setup_tf": "15m", "timeframe": "15m",
            },
        }
        row = _build_shadow_feature_row(package)
        assert row["strategy_name"] == "turtle_soup"
        assert row["direction"] == "long"
        assert row["confidence"] == pytest.approx(0.72)
        assert row["atr"] == pytest.approx(220.5)
        assert row["body_to_range"] == pytest.approx(0.78)
        assert row["setup_type"] == "15m"
        assert row["timeframe"] == "15m"
        # Outcome columns must NOT be in the row.
        assert "pnl" not in row
        assert "r_multiple" not in row

    def test_missing_meta_handled(self):
        row = _build_shadow_feature_row(
            {"symbol": "BTCUSDT", "direction": "long", "confidence": 0.5}
        )
        assert row["atr"] == 0.0
        assert row["body_to_range"] == 0.0
        assert row["setup_type"] == ""
        assert row["timeframe"] == ""
