"""Dataset family registry (WS3).

Maps `family` strings to concrete `DatasetBuilder` subclasses. New
families register themselves here so the CLI can dispatch by name.
"""
from __future__ import annotations

from typing import Mapping, Type

from .builder import DatasetBuilder
from .families.backtest_results import BacktestResultsBuilder

FAMILY_REGISTRY: Mapping[str, Type[DatasetBuilder]] = {
    BacktestResultsBuilder.family: BacktestResultsBuilder,
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
