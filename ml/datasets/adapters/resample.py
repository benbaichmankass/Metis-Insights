"""Resample adapter for `market_raw` — derive higher timeframes from a
cached finer-grained dataset instead of re-downloading.

Architecture (2026-05-22 directive): pull the finest granularity we need
(5m) ONCE, persist it, and build every higher timeframe (15m, 1h, …) by
aggregating the cached 5m bars. No second network pull, no exchange
rate-limit risk, and one canonical store per symbol.

This adapter takes a previously-built `market_raw` dataset directory (or
its `data.jsonl`) and emits canonical bars at the requested coarser
timeframe. OHLCV aggregation per target bucket: open=first, high=max,
low=min, close=last, volume=sum, with left-closed/left-labelled bins
(a 15:00 bar covers [15:00, 15:15)). Pure local computation — no network,
so no off-VM guard.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

from .base import MarketRawAdapter

# Canonical timeframe token -> pandas offset alias.
_PANDAS_RULE: Mapping[str, str] = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
}


class ResampleMarketRawAdapter(MarketRawAdapter):
    source: ClassVar[str] = "resample"

    def iter_bars(
        self,
        *,
        symbol: str,
        timeframe: str,
        source_path: str | Path,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        if timeframe not in _PANDAS_RULE:
            raise ValueError(
                f"unsupported timeframe {timeframe!r}; known: {sorted(_PANDAS_RULE)}"
            )
        import pandas as pd

        src = Path(source_path)
        data_file = src / "data.jsonl" if src.is_dir() else src
        if not data_file.is_file():
            raise FileNotFoundError(f"source market_raw not found at {data_file}")

        rows = [json.loads(line) for line in data_file.read_text().splitlines() if line.strip()]
        if not rows:
            return
        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"]).set_index("ts").sort_index()
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df.get("volume"), errors="coerce").fillna(0.0)

        agg = (
            df.resample(_PANDAS_RULE[timeframe], label="left", closed="left")
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            )
            .dropna(subset=["open", "high", "low", "close"])
        )
        for ts, r in agg.iterrows():
            yield {
                "ts": ts.isoformat().replace("+00:00", "Z"),
                "symbol": symbol,
                "timeframe": timeframe,
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]) if r["volume"] == r["volume"] else 0.0,
                "source": self.source,
            }
