"""M15 Phase 2b — Alpaca wiring tests (new-broker checklist §8).

Registry, factory cred handling, executor edges, bracket-order payload
shapes (mocked HTTP, no network), idempotent close, and the inert
``accounts.yaml::alpaca_paper`` gates.
"""
from __future__ import annotations

import pytest
import yaml

from src.units.accounts.alpaca_client import AlpacaClient, MissingCredentialsError
from src.units.accounts.clients import alpaca_client_for
from src.units.accounts.execute import _submit_order
from src.units.accounts.integrator import EXCHANGE_MAP, AlpacaAPI


def test_exchange_map_has_alpaca():
    assert EXCHANGE_MAP["alpaca"] is AlpacaAPI


# ------------------------------------------------------------ factory
def test_factory_returns_none_without_creds(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    assert alpaca_client_for({"exchange": "alpaca"}) is None


def test_factory_builds_paper_client_with_creds(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")
    cli = alpaca_client_for({"exchange": "alpaca"})
    assert isinstance(cli, AlpacaClient)
    assert cli.env == "paper"
    assert "paper-api" in cli.base_url


# ------------------------------------------------------------ executor
_ORDER = {
    "symbol": "SPY",
    "side": "Buy",
    "qty": 3,
    "entry": 600.0,
    "sl": 594.0,
    "tp": 612.0,
    "strategy": "spy_trend_long_1d",
}
_CFG = {"exchange": "alpaca", "account_id": "alpaca_paper"}


def test_submit_order_missing_client_raises_missing_creds():
    with pytest.raises(MissingCredentialsError):
        _submit_order(None, dict(_ORDER), dict(_CFG))


def test_submit_order_wrong_client_type():
    with pytest.raises(TypeError):
        _submit_order(object(), dict(_ORDER), dict(_CFG))


class _StubAlpaca(AlpacaClient):
    def __init__(self, resp):
        super().__init__(api_key="k", api_secret="s")
        self._resp = resp
        self.last_order = None

    def place(self, order):
        self.last_order = order
        return self._resp


def test_submit_order_success_returns_order_id():
    cli = _StubAlpaca({"retCode": 0, "result": {"orderId": "abc-123"}})
    assert _submit_order(cli, dict(_ORDER), dict(_CFG)) == "abc-123"
    assert cli.last_order["symbol"] == "SPY"


def test_submit_order_reject_raises_runtime_error():
    cli = _StubAlpaca({"retCode": 403, "retMsg": "insufficient buying power"})
    with pytest.raises(RuntimeError, match="insufficient buying power"):
        _submit_order(cli, dict(_ORDER), dict(_CFG))


# ------------------------------------------------------------ client HTTP
class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = b"x"

    def json(self):
        return self._payload


def test_client_place_builds_bracket_order(monkeypatch):
    captured = {}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        captured.update(method=method, url=url, body=json)
        return _Resp({"id": "ord-9"})

    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.requests.request", fake_request
    )
    cli = AlpacaClient(api_key="k", api_secret="s")
    out = cli.place({"symbol": "spy", "side": "Sell", "qty": 2.4,
                     "sl": 610.456, "tp": 588.1})
    assert out == {"retCode": 0, "result": {"orderId": "ord-9"}}
    b = captured["body"]
    assert b["symbol"] == "SPY"
    assert b["qty"] == "2"  # rounded to whole shares (bracket constraint)
    assert b["side"] == "sell"
    assert b["order_class"] == "bracket"
    assert b["take_profit"]["limit_price"] == "588.10"
    assert b["stop_loss"]["stop_price"] == "610.46"
    assert "/v2/orders" in captured["url"]


def test_client_requires_creds_and_degrades():
    cli = AlpacaClient(api_key="", api_secret="")
    with pytest.raises(MissingCredentialsError):
        cli.place({"symbol": "SPY", "side": "Buy", "qty": 1})
    assert cli.balance() is None
    assert cli.positions() == []


def test_client_close_idempotent_on_404(monkeypatch):
    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.requests.request",
        lambda *a, **k: _Resp({"message": "position does not exist"}, status=404),
    )
    cli = AlpacaClient(api_key="k", api_secret="s")
    assert cli.close("SPY")["retCode"] == 0


# ------------------------------------------------------------ config gates
def test_accounts_yaml_alpaca_paper_ships_inert():
    acct = yaml.safe_load(open("config/accounts.yaml"))["accounts"]["alpaca_paper"]
    assert acct["exchange"] == "alpaca"
    assert acct["mode"] == "live"  # practice/paper money; flipped 2026-06-11 (set-account-mode)
    # M15 Phase 4 buildout assigned the ETF legs (operator-approved); the
    # ETF-breadth daily sweep (2026-06-20) added iwm/tlt/ief (Tier-3); the
    # intraday ETF pilot (2026-06-20 § 0e) added gld_pullback_1h/slv_trend_1h.
    assert acct["strategies"] == [
        "spy_trend_long_1d", "qqq_trend_long_1d", "gld_pullback_1d",
        "iwm_trend_long_1d", "tlt_pullback_1d", "ief_pullback_1d",
        "gld_pullback_1h", "slv_trend_1h",
    ]
    # 2026-06-15: the old `demo: true` category stamp was superseded by
    # account_class (non-Bybit, so demo was only the category marker).
    assert "demo" not in acct
    assert acct["account_class"] == "paper"
    assert acct["symbols"] == ["SPY", "QQQ", "GLD", "IWM", "TLT", "IEF", "SLV"]
