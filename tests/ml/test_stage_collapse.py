"""Backward-compatible 3-stage collapse (2026-06-16, operator-approved).

The deployment ladder collapsed from 7 stages to 3 canonical ones —
``candidate → shadow → advisory`` — with a permanent alias map so historical
registry entries / manifests using the old names keep resolving. These tests
pin:

  * ``canonical_stage`` maps each alias correctly, passes canonical through,
    raises on garbage.
  * a manifest / registry entry created with an OLD name validates and reports
    the canonical stage (never crashes, never strands the model).
  * the shadow factory loads ``shadow`` / ``advisory`` (and a stored
    ``live_approved``) entries, and refuses a ``candidate`` entry.
  * ``advisory_sizing`` influence set: ``candidate`` / ``shadow`` models do
    not influence; ``advisory`` (and a stored ``live_approved``) does.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ml.manifest import (
    MANIFEST_VERSION,
    STAGE_ALIASES,
    VALID_DEPLOYMENT_STAGES,
    TrainingManifest,
    canonical_stage,
)


# ---- canonical_stage ------------------------------------------------------

def test_canonical_stage_passes_canonical_through():
    for s in ("candidate", "shadow", "advisory"):
        assert canonical_stage(s) == s


def test_canonical_stage_maps_each_alias():
    assert canonical_stage("research_only") == "candidate"
    assert canonical_stage("backtest_approved") == "candidate"
    assert canonical_stage("limited_live") == "advisory"
    assert canonical_stage("live_approved") == "advisory"
    # The map covers exactly the four legacy names.
    assert set(STAGE_ALIASES) == {
        "research_only", "backtest_approved", "limited_live", "live_approved",
    }


def test_canonical_stage_aliases_resolve_into_canonical_set():
    for canon in STAGE_ALIASES.values():
        assert canon in VALID_DEPLOYMENT_STAGES


def test_canonical_stage_raises_on_garbage():
    for bad in ("prod", "made-up", "", "SHADOW", "live"):
        with pytest.raises(ValueError):
            canonical_stage(bad)


def test_valid_deployment_stages_are_the_three_canonical():
    assert VALID_DEPLOYMENT_STAGES == ("candidate", "shadow", "advisory")


# ---- manifest normalization ----------------------------------------------

def _manifest_payload(stage: str) -> dict:
    return {
        "manifest_version": MANIFEST_VERSION,
        "model_id": "m-collapse",
        "model_family": "regression_baseline",
        "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
        "trainer_config": {"target_column": "y"},
        "dataset": {
            "family": "backtest_results",
            "symbol_scope": "all",
            "timeframe": "all",
            "version": "v001",
        },
        "evaluator": "ml.evaluators.regression.RegressionEvaluator",
        "evaluator_config": {"target_column": "y", "metrics": ["mse"]},
        "target_deployment_stage": stage,
    }


@pytest.mark.parametrize(
    "old, canon",
    [
        ("research_only", "candidate"),
        ("backtest_approved", "candidate"),
        ("limited_live", "advisory"),
        ("live_approved", "advisory"),
    ],
)
def test_manifest_old_name_validates_and_normalizes(old, canon):
    m = TrainingManifest.from_dict(_manifest_payload(old))
    assert m.target_deployment_stage == canon
    # And the stored canonical value round-trips.
    assert m.to_dict()["target_deployment_stage"] == canon


def test_manifest_rejects_garbage_stage():
    with pytest.raises(ValueError):
        TrainingManifest.from_dict(_manifest_payload("prod"))


# ---- registry normalization ----------------------------------------------

def _register(reg, model_id: str, stage: str):
    return reg.register(
        model_id=model_id,
        manifest={"manifest_version": "v1", "target_deployment_stage": stage},
        model_state_path="/tmp/s.json", metrics={}, code_revision="x",
    )


@pytest.mark.parametrize(
    "old, canon",
    [
        ("research_only", "candidate"),
        ("backtest_approved", "candidate"),
        ("limited_live", "advisory"),
        ("live_approved", "advisory"),
    ],
)
def test_registry_register_old_name_reports_canonical(tmp_path: Path, old, canon):
    from ml.registry.model_registry import ModelRegistry

    reg = ModelRegistry(tmp_path)
    entry = _register(reg, "m-1", old)
    assert entry.target_deployment_stage == canon
    # And re-reading from disk also reports canonical (normalize-on-read).
    assert reg.get("m-1").target_deployment_stage == canon


def test_registry_loads_entry_stored_with_old_name(tmp_path: Path):
    """A registry JSON file written with an OLD stage name (a historical row)
    must deserialize, normalizing to canonical — never fail to load."""
    import json

    from ml.registry.model_registry import ModelRegistry, RegistryEntry

    # Hand-write a row whose stored stage is the legacy `live_approved`.
    entry = RegistryEntry(
        model_id="legacy-1",
        status="candidate",
        manifest={"manifest_version": "v1"},
        model_state_path="/tmp/s.json",
        metrics={},
        code_revision="x",
        created_at=__import__("datetime").datetime(
            2026, 1, 1, tzinfo=__import__("datetime").timezone.utc
        ),
        target_deployment_stage="advisory",  # construction normalizes anyway
    )
    raw = entry.to_dict()
    raw["target_deployment_stage"] = "live_approved"  # simulate an old file
    (tmp_path / "legacy-1.json").write_text(json.dumps(raw), encoding="utf-8")

    reg = ModelRegistry(tmp_path)
    loaded = reg.get("legacy-1")
    assert loaded.target_deployment_stage == "advisory"


# ---- shadow factory stage gate -------------------------------------------

def _factory_entry(stage: str):
    """A minimal RegistryEntry-like object the factory's _check_stage reads."""
    return SimpleNamespace(model_id=f"m-{stage}", target_deployment_stage=stage)


