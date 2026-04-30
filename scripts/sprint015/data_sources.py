"""S-015 multi-source keyless OHLCV fetcher.

Adapter contract: each ``fetch_<source>`` function takes
``(symbol, timeframe, start_utc, end_utc)`` and returns a normalised
``pd.DataFrame`` indexed by UTC ``DatetimeIndex`` with columns
``[open, high, low, close, volume]`` — or ``None`` if the source is
unreachable / rejected the request. Adapters never raise on transport
errors; they yield ``None`` so the orchestrator can fall through.

**Bybit is intentionally excluded** — that's the live execution venue,
training data must come from elsewhere to avoid leakage between the
training set and live fills.

Source order (per S-015 prompt § Data contract):

1. Coinbase Exchange public REST
2. Kraken public REST
3. yfinance (Yahoo Finance crypto pairs)
4. CryptoCompare keyless tier
5. HuggingFace community datasets (placeholder — wire when needed)

If every source returns ``None``, ``fetch_ohlcv`` raises
``DataUnavailableError`` so the caller sees a loud failure rather than
silently substituting synthetic data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 15
USER_AGENT = "ict-trading-bot/sprint015 (+https://github.com/the-lizardking/ict-trading-bot)"

# Coinbase + Kraken use distinct timeframe spellings; normalise here.
_COINBASE_GRANULARITY = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
_KRAKEN_INTERVAL = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 1440}
_YFINANCE_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "1d": "1d"}
_CRYPTOCOMPARE_PATH = {"1h": "histohour", "1d": "histoday"}

_NORMALISED_COLUMNS = ["open", "high", "low", "close", "volume"]


class DataUnavailableError(RuntimeError):
    """Every keyless source rejected the request — caller must fail loudly."""


@dataclass(frozen=True)
class FetchAttempt:
    source: str
    ok: bool
    detail: str = ""


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce to UTC DatetimeIndex with the canonical column set."""
    if df.empty:
        return df
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df[[c for c in _NORMALISED_COLUMNS if c in df.columns]].astype(float)
    return df.sort_index()


def _coinbase_product(symbol: str) -> str:
    """Map ``BTCUSDT`` -> ``BTC-USD`` (Coinbase doesn't list USDT pairs for
    most majors; quote in USD is the keyless equivalent)."""
    s = symbol.upper().replace("/", "").replace(":", "")
    base = s[:-4] if s.endswith("USDT") else s[:-3]
    return f"{base}-USD"


def fetch_coinbase(symbol: str, timeframe: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
    if timeframe not in _COINBASE_GRANULARITY:
        return None
    url = f"https://api.exchange.coinbase.com/products/{_coinbase_product(symbol)}/candles"
    params = {
        "granularity": _COINBASE_GRANULARITY[timeframe],
        "start": start.astimezone(timezone.utc).isoformat(),
        "end": end.astimezone(timezone.utc).isoformat(),
    }
    try:
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_S, headers={"User-Agent": USER_AGENT})
        if r.status_code >= 400:
            logger.info("coinbase %s -> HTTP %s", symbol, r.status_code)
            return None
        rows = r.json()  # [[ts, low, high, open, close, volume], ...]
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["ts", "low", "high", "open", "close", "volume"])
        df.index = pd.to_datetime(df["ts"], unit="s", utc=True)
        return _normalise(df)
    except (requests.RequestException, ValueError) as exc:  # noqa: BLE001
        logger.info("coinbase fetch failed: %s", exc.__class__.__name__)
        return None


def _kraken_pair(symbol: str) -> str:
    """Map ``BTCUSDT`` -> ``XBTUSDT`` (Kraken uses XBT for BTC)."""
    s = symbol.upper().replace("/", "").replace(":", "")
    if s.startswith("BTC"):
        s = "XBT" + s[3:]
    return s


def fetch_kraken(symbol: str, timeframe: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
    if timeframe not in _KRAKEN_INTERVAL:
        return None
    url = "https://api.kraken.com/0/public/OHLC"
    params = {
        "pair": _kraken_pair(symbol),
        "interval": _KRAKEN_INTERVAL[timeframe],
        "since": int(start.astimezone(timezone.utc).timestamp()),
    }
    try:
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_S, headers={"User-Agent": USER_AGENT})
        if r.status_code >= 400:
            return None
        body = r.json()
        if body.get("error"):
            logger.info("kraken error: %s", body["error"])
            return None
        result = body.get("result") or {}
        # Kraken returns the actual pair name as a key alongside "last"; strip the latter.
        pair_rows: list = []
        for key, val in result.items():
            if key == "last":
                continue
            if isinstance(val, list):
                pair_rows = val
                break
        if not pair_rows:
            return None
        df = pd.DataFrame(pair_rows, columns=["ts", "open", "high", "low", "close", "vwap", "volume", "count"])
        df.index = pd.to_datetime(df["ts"].astype(int), unit="s", utc=True)
        df = df.astype({c: float for c in ["open", "high", "low", "close", "volume"]})
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        df = df.loc[(df.index >= start_ts) & (df.index <= end_ts)]
        return _normalise(df)
    except (requests.RequestException, ValueError, KeyError) as exc:  # noqa: BLE001
        logger.info("kraken fetch failed: %s", exc.__class__.__name__)
        return None


