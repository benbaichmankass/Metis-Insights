"""Tests for `ml.registry.model_registry`."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ml.manifest import VALID_DEPLOYMENT_STAGES
from ml.registry.model_registry import (
    ModelRegistry,
    RegistryEntry,
    RegistryError,
    StageEvent,
    StatusEvent,
    VALID_STATUSES,
)


def _entry(**overrides):
    base = dict(
        model_id="m-1",
        status="candidate",
        manifest={"manifest_version": "v1"},
        model_state_path="/tmp/state.json",
        metrics={"mse": 0.1},
        code_revision="deadbeef",
        created_at=datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return RegistryEntry(**base)


class TestRegistryEntry:
    def test_valid(self):
        e = _entry()
        assert e.status == "candidate"
        assert e.history == ()

    def test_invalid_status(self):
        with pytest.raises(RegistryError):
            _entry(status="something")

    def test_blank_model_id(self):
        with pytest.raises(RegistryError):
            _entry(model_id="   ")

    def test_round_trip(self):
        ev = StatusEvent(
            from_status=None,
            to_status="candidate",
            by="x",
            reason="r",
            at=datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc),
        )
        e = _entry(history=(ev,))
        d = e.to_dict()
        e2 = RegistryEntry.from_dict(d)
        assert e2.status == "candidate"
        assert len(e2.history) == 1
        assert e2.history[0].to_status == "candidate"


class TestModelRegistry:
    def test_register_creates_candidate(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        entry = reg.register(
            model_id="m-1",
            manifest={"manifest_version": "v1"},
            model_state_path="/tmp/s.json",
            metrics={"mse": 0.1},
            code_revision="deadbeef",
        )
        assert entry.status == "candidate"
        assert reg.exists("m-1")
        assert reg.get("m-1").metrics["mse"] == 0.1
        assert len(entry.history) == 1
        assert entry.history[0].to_status == "candidate"

    def test_register_rejects_duplicate(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={}, model_state_path="/tmp/s.json",
            metrics={}, code_revision="x",
        )
        with pytest.raises(RegistryError):
            reg.register(
                model_id="m-1", manifest={}, model_state_path="/tmp/s.json",
                metrics={}, code_revision="x",
            )

    def test_promote_allowed_transition(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={}, model_state_path="/tmp/s.json",
            metrics={}, code_revision="x",
        )
        updated = reg.promote(
            "m-1", "paper", by="op", reason="leakage_passed"
        )
        assert updated.status == "paper"
        assert len(updated.history) == 2
        assert updated.history[-1].from_status == "candidate"

    def test_promote_disallowed_transition(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={}, model_state_path="/tmp/s.json",
            metrics={}, code_revision="x",
        )
        with pytest.raises(RegistryError):
            reg.promote(
                "m-1", "live-approved", by="op", reason="skip-gate"
            )

    def test_promote_unknown_status(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={}, model_state_path="/tmp/s.json",
            metrics={}, code_revision="x",
        )
        with pytest.raises(RegistryError):
            reg.promote("m-1", "made-up", by="op", reason="x")

    def test_list_filters_by_status(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={}, model_state_path="/tmp/s.json",
            metrics={}, code_revision="x",
        )
        reg.register(
            model_id="m-2", manifest={}, model_state_path="/tmp/s.json",
            metrics={}, code_revision="x",
        )
        reg.promote("m-2", "paper", by="op", reason="ok")
        candidates = reg.list(status="candidate")
        assert {e.model_id for e in candidates} == {"m-1"}

    def test_get_missing(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        with pytest.raises(RegistryError):
            reg.get("missing")


def test_valid_statuses_complete():
    assert "candidate" in VALID_STATUSES
    assert "live-approved" in VALID_STATUSES
    assert len(set(VALID_STATUSES)) == len(VALID_STATUSES)


class TestDeploymentStage:
    """WS7-PART-1: target_deployment_stage on RegistryEntry."""

    def test_default_stage_is_research_only(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        entry = reg.register(
            model_id="m-1", manifest={"manifest_version": "v1"},
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        assert entry.target_deployment_stage == "research_only"
        assert entry.stage_history == ()

    def test_register_picks_up_manifest_stage(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        entry = reg.register(
            model_id="m-1",
            manifest={
                "manifest_version": "v1",
                "target_deployment_stage": "candidate",
            },
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        assert entry.target_deployment_stage == "candidate"

    def test_register_rejects_unknown_manifest_stage(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        with pytest.raises(RegistryError, match="target_deployment_stage"):
            reg.register(
                model_id="m-1",
                manifest={
                    "manifest_version": "v1",
                    "target_deployment_stage": "made-up",
                },
                model_state_path="/tmp/s.json", metrics={}, code_revision="x",
            )

    def test_round_trip_preserves_stage_and_history(self, tmp_path: Path):
        ev = StageEvent(
            from_stage="research_only", to_stage="candidate",
            by="op", reason="leakage_test_clean",
            at=datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc),
        )
        e = _entry(target_deployment_stage="candidate", stage_history=(ev,))
        d = e.to_dict()
        e2 = RegistryEntry.from_dict(d)
        assert e2.target_deployment_stage == "candidate"
        assert len(e2.stage_history) == 1
        assert e2.stage_history[0].to_stage == "candidate"

    def test_promote_stage_forward(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={"manifest_version": "v1"},
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        updated = reg.promote_stage(
            "m-1", "candidate", by="op", reason="leakage_test_clean",
        )
        assert updated.target_deployment_stage == "candidate"
        assert len(updated.stage_history) == 1
        assert updated.stage_history[-1].from_stage == "research_only"
        assert updated.stage_history[-1].to_stage == "candidate"

    def test_promote_stage_rollback(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={"manifest_version": "v1"},
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        reg.promote_stage(
            "m-1", "candidate", by="op", reason="leakage_test_clean",
        )
        reg.promote_stage(
            "m-1", "backtest_approved", by="op", reason="walk_forward_ok",
        )
        rolled = reg.promote_stage(
            "m-1", "candidate", by="op", reason="walk_forward_regressed",
        )
        assert rolled.target_deployment_stage == "candidate"
        assert len(rolled.stage_history) == 3

    def test_promote_stage_disallowed_skip(self, tmp_path: Path):
        # Skipping `candidate` from `research_only` straight to `shadow`
        # is not a legal transition.
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={"manifest_version": "v1"},
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        with pytest.raises(RegistryError, match="not allowed"):
            reg.promote_stage("m-1", "shadow", by="op", reason="skip-gate")

    def test_promote_stage_unknown(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={"manifest_version": "v1"},
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        with pytest.raises(RegistryError, match="must be one of"):
            reg.promote_stage("m-1", "made-up", by="op", reason="x")

    def test_promote_stage_no_op_refused(self, tmp_path: Path):
        # Already in research_only — promote_stage to research_only should
        # raise, not silently noop. Audit log integrity matters.
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={"manifest_version": "v1"},
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        with pytest.raises(RegistryError, match="no-op"):
            reg.promote_stage(
                "m-1", "research_only", by="op", reason="hold",
            )

    def test_promote_stage_blank_by_or_reason(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={"manifest_version": "v1"},
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        with pytest.raises(RegistryError, match="by"):
            reg.promote_stage(
                "m-1", "candidate", by="   ", reason="x",
            )
        with pytest.raises(RegistryError, match="reason"):
            reg.promote_stage(
                "m-1", "candidate", by="op", reason="",
            )

    def test_promote_stage_full_ladder(self, tmp_path: Path):
        # Walk a model all the way up the WS7 ladder.
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={"manifest_version": "v1"},
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        ladder = [
            "candidate",
            "backtest_approved",
            "shadow",
            "advisory",
            "limited_live",
            "live_approved",
        ]
        for stage in ladder:
            reg.promote_stage(
                "m-1", stage, by="op", reason=f"promoted-to-{stage}",
            )
        final = reg.get("m-1")
        assert final.target_deployment_stage == "live_approved"
        assert len(final.stage_history) == 6
        assert (
            tuple(e.to_stage for e in final.stage_history)
            == tuple(ladder)
        )

    def test_status_promote_preserves_stage(self, tmp_path: Path):
        # Legacy status promote() should not disturb target_deployment_stage.
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1",
            manifest={
                "manifest_version": "v1",
                "target_deployment_stage": "candidate",
            },
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        updated = reg.promote(
            "m-1", "paper", by="op", reason="leakage_test_clean",
        )
        assert updated.status == "paper"
        assert updated.target_deployment_stage == "candidate"


def test_valid_deployment_stages_complete():
    assert "research_only" in VALID_DEPLOYMENT_STAGES
    assert "live_approved" in VALID_DEPLOYMENT_STAGES
    assert len(set(VALID_DEPLOYMENT_STAGES)) == len(VALID_DEPLOYMENT_STAGES)
    assert len(VALID_DEPLOYMENT_STAGES) == 7
