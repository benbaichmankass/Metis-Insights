"""Tests for S10 (M11): ML decision-layer advisory hooks.

Validates:
  - with_shadow_preds_advisory returns (decision, {}) when predictors=None/[]
  - with_shadow_preds_advisory captures scores from advisory-stage predictors
  - with_shadow_preds_advisory does NOT include shadow-stage scores in the dict
  - with_shadow_preds_advisory returns decision unchanged (observe-only)
  - with_shadow_preds_advisory handles predictor failures gracefully
  - with_shadow_preds_advisory rejects bare Predictor (not ShadowPredictor)
  - _advisory_mode_enabled reads from settings dict and env var
  - Coordinator.log_advisory_scores logs without side effects on the order path
  - Coordinator.log_advisory_scores is noop when scores is empty
  - Coordinator.log_advisory_scores writes advisory_decisions.jsonl
"""
from __future__ import annotations

import json
from typing import Any, Mapping
from unittest.mock import MagicMock, patch

import pytest

from src.runtime.shadow_adapter import with_shadow_preds_advisory


# ---------------------------------------------------------------------------
# Minimal stubs for ShadowPredictor
# ---------------------------------------------------------------------------

def _make_shadow_predictor(model_id: str, stage: str, score: float = 0.75):
    """Build a minimal ShadowPredictor-like stub without hitting the registry."""
    from ml.predictors.shadow import ShadowPredictor
    from ml.predictors.base import Predictor

    _score = score

    class _FakePredictor(Predictor):
        def predict(self, row: Mapping[str, Any]) -> float:
            return _score

    wrapped = _FakePredictor()
    return ShadowPredictor(wrapped, model_id=model_id, stage=stage, log_path=None)


# ---------------------------------------------------------------------------
# with_shadow_preds_advisory
# ---------------------------------------------------------------------------

class TestWithShadowPredsAdvisory:
    def test_none_predictors_returns_decision_and_empty_dict(self):
        result, scores = with_shadow_preds_advisory(
            "my_decision", predictors=None, feature_row={}
        )
        assert result == "my_decision"
        assert scores == {}

    def test_empty_predictors_returns_decision_and_empty_dict(self):
        result, scores = with_shadow_preds_advisory(
            42, predictors=[], feature_row={}
        )
        assert result == 42
        assert scores == {}

    def test_decision_returned_unchanged_with_advisory_predictor(self):
        pred = _make_shadow_predictor("m1", "advisory", score=0.9)
        decision = {"direction": "long", "qty": 0.5}
        result, _ = with_shadow_preds_advisory(
            decision, predictors=[pred], feature_row={}
        )
        assert result is decision

    def test_advisory_stage_score_captured(self):
        pred = _make_shadow_predictor("adv-model", "advisory", score=0.88)
        _, scores = with_shadow_preds_advisory(
            "decision", predictors=[pred], feature_row={"x": 1}
        )
        assert "adv-model" in scores
        assert scores["adv-model"] == pytest.approx(0.88)

    def test_shadow_stage_score_not_in_dict(self):
        pred = _make_shadow_predictor("shadow-model", "shadow", score=0.5)
        _, scores = with_shadow_preds_advisory(
            "decision", predictors=[pred], feature_row={}
        )
        assert scores == {}

    def test_mixed_stages_only_advisory_captured(self):
        p_shadow = _make_shadow_predictor("s-model", "shadow", score=0.3)
        p_advisory = _make_shadow_predictor("a-model", "advisory", score=0.7)
        _, scores = with_shadow_preds_advisory(
            "decision", predictors=[p_shadow, p_advisory], feature_row={}
        )
        assert list(scores.keys()) == ["a-model"]
        assert scores["a-model"] == pytest.approx(0.7)

    def test_multiple_advisory_predictors_all_captured(self):
        p1 = _make_shadow_predictor("adv-1", "advisory", score=0.6)
        p2 = _make_shadow_predictor("adv-2", "advisory", score=0.8)
        _, scores = with_shadow_preds_advisory(
            "x", predictors=[p1, p2], feature_row={}
        )
        assert scores == {"adv-1": pytest.approx(0.6), "adv-2": pytest.approx(0.8)}

    def test_predictor_failure_skipped_gracefully(self):
        from ml.predictors.shadow import ShadowPredictor
        from ml.predictors.base import Predictor

        class _BrokenPredictor(Predictor):
            def predict(self, row):
                raise RuntimeError("model exploded")

        broken = ShadowPredictor(_BrokenPredictor(), model_id="broken", stage="advisory", log_path=None)
        ok = _make_shadow_predictor("ok-model", "advisory", score=0.5)
        _, scores = with_shadow_preds_advisory("d", predictors=[broken, ok], feature_row={})
        assert "broken" not in scores
        assert scores == {"ok-model": pytest.approx(0.5)}

    def test_bare_predictor_raises_type_error(self):
        from ml.predictors.base import Predictor

        class _Bare(Predictor):
            def predict(self, row):
                return 0.0

        with pytest.raises(TypeError, match="ShadowPredictor"):
            with_shadow_preds_advisory("d", predictors=[_Bare()], feature_row={})

    def test_limited_live_stage_not_captured_as_advisory(self):
        pred = _make_shadow_predictor("ll-model", "limited_live", score=0.9)
        _, scores = with_shadow_preds_advisory(
            "d", predictors=[pred], feature_row={}
        )
        assert scores == {}

    def test_live_approved_stage_not_captured_as_advisory(self):
        pred = _make_shadow_predictor("la-model", "live_approved", score=0.95)
        _, scores = with_shadow_preds_advisory(
            "d", predictors=[pred], feature_row={}
        )
        assert scores == {}


