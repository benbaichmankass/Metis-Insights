"""M15 Phase 2 — OANDA wiring tests (new-broker checklist §8).

Covers EXCHANGE_MAP registration, factory cred handling,
``_submit_order`` edge cases (missing client, wrong type, success and
reject envelopes), the OandaClient order/close payload shapes (mocked
HTTP, no network), and the inert ``accounts.yaml::oanda_practice``
entry's gates.
"""
from __future__ import annotations

import pytest
import yaml

from src.units.accounts.clients import oanda_client_for
from src.units.accounts.execute import _submit_order
from src.units.accounts.integrator import EXCHANGE_MAP, OandaAPI
from src.units.accounts.oanda_client import (
    MissingCredentialsError,
    OandaClient,
    to_instrument,
)


# ------------------------------------------------------------ registry
def test_exchange_map_has_oanda():
    assert EXCHANGE_MAP["oanda"] is OandaAPI


def test_to_instrument():
    assert to_instrument("XAUUSD") == "XAU_USD"
    assert to_instrument("XAU_USD") == "XAU_USD"
    assert to_instrument("EURUSD") == "EUR_USD"


# ------------------------------------------------------------ factory
def test_factory_returns_none_without_creds(monkeypatch):
    monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)
    assert oanda_client_for({"exchange": "oanda"}) is None


def test_factory_builds_client_with_creds(monkeypatch):
    monkeypatch.setenv("OANDA_API_TOKEN", "tok")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "001-001-1234567-001")
    cli = oanda_client_for({"exchange": "oanda"})
    assert isinstance(cli, OandaClient)
    assert cli.env == "practice"  # default host is the practice API
    assert "fxpractice" in cli.base_url


# ------------------------------------------------------------ executor
_ORDER = {
    "symbol": "XAUUSD",
    "side": "Buy",
    "qty": 2,
    "entry": 2300.0,
    "sl": 2290.0,
    "tp": 2320.0,
    "strategy": "trend_donchian",
}
_CFG = {"exchange": "oanda", "account_id": "oanda_practice"}


def test_submit_order_missing_client_raises_missing_creds():
    with pytest.raises(MissingCredentialsError):
        _submit_order(None, dict(_ORDER), dict(_CFG))


def test_submit_order_wrong_client_type():
    with pytest.raises(TypeError):
        _submit_order(object(), dict(_ORDER), dict(_CFG))


class _StubOanda(OandaClient):
    def __init__(self, resp):
        super().__init__(api_token="tok", account_id="acct")
        self._resp = resp
        self.last_order = None

    def place(self, order):
        self.last_order = order
        return self._resp


def test_submit_order_success_returns_order_id():
    cli = _StubOanda({"retCode": 0, "result": {"orderId": "42"}})
    assert _submit_order(cli, dict(_ORDER), dict(_CFG)) == "42"
    assert cli.last_order["symbol"] == "XAUUSD"
    assert cli.last_order["sl"] == 2290.0


def test_submit_order_reject_raises_runtime_error():
    cli = _StubOanda({"retCode": 400, "retMsg": "INSUFFICIENT_MARGIN"})
    with pytest.raises(RuntimeError, match="INSUFFICIENT_MARGIN"):
        _submit_order(cli, dict(_ORDER), dict(_CFG))


# ------------------------------------------------------------ client HTTP
class _Resp:
    def __init__(self, payload, status=201):
        self._payload = payload
        self.status_code = status
        self.content = b"x"

    def json(self):
        return self._payload


def test_client_place_builds_market_order_with_sl_tp(monkeypatch):
    captured = {}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        captured.update(method=method, url=url, body=json)
        return _Resp({"orderFillTransaction": {"id": "77"}})

    monkeypatch.setattr(
        "src.units.accounts.oanda_client.requests.request", fake_request
    )
    cli = OandaClient(api_token="tok", account_id="acct")
    out = cli.place({"symbol": "XAUUSD", "side": "Sell", "qty": 3,
                     "sl": 2310.1234, "tp": 2280.5})
    assert out == {"retCode": 0, "result": {"orderId": "77"}}
    o = captured["body"]["order"]
    assert o["instrument"] == "XAU_USD"
    assert o["units"] == "-3"  # sell = negative units
    assert o["stopLossOnFill"]["price"] == "2310.123"  # metals: 3dp
    assert o["takeProfitOnFill"]["price"] == "2280.500"
    assert "/v3/accounts/acct/orders" in captured["url"]


def test_client_place_fok_cancel_is_error(monkeypatch):
    monkeypatch.setattr(
        "src.units.accounts.oanda_client.requests.request",
        lambda *a, **k: _Resp(
            {"orderCreateTransaction": {"id": "1"},
             "orderCancelTransaction": {"reason": "INSUFFICIENT_LIQUIDITY"}}
        ),
    )
    cli = OandaClient(api_token="tok", account_id="acct")
    out = cli.place({"symbol": "EURUSD", "side": "Buy", "qty": 1})
    assert out["retCode"] == -3
    assert "INSUFFICIENT_LIQUIDITY" in out["retMsg"]


def test_client_requires_creds():
    cli = OandaClient(api_token="", account_id="")
    with pytest.raises(MissingCredentialsError):
        cli.place({"symbol": "XAUUSD", "side": "Buy", "qty": 1})
    assert cli.balance() is None  # degrades, never raises
    assert cli.positions() == []


def test_client_close_idempotent_when_flat(monkeypatch):
    cli = OandaClient(api_token="tok", account_id="acct")
    monkeypatch.setattr(cli, "positions", lambda: [])
    assert cli.close("XAUUSD")["retCode"] == 0


# ------------------------------------------------------------ config gates
def test_accounts_yaml_oanda_practice_ships_inert():
    cfg = yaml.safe_load(open("config/accounts.yaml"))
    acct = cfg["accounts"]["oanda_practice"]
    assert acct["exchange"] == "oanda"
    assert acct["mode"] == "live"  # practice money; flipped 2026-06-11 (set-account-mode)
    # Phase 3 assigned xauusd_trend_1h (execution: shadow); inertness now
    # rests on mode dry_run + missing creds + strategy-level shadow.
    assert acct["strategies"] == ["xauusd_trend_1h"]
    assert acct["demo"] is True
    assert acct["symbols"] == ["XAUUSD"]
