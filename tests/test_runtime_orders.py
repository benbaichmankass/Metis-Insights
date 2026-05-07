
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
    assert result["status"] == "dry_run"
    assert client.calls == []


def test_live_submission_calls_exchange():
    client = DummyClient()
    settings = make_settings(DRY_RUN="false", ALLOW_LIVE_TRADING="true")
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
    result = safe_place_order(
        {"symbol": "BTCUSDT", "side": "buy", "qty": 2},
        settings,
        client,
    )
    assert result["status"] == "failed_validation"
    assert "exceeds MAX_QTY" in result["reason"]


def test_order_rejected_for_bad_side():
    client = DummyClient()
    settings = make_settings()
    result = safe_place_order(
        {"symbol": "BTCUSDT", "side": "hold", "qty": 1},
        settings,
        client,
    )
    assert result["status"] == "failed_validation"
    assert "side must be" in result["reason"]
def test_live_is_the_default_when_dry_run_is_false():
    """BUG-031: live is the default. DRY_RUN=false with ALLOW_LIVE_TRADING
    unset must SUBMIT, not refuse. The opt-in safety rails are the risk
    manager + halt flag (per CLAUDE.md), not an extra env-var gate.
    """
    client = DummyClient()
    settings = make_settings(DRY_RUN="false")
    result = safe_place_order(
        {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
        settings,
        client,
    )
    assert result["status"] == "submitted"
    assert len(client.calls) == 1


def test_explicit_allow_live_false_still_blocks():
    """Operator can still explicitly disable live by setting
    ALLOW_LIVE_TRADING=false (e.g. /set_all_dry temporarily)."""
    client = DummyClient()
    settings = make_settings(DRY_RUN="false", ALLOW_LIVE_TRADING="false")
    result = safe_place_order(
        {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
        settings,
        client,
    )
    assert result["status"] == "failed_validation"
    assert "ALLOW_LIVE_TRADING" in result["reason"]
    assert client.calls == []


def test_allow_live_accepts_literal_live_string():
    """BUG-031: the operator's natural-language 'live' must be accepted."""
    client = DummyClient()
    settings = make_settings(DRY_RUN="false", ALLOW_LIVE_TRADING="live")
    result = safe_place_order(
        {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
        settings,
        client,
    )
    assert result["status"] == "submitted"


def test_live_submission_allowed_only_with_explicit_gate():
    client = DummyClient()
    settings = make_settings(DRY_RUN="false", ALLOW_LIVE_TRADING="true")
    result = safe_place_order(
        {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
        settings,
        client,
    )
    assert result["status"] == "submitted"
    assert len(client.calls) == 1
class FailingClient:
    def place_order(self, **order):
        raise RuntimeError("exchange unavailable")


def test_live_submission_returns_failed_exchange_on_client_error():
    client = FailingClient()
    settings = make_settings(DRY_RUN="false", ALLOW_LIVE_TRADING="true")
    result = safe_place_order(
        {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
        settings,
        client,
    )
    assert result["status"] == "failed_exchange"
    assert "exchange unavailable" in result["reason"]


def test_invalid_qty_returns_failed_validation():
    client = DummyClient()
    settings = make_settings(DRY_RUN="true")
    result = safe_place_order(
        {"symbol": "BTCUSDT", "side": "buy", "qty": "abc"},
        settings,
        client,
    )
    assert result["status"] == "failed_validation"
    assert "invalid qty" in result["reason"].lower()


def test_non_positive_qty_returns_failed_validation():
    client = DummyClient()
    settings = make_settings(DRY_RUN="true")
    result = safe_place_order(
        {"symbol": "BTCUSDT", "side": "buy", "qty": 0},
        settings,
        client,
    )
    assert result["status"] == "failed_validation"
    assert "qty must be > 0" in result["reason"]
