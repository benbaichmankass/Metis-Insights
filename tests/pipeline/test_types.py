"""Schema invariants for `src/pipeline/types.py` (WS2).

Pins the contract of `TradeCandidate`, `ExecutionIntent`, and
`StageDecision`. Runs as part of the canonical `pytest-collect` CI;
does not exercise any live runtime code.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from src.pipeline import (
    DecisionVerdict,
    Direction,
    ExecutionIntent,
    RejectionSource,
    StageDecision,
    StageName,
    TradeCandidate,
)


def _candidate(**overrides):
    base = dict(
        candidate_id="cand-1",
        strategy="vwap",
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entry=50_000.0,
        stop_loss=49_500.0,
        take_profit=51_000.0,
        confidence=0.75,
    )
    base.update(overrides)
    return TradeCandidate(**base)


def _intent(**overrides):
    base = dict(
        intent_id="int-1",
        candidate_id="cand-1",
        account_id="bybit_main",
        symbol="BTCUSDT",
        direction=Direction.LONG,
        quantity=0.01,
        entry=50_000.0,
        stop_loss=49_500.0,
        take_profit=51_000.0,
        dry_run=True,
    )
    base.update(overrides)
    return ExecutionIntent(**base)


class TestTradeCandidate:
    def test_construct_minimal(self):
        c = _candidate()
        assert c.candidate_id == "cand-1"
        assert c.direction is Direction.LONG
        assert c.created_at.tzinfo is not None

    def test_frozen(self):
        c = _candidate()
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.symbol = "ETHUSDT"  # type: ignore[misc]

    def test_confidence_out_of_range(self):
        with pytest.raises(ValueError):
            _candidate(confidence=1.5)
        with pytest.raises(ValueError):
            _candidate(confidence=-0.1)

    def test_confidence_can_be_none(self):
        c = _candidate(confidence=None)
        assert c.confidence is None

    def test_entry_must_be_positive(self):
        with pytest.raises(ValueError):
            _candidate(entry=0)
        with pytest.raises(ValueError):
            _candidate(entry=-1.0)

    def test_stop_loss_must_be_positive(self):
        with pytest.raises(ValueError):
            _candidate(stop_loss=0)

    def test_model_scores_carry_through(self):
        c = _candidate(model_scores={"setup_quality_v0": 0.62})
        assert c.model_scores["setup_quality_v0"] == 0.62

    def test_model_scores_range_validated(self):
        with pytest.raises(ValueError):
            _candidate(model_scores={"oops": 1.7})

    def test_take_profit_optional(self):
        c = _candidate(take_profit=None)
        assert c.take_profit is None

    def test_explicit_created_at(self):
        when = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
        c = _candidate(created_at=when)
        assert c.created_at == when


class TestExecutionIntent:
    def test_construct_minimal(self):
        i = _intent()
        assert i.intent_id == "int-1"
        assert i.dry_run is True
        assert i.created_at.tzinfo is not None

    def test_quantity_must_be_positive(self):
        with pytest.raises(ValueError):
            _intent(quantity=0)
        with pytest.raises(ValueError):
            _intent(quantity=-1.0)

    def test_entry_must_be_positive(self):
        with pytest.raises(ValueError):
            _intent(entry=0)

    def test_stop_loss_must_be_positive(self):
        with pytest.raises(ValueError):
            _intent(stop_loss=0)

    def test_frozen(self):
        i = _intent()
        with pytest.raises(dataclasses.FrozenInstanceError):
            i.account_id = "different"  # type: ignore[misc]

    def test_take_profit_optional(self):
        i = _intent(take_profit=None)
        assert i.take_profit is None


class TestStageDecision:
    def test_allow(self):
        d = StageDecision(stage=StageName.RISK, verdict=DecisionVerdict.ALLOW)
        assert d.rejection_source is None
        assert d.verdict is DecisionVerdict.ALLOW

    def test_veto_requires_rejection_source(self):
        with pytest.raises(ValueError):
            StageDecision(stage=StageName.RISK, verdict=DecisionVerdict.VETO)

    def test_veto_with_deterministic_source(self):
        d = StageDecision(
            stage=StageName.RISK,
            verdict=DecisionVerdict.VETO,
            rejection_source=RejectionSource.DETERMINISTIC,
            reason="daily_usd_cap exceeded",
        )
        assert d.rejection_source is RejectionSource.DETERMINISTIC

    def test_veto_with_model_source(self):
        d = StageDecision(
            stage=StageName.SCORE,
            verdict=DecisionVerdict.VETO,
            rejection_source=RejectionSource.MODEL,
            reason="outcome probability below threshold",
            model_id="outcome-prob-v0",
            score=0.12,
        )
        assert d.rejection_source is RejectionSource.MODEL
        assert d.model_id == "outcome-prob-v0"

    def test_score_only(self):
        d = StageDecision(
            stage=StageName.SCORE,
            verdict=DecisionVerdict.SCORE_ONLY,
            score=0.62,
            model_id="setup-quality-baseline-v0",
        )
        assert d.score == 0.62
        assert d.rejection_source is None

    def test_score_out_of_range(self):
        with pytest.raises(ValueError):
            StageDecision(
                stage=StageName.SCORE,
                verdict=DecisionVerdict.SCORE_ONLY,
                score=1.5,
            )
        with pytest.raises(ValueError):
            StageDecision(
                stage=StageName.SCORE,
                verdict=DecisionVerdict.SCORE_ONLY,
                score=-0.1,
            )

    def test_frozen(self):
        d = StageDecision(stage=StageName.RISK, verdict=DecisionVerdict.ALLOW)
        with pytest.raises(dataclasses.FrozenInstanceError):
            d.reason = "changed"  # type: ignore[misc]


class TestStageName:
    def test_ten_stages(self):
        assert len(list(StageName)) == 10

    def test_str_value(self):
        assert StageName.RISK.value == "risk"