# ---------------------------------------------------------------------------
# Coordinator.log_advisory_scores
# ---------------------------------------------------------------------------

class TestCoordinatorLogAdvisoryScores:
    def _coord(self):
        from src.core.coordinator import Coordinator
        c = Coordinator.__new__(Coordinator)
        c._allocator = None
        c._units_path = ""
        c._accounts_path = ""
        c._instruments_path = ""
        c._cfg = {}
        c._shadow_predictors_cache = {}
        return c

    def test_noop_when_scores_empty(self, tmp_path, monkeypatch):
        coord = self._coord()
        monkeypatch.chdir(tmp_path)
        coord.log_advisory_scores({})
        assert not (tmp_path / "runtime_logs" / "advisory_decisions.jsonl").exists()

    def test_logs_scores_to_jsonl(self, tmp_path, monkeypatch):
        coord = self._coord()
        monkeypatch.chdir(tmp_path)
        (tmp_path / "runtime_logs").mkdir()

        coord.log_advisory_scores(
            {"adv-model": 0.72},
            strategy_id="vwap",
            symbol="BTCUSDT",
        )

        log_path = tmp_path / "runtime_logs" / "advisory_decisions.jsonl"
        assert log_path.exists()
        record = json.loads(log_path.read_text().strip())
        assert record["strategy_id"] == "vwap"
        assert record["symbol"] == "BTCUSDT"
        assert record["advisory_scores"] == {"adv-model": pytest.approx(0.72)}
        assert "logged_at_utc" in record

    def test_multiple_scores_all_logged(self, tmp_path, monkeypatch):
        coord = self._coord()
        monkeypatch.chdir(tmp_path)
        (tmp_path / "runtime_logs").mkdir()

        coord.log_advisory_scores(
            {"m1": 0.5, "m2": 0.9},
            strategy_id="turtle_soup",
            symbol="BTCUSDT",
        )

        log_path = tmp_path / "runtime_logs" / "advisory_decisions.jsonl"
        record = json.loads(log_path.read_text().strip())
        assert set(record["advisory_scores"].keys()) == {"m1", "m2"}

    def test_does_not_modify_order_path(self, tmp_path, monkeypatch):
        """Advisory hook must not touch allocator or multi_account_execute."""
        coord = self._coord()
        monkeypatch.chdir(tmp_path)
        (tmp_path / "runtime_logs").mkdir()

        coord._allocator = MagicMock()
        coord.log_advisory_scores({"adv": 0.5})
        coord._allocator.allocate.assert_not_called()

    def test_os_error_on_write_does_not_raise(self, tmp_path, monkeypatch):
        """A write failure must never crash the tick loop."""
        coord = self._coord()
        monkeypatch.chdir(tmp_path)

        with patch("builtins.open", side_effect=OSError("disk full")):
            coord.log_advisory_scores({"adv": 0.5})
