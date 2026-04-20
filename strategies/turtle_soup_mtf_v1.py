from strategies.base_strategy import BaseStrategy

class TurtleSoupMTFv1(BaseStrategy):
    def __init__(self, config):
        super().__init__(config)
        # PF 1.34 - Top sweep config
        self.sweep_lookback_15m = 60
        self.min_sweep_buffer_bps = 12
        self.min_body_to_range = 0.6
        self.atr_stop_mult = 0.35
        self.be_at_r = 1.0
        self.trail_atr_mult = 1.4
