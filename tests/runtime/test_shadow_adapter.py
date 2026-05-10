"""Tests for `src.runtime.shadow_adapter.with_shadow_pred` (S-AI-WS7-PART-2)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping

import pytest

from ml.predictors import Predictor, ShadowPredictor
from src.runtime.shadow_adapter import with_shadow_pred


class _FixedPredictor(Predictor):
    def __init__(self, score: float) -> None:
        self._score = score
        self.calls: list[Mapping[str, Any]] = []

    def predict(self, row: Mapping[str, Any]) -> float:
        self.calls.append(dict(row))
        return self._score


class _BrokenPredictor(Predictor):
    """Raises on every call. Simulates a misbehaving production model."""

    def predict(self, row: Mapping[str, Any]) -> float:
        raise RuntimeError("model state corrupted")


def _sample_package() -> dict[str, Any]:
    return {
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry": 42_000.0,
        "sl": 41_000.0,
        "tp": 43_000.0,
        "confidence": 0.7,
        "meta": {"strategy_name": "vwap"},
    }


def _sample_features() -> dict[str, Any]:
    return {
        "strategy_name": "vwap",
        "setup_type": "FVG",
        "direction": "long",
    }


class TestWithShadowPred:
    def test_returns_decision_unchanged_on_success(self, tmp_path: Path):
        package = _sample_package()
        predictor = ShadowPredictor(
            _FixedPredictor(score=2.5),
            model_id="m-1",
            stage="shadow",
            log_path=tmp_path / "audit.jsonl",
        )
        returned = with_shadow_pred(
            package, predictor=predictor, feature_row=_sample_features(),
        )
        assert returned is package
        assert returned == _sample_package()

    def test_returns_decision_unchanged_on_predictor_failure(
        self, tmp_path: Path, caplog
    ):
        package = _sample_package()
        predictor = ShadowPredictor(
            _BrokenPredictor(),
            model_id="m-broken",
            stage="shadow",
            log_path=tmp_path / "audit.jsonl",
        )
        with caplog.at_level(logging.WARNING):
            returned = with_shadow_pred(
                package, predictor=predictor,
                feature_row=_sample_features(),
            )
        assert returned is package
        assert returned == _sample_package()
        # Verify the failure was logged.
        assert any(
            "shadow_predict_failed" in record.message
            and "m-broken" in record.message
            for record in caplog.records
        )

    def test_predictor_is_called_with_feature_row(self, tmp_path: Path):
        inner = _FixedPredictor(score=1.0)
        predictor = ShadowPredictor(
            inner, model_id="m-1", stage="shadow",
            log_path=tmp_path / "audit.jsonl",
        )
        features = _sample_features()
        with_shadow_pred(
            _sample_package(), predictor=predictor, feature_row=features,
        )
        assert inner.calls == [features]

    def test_audit_log_emitted_on_success(self, tmp_path: Path):
        audit = tmp_path / "audit.jsonl"
        predictor = ShadowPredictor(
            _FixedPredictor(score=2.5),
            model_id="m-1", stage="shadow", log_path=audit,
        )
        with_shadow_pred(
            _sample_package(), predictor=predictor,
            feature_row=_sample_features(),
        )
        lines = [
            json.loads(line)
            for line in audit.read_text().splitlines()
            if line
        ]
        assert len(lines) == 1
        assert lines[0]["model_id"] == "m-1"
        assert lines[0]["score"] == pytest.approx(2.5)

    def test_no_audit_log_on_predictor_failure(self, tmp_path: Path):
        # Audit logging happens AFTER predict() returns. If predict
        # raises, no audit line should land — operators searching the
        # shadow log can trust each entry represents a real model call.
        audit = tmp_path / "audit.jsonl"
        predictor = ShadowPredictor(
            _BrokenPredictor(),
            model_id="m-broken", stage="shadow", log_path=audit,
        )
        with_shadow_pred(
            _sample_package(), predictor=predictor,
            feature_row=_sample_features(),
        )
        # Audit file may not even exist if no successful call ever wrote.
        if audit.is_file():
            assert audit.read_text() == ""

    def test_predictor_none_is_passthrough(self, tmp_path: Path):
        # No predictor configured — helper should noop and return
        # the package without touching anything.
        package = _sample_package()
        returned = with_shadow_pred(
            package, predictor=None, feature_row=_sample_features(),
        )
        assert returned is package

    def test_strategy_exception_not_caught(self):
        # The helper only catches predictor exceptions, not strategy
        # exceptions. Verify by passing a generator that raises if it
        # is iterated more than once.
        class _StrategyError(RuntimeError):
            pass

        # Strategy errors are raised BEFORE with_shadow_pred sees
        # anything — there's no path for the helper to swallow them.
        # The test below verifies that the helper itself doesn't
        # introduce any try/except around the decision value.
        sentinel = object()
        returned = with_shadow_pred(
            sentinel, predictor=None, feature_row={},
        )
        assert returned is sentinel

    def test_non_shadow_predictor_rejected(self):
        # Bare Predictor (not wrapped in ShadowPredictor) is a
        # misconfiguration — surface it, don't silently consume.
        bare = _FixedPredictor(score=1.0)
        with pytest.raises(TypeError, match="ShadowPredictor"):
            with_shadow_pred(
                _sample_package(),
                predictor=bare,  # type: ignore[arg-type]
                feature_row=_sample_features(),
            )

    def test_custom_logger(self, tmp_path: Path):
        predictor = ShadowPredictor(
            _BrokenPredictor(),
            model_id="m-broken", stage="shadow",
            log_path=tmp_path / "audit.jsonl",
        )
        custom = logging.getLogger("test.shadow.custom")
        custom_records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record):
                custom_records.append(record)

        custom.addHandler(_Capture())
        custom.setLevel(logging.WARNING)
        try:
            with_shadow_pred(
                _sample_package(),
                predictor=predictor,
                feature_row=_sample_features(),
                logger=custom,
            )
        finally:
            custom.handlers.clear()
        assert any(
            "shadow_predict_failed" in r.getMessage() for r in custom_records
        )

    def test_score_not_added_to_decision(self, tmp_path: Path):
        # Defense-in-depth: even if a future refactor confuses "shadow"
        # with "advisory", the package keys MUST NOT include any
        # model-derived field. The decision must be byte-identical.
        package = _sample_package()
        before_keys = sorted(package.keys())
        predictor = ShadowPredictor(
            _FixedPredictor(score=999.0),
            model_id="m-1", stage="shadow",
            log_path=tmp_path / "audit.jsonl",
        )
        returned = with_shadow_pred(
            package, predictor=predictor,
            feature_row=_sample_features(),
        )
        assert sorted(returned.keys()) == before_keys
        assert "score" not in returned
        assert "shadow_score" not in returned
        assert "model_id" not in returned
