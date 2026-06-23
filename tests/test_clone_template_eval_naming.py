"""BL-20260611-003: clone-template strategies must name THEMSELVES (not the
parent template) in the non-actionable reason strings the units raise.

The mes/mgc/mhg/xauusd/etf families reuse ``trend_donchian.order_package`` /
``htf_pullback_trend_2h.order_package``. Before the fix those units hardcoded
``Strategy 'trend_donchian'`` / ``Strategy 'htf_pullback_trend_2h'`` in their
ValueError reasons, so an operator reading an ``mes_trend_long_1d_eval`` row saw
the parent name. The units now read ``cfg["strategy_label"]`` (the clone threads
its own name); the default is the canonical name for the flagship caller.
"""
from __future__ import annotations

import pandas as pd
import pytest


def _short_frame(n: int = 5) -> pd.DataFrame:
    # Far fewer rows than any window needs → the units raise the
    # "need at least N candles" ValueError, which carries the label.
    rows = [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
             "volume": 1.0} for _ in range(n)]
    return pd.DataFrame(rows)


def test_trend_donchian_unit_uses_strategy_label():
    from src.units.strategies.trend_donchian import order_package
    cfg = {"symbol": "MES", "timeframe": "1d",
           "strategy_label": "mes_trend_long_1d"}
    with pytest.raises(ValueError) as ei:
        order_package(cfg, candles_df=_short_frame())
    assert "mes_trend_long_1d" in str(ei.value)
    assert "trend_donchian'" not in str(ei.value)  # parent name not leaked


def test_trend_donchian_unit_defaults_to_canonical_name():
    from src.units.strategies.trend_donchian import order_package
    with pytest.raises(ValueError) as ei:
        order_package({"symbol": "BTCUSDT"}, candles_df=_short_frame())
    assert "trend_donchian" in str(ei.value)


def test_htf_pullback_unit_uses_strategy_label():
    from src.units.strategies.htf_pullback_trend_2h import order_package
    cfg = {"symbol": "MHG", "timeframe": "1d",
           "strategy_label": "mhg_pullback_1d"}
    with pytest.raises(ValueError) as ei:
        order_package(cfg, candles_df=_short_frame())
    assert "mhg_pullback_1d" in str(ei.value)
    assert "htf_pullback_trend_2h'" not in str(ei.value)


def test_htf_pullback_unit_defaults_to_canonical_name():
    from src.units.strategies.htf_pullback_trend_2h import order_package
    with pytest.raises(ValueError) as ei:
        order_package({"symbol": "BTCUSDT"}, candles_df=_short_frame())
    assert "htf_pullback_trend_2h" in str(ei.value)
