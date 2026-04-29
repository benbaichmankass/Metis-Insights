"""
Offline tests for the VWAP strategy runtime path.

All tests use fake candle data — no exchange calls, no secrets, no network.

Dependency note: requires pandas (listed in requirements.txt).
If pandas is absent the entire module is skipped via pytest.importorskip.
matplotlib is mocked so the pipeline import chain works without it installed.
"""
import sys
import types
from unittest import mock

# Provide a minimal matplotlib stub so pipeline.py can be imported without
# matplotlib installed (matplotlib is a transitive dep of signal_notifications).
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

import pytest

pd = pytest.importorskip("pandas")

# S-012 PR C5: VWAP helpers moved from strategies/vwap_signal_builder.py
# (deleted) into src/units/strategies/vwap.py.
from src.units.strategies.vwap import (
    MIN_CANDLES,
    ENTRY_STD_THRESHOLD,
    build_vwap_signal,
    compute_vwap,
)
from src.runtime.orders import safe_place_order
from src.runtime.pipeline import run_pipeline
from src.runtime.validation import validate_startup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candles(*close_prices, volume=1000.0):
    """Build a minimal OHLCV DataFrame from a list of close prices."""
    rows = []
    for i, close in enumerate(close_prices):
        rows.append({
            "timestamp": i,
            "open": close - 1,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": volume,
        })
    return pd.DataFrame(rows)


def _candles_below_vwap():
    """Candles where the last price is well below VWAP (triggers buy)."""
    # High prices dominate the window, then a sharp dip at the end.
    prices = [100, 102, 101, 103, 102, 60]
    return _candles(*prices)


def _candles_above_vwap():
    """Candles where the last price is well above VWAP (triggers sell)."""
    prices = [100, 98, 99, 97, 98, 140]
    return _candles(*prices)


def _candles_near_vwap():
    """Candles where the last price is within 1 std-dev of VWAP (no signal)."""
    prices = [100, 100, 100, 100, 100, 100]
    return _candles(*prices)


class DummyExchangeClient:
    def __init__(self):
        self.calls = []

    def place_order(self, **order):
        self.calls.append(order)
        return {"ok": True, "order": order}


class DummyTelegramClient:
    def __init__(self):
        self.messages = []

    def send_message(self, message: str):
        self.messages.append(message)


# ---------------------------------------------------------------------------
# Unit: compute_vwap
# ---------------------------------------------------------------------------

class TestComputeVwap:
    def test_basic_vwap_calculation(self):
        df = _candles(100, 102, 101)
        vwap = compute_vwap(df)
        # typical_price = (high + low + close) / 3
        # high = close + 2, low = close - 2 → typical = close
        # VWAP = mean of close prices (equal volume)
        expected = (100 + 102 + 101) / 3
        assert abs(vwap - expected) < 0.01

    def test_too_few_candles_raises(self):
        df = _candles(100)  # only 1 row
        with pytest.raises(ValueError, match="at least"):
            compute_vwap(df)

    def test_empty_dataframe_raises(self):
        df = pd.DataFrame()
        with pytest.raises(ValueError, match="non-empty"):
            compute_vwap(df)

    def test_zero_volume_raises(self):
        df = _candles(100, 102, volume=0)
        with pytest.raises(ValueError, match="volume"):
            compute_vwap(df)

    def test_missing_columns_raises(self):
        df = pd.DataFrame({"close": [100, 102], "volume": [1, 1]})
        with pytest.raises(ValueError, match="missing columns"):
            compute_vwap(df)

    def test_vwap_weighted_by_volume(self):
        """Higher-volume candles should pull VWAP toward their typical price."""
        df = pd.DataFrame([
            {"timestamp": 0, "open": 99, "high": 102, "low": 98, "close": 100, "volume": 1},
            {"timestamp": 1, "open": 199, "high": 202, "low": 198, "close": 200, "volume": 100},
        ])
        vwap = compute_vwap(df)
        assert vwap > 190, "VWAP should be pulled toward the high-volume candle"


# ---------------------------------------------------------------------------
# Unit: build_vwap_signal
# ---------------------------------------------------------------------------

