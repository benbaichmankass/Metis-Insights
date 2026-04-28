from src.runtime.validation import validate_startup
from src.runtime.orders import safe_place_order


class DummyClient:
    def place_order(self, **order):
        return {"ok": True, "order": order}


def test_runtime_smoke_path():
    settings = {
        "EXCHANGE": "bybit",
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

    validate_startup(settings)

    result = safe_place_order(
        {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
        settings,
        DummyClient(),
    )

    assert result["status"] == "dry_run"
