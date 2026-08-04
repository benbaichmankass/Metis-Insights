"""Tests for the training-population guard (scripts/check_training_population.py).

Locks the three load-bearing behaviors so the guard itself can't silently rot:
  1. a live-only decision family in augmentation_debt (within ceiling) PASSES;
  2. a decision family that is neither wired nor exempt nor debt VIOLATES;
  3. a family WIRED for augmentation but still parked in debt VIOLATES (the
     ratchet forcing function that drives the corpus off the ~78-label wall).
"""
from __future__ import annotations

import importlib
from pathlib import Path

import yaml

guard = importlib.import_module("scripts.check_training_population")


def _write(tmp_path: Path, registry: dict, build_sh: str) -> tuple[Path, Path]:
    reg = tmp_path / "training_population.yaml"
    reg.write_text(yaml.safe_dump(registry, sort_keys=False))
    sh = tmp_path / "build_trainer_datasets.sh"
    sh.write_text(build_sh)
    return reg, sh


def _evaluate(monkeypatch, tmp_path, registry, build_sh):
    reg, sh = _write(tmp_path, registry, build_sh)
    monkeypatch.setattr(guard, "REGISTRY", reg)
    monkeypatch.setattr(guard, "BUILD_SH", sh)
    return guard.evaluate()


# One live-only build_family invocation, no augmentation marker.
_LIVE_ONLY_SH = (
    'build_family trade_outcomes \\\n'
    '  --output-dir "$DATASETS_ROOT" --source "trade_journal.db" --overwrite \\\n'
    '  "db_path=${DB_PATH}"\n'
)
# An UNRELATED augmented block elsewhere in the file (the MES-style marker) must
# NOT falsely certify trade_outcomes — the regression this guard was written for.
_LIVE_ONLY_WITH_FOREIGN_MARKER_SH = _LIVE_ONLY_SH + (
    '\n# MES block\n'
    'python -m ml build-dataset trade_outcomes "include_backtest=true" --only mes\n'
)
_WIRED_SH = (
    'build_family trade_outcomes \\\n'
    '  --output-dir "$DATASETS_ROOT" --source "trade_journal.db" --overwrite \\\n'
    '  "db_path=${DB_PATH}" "include_backtest=true"\n'
)


def _debt_registry(ceiling=1):
    return {
        "schema_version": 1,
        "augmentation_marker": "include_backtest",
        "decision_families": ["trade_outcomes"],
        "debt_ceiling": ceiling,
        "augmentation_debt": {
            "trade_outcomes": {"tracking_id": "BL-X", "reason": "live-only, owed the feed"},
        },
    }


def test_live_only_in_debt_within_ceiling_passes(monkeypatch, tmp_path):
    violations, rows = _evaluate(monkeypatch, tmp_path, _debt_registry(), _LIVE_ONLY_SH)
    assert violations == []
    assert rows[0]["state"] == "debt"


def test_foreign_marker_does_not_certify_wiring(monkeypatch, tmp_path):
    # The MES-style `include_backtest` elsewhere must not flip trade_outcomes to
    # "wired" — it would falsely fire the ratchet and, worse, hide a real gap.
    violations, rows = _evaluate(
        monkeypatch, tmp_path, _debt_registry(), _LIVE_ONLY_WITH_FOREIGN_MARKER_SH
    )
    assert rows[0]["state"] == "debt"
    assert violations == []


def test_unclassified_live_only_family_violates(monkeypatch, tmp_path):
    reg = {
        "schema_version": 1,
        "augmentation_marker": "include_backtest",
        "decision_families": ["trade_outcomes"],
        "debt_ceiling": 0,
        # no exempt, no debt → unclassified live-only
    }
    violations, rows = _evaluate(monkeypatch, tmp_path, reg, _LIVE_ONLY_SH)
    assert rows[0]["state"] == "LIVE_ONLY"
    assert any("[population]" in v for v in violations)


def test_wired_but_still_in_debt_violates_ratchet(monkeypatch, tmp_path):
    violations, rows = _evaluate(monkeypatch, tmp_path, _debt_registry(), _WIRED_SH)
    assert any("[ratchet]" in v and "trade_outcomes" in v for v in violations)


def test_wired_and_paid_down_passes(monkeypatch, tmp_path):
    reg = {
        "schema_version": 1,
        "augmentation_marker": "include_backtest",
        "decision_families": ["trade_outcomes"],
        "debt_ceiling": 0,
        # paid down: no debt entry, family is wired
    }
    violations, rows = _evaluate(monkeypatch, tmp_path, reg, _WIRED_SH)
    assert rows[0]["state"] == "augmented"
    assert violations == []


def test_debt_over_ceiling_violates(monkeypatch, tmp_path):
    violations, _ = _evaluate(monkeypatch, tmp_path, _debt_registry(ceiling=0), _LIVE_ONLY_SH)
    assert any("[ratchet]" in v and "debt_ceiling" in v for v in violations)


def test_repo_registry_is_currently_clean():
    # The committed config must pass its own guard (guards the seed state).
    violations, _ = guard.evaluate()
    assert violations == [], violations
