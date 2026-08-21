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


class TestExchangeClientCache:
    """The connector memo added 2026-08-10 (BL-20260810-TICK-CHAIN-260S-PER-TICK).

    It exists because every strategy builder constructed a fresh ccxt client and
    paid a full market-catalogue download (~3.2s x ~52 builders of a 251s tick).
    These tests pin the two properties that make it safe to share one.
    """

    def _fresh(self):
        from src.runtime import market_data
        try:
            import src.exchange.bybit_connector  # noqa: F401
        except ImportError as exc:  # sandbox without ccxt — same guard as above
            pytest.skip(f"bybit_connector import failed in sandbox: {exc}")
        market_data.reset_exchange_client_cache()
        return market_data

    def test_same_credentials_reuse_one_client(self, monkeypatch):
        market_data = self._fresh()

        class _FakeBybit:
            def __init__(self, **kw):
                pass

        monkeypatch.setattr(
            "src.exchange.bybit_connector.BybitConnector", _FakeBybit
        )
        first = market_data._build_exchange_client({})
        second = market_data._build_exchange_client({})
        assert first is second, "connector memo must return the same instance"

    def test_swapping_the_connector_class_misses_the_cache(self, monkeypatch):
        """REGRESSION (CI, 2026-08-10): a warmed cache made monkeypatch inert.

        ``test_default_is_bybit`` passed in isolation and FAILED in the full
        suite, because an earlier test had already cached a real
        ``BybitConnector`` — so the patched class was never constructed and the
        caller silently received the pre-patch object. The cache key now
        includes the identity of the class that would actually be built, so a
        swap misses and rebuilds.
        """
        market_data = self._fresh()

        class _FakeA:
            def __init__(self, **kw):
                pass

        class _FakeB:
            def __init__(self, **kw):
                pass

        monkeypatch.setattr("src.exchange.bybit_connector.BybitConnector", _FakeA)
        first = market_data._build_exchange_client({})
        assert isinstance(first, _FakeA)

        monkeypatch.setattr("src.exchange.bybit_connector.BybitConnector", _FakeB)
        second = market_data._build_exchange_client({})
        assert isinstance(second, _FakeB), (
            "a swapped connector class must MISS the cache, not return a stale client"
        )

    def test_ib_is_memoized_like_every_other_venue(self, monkeypatch):
        """IB IS memoized — and the premise this test used to assert was FALSE.

        ⚠️ **This test previously asserted the opposite** (`test_ib_is_never_memoized`,
        "IB must be rebuilt every time, never cached") on the stated grounds that
        "IB holds a live socket on a specific clientId — sharing one instance is
        the BL-20260706-IBACCTUPDATES-COLLISION multi-client hazard."

        **`IBMarketData` holds no socket.** Its `__init__` sets `use_rth` +
        `market_data_type` and takes `self._client = get_ib_client(...)`, which is
        already a process-wide registry keyed on `(host, port, client_id)`
        (`src/units/accounts/ib_client.py`). Every `IBMarketData` for one endpoint
        therefore ALREADY shared a single `IBClient` before the memo existed, so
        memoizing the wrapper cannot change how many IB clientIds are live and
        BL-20260706-IBACCTUPDATES-COLLISION is untouched. Field beats comment.

        **What the false premise cost**, which is why the history is kept here
        rather than deleted: because `_client_cache_key` returned `None` for IB, a
        fresh wrapper was built per request, and `_candle_cache_key` keys on a
        per-OBJECT lifetime token — so every IB candle request was a guaranteed
        venue round trip **at any TTL**. Measured on the live trader: the one open
        IB 15m package was fetched **1.002 times per exit pass over n=433 passes**
        (zero cache hits) at ~10.8 s per fetch, while the three non-IB frames landed
        within 12% of what their TTL predicts. Full working:
        `docs/research/exit-eval-fetch-attribution-2026-08-21.md` (T.1).

        The safety property this test was really protecting is now asserted
        directly, by `test_ib_memo_adds_no_new_socket_sharing` in
        `tests/test_ib_candle_cache_memo.py`, which pins the `get_ib_client()`
        delegation rather than banning the memo.
        """
        market_data = self._fresh()
        built = []

        def _fake_ib(settings):
            built.append(1)
            return object()

        monkeypatch.setattr(market_data, "_build_ib_market_data", _fake_ib)
        first = market_data._build_exchange_client({"EXCHANGE": "interactive_brokers"})
        second = market_data._build_exchange_client({"EXCHANGE": "interactive_brokers"})
        assert len(built) == 1, (
            "IB must be memoized like every other venue — a fresh wrapper per "
            "request is what made every IB candle fetch a guaranteed cache miss"
        )
        assert first is second

    def test_ib_declines_the_memo_when_the_endpoint_is_unresolvable(self, monkeypatch):
        """Fail-safe: no resolvable endpoint => no memo, exactly as before.

        Refusing to memo is always correct; guessing an identity is what would
        make two different endpoints share one client.
        """
        market_data = self._fresh()
        monkeypatch.delenv("IB_HOST", raising=False)
        monkeypatch.delenv("IB_PORT", raising=False)
        monkeypatch.setattr(market_data, "_ib_account_field", lambda field: None)
        assert market_data._client_cache_key({"EXCHANGE": "interactive_brokers"}) is None
