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
