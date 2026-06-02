"""Tradovate phase-1 wiring tests.

Covers the bridge between the bot's accounts unit and the Tradovate
package added in PR #2647:

  - ``tradovate_client_for(account)``: factory returns ``None`` when
    creds are missing; constructs a ``TradovateAdapter`` when set.
  - ``EXCHANGE_MAP`` registers ``"tradovate" → TradovateAPI``.
  - ``TradovateAPI.place()`` dry-run path returns a synthetic id and
    never imports the adapter.
  - ``_submit_order(exchange="tradovate", client=None)`` raises
    ``TradovateConfigError`` (so the coordinator's diagnostic-ping
    wrapper formats it as "not fully configured").
  - ``_submit_order(exchange="tradovate")`` refuses when
    ``tradovate_account_id`` is 0/unset.
  - Loader includes ``tradovate_demo_1`` and reports it as
    ``configured=True`` even without env vars (Tradovate doesn't use
    the ``api_key_env`` pattern, so the loader's cred check is skipped).
"""
from __future__ import annotations

import pytest

from src.core.coordinator import OrderPackage


_TRADOVATE_ENV = {
    "TRADOVATE_USERNAME": "u", "TRADOVATE_PASSWORD": "p",
    "TRADOVATE_APP_ID": "appid", "TRADOVATE_APP_VERSION": "1.0",
    "TRADOVATE_CID": "1234", "TRADOVATE_SECRET": "s",
    "TRADOVATE_DEVICE_ID": "dev",
}


def _set_tradovate_env(monkeypatch) -> None:
    for k, v in _TRADOVATE_ENV.items():
        monkeypatch.setenv(k, v)


def _pkg(symbol: str = "MES") -> OrderPackage:
    return OrderPackage(
        strategy="mes_trend_long_1d",
        symbol=symbol,
        direction="long",
        entry=5100.0,
        sl=5080.0,
        tp=5160.0,
        meta={},
    )


# ---------------------------------------------------------------------------
# EXCHANGE_MAP registration
# ---------------------------------------------------------------------------


class TestExchangeMap:
    def test_tradovate_registered(self):
        from src.units.accounts.integrator import EXCHANGE_MAP, TradovateAPI
        assert EXCHANGE_MAP["tradovate"] is TradovateAPI

    def test_tradovate_api_dry_run_returns_synthetic_id(self):
        from src.units.accounts.integrator import TradovateAPI
        api = TradovateAPI(api_key_env="")
        trade_id = api.place(_pkg(), dry_run=True)
        assert trade_id.startswith("dry-tradovate-")
        assert len(trade_id) > len("dry-tradovate-")

    def test_tradovate_api_live_requires_client(self):
        from src.units.accounts.integrator import TradovateAPI
        from src.units.accounts.tradovate.exceptions import TradovateConfigError
        api = TradovateAPI(api_key_env="")
        with pytest.raises(TradovateConfigError):
            api.place(_pkg(), dry_run=False)


# ---------------------------------------------------------------------------
# tradovate_client_for factory
# ---------------------------------------------------------------------------


class TestTradovateClientFactory:
    def test_returns_none_when_env_missing(self, monkeypatch):
        for k in _TRADOVATE_ENV:
            monkeypatch.delenv(k, raising=False)
        from src.units.accounts.clients import tradovate_client_for
        assert tradovate_client_for({"exchange": "tradovate"}) is None

    def test_returns_none_when_partial_env(self, monkeypatch):
        for k in _TRADOVATE_ENV:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("TRADOVATE_USERNAME", "u")
        # everything else missing
        from src.units.accounts.clients import tradovate_client_for
        assert tradovate_client_for({"exchange": "tradovate"}) is None

    def test_returns_none_for_non_tradovate_account(self, monkeypatch):
        _set_tradovate_env(monkeypatch)
        from src.units.accounts.clients import tradovate_client_for
        # The factory checks `exchange:` so other-exchange accounts get None
        # even when Tradovate creds happen to be in the env.
        assert tradovate_client_for({"exchange": "bybit"}) is None

    def test_constructs_adapter_when_env_present(self, monkeypatch):
        _set_tradovate_env(monkeypatch)
        from src.units.accounts.clients import tradovate_client_for
        from src.units.accounts.tradovate.adapter import TradovateAdapter
        client = tradovate_client_for({
            "exchange": "tradovate",
            "tradovate_env": "demo",
        })
        assert isinstance(client, TradovateAdapter)
        assert client.config.env.value == "demo"

    def test_yaml_env_toggle_routes_to_live_urls(self, monkeypatch):
        _set_tradovate_env(monkeypatch)
        from src.units.accounts.clients import tradovate_client_for
        client = tradovate_client_for({
            "exchange": "tradovate",
            "tradovate_env": "live",
        })
        assert client is not None
        assert client.config.env.value == "live"
        assert "live.tradovateapi.com" in client.config.urls.rest_base

    def test_invalid_yaml_env_defaults_to_demo(self, monkeypatch):
        _set_tradovate_env(monkeypatch)
        from src.units.accounts.clients import tradovate_client_for
        client = tradovate_client_for({
            "exchange": "tradovate",
            "tradovate_env": "staging",  # invalid
        })
        assert client is not None
        assert client.config.env.value == "demo"


