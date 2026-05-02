"""Multi-source candle loader for training runs.

Tries free sources in order: yfinance -> Coinbase public -> Bybit public.
Each source has explicit error reporting so failures are debuggable.
No Binance per docs/claude/testing-policy.md.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

YF_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h"}
YF_TICKER = {"BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD"}
YF_MAX_DAYS = {"1m": 7, "5m": 60, "15m": 729, "1h": 729}

UA = {"User-Agent": "ict-trading-bot/1.0"}


def _from_yfinance(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    import yfinance as yf

    ticker = YF_TICKER.get(symbol)
    if not ticker:
        raise RuntimeError(f"yfinance: no ticker mapping for {symbol}")
    capped = min(days, YF_MAX_DAYS[timeframe])
    df = yf.download(
        ticker, period=f"{capped}d", interval=YF_INTERVAL[timeframe],
        progress=False, auto_adjust=False,
    )
    if df is None or df.empty:
        raise RuntimeError(f"yfinance returned empty frame for {ticker}/{timeframe}/{capped}d")
    if hasattr(df.columns, "get_level_values"):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.reset_index().rename(columns={
        "Datetime": "timestamp", "Date": "timestamp",
        "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume",
    })
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df[["timestamp", "open", "high", "low", "close", "volume"]].astype(
        {c: float for c in ["open", "high", "low", "close", "volume"]}
    )


def _from_coinbase(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    granularity = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}[timeframe]
    pair = {"BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD"}[symbol]
    end = int(time.time())
    start = end - days * 86400
    rows: list = []
    cursor = end
    step = 300 * granularity
    while cursor > start:
        s = max(start, cursor - step)
        r = requests.get(
            f"https://api.exchange.coinbase.com/products/{pair}/candles",
            params={
                "granularity": granularity,
                "start": pd.Timestamp(s, unit="s", tz="UTC").isoformat(),
                "end": pd.Timestamp(cursor, unit="s", tz="UTC").isoformat(),
            },
            headers=UA, timeout=30,
        )
        try:
            batch = r.json()
        except Exception:
            raise RuntimeError(f"Coinbase non-JSON ({r.status_code}): {r.text[:200]}")
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(batch)
        cursor = s
    if not rows:
        raise RuntimeError("Coinbase returned no candles")
    df = pd.DataFrame(rows, columns=["ts", "low", "high", "open", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["ts"].astype(int), unit="s", utc=True)
    return df.sort_values("timestamp").reset_index(drop=True).drop(columns=["ts"])


def _from_bybit(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    interval = {"1m": "1", "5m": "5", "15m": "15", "1h": "60"}[timeframe]
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000
    rows: list = []
    cursor = end_ms
    payload = None
    while cursor > start_ms:
        r = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": symbol,
                    "interval": interval, "end": cursor, "limit": 1000},
            headers=UA, timeout=30,
        )
        try:
            payload = r.json()
        except Exception:
            raise RuntimeError(f"Bybit non-JSON ({r.status_code}): {r.text[:200]}")
        batch = payload.get("result", {}).get("list", []) if isinstance(payload, dict) else []
        if not batch:
            break
        rows.extend(batch)
        cursor = int(batch[-1][0]) - 1
        if len(batch) < 1000:
            break
    if not rows:
        raise RuntimeError(f"Bybit empty: {payload!r}")
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    df = df.astype({c: float for c in ["open", "high", "low", "close", "volume"]})
    df["timestamp"] = pd.to_datetime(df["ts"].astype(int), unit="ms", utc=True)
    return df.sort_values("timestamp").reset_index(drop=True).drop(columns=["ts", "turnover"])


def load_candles(symbol: str, timeframe: str, days: int, cache_dir: Path) -> pd.DataFrame:
    cache = cache_dir / f"{symbol}_{timeframe}_{days}d.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    errors = []
    for name, fn in [("yfinance", _from_yfinance), ("coinbase", _from_coinbase), ("bybit", _from_bybit)]:
        try:
            df = fn(symbol, timeframe, days)
            print(f"  [{name}] {symbol} {timeframe} {days}d: {len(df)} bars")
            df.to_parquet(cache)
            return df
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(f"  [{name}] failed: {e}")
    raise RuntimeError(f"All sources failed for {symbol} {timeframe}: " + " | ".join(errors))
