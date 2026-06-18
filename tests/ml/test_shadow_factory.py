"""Tests for `ml.shadow.factory` (S-AI-WS7-PART-4)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from ml.predictors.shadow import ShadowPredictor
from ml.registry.model_registry import ModelRegistry
from ml.shadow.factory import (
    LIVE_INFLUENCE_STAGES,
    ShadowFactoryError,
    _load_model_state,
    _resolve_default_registry_root,
    _resolve_state_path_via_mirror,
    resolve_predictor,
    resolve_predictors,
)


def _make_registered_model(
    tmp_path: Path,
    *,
    model_id: str,
    stage: str,
    symbol_scope: str | None = None,
) -> tuple[ModelRegistry, Path]:
    """Register a model in `tmp_path/registry-store` and return
    (registry, model_state_path) for use by tests.

    Uses the constant-baseline trainer + ConstantPredictor pair —
    these are the simplest predictor in the codebase and avoid
    pulling in pandas (which is not installed in the dev sandbox).
    """
    state_path = tmp_path / "model_state.json"
    state_path.write_text(
        json.dumps(
            {
                "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
                "constant": 0.5,
            }
        )
    )
    registry_root = tmp_path / "registry-store"
    registry = ModelRegistry(registry_root)
    # Register directly into a pre-shadow stage when requested so the
    # 2026-05-19 default-flip (register defaults to `shadow`) doesn't
    # silently bump test fixtures past the stage they're trying to
    # cover. For shadow / advisory / limited_live / live_approved the
    # ladder walk still applies; the new direct edges keep that walk
    # legal.
    pre_shadow = {"research_only", "candidate", "backtest_approved"}
    manifest: dict = {"manifest_version": "v1"}
    if symbol_scope is not None:
        manifest["dataset"] = {"symbol_scope": symbol_scope}
    if stage in pre_shadow:
        manifest["target_deployment_stage"] = stage
    registry.register(
        model_id=model_id,
        manifest=manifest,
        model_state_path=str(state_path),
        metrics={"mae": 0.1},
        code_revision="x",
    )
    ladder = [
        "candidate", "backtest_approved", "shadow",
        "advisory", "limited_live", "live_approved",
    ]
    if stage not in ladder + ["research_only"]:
        raise ValueError(stage)
    if stage in pre_shadow:
        # Registered directly at the requested stage — nothing to do.
        return registry, state_path
    # Default registration lands at `shadow`. Walk forward from shadow
    # to the target stage.
    if stage == "shadow":
        return registry, state_path
    onward_from_shadow = ["advisory", "limited_live", "live_approved"]
    for step in onward_from_shadow:
        registry.promote_stage(
            model_id, step, by="op", reason=f"to-{step}",
        )
        if step == stage:
            break
    return registry, state_path


class TestResolvePredictor:
    @pytest.mark.parametrize("stage", sorted(LIVE_INFLUENCE_STAGES))
    def test_resolves_at_allowed_stages(self, tmp_path: Path, stage: str):
        registry, _ = _make_registered_model(
            tmp_path, model_id=f"m-{stage}", stage=stage,
        )
        predictor = resolve_predictor(f"m-{stage}", registry)
        assert isinstance(predictor, ShadowPredictor)
        assert predictor.model_id == f"m-{stage}"
        assert predictor.stage == stage
        # Smoke-call it — ConstantPredictor returns 0.5.
        assert predictor.predict({"k": 1}) == pytest.approx(0.5)

    @pytest.mark.parametrize(
        "stage", ["research_only", "candidate", "backtest_approved"],
    )
    def test_refuses_unpromoted_stages(self, tmp_path: Path, stage: str):
        registry, _ = _make_registered_model(
            tmp_path, model_id=f"m-{stage}", stage=stage,
        )
        with pytest.raises(ShadowFactoryError, match="stage"):
            resolve_predictor(f"m-{stage}", registry)

    def test_unknown_model_id(self, tmp_path: Path):
        registry = ModelRegistry(tmp_path / "registry")
        with pytest.raises(ShadowFactoryError, match="not found"):
            resolve_predictor("does-not-exist", registry)

    def test_missing_model_state_path(self, tmp_path: Path):
        registry, state_path = _make_registered_model(
            tmp_path, model_id="m-1", stage="shadow",
        )
        state_path.unlink()
        with pytest.raises(ShadowFactoryError, match="model_state_path"):
            resolve_predictor("m-1", registry)

    def test_unknown_trainer_qualname(self, tmp_path: Path):
        registry, state_path = _make_registered_model(
            tmp_path, model_id="m-1", stage="shadow",
        )
        # Rewrite the state with a bogus trainer qualname.
        state_path.write_text(
            json.dumps(
                {"trainer": "no.such.module.Trainer", "constant": 0.5}
            )
        )
        with pytest.raises(ShadowFactoryError):
            resolve_predictor("m-1", registry)

    def test_blank_trainer_qualname(self, tmp_path: Path):
        registry, state_path = _make_registered_model(
            tmp_path, model_id="m-1", stage="shadow",
        )
        state_path.write_text(json.dumps({"constant": 0.5}))
        with pytest.raises(ShadowFactoryError, match="qualname"):
            resolve_predictor("m-1", registry)

    def test_log_path_propagates(self, tmp_path: Path):
        registry, _ = _make_registered_model(
            tmp_path, model_id="m-1", stage="shadow",
        )
        log = tmp_path / "audit.jsonl"
        predictor = resolve_predictor("m-1", registry, log_path=log)
        predictor.predict({"k": 1})
        lines = log.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["model_id"] == "m-1"


class TestResolvePredictors:
    def test_returns_in_input_order(self, tmp_path: Path):
        for mid in ("a", "b", "c"):
            _make_registered_model(
                tmp_path, model_id=mid, stage="shadow",
            )
        registry = ModelRegistry(tmp_path / "registry-store")
        out = resolve_predictors(["c", "a", "b"], registry, log_path=None)
        assert [p.model_id for p in out] == ["c", "a", "b"]

    def test_skips_unknown_id_when_not_strict(
        self, tmp_path: Path, caplog
    ):
        _make_registered_model(
            tmp_path, model_id="good", stage="shadow",
        )
        registry = ModelRegistry(tmp_path / "registry-store")
        with caplog.at_level(logging.WARNING):
            out = resolve_predictors(
                ["good", "missing", "good"], registry, log_path=None,
            )
        assert [p.model_id for p in out] == ["good", "good"]
        assert any(
            "shadow_factory_skipped" in r.message and "missing" in r.message
            for r in caplog.records
        )

    def test_skips_unpromoted_stage_when_not_strict(
        self, tmp_path: Path, caplog
    ):
        _make_registered_model(
            tmp_path, model_id="ok", stage="shadow",
        )
        _make_registered_model(
            tmp_path, model_id="research", stage="research_only",
        )
        registry = ModelRegistry(tmp_path / "registry-store")
        with caplog.at_level(logging.WARNING):
            out = resolve_predictors(
                ["ok", "research"], registry, log_path=None,
            )
        assert [p.model_id for p in out] == ["ok"]
        assert any(
            "shadow_factory_skipped" in r.message and "research" in r.message
            for r in caplog.records
        )

    def test_strict_reraises(self, tmp_path: Path):
        _make_registered_model(
            tmp_path, model_id="ok", stage="shadow",
        )
        registry = ModelRegistry(tmp_path / "registry-store")
        with pytest.raises(ShadowFactoryError):
            resolve_predictors(
                ["ok", "missing"], registry, log_path=None, strict=True,
            )

    def test_empty_list(self, tmp_path: Path):
        registry = ModelRegistry(tmp_path / "registry-store")
        assert resolve_predictors([], registry) == []


class TestDiscoverShadowStageModelIds:
    """The 2026-05-19 auto-wire helper. Returns every model in the
    `shadow` stage exactly — higher stages (advisory / limited_live
    / live_approved) are intentionally excluded so a model promoted
    past shadow doesn't silently keep showing up as a shadow
    side-channel on every strategy."""

    def test_returns_only_shadow_stage_models(self, tmp_path: Path):
        from ml.shadow.factory import discover_shadow_stage_model_ids

        _make_registered_model(tmp_path, model_id="m-shadow-a", stage="shadow")
        _make_registered_model(tmp_path, model_id="m-shadow-b", stage="shadow")
        _make_registered_model(
            tmp_path, model_id="m-advisory", stage="advisory",
        )
        _make_registered_model(
            tmp_path, model_id="m-research", stage="research_only",
        )
        registry = ModelRegistry(tmp_path / "registry-store")
        assert discover_shadow_stage_model_ids(registry) == [
            "m-shadow-a", "m-shadow-b",
        ]

    def test_returns_empty_when_no_shadow_stage_models(self, tmp_path: Path):
        from ml.shadow.factory import discover_shadow_stage_model_ids

        _make_registered_model(
            tmp_path, model_id="m-advisory", stage="advisory",
        )
        _make_registered_model(
            tmp_path, model_id="m-research", stage="research_only",
        )
        registry = ModelRegistry(tmp_path / "registry-store")
        assert discover_shadow_stage_model_ids(registry) == []

    def test_stable_alphabetical_order(self, tmp_path: Path):
        from ml.shadow.factory import discover_shadow_stage_model_ids

        # Register in reverse-alpha order; expect alpha-sorted output.
        for mid in ["zzz", "aaa", "mmm"]:
            _make_registered_model(tmp_path, model_id=mid, stage="shadow")
        registry = ModelRegistry(tmp_path / "registry-store")
        assert discover_shadow_stage_model_ids(registry) == [
            "aaa", "mmm", "zzz",
        ]


class TestSymbolAwareAutoWire:
    """`discover_shadow_stage_model_ids(registry, symbol=...)` restricts the
    auto-wire set to the strategy's symbol + symbol-agnostic (`all`/unset)
    models, so an alt/futures strategy never auto-wires a different-symbol
    regime head (2026-06-18, soak-everything)."""

    def _registry(self, tmp_path: Path) -> ModelRegistry:
        _make_registered_model(
            tmp_path, model_id="btc-regime", stage="shadow",
            symbol_scope="BTCUSDT",
        )
        _make_registered_model(
            tmp_path, model_id="mes-regime", stage="shadow",
            symbol_scope="MES",
        )
        _make_registered_model(
            tmp_path, model_id="decision-all", stage="shadow",
            symbol_scope="all",
        )
        # No dataset/symbol_scope at all → treated as agnostic (fail-permissive).
        _make_registered_model(tmp_path, model_id="no-scope", stage="shadow")
        return ModelRegistry(tmp_path / "registry-store")

    def test_btc_symbol_excludes_mes_head(self, tmp_path: Path):
        from ml.shadow.factory import discover_shadow_stage_model_ids

        assert discover_shadow_stage_model_ids(
            self._registry(tmp_path), symbol="BTCUSDT",
        ) == ["btc-regime", "decision-all", "no-scope"]

    def test_mes_symbol_excludes_btc_head(self, tmp_path: Path):
        from ml.shadow.factory import discover_shadow_stage_model_ids

        assert discover_shadow_stage_model_ids(
            self._registry(tmp_path), symbol="MES",
        ) == ["decision-all", "mes-regime", "no-scope"]

    def test_alt_symbol_gets_only_agnostic(self, tmp_path: Path):
        from ml.shadow.factory import discover_shadow_stage_model_ids

        # SOLUSDT has no symbol-specific head → only the `all`/unset models.
        assert discover_shadow_stage_model_ids(
            self._registry(tmp_path), symbol="SOLUSDT",
        ) == ["decision-all", "no-scope"]

    def test_no_symbol_returns_all_backcompat(self, tmp_path: Path):
        from ml.shadow.factory import discover_shadow_stage_model_ids

        assert discover_shadow_stage_model_ids(self._registry(tmp_path)) == [
            "btc-regime", "decision-all", "mes-regime", "no-scope",
        ]

    def test_symbol_scope_case_insensitive(self, tmp_path: Path):
        from ml.shadow.factory import discover_shadow_stage_model_ids

        _make_registered_model(
            tmp_path, model_id="btc-lower", stage="shadow",
            symbol_scope="btcusdt",
        )
        assert discover_shadow_stage_model_ids(
            ModelRegistry(tmp_path / "registry-store"), symbol="BTCUSDT",
        ) == ["btc-lower"]

    def test_model_symbol_scope_helper(self, tmp_path: Path):
        from ml.shadow.factory import model_symbol_scope

        by_id = {e.model_id: e for e in self._registry(tmp_path).list()}
        assert model_symbol_scope(by_id["btc-regime"]) == "BTCUSDT"
        assert model_symbol_scope(by_id["decision-all"]) == "ALL"
        assert model_symbol_scope(by_id["no-scope"]) is None


class TestResolveDefaultRegistryRoot:
    """The default-registry-root resolver picks the right path for the
    running environment so live-VM strategies, the dashboard, and
    trainer-VM tooling all land on the registry the trainer writes."""

    def test_explicit_env_override_wins(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("ML_REGISTRY_ROOT", str(tmp_path / "explicit"))
        monkeypatch.setenv("DATA_DIR", "/data/bot-data")
        assert _resolve_default_registry_root() == tmp_path / "explicit"

    def test_data_dir_canonical_layout(self, monkeypatch):
        monkeypatch.delenv("ML_REGISTRY_ROOT", raising=False)
        monkeypatch.setenv("DATA_DIR", "/data/bot-data")
        # `models/` subdir so the factory's glob doesn't see sibling
        # artifacts like `trainer_status.json` in the same mirror dir.
        assert _resolve_default_registry_root() == Path(
            "/data/bot-data/runtime_logs/trainer_mirror/models"
        )

    def test_relative_data_dir_falls_through(self, monkeypatch):
        # A relative DATA_DIR is a misconfiguration (src/utils/paths.py
        # logs CRITICAL when it sees one). The factory ignores it and
        # falls through to the local dev path rather than building a
        # path relative to CWD, which would silently land on a
        # non-existent directory.
        monkeypatch.delenv("ML_REGISTRY_ROOT", raising=False)
        monkeypatch.setenv("DATA_DIR", "data")
        assert _resolve_default_registry_root() == Path("./ml/registry-store")

    def test_no_env_falls_back_to_local(self, monkeypatch):
        monkeypatch.delenv("ML_REGISTRY_ROOT", raising=False)
        monkeypatch.delenv("DATA_DIR", raising=False)
        assert _resolve_default_registry_root() == Path("./ml/registry-store")


class TestModelStatePathMirrorFallback:
    """The registry's `model_state_path` is an absolute path on the
    trainer VM's filesystem. On the live VM the equivalent file lives
    under the trainer mirror — `_load_model_state` resolves the
    mismatch transparently so a single registry entry works on both
    machines."""

    def _make_state_file(self, tmp_path: Path, *, mid: str, run_id: str) -> Path:
        run_dir = tmp_path / "trainer-mirror" / "experiments-runs" / mid / run_id
        run_dir.mkdir(parents=True)
        state_path = run_dir / "model_state.json"
        state_path.write_text('{"trainer": "x", "constant": 0.5}')
        return state_path

    def test_resolver_extracts_suffix_from_experiments_runs(self, tmp_path: Path):
        # Trainer-VM absolute path the entry stored.
        trainer_abs = Path(
            "/home/ubuntu/ict-trading-bot/ml/experiments-runs/m1/r1/model_state.json"
        )
        registry_root = tmp_path / "trainer-mirror" / "models"
        # `parent` of registry_root is `<mirror>/`; `experiments-runs/`
        # lives next to `models/` in the canonical layout.
        result = _resolve_state_path_via_mirror(trainer_abs, registry_root)
        assert result == tmp_path / "trainer-mirror" / "experiments-runs" / "m1" / "r1" / "model_state.json"

    def test_resolver_returns_none_when_registry_root_missing(self):
        trainer_abs = Path(
            "/home/ubuntu/ict-trading-bot/ml/experiments-runs/m1/r1/model_state.json"
        )
        assert _resolve_state_path_via_mirror(trainer_abs, None) is None

    def test_resolver_returns_none_when_no_experiments_runs_segment(self, tmp_path: Path):
        # Pathological path without "experiments-runs/" — resolver
        # signals "give up" rather than guessing.
        weird = Path("/home/ubuntu/some/other/place/model_state.json")
        registry_root = tmp_path / "trainer-mirror" / "models"
        assert _resolve_state_path_via_mirror(weird, registry_root) is None

    def test_load_state_uses_literal_path_when_present(self, tmp_path: Path):
        state_path = tmp_path / "literal" / "model_state.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text('{"trainer": "ok"}')
        # registry_root is irrelevant when the literal path resolves.
        assert _load_model_state(state_path, registry_root=tmp_path / "anywhere") == {"trainer": "ok"}

    def test_load_state_falls_back_to_mirror_when_literal_missing(self, tmp_path: Path):
        # The trainer-VM absolute path that the registry entry stored.
        trainer_abs = Path(
            "/home/ubuntu/ict-trading-bot/ml/experiments-runs/m1/r1/model_state.json"
        )
        # The actual file on the live VM under the mirror.
        actual = self._make_state_file(tmp_path, mid="m1", run_id="r1")
        registry_root = tmp_path / "trainer-mirror" / "models"
        assert _load_model_state(trainer_abs, registry_root=registry_root) == {
            "trainer": "x",
            "constant": 0.5,
        }
        # Sanity: the file we found was the mirror-resolved one.
        assert actual.is_file()

    def test_load_state_raises_when_both_paths_miss(self, tmp_path: Path):
        trainer_abs = Path(
            "/home/ubuntu/ict-trading-bot/ml/experiments-runs/missing/r/model_state.json"
        )
        registry_root = tmp_path / "trainer-mirror" / "models"
        registry_root.mkdir(parents=True)
        with pytest.raises(ShadowFactoryError, match="model_state_path not found"):
            _load_model_state(trainer_abs, registry_root=registry_root)