# ---------------------------------------------------------------------------
# _submit_order branch
# ---------------------------------------------------------------------------


class TestSubmitOrderTradovateBranch:
    def test_missing_client_raises_config_error(self):
        from src.units.accounts.execute import _submit_order
        from src.units.accounts.tradovate.exceptions import TradovateConfigError
        order = {"symbol": "MES", "side": "Buy", "qty": 1}
        with pytest.raises(TradovateConfigError, match="not fully configured"):
            _submit_order(client=None, order=order, account_cfg={
                "exchange": "tradovate",
                "tradovate_account_id": 12345,
                "account_id": "tradovate_demo_1",
            })

    def test_wrong_client_type_raises(self, monkeypatch):
        _set_tradovate_env(monkeypatch)
        from src.units.accounts.execute import _submit_order

        class _FakeClient:
            pass

        order = {"symbol": "MES", "side": "Buy", "qty": 1}
        with pytest.raises(TypeError, match="expected TradovateAdapter"):
            _submit_order(client=_FakeClient(), order=order, account_cfg={
                "exchange": "tradovate",
                "tradovate_account_id": 12345,
            })

    def test_missing_account_id_raises(self, monkeypatch):
        _set_tradovate_env(monkeypatch)
        from src.units.accounts.clients import tradovate_client_for
        from src.units.accounts.execute import _submit_order
        from src.units.accounts.tradovate.exceptions import TradovateConfigError

        client = tradovate_client_for({
            "exchange": "tradovate", "tradovate_env": "demo",
        })
        order = {"symbol": "MES", "side": "Buy", "qty": 1}
        with pytest.raises(TradovateConfigError, match="tradovate_account_id"):
            _submit_order(client=client, order=order, account_cfg={
                "exchange": "tradovate",
                "tradovate_account_id": 0,  # not set
            })

    def test_dry_run_through_adapter_returns_synthetic_id(self, monkeypatch):
        # The adapter's TRADOVATE_DRY_RUN=true gate (the package's second
        # safety net) returns a synthetic negative-id Order without
        # touching the wire.
        _set_tradovate_env(monkeypatch)
        monkeypatch.setenv("TRADOVATE_DRY_RUN", "true")
        from src.units.accounts.clients import tradovate_client_for
        from src.units.accounts.execute import _submit_order

        client = tradovate_client_for({
            "exchange": "tradovate", "tradovate_env": "demo",
        })
        assert client is not None
        order = {"symbol": "MES", "side": "Buy", "qty": 1}
        out = _submit_order(client=client, order=order, account_cfg={
            "exchange": "tradovate",
            "tradovate_account_id": 999,
        })
        # Synthetic id space is negative integers; the executor returns
        # them as strings.
        assert int(out) < 0


# ---------------------------------------------------------------------------
# accounts.yaml entry
# ---------------------------------------------------------------------------


class TestAccountsYamlEntry:
    def test_tradovate_demo_1_loads(self, monkeypatch):
        # No Tradovate env vars — the loader must still surface the
        # account (configured=True because it doesn't use api_key_env).
        for k in _TRADOVATE_ENV:
            monkeypatch.delenv(k, raising=False)
        from src.units.accounts import load_accounts
        accounts = load_accounts()
        tv = [a for a in accounts if a.name == "tradovate_demo_1"]
        assert len(tv) == 1
        a = tv[0]
        assert a.exchange == "tradovate"
        # Ships inert: dry_run + empty strategies → never trades until
        # the operator promotes (Tier-3, set-account-mode action).
        assert a.dry_run is True
        assert a.strategies == []
        assert a.configured is True
