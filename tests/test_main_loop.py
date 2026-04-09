import os
import pytest
import src.main as main_module


def safe_binance_env(monkeypatch):
    monkeypatch.setenv("EXCHANGE", "binance")
    monkeypatch.setenv("BINANCE_API_KEY", "demo_key")
    monkeypatch.setenv("BINANCE_API_SECRET", "demo_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "demo_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456789")
    monkeypatch.setenv("MODE", "testnet")
    monkeypatch.setenv("SYMBOL", "BTCUSDT")
    monkeypatch.setenv("TIMEFRAME", "15")
    monkeypatch.setenv("RISK_PER_TRADE", "0.01")
    monkeypatch.setenv("MAX_QTY", "10")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "false")
    monkeypatch.setenv("LOOP", "false")


def safe_bybit_env(monkeypatch):
    monkeypatch.setenv("EXCHANGE", "bybit")
    monkeypatch.setenv("BYBIT_API_KEY", "demo_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "demo_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "demo_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456789")
    monkeypatch.setenv("MODE", "testnet")
    monkeypatch.setenv("SYMBOL", "BTCUSDT")
    monkeypatch.setenv("TIMEFRAME", "15")
    monkeypatch.setenv("RISK_PER_TRADE", "0.01")
    monkeypatch.setenv("MAX_QTY", "10")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "false")
    monkeypatch.setenv("LOOP", "false")


def test_main_single_tick_binance(monkeypatch):
    safe_binance_env(monkeypatch)
    monkeypatch.setattr(main_module, 'load_dotenv', lambda: None)

    called = {}

    def fake_run_pipeline(settings, exchange_client, telegram_client):
        called["ran"] = True
        return {"signal": {}, "order_result": {"status": "simulated"}}

    monkeypatch.setattr(main_module, 'run_pipeline', fake_run_pipeline)

    class FakeBinanceConnector:
        def __init__(self, **kw): pass
        def get_price(self, *a, **kw): return 50000

    monkeypatch.setattr('src.exchange.binance_connector.BinanceConnector', FakeBinanceConnector)

    main_module.main()
    assert called.get("ran") is True


def test_main_single_tick_bybit(monkeypatch):
    safe_bybit_env(monkeypatch)
    monkeypatch.setattr(main_module, 'load_dotenv', lambda: None)

    called = {}

    def fake_run_pipeline(settings, exchange_client, telegram_client):
        called["ran"] = True
        return {"signal": {}, "order_result": {"status": "simulated"}}

    monkeypatch.setattr(main_module, 'run_pipeline', fake_run_pipeline)

    class FakeBybitConnector:
        def __init__(self, **kw): pass
        def get_price(self, *a, **kw): return 50000

    monkeypatch.setattr('src.exchange.bybit_connector.BybitConnector', FakeBybitConnector)

    main_module.main()
    assert called.get("ran") is True


def test_main_raises_on_invalid_exchange(monkeypatch):
    safe_binance_env(monkeypatch)
    monkeypatch.setenv("EXCHANGE", "kraken")
    monkeypatch.setattr(main_module, 'load_dotenv', lambda: None)
    with pytest.raises(RuntimeError, match='Invalid EXCHANGE'):
        main_module.main()
