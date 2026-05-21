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

S-012 fix was subsequently superseded by operator directive 2026-05-03:
per-account ``mode: live | dry_run`` in ``config/accounts.yaml`` is now
the single dry/live toggle; ``build_settings_from_env`` and
``safe_place_order`` no longer carry or consult DRY_RUN /
ALLOW_LIVE_TRADING.  Tests updated to verify the current contracts
(b: outdated contract — cite: validation.py:115-125, orders.py:213-220
"Operator directive 2026-05-03").
"""
from __future__ import annotations

from unittest.mock import MagicMock



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
# (b) OUTDATED CONTRACT — operator directive 2026-05-03 removed DRY_RUN /
# ALLOW_LIVE_TRADING from build_settings_from_env.  The new contract is that
# neither key appears; per-account config/accounts.yaml mode is the toggle.
# ---------------------------------------------------------------------------


def test_settings_dict_does_not_include_dry_run(monkeypatch):
    """build_settings_from_env must NOT emit DRY_RUN / ALLOW_LIVE_TRADING;
    per-account mode in accounts.yaml is the sole dry/live toggle
    (operator directive 2026-05-03, validation.py:115-125)."""
    from src.runtime.validation import build_settings_from_env

    _set_env(monkeypatch)
    s = build_settings_from_env()
    assert "DRY_RUN" not in s, (
        "build_settings_from_env must NOT emit DRY_RUN after operator "
        "directive 2026-05-03 removed process-level mode interlocks."
    )
    assert "ALLOW_LIVE_TRADING" not in s, (
        "build_settings_from_env must NOT emit ALLOW_LIVE_TRADING after "
        "operator directive 2026-05-03."
    )


def test_settings_dict_does_not_include_allow_live_trading(monkeypatch):
    """Companion: ALLOW_LIVE_TRADING not present under any env combination."""
    from src.runtime.validation import build_settings_from_env

    _set_env(monkeypatch)
    s = build_settings_from_env()
    assert "ALLOW_LIVE_TRADING" not in s
    assert "allow_live_trading" not in s


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
        "safe_place_order is a payload-validation + risk-cap rail only "
        "(operator directive 2026-05-03, orders.py:213-220); it always "
        "submits for valid orders."
    )
    assert client.place_order.called


def test_safe_place_order_always_submits_regardless_of_dry_run_key(monkeypatch):
    """(b) OUTDATED CONTRACT — operator directive 2026-05-03: safe_place_order
    no longer consults DRY_RUN.  Even if the caller injects DRY_RUN=True into
    settings, the function submits; the dry-run interlock is per-account in
    RiskManager (orders.py:213-220)."""
    from src.runtime.validation import build_settings_from_env
    from src.runtime.orders import safe_place_order

    _set_env(monkeypatch, DRY_RUN="true", ALLOW_LIVE_TRADING="true")
    settings = build_settings_from_env()
    # Even if the caller manually injects a DRY_RUN key, safe_place_order
    # ignores it and submits.
    settings["DRY_RUN"] = True

    client = MagicMock()
    client.place_order.return_value = {"orderId": "x", "ok": True}
    order = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "qty": 0.0005,
        "price": 50_000.0,
        "meta": {"strategy_name": "turtle_soup"},
    }
    result = safe_place_order(order, settings, client)
    # safe_place_order is NOT the dry-run gate; it always submits valid orders.
    assert result["status"] == "submitted"
    assert client.place_order.called


def test_safe_place_order_submits_even_without_allow_live_key(monkeypatch):
    """(b) OUTDATED CONTRACT — safe_place_order no longer requires
    ALLOW_LIVE_TRADING (operator directive 2026-05-03, orders.py:213-220).
    A missing ALLOW_LIVE_TRADING key is not a validation failure."""
    from src.runtime.validation import build_settings_from_env
    from src.runtime.orders import safe_place_order

    _set_env(monkeypatch, DRY_RUN="false", ALLOW_LIVE_TRADING="false", MODE="BACKTEST")
    settings = build_settings_from_env()

    client = MagicMock()
    client.place_order.return_value = {"orderId": "x", "ok": True}
    order = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "qty": 0.0005,
        "price": 50_000.0,
        "meta": {"strategy_name": "vwap"},
    }
    result = safe_place_order(order, settings, client)
    # No ALLOW_LIVE_TRADING interlock in safe_place_order post-directive.
    assert result["status"] == "submitted"
    assert client.place_order.called


# ---------------------------------------------------------------------------
# Pin: build_settings_from_env source documents the operator directive
# ---------------------------------------------------------------------------


def test_build_settings_from_env_source_documents_operator_directive():
    """The removal of DRY_RUN / ALLOW_LIVE_TRADING from build_settings_from_env
    is documented inline with 'operator directive 2026-05-03'. This test pins
    the comment so a future refactor can't silently re-add the keys without
    also updating the directive comment."""
    import inspect
    from src.runtime import validation

    src = inspect.getsource(validation.build_settings_from_env)
    assert "2026-05-03" in src or "per-account" in src, (
        "build_settings_from_env docstring must document the operator "
        "directive (2026-05-03) that removed process-level dry/live flags. "
        "S-012 hotfix superseded by operator directive."
    )
