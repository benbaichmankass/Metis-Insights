"""``account_bybit_open_orders`` — the accessor behind /api/diag/bybit_open_orders.

Split from the route tests deliberately: those need FastAPI's TestClient, which
does not import in every environment (the pre-existing
``tests/test_diag_ib_open_orders.py`` has the identical constraint), whereas the
properties that actually decide whether a protection read is TRUSTWORTHY live
here and must be falsifiable anywhere.

BL-20260820-PROTECTION-COVERAGE-IS-PRICE-BLIND criterion 5. Three properties:

1. **An unset price is ``None``, never ``0.0``.** Bybit reports no-stop as
   ``""`` or ``"0"``. Coerced to a float it would publish a stop AT ZERO, which
   a price-axis consumer grades as a catastrophic divergence from the declared
   level when the truth is that no stop is set at all.
2. **BOTH collections are read.** Full mode puts the stop on the POSITION row
   with no resting order; Partial mode puts it in a conditional leg. Reading one
   reports a protected position as naked, or the reverse.
3. **``None`` is not an empty book.** A read failure must never present as a
   confirmed-flat account.
"""
from __future__ import annotations

import pytest

from src.units.accounts import clients as accounts_clients

class _FakeBybit:
    """Minimal pybit stand-in recording which filters were asked for."""

    def __init__(self, positions=None, orders_by_filter=None, by_symbol=None):
        self._positions = positions or []
        self._orders = orders_by_filter or {}
        self._by_symbol = by_symbol or {}
        self.filters_seen: list = []
        self.symbol_reads: list = []

    def get_positions(self, category=None, settleCoin=None, symbol=None):
        if symbol is not None:
            self.symbol_reads.append(symbol)
            return {"result": {"list": self._by_symbol.get(symbol, [])}}
        return {"result": {"list": self._positions}}

    def get_open_orders(self, category=None, settleCoin=None, orderFilter=None):
        self.filters_seen.append(orderFilter)
        return {"result": {"list": self._orders.get(orderFilter, [])}}


def _acct(**kw):
    base = {"account_id": "bybit_2", "exchange": "bybit", "mode": "live",
            "market_type": "linear"}
    base.update(kw)
    return base


def _patch_client(monkeypatch, fake):
    monkeypatch.setattr(accounts_clients, "bybit_client_for", lambda acc: fake)


def test_accessor_rejects_non_bybit(monkeypatch):
    assert accounts_clients.account_bybit_open_orders(
        {"account_id": "ib_paper", "exchange": "interactive_brokers"}) is None
    assert accounts_clients.account_bybit_open_orders(None) is None


def test_accessor_none_when_creds_missing(monkeypatch):
    monkeypatch.setattr(accounts_clients, "bybit_client_for", lambda acc: None)
    assert accounts_clients.account_bybit_open_orders(_acct()) is None


def test_spot_account_is_could_not_look(monkeypatch):
    """Cash spot carries no derivative position, so there is no protection
    question -- and reporting an empty book would assert one was answered."""
    _patch_client(monkeypatch, _FakeBybit())
    assert accounts_clients.account_bybit_open_orders(
        _acct(market_type="spot")) is None


def test_sdk_failure_is_none_not_an_empty_book(monkeypatch):
    class _Boom:
        def get_positions(self, **kw):
            raise RuntimeError("venue down")

    _patch_client(monkeypatch, _Boom())
    assert accounts_clients.account_bybit_open_orders(_acct()) is None


@pytest.mark.parametrize("raw", ["", "0", "0.0", 0, None, "  "])
def test_unset_price_is_none_never_zero(monkeypatch, raw):
    """THE property. Bybit reports no-stop as "" or "0". Coerced to 0.0 it
    would publish a stop AT ZERO, which a price-axis consumer would grade as a
    catastrophic divergence from the declared level rather than as absent."""
    fake = _FakeBybit(positions=[
        {"symbol": "XRPUSDT", "side": "Buy", "size": "21.3", "avgPrice": "1.4983",
         "stopLoss": raw, "takeProfit": raw, "tpSlMode": "Full"}])
    _patch_client(monkeypatch, fake)
    out = accounts_clients.account_bybit_open_orders(_acct())
    assert out["positions"][0]["stop_loss"] is None
    assert out["positions"][0]["take_profit"] is None


