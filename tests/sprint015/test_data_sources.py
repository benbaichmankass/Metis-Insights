"""Data-source adapter contract tests.

Network is stubbed via ``monkeypatch.setattr`` — these tests must run in
the lean sandbox without outbound HTTPS access. They verify each adapter
* parses a known-good response into the canonical OHLCV frame, and
* returns ``None`` (rather than raising) on a 4xx / network error so the
  orchestrator can fall through to the next source.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from scripts.sprint015 import data_sources as ds


def _stub_response(json_body, status_code=200):
    return SimpleNamespace(
        status_code=status_code,
        json=lambda: json_body,
        text="",
    )


def _ts(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Coinbase
# ---------------------------------------------------------------------------


def test_coinbase_parses_ohlcv(monkeypatch):
    body = [
        # ts, low, high, open, close, volume
        [1714521600, 60000.0, 61000.0, 60500.0, 60900.0, 12.5],
        [1714525200, 60900.0, 61500.0, 60900.0, 61400.0, 8.1],
    ]
    monkeypatch.setattr(ds.requests, "get", lambda *a, **kw: _stub_response(body))
    df = ds.fetch_coinbase("BTCUSDT", "1h", _ts(2024, 5, 1), _ts(2024, 5, 2))
    assert df is not None
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert df.iloc[0]["close"] == pytest.approx(60900.0)
    assert df.index.tz is not None  # UTC


def test_coinbase_returns_none_on_4xx(monkeypatch):
    monkeypatch.setattr(ds.requests, "get", lambda *a, **kw: _stub_response([], 403))
    df = ds.fetch_coinbase("BTCUSDT", "1h", _ts(2024, 5, 1), _ts(2024, 5, 2))
    assert df is None


def test_coinbase_returns_none_on_network_error(monkeypatch):
    def boom(*a, **kw):
        raise requests.ConnectionError("dns fail")
    monkeypatch.setattr(ds.requests, "get", boom)
    assert ds.fetch_coinbase("BTCUSDT", "1h", _ts(2024, 5, 1), _ts(2024, 5, 2)) is None


def test_coinbase_returns_none_for_unknown_timeframe():
    assert ds.fetch_coinbase("BTCUSDT", "3m", _ts(2024, 5, 1), _ts(2024, 5, 2)) is None


# ---------------------------------------------------------------------------
# Kraken
# ---------------------------------------------------------------------------


def test_kraken_parses_ohlcv_and_filters_window(monkeypatch):
    body = {
        "error": [],
        "result": {
            "XBTUSDT": [
                # ts, open, high, low, close, vwap, volume, count
                [1714521600, "60500", "61000", "60000", "60900", "60700", "12.5", 100],
                [1714525200, "60900", "61500", "60900", "61400", "61200", "8.1", 80],
                [1714528800, "61400", "61900", "61300", "61800", "61600", "6.2", 60],
            ],
            "last": 1714528800,
        },
    }
    monkeypatch.setattr(ds.requests, "get", lambda *a, **kw: _stub_response(body))
    df = ds.fetch_kraken("BTCUSDT", "1h", _ts(2024, 5, 1, 0), _ts(2024, 5, 1, 1))
    assert df is not None
    # Two of three rows are inside the [00:00..01:00] window.
    assert len(df) == 2


def test_kraken_returns_none_on_api_error(monkeypatch):
    body = {"error": ["EService:Unavailable"], "result": {}}
    monkeypatch.setattr(ds.requests, "get", lambda *a, **kw: _stub_response(body))
    assert ds.fetch_kraken("BTCUSDT", "1h", _ts(2024, 5, 1), _ts(2024, 5, 2)) is None


def test_kraken_pair_normalises_btc():
    assert ds._kraken_pair("BTCUSDT") == "XBTUSDT"
    assert ds._kraken_pair("ETHUSDT") == "ETHUSDT"


# ---------------------------------------------------------------------------
# CryptoCompare
# ---------------------------------------------------------------------------


def test_cryptocompare_parses_histohour(monkeypatch):
    body = {
        "Data": {
            "Data": [
                {"time": 1714521600, "open": 60500, "high": 61000, "low": 60000,
                 "close": 60900, "volumefrom": 12.5},
                {"time": 1714525200, "open": 60900, "high": 61500, "low": 60900,
                 "close": 61400, "volumefrom": 8.1},
            ]
        }
    }
    monkeypatch.setattr(ds.requests, "get", lambda *a, **kw: _stub_response(body))
    df = ds.fetch_cryptocompare("BTCUSDT", "1h", _ts(2024, 5, 1), _ts(2024, 5, 2))
    assert df is not None
    assert len(df) == 2


def test_cryptocompare_returns_none_for_sub_hour():
    assert ds.fetch_cryptocompare("BTCUSDT", "5m", _ts(2024, 5, 1), _ts(2024, 5, 2)) is None


# ---------------------------------------------------------------------------
# Orchestrator — fallthrough + DataUnavailableError
# ---------------------------------------------------------------------------


def test_fetch_ohlcv_falls_through_to_next_source(monkeypatch):
    real = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
        index=pd.to_datetime(["2024-05-01"], utc=True),
    )
    registry = [
        ("alpha", lambda *a, **kw: None),
        ("beta", lambda *a, **kw: None),
        ("gamma", lambda *a, **kw: real),
    ]
    df, src, attempts = ds.fetch_ohlcv(
        "BTCUSDT", "1h", _ts(2024, 5, 1), _ts(2024, 5, 2),
        source_registry=registry,
    )
    assert src == "gamma"
    assert len(df) == 1
    assert [a.source for a in attempts] == ["alpha", "beta", "gamma"]
    assert [a.ok for a in attempts] == [False, False, True]


def test_fetch_ohlcv_raises_when_all_sources_fail():
    registry = [
        ("alpha", lambda *a, **kw: None),
        ("beta", lambda *a, **kw: None),
    ]
    with pytest.raises(ds.DataUnavailableError):
        ds.fetch_ohlcv(
            "BTCUSDT", "1h", _ts(2024, 5, 1), _ts(2024, 5, 2),
            source_registry=registry,
        )


def test_default_registry_excludes_bybit():
    """No-leakage rule: training data must NOT come from the live venue."""
    names = [name for name, _ in ds._SOURCE_REGISTRY]
    assert "bybit" not in names
    assert "binance" not in names


# ---------------------------------------------------------------------------
# github-raw — tier-3 fallback (curated keyless datasets)
# ---------------------------------------------------------------------------


_COINMETRICS_SAMPLE = (
    "time,AdrActCnt,PriceUSD,ReferenceRateUSD,volume_reported_spot_usd_1d\n"
    "2024-05-01,1,60000.0,,12500000\n"
    "2024-05-02,1,60500.0,,11800000\n"
    "2024-05-03,1,59800.0,,9900000\n"
)


def test_github_raw_returns_none_for_unregistered_pair():
    assert ds.fetch_github_raw(
        "DOGEUSDT", "1d", _ts(2024, 5, 1), _ts(2024, 5, 4),
    ) is None


def test_github_raw_returns_none_for_unsupported_timeframe():
    """Coinmetrics is daily-only; sub-daily timeframes must NOT silently
    upsample. The adapter returns None so the orchestrator falls
    through (or raises) rather than producing an OHLC frame that lies
    about strategy behaviour."""
    assert ds.fetch_github_raw(
        "BTCUSDT", "5m", _ts(2024, 5, 1), _ts(2024, 5, 4),
    ) is None


def test_github_raw_parses_coinmetrics_daily(monkeypatch):
    monkeypatch.setattr(ds.requests, "get",
                        lambda *a, **kw: SimpleNamespace(
                            status_code=200, text=_COINMETRICS_SAMPLE, json=lambda: {},
                        ))
    df = ds.fetch_github_raw("BTCUSDT", "1d", _ts(2024, 5, 1), _ts(2024, 5, 3))
    assert df is not None
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 3
    # Each bar's OHLC is the daily reference rate (no real intra-day shape).
    assert df.iloc[0]["close"] == pytest.approx(60000.0)
    assert df.iloc[0]["open"] == pytest.approx(60000.0)
    assert df.iloc[0]["high"] == pytest.approx(60000.0)
    assert df.iloc[0]["low"] == pytest.approx(60000.0)


def test_github_raw_returns_none_on_4xx(monkeypatch):
    monkeypatch.setattr(ds.requests, "get",
                        lambda *a, **kw: SimpleNamespace(
                            status_code=403, text="forbidden", json=lambda: {},
                        ))
    assert ds.fetch_github_raw(
        "BTCUSDT", "1d", _ts(2024, 5, 1), _ts(2024, 5, 3),
    ) is None


def test_github_raw_is_last_in_default_registry():
    """Tier-3 fallback ordering — public-exchange adapters must run first
    so live-quality data wins when available."""
    names = [name for name, _ in ds._SOURCE_REGISTRY]
    assert names[-1] == "github_raw"


def test_github_dataset_registry_only_serves_daily():
    """Sanity: the curated github registry must NOT register a sub-daily
    pair (that would let coinmetrics-style daily reference rates
    masquerade as 5m/15m bars)."""
    for (symbol, timeframe), entry in ds._GITHUB_DATASETS.items():
        assert timeframe == "1d", (
            f"{symbol} {timeframe}: only daily entries allowed (no fake "
            f"intraday from daily refrate)"
        )
