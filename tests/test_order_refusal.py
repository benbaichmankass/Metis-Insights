"""M3c refusal tests: prove safe_place_order blocks orders for all three hard guards."""
from __future__ import annotations

import pytest

from src.runtime.orders import safe_place_order


class _Client:
    def place_order(self, **order):
        return {"ok": True}


def _settings(**overrides):
    base = {"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "true"}
    base.update(overrides)
    return base


def _order(price=50_000.0, qty=1.0):
    return {"symbol": "BTCUSDT", "side": "buy", "qty": qty, "price": price}


# ---------------------------------------------------------------------------
# Guard 1 — MAX_POSITION_USD
# ---------------------------------------------------------------------------


def test_order_refused_when_notional_exceeds_max_position_usd():
    # notional = 1.0 * 50_000 = 50_000 > 10_000 limit
    with pytest.raises(ValueError, match="MAX_POSITION_USD"):
        safe_place_order(_order(price=50_000.0, qty=1.0), _settings(MAX_POSITION_USD="10000"), _Client())


def test_order_allowed_when_notional_within_max_position_usd():
    # notional = 0.1 * 50_000 = 5_000 < 10_000 limit
    result = safe_place_order(_order(price=50_000.0, qty=0.1), _settings(MAX_POSITION_USD="10000"), _Client())
    assert result["status"] == "submitted"


def test_order_allowed_when_max_position_usd_not_configured():
    result = safe_place_order(_order(), _settings(), _Client())
    assert result["status"] == "submitted"


# ---------------------------------------------------------------------------
# Guard 2 — MAX_DAILY_LOSS_USD
# ---------------------------------------------------------------------------


def test_order_refused_when_daily_loss_at_limit():
    settings = _settings(MAX_DAILY_LOSS_USD="200", CURRENT_DAILY_LOSS_USD="200")
    with pytest.raises(ValueError, match="MAX_DAILY_LOSS_USD"):
        safe_place_order(_order(), settings, _Client())


def test_order_refused_when_daily_loss_exceeds_limit():
    settings = _settings(MAX_DAILY_LOSS_USD="200", CURRENT_DAILY_LOSS_USD="350")
    with pytest.raises(ValueError, match="MAX_DAILY_LOSS_USD"):
        safe_place_order(_order(), settings, _Client())


def test_order_allowed_when_daily_loss_below_limit():
    settings = _settings(MAX_DAILY_LOSS_USD="500", CURRENT_DAILY_LOSS_USD="100")
    result = safe_place_order(_order(), settings, _Client())
    assert result["status"] == "submitted"


def test_order_allowed_when_max_daily_loss_not_configured():
    result = safe_place_order(_order(), _settings(), _Client())
    assert result["status"] == "submitted"


# ---------------------------------------------------------------------------
# Guard 3 — halt flag (checked inside safe_place_order)
# ---------------------------------------------------------------------------


def test_order_refused_when_halt_flag_active(tmp_path):
    flag = tmp_path / "halt.flag"
    flag.write_text("halted")
    settings = _settings(HALT_FLAG_PATH=str(flag))
    result = safe_place_order(_order(), settings, _Client())
    assert result["status"] == "halted"
    assert result["reason"] == "halt_flag_active"


def test_order_allowed_when_halt_flag_absent(tmp_path):
    flag = tmp_path / "halt.flag"  # does not exist
    settings = _settings(HALT_FLAG_PATH=str(flag))
    result = safe_place_order(_order(), settings, _Client())
    assert result["status"] == "submitted"


def test_order_allowed_when_halt_flag_path_not_configured():
    # No HALT_FLAG_PATH in settings — guard is skipped entirely.
    result = safe_place_order(_order(), _settings(), _Client())
    assert result["status"] == "submitted"
