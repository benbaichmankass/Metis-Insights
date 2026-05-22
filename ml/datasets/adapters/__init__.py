"""`market_raw` source adapters (S-AI-WS5-B-PART-1).

Multi-source intake framework: each adapter normalises a specific
upstream into the canonical `market_raw` row shape
(`base.CANONICAL_COLUMNS`). The dataset builder dispatches by
adapter name.

Operator directive (2026-05-10): "we should have a running list of
various sources to choose from — we should have capacity to intake
different types from different sources and normalize it to the
training center format."
"""
from .base import CANONICAL_COLUMNS, CANONICAL_SCHEMA, MarketRawAdapter
from .bybit_offvm import BybitOffvmMarketRawAdapter
from .csv import CsvMarketRawAdapter
from .ibkr_offvm import IBKRHistoricalMarketRawAdapter
from .registry import ADAPTER_REGISTRY, get_adapter, list_adapters
from .yfinance_offvm import YFinanceOffvmMarketRawAdapter

__all__ = [
    "ADAPTER_REGISTRY",
    "BybitOffvmMarketRawAdapter",
    "CANONICAL_COLUMNS",
    "CANONICAL_SCHEMA",
    "CsvMarketRawAdapter",
    "IBKRHistoricalMarketRawAdapter",
    "MarketRawAdapter",
    "YFinanceOffvmMarketRawAdapter",
    "get_adapter",
    "list_adapters",
]
