import pandas as pd
import pytest

from src.core.automated_trading_loop import KillZoneScalperBot
from src.runtime.pipeline import killzone_signal_builder, run_pipeline


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


def no_signal_builder(settings):
    return {
        "symbol": settings["SYMBOL"],
        "side": "none",
        "qty": 0,
    }


def forced_long_builder(settings):
    return {
        "symbol": settings["SYMBOL"],
        "side": "buy",
        "qty": 1,
    }


def test_pipeline_skips_when_no_signal():
    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "true",
        "MAX_QTY": "10",
    }
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    result = run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=no_signal_builder,
    )

    assert result["order_result"]["status"] == "skipped"
    assert result["order_result"]["reason"] == "no_signal"
    assert exchange.calls == []
    assert len(telegram.messages) >= 0


def test_pipeline_places_order_for_forced_signal():
    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "true",
        "MAX_QTY": "10",
    }
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    result = run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=forced_long_builder,
    )

    assert result["order_result"]["status"] == "simulated"
    assert len(exchange.calls) == 0


class FailingTelegramClient:
    def send_message(self, message: str):
        raise RuntimeError("telegram send failed")


def test_pipeline_does_not_crash_when_telegram_notification_fails():
    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "true",
        "MAX_QTY": "10",
    }
    exchange = DummyExchangeClient()
    telegram = FailingTelegramClient()

    result = run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=forced_long_builder,
    )

    assert result["order_result"]["status"] == "simulated"


def test_pipeline_returns_skipped_status_for_no_signal():
    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "true",
        "MAX_QTY": "10",
    }
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    result = run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=no_signal_builder,
    )

    assert result["order_result"]["status"] == "skipped"
    assert result["order_result"]["reason"] == "no_signal"


def test_pipeline_telegram_message_includes_skipped_status():
    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "true",
        "MAX_QTY": "10",
    }
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=no_signal_builder,
    )

    assert len(telegram.messages) == 1
    assert "skipped" in telegram.messages[0].lower()
    assert "BTCUSDT" in telegram.messages[0]


def test_pipeline_telegram_message_includes_simulated_status():
    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "true",
        "MAX_QTY": "10",
    }
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=forced_long_builder,
    )

    assert len(telegram.messages) == 1
    assert "simulated" in telegram.messages[0].lower()
    assert "BTCUSDT" in telegram.messages[0]


def test_pipeline_telegram_message_includes_failed_validation_reason():
    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "false",
        "MAX_QTY": "10",
    }
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=forced_long_builder,
    )

    assert len(telegram.messages) == 1
    assert "failed_validation" in telegram.messages[0].lower()
    assert "ALLOW_LIVE_TRADING" in telegram.messages[0]


class StubStrategyExchange:
    def __init__(self):
        self.balance_calls = 0

    def get_balance(self):
        self.balance_calls += 1
        return {"total": {"USDT": 1000}}

    def get_ohlcv(self, symbol="BTC/USDT", timeframe="15m", limit=100):
        rows = [
            [1, 100, 110, 95, 105, 1],
            [2, 105, 115, 100, 111, 1],
            [3, 111, 120, 108, 118, 1],
        ]
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        return df

    def get_price(self, symbol="BTC/USDT"):
        return 112

    def place_market_order(self, symbol, side, amount, params=None):
        return {"symbol": symbol, "side": side, "amount": amount, "params": params or {}}


def test_killzone_bot_accepts_injected_exchange():
    exchange = StubStrategyExchange()
    bot = KillZoneScalperBot(exchange=exchange, symbol="BTC/USDT")
    assert bot.exchange is exchange
    assert bot.symbol == "BTC/USDT"


def test_killzone_bot_requires_exchange():
    with pytest.raises(ValueError, match="exchange connector"):
        KillZoneScalperBot(exchange=None)


def test_killzone_signal_builder_selects_binance(monkeypatch):
    captured = {}

    class FakeBinanceConnector:
        def __init__(self, api_key=None, api_secret=None, testnet=True):
            captured["connector"] = "binance"
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret
            captured["testnet"] = testnet

    def fake_analyze_market(self):
        captured["bot_symbol"] = self.symbol
        captured["bot_exchange_class"] = self.exchange.__class__.__name__
        return "long", 50000.0, {"type": "bullish", "top": 50100, "bottom": 49900, "idx": 199}

    monkeypatch.setattr("src.exchange.binance_connector.BinanceConnector", FakeBinanceConnector)
    monkeypatch.setattr(KillZoneScalperBot, "analyze_market", fake_analyze_market)

    settings = {
        "EXCHANGE": "binance",
        "MODE": "testnet",
        "BINANCE_API_KEY": "binance_key",
        "BINANCE_API_SECRET": "binance_secret",
        "SYMBOL": "BTCUSDT",
        "MAX_QTY": "2",
    }

    signal = killzone_signal_builder(settings)

    assert captured["connector"] == "binance"
    assert captured["api_key"] == "binance_key"
    assert captured["api_secret"] == "binance_secret"
    assert captured["testnet"] is True
    assert captured["bot_symbol"] == "BTCUSDT"
    assert signal["side"] == "buy"
    assert signal["qty"] == 2.0
    assert signal["meta"]["exchange"] == "binance"


def test_killzone_signal_builder_selects_bybit(monkeypatch):
    captured = {}

    class FakeBybitConnector:
        def __init__(self, api_key=None, api_secret=None, testnet=True):
            captured["connector"] = "bybit"
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret
            captured["testnet"] = testnet

    def fake_analyze_market(self):
        captured["bot_symbol"] = self.symbol
        captured["bot_exchange_class"] = self.exchange.__class__.__name__
        return "short", 50000.0, {"type": "bearish", "top": 50100, "bottom": 49900, "idx": 199}

    monkeypatch.setattr("src.exchange.bybit_connector.BybitConnector", FakeBybitConnector)
    monkeypatch.setattr(KillZoneScalperBot, "analyze_market", fake_analyze_market)

    settings = {
        "EXCHANGE": "bybit",
        "MODE": "live",
        "BYBIT_API_KEY": "bybit_key",
        "BYBIT_API_SECRET": "bybit_secret",
        "SYMBOL": "BTC/USDT:USDT",
        "MAX_QTY": "3",
    }

    signal = killzone_signal_builder(settings)

    assert captured["connector"] == "bybit"
    assert captured["api_key"] == "bybit_key"
    assert captured["api_secret"] == "bybit_secret"
    assert captured["testnet"] is False
    assert captured["bot_symbol"] == "BTC/USDT:USDT"
    assert signal["side"] == "sell"
    assert signal["qty"] == 3.0
    assert signal["meta"]["exchange"] == "bybit"


def test_killzone_signal_builder_raises_for_unsupported_exchange():
    settings = {
        "EXCHANGE": "kraken",
        "MODE": "testnet",
        "SYMBOL": "BTCUSDT",
    }

    with pytest.raises(ValueError, match="Unsupported EXCHANGE value"):
        killzone_signal_builder(settings)
