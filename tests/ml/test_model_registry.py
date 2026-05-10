"""Tests for `ml.registry.model_registry`."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ml.registry.model_registry import (
    ModelRegistry,
    RegistryEntry,
    RegistryError,
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
