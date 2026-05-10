"""CSV adapter for `market_raw` (S-AI-WS5-B-PART-1).

Reads a local CSV staged by the operator on a build host. No
network, no exchange creds; the cleanest first adapter and the
one used by tests.

Expected CSV columns (case-insensitive, extras ignored):
  ts, open, high, low, close, volume

The `symbol` and `timeframe` are passed via kwargs because they
describe the file's scope, not the rows.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

from .base import MarketRawAdapter

_REQUIRED_CSV_COLUMNS = ("ts", "open", "high", "low", "close")


class CsvMarketRawAdapter(MarketRawAdapter):
    source: ClassVar[str] = "csv"

    def iter_bars(
        self,
        *,
        csv_path: Path,
        symbol: str,
        timeframe: str,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        if not csv_path.is_file():
            raise FileNotFoundError(f"CSV not found at {csv_path}")
        with csv_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise ValueError(f"CSV at {csv_path} has no header row")
            normalized_fieldnames = {name.lower(): name for name in reader.fieldnames}
            missing = [
                col for col in _REQUIRED_CSV_COLUMNS
                if col not in normalized_fieldnames
            ]
            if missing:
                raise ValueError(
                    f"CSV at {csv_path} missing required columns: {missing}"
                )
            volume_col = normalized_fieldnames.get("volume")
            for raw in reader:
                row: dict[str, Any] = {
                    "ts": str(raw[normalized_fieldnames["ts"]]),
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "open": float(raw[normalized_fieldnames["open"]]),
                    "high": float(raw[normalized_fieldnames["high"]]),
                    "low": float(raw[normalized_fieldnames["low"]]),
                    "close": float(raw[normalized_fieldnames["close"]]),
                    "volume": (
                        float(raw[volume_col])
                        if volume_col and raw.get(volume_col) not in (None, "")
                        else 0.0
                    ),
                    "source": "csv",
                }
                yield row
