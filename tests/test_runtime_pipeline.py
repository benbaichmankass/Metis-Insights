import pandas as pd
import pytest

from src.core.automated_trading_loop import KillZoneScalperBot
from src.runtime.pipeline import (
    STRATEGIES,
    killzone_signal_builder,
    multiplexed_signal_builder,
    run_pipeline,
)
import src.runtime.pipeline as _pipeline_mod


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


def test_pipeline_skips_when_halted(tmp_path, monkeypatch):
    flag = tmp_path / "trader_halt.flag"
    flag.write_text("halted")
    monkeypatch.setattr("src.runtime.pipeline.HALT_FLAG_PATH", str(flag))

    settings = {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "10"}
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    result = run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=forced_long_builder,
    )

    assert result["order_result"]["status"] == "halted"
    assert result["order_result"]["reason"] == "halt_flag_active"
    assert exchange.calls == []


def test_pipeline_runs_normally_when_not_halted(tmp_path, monkeypatch):
    flag = tmp_path / "trader_halt.flag"
    # flag intentionally NOT created
    monkeypatch.setattr("src.runtime.pipeline.HALT_FLAG_PATH", str(flag))

    settings = {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "10"}
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    result = run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=forced_long_builder,
    )

    assert result["order_result"]["status"] == "simulated"
    assert exchange.calls == []


# ---------------------------------------------------------------------------
# Strategy multiplexer tests
# ---------------------------------------------------------------------------

def _make_signal(side="buy", qty=1.0, strategy="test"):
    return {"symbol": "BTCUSDT", "side": side, "qty": qty,
            "meta": {"strategy_name": strategy}}


def _flat_signal(symbol="BTCUSDT"):
    return {"symbol": symbol, "side": "none", "qty": 0}


def test_multi_strategy_pipeline_strategies_list_contains_expected_strategies():
    assert "breakout_confirmation" in STRATEGIES
    assert "vwap" in STRATEGIES


def test_multi_strategy_pipeline_first_wins(monkeypatch):
    """First strategy returns actionable; second builder must not be called."""
    second_called = []

    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: _make_signal(side="buy", qty=1.0, strategy="breakout_confirmation"),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "vwap",
        lambda s: second_called.append(True) or _make_signal(side="sell", qty=1.0, strategy="vwap"),
    )

    settings = {"SYMBOL": "BTCUSDT", "MAX_QTY": "1"}
    signal = multiplexed_signal_builder(settings)

    assert signal["side"] == "buy"
    assert signal["meta"]["strategy_name"] == "breakout_confirmation"
    assert second_called == [], "second strategy must not be invoked when first fires"


def test_multi_strategy_pipeline_fallback_to_second(monkeypatch):
    """First strategy flat; second produces the actionable signal."""
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: _flat_signal(),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "vwap",
        lambda s: _make_signal(side="sell", qty=2.0, strategy="vwap"),
    )

    settings = {"SYMBOL": "BTCUSDT", "MAX_QTY": "2"}
    signal = multiplexed_signal_builder(settings)

    assert signal["side"] == "sell"
    assert signal["qty"] == 2.0
    assert signal["meta"]["strategy_name"] == "vwap"


def test_multi_strategy_pipeline_no_signal_when_all_flat(monkeypatch):
    """All strategies flat → side=none returned."""
    monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, "breakout_confirmation", lambda s: _flat_signal())
    monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, "vwap", lambda s: _flat_signal())

    settings = {"SYMBOL": "BTCUSDT"}
    signal = multiplexed_signal_builder(settings)

    assert signal["side"] == "none"
    assert float(signal["qty"]) == 0


def test_multi_strategy_pipeline_skips_erroring_strategy(monkeypatch):
    """Strategy that raises is skipped; next strategy wins."""
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: (_ for _ in ()).throw(RuntimeError("exchange down")),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "vwap",
        lambda s: _make_signal(side="buy", qty=1.0, strategy="vwap"),
    )

    settings = {"SYMBOL": "BTCUSDT", "MAX_QTY": "1"}
    signal = multiplexed_signal_builder(settings)

    assert signal["side"] == "buy"
    assert signal["meta"]["strategy_name"] == "vwap"


def test_multi_strategy_pipeline_per_strategy_sizing_no_compounding(monkeypatch):
    """Each strategy uses its own qty; quantities must not be summed."""
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: _make_signal(side="buy", qty=3.0, strategy="breakout_confirmation"),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "vwap",
        lambda s: _make_signal(side="buy", qty=5.0, strategy="vwap"),
    )

    settings = {"SYMBOL": "BTCUSDT", "MAX_QTY": "3"}
    signal = multiplexed_signal_builder(settings)

    # Only the first-winning strategy qty is returned, no summing
    assert signal["qty"] == 3.0
    assert signal["meta"]["strategy_name"] == "breakout_confirmation"


def test_multi_strategy_pipeline_via_env_var(monkeypatch):
    """STRATEGY=multiplexed env var activates the multiplexer through run_pipeline."""
    monkeypatch.setenv("STRATEGY", "multiplexed")
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: _make_signal(side="buy", qty=1.0, strategy="breakout_confirmation"),
    )

    settings = {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "1"}
    telegram = DummyTelegramClient()

    result = run_pipeline(settings, telegram_client=telegram)

    assert result["signal"]["side"] == "buy"
    assert result["signal"]["meta"]["strategy_name"] == "breakout_confirmation"
    assert result["order_result"]["status"] == "simulated"


def test_multi_strategy_pipeline_respects_max_qty_via_env_var(monkeypatch):
    """
    M6 risk-cap guarantee: when the multiplexer is active and a strategy
    returns a qty above MAX_QTY, safe_place_order must still reject the
    order. Proves the combined execution path does not bypass risk caps.
    """
    monkeypatch.setenv("STRATEGY", "multiplexed")
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: _flat_signal(),
    )
    # vwap claims qty=10, exceeding MAX_QTY=1 — caps must still bite.
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "vwap",
        lambda s: _make_signal(side="buy", qty=10.0, strategy="vwap"),
    )

    settings = {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "1"}
    telegram = DummyTelegramClient()

    result = run_pipeline(settings, telegram_client=telegram)

    assert result["signal"]["meta"]["strategy_name"] == "vwap"
    assert result["order_result"]["status"] == "failed_validation"
    assert "MAX_QTY" in result["order_result"]["reason"]


def test_multi_strategy_pipeline_respects_max_position_usd(monkeypatch):
    """
    M6 risk-cap guarantee: MAX_POSITION_USD must abort multiplexed orders
    just like single-strategy orders. Proves notional caps apply across
    breakout_confirmation + vwap combined execution path.
    """
    monkeypatch.setenv("STRATEGY", "multiplexed")
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: {
            "symbol": "BTCUSDT",
            "side": "buy",
            "qty": 1.0,
            "meta": {"strategy_name": "breakout_confirmation", "price": 100000.0},
        },
    )

    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "true",
        "MAX_QTY": "10",
        "MAX_POSITION_USD": "5000",  # 1 * 100_000 = 100_000 USD >> 5_000
    }
    telegram = DummyTelegramClient()

    with pytest.raises(ValueError, match="MAX_POSITION_USD"):
        run_pipeline(settings, telegram_client=telegram)
