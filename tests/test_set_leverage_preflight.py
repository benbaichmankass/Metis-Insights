"""Pin the boot-time set_leverage pre-flight in src/main.py.

The pre-flight historically routed through BybitConnector's hand-rolled
V5 signer (PR #903), which returned retCode=10003 'API key is invalid'
on the same credentials that successfully placed orders via pybit
(FU-20260510-005). The fix routes through pybit's
`unified_trading.HTTP.set_leverage` — the same client factory order
placement already uses — so the auth path is identical to the
proven-working order path.

These tests pin:
  1. The pre-flight calls `bybit_client_for(account_cfg)` to construct
     the pybit HTTP client (NOT BybitConnector).
  2. The pre-flight invokes `client.set_leverage(...)` with the V5
     contract: `category="linear"`, `buyLeverage` and `sellLeverage`
     as string-typed integers (Bybit V5 110044 rule).
  3. retCode 0 + retCode 110043 are both treated as success (idempotent
     "already at target leverage" semantics).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fake_account():
    """A linear-perp bybit account with leverage=3 configured."""
    acct = MagicMock()
    acct.name = "bybit_2"
    acct.market_type = "linear"
    acct.exchange = "bybit"
    acct.api_key_env = "BYBIT_API_KEY"
    acct.env_path = ""
    acct.risk_manager = MagicMock()
    acct.risk_manager.leverage = 3
    return acct


@pytest.fixture
def fake_strategies_cfg():
    return {
        "strategies": {
            "vwap": {"symbols": ["BTCUSDT"]},
        }
    }


def _call_preflight(monkeypatch, fake_account, fake_strategies_cfg, fake_http):
    """Patch the import surface + invoke _apply_per_account_leverage()."""
    from src import main as main_mod

    monkeypatch.setattr(
        "src.units.accounts.load_accounts",
        lambda: [fake_account],
        raising=False,
    )
    monkeypatch.setattr(
        "src.units.accounts.clients.bybit_client_for",
        lambda cfg: fake_http,
        raising=True,
    )
    monkeypatch.setattr(
        "src.units.strategies.load_strategy_config",
        lambda: fake_strategies_cfg,
        raising=False,
    )
    # Force the strategies-discovery helper to return our symbols
    # regardless of the account's wired strategies list.
    monkeypatch.setattr(
        main_mod,
        "_symbols_for_account",
        lambda account, cfg: ["BTCUSDT"],
        raising=True,
    )

    main_mod._apply_per_account_leverage()


def test_preflight_routes_through_pybit_client(monkeypatch, fake_account, fake_strategies_cfg):
    """The pre-flight must use pybit's HTTP.set_leverage, NOT
    BybitConnector. This pins the FU-005 fix: same auth path as
    order placement."""
    fake_http = MagicMock()
    fake_http.set_leverage.return_value = {"retCode": 0, "retMsg": "OK"}

    # BybitConnector should NOT be touched by the pre-flight anymore.
    with patch("src.exchange.bybit_connector.BybitConnector") as bc:
        _call_preflight(monkeypatch, fake_account, fake_strategies_cfg, fake_http)
        assert not bc.called, (
            "Pre-flight must not instantiate BybitConnector — "
            "it's the broken auth path (FU-20260510-005)."
        )

    # pybit's set_leverage was called once with the V5 contract.
    assert fake_http.set_leverage.call_count == 1
    kwargs = fake_http.set_leverage.call_args.kwargs
    assert kwargs["category"] == "linear"
    assert kwargs["symbol"] == "BTCUSDT"
    # V5 rule 110044: buyLeverage and sellLeverage must be the SAME
    # string-typed integer.
    assert kwargs["buyLeverage"] == "3"
    assert kwargs["sellLeverage"] == "3"
    assert isinstance(kwargs["buyLeverage"], str)
    assert isinstance(kwargs["sellLeverage"], str)


def test_preflight_treats_110043_as_idempotent_success(monkeypatch, fake_account, fake_strategies_cfg, caplog):
    """retCode=110043 ('leverage not modified') means the target value
    was already set — that's a success, not a warning."""
    fake_http = MagicMock()
    fake_http.set_leverage.return_value = {
        "retCode": 110043,
        "retMsg": "leverage not modified",
    }
    import logging
    with caplog.at_level(logging.INFO):
        _call_preflight(monkeypatch, fake_account, fake_strategies_cfg, fake_http)

    msgs = [r.getMessage() for r in caplog.records]
    assert any("ok (retCode=110043)" in m for m in msgs), msgs
    # No WARNING for the idempotent path.
    assert not any("rejected" in m for m in msgs), msgs


def test_preflight_logs_warning_on_real_failure(monkeypatch, fake_account, fake_strategies_cfg, caplog):
    """A genuine rejection (e.g., 10001 'parameter error') should
    surface as a WARNING but not abort the pre-flight loop."""
    fake_http = MagicMock()
    fake_http.set_leverage.return_value = {
        "retCode": 10001,
        "retMsg": "parameter error",
    }
    import logging
    with caplog.at_level(logging.WARNING):
        _call_preflight(monkeypatch, fake_account, fake_strategies_cfg, fake_http)

    msgs = [r.getMessage() for r in caplog.records]
    assert any(
        "rejected (retCode=10001" in m and "parameter error" in m
        for m in msgs
    ), msgs


def test_preflight_absorbs_pybit_110043_exception(monkeypatch, fake_account, fake_strategies_cfg, caplog):
    """pybit raises InvalidRequestError on some non-zero retCodes; the
    pre-flight must still treat 110043 (already set) as success."""
    fake_http = MagicMock()
    fake_http.set_leverage.side_effect = RuntimeError(
        "(ErrCode: 110043) leverage not modified"
    )
    import logging
    with caplog.at_level(logging.INFO):
        _call_preflight(monkeypatch, fake_account, fake_strategies_cfg, fake_http)

    msgs = [r.getMessage() for r in caplog.records]
    assert any(
        "already set (retCode=110043, idempotent)" in m for m in msgs
    ), msgs
