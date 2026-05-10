"""Tests for `with_shadow_preds` (S-AI-WS7-PART-4)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping

import pytest

from ml.predictors import Predictor, ShadowPredictor
from src.runtime.shadow_adapter import with_shadow_pred, with_shadow_preds


class _Fixed(Predictor):
    def __init__(self, score: float) -> None:
        self._score = score
        self.calls: list[Mapping[str, Any]] = []

    def predict(self, row: Mapping[str, Any]) -> float:
        self.calls.append(dict(row))
        return self._score


class _Broken(Predictor):
    def predict(self, row: Mapping[str, Any]) -> float:
        raise RuntimeError("boom")


def _shadow(score: float, model_id: str, *, log_path: Path | None = None) -> ShadowPredictor:
    return ShadowPredictor(
        _Fixed(score),
        model_id=model_id,
        stage="shadow",
        log_path=log_path,
    )


def _shadow_broken(model_id: str, *, log_path: Path | None = None) -> ShadowPredictor:
    return ShadowPredictor(
        _Broken(),
        model_id=model_id,
        stage="shadow",
        log_path=log_path,
    )


class TestWithShadowPreds:
    def test_empty_or_none_is_passthrough(self):
        sentinel = object()
        assert with_shadow_preds(
            sentinel, predictors=None, feature_row={},
        ) is sentinel
        assert with_shadow_preds(
            sentinel, predictors=[], feature_row={},
        ) is sentinel

    def test_calls_every_predictor_once(self):
        a = _shadow(0.1, "m-a")
        b = _shadow(0.2, "m-b")
        c = _shadow(0.3, "m-c")
        sentinel = {"x": 1}
        result = with_shadow_preds(
            sentinel, predictors=[a, b, c], feature_row={"k": 1},
        )
        assert result is sentinel
        # Inner predictor saw the row exactly once.
        assert len(a._wrapped.calls) == 1  # type: ignore[attr-defined]
        assert len(b._wrapped.calls) == 1  # type: ignore[attr-defined]
        assert len(c._wrapped.calls) == 1  # type: ignore[attr-defined]

    def test_one_failure_does_not_block_others(self, tmp_path: Path, caplog):
        good_a_log = tmp_path / "a.jsonl"
        good_c_log = tmp_path / "c.jsonl"
        a = _shadow(0.1, "m-good-a", log_path=good_a_log)
        b = _shadow_broken("m-broken-b")
        c = _shadow(0.3, "m-good-c", log_path=good_c_log)
        sentinel = {"x": 1}
        with caplog.at_level(logging.WARNING):
            result = with_shadow_preds(
                sentinel, predictors=[a, b, c], feature_row={"k": 1},
            )
        assert result is sentinel
        # Predictor `a` and `c` audit logs have one entry each.
        assert len(good_a_log.read_text().strip().splitlines()) == 1
        assert len(good_c_log.read_text().strip().splitlines()) == 1
        # `b`'s failure was logged with its model_id.
        assert any(
            "shadow_predict_failed" in r.message and "m-broken-b" in r.message
            for r in caplog.records
        )

    def test_audit_log_per_predictor(self, tmp_path: Path):
        log_a = tmp_path / "a.jsonl"
        log_b = tmp_path / "b.jsonl"
        a = _shadow(0.5, "m-a", log_path=log_a)
        b = _shadow(0.7, "m-b", log_path=log_b)
        with_shadow_preds(
            {"x": 1}, predictors=[a, b], feature_row={"k": 1},
        )
        line_a = json.loads(log_a.read_text().strip())
        line_b = json.loads(log_b.read_text().strip())
        assert line_a["model_id"] == "m-a"
        assert line_a["score"] == pytest.approx(0.5)
        assert line_b["model_id"] == "m-b"
        assert line_b["score"] == pytest.approx(0.7)

    def test_non_shadow_entry_rejected(self):
        bare = _Fixed(0.5)
        with pytest.raises(TypeError, match="ShadowPredictor"):
            with_shadow_preds(
                {"x": 1},
                predictors=[bare],  # type: ignore[list-item]
                feature_row={},
            )

    def test_decision_keys_unchanged(self, tmp_path: Path):
        # Defense-in-depth, plural variant — same property as PART-2's
        # singular check.
        package = {
            "symbol": "BTCUSDT", "direction": "long", "entry": 42_000.0,
            "sl": 41_000.0, "tp": 43_000.0, "confidence": 0.7,
            "meta": {},
        }
        before = sorted(package.keys())
        a = _shadow(999.0, "m-a", log_path=tmp_path / "a.jsonl")
        b = _shadow(-999.0, "m-b", log_path=tmp_path / "b.jsonl")
        result = with_shadow_preds(
            package, predictors=[a, b], feature_row={"k": 1},
        )
        assert sorted(result.keys()) == before
        assert "score" not in result
        assert "shadow_score" not in result


class TestSingularPathStillWorks:
    """`with_shadow_pred` (singular, PART-2/3 API) must continue to
    work — PART-3 vwap tests still call it under the hood until they
    migrate."""

    def test_singular_passthrough_with_none(self):
        sentinel = object()
        assert with_shadow_pred(
            sentinel, predictor=None, feature_row={},
        ) is sentinel

    def test_singular_calls_predictor(self, tmp_path: Path):
        log = tmp_path / "audit.jsonl"
        s = _shadow(0.5, "m-singular", log_path=log)
        with_shadow_pred(
            {"x": 1}, predictor=s, feature_row={"k": 1},
        )
        assert len(log.read_text().strip().splitlines()) == 1
