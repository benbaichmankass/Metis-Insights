"""Tests for ``ml.registry.reconcile``."""
from __future__ import annotations

import json
from pathlib import Path

from ml.registry.model_registry import ModelRegistry
from ml.registry.reconcile import (
    _BACKFILL_BY,
    _UNKNOWN_REVISION,
    reconcile_all,
    reconcile_model,
)


def _write_run_dir(experiments_root: Path, model_id: str, run_id: str, metrics: dict) -> Path:
    run_dir = experiments_root / model_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (run_dir / "model_state.json").write_text("{}", encoding="utf-8")
    (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
    return run_dir


class TestReconcile:
    def test_backfills_runs_missing_from_entry(self, tmp_path: Path):
        """Entry shows runs=[r2] (pre-#1133 migration shape), disk has r1+r2.

        Reconcile should prepend r1, leave r2's existing RunRecord intact.
        """
        registry_root = tmp_path / "registry"
        experiments_root = tmp_path / "experiments"
        registry = ModelRegistry(registry_root)

        # Two runs on disk.
        _write_run_dir(experiments_root, "m-1", "20260513T120000Z", {"mse": 0.5})
        _write_run_dir(experiments_root, "m-1", "20260514T120000Z", {"mse": 0.3})

        # Entry knows about only the second one (simulates post-#1133 first re-train).
        registry.register(
            model_id="m-1",
            manifest={},
            model_state_path="/tmp/s2.json",
            metrics={"mse": 0.3},
            code_revision="rev2",
            run_id="20260514T120000Z",
        )

        result = reconcile_model(
            model_id="m-1",
            registry=registry,
            experiments_root=experiments_root,
        )
        assert result.before_runs == 1
        assert result.after_runs == 2
        assert result.added_run_ids == ("20260513T120000Z",)
        assert result.wrote is True

        # Verify on-disk state.
        reloaded = registry.get("m-1")
        assert [r.run_id for r in reloaded.runs] == [
            "20260513T120000Z",
            "20260514T120000Z",
        ]
        # The synthesized first run carries the backfill markers.
        first = reloaded.runs[0]
        assert first.code_revision == _UNKNOWN_REVISION
        assert first.by == _BACKFILL_BY
        assert first.metrics == {"mse": 0.5}
        # The existing run is preserved verbatim (rev2 / experiments-runner).
        second = reloaded.runs[1]
        assert second.code_revision == "rev2"
        assert second.by == "experiments-runner"
        assert second.metrics == {"mse": 0.3}

    def test_idempotent_when_in_sync(self, tmp_path: Path):
        registry_root = tmp_path / "registry"
        experiments_root = tmp_path / "experiments"
        registry = ModelRegistry(registry_root)
        _write_run_dir(experiments_root, "m-1", "20260514T120000Z", {"mse": 0.3})
        registry.register(
            model_id="m-1", manifest={}, model_state_path="/tmp/s.json",
            metrics={"mse": 0.3}, code_revision="rev1",
            run_id="20260514T120000Z",
        )

        # First call: nothing to add.
        r1 = reconcile_model(
            model_id="m-1", registry=registry, experiments_root=experiments_root,
        )
        assert r1.added_run_ids == ()
        assert r1.wrote is False
        assert r1.before_runs == r1.after_runs == 1

        # Second call: still nothing.
        r2 = reconcile_model(
            model_id="m-1", registry=registry, experiments_root=experiments_root,
        )
        assert r2.added_run_ids == ()
        assert r2.wrote is False

    def test_dry_run_does_not_write(self, tmp_path: Path):
        registry_root = tmp_path / "registry"
        experiments_root = tmp_path / "experiments"
        registry = ModelRegistry(registry_root)
        _write_run_dir(experiments_root, "m-1", "20260513T120000Z", {"mse": 0.5})
        _write_run_dir(experiments_root, "m-1", "20260514T120000Z", {"mse": 0.3})
        registry.register(
            model_id="m-1", manifest={}, model_state_path="/tmp/s.json",
            metrics={"mse": 0.3}, code_revision="rev2",
            run_id="20260514T120000Z",
        )

        result = reconcile_model(
            model_id="m-1",
            registry=registry,
            experiments_root=experiments_root,
            dry_run=True,
        )
        assert result.added_run_ids == ("20260513T120000Z",)
        assert result.wrote is False
        # Disk unchanged — entry still has runs=1.
        assert len(registry.get("m-1").runs) == 1

    def test_runs_sorted_by_run_id(self, tmp_path: Path):
        """Runs are sorted alphabetically by run_id (which is a UTC timestamp)."""
        registry_root = tmp_path / "registry"
        experiments_root = tmp_path / "experiments"
        registry = ModelRegistry(registry_root)
        for rid, mse in [
            ("20260514T120000Z", 0.3),
            ("20260512T120000Z", 0.7),
            ("20260513T120000Z", 0.5),
        ]:
            _write_run_dir(experiments_root, "m-1", rid, {"mse": mse})
        registry.register(
            model_id="m-1", manifest={}, model_state_path="/tmp/s.json",
            metrics={"mse": 0.3}, code_revision="rev",
            run_id="20260514T120000Z",
        )
        reconcile_model(
            model_id="m-1", registry=registry, experiments_root=experiments_root,
        )
        ids = [r.run_id for r in registry.get("m-1").runs]
        assert ids == [
            "20260512T120000Z",
            "20260513T120000Z",
            "20260514T120000Z",
        ]

    def test_top_level_fields_preserved(self, tmp_path: Path):
        """Reconcile only touches `runs`. Status, created_at, history are intact."""
        registry_root = tmp_path / "registry"
        experiments_root = tmp_path / "experiments"
        registry = ModelRegistry(registry_root)
        _write_run_dir(experiments_root, "m-1", "20260513T120000Z", {"mse": 0.5})
        registry.register(
            model_id="m-1", manifest={"k": "v"}, model_state_path="/latest.json",
            metrics={"mse": 0.3}, code_revision="rev2",
            run_id="20260514T120000Z",
        )
        _write_run_dir(experiments_root, "m-1", "20260514T120000Z", {"mse": 0.3})
        before = registry.get("m-1")
        reconcile_model(
            model_id="m-1", registry=registry, experiments_root=experiments_root,
        )
        after = registry.get("m-1")
        assert after.status == before.status
        assert after.created_at == before.created_at
        assert after.history == before.history
        assert after.stage_history == before.stage_history
        assert after.target_deployment_stage == before.target_deployment_stage
        assert after.metrics == before.metrics
        assert after.model_state_path == before.model_state_path
        assert after.code_revision == before.code_revision

    def test_reconcile_all_targets_every_model_by_default(self, tmp_path: Path):
        registry_root = tmp_path / "registry"
        experiments_root = tmp_path / "experiments"
        registry = ModelRegistry(registry_root)
        for mid in ["m-1", "m-2"]:
            _write_run_dir(experiments_root, mid, "20260513T120000Z", {"mse": 0.5})
            _write_run_dir(experiments_root, mid, "20260514T120000Z", {"mse": 0.3})
            registry.register(
                model_id=mid, manifest={}, model_state_path=f"/{mid}.json",
                metrics={"mse": 0.3}, code_revision="rev",
                run_id="20260514T120000Z",
            )
        results = reconcile_all(
            registry_root=registry_root,
            experiments_root=experiments_root,
        )
        assert {r.model_id for r in results} == {"m-1", "m-2"}
        assert all(r.added_run_ids == ("20260513T120000Z",) for r in results)

    def test_reconcile_all_respects_model_id_filter(self, tmp_path: Path):
        registry_root = tmp_path / "registry"
        experiments_root = tmp_path / "experiments"
        registry = ModelRegistry(registry_root)
        for mid in ["m-1", "m-2"]:
            _write_run_dir(experiments_root, mid, "20260513T120000Z", {"mse": 0.5})
            _write_run_dir(experiments_root, mid, "20260514T120000Z", {"mse": 0.3})
            registry.register(
                model_id=mid, manifest={}, model_state_path=f"/{mid}.json",
                metrics={"mse": 0.3}, code_revision="rev",
                run_id="20260514T120000Z",
            )
        results = reconcile_all(
            registry_root=registry_root,
            experiments_root=experiments_root,
            model_ids=["m-2"],
        )
        assert [r.model_id for r in results] == ["m-2"]
        # m-1 was not touched.
        assert len(registry.get("m-1").runs) == 1

    def test_missing_experiment_dir_is_safe(self, tmp_path: Path):
        """Reconciler is strictly additive: a RunRecord whose experiment dir
        was wiped from disk is preserved, not dropped.
        """
        registry_root = tmp_path / "registry"
        experiments_root = tmp_path / "experiments"
        registry = ModelRegistry(registry_root)
        registry.register(
            model_id="m-orphan", manifest={}, model_state_path="/x.json",
            metrics={}, code_revision="rev", run_id="20260514T120000Z",
        )
        # experiments_root doesn't have m-orphan/ at all.
        result = reconcile_model(
            model_id="m-orphan",
            registry=registry,
            experiments_root=experiments_root,
        )
        assert result.before_runs == 1
        assert result.after_runs == 1
        assert result.added_run_ids == ()
        assert result.wrote is False
        # The original RunRecord is intact.
        runs = registry.get("m-orphan").runs
        assert len(runs) == 1
        assert runs[0].run_id == "20260514T120000Z"
