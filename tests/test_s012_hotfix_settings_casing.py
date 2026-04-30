"""S-012 hotfix: settings-dict / safe_place_order case-mismatch regression.

Original symptom (post-PR-E1, observed on the live bot):

    Pipeline result: status=failed_validation | symbol=BTCUSDT |
    side=buy | qty=0.0005 |
    reason=ALLOW_LIVE_TRADING=true is required for live submission

Root cause: ``build_settings_from_env()`` wrote the live-mode flags as
**lowercase** keys (``dry_run`` / ``allow_live_trading``), but
``safe_place_order()`` reads them with **uppercase** keys (``DRY_RUN``
/ ``ALLOW_LIVE_TRADING``). Even with the env set correctly, the
order-layer lookup defaulted to "false" and rejected every live
signal.

Fix: ``build_settings_from_env()`` now emits both casings. This file
locks the fix in place.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


_BASE_VALID_ENV = {
    "EXCHANGE": "bybit",
    "BYBIT_API_KEY": "k",
    "BYBIT_API_SECRET": "s",
    "TELEGRAM_BOT_TOKEN": "tt",
    "TELEGRAM_CHAT_ID": "1",
    "MODE": "LIVE",
    "SYMBOL": "BTCUSDT",
    "TIMEFRAME": "15m",
    "RISK_PER_TRADE": "0.01",
    "MAX_QTY": "1",
}


def _set_env(monkeypatch, **overrides):
    for key, value in {**_BASE_VALID_ENV, **overrides}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, str(value))


# ---------------------------------------------------------------------------
# build_settings_from_env emits BOTH casings
# ---------------------------------------------------------------------------


def test_settings_dict_includes_uppercase_dry_run(monkeypatch):
    from src.runtime.validation import build_settings_from_env

    _set_env(monkeypatch, DRY_RUN="false", ALLOW_LIVE_TRADING="true")
    s = build_settings_from_env()
    assert "DRY_RUN" in s, (
        "build_settings_from_env must emit uppercase 'DRY_RUN' so "
        "safe_place_order's lookup finds it. S-012 hotfix."
    )
    assert s["DRY_RUN"] is False
    assert s["dry_run"] is False  # lowercase still present for back-compat


def test_settings_dict_includes_uppercase_allow_live_trading(monkeypatch):
    from src.runtime.validation import build_settings_from_env

    _set_env(monkeypatch, DRY_RUN="false", ALLOW_LIVE_TRADING="true")
    s = build_settings_from_env()
    assert "ALLOW_LIVE_TRADING" in s, (
        "build_settings_from_env must emit uppercase 'ALLOW_LIVE_TRADING' "
        "so safe_place_order's lookup finds it. S-012 hotfix — without "
        "this key every live signal is rejected with reason "
        "'ALLOW_LIVE_TRADING=true is required for live submission'."
    )
    assert s["ALLOW_LIVE_TRADING"] is True
    assert s["allow_live_trading"] is True  # back-compat lowercase

    _set_env(monkeypatch, DRY_RUN="false", ALLOW_LIVE_TRADING="false")
    s = build_settings_from_env()
    assert s["ALLOW_LIVE_TRADING"] is False
    assert s["allow_live_trading"] is False


# ---------------------------------------------------------------------------
# End-to-end: safe_place_order reaches the exchange-submission path
# ---------------------------------------------------------------------------


def test_safe_place_order_submits_when_settings_built_from_env(monkeypatch):
    """The exact production path: build settings from a live-mode env,
    pass to safe_place_order, observe a 'submitted' status (not
    'failed_validation' / 'dry_run')."""
    from src.runtime.validation import build_settings_from_env
    from src.runtime.orders import safe_place_order

    _set_env(monkeypatch, DRY_RUN="false", ALLOW_LIVE_TRADING="true")
    settings = build_settings_from_env()

    client = MagicMock()
    client.place_order.return_value = {"orderId": "exchange-123", "ok": True}
    order = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "qty": 0.0005,
        "price": 50_000.0,
        "meta": {"strategy_name": "vwap"},
    }

    result = safe_place_order(order, settings, client)

    assert result["status"] == "submitted", (
        f"Expected live submission; got {result}. "
        "If reason includes 'ALLOW_LIVE_TRADING=true is required', the "
        "S-012 hotfix has regressed: build_settings_from_env must emit "
        "the uppercase key safe_place_order looks for."
    )
    assert client.place_order.called


def test_safe_place_order_dry_run_when_dry_run_env_true(monkeypatch):
    """The legitimate dry-run path: settings.DRY_RUN=True → no exchange call."""
    from src.runtime.validation import build_settings_from_env
    from src.runtime.orders import safe_place_order

    _set_env(monkeypatch, DRY_RUN="true", ALLOW_LIVE_TRADING="true")
    settings = build_settings_from_env()

    client = MagicMock()
    order = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "qty": 0.0005,
        "price": 50_000.0,
        "meta": {"strategy_name": "turtle_soup"},
    }
    result = safe_place_order(order, settings, client)
    assert result["status"] == "dry_run"
    assert not client.place_order.called


def test_safe_place_order_blocks_when_env_drops_allow_live(monkeypatch):
    """Validation passes (e.g. DRY_RUN=true) but a downstream code path
    flips DRY_RUN=false at runtime — the order layer must still refuse
    without ALLOW_LIVE_TRADING. The interlock is preserved by the hotfix."""
    from src.runtime.validation import build_settings_from_env
    from src.runtime.orders import safe_place_order

    _set_env(monkeypatch, DRY_RUN="true", ALLOW_LIVE_TRADING="false", MODE="BACKTEST")
    settings = build_settings_from_env()
    # Simulate the runtime forcing DRY_RUN=false (e.g. an operator
    # override) without flipping ALLOW_LIVE_TRADING.
    settings["DRY_RUN"] = False
    settings["dry_run"] = False

    client = MagicMock()
    order = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "qty": 0.0005,
        "price": 50_000.0,
        "meta": {"strategy_name": "vwap"},
    }
    result = safe_place_order(order, settings, client)
    assert result["status"] == "failed_validation"
    assert "ALLOW_LIVE_TRADING" in result["reason"]
    assert not client.place_order.called


# ---------------------------------------------------------------------------
# Pin: the source has the dual-casing pattern documented
# ---------------------------------------------------------------------------


def test_build_settings_from_env_source_documents_uppercase_aliases():
    """The dual-casing isn't an accident — it's a documented contract.
    If a future refactor drops the uppercase emission, this test fails
    with the exact reason."""
    import inspect
    from src.runtime import validation

    src = inspect.getsource(validation.build_settings_from_env)
    assert '"DRY_RUN"' in src and '"ALLOW_LIVE_TRADING"' in src, (
        "build_settings_from_env source must explicitly emit uppercase "
        "DRY_RUN and ALLOW_LIVE_TRADING aliases. S-012 hotfix."
    )
