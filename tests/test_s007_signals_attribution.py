"""Tests for S-007 #116: signals/trades attribution via registry signal_prefixes."""
from __future__ import annotations

import sys
import types


# ---------------------------------------------------------------------------
# registry — signal_prefixes()
# ---------------------------------------------------------------------------

def test_signal_prefixes_vwap():
    from src.strategy_registry import signal_prefixes
    assert signal_prefixes("vwap") == ["vwap"]


def test_signal_prefixes_turtle_soup():
    """S-012 PR B1: turtle_soup is the new strategy alongside vwap."""
    from src.strategy_registry import signal_prefixes
    p = signal_prefixes("turtle_soup")
    for expected in ("turtle_soup", "sweep_reversal"):
        assert expected in p


def test_signal_prefixes_unknown_strategy_raises():
    from src.strategy_registry import signal_prefixes
    import pytest
    with pytest.raises(KeyError):
        signal_prefixes("nonexistent_strategy")


def test_load_strategies_includes_signal_prefixes():
    """load_strategies() dicts must include a signal_prefixes key (S-007)."""
    from src.strategy_registry import load_strategies
    for s in load_strategies():
        assert "signal_prefixes" in s, f"'{s['name']}' missing signal_prefixes"
        assert isinstance(s["signal_prefixes"], list)


# ---------------------------------------------------------------------------
# data_loaders — _get_signal_prefixes registry-first
# ---------------------------------------------------------------------------

def test_get_signal_prefixes_uses_registry(monkeypatch):
    """_get_signal_prefixes returns registry prefixes when available."""
    from src.bot import data_loaders as dl

    fake_reg = types.ModuleType("src.strategy_registry")
    fake_reg.signal_prefixes = lambda name: ["custom_prefix"]
    monkeypatch.setitem(sys.modules, "src.strategy_registry", fake_reg)

    result = dl._get_signal_prefixes("any_strategy")
    assert result == ("custom_prefix",)


def test_get_signal_prefixes_falls_back_to_hardcoded(monkeypatch):
    """Falls back to hardcoded map when registry is unavailable."""
    from src.bot import data_loaders as dl

    class _Boom:
        def __getattr__(self, _):
            raise RuntimeError("registry broken")

    monkeypatch.setitem(sys.modules, "src.strategy_registry", _Boom())
    result = dl._get_signal_prefixes("breakout_confirmation")
    assert "ml_breakout" in result


def test_get_signal_prefixes_empty_for_unknown_strategy(monkeypatch):
    """Unknown strategy with no registry and not in fallback → empty tuple."""
    from src.bot import data_loaders as dl

    class _Boom:
        def __getattr__(self, _):
            raise RuntimeError("registry broken")

    monkeypatch.setitem(sys.modules, "src.strategy_registry", _Boom())
    result = dl._get_signal_prefixes("totally_unknown")
    assert result == ()


# ---------------------------------------------------------------------------
# vwap attribution — the key bug this PR fixes
# ---------------------------------------------------------------------------

def test_vwap_signal_prefixes_are_not_empty():
    """vwap had no attribution before S-007 (ternary always wrote 'trade_signal')."""
    from src.strategy_registry import signal_prefixes
    p = signal_prefixes("vwap")
    assert p, "vwap must have signal_prefixes so its DB rows are attributed correctly"
    assert p[0] == "vwap"


def test_data_loaders_vwap_prefixes_match_registry():
    """data_loaders._get_signal_prefixes('vwap') must return ('vwap',)."""
    from src.bot import data_loaders as dl
    result = dl._get_signal_prefixes("vwap")
    assert "vwap" in result


# ---------------------------------------------------------------------------
# registry YAML completeness — all 4 strategies have signal_prefixes
# ---------------------------------------------------------------------------

def test_all_strategies_have_nonempty_signal_prefixes():
    from src.strategy_registry import load_strategies
    for s in load_strategies():
        assert s["signal_prefixes"], (
            f"Strategy '{s['name']}' has empty signal_prefixes in strategies.yaml. "
            "Add at least one prefix so its signals can be attributed."
        )