class TestBuildVwapSignal:
    def test_buy_signal_when_price_below_vwap(self):
        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)
        assert signal["side"] == "buy"
        assert signal["qty"] == 1.0
        assert signal["symbol"] == "BTCUSDT"
        assert signal["meta"]["strategy_name"] == "vwap"
        assert signal["meta"]["current_price"] < signal["meta"]["vwap"]

    def test_sell_signal_when_price_above_vwap(self):
        df = _candles_above_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)
        assert signal["side"] == "sell"
        assert signal["qty"] == 1.0
        assert signal["meta"]["current_price"] > signal["meta"]["vwap"]

    def test_no_signal_when_price_near_vwap(self):
        df = _candles_near_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)
        assert signal["side"] == "none"
        assert signal["qty"] == 0.0

    def test_signal_includes_vwap_meta(self):
        df = _candles(100, 102, 101, 103, 100)
        signal = build_vwap_signal(df, symbol="BTCUSDT", qty=2.0)
        assert "vwap" in signal["meta"]
        assert "current_price" in signal["meta"]
        assert "std_dev" in signal["meta"]
        assert "deviation_std" in signal["meta"]
        assert "reason" in signal["meta"]

    def test_qty_is_zero_for_no_signal(self):
        df = _candles_near_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT", qty=5.0)
        assert signal["qty"] == 0.0

    def test_insufficient_candles_raises(self):
        df = _candles(100)
        with pytest.raises(ValueError, match="at least"):
            build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)


# ---------------------------------------------------------------------------
# Integration: STRATEGY=vwap routes to VWAP logic via run_pipeline
# ---------------------------------------------------------------------------

class TestVwapPipelineRouting:
    def _vwap_no_signal_builder(self, settings):
        return {
            "symbol": settings.get("SYMBOL", "BTCUSDT"),
            "side": "none",
            "qty": 0,
            "meta": {"strategy_name": "vwap"},
        }

    def _vwap_buy_signal_builder(self, settings):
        return {
            "symbol": settings.get("SYMBOL", "BTCUSDT"),
            "side": "buy",
            "qty": 1.0,
            "meta": {"strategy_name": "vwap", "vwap": 100.0, "current_price": 90.0},
        }

    def test_vwap_strategy_routes_correctly(self, monkeypatch):
        """STRATEGY=vwap should invoke the vwap signal builder."""
        called_with = {}

        def fake_vwap_builder(settings):
            called_with["settings"] = settings
            return {"symbol": "BTCUSDT", "side": "none", "qty": 0}

        monkeypatch.setattr("src.runtime.pipeline.vwap_signal_builder", fake_vwap_builder)
        monkeypatch.setenv("STRATEGY", "vwap")

        settings = {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "10"}
        run_pipeline(
            settings,
            exchange_client=DummyExchangeClient(),
            telegram_client=DummyTelegramClient(),
        )

        assert called_with, "vwap_signal_builder was not called"

    def test_vwap_dry_run_does_not_call_exchange_place_order(self):
        """DRY_RUN=true must never touch the exchange order path."""
        exchange = DummyExchangeClient()
        settings = {
            "SYMBOL": "BTCUSDT",
            "DRY_RUN": "true",
            "MAX_QTY": "10",
        }
        run_pipeline(
            settings,
            exchange_client=exchange,
            telegram_client=DummyTelegramClient(),
            signal_builder=self._vwap_buy_signal_builder,
        )
        assert exchange.calls == [], "Exchange order method must not be called in DRY_RUN mode"

    def test_vwap_dry_run_returns_dry_run_status(self):
        settings = {
            "SYMBOL": "BTCUSDT",
            "DRY_RUN": "true",
            "MAX_QTY": "10",
        }
        result = run_pipeline(
            settings,
            exchange_client=DummyExchangeClient(),
            telegram_client=DummyTelegramClient(),
            signal_builder=self._vwap_buy_signal_builder,
        )
        assert result["order_result"]["status"] == "dry_run"

    def test_vwap_no_signal_returns_skipped(self):
        settings = {
            "SYMBOL": "BTCUSDT",
            "DRY_RUN": "true",
            "MAX_QTY": "10",
        }
        result = run_pipeline(
            settings,
            exchange_client=DummyExchangeClient(),
            telegram_client=DummyTelegramClient(),
            signal_builder=self._vwap_no_signal_builder,
        )
        assert result["order_result"]["status"] == "skipped"
        assert result["order_result"]["reason"] == "no_signal"


