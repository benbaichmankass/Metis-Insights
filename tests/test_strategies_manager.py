import pytest

from src.strategies_manager import StrategyManager, register, _REGISTRY


class _FixedStrategy:
    def score_breakout(self, candles_df):
        return {"signal": "CONFIRM", "prob_tp": 0.9}


class _CountingStrategy:
    def __init__(self):
        self.calls = 0

    def score_breakout(self, candles_df):
        self.calls += 1
        return {"signal": "CONFIRM", "calls": self.calls}


@pytest.fixture(autouse=True)
def _clean_registry():
    """Restore registry after each test; stub out breakout_confirmation to avoid joblib."""
    saved = dict(_REGISTRY)
    _REGISTRY.setdefault("breakout_confirmation", _FixedStrategy)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


def test_unknown_strategy_returns_error_signal():
    sm = StrategyManager()
    result = sm.get_signal("nonexistent_xyz", None)
    assert result["signal"] == "ERROR"
    assert "nonexistent_xyz" in result["message"]


def test_register_and_dispatch():
    register("_test_fixed", _FixedStrategy)
    sm = StrategyManager()
    result = sm.get_signal("_test_fixed", None)
    assert result["signal"] == "CONFIRM"


def test_list_strategies_includes_breakout_confirmation():
    sm = StrategyManager()
    assert "breakout_confirmation" in sm.list_strategies()


def test_list_strategies_includes_newly_registered():
    register("_test_listed", _FixedStrategy)
    sm = StrategyManager()
    assert "_test_listed" in sm.list_strategies()


def test_lazy_instantiation_reuses_instance():
    register("_test_counting", _CountingStrategy)
    sm = StrategyManager()
    sm.get_signal("_test_counting", None)
    sm.get_signal("_test_counting", None)
    instance = sm._instances["_test_counting"]
    assert isinstance(instance, _CountingStrategy)
    assert instance.calls == 2


def test_register_replaces_existing_class():
    register("_test_replaced", _FixedStrategy)
    register("_test_replaced", _CountingStrategy)
    sm = StrategyManager()
    sm.get_signal("_test_replaced", None)
    assert isinstance(sm._instances["_test_replaced"], _CountingStrategy)
