"""Tests for vwap.order_package shadow-mode integration (S-AI-WS7-PART-3)."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any, Mapping
from unittest import mock

import pytest

# Same matplotlib stub as the existing vwap tests use — pipeline.py has
# a transitive dep that we don't need for this integration test.
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

pd = pytest.importorskip("pandas")

from ml.predictors import ConstantPredictor, Predictor, ShadowPredictor  # noqa: E402
from src.units.strategies.vwap import (  # noqa: E402
    _build_shadow_feature_row,
    order_package,
)


def _candles(*close_prices, volume=1000.0):
    rows = []
    for i, close in enumerate(close_prices):
        rows.append(
            {
                "timestamp": i,
                "open": close - 1,
                "high": close + 2,
                "low": close - 2,
                "close": close,
                "volume": volume,
            }
        )
    return pd.DataFrame(rows)


def _candles_below_vwap():
    """Last price well below VWAP — triggers a buy signal."""
    return _candles(100, 102, 101, 103, 102, 60)


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


class TestVwapShadowIntegration:
    """Verify that vwap.order_package threads through the shadow adapter
    without changing its return value."""

    def test_no_predictor_keys_unchanged(self):
        """When `_shadow_predictor` is absent, the package has the same
        canonical keys as the v1 baseline — no leakage of any
        shadow-mode field."""
        candles = _candles_below_vwap()
        package = order_package({}, candles)
        assert sorted(package.keys()) == sorted(
            ["symbol", "direction", "entry", "sl", "tp", "confidence", "meta"]
        )
        assert "shadow_score" not in package
        assert "model_id" not in package

    def test_predictor_called_with_signal_time_features(self, tmp_path: Path):
        inner = _CountingPredictor(score=0.42)
        predictor = ShadowPredictor(
            inner, model_id="vwap-shadow-v0", stage="shadow",
            log_path=tmp_path / "audit.jsonl",
        )
        candles = _candles_below_vwap()
        package = order_package(
            {"_shadow_predictor": predictor}, candles,
        )
        # Package shape unchanged.
        assert sorted(package.keys()) == sorted(
            ["symbol", "direction", "entry", "sl", "tp", "confidence", "meta"]
        )
        # Predictor saw exactly one row with the signal-time fields.
        assert len(inner.calls) == 1
        row = inner.calls[0]
        assert row["strategy_name"] == "vwap"
        assert row["direction"] == package["direction"]
        assert row["confidence"] == package["confidence"]
        assert row["symbol"] == package["symbol"]

    def test_audit_log_emitted(self, tmp_path: Path):
        predictor = ShadowPredictor(
            ConstantPredictor(state={"constant": 0.5}),
            model_id="vwap-shadow-v0", stage="shadow",
            log_path=tmp_path / "audit.jsonl",
        )
        order_package(
            {"_shadow_predictor": predictor}, _candles_below_vwap(),
        )
        lines = [
            json.loads(line)
            for line in (tmp_path / "audit.jsonl").read_text().splitlines()
            if line
        ]
        assert len(lines) == 1
        entry = lines[0]
        assert entry["model_id"] == "vwap-shadow-v0"
        assert entry["stage"] == "shadow"
        # row_keys must NOT include the secret-bearing fields. For vwap,
        # the row_keys list is the signal-time feature surface only.
        assert "score" not in entry["row_keys"]  # not a feature
        assert "pnl" not in entry["row_keys"]    # would be a leak
        assert "strategy_name" in entry["row_keys"]

    def test_broken_predictor_does_not_break_strategy(self, tmp_path: Path):
        """A misbehaving model must not crash the strategy tick. The
        deterministic package is returned identically to the no-predictor
        case."""
        baseline = order_package({}, _candles_below_vwap())

        broken = ShadowPredictor(
            _BrokenPredictor(),
            model_id="vwap-broken-v0", stage="shadow",
            log_path=tmp_path / "audit.jsonl",
        )
        with_predictor = order_package(
            {"_shadow_predictor": broken}, _candles_below_vwap(),
        )
        assert with_predictor == baseline


class TestBuildShadowFeatureRow:
    """Pure dict-transform test of `_build_shadow_feature_row`. Lives in
    this file so the import surface stays consistent with the integration
    test (pandas-gated)."""

    def test_minimal_package(self):
        row = _build_shadow_feature_row(
            {
                "symbol": "BTCUSDT",
                "direction": "long",
                "confidence": 0.7,
                "meta": {},
            }
        )
        assert row["strategy_name"] == "vwap"
        assert row["symbol"] == "BTCUSDT"
        assert row["direction"] == "long"
        assert row["confidence"] == pytest.approx(0.7)
        assert row["setup_type"] == ""
        assert row["killzone"] == ""
        assert row["bias"] == ""

    def test_meta_fields_passed_through(self):
        row = _build_shadow_feature_row(
            {
                "symbol": "BTCUSDT",
                "direction": "short",
                "confidence": 0.5,
                "meta": {
                    "setup_type": "FVG",
                    "killzone": "NY",
                    "bias": "BULLISH",
                },
            }
        )
        assert row["setup_type"] == "FVG"
        assert row["killzone"] == "NY"
        assert row["bias"] == "BULLISH"

    def test_row_excludes_outcome_fields(self):
        # Defense-in-depth: even if a future change adds pnl / r_multiple
        # to the package's meta, those are outcomes and must NOT show up
        # in the feature row that goes to a shadow predictor.
        row = _build_shadow_feature_row(
            {
                "symbol": "BTCUSDT",
                "direction": "long",
                "confidence": 0.5,
                "meta": {"pnl": 100.0, "r_multiple": 1.5},
            }
        )
        assert "pnl" not in row
        assert "r_multiple" not in row

    def test_missing_meta_handled(self):
        row = _build_shadow_feature_row(
            {
                "symbol": "BTCUSDT",
                "direction": "long",
                "confidence": 0.5,
                # No meta key at all.
            }
        )
        assert row["setup_type"] == ""
        assert row["killzone"] == ""


class TestVwapConfigDrivenShadow:
    """PART-4: vwap reads `shadow_model_ids` from cfg + resolves them
    via the registry-backed factory."""

    def _register_model(self, root: Path, model_id: str, stage: str = "shadow"):
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
        """Operator's PART-4 spec: wire all three WS5 models
        concurrently against vwap. Verify three audit lines per
        tick."""
        registry_root = self._register_model(tmp_path, "wr-baseline")
        self._register_model(tmp_path, "rmult-baseline")
        self._register_model(tmp_path, "slip-baseline")
        log_path = tmp_path / "shadow_audit.jsonl"

        cfg = {
            "shadow_model_ids": [
                "wr-baseline", "rmult-baseline", "slip-baseline",
            ],
            "_shadow_registry_root": str(registry_root),
            "_shadow_log_path": str(log_path),
        }
        package = order_package(cfg, _candles_below_vwap())
        # Package shape unchanged.
        assert sorted(package.keys()) == sorted(
            ["symbol", "direction", "entry", "sl", "tp", "confidence", "meta"]
        )
        # One audit line per predictor.
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
        """A model_id stuck at `research_only` is logged + skipped.
        Other models in the list still fire normally."""
        registry_root = self._register_model(tmp_path, "ok-shadow")
        self._register_model(tmp_path, "stuck-research", stage="research_only")
        log_path = tmp_path / "shadow_audit.jsonl"

        cfg = {
            "shadow_model_ids": ["ok-shadow", "stuck-research"],
            "_shadow_registry_root": str(registry_root),
            "_shadow_log_path": str(log_path),
        }
        package = order_package(cfg, _candles_below_vwap())
        assert "shadow_score" not in package
        lines = [
            json.loads(line)
            for line in log_path.read_text().splitlines()
            if line
        ]
        assert len(lines) == 1
        assert lines[0]["model_id"] == "ok-shadow"

    def test_empty_shadow_model_ids_no_op(self, tmp_path: Path):
        cfg = {"shadow_model_ids": []}
        package = order_package(cfg, _candles_below_vwap())
        assert sorted(package.keys()) == sorted(
            ["symbol", "direction", "entry", "sl", "tp", "confidence", "meta"]
        )

    def test_singular_predictor_still_works(self, tmp_path: Path):
        """PART-3 backward-compat: cfg["_shadow_predictor"] (singular)
        still wires through the new plural path."""
        from ml.predictors import ConstantPredictor

        log_path = tmp_path / "audit.jsonl"
        predictor = ShadowPredictor(
            ConstantPredictor(state={"constant": 0.5}),
            model_id="legacy-singular", stage="shadow",
            log_path=log_path,
        )
        cfg = {"_shadow_predictor": predictor}
        order_package(cfg, _candles_below_vwap())
        lines = log_path.read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["model_id"] == "legacy-singular"
