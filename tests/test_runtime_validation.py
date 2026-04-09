import pytest
from src.runtime.validation import validate_startup


def make_bybit_settings(**overrides):
    base = {
        "EXCHANGE": "bybit",
        "BYBIT_API_KEY": "demo_bybit_key",
        "BYBIT_API_SECRET": "demo_bybit_secret",
        "TELEGRAM_BOT_TOKEN": "demo_token",
        "TELEGRAM_CHAT_ID": "123456789",
        "MODE": "testnet",
        "SYMBOL": "BTCUSDT",
        "TIMEFRAME": "15",
        "RISK_PER_TRADE": "0.01",
        "MAX_QTY": "10",
        "DRY_RUN": "true",
    }
    base.update(overrides)
    return base


def make_binance_settings(**overrides):
    base = {
        "EXCHANGE": "binance",
        "BINANCE_API_KEY": "demo_binance_key",
        "BINANCE_API_SECRET": "demo_binance_secret",
        "TELEGRAM_BOT_TOKEN": "demo_token",
        "TELEGRAM_CHAT_ID": "123456789",
        "MODE": "testnet",
        "SYMBOL": "BTCUSDT",
        "TIMEFRAME": "15",
        "RISK_PER_TRADE": "0.01",
        "MAX_QTY": "10",
        "DRY_RUN": "true",
    }
    base.update(overrides)
    return base


def test_bybit_happy_path():
    validate_startup(make_bybit_settings())


def test_binance_happy_path():
    validate_startup(make_binance_settings())


def test_binance_does_not_need_bybit_keys():
    s = make_binance_settings()
    s.pop("BYBIT_API_KEY", None)
    s.pop("BYBIT_API_SECRET", None)
    validate_startup(s)


def test_bybit_does_not_need_binance_keys():
    s = make_bybit_settings()
    s.pop("BINANCE_API_KEY", None)
    s.pop("BINANCE_API_SECRET", None)
    validate_startup(s)


def test_binance_missing_api_key_raises():
    s = make_binance_settings(BINANCE_API_KEY="")
    with pytest.raises(RuntimeError, match="Missing required settings"):
        validate_startup(s)


def test_bybit_missing_api_key_raises():
    s = make_bybit_settings(BYBIT_API_KEY="")
    with pytest.raises(RuntimeError, match="Missing required settings"):
        validate_startup(s)


def test_invalid_exchange_raises():
    s = make_bybit_settings(EXCHANGE="kraken")
    with pytest.raises(RuntimeError, match="Invalid EXCHANGE"):
        validate_startup(s)


def test_missing_exchange_raises():
    s = make_bybit_settings(EXCHANGE="")
    with pytest.raises(RuntimeError, match="Invalid EXCHANGE"):
        validate_startup(s)


def test_invalid_mode_raises():
    s = make_bybit_settings(MODE="paper")
    with pytest.raises(RuntimeError, match="Invalid MODE"):
        validate_startup(s)


def test_invalid_risk_raises():
    s = make_bybit_settings(RISK_PER_TRADE="0.50")
    with pytest.raises(RuntimeError, match="RISK_PER_TRADE must be between"):
        validate_startup(s)


def test_invalid_dry_run_raises():
    s = make_bybit_settings(DRY_RUN="sometimes")
    with pytest.raises(RuntimeError, match="DRY_RUN must be one of"):
        validate_startup(s)


def test_dry_run_false_without_allow_live_raises():
    s = make_bybit_settings(DRY_RUN="false", ALLOW_LIVE_TRADING="false")
    with pytest.raises(RuntimeError, match="ALLOW_LIVE_TRADING"):
        validate_startup(s)


def test_dry_run_false_with_allow_live_passes():
    s = make_bybit_settings(DRY_RUN="false", ALLOW_LIVE_TRADING="true")
    validate_startup(s)


def test_telegram_always_required_for_binance():
    s = make_binance_settings(TELEGRAM_BOT_TOKEN="")
    with pytest.raises(RuntimeError, match="Missing required settings"):
        validate_startup(s)


def test_telegram_always_required_for_bybit():
    s = make_bybit_settings(TELEGRAM_BOT_TOKEN="")
    with pytest.raises(RuntimeError, match="Missing required settings"):
        validate_startup(s)
