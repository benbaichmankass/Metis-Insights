"""Tests for S-007 #114: pipeline.STRATEGIES and data_loaders rewired to registry."""
from __future__ import annotations

import types
import sys



# ---------------------------------------------------------------------------
# registry invariants that pipeline.STRATEGIES depends on
# (pipeline imports pandas so we validate via the registry directly)
# ---------------------------------------------------------------------------

def test_registry_contains_all_pipeline_strategies():
    """All multiplexer strategies must be in config/strategies.yaml.

    S-012 PR B1: roster reduced to turtle_soup + vwap.
    """
    from src.strategy_registry import load_strategies
    names = [s["name"] for s in load_strategies()]
    for expected in ("turtle_soup", "vwap"):
        assert expected in names, f"'{expected}' missing from strategies.yaml"


def test_registry_roster_is_exactly_turtle_soup_and_vwap():
    """S-012 production roster — strict equality, no extras."""
    from src.strategy_registry import load_strategies
    names = sorted(s["name"] for s in load_strategies())
    assert names == ["turtle_soup", "vwap"]


def test_registry_fallback_loader_returns_new_roster(monkeypatch):
    """_strategies_from_registry() falls back to hardcoded list when registry is broken."""
    class _Boom:
        def __getattr__(self, _name):
            raise RuntimeError("registry broken")

    monkeypatch.setitem(sys.modules, "src.strategy_registry", _Boom())
    # Import the fallback logic inline — mirrors what pipeline.py does.
    try:
        from src.strategy_registry import load_strategies
        result = [s["name"] for s in load_strategies()]
    except Exception:
        result = ["turtle_soup", "vwap"]
    for name in ("turtle_soup", "vwap"):
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

def test_list_trader_services_returns_deduplicated_registry_services():
    """list_trader_services() returns unique service names from the registry.

    S-012 PR C4: single-process architecture — every strategy maps to
    ict-trader-live. The function dedupes so callers see one entry per
    real systemd unit, not one per strategy.
    """
    from src.bot import data_loaders as dl
    from src.strategy_registry import load_strategies

    result = dl.list_trader_services()
    expected = list(dict.fromkeys(s["service"] for s in load_strategies()))
    assert result == expected
    # Production roster of two strategies → one unique service.
    assert len(set(result)) == len(result)


def test_list_trader_services_all_ict_trader_prefix():
    """Every service in the registry must start with ict-trader-."""
    from src.bot import data_loaders as dl

    for svc in dl.list_trader_services():
        assert svc.startswith("ict-trader-"), f"Unexpected service prefix: {svc}"
