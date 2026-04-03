import pytest

from src.runtime.orders import safe_place_order


class DummyClient:
    def __init__(self):
        self.calls = []

    def place_order(self, **order):
        self.calls.append(order)
        return {"ok": True, "order_id": "abc123"}


def make_settings(**overrides):
    base = {
        "DRY_RUN": "true",
        "MAX_QTY": "10",
    }
    base.update(overrides)
    return base


def test_dry_run_does_not_call_exchange():
    client = DummyClient()
    settings = make_settings(DRY_RUN="true")
    result = safe_place_order(
        {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
        settings,
        client,
    )
    assert result["status"] == "simulated"
    assert client.calls == []


def test_live_submission_calls_exchange():
    client = DummyClient()
    settings = make_settings(DRY_RUN="false")
    result = safe_place_order(
        {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
        settings,
        client,
    )
    assert result["status"] == "submitted"
    assert len(client.calls) == 1


def test_order_rejected_when_qty_exceeds_max():
    client = DummyClient()
    settings = make_settings(DRY_RUN="false", MAX_QTY="1")
    with pytest.raises(RuntimeError, match="exceeds MAX_QTY"):
        safe_place_order(
            {"symbol": "BTCUSDT", "side": "buy", "qty": 2},
            settings,
            client,
        )


def test_order_rejected_for_bad_side():
    client = DummyClient()
    settings = make_settings()
    with pytest.raises(RuntimeError, match="side must be"):
        safe_place_order(
            {"symbol": "BTCUSDT", "side": "hold", "qty": 1},
            settings,
            client,
        )
