"""Adapter abstract base class for `market_raw` (S-AI-WS5-B-PART-1).

Canonical `market_raw` row shape: every adapter MUST emit dicts
with exactly these keys (any extra keys are rejected by the
builder schema check):

  ts (str)        — ISO 8601 UTC timestamp of the bar.
  symbol (str)    — e.g. "BTCUSDT".
  timeframe (str) — canonical token, e.g. "1m", "15m", "1h", "1d".
  open (float)    — bar open price.
  high (float)    — bar high price.
  low (float)     — bar low price.
  close (float)   — bar close price.
  volume (float)  — bar volume in base units.
  source (str)    — adapter name (`MarketRawAdapter.source`).

Leakage discipline: `market_raw` carries no labels. Downstream
`market_features` / regime-label datasets that derive features
from these bars are responsible for their own leakage tests.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Iterator, Mapping

CANONICAL_COLUMNS: tuple[str, ...] = (
    "ts",
    "symbol",
    "timeframe",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "source",
)

# Type tokens used by the builder's schema check + by
# `validate_dataset(...)`. Strings are the wire format.
CANONICAL_SCHEMA: Mapping[str, type] = {
    "ts": str,
    "symbol": str,
    "timeframe": str,
    "open": float,
    "high": float,
    "low": float,
    "close": float,
    "volume": float,
    "source": str,
}


class MarketRawAdapter(ABC):
    """Pluggable upstream-source adapter for the `market_raw` family.

    Subclasses set `source` (a stable token like `csv`,
    `bybit_v5_offvm`, `yfinance_daily`) and implement
    `iter_bars(**kwargs)` to yield canonical rows. The dataset
    builder records the adapter name in metadata so a future
    operator can reproduce the build.
    """

    source: ClassVar[str]

    @abstractmethod
    def iter_bars(self, **kwargs: Any) -> Iterator[Mapping[str, Any]]:
        """Yield canonical `market_raw` rows. See `CANONICAL_COLUMNS`."""
