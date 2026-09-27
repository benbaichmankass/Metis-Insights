"""FIX-CA-12 (CA-A08-diag-venue-reads-collapse-could-not-look).

Nine token-gated venue-truth diag routes used to answer HTTP 200 with
``accounts: []`` (and ``ib_open_orders`` ``count: 0``) both when
``list_accounts()`` raised and when ``account_id`` named no configured
account — so "could not look" / "wrong account id" read exactly like "looked,
the venue holds nothing".

Contract now:
  * account list unreadable (``list_accounts`` raised, or ``accounts.yaml``
    failed to parse) -> 503 ``accounts_unreadable``;
  * ``account_id`` names no configured account -> 404 ``unknown_account_id``
    on the seven multi-account routes; the two single-account raw routes keep
    their existing explicit ``read_state: unknown_account``.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main

_TOKEN = "t" * 64

# route -> extra query params it requires
_ROUTES = {
    "/api/diag/exchange_positions": {},
    "/api/diag/venue_session": {},
    "/api/diag/ib_open_orders": {},
    "/api/diag/broker_account_status": {},
    "/api/diag/bybit_open_orders": {},
    "/api/diag/bybit_raw_order_history": {
        "account_id": "bybit_2", "symbol": "BTCUSDT",
        "start_ms": 1_700_000_000_000, "end_ms": 1_700_086_400_000},
    "/api/diag/bybit_raw_closed_pnl": {
        "account_id": "bybit_2", "symbol": "BTCUSDT",
        "start_ms": 1_700_000_000_000, "end_ms": 1_700_086_400_000},
    "/api/diag/bybit_raw_positions": {},
    "/api/diag/alpaca_open_orders": {},
}
_MULTI_ACCOUNT = [r for r, p in _ROUTES.items() if "account_id" not in p]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DIAG_READ_TOKEN", _TOKEN)
    monkeypatch.setenv("JWT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("ALLOWED_EMAIL", "test@example.com")
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", "deadbeef")
    return TestClient(api_main.app, raise_server_exceptions=False)


def _get(client, route, **params):
    return client.get(route, params=params,
                      headers={"Authorization": f"Bearer {_TOKEN}"})


@pytest.mark.parametrize("route", sorted(_ROUTES))
def test_list_accounts_raising_is_503_not_empty(client, monkeypatch, route):
    from src.units.ui import data_loaders

    def _boom():
        raise RuntimeError("accounts.yaml unreadable")

    monkeypatch.setattr(data_loaders, "list_accounts", _boom)
    r = _get(client, route, **_ROUTES[route])
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["error"] == "accounts_unreadable"


@pytest.mark.parametrize("route", sorted(_ROUTES))
def test_accounts_yaml_parse_failure_is_503(client, monkeypatch, tmp_path,
                                            route):
    """list_accounts() itself swallows a YAML parse failure to [] — the
    route must still tell that apart from 'no accounts configured'."""
    from src.units.ui import data_loaders

    bad = tmp_path / "accounts.yaml"
    bad.write_text("accounts: [unclosed\n")
    monkeypatch.setattr(data_loaders, "ACCOUNTS_YAML_PATH", bad)
    r = _get(client, route, **_ROUTES[route])
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["error"] == "accounts_unreadable"


@pytest.mark.parametrize("route", sorted(_MULTI_ACCOUNT))
def test_unknown_account_id_is_404(client, monkeypatch, route):
    from src.units.ui import data_loaders

    monkeypatch.setattr(data_loaders, "list_accounts",
                        lambda: [{"account_id": "bybit_2",
                                  "exchange": "bybit"}])
    r = _get(client, route, account_id="bybit_typo")
    assert r.status_code == 404, r.text
    body = r.json()["detail"]
    assert body["error"] == "unknown_account_id"
    assert body["account_id"] == "bybit_typo"
