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

    def test_register_appends_run_on_duplicate(self, tmp_path: Path):
        """Re-registering the same model_id used to raise. Now it appends
        a new RunRecord and refreshes top-level metrics — daily-cadence
        re-trains accumulate training history under a stable model_id.
        """
        reg = ModelRegistry(tmp_path)
        first = reg.register(
            model_id="m-1", manifest={}, model_state_path="/tmp/s1.json",
            metrics={"mse": 0.5}, code_revision="rev1", run_id="20260514T120000Z",
        )
        assert len(first.runs) == 1
        assert first.runs[0].run_id == "20260514T120000Z"
        assert first.metrics["mse"] == 0.5

        second = reg.register(
            model_id="m-1", manifest={}, model_state_path="/tmp/s2.json",
            metrics={"mse": 0.3}, code_revision="rev2", run_id="20260515T120000Z",
        )
        # New run appended; top-level metrics now reflect the latest run.
        assert len(second.runs) == 2
        assert second.runs[0].run_id == "20260514T120000Z"
        assert second.runs[1].run_id == "20260515T120000Z"
        assert second.metrics["mse"] == 0.3
        assert second.model_state_path == "/tmp/s2.json"
        assert second.code_revision == "rev2"
        # Status + created_at preserved across re-trains.
        assert second.status == first.status
        assert second.created_at == first.created_at
        # History grew with a re-trained event.
        assert len(second.history) == 2
        assert "re-trained" in second.history[-1].reason

    def test_register_idempotent_on_same_run_id(self, tmp_path: Path):
        """Re-registering the same (model_id, run_id) is a no-op — guards
        against double-publishing if a cycle retries before completing.
        """
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={}, model_state_path="/tmp/s.json",
            metrics={"mse": 0.1}, code_revision="rev1", run_id="run-a",
        )
        again = reg.register(
            model_id="m-1", manifest={}, model_state_path="/tmp/s.json",
            metrics={"mse": 0.99}, code_revision="rev2", run_id="run-a",
        )
        assert len(again.runs) == 1
        # Top-level didn't change because the run was deduplicated.
        assert again.metrics["mse"] == 0.1

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

    def test_promote_preserves_run_history(self, tmp_path: Path):
        """S-AUDIT-G B1 regression: promote()/promote_stage() must NOT wipe
        the model's training-run history. RegistryEntry.runs defaults to ()
        (field(default_factory=tuple)), so before the fix the promote rebuilds
        omitted runs= and silently reset it to () — the cross_run_stability
        promotion gate reads entry.runs, so a promoted model lost its
        stability evidence.
        """
        reg = ModelRegistry(tmp_path)
        # status promote must keep both runs
        reg.register(
            model_id="m-1", manifest={}, model_state_path="/tmp/s1.json",
            metrics={"mse": 0.5}, code_revision="rev1", run_id="run-a",
        )
        reg.register(
            model_id="m-1", manifest={}, model_state_path="/tmp/s2.json",
            metrics={"mse": 0.3}, code_revision="rev2", run_id="run-b",
        )
        assert len(reg.get("m-1").runs) == 2
        after_status = reg.promote("m-1", "paper", by="op", reason="leakage_passed")
        assert [r.run_id for r in after_status.runs] == ["run-a", "run-b"]
        assert len(reg.get("m-1").runs) == 2  # persisted to disk

        # stage promote must keep them too
        reg.register(
            model_id="m-2", manifest={}, model_state_path="/tmp/t1.json",
            metrics={"mse": 0.5}, code_revision="rev1", run_id="run-c",
        )
        reg.register(
            model_id="m-2", manifest={}, model_state_path="/tmp/t2.json",
            metrics={"mse": 0.3}, code_revision="rev2", run_id="run-d",
        )
        after_stage = reg.promote_stage("m-2", "advisory", by="op", reason="soak_ok")
        assert [r.run_id for r in after_stage.runs] == ["run-c", "run-d"]
        assert len(reg.get("m-2").runs) == 2  # persisted to disk

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

    def test_default_stage_is_shadow(self, tmp_path: Path):
        # 2026-05-19: the default lifecycle is "every trained model
        # lives in shadow." A manifest that omits
        # `target_deployment_stage` lands the model in shadow, not
        # `research_only`.
        reg = ModelRegistry(tmp_path)
        entry = reg.register(
            model_id="m-1", manifest={"manifest_version": "v1"},
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        assert entry.target_deployment_stage == "shadow"
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
        # Canonical forward edge candidate → shadow. Register a model into
        # `candidate` (pre-shadow) explicitly (the default is `shadow`).
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1",
            manifest={
                "manifest_version": "v1",
                "target_deployment_stage": "candidate",
            },
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        updated = reg.promote_stage(
            "m-1", "shadow", by="op", reason="leakage_test_clean",
        )
        assert updated.target_deployment_stage == "shadow"
        assert len(updated.stage_history) == 1
        assert updated.stage_history[-1].from_stage == "candidate"
        assert updated.stage_history[-1].to_stage == "shadow"

    def test_promote_stage_rollback(self, tmp_path: Path):
        # candidate → shadow → advisory → shadow (one-step rollback).
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1",
            manifest={
                "manifest_version": "v1",
                "target_deployment_stage": "candidate",
            },
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        reg.promote_stage(
            "m-1", "shadow", by="op", reason="leakage_test_clean",
        )
        reg.promote_stage(
            "m-1", "advisory", by="op", reason="walk_forward_ok",
        )
        rolled = reg.promote_stage(
            "m-1", "shadow", by="op", reason="live_underperformed",
        )
        assert rolled.target_deployment_stage == "shadow"
        assert len(rolled.stage_history) == 3

    def test_promote_stage_alias_research_only_normalizes_to_candidate(
        self, tmp_path: Path
    ):
        # Backward-compat: registering with the legacy `research_only` name
        # normalizes to canonical `candidate` on store, and the forward edge
        # to `shadow` still works in one hop.
        reg = ModelRegistry(tmp_path)
        entry = reg.register(
            model_id="m-1",
            manifest={
                "manifest_version": "v1",
                "target_deployment_stage": "research_only",
            },
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        assert entry.target_deployment_stage == "candidate"
        updated = reg.promote_stage(
            "m-1", "shadow", by="op", reason="default-policy",
        )
        assert updated.target_deployment_stage == "shadow"
        assert updated.stage_history[-1].from_stage == "candidate"
        assert updated.stage_history[-1].to_stage == "shadow"

    def test_promote_stage_shadow_can_demote_to_candidate(
        self, tmp_path: Path
    ):
        # A misbehaving shadow model can be parked back in `candidate`
        # (pre-shadow) in one hop, so the audit log captures the demotion
        # intent cleanly. A legacy `research_only` request normalizes to it.
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={"manifest_version": "v1"},
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        assert reg.get("m-1").target_deployment_stage == "shadow"
        rolled = reg.promote_stage(
            "m-1",
            "research_only",  # legacy alias → candidate
            by="op",
            reason="park-pending-investigation",
        )
        assert rolled.target_deployment_stage == "candidate"

    def test_promote_stage_unknown(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={"manifest_version": "v1"},
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        with pytest.raises(RegistryError, match="must be one of"):
            reg.promote_stage("m-1", "made-up", by="op", reason="x")

    def test_promote_stage_no_op_refused(self, tmp_path: Path):
        # Already in shadow (the new default) — promote_stage to shadow
        # should raise, not silently noop. Audit log integrity matters.
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={"manifest_version": "v1"},
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        with pytest.raises(RegistryError, match="no-op"):
            reg.promote_stage(
                "m-1", "shadow", by="op", reason="hold",
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
        # Walk a model up the canonical 3-stage ladder candidate → shadow →
        # advisory. Start from `candidate` (default is `shadow`).
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1",
            manifest={
                "manifest_version": "v1",
                "target_deployment_stage": "candidate",
            },
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )
        ladder = ["shadow", "advisory"]
        for stage in ladder:
            reg.promote_stage(
                "m-1", stage, by="op", reason=f"promoted-to-{stage}",
            )
        final = reg.get("m-1")
        assert final.target_deployment_stage == "advisory"
        assert len(final.stage_history) == 2
        assert (
            tuple(e.to_stage for e in final.stage_history)
            == tuple(ladder)
        )

    def test_promote_stage_legacy_alias_accepted(self, tmp_path: Path):
        # Backward-compat: a caller passing a legacy influence-stage name
        # (`live_approved`) is accepted and stored as canonical `advisory`,
        # not hard-broken.
        reg = ModelRegistry(tmp_path)
        reg.register(
            model_id="m-1", manifest={"manifest_version": "v1"},
            model_state_path="/tmp/s.json", metrics={}, code_revision="x",
        )  # default shadow
        updated = reg.promote_stage(
            "m-1", "live_approved", by="op", reason="promote-to-influence",
        )
        assert updated.target_deployment_stage == "advisory"

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
    # 3-stage collapse (2026-06-16): canonical stages are candidate / shadow
    # / advisory. The legacy 7-stage names are no longer canonical — they
    # resolve via the alias map (`ml.manifest.STAGE_ALIASES`).
    assert "candidate" in VALID_DEPLOYMENT_STAGES
    assert "shadow" in VALID_DEPLOYMENT_STAGES
    assert "advisory" in VALID_DEPLOYMENT_STAGES
    assert len(set(VALID_DEPLOYMENT_STAGES)) == len(VALID_DEPLOYMENT_STAGES)
    assert len(VALID_DEPLOYMENT_STAGES) == 3
