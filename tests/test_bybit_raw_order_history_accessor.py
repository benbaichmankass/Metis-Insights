"""``account_bybit_raw_order_history`` — the accessor behind /api/diag/bybit_raw_order_history.

MI-221. It answers the one question no surface could ask: **when was a
protective leg cancelled, and why?**

``trades.sl_order_id`` / ``tp_order_id`` record that a leg WAS placed at entry
(written only on the Partial branch, from a before/after snapshot diff of the
venue's legs). ``get_open_orders`` shows what rests NOW. Between those two there
was no way to ask what became of a leg that no longer rests — consumed at the
close (the position was protected throughout) or cancelled days earlier (it ran
naked). On a real-money position those are OPPOSITE findings and nothing could
tell them apart.

Its nearest existing caller, ``account_order_status``, NORMALISES the answer
away: ``{order_id, status, filled_qty, avg_price, exec_time}`` drops
``createdTime``, ``updatedTime``, ``cancelType``, ``triggerPrice`` and
``stopOrderType``. It is also per-orderId, so it cannot surface a leg the
journal never recorded.
"""
from __future__ import annotations

from src.units.accounts import clients as accounts_clients


class _FakeBybit:
    def __init__(self, pages=None, raise_on_call=False):
        self._pages = pages if pages is not None else [[]]
        self._raise = raise_on_call
        self.calls: list = []

    def get_order_history(self, category=None, symbol=None, startTime=None,
                          endTime=None, limit=None, cursor=None):
        self.calls.append({"category": category, "symbol": symbol,
                           "startTime": startTime, "endTime": endTime,
                           "cursor": cursor})
        if self._raise:
            raise RuntimeError("venue refused")
        idx = 0 if not cursor else int(cursor.split("-")[-1])
        rows = self._pages[idx]
        nxt = f"cur-{idx + 1}" if idx + 1 < len(self._pages) else ""
        return {"result": {"list": rows, "nextPageCursor": nxt}}


def _leg(**kw):
    base = {"orderId": "1a3490f9", "side": "Sell", "orderType": "Market",
            "stopOrderType": "PartialStopLoss", "orderStatus": "Cancelled",
            "cancelType": "CancelByTpSlTsClear", "reduceOnly": True,
            "tpslMode": "Partial", "positionIdx": 1, "qty": "0.04",
            "triggerPrice": "2451.59428571", "cumExecQty": "0",
            "createdTime": "1788530654000", "updatedTime": "1788874633873"}
    base.update(kw)
    return base


def _acct(**kw):
    base = {"account_id": "bybit_2", "exchange": "bybit", "market_type": "linear"}
    base.update(kw)
    return base


def _install(monkeypatch, fake):
    monkeypatch.setattr(accounts_clients, "bybit_client_for", lambda a: fake)


def _call(**kw):
    args = {"symbol": "ETHUSDT", "start_ms": 1788480000000, "end_ms": 1788970200000}
    args.update(kw)
    return args


def test_cancel_type_and_both_timestamps_survive(monkeypatch):
    """THE WHOLE POINT. `account_order_status` drops cancelType, createdTime and
    updatedTime — which are exactly the fields that separate 'protected until
    the close' from 'cancelled days early'."""
    _install(monkeypatch, _FakeBybit(pages=[[_leg()]]))
    out = accounts_clients.account_bybit_raw_order_history(_acct(), **_call())
    r = out["records"][0]
    assert r["cancel_type"] == "CancelByTpSlTsClear"
    assert r["created_time"] == "1788530654000"
    assert r["updated_time"] == "1788874633873"
    assert r["stop_order_type"] == "PartialStopLoss"
    assert r["trigger_price"] == "2451.59428571"
    assert r["position_idx"] == 1


def test_every_order_is_returned_including_entries(monkeypatch):
    """Not per-orderId: a leg the journal never recorded must still appear."""
    _install(monkeypatch, _FakeBybit(pages=[[
        _leg(orderId="entry", stopOrderType="", reduceOnly=False),
        _leg(orderId="1a3490f9"),
        _leg(orderId="unknown-to-the-journal"),
    ]]))
    out = accounts_clients.account_bybit_raw_order_history(_acct(), **_call())
    assert out["record_count"] == 3
    assert [r["order_id"] for r in out["records"]] == [
        "entry", "1a3490f9", "unknown-to-the-journal"]
    # An entry order carries no stopOrderType; it must not be coerced away.
    assert out["records"][0]["stop_order_type"] is None


def test_no_rows_and_could_not_look_are_different_states(monkeypatch):
    _install(monkeypatch, _FakeBybit(pages=[[]]))
    empty = accounts_clients.account_bybit_raw_order_history(_acct(), **_call())
    assert empty["query_state"] == "no_rows" and empty["record_count"] == 0

    _install(monkeypatch, _FakeBybit(raise_on_call=True))
    failed = accounts_clients.account_bybit_raw_order_history(_acct(), **_call())
    assert failed["query_state"] == "could_not_look"
    assert failed["record_count"] is None, "never 0 when we could not look"
    assert failed["error"] and failed["records"] == []


def test_cursor_is_followed_and_reported(monkeypatch):
    fake = _FakeBybit(pages=[[_leg(orderId="p1")], [_leg(orderId="p2")]])
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_order_history(_acct(), **_call())
    assert out["pages_read"] == 2 and out["record_count"] == 2
    assert out["next_page_cursor_seen"]
    assert any(c["cursor"] for c in fake.calls)


def test_truncation_is_reported(monkeypatch):
    _install(monkeypatch, _FakeBybit(pages=[[_leg()] for _ in range(25)]))
    out = accounts_clients.account_bybit_raw_order_history(_acct(), **_call())
    assert out["pages_truncated"] is True and out["pages_read"] == 10


def test_venue_strings_are_preserved_not_coerced(monkeypatch):
    _install(monkeypatch, _FakeBybit(pages=[[_leg(qty="", triggerPrice="n/a")]]))
    out = accounts_clients.account_bybit_raw_order_history(_acct(), **_call())
    r = out["records"][0]
    assert r["qty"] == "" and r["trigger_price"] == "n/a"


def test_window_and_symbol_reach_the_venue(monkeypatch):
    fake = _FakeBybit(pages=[[]])
    _install(monkeypatch, fake)
    accounts_clients.account_bybit_raw_order_history(
        _acct(), symbol="ETHUSDT", start_ms=111, end_ms=222)
    c = fake.calls[0]
    assert c["symbol"] == "ETHUSDT" and c["startTime"] == 111 and c["endTime"] == 222
    assert c["category"] == "linear"


def test_non_bybit_and_missing_client_read_none(monkeypatch):
    assert accounts_clients.account_bybit_raw_order_history(
        _acct(exchange="alpaca"), **_call()) is None
    monkeypatch.setattr(accounts_clients, "bybit_client_for", lambda a: None)
    assert accounts_clients.account_bybit_raw_order_history(_acct(), **_call()) is None