def test_full_mode_position_stop_is_captured(monkeypatch):
    """Full mode has NO resting order -- the stop is on the position row. A
    surface that read only `orders` would report this position naked."""
    fake = _FakeBybit(positions=[
        {"symbol": "XRPUSDT", "side": "Buy", "size": "21.3", "avgPrice": "1.4983",
         "stopLoss": "1.41", "takeProfit": "1.72", "tpSlMode": "Full"}])
    _patch_client(monkeypatch, fake)
    out = accounts_clients.account_bybit_open_orders(_acct())
    assert out["orders"] == [], "the fixture has no resting legs, by design"
    assert out["positions"][0]["stop_loss"] == 1.41
    assert out["positions"][0]["take_profit"] == 1.72


def test_both_order_filters_are_read(monkeypatch):
    """A resting limit take-profit is an `Order`, invisible to `StopOrder`.
    Reading one filter under-reports target protection."""
    fake = _FakeBybit(
        positions=[],
        orders_by_filter={
            "StopOrder": [{"symbol": "S", "orderId": "1", "triggerPrice": "1.4",
                           "stopOrderType": "StopLoss"}],
            "Order": [{"symbol": "S", "orderId": "2", "price": "1.9",
                       "orderType": "Limit"}]})
    _patch_client(monkeypatch, fake)
    out = accounts_clients.account_bybit_open_orders(_acct())
    assert sorted(fake.filters_seen) == ["Order", "StopOrder"]
    assert {o["order_id"] for o in out["orders"]} == {"1", "2"}
    assert {o["order_filter"] for o in out["orders"]} == {"StopOrder", "Order"}
    stop = next(o for o in out["orders"] if o["order_id"] == "1")
    assert stop["trigger_price"] == 1.4


def test_one_failing_filter_does_not_lose_the_other(monkeypatch):
    class _HalfBroken(_FakeBybit):
        def get_open_orders(self, category=None, settleCoin=None, orderFilter=None):
            if orderFilter == "Order":
                raise RuntimeError("filter down")
            return {"result": {"list": [{"symbol": "S", "orderId": "1",
                                         "triggerPrice": "1.4"}]}}

    _patch_client(monkeypatch, _HalfBroken())
    out = accounts_clients.account_bybit_open_orders(_acct())
    assert [o["order_id"] for o in out["orders"]] == ["1"]


def test_settlecoin_blind_symbol_is_cross_checked(monkeypatch):
    """BL-20260713-BYBIT2-BTC-SETTLECOIN-BLIND: a settleCoin page can OMIT a
    live symbol. Inheriting that blindness would report a position as absent
    rather than unprotected -- the quieter failure, so the worse one."""
    fake = _FakeBybit(
        positions=[{"symbol": "XRPUSDT", "side": "Buy", "size": "21.3",
                    "avgPrice": "1.5", "stopLoss": "1.4"}],
        by_symbol={"BTCUSDT": [{"symbol": "BTCUSDT", "side": "Buy",
                                "size": "0.001", "avgPrice": "80000",
                                "stopLoss": ""}]})
    _patch_client(monkeypatch, fake)
    out = accounts_clients.account_bybit_open_orders(
        _acct(symbols=["XRPUSDT", "BTCUSDT"]))
    syms = {p["symbol"] for p in out["positions"]}
    assert syms == {"XRPUSDT", "BTCUSDT"}
    btc = next(p for p in out["positions"] if p["symbol"] == "BTCUSDT")
    assert btc["settlecoin_blind"] is True
    assert btc["stop_loss"] is None, "unprotected, and reported as such"
    # The symbol already on the page is not re-read.
    assert fake.symbol_reads == ["BTCUSDT"]


def test_zero_size_positions_are_dropped(monkeypatch):
    fake = _FakeBybit(positions=[
        {"symbol": "A", "size": "0", "avgPrice": "1", "stopLoss": "1"},
        {"symbol": "B", "size": "5", "avgPrice": "1", "stopLoss": "1"}])
    _patch_client(monkeypatch, fake)
    out = accounts_clients.account_bybit_open_orders(_acct())
    assert [p["symbol"] for p in out["positions"]] == ["B"]


