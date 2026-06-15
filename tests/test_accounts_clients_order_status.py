"""SSOT-from-Bybit reconciler (issue #502) — primitive
``src.units.accounts.clients.account_order_status``.

The reconciler asks Bybit "what is the status of THIS order id?" via
this helper, which wraps ``get_open_orders`` (live / partially filled)
and falls back to ``get_order_history`` (filled / cancelled). The five
return-contract cells are pinned here.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.units.accounts import clients
from src.units.accounts.clients import account_order_status


@pytest.fixture
def linear_account():
    """A linear-perp Bybit account dict — the SSOT helper passes
    ``category=linear`` to the lookup endpoints."""
    return {
        "account_id": "bybit_2",
        "exchange": "bybit",
        "api_key_env": "BYBIT_KEY_2",
        "market_type": "linear",
    }


def _stub_bybit_client(open_payload=None, history_payload=None):
    """Build a minimal pybit-shaped client whose ``get_open_orders`` /
    ``get_order_history`` return the supplied dicts (or ``None`` to
    raise on the call). Each payload follows the V5 envelope:
    ``{"result": {"list": [...]}}``.
    """
    client = MagicMock()
    if open_payload is None:
        client.get_open_orders.return_value = {"result": {"list": []}}
    else:
        client.get_open_orders.return_value = open_payload
    if history_payload is None:
        client.get_order_history.return_value = {"result": {"list": []}}
    else:
        client.get_order_history.return_value = history_payload
    return client


# ---------------------------------------------------------------------------
# 1. Open-orders hit (live order)
# ---------------------------------------------------------------------------


class TestOpenOrdersHit:
    def test_live_order_returned_from_open_orders_endpoint(
        self, linear_account, monkeypatch,
    ):
        order_id = "1900000000000000001"
        client = _stub_bybit_client(open_payload={
            "result": {"list": [{
                "orderId": order_id,
                "orderStatus": "New",
                "cumExecQty": "0",
                "avgPrice": "0",
                "updatedTime": "1762620000000",
            }]},
        })
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _acc: client,
        )
        result = account_order_status(linear_account, order_id)
        assert result is not None
        assert result["order_id"] == order_id
        assert result["status"] == "New"
        assert result["filled_qty"] == 0.0
        assert result["avg_price"] == 0.0
        assert result["exec_time"] == "1762620000000"
        # History endpoint must NOT be called — open-orders hit short-
        # circuits the lookup.
        client.get_order_history.assert_not_called()

    def test_partially_filled_in_open_orders(
        self, linear_account, monkeypatch,
    ):
        order_id = "1900000000000000002"
        client = _stub_bybit_client(open_payload={
            "result": {"list": [{
                "orderId": order_id,
                "orderStatus": "PartiallyFilled",
                "cumExecQty": "0.002",
                "avgPrice": "80050.0",
                "updatedTime": "1762620000500",
            }]},
        })
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _acc: client,
        )
        result = account_order_status(linear_account, order_id)
        assert result["status"] == "PartiallyFilled"
        assert abs(result["filled_qty"] - 0.002) < 1e-9
        assert abs(result["avg_price"] - 80050.0) < 1e-6


# ---------------------------------------------------------------------------
# 2. Order-history fallback (filled / cancelled)
# ---------------------------------------------------------------------------


class TestHistoryFallback:
    def test_filled_order_returned_from_history_endpoint(
        self, linear_account, monkeypatch,
    ):
        order_id = "1900000000000000003"
        client = _stub_bybit_client(
            open_payload={"result": {"list": []}},
            history_payload={"result": {"list": [{
                "orderId": order_id,
                "orderStatus": "Filled",
                "cumExecQty": "0.005",
                "avgPrice": "80123.45",
                "updatedTime": "1762620010000",
                "createdTime": "1762620005000",
            }]}},
        )
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _acc: client,
        )
        result = account_order_status(linear_account, order_id)
        assert result["status"] == "Filled"
        assert abs(result["filled_qty"] - 0.005) < 1e-9
        assert abs(result["avg_price"] - 80123.45) < 1e-6
        assert result["exec_time"] == "1762620010000"
        client.get_open_orders.assert_called_once()
        client.get_order_history.assert_called_once()

    def test_cancelled_order_returned_from_history_endpoint(
        self, linear_account, monkeypatch,
    ):
        order_id = "1900000000000000004"
        client = _stub_bybit_client(history_payload={
            "result": {"list": [{
                "orderId": order_id,
                "orderStatus": "Cancelled",
                "cumExecQty": "0",
                "avgPrice": "0",
                "updatedTime": "1762620020000",
            }]},
        })
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _acc: client,
        )
        result = account_order_status(linear_account, order_id)
        assert result["status"] == "Cancelled"
        assert result["filled_qty"] == 0.0


# ---------------------------------------------------------------------------
# 3. Not-found verdict (Bybit denies any record)
# ---------------------------------------------------------------------------


class TestNotFound:
    def test_both_endpoints_empty_returns_not_found(
        self, linear_account, monkeypatch,
    ):
        order_id = "1900000000000000099"
        client = _stub_bybit_client()  # both endpoints return empty lists
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _acc: client,
        )
        result = account_order_status(linear_account, order_id)
        assert result is not None  # NB: 'not_found' is a verdict, not a failure
        assert result["status"] == "not_found"
        assert result["order_id"] == order_id
        assert result["filled_qty"] == 0.0
        assert result["avg_price"] == 0.0
        assert result["exec_time"] is None

    def test_endpoints_return_other_orders_does_not_match(
        self, linear_account, monkeypatch,
    ):
        """The endpoints filter by orderId on Bybit's side, but the
        helper guards against a stale cache by also matching the
        returned record's ``orderId`` field. A response containing
        someone else's order must still resolve to ``not_found``.
        """
        order_id = "1900000000000000100"
        other = "9999999999999999999"
        client = _stub_bybit_client(
            open_payload={"result": {"list": [{"orderId": other,
                                                "orderStatus": "New",
                                                "cumExecQty": "0",
                                                "avgPrice": "0"}]}},
            history_payload={"result": {"list": [{"orderId": other,
                                                    "orderStatus": "Filled"}]}},
        )
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _acc: client,
        )
        result = account_order_status(linear_account, order_id)
        assert result["status"] == "not_found"


# ---------------------------------------------------------------------------
# 4. Read-failure paths → None (conservative: same semantic as
#    account_open_positions)
# ---------------------------------------------------------------------------


class TestReadFailure:
    def test_missing_creds_returns_none(self, linear_account, monkeypatch):
        # bybit_client_for returns None when creds are absent.
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _acc: None,
        )
        assert account_order_status(linear_account, "1900000000000000005") is None

    def test_exchange_sdk_exception_returns_none(
        self, linear_account, monkeypatch,
    ):
        client = MagicMock()
        client.get_open_orders.side_effect = RuntimeError("bybit blew up")
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _acc: client,
        )
        assert account_order_status(linear_account, "1900000000000000006") is None

    def test_history_endpoint_exception_returns_none(
        self, linear_account, monkeypatch,
    ):
        client = MagicMock()
        client.get_open_orders.return_value = {"result": {"list": []}}
        client.get_order_history.side_effect = ConnectionError("network")
        monkeypatch.setattr(
            clients, "bybit_client_for", lambda _acc: client,
        )
        assert account_order_status(linear_account, "1900000000000000007") is None

    def test_non_bybit_exchange_returns_none(self):
        """Non-bybit exchanges not yet wired through this primitive — the
        reconciler treats ``None`` as "skip the row this tick", which is
        the correct conservative semantic until the connector lookup
        lands."""
        binance_account = {
            "account_id": "binance_1",
            "exchange": "binance",
            "api_key_env": "BINANCE_KEY_1",
        }
        assert account_order_status(binance_account, "12345") is None

    def test_unknown_exchange_returns_none(self):
        assert (
            account_order_status({"exchange": "kraken"}, "12345") is None
        )

    def test_non_dict_account_returns_none(self):
        assert account_order_status("not-a-dict", "12345") is None  # type: ignore[arg-type]
        assert account_order_status(None, "12345") is None  # type: ignore[arg-type]

    def test_empty_order_id_returns_none(self, linear_account):
        assert account_order_status(linear_account, "") is None
        assert account_order_status(linear_account, None) is None  # type: ignore[arg-type]