def test_factory_loads_shadow_advisory_and_stored_live_approved():
    from ml.shadow.factory import LIVE_INFLUENCE_STAGES, _check_stage

    assert LIVE_INFLUENCE_STAGES == frozenset({"shadow", "advisory"})
    # shadow + advisory (canonical) load; a stored legacy live_approved loads
    # because _check_stage normalizes it to advisory.
    for stage in ("shadow", "advisory", "live_approved", "limited_live"):
        _check_stage(_factory_entry(stage))  # must not raise


def test_factory_refuses_candidate_and_aliases():
    from ml.shadow.factory import ShadowFactoryError, _check_stage

    for stage in ("candidate", "research_only", "backtest_approved"):
        with pytest.raises(ShadowFactoryError):
            _check_stage(_factory_entry(stage))


# ---- advisory_sizing influence set ---------------------------------------

def test_advisory_influence_set_only_advisory():
    from src.runtime.advisory_sizing import _influences

    # candidate / shadow never influence.
    assert _influences("candidate") is False
    assert _influences("shadow") is False
    assert _influences("research_only") is False  # → candidate
    assert _influences("backtest_approved") is False  # → candidate
    # advisory (and the legacy names that normalize to it) influence.
    assert _influences("advisory") is True
    assert _influences("live_approved") is True
    assert _influences("limited_live") is True
    # garbage never influences (fail-safe).
    assert _influences("prod") is False


def test_advisory_discover_includes_stored_live_approved():
    from src.runtime.advisory_sizing import discover_advisory_stage_model_ids

    registry = SimpleNamespace(list=lambda: [
        SimpleNamespace(model_id="cand", target_deployment_stage="candidate"),
        SimpleNamespace(model_id="shad", target_deployment_stage="shadow"),
        SimpleNamespace(model_id="adv", target_deployment_stage="advisory"),
        SimpleNamespace(model_id="lim", target_deployment_stage="limited_live"),
        SimpleNamespace(model_id="liv", target_deployment_stage="live_approved"),
    ])
    # Only the influence-stage models, with legacy aliases folded in.
    assert discover_advisory_stage_model_ids(registry) == ["adv", "lim", "liv"]
