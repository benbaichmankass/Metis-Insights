"""``account_bybit_raw_closed_pnl`` — the accessor behind /api/diag/bybit_raw_closed_pnl.

MI-221. The SECOND, INDEPENDENT venue endpoint. Every existing question about
whether a position closed goes through ``/v5/position/list``, and this repo has
been wrong about that endpoint's answer twice in two days
(``BL-20260908-...-ROWS0``, then MI-221). ``/v5/position/closed-pnl`` asks a
DIFFERENT question — *did the venue book a realised close?* — sharing no filter,
dedupe, cursor or ``size`` field with the position path.

WHY A RAW ONE. Its only existing caller, ``_bybit_closed_pnl_lookup``, is a
MATCHER: it scores records against a trade's side, qty, entry price and time,
returns the single best, and drops the rest into ``rej_*`` locals that never
leave the function. So *"the venue booked no close"*, *"it booked one we could
not match"* and *"we could not ask"* are ONE observation to every consumer. Every
test here asserts an ABSENCE of that reduction.
"""
from __future__ import annotations

from src.units.accounts import clients as accounts_clients


class _FakeBybit:
    def __init__(self, pages=None, raise_on_call=False):
        self._pages = pages if pages is not None else [[]]
        self._raise = raise_on_call
        self.calls: list = []

    def get_closed_pnl(self, category=None, symbol=None, startTime=None,
                       endTime=None, limit=None, cursor=None):
        self.calls.append({"category": category, "symbol": symbol,
                           "startTime": startTime, "endTime": endTime,
                           "limit": limit, "cursor": cursor})
        if self._raise:
            raise RuntimeError("venue refused")
        idx = 0 if not cursor else int(cursor.split("-")[-1])
        rows = self._pages[idx]
        nxt = f"cur-{idx + 1}" if idx + 1 < len(self._pages) else ""
        return {"result": {"list": rows, "nextPageCursor": nxt}}


def _rec(**kw):
    base = {"orderId": "o1", "side": "Sell", "qty": "0.04",
            "avgEntryPrice": "2453.97", "avgExitPrice": "2451.27",
            "closedPnl": "-0.2528", "execType": "Trade",
            "createdTime": "1788874633873", "updatedTime": "1788874633873"}
    base.update(kw)
    return base


def _acct(**kw):
    base = {"account_id": "bybit_2", "exchange": "bybit", "market_type": "linear"}
    base.update(kw)
    return base


def _install(monkeypatch, fake):
    monkeypatch.setattr(accounts_clients, "bybit_client_for", lambda a: fake)


def _call(**kw):
    args = {"symbol": "ETHUSDT", "start_ms": 1788800000000, "end_ms": 1788900000000}
    args.update(kw)
    return args


def test_every_record_is_returned_not_just_the_best_match(monkeypatch):
    """THE WHOLE POINT. `_bybit_closed_pnl_lookup` returns ONE record and drops
    the rest; a reader needs all of them to tell 'no close booked' from 'a close
    booked that our matcher rejected'."""
    fake = _FakeBybit(pages=[[_rec(orderId="a"), _rec(orderId="b", qty="0.01"),
                             _rec(orderId="c", side="Buy")]])
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_closed_pnl(_acct(), **_call())
    assert out is not None
    assert out["record_count"] == 3, "a matcher would have returned one"
    assert [r["order_id"] for r in out["records"]] == ["a", "b", "c"]
    assert out["query_state"] == "rows_returned"


def test_no_rows_and_could_not_look_are_different_states(monkeypatch):
    """An EMPTY answer is a positive measurement that the venue booked no close.
    A raised call says nothing about the world."""
    _install(monkeypatch, _FakeBybit(pages=[[]]))
    empty = accounts_clients.account_bybit_raw_closed_pnl(_acct(), **_call())
    assert empty["query_state"] == "no_rows" and empty["record_count"] == 0

    _install(monkeypatch, _FakeBybit(raise_on_call=True))
    failed = accounts_clients.account_bybit_raw_closed_pnl(_acct(), **_call())
    assert failed["query_state"] == "could_not_look"
    assert failed["record_count"] is None, "never 0 when we could not look"
    assert failed["error"] and failed["records"] == []


def test_cursor_is_followed_and_reported(monkeypatch):
    """Nothing in src/ followed nextPageCursor before MI-221, so a truncated
    page was indistinguishable from a complete one."""
    fake = _FakeBybit(pages=[[_rec(orderId="p1")], [_rec(orderId="p2")]])
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_closed_pnl(_acct(), **_call())
    assert out["pages_read"] == 2 and out["record_count"] == 2
    assert [r["order_id"] for r in out["records"]] == ["p1", "p2"]
    assert out["next_page_cursor_seen"], "the cursor must be reported, not just used"
    assert any(c["cursor"] for c in fake.calls), "a cursor must go back to the venue"


def test_truncation_is_reported_rather_than_read_as_complete(monkeypatch):
    fake = _FakeBybit(pages=[[_rec(orderId=f"r{i}")] for i in range(25)])
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_closed_pnl(_acct(), **_call())
    assert out["pages_truncated"] is True and out["pages_read"] == 10


def test_venue_strings_are_preserved_not_coerced(monkeypatch):
    """Coercing an unreadable value would manufacture a reading — the same class
    as `_f` turning an unparseable size into 0.0."""
    fake = _FakeBybit(pages=[[_rec(closedPnl="", qty="n/a")]])
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_closed_pnl(_acct(), **_call())
    r = out["records"][0]
    assert r["closed_pnl"] == "" and r["qty"] == "n/a"


def test_window_is_passed_through_to_the_venue(monkeypatch):
    fake = _FakeBybit(pages=[[]])
    _install(monkeypatch, fake)
    accounts_clients.account_bybit_raw_closed_pnl(
        _acct(), symbol="ETHUSDT", start_ms=111, end_ms=222)
    c = fake.calls[0]
    assert c["symbol"] == "ETHUSDT" and c["startTime"] == 111 and c["endTime"] == 222
    assert c["category"] == "linear"


def test_non_bybit_missing_client_and_spot_read_none(monkeypatch):
    """`None` means 'could not look' and must never present as an empty book."""
    assert accounts_clients.account_bybit_raw_closed_pnl(
        _acct(exchange="alpaca"), **_call()) is None
    _install(monkeypatch, _FakeBybit())
    assert accounts_clients.account_bybit_raw_closed_pnl(
        _acct(market_type="spot"), **_call()) is None
    monkeypatch.setattr(accounts_clients, "bybit_client_for", lambda a: None)
    assert accounts_clients.account_bybit_raw_closed_pnl(_acct(), **_call()) is None
