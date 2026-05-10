"""Dataset family registry (WS3 + WS5-A + WS5-B-PART-1)."""
from __future__ import annotations

from typing import Mapping, Type

from .builder import DatasetBuilder
from .families.backtest_results import BacktestResultsBuilder
from .families.market_raw import MarketRawBuilder
from .families.trade_outcomes import TradeOutcomesBuilder

FAMILY_REGISTRY: Mapping[str, Type[DatasetBuilder]] = {
    BacktestResultsBuilder.family: BacktestResultsBuilder,
    MarketRawBuilder.family: MarketRawBuilder,
    TradeOutcomesBuilder.family: TradeOutcomesBuilder,
}


def list_families() -> list[str]:
    return sorted(FAMILY_REGISTRY.keys())


def get_builder(family: str) -> DatasetBuilder:
    try:
        cls = FAMILY_REGISTRY[family]
    except KeyError as e:
        raise KeyError(
            f"unknown family {family!r}; known: {list_families()}"
        ) from e
    return cls()
