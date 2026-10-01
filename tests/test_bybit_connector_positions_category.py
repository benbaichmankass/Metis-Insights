"""ORDER-AUDIT-2 item 6 (AUD-20260927-CA-A15-bybit-positions-spot-category).

``BybitConnector.get_positions`` hardcoded ``category: "spot"``, so on a
linear-perp account it read the wrong book and silently returned ``[]``; a
failed read also returned ``[]``. Both made "no positions" indistinguishable
from "we did not look".
"""
from __future__ import annotations

import pytest

from src.exchange.bybit_connector import BybitConnector


class _FakeExchange:
    def __init__(self, rows=None, raises=False):
        self.calls = []
        self.rows = rows or []
        self.raises = raises

    def fetch_positions(self, symbols=None, params=None):
        self.calls.append(params)
        if self.raises:
            raise RuntimeError("bybit down")
        return self.rows


def _connector(market_type, exchange):
    c = BybitConnector.__new__(BybitConnector)
    c.testnet = False
    c.market_type = market_type
    c.exchange = exchange
    return c


def test_linear_account_reads_linear_category():
    ex = _FakeExchange(rows=[{"contracts": 1}, {"contracts": 0}])
    out = _connector("linear", ex).get_positions()
    assert ex.calls == [{"category": "linear"}]
    assert out == [{"contracts": 1}]


def test_default_market_type_is_linear():
    c = BybitConnector(api_key=None, api_secret=None, testnet=False)
    assert c.market_type == "linear"


def test_spot_account_has_no_positions_and_makes_no_call():
    ex = _FakeExchange(rows=[{"contracts": 1}])
    assert _connector("spot", ex).get_positions() == []
    assert ex.calls == []


def test_failed_read_raises_instead_of_reading_as_empty():
    with pytest.raises(RuntimeError):
        _connector("linear", _FakeExchange(raises=True)).get_positions()
