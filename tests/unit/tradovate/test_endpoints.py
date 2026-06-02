"""Endpoint paths should be stable strings (single source of truth)."""
from src.units.accounts.tradovate.endpoints import REST, WS


def test_auth_paths():
    # partner.tradovate.com docs: /auth/accesstokenrequest (lowercase) and
    # /auth/renewAccessToken (camelCase operation name). Router is
    # case-insensitive but we match the documented spelling.
    assert REST.auth_token == "/auth/accesstokenrequest"
    assert REST.auth_renew == "/auth/renewAccessToken"


def test_order_paths_camelcase():
    # Docs use camelCase for the order operations. Tradovate's router
    # accepts either case but matching the docs reduces operator confusion.
    assert REST.order_place == "/order/placeOrder"
    assert REST.order_cancel == "/order/cancelOrder"
    assert REST.order_modify == "/order/modifyOrder"
    for path in (REST.order_place, REST.order_cancel, REST.order_modify):
        assert path.startswith("/order/")


def test_ws_topics_use_slash_namespace():
    assert WS.md_subscribe_quote.startswith("md/")
    assert WS.authorize == "authorize"
