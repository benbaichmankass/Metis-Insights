_REGISTRY: dict[str, type] = {}


def register(name: str, strategy_class: type) -> None:
    """Add or replace a strategy in the registry without editing this file."""
    _REGISTRY[name] = strategy_class


def _ensure_defaults() -> None:
    if "breakout_confirmation" not in _REGISTRY:
        from strategies.breakout_confirmation import BreakoutConfirmationStrategy
        _REGISTRY["breakout_confirmation"] = BreakoutConfirmationStrategy


class StrategyManager:
    def __init__(self):
        _ensure_defaults()
        self._instances: dict = {}

    def get_signal(self, strategy_name: str, candles_df):
        if strategy_name not in _REGISTRY:
            return {"signal": "ERROR", "message": f"Unknown strategy: {strategy_name}"}
        if strategy_name not in self._instances:
            self._instances[strategy_name] = _REGISTRY[strategy_name]()
        return self._instances[strategy_name].score_breakout(candles_df)

    def list_strategies(self) -> list[str]:
        return list(_REGISTRY)
