"""S-033 regression tests
(architecture-audit-2026-05-02 § P1-8).

Pre-PR ``turtle_soup_signal_builder`` and ``vwap_signal_builder``
in ``src/runtime/pipeline.py`` instantiated a Bybit / Binance
connector and called ``get_ohlcv()`` inline. Per CLAUDE.md
§ Architecture rules § 2 the strategy / signal layer should be
pure: given candles + config, emit a package; don't decide where
the candles come from.

Post-PR a new ``src/runtime/market_data.py::fetch_candles`` owns
the connector + fetch + DataFrame normalisation in one place. The
pipeline builders call it; on a missing fetch they raise the same
``RuntimeError`` the legacy code raised so existing callers + tests
see no behaviour change.

Tests pin:
  1. ``fetch_candles`` returns a DataFrame with the canonical
     column order + numeric OHLCV columns.
  2. Empty / None responses → ``None`` (no exception).
  3. Connector init errors → ``None`` (logged).
  4. ``get_ohlcv`` errors → ``None`` (logged).
  5. The pre-existing DataFrame passthrough (exchanges that already
     return a DF) survives the move.
  6. ``pipeline._build_killzone_exchange`` still resolves to the
     canonical implementation in ``market_data`` (back-compat).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# fetch_candles — happy path
# ---------------------------------------------------------------------------


CANDLE_ROWS = [
    [1714000000000, "100.0", "101.0", "99.0", "100.5", "12.34"],
    [1714000300000, "100.5", "102.0", "100.0", "101.0", "11.0"],
    [1714000600000, "101.0", "103.0", "100.5", "102.5", "9.5"],
]


class TestFetchCandlesHappyPath:
    def test_list_response_becomes_normalised_dataframe(self):
        from src.runtime import market_data

        fake_client = MagicMock()
        fake_client.get_ohlcv.return_value = CANDLE_ROWS

        with patch.object(market_data, "_build_exchange_client",
                          return_value=fake_client):
            df = market_data.fetch_candles(
                "BTCUSDT", "5m", settings={"EXCHANGE": "bybit"}, limit=100,
            )

        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == [
            "timestamp", "open", "high", "low", "close", "volume",
        ]
        # Numeric coercion: strings → float.
        assert df["close"].dtype.kind == "f"
        assert df["volume"].dtype.kind == "f"
        assert len(df) == 3
        # Values survived the cast.
        assert df["close"].iloc[0] == pytest.approx(100.5)

    def test_dataframe_response_passes_through(self):
        from src.runtime import market_data

        source_df = pd.DataFrame(CANDLE_ROWS, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
        ])
        # Simulate a connector that already returns a DF.
        fake_client = MagicMock()
        fake_client.get_ohlcv.return_value = source_df

        with patch.object(market_data, "_build_exchange_client",
                          return_value=fake_client):
            df = market_data.fetch_candles(
                "BTCUSDT", "5m", settings={}, limit=100,
            )

        assert isinstance(df, pd.DataFrame)
        # We mutate via .copy() in fetch_candles — caller's source DF
        # should NOT be modified by the numeric coercion.
        assert df is not source_df
        assert df["close"].dtype.kind == "f"

    def test_forwards_symbol_timeframe_limit(self):
        from src.runtime import market_data

        fake_client = MagicMock()
        fake_client.get_ohlcv.return_value = CANDLE_ROWS

        with patch.object(market_data, "_build_exchange_client",
                          return_value=fake_client):
            market_data.fetch_candles(
                "ETHUSDT", "15m", settings={}, limit=200,
            )

        fake_client.get_ohlcv.assert_called_once_with(
            "ETHUSDT", "15m", limit=200,
        )


# ---------------------------------------------------------------------------
# fetch_candles — error / empty paths
# ---------------------------------------------------------------------------


class TestFetchCandlesErrorPaths:
    def test_none_response_returns_none(self):
        from src.runtime import market_data

        fake_client = MagicMock()
        fake_client.get_ohlcv.return_value = None

        with patch.object(market_data, "_build_exchange_client",
                          return_value=fake_client):
            df = market_data.fetch_candles(
                "BTCUSDT", "5m", settings={}, limit=100,
            )
        assert df is None

    def test_empty_list_returns_none(self):
        from src.runtime import market_data

        fake_client = MagicMock()
        fake_client.get_ohlcv.return_value = []

        with patch.object(market_data, "_build_exchange_client",
                          return_value=fake_client):
            df = market_data.fetch_candles(
                "BTCUSDT", "5m", settings={}, limit=100,
            )
        assert df is None

    def test_empty_dataframe_returns_none(self):
        from src.runtime import market_data

        fake_client = MagicMock()
        fake_client.get_ohlcv.return_value = pd.DataFrame(columns=[
            "timestamp", "open", "high", "low", "close", "volume",
        ])

        with patch.object(market_data, "_build_exchange_client",
                          return_value=fake_client):
            df = market_data.fetch_candles(
                "BTCUSDT", "5m", settings={}, limit=100,
            )
        assert df is None

    def test_connector_init_error_returns_none(self):
        from src.runtime import market_data

        with patch.object(
            market_data, "_build_exchange_client",
            side_effect=ValueError("Unsupported EXCHANGE value: kraken"),
        ):
            df = market_data.fetch_candles(
                "BTCUSDT", "5m", settings={"EXCHANGE": "kraken"}, limit=100,
            )
        assert df is None

    def test_get_ohlcv_raises_returns_none(self):
        from src.runtime import market_data

        fake_client = MagicMock()
        fake_client.get_ohlcv.side_effect = ConnectionError("offline")

        with patch.object(market_data, "_build_exchange_client",
                          return_value=fake_client):
            df = market_data.fetch_candles(
                "BTCUSDT", "5m", settings={}, limit=100,
            )
        assert df is None


# ---------------------------------------------------------------------------
# Connector picker preserves the legacy behaviour
# ---------------------------------------------------------------------------


class TestBuildExchangeClient:
    def test_unsupported_exchange_raises(self):
        from src.runtime import market_data
        with pytest.raises(ValueError):
            market_data._build_exchange_client({"EXCHANGE": "kraken"})

    def test_default_is_bybit(self, monkeypatch):
        """Pre-PR ``_build_killzone_exchange`` defaulted to Bybit when
        the EXCHANGE setting was missing. The new helper must too."""
        from src.runtime import market_data
        try:
            import src.exchange.bybit_connector  # noqa: F401
        except ImportError as exc:
            pytest.skip(f"bybit_connector import failed in sandbox: {exc}")

        called = {}

        class _FakeBybit:
            def __init__(self, **kw):
                called["kind"] = "bybit"
                called["kw"] = kw

        monkeypatch.setattr(
            "src.exchange.bybit_connector.BybitConnector",
            _FakeBybit,
        )
        client = market_data._build_exchange_client({})
        assert isinstance(client, _FakeBybit)
        assert called["kind"] == "bybit"


# ---------------------------------------------------------------------------
# pipeline back-compat shim
# ---------------------------------------------------------------------------


class TestPipelineShim:
    def test_build_killzone_exchange_delegates_to_market_data(
        self, monkeypatch,
    ):
        """Existing tests that monkeypatch
        ``pipeline._build_killzone_exchange`` MUST keep working —
        the function is preserved as a thin shim that delegates to
        the canonical helper."""
        try:
            from src.runtime import pipeline
        except ModuleNotFoundError as exc:
            pytest.skip(f"pipeline import failed in sandbox: {exc}")

        sentinel = object()
        from src.runtime import market_data
        with patch.object(market_data, "_build_exchange_client",
                          return_value=sentinel):
            assert pipeline._build_killzone_exchange({}) is sentinel
