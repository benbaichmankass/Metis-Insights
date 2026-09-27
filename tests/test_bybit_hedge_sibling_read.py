"""OI-20260913-A-BYBIT2-HEDGE-BOOK-IS-NEVER-FETCHED — a hedge symbol's SECOND
book is read, or its absence is counted.

WHAT WAS WRONG. bybit_2's ``settleCoin=USDT`` page returns ONE row per symbol
(measured 148 of 148 reads, 2026-09-13: ETHUSDT listed at positionIdx 1 only,
XRPUSDT at 2 only), and the roster cross-check skips every symbol the page
surfaced (``sym in seen``). So the other hedge book of a listed symbol was
never asked for — which is how a real-money ETHUSDT short sat naked and
invisible to every bot surface — and ``could_not_look_count`` read 0, making the
miss byte-identical to an empty book.

⚠️ A GREEN SUITE CLEARS NOTHING ON THE ITEM. A fixture cannot reproduce a venue
page returning fewer rows than the account holds; these fakes ENCODE that shape
(page lists one book, symbol-scoped read returns both) and pin the DECISION
made about it. Clause (1) of the item needs a live bybit_2 read compared with a
same-minute venue read.

Fakes answer per call shape — ``settleCoin`` → the page, ``symbol=X`` → that
symbol's books — so a test cannot pass because a fake returned every row to
every query.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.units.accounts.clients import (
    account_bybit_open_orders,
    account_bybit_symbol_books,
    account_open_positions,
)


def _acct(**kw):
    base = {
        "account_id": "bybit_not_in_accounts_yaml",
        "exchange": "bybit",
        "api_key_env": "BYBIT_KEY_2",
        "market_type": "linear",
        "symbols": [],
    }
    base.update(kw)
    return base


ETH_LONG = {"symbol": "ETHUSDT", "side": "Buy", "size": "0.03",
            "avgPrice": "2450.0", "unrealisedPnl": "0.5", "positionIdx": 1,
            "stopLoss": "2300", "takeProfit": "2600"}
ETH_SHORT = {"symbol": "ETHUSDT", "side": "Sell", "size": "0.05",
             "avgPrice": "2500.0", "unrealisedPnl": "-1.0", "positionIdx": 2,
             "stopLoss": "", "takeProfit": "0"}
ETH_SHORT_FLAT = dict(ETH_SHORT, size="0")
BTC_ONEWAY = {"symbol": "BTCUSDT", "side": "Buy", "size": "0.001",
              "avgPrice": "78000", "unrealisedPnl": "0", "positionIdx": 0}


class _Venue:
    """Per-query fake: the page and each symbol answer independently."""

    def __init__(self, page, by_symbol, raise_for=()):
        self.page = page
        self.by_symbol = by_symbol
        self.raise_for = set(raise_for)
        self.calls = []

    def get_positions(self, **kw):
        self.calls.append(kw)
        sym = kw.get("symbol")
        if sym is None:
            return {"result": {"list": list(self.page)}}
        if sym in self.raise_for:
            raise RuntimeError(f"venue timeout for {sym}")
        return {"result": {"list": list(self.by_symbol.get(sym, []))}}

    def get_open_orders(self, **kw):
        self.calls.append(kw)
        return {"result": {"list": []}}

    def scoped(self):
        return [c.get("symbol") for c in self.calls
                if c.get("symbol") and "orderFilter" not in c]


def _read(account, venue, tmp_path):
    with patch("src.units.accounts.clients.bybit_client_for",
               return_value=venue), \
         patch("src.utils.paths.runtime_logs_dir", return_value=tmp_path):
        out = account_open_positions(account)
    soak = tmp_path / "position_read_state_soak.jsonl"
    lines = ([json.loads(x) for x in soak.read_text().splitlines() if x]
             if soak.exists() else [])
    return out, lines


class TestSecondBookIsFetched:
    def test_page_lists_one_hedge_book_and_the_sibling_is_read(self, tmp_path):
        """THE FIX. Page shows ETH long (idx 1) only; the venue holds a short
        on idx 2 too. Before: one row returned, the short invisible."""
        venue = _Venue([ETH_LONG], {"ETHUSDT": [ETH_LONG, ETH_SHORT]})
        out, soak = _read(_acct(), venue, tmp_path)
        assert sorted((r["position_idx"], r["side"], r["size"]) for r in out) == [
            (1, "Buy", 0.03), (2, "Sell", 0.05)]
        assert venue.scoped() == ["ETHUSDT"]
        (line,) = soak
        # The re-read of the book the page already listed is NOT a venue
        # duplicate and must not page as one.
        assert line["dropped_symbol_dedupe_count"] == 0
        assert line["could_not_look_count"] == 0
        (q,) = [q for q in line["queries"] if q["scope"] == "hedge_sibling"]
        assert q["symbol"] == "ETHUSDT"
        assert q["books_missing_from_page"] == [2]
        assert q["books_returned"] == [1, 2]

    def test_sibling_is_flat_so_nothing_is_added(self, tmp_path):
        venue = _Venue([ETH_LONG], {"ETHUSDT": [ETH_LONG, ETH_SHORT_FLAT]})
        out, soak = _read(_acct(), venue, tmp_path)
        assert [(r["position_idx"], r["size"]) for r in out] == [(1, 0.03)]
        assert soak[0]["dropped_zero_size_count"] == 1
        assert soak[0]["could_not_look_count"] == 0

    def test_page_row_zero_size_still_triggers_the_sibling_read(self, tmp_path):
        """A zero-size listed book is still a LISTED book; its sibling may be
        the live one."""
        venue = _Venue([dict(ETH_LONG, size="0")],
                       {"ETHUSDT": [dict(ETH_LONG, size="0"), ETH_SHORT]})
        out, _ = _read(_acct(), venue, tmp_path)
        assert [(r["position_idx"], r["side"]) for r in out] == [(2, "Sell")]


class TestCouldNotLookIsExpressible:
    def test_a_failed_sibling_read_is_counted_not_silent(self, tmp_path):
        """Clause (2): a read that did not fetch the second book increments
        could_not_look_count instead of silently reading 0."""
        venue = _Venue([ETH_LONG], {}, raise_for={"ETHUSDT"})
        out, soak = _read(_acct(), venue, tmp_path)
        # The page's book still stands -- the read is not nuked to None.
        assert [(r["position_idx"], r["size"]) for r in out] == [(1, 0.03)]
        (line,) = soak
        assert line["could_not_look_count"] == 1
        (q,) = [q for q in line["queries"] if q["scope"] == "hedge_sibling"]
        assert q["query_state"] == "could_not_look"
        assert q["books_missing_from_page"] == [2]
        assert "venue timeout" in q["error"]

    def test_positive_control_same_read_succeeding_counts_zero(self, tmp_path):
        """So the 1 above has a denominator: the identical shape, read OK."""
        venue = _Venue([ETH_LONG], {"ETHUSDT": [ETH_LONG]})
        _, soak = _read(_acct(), venue, tmp_path)
        assert soak[0]["could_not_look_count"] == 0


class TestNoExtraCallsWhereNoneAreNeeded:
    def test_one_way_symbol_costs_no_extra_call(self, tmp_path):
        venue = _Venue([BTC_ONEWAY], {})
        out, _ = _read(_acct(), venue, tmp_path)
        assert len(out) == 1
        assert venue.scoped() == []

    def test_both_books_on_the_page_costs_no_extra_call(self, tmp_path):
        venue = _Venue([ETH_LONG, ETH_SHORT], {})
        out, _ = _read(_acct(), venue, tmp_path)
        assert len(out) == 2
        assert venue.scoped() == []

    def test_symbol_already_read_by_roster_crosscheck_is_not_read_twice(
        self, tmp_path,
    ):
        # Page row zero-size -> not in `seen` -> roster cross-check reads it;
        # the sibling pass must not repeat that call.
        venue = _Venue([dict(ETH_LONG, size="0")],
                       {"ETHUSDT": [dict(ETH_LONG, size="0"), ETH_SHORT]})
        out, _ = _read(_acct(symbols=["ETHUSDT"]), venue, tmp_path)
        assert venue.scoped() == ["ETHUSDT"]
        assert [(r["position_idx"], r["side"]) for r in out] == [(2, "Sell")]


class TestSymbolBooksRead:
    def _call(self, venue, symbol="ethusdt", account=None):
        with patch("src.units.accounts.clients.bybit_client_for",
                   return_value=venue):
            return account_bybit_symbol_books(account or _acct(), symbol)

    def test_reads_both_hedge_books_and_says_so(self):
        res = self._call(_Venue([], {"ETHUSDT": [ETH_LONG, ETH_SHORT]}))
        assert res["symbol"] == "ETHUSDT"
        assert res["query_state"] == "rows_returned"
        assert res["books_read"] == [1, 2]
        assert res["hedge_books_missing"] == []
        assert res["nonzero_books"] == [1, 2]
        short = next(r for r in res["rows"] if r["position_idx"] == 2)
        # "" / "0" are the venue saying NO stop -- never a stop at zero.
        assert short["stop_loss"] is None and short["take_profit"] is None
        assert (short["side"], short["size"], short["avg_price"]) == (
            "Sell", 0.05, 2500.0)
        long_ = next(r for r in res["rows"] if r["position_idx"] == 1)
        assert (long_["stop_loss"], long_["take_profit"]) == (2300.0, 2600.0)

    def test_names_a_hedge_book_the_venue_did_not_return(self):
        res = self._call(_Venue([], {"ETHUSDT": [ETH_LONG]}))
        assert res["hedge_books_missing"] == [2]

    def test_one_way_symbol_reports_no_hedge_gap(self):
        res = self._call(_Venue([], {"BTCUSDT": [BTC_ONEWAY]}), "BTCUSDT")
        assert res["books_read"] == [0]
        assert res["hedge_books_missing"] is None

    def test_raise_is_could_not_look_not_empty(self):
        res = self._call(_Venue([], {}, raise_for={"ETHUSDT"}))
        assert res["query_state"] == "could_not_look"
        assert res["rows"] is None

    def test_empty_answer_is_no_rows_not_could_not_look(self):
        res = self._call(_Venue([], {}))
        assert res["query_state"] == "no_rows"
        assert res["rows"] == []

    def test_not_bybit_is_none(self):
        assert account_bybit_symbol_books(
            {"account_id": "x", "exchange": "alpaca"}, "ETHUSDT") is None

    def test_places_no_order(self):
        venue = _Venue([], {"ETHUSDT": [ETH_LONG, ETH_SHORT]})
        self._call(venue)
        assert all(set(c) <= {"category", "symbol"} for c in venue.calls)


class TestOpenOrdersSurfaceAlsoReadsTheSibling:
    def _call(self, venue, **kw):
        with patch("src.units.accounts.clients.bybit_client_for",
                   return_value=venue):
            return account_bybit_open_orders(_acct(**kw))

    def test_sibling_book_appears(self):
        res = self._call(_Venue([ETH_LONG], {"ETHUSDT": [ETH_LONG, ETH_SHORT]}))
        assert sorted(p["position_idx"] for p in res["positions"]) == [1, 2]
        assert res["hedge_sibling_read"] == ["ETHUSDT"]
        assert res["hedge_sibling_could_not_look"] == []

    def test_failed_sibling_is_named(self):
        res = self._call(_Venue([ETH_LONG], {}, raise_for={"ETHUSDT"}))
        assert [p["position_idx"] for p in res["positions"]] == [1]
        assert res["hedge_sibling_could_not_look"] == ["ETHUSDT"]


# ---------------------------------------------------------------------------
# /api/diag/exchange_positions?symbol=
# ---------------------------------------------------------------------------

_TOKEN = "t" * 64


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("DIAG_READ_TOKEN", _TOKEN)
    monkeypatch.setenv("JWT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("ALLOWED_EMAIL", "test@example.com")
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", "deadbeef")
    import src.units.ui.data_loaders as dl
    from src.web.api import main as api_main

    accounts = [
        _acct(account_id="bybit_2"),
        {"account_id": "alpaca_live", "exchange": "alpaca"},
    ]
    monkeypatch.setattr(dl, "list_accounts", lambda: accounts)
    monkeypatch.setattr(dl, "account_open_positions", lambda acc: (
        [{"symbol": "ETHUSDT", "side": "Buy", "size": 0.03, "position_idx": 1},
         {"symbol": "XRPUSDT", "side": "Sell", "size": 55.6, "position_idx": 2}]
        if acc["account_id"] == "bybit_2"
        else [{"symbol": "SPY", "side": "long", "size": 1.0}]))
    venue = _Venue([], {"ETHUSDT": [ETH_LONG, ETH_SHORT]})
    monkeypatch.setattr("src.units.accounts.clients.bybit_client_for",
                        lambda acc: venue)
    return TestClient(api_main.app, raise_server_exceptions=False)


def _get(api, qs):
    r = api.get(f"/api/diag/exchange_positions{qs}",
                headers={"Authorization": f"Bearer {_TOKEN}"})
    assert r.status_code == 200
    return r.json()


def test_symbol_parameter_actually_changes_the_payload(api):
    """NEGATIVE CONTROL for the silent-ignore failure: before 2026-09-27 an
    appended ``&symbol=ETHUSDT`` returned a byte-identical payload."""
    plain = _get(api, "?account_id=bybit_2")
    scoped = _get(api, "?account_id=bybit_2&symbol=ETHUSDT")
    plain.pop("captured_at"), scoped.pop("captured_at")
    assert plain != scoped
    assert plain["requested_symbol"] is None
    assert scoped["requested_symbol"] == "ETHUSDT"
    assert "symbol_books" not in plain["accounts"][0]
    (row,) = scoped["accounts"]
    assert row["symbol_read"] == "rows_returned"
    assert row["symbol_books"]["books_read"] == [1, 2]
    # The account read is filtered to the symbol, so it can be compared with
    # the venue's symbol-scoped answer in one response.
    assert [p["symbol"] for p in row["positions"]] == ["ETHUSDT"]


def test_symbol_read_exposes_the_book_the_account_read_missed(api):
    (row,) = _get(api, "?account_id=bybit_2&symbol=ethusdt")["accounts"]
    account_books = {p["position_idx"] for p in row["positions"]}
    venue_books = set(row["symbol_books"]["nonzero_books"])
    assert venue_books - account_books == {2}


def test_non_bybit_account_says_why_there_is_no_venue_read(api):
    body = _get(api, "?account_id=alpaca_live&symbol=SPY")
    (row,) = body["accounts"]
    assert row["symbol_read"] == "not_bybit"
    assert row["symbol_books"] is None
    assert [p["symbol"] for p in row["positions"]] == ["SPY"]
