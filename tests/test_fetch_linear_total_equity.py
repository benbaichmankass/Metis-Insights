"""S-052 wiring — `_fetch_linear_total_equity` parses Bybit UNIFIED total equity.

The coordinator now feeds `total_account_usd` into `RiskManager.position_size`
for `market_type: linear` accounts (it was hardwired `None`, so the documented
S-052 fix never ran). The fetch is best-effort: any malformed / empty / errored
response returns `None`, leaving the sizer on the pre-S-052 free-balance basis
(no regression). These tests pin that contract — mirroring
`test_m15_balance_wiring.py`'s stub-client pattern.
"""
from __future__ import annotations

import pytest

from src.units.accounts.execute import (
    _fetch_linear_available_balance,
    _fetch_linear_total_equity,
)


class _StubBybitClient:
    """Stand-in for the BybitAPI wrapper: get_wallet_balance(...) -> dict."""

    def __init__(self, resp):
        self._resp = resp

    def get_wallet_balance(self, accountType=None):  # noqa: N803 (Bybit kwarg)
        return self._resp


def _resp(account_fields=None, coins=None):
    """Build a Bybit V5 get_wallet_balance UNIFIED response shape."""
    entry = dict(account_fields or {})
    if coins is not None:
        entry["coin"] = coins
    return {"result": {"list": [entry]}}


# ---------------------------------------------------------------------------
# Good-response parsing
# ---------------------------------------------------------------------------


def test_total_equity_prefers_total_equity_field():
    client = _StubBybitClient(_resp({"totalEquity": "1234.56",
                                      "totalWalletBalance": "1000.00"}))
    assert _fetch_linear_total_equity(client) == pytest.approx(1234.56)


def test_total_equity_falls_back_to_total_wallet_balance():
    # totalEquity absent → totalWalletBalance is used.
    client = _StubBybitClient(_resp({"totalWalletBalance": "987.65"}))
    assert _fetch_linear_total_equity(client) == pytest.approx(987.65)


def test_total_equity_floors_at_zero():
    client = _StubBybitClient(_resp({"totalEquity": "-5.0"}))
    assert _fetch_linear_total_equity(client) == 0.0


# ---------------------------------------------------------------------------
# Best-effort fallback to None (no regression — caller uses free balance)
# ---------------------------------------------------------------------------


def test_total_equity_none_on_missing_fields():
    # Neither totalEquity nor totalWalletBalance present.
    client = _StubBybitClient(_resp({"accountType": "UNIFIED"}))
    assert _fetch_linear_total_equity(client) is None


def test_total_equity_none_on_empty_response():
    assert _fetch_linear_total_equity(_StubBybitClient({})) is None


def test_total_equity_none_on_empty_list():
    assert _fetch_linear_total_equity(
        _StubBybitClient({"result": {"list": []}})) is None


def test_total_equity_none_on_null_string():
    client = _StubBybitClient(_resp({"totalEquity": "null"}))
    assert _fetch_linear_total_equity(client) is None


def test_total_equity_none_on_client_raise():
    class _Raises:
        def get_wallet_balance(self, accountType=None):  # noqa: N803
            raise RuntimeError("network down")

    assert _fetch_linear_total_equity(_Raises()) is None


def test_total_equity_none_on_non_numeric():
    client = _StubBybitClient(_resp({"totalEquity": "not-a-number"}))
    assert _fetch_linear_total_equity(client) is None


# ---------------------------------------------------------------------------
# Sibling sanity: the available-balance helper still parses the coin list
# (confirms the two helpers read the same response shape at different levels).
# ---------------------------------------------------------------------------


def test_available_and_total_read_same_response():
    # Legacy fallback shape (no account-level totalAvailableBalance) →
    # _fetch_linear_available_balance still parses the per-coin field.
    resp = _resp(
        {"totalEquity": "2000.00"},
        coins=[{"coin": "USDT", "availableToWithdraw": "1500.00"}],
    )
    client = _StubBybitClient(resp)
    assert _fetch_linear_available_balance(client) == pytest.approx(1500.00)
    assert _fetch_linear_total_equity(client) == pytest.approx(2000.00)


# ---------------------------------------------------------------------------
# BL-20260701-BYBIT-AVAILABLE-FIELD — the available-margin helper now PREFERS
# the account-level ``totalAvailableBalance`` (trading-available margin), not
# the per-coin ``availableToWithdraw`` (deprecated for UTA 2025-01-09 and a
# withdrawal-eligibility figure, not a new-order-margin figure).
# ---------------------------------------------------------------------------


def test_available_prefers_total_available_balance():
    # Both present + DIFFERENT → the account-level field wins (the per-coin
    # withdraw value must be ignored).
    resp = _resp(
        {"totalAvailableBalance": "900.00", "totalEquity": "2000.00"},
        coins=[{"coin": "USDT", "availableToWithdraw": "1500.00"}],
    )
    assert _fetch_linear_available_balance(_StubBybitClient(resp)) == pytest.approx(900.00)


def test_available_falls_back_to_coin_withdraw_when_account_field_absent():
    # Account-level field missing → legacy per-coin availableToWithdraw.
    resp = _resp({"totalEquity": "2000.00"},
                 coins=[{"coin": "USDT", "availableToWithdraw": "1500.00"}])
    assert _fetch_linear_available_balance(_StubBybitClient(resp)) == pytest.approx(1500.00)


def test_available_floors_at_zero():
    resp = _resp({"totalAvailableBalance": "-3.0"})
    assert _fetch_linear_available_balance(_StubBybitClient(resp)) == 0.0


def test_available_none_when_neither_field_present():
    resp = _resp({"totalEquity": "2000.00"}, coins=[{"coin": "BTC"}])
    assert _fetch_linear_available_balance(_StubBybitClient(resp)) is None
