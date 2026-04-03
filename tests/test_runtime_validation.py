import pytest

from src.runtime.validation import validate_startup


def make_settings(**overrides):
   base = {
       "BYBIT_API_KEY": "demo_key",
       "BYBIT_API_SECRET": "demo_secret",
       "TELEGRAM_BOT_TOKEN": "demo_telegram_token",
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


def test_validate_startup_happy_path():
   settings = make_settings()
   validate_startup(settings)


def test_missing_api_key_raises():
   settings = make_settings(BYBIT_API_KEY="")
   with pytest.raises(RuntimeError, match="Missing required settings"):
       validate_startup(settings)


def test_invalid_mode_raises():
   settings = make_settings(MODE="paper")
   with pytest.raises(RuntimeError, match="Invalid MODE"):
       validate_startup(settings)


def test_invalid_risk_raises():
   settings = make_settings(RISK_PER_TRADE="0.50")
   with pytest.raises(RuntimeError, match="RISK_PER_TRADE must be between"):
       validate_startup(settings)


def test_invalid_dry_run_raises():
   settings = make_settings(DRY_RUN="sometimes")
   with pytest.raises(RuntimeError, match="DRY_RUN must be one of"):
       validate_startup(settings)