# ---------------------------------------------------------------------------
# The ORDERS half inherited the settleCoin blindness the POSITIONS half was
# fixed for (2026-08-26). Measured on bybit_1/ETHUSDT: the settleCoin order
# page returned 7 SL legs where the trader's own symbol-scoped read saw 9 — and
# a missing leg reads as MISSING PROTECTION, which is the worse direction.
# ---------------------------------------------------------------------------
class _BlindOrderPage:
    """settleCoin returns a partial order list; the symbol-scoped read is whole."""

    def __init__(self, page_orders, symbol_orders, *, symbol_raises=False):
        self._page = page_orders
        self._symbol = symbol_orders
        self._symbol_raises = symbol_raises
        self.symbol_order_reads: list = []

    def get_positions(self, category=None, settleCoin=None, symbol=None):
        if symbol is not None:
            return {"result": {"list": []}}
        return {"result": {"list": [
            {"symbol": "ETHUSDT", "side": "Buy", "size": "5.59",
             "avgPrice": "4000", "stopLoss": "", "takeProfit": "",
             "tpSlMode": "Partial"}]}}

    def get_open_orders(self, category=None, settleCoin=None, symbol=None,
                        orderFilter=None):
        if symbol is not None:
            self.symbol_order_reads.append((symbol, orderFilter))
            if self._symbol_raises:
                raise RuntimeError("symbol-scoped read failed")
            return {"result": {"list": self._symbol.get(orderFilter, [])}}
        return {"result": {"list": self._page.get(orderFilter, [])}}


def _leg(oid, qty):
    return {"symbol": "ETHUSDT", "orderId": oid, "qty": str(qty),
            "stopOrderType": "StopLoss", "triggerPrice": "3900",
            "orderStatus": "Untriggered"}


def test_symbol_scoped_reread_finds_legs_the_settlecoin_page_omitted(monkeypatch):
    fake = _BlindOrderPage(
        page_orders={"StopOrder": [_leg("a", 0.19)], "Order": []},
        # The venue actually holds three; the page showed one.
        symbol_orders={"StopOrder": [_leg("a", 0.19), _leg("b", 1.18),
                                     _leg("c", 4.41)], "Order": []})
    _patch_client(monkeypatch, fake)
    out = accounts_clients.account_bybit_open_orders(_acct())
    ids = [o["order_id"] for o in out["orders"]]
    assert sorted(ids) == ["a", "b", "c"], ids          # the two hidden legs surfaced
    assert len(ids) == len(set(ids))                    # and NOT double-counted
    blind = {o["order_id"] for o in out["orders"] if o.get("settlecoin_blind")}
    assert blind == {"b", "c"}                          # only the re-read ones marked
    assert out["order_symbols_unchecked"] == []
    assert out["order_symbols_cross_checked"] == ["ETHUSDT"]


def test_a_failed_cross_check_is_recorded_not_silent(monkeypatch):
    """A short `orders` list must never be mistaken for a complete one."""
    fake = _BlindOrderPage(
        page_orders={"StopOrder": [_leg("a", 0.19)], "Order": []},
        symbol_orders={}, symbol_raises=True)
    _patch_client(monkeypatch, fake)
    out = accounts_clients.account_bybit_open_orders(_acct())
    assert out is not None                              # still a usable read
    assert [o["order_id"] for o in out["orders"]] == ["a"]
    assert out["order_symbols_unchecked"] == [
        "ETHUSDT/StopOrder", "ETHUSDT/Order"]


def test_a_flat_account_spends_no_cross_check_calls(monkeypatch):
    """The denominator is position-bearing symbols: protection is a property of
    a position, so a flat book must not cost a broker call per instrument."""
    fake = _FakeBybit(positions=[], orders_by_filter={"StopOrder": [], "Order": []})
    _patch_client(monkeypatch, fake)
    out = accounts_clients.account_bybit_open_orders(
        _acct(symbols=["ETHUSDT", "BTCUSDT", "SOLUSDT"]))
    assert out["order_symbols_cross_checked"] == []
    assert out["order_symbols_unchecked"] == []
    assert out["orders"] == []