def fetch_yfinance(symbol: str, timeframe: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
    if timeframe not in _YFINANCE_INTERVAL:
        return None
    try:
        import yfinance as yf  # noqa: WPS433 — optional, may be absent
    except ImportError:
        return None
    ticker = symbol.upper().replace("/", "").replace(":", "")
    if ticker.endswith("USDT"):
        ticker = f"{ticker[:-4]}-USD"
    elif ticker.endswith("USD"):
        ticker = f"{ticker[:-3]}-USD"
    try:
        df = yf.download(
            ticker,
            start=start.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            end=end.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            interval=_YFINANCE_INTERVAL[timeframe],
            progress=False,
            auto_adjust=False,
        )
        if df is None or df.empty:
            return None
        df = df.rename(columns={c: c.lower() for c in df.columns})
        return _normalise(df)
    except Exception as exc:  # noqa: BLE001 — yfinance raises a wide variety
        logger.info("yfinance fetch failed: %s", exc.__class__.__name__)
        return None


def fetch_cryptocompare(symbol: str, timeframe: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
    """Keyless tier — `histohour` and `histoday` only; sub-hourly is paid."""
    path = _CRYPTOCOMPARE_PATH.get(timeframe)
    if path is None:
        return None
    s = symbol.upper().replace("/", "").replace(":", "")
    fsym = "BTC" if s.startswith("BTC") else s[:-4] if s.endswith("USDT") else s[:-3]
    tsym = "USD" if s.endswith("USDT") or s.endswith("USD") else "USD"
    url = f"https://min-api.cryptocompare.com/data/v2/{path}"
    params = {
        "fsym": fsym,
        "tsym": tsym,
        "toTs": int(end.astimezone(timezone.utc).timestamp()),
        "limit": 2000,
    }
    try:
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_S, headers={"User-Agent": USER_AGENT})
        if r.status_code >= 400:
            return None
        rows = ((r.json().get("Data") or {}).get("Data") or [])
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df["time"].astype(int), unit="s", utc=True)
        df = df.rename(columns={"volumefrom": "volume"})
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        df = df.loc[(df.index >= start_ts) & (df.index <= end_ts)]
        return _normalise(df)
    except (requests.RequestException, ValueError, KeyError) as exc:  # noqa: BLE001
        logger.info("cryptocompare fetch failed: %s", exc.__class__.__name__)
        return None


def fetch_huggingface(symbol: str, timeframe: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
    """Placeholder — wire to a specific HF community OHLCV dataset when one
    is identified. Returning ``None`` keeps the source registered without
    masking failures from the upstream four sources."""
    return None


# Order matters: try the most-trusted public exchange first, fall through.
_SOURCE_REGISTRY: List[tuple[str, Callable[..., Optional[pd.DataFrame]]]] = [
    ("coinbase", fetch_coinbase),
    ("kraken", fetch_kraken),
    ("yfinance", fetch_yfinance),
    ("cryptocompare", fetch_cryptocompare),
    ("huggingface", fetch_huggingface),
]


def fetch_ohlcv(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    *,
    source_registry: Optional[List[tuple[str, Callable[..., Optional[pd.DataFrame]]]]] = None,
) -> tuple[pd.DataFrame, str, List[FetchAttempt]]:
    """Try each source in order; return ``(df, source_name, attempts)``.

    Raises ``DataUnavailableError`` if every source returned ``None``.
    The ``attempts`` list lets the caller record provenance per bucket.
    """
    sources = source_registry or _SOURCE_REGISTRY
    attempts: List[FetchAttempt] = []
    for name, fn in sources:
        df = fn(symbol, timeframe, start, end)
        if df is not None and not df.empty:
            attempts.append(FetchAttempt(source=name, ok=True, detail=f"{len(df)} rows"))
            return df, name, attempts
        attempts.append(FetchAttempt(source=name, ok=False))
    raise DataUnavailableError(
        f"every keyless source rejected {symbol} {timeframe} "
        f"{start.isoformat()}..{end.isoformat()} (attempts: "
        f"{', '.join(a.source for a in attempts)})"
    )
