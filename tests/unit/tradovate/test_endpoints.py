"""Endpoint paths should be stable strings (single source of truth)."""
from src.units.accounts.tradovate.endpoints import REST, WS


def test_auth_paths():
    assert REST.auth_token == "/auth/accesstokenrequest"
    assert REST.auth_renew == "/auth/renewaccesstoken"


def test_order_paths_are_namespaced():
    for path in (REST.order_place, REST.order_cancel, REST.order_modify):
        assert path.startswith("/order/")


def test_ws_topics_use_slash_namespace():
    assert WS.md_subscribe_quote.startswith("md/")
    assert WS.authorize == "authorize"