# ---------------------------------------------------------------------------
# Safety: live mode without explicit gate fails closed
# ---------------------------------------------------------------------------

class TestLiveSafetyGate:
    def test_live_without_allow_live_trading_blocked_by_safe_place_order(self):
        """DRY_RUN=false + ALLOW_LIVE_TRADING absent → order is rejected at orders layer."""
        client = DummyExchangeClient()
        settings = {"DRY_RUN": "false", "MAX_QTY": "10"}
        result = safe_place_order(
            {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
            settings,
            client,
        )
        assert result["status"] == "failed_validation"
        assert "ALLOW_LIVE_TRADING" in result["reason"]
        assert client.calls == []

    def test_live_with_explicit_gate_is_submitted(self):
        """DRY_RUN=false + ALLOW_LIVE_TRADING=true → order reaches exchange."""
        client = DummyExchangeClient()
        settings = {"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "true", "MAX_QTY": "10"}
        result = safe_place_order(
            {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
            settings,
            client,
        )
        assert result["status"] == "submitted"
        assert len(client.calls) == 1

    def test_dry_run_true_blocks_submission_regardless_of_allow_live(self):
        """DRY_RUN=true blocks real submission even if ALLOW_LIVE_TRADING=true."""
        client = DummyExchangeClient()
        settings = {"DRY_RUN": "true", "ALLOW_LIVE_TRADING": "true", "MAX_QTY": "10"}
        result = safe_place_order(
            {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
            settings,
            client,
        )
        assert result["status"] == "dry_run"
        assert client.calls == []

    def test_mode_live_without_allow_live_fails_validate_startup(self, monkeypatch):
        """validate_startup must reject MODE=LIVE without ALLOW_LIVE_TRADING=true."""
        env = {
            "EXCHANGE": "bybit",
            "BYBIT_API_KEY": "fake_key",
            "BYBIT_API_SECRET": "fake_secret",
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "123",
            "MODE": "LIVE",
            "SYMBOL": "BTCUSDT",
            "TIMEFRAME": "5m",
            "RISK_PER_TRADE": "0.01",
            "MAX_QTY": "1",
            "DRY_RUN": "true",
            "ALLOW_LIVE_TRADING": "false",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)

        with pytest.raises(EnvironmentError, match="ALLOW_LIVE_TRADING"):
            validate_startup()

    def test_mode_paper_is_rejected_by_validate_startup(self, monkeypatch):
        """MODE=PAPER must be rejected outright — paper trading is not supported."""
        env = {
            "EXCHANGE": "bybit",
            "BYBIT_API_KEY": "fake_key",
            "BYBIT_API_SECRET": "fake_secret",
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "123",
            "MODE": "PAPER",
            "SYMBOL": "BTCUSDT",
            "TIMEFRAME": "5m",
            "RISK_PER_TRADE": "0.01",
            "MAX_QTY": "1",
            "DRY_RUN": "true",
            "ALLOW_LIVE_TRADING": "false",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)

        with pytest.raises(EnvironmentError, match="MODE"):
            validate_startup()

    def test_mode_paper_lowercase_is_rejected(self, monkeypatch):
        """MODE=paper (lowercase) must also be rejected after .upper() normalisation."""
        env = {
            "EXCHANGE": "bybit",
            "BYBIT_API_KEY": "fake_key",
            "BYBIT_API_SECRET": "fake_secret",
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "123",
            "MODE": "paper",
            "SYMBOL": "BTCUSDT",
            "TIMEFRAME": "5m",
            "RISK_PER_TRADE": "0.01",
            "MAX_QTY": "1",
            "DRY_RUN": "true",
            "ALLOW_LIVE_TRADING": "false",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)

        with pytest.raises(EnvironmentError, match="MODE"):
            validate_startup()

    def test_mode_live_lowercase_requires_allow_live(self, monkeypatch):
        """MODE=live (lowercase) must still trigger ALLOW_LIVE_TRADING gate."""
        env = {
            "EXCHANGE": "bybit",
            "BYBIT_API_KEY": "fake_key",
            "BYBIT_API_SECRET": "fake_secret",
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "123",
            "MODE": "live",
            "SYMBOL": "BTCUSDT",
            "TIMEFRAME": "5m",
            "RISK_PER_TRADE": "0.01",
            "MAX_QTY": "1",
            "DRY_RUN": "true",
            "ALLOW_LIVE_TRADING": "false",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)

        with pytest.raises(EnvironmentError, match="ALLOW_LIVE_TRADING"):
            validate_startup()


# ---------------------------------------------------------------------------
# Edge cases: missing / malformed candle data
# ---------------------------------------------------------------------------

class TestVwapEdgeCases:
    def test_single_candle_insufficient(self):
        df = _candles(100)
        with pytest.raises(ValueError, match="at least"):
            build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)

    def test_exactly_min_candles_is_accepted(self):
        df = _candles(*([100] * MIN_CANDLES))
        # All-same prices → std_dev = 0 → deviation = 0 → no signal, but no error
        signal = build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)
        assert signal["side"] == "none"

    def test_vwap_meta_never_contains_api_key(self):
        """Ensure VWAP signal meta cannot leak credentials."""
        df = _candles(100, 102, 101)
        signal = build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)
        meta_str = str(signal)
        for suspicious in ("api_key", "api_secret", "token", "password", "secret"):
            assert suspicious not in meta_str.lower(), (
                f"Signal output contains suspicious key: {suspicious}"
            )


# ---------------------------------------------------------------------------
# Invalid candle data — must return no-trade, never raise
# ---------------------------------------------------------------------------

class TestVwapInvalidDataNoTrade:
    """Bad market data must yield a no-trade signal; the tick must not crash."""

    def test_zero_volume_returns_no_trade(self):
        df = _candles(100, 102, 101, volume=0)
        signal = build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)
        assert signal["side"] == "none"
        assert signal["qty"] == 0.0
        assert signal["meta"]["strategy_name"] == "vwap"

    def test_zero_volume_reason_text(self):
        df = _candles(100, 102, 101, volume=0)
        signal = build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)
        reason = signal["meta"]["reason"]
        assert "zero" in reason.lower() or "negative" in reason.lower()

    def test_zero_volume_does_not_raise(self):
        df = _candles(100, 102, 101, volume=0)
        build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)  # must not raise

    def test_missing_volume_column_returns_no_trade(self):
        df = pd.DataFrame([
            {"timestamp": 0, "open": 99, "high": 102, "low": 98, "close": 100},
            {"timestamp": 1, "open": 100, "high": 103, "low": 99, "close": 101},
        ])
        signal = build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)
        assert signal["side"] == "none"
        assert signal["qty"] == 0.0

    def test_missing_volume_column_does_not_raise(self):
        df = pd.DataFrame([
            {"timestamp": 0, "open": 99, "high": 102, "low": 98, "close": 100},
            {"timestamp": 1, "open": 100, "high": 103, "low": 99, "close": 101},
        ])
        build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)  # must not raise

    def test_empty_dataframe_returns_no_trade(self):
        df = pd.DataFrame()
        signal = build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)
        assert signal["side"] == "none"
        assert signal["qty"] == 0.0

    def test_empty_dataframe_does_not_raise(self):
        df = pd.DataFrame()
        build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)  # must not raise

    def test_normal_candles_still_produce_signal(self):
        """Valid candle data must continue to generate actionable signals."""
        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)
        assert signal["side"] == "buy"
        assert signal["qty"] == 1.0
        assert signal["meta"]["strategy_name"] == "vwap"
        assert signal["meta"]["current_price"] < signal["meta"]["vwap"]

    def test_pipeline_zero_volume_skips_order_placement(self):
        """Zero-volume candles routed through pipeline must not reach order placement."""
        exchange = DummyExchangeClient()

        def zero_volume_builder(settings):
            df = _candles(100, 102, 101, volume=0)
            return build_vwap_signal(df, symbol=settings.get("SYMBOL", "BTCUSDT"), qty=1.0)

        settings = {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "1"}
        result = run_pipeline(
            settings,
            exchange_client=exchange,
            telegram_client=DummyTelegramClient(),
            signal_builder=zero_volume_builder,
        )
        assert result["order_result"]["status"] == "skipped"
        assert exchange.calls == []
