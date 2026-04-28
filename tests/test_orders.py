"""Unit tests for risk guards (M3a) and kill-switch (M3b)."""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Stub optional heavy deps so pipeline can be imported without a full install.
for _mod in ("pandas", "matplotlib", "matplotlib.pyplot", "numpy", "scipy", "sklearn", "dotenv"):
    sys.modules.setdefault(_mod, MagicMock())

from src.runtime.orders import safe_place_order  # noqa: E402
from src.runtime.pipeline import HALT_FLAG_PATH, run_pipeline  # noqa: E402


class _DummyClient:
    def place_order(self, **order):
        return {"ok": True}


def _settings(**overrides):
    base = {"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "true"}
    base.update(overrides)
    return base


def _buy_order(price=50_000.0, qty=1.0):
    return {"symbol": "BTCUSDT", "side": "buy", "qty": qty, "price": price}


# ---------------------------------------------------------------------------
# MAX_POSITION_USD guard
# ---------------------------------------------------------------------------


def test_safe_place_order_raises_when_max_position_usd_exceeded():
    # notional = 1.0 qty * 50_000 price = 50_000 USD  >  10_000 limit
    settings = _settings(MAX_POSITION_USD="10000")
    with pytest.raises(ValueError, match="MAX_POSITION_USD"):
        safe_place_order(_buy_order(price=50_000.0, qty=1.0), settings, _DummyClient())


def test_safe_place_order_passes_when_notional_within_max_position_usd():
    # notional = 1.0 * 50_000 = 50_000 < 100_000
    settings = _settings(MAX_POSITION_USD="100000")
    result = safe_place_order(_buy_order(price=50_000.0, qty=1.0), settings, _DummyClient())
    assert result["status"] == "submitted"


# ---------------------------------------------------------------------------
# MAX_DAILY_LOSS_USD guard
# ---------------------------------------------------------------------------


def test_safe_place_order_raises_when_max_daily_loss_usd_at_limit():
    settings = _settings(MAX_DAILY_LOSS_USD="200", CURRENT_DAILY_LOSS_USD="200")
    with pytest.raises(ValueError, match="MAX_DAILY_LOSS_USD"):
        safe_place_order(_buy_order(), settings, _DummyClient())


def test_safe_place_order_raises_when_daily_loss_exceeds_limit():
    settings = _settings(MAX_DAILY_LOSS_USD="200", CURRENT_DAILY_LOSS_USD="350")
    with pytest.raises(ValueError, match="MAX_DAILY_LOSS_USD"):
        safe_place_order(_buy_order(), settings, _DummyClient())


def test_safe_place_order_passes_when_daily_loss_below_limit():
    settings = _settings(MAX_DAILY_LOSS_USD="500", CURRENT_DAILY_LOSS_USD="100")
    result = safe_place_order(_buy_order(), settings, _DummyClient())
    assert result["status"] == "submitted"


# ---------------------------------------------------------------------------
# Kill-switch / halt flag  (checked in run_pipeline before order submission)
# ---------------------------------------------------------------------------


def _signal_stub(signal: dict):
    """Return a pipeline signal_builder that always yields the given signal."""
    return lambda _settings: signal


_ACTIONABLE_SIGNAL = {"symbol": "BTCUSDT", "side": "buy", "qty": 1.0, "price": 50_000.0}


def test_run_pipeline_skips_order_when_halt_flag_exists():
    with patch("src.runtime.pipeline.os.path.exists", return_value=True):
        result = run_pipeline(
            settings=_settings(),
            exchange_client=_DummyClient(),
            signal_builder=_signal_stub(_ACTIONABLE_SIGNAL),
        )
    order_result = result["order_result"]
    assert order_result["status"] == "halted"
    assert order_result.get("reason") == "halt_flag_active"


def test_run_pipeline_places_order_when_halt_flag_absent():
    with patch("src.runtime.pipeline.os.path.exists", return_value=False):
        result = run_pipeline(
            settings=_settings(),
            exchange_client=_DummyClient(),
            signal_builder=_signal_stub(_ACTIONABLE_SIGNAL),
        )
    assert result["order_result"]["status"] == "submitted"
