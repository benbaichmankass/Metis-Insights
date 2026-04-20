from strategies.breakout_confirmation import BreakoutConfirmationStrategy


class StrategyManager:
    def __init__(self):
        self.strategies = {
            "breakout_confirmation": BreakoutConfirmationStrategy()
        }

    def get_signal(self, strategy_name, candles_df):
        if strategy_name not in self.strategies:
            return {
                "signal": "ERROR",
                "message": f"Unknown strategy: {strategy_name}"
            }

        return self.strategies[strategy_name].score_breakout(candles_df)
