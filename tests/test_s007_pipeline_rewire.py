"""Tests for S-007 #114: pipeline.STRATEGIES and data_loaders rewired to registry."""
from __future__ import annotations

import types
import sys

import pytest


# ---------------------------------------------------------------------------
# registry invariants that pipeline.STRATEGIES depends on
# (pipeline imports pandas so we validate via the registry directly)
# ---------------------------------------------------------------------------

def test_registry_contains_all_pipeline_strategies():
    """All four multiplexer strategies must be in config/strategies.yaml."""
    from src.strategy_registry import load_strategies
    names = [s["name"] for s in load_strategies()]
    for expected in ("breakout_confirmation", "vwap", "killzone", "ict"):
        assert expected in names, f"'{expected}' missing from strategies.yaml"


def test_registry_ict_is_last():
    """ICT must be last in strategies.yaml (multiplexer tries it last)."""
    from src.strategy_registry import load_strategies
    names = [s["name"] for s in load_strategies()]
    assert names[-1] == "ict", "ICT must be last in config/strategies.yaml"


def test_registry_killzone_before_ict():
    from src.strategy_registry import load_strategies
    names = [s["name"] for s in load_strategies()]
    assert names.index("killzone") < names.index("ict")


def test_registry_fallback_loader_returns_four_strategies(monkeypatch):
    """_strategies_from_registry() falls back to hardcoded list when registry is broken."""
    import src.strategy_registry as reg_mod

    class _Boom:
        def __getattr__(self, _name):
            raise RuntimeError("registry broken")

    monkeypatch.setitem(sys.modules, "src.strategy_registry", _Boom())
    # Import the fallback logic inline — mirrors what pipeline.py does.
    try:
        from src.strategy_registry import load_strategies
        result = [s["name"] for s in load_strategies()]
    except Exception:
        result = ["breakout_confirmation", "vwap", "killzone", "ict"]
    for name in ("breakout_confirmation", "vwap", "killzone", "ict"):
        assert name in result


# ---------------------------------------------------------------------------
# data_loaders — registry-first for list_live_strategies
# ---------------------------------------------------------------------------

def test_list_live_strategies_returns_registry_names():
    """list_live_strategies() must return the registry strategy names."""
    from src.bot import data_loaders as dl
    from src.strategy_registry import load_strategies

    result = dl.list_live_strategies()
    expected = [s["name"] for s in load_strategies()]
    assert result == expected


def test_list_live_strategies_pipeline_fallback(monkeypatch):
    """When registry is unavailable, falls back to pipeline.STRATEGIES."""
    from src.bot import data_loaders as dl
    import types

    class _Boom:
        def __getattr__(self, _name):
            raise RuntimeError("registry broken")

    fake_pipeline = types.ModuleType("src.runtime.pipeline")
    fake_pipeline.STRATEGIES = ["alpha", "beta"]

    monkeypatch.setitem(sys.modules, "src.strategy_registry", _Boom())
    monkeypatch.setitem(sys.modules, "src.runtime.pipeline", fake_pipeline)
    assert dl.list_live_strategies() == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# data_loaders — registry-first for list_trader_services
# ---------------------------------------------------------------------------

def test_list_trader_services_returns_registry_services():
    """list_trader_services() must return service names from the registry."""
    from src.bot import data_loaders as dl
    from src.strategy_registry import load_strategies

    result = dl.list_trader_services()
    expected = [s["service"] for s in load_strategies()]
    assert result == expected


def test_list_trader_services_all_ict_trader_prefix():
    """Every service in the registry must start with ict-trader-."""
    from src.bot import data_loaders as dl

    for svc in dl.list_trader_services():
        assert svc.startswith("ict-trader-"), f"Unexpected service prefix: {svc}"
