"""Adapter dispatch by name (S-AI-WS5-B-PART-1)."""
from __future__ import annotations

from typing import Mapping, Type

from .base import MarketRawAdapter
from .bybit_offvm import BybitOffvmMarketRawAdapter
from .csv import CsvMarketRawAdapter

ADAPTER_REGISTRY: Mapping[str, Type[MarketRawAdapter]] = {
    CsvMarketRawAdapter.source: CsvMarketRawAdapter,
    BybitOffvmMarketRawAdapter.source: BybitOffvmMarketRawAdapter,
}


def list_adapters() -> list[str]:
    return sorted(ADAPTER_REGISTRY.keys())


def get_adapter(name: str) -> MarketRawAdapter:
    try:
        cls = ADAPTER_REGISTRY[name]
    except KeyError as e:
        raise KeyError(
            f"unknown market_raw adapter {name!r}; known: {list_adapters()}"
        ) from e
    return cls()
