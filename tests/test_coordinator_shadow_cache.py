"""Tests for Coordinator-side shadow predictor cache (S-AI-WS7-PART-6).

Verifies that Coordinator resolves `shadow_model_ids` once per
strategy and reuses the cached list across ticks; that
`reload_strategy_config` invalidates the cache so a YAML edit is
picked up; and that a strategy without the field gets an empty
list (no factory call).
"""
from __future__ import annotations

import json
import sys
import textwrap
import types
from pathlib import Path
from unittest import mock

import pytest

# Match the matplotlib stub the existing coordinator tests use.
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from src.core.coordinator import Coordinator  # noqa: E402


SHADOW_UNITS_YAML = textwrap.dedent("""\
    units:
      strategies:
        - name: vwap
          model: null
          signal_prefixes: [vwap]
          shadow_model_ids: [m-shadow-a, m-shadow-b]
        - name: turtle_soup
          model: null
          signal_prefixes: [turtle_soup]
      accounts:
        - id: flow_account
          exchange: bybit
          risk_pct: 0.01
          env_path: .env
          strategies: [vwap, turtle_soup]
      dashboards:
        alerts_enabled: true
""")


def _register_models(tmp_path: Path, model_ids: list[str]) -> Path:
    """Register the given model_ids in tmp_path/registry-store at
    stage `shadow` so the factory is happy to resolve them."""
    from ml.registry.model_registry import ModelRegistry

    registry_root = tmp_path / "registry-store"
    registry = ModelRegistry(registry_root)
    for mid in model_ids:
        state_path = tmp_path / f"{mid}_state.json"
        state_path.write_text(
            json.dumps(
                {
                    "trainer": "ml.trainers.constant_baseline."
                               "ConstantPredictionTrainer",
                    "constant": 0.5,
                }
            )
        )
        registry.register(
            model_id=mid,
            manifest={"manifest_version": "v1"},
            model_state_path=str(state_path),
            metrics={"mae": 0.1},
            code_revision="x",
        )
        for step in ["candidate", "backtest_approved", "shadow"]:
            registry.promote_stage(mid, step, by="op", reason="test")
    return registry_root


def _populate_strategy_cfg(
    coord: Coordinator,
    name: str,
    *,
    shadow_model_ids: list[str],
    registry_root: Path,
    log_path: Path,
) -> None:
    """Inject runtime-only fields (registry root + log path) into the
    strategy cfg. These come from `cfg.get("_shadow_registry_root")`
    in `_get_shadow_predictors` — values that wouldn't normally
    appear in YAML but are honoured in tests."""
    for s in coord.list_strategies():
        if isinstance(s, dict) and s.get("name") == name:
            s["shadow_model_ids"] = list(shadow_model_ids)
            s["_shadow_registry_root"] = str(registry_root)
            s["_shadow_log_path"] = str(log_path)
            return
    raise AssertionError(f"strategy {name!r} not in units.yaml")


@pytest.fixture()
def coord(tmp_path):
    units_path = tmp_path / "units.yaml"
    units_path.write_text(SHADOW_UNITS_YAML)
    accounts_path = tmp_path / "no-accounts.yaml"
    return Coordinator(
        units_path=str(units_path),
        accounts_path=str(accounts_path),
    )


class TestShadowPredictorCache:
    def test_strategy_without_shadow_field_returns_empty(self, coord):
        # turtle_soup has no shadow_model_ids in the fixture YAML.
        out = coord._get_shadow_predictors("turtle_soup")
        assert out == []
        # Cache populated with the empty list.
        assert coord._shadow_predictors_cache["turtle_soup"] == []

    def test_resolves_once_then_caches(self, tmp_path, coord):
        registry_root = _register_models(tmp_path, ["m-a", "m-b"])
        log_path = tmp_path / "audit.jsonl"
        _populate_strategy_cfg(
            coord, "vwap",
            shadow_model_ids=["m-a", "m-b"],
            registry_root=registry_root, log_path=log_path,
        )
        with mock.patch(
            "ml.shadow.factory.resolve_predictors",
            wraps=__import__(
                "ml.shadow.factory", fromlist=["resolve_predictors"]
            ).resolve_predictors,
        ) as spy:
            first = coord._get_shadow_predictors("vwap")
            second = coord._get_shadow_predictors("vwap")
            third = coord._get_shadow_predictors("vwap")
        assert spy.call_count == 1
        assert first is second is third
        assert [p.model_id for p in first] == ["m-a", "m-b"]

    def test_reload_invalidates_cache(self, tmp_path, coord):
        registry_root = _register_models(tmp_path, ["m-a"])
        log_path = tmp_path / "audit.jsonl"
        _populate_strategy_cfg(
            coord, "vwap",
            shadow_model_ids=["m-a"],
            registry_root=registry_root, log_path=log_path,
        )
        # Prime the cache.
        first = coord._get_shadow_predictors("vwap")
        assert coord._shadow_predictors_cache.get("vwap") is first
        # Reload (with a missing path to make it cheap — we only care
        # about the cache-clear side effect, not the YAML load).
        coord.reload_strategy_config(config_path=str(tmp_path / "no-such-yaml"))
        assert coord._shadow_predictors_cache == {}

    def test_separate_strategies_cache_independently(
        self, tmp_path, coord
    ):
        registry_root = _register_models(tmp_path, ["m-a", "m-b"])
        log_path = tmp_path / "audit.jsonl"
        _populate_strategy_cfg(
            coord, "vwap",
            shadow_model_ids=["m-a"],
            registry_root=registry_root, log_path=log_path,
        )
        _populate_strategy_cfg(
            coord, "turtle_soup",
            shadow_model_ids=["m-b"],
            registry_root=registry_root, log_path=log_path,
        )
        v = coord._get_shadow_predictors("vwap")
        t = coord._get_shadow_predictors("turtle_soup")
        assert [p.model_id for p in v] == ["m-a"]
        assert [p.model_id for p in t] == ["m-b"]
        # Caches don't share entries.
        assert coord._shadow_predictors_cache["vwap"] is not (
            coord._shadow_predictors_cache["turtle_soup"]
        )

    def test_dispatch_injects_shadow_predictors(
        self, tmp_path, coord
    ):
        """The dispatcher path (`strategy_order_pkg`) merges
        `_shadow_predictors` into the cfg passed to
        `mod.order_package`. Verify by spying on the strategy
        module."""
        registry_root = _register_models(tmp_path, ["m-a"])
        log_path = tmp_path / "audit.jsonl"
        _populate_strategy_cfg(
            coord, "vwap",
            shadow_model_ids=["m-a"],
            registry_root=registry_root, log_path=log_path,
        )
        # The vwap strategy needs candle data, but we spy on the
        # module's order_package so we never reach the deterministic
        # logic.
        sentinel_pkg = {
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry": 100.0, "sl": 99.0, "tp": 101.0,
            "confidence": 0.5, "meta": {},
        }
        captured: dict = {}

        def fake_order_package(cfg, candles_df=None):
            captured["cfg"] = cfg
            return sentinel_pkg

        import src.units.strategies.vwap as vwap_mod
        with mock.patch.object(
            vwap_mod, "order_package", side_effect=fake_order_package,
        ):
            coord.strategy_order_pkg(
                "vwap", symbol="BTCUSDT", candles_df=pd.DataFrame(),
            )
        assert "_shadow_predictors" in captured["cfg"]
        injected = captured["cfg"]["_shadow_predictors"]
        assert [p.model_id for p in injected] == ["m-a"]
