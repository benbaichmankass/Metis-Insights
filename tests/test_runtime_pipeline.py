from src.runtime.pipeline import run_pipeline


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
    assert len(telegram.messages) >= 0  # may or may not send, just ensure no crash


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
    assert len(exchange.calls) == 0  # DRY_RUN=True, safe_place_order will not call exchange
