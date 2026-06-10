"""M15 Phase 1 — data-only connectors + market-hours gate.

Covers:
- AlpacaMarketData / OandaMarketData ``get_ohlcv`` normalisation against
  mocked HTTP responses (no network).
- ``_build_exchange_client`` routing for the new exchange names.
- ``market_hours.is_market_open`` session logic (fx weekend close,
  us_equity RTH incl. the DST month approximation, crypto 24/7,
  fail-permissive unknown class).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from src.exchange.alpaca_connector import AlpacaMarketData
from src.exchange.oanda_connector import OandaMarketData, _to_instrument
from src.runtime.market_data import _build_exchange_client
from src.runtime.market_hours import asset_class_for_exchange, is_market_open


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_alpaca_get_ohlcv_normalises(monkeypatch):
    payload = {
        "bars": [
            {"t": "2026-06-10T15:00:00Z", "o": 2, "h": 3, "l": 1, "c": 2.5, "v": 100},
            {"t": "2026-06-10T14:55:00Z", "o": 1, "h": 2, "l": 0.5, "c": 2.0, "v": 50},
        ]
    }
    monkeypatch.setattr(
        "src.exchange.alpaca_connector.requests.get",
        lambda *a, **k: _Resp(payload),
    )
    df = AlpacaMarketData(api_key="k", api_secret="s").get_ohlcv("QQQ", "5m", limit=2)
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    # desc payload must come back ascending
    assert df["timestamp"].is_monotonic_increasing
    assert df["timestamp"].dt.tz is not None
    assert len(df) == 2


def test_alpaca_returns_none_on_error_and_empty(monkeypatch):
    monkeypatch.setattr(
        "src.exchange.alpaca_connector.requests.get",
        lambda *a, **k: _Resp({"bars": []}),
    )
    assert AlpacaMarketData(api_key="k", api_secret="s").get_ohlcv("QQQ", "5m", limit=2) is None
    monkeypatch.setattr(
        "src.exchange.alpaca_connector.requests.get",
        lambda *a, **k: _Resp({}, status=403),
    )
    assert AlpacaMarketData(api_key="k", api_secret="s").get_ohlcv("QQQ", "5m", limit=2) is None
    assert AlpacaMarketData(api_key="k", api_secret="s").get_ohlcv("QQQ", "7m", limit=2) is None


def test_oanda_get_ohlcv_normalises_and_drops_incomplete(monkeypatch):
    payload = {
        "candles": [
            {
                "time": "2026-06-10T15:00:00.000000000Z",
                "mid": {"o": "1.1", "h": "1.2", "l": "1.0", "c": "1.15"},
                "volume": 42,
                "complete": True,
            },
            {
                "time": "2026-06-10T15:15:00.000000000Z",
                "mid": {"o": "1.15", "h": "1.16", "l": "1.14", "c": "1.155"},
                "volume": 7,
                "complete": False,
            },
        ]
    }
    monkeypatch.setattr(
        "src.exchange.oanda_connector.requests.get",
        lambda *a, **k: _Resp(payload),
    )
    df = OandaMarketData(api_token="t").get_ohlcv("EURUSD", "15m", limit=2)
    assert len(df) == 1  # incomplete candle dropped
    assert df["close"].dtype.kind == "f"
    assert df["timestamp"].dt.tz is not None


def test_oanda_instrument_mapping():
    assert _to_instrument("EURUSD") == "EUR_USD"
    assert _to_instrument("EUR_USD") == "EUR_USD"
    assert _to_instrument("XAUUSD") == "XAU_USD"


def test_build_exchange_client_routes_new_exchanges():
    assert isinstance(_build_exchange_client({"EXCHANGE": "alpaca"}), AlpacaMarketData)
    assert isinstance(_build_exchange_client({"EXCHANGE": "oanda"}), OandaMarketData)
    with pytest.raises(ValueError):
        _build_exchange_client({"EXCHANGE": "nonsense"})


# ---------------------------------------------------------------- hours
def _dt(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_crypto_and_unknown_always_open():
    assert is_market_open("crypto", _dt(2026, 6, 13, 3, 0))  # Saturday
    assert is_market_open("whatever", _dt(2026, 6, 13, 3, 0))  # fail-permissive


def test_fx_weekend_close():
    assert is_market_open("fx", _dt(2026, 6, 12, 20, 59))  # Fri before 21:00
    assert not is_market_open("fx", _dt(2026, 6, 12, 21, 0))  # Fri 21:00
    assert not is_market_open("fx", _dt(2026, 6, 13, 12, 0))  # Saturday
    assert not is_market_open("fx", _dt(2026, 6, 14, 20, 59))  # Sun before 21:00
    assert is_market_open("fx", _dt(2026, 6, 14, 21, 0))  # Sunday reopen
    assert is_market_open("fx", _dt(2026, 6, 10, 3, 0))  # mid-week night


def test_us_equity_rth_dst_months():
    # June = DST month -> 13:30-20:00 UTC
    assert not is_market_open("us_equity", _dt(2026, 6, 10, 13, 29))
    assert is_market_open("us_equity", _dt(2026, 6, 10, 13, 30))
    assert is_market_open("us_equity", _dt(2026, 6, 10, 19, 59))
    assert not is_market_open("us_equity", _dt(2026, 6, 10, 20, 0))
    assert not is_market_open("us_equity", _dt(2026, 6, 13, 15, 0))  # Saturday


def test_us_equity_rth_standard_months():
    # January = standard time -> 14:30-21:00 UTC
    assert not is_market_open("us_equity", _dt(2026, 1, 7, 14, 29))
    assert is_market_open("us_equity", _dt(2026, 1, 7, 14, 30))
    assert is_market_open("us_equity", _dt(2026, 1, 7, 20, 59))
    assert not is_market_open("us_equity", _dt(2026, 1, 7, 21, 0))


def test_asset_class_for_exchange():
    assert asset_class_for_exchange("oanda") == "fx"
    assert asset_class_for_exchange("alpaca") == "us_equity"
    assert asset_class_for_exchange("bybit") == "crypto"
