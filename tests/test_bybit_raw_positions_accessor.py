"""``account_bybit_raw_positions`` — the accessor behind /api/diag/bybit_raw_positions.

MI-221. This is a DISCRIMINATING INSTRUMENT, and the only thing that makes it
worth having is that it does NOT do what every other Bybit position reader does.
So every test here asserts an ABSENCE of reduction, plus the state distinctions
that absence buys.

WHY IT EXISTS. ``account_open_positions._emit`` skips ``size <= 0`` and dedupes
by SYMBOL; ``account_bybit_open_orders`` does the same. So three different facts
— *the venue is genuinely flat*, *the venue returned a ZERO-SIZE row* (the
hedge-mode sibling book, routine since ``BYBIT_HEDGE_MODE_SYMBOLS`` was armed
2026-08-30) and *the venue returned no row at all* — are ONE observation to
every consumer in the system. That collapse blocked root-cause on two
real-money P1s one day apart (``BL-20260908-...-ROWS0`` states it verbatim;
MI-221 hit it again on a different function).

⚠️ **WIDENED 2026-09-09, AND THE WIDENING IS ITSELF A CORRECTION.** The first
version sent the SAME two queries the production readers send — ``settleCoin``
hardcoded ``"USDT"`` on one configured ``category``, then the configured symbol
roster — so it could say HOW a row was dropped but was blind to any book those
filters never ask about. It reported "no ETH row" for an account whose operator
was looking at an ETH position on the Bybit web UI at that moment. **An empty
read is evidence about the READ.** The tests below therefore assert the sweep
REACHES the books those three filters exclude, because a sweep that inherits the
filters it exists to test is worse than no sweep: it launders them as a negative.

Split from the route tests deliberately, mirroring
``tests/test_bybit_open_orders_accessor.py``: those need FastAPI's TestClient,
which does not import in every environment, whereas the properties that decide
whether this instrument can be TRUSTED must be falsifiable anywhere.
"""
from __future__ import annotations

from src.units.accounts import clients as accounts_clients


class _FakeBybit:
    """pybit stand-in recording every query it was asked for.

    ``pages`` maps a recorded call-key to a LIST of pages so cursor-following is
    testable; anything else answers in one page.
    """

    def __init__(self, settle_rows=None, by_symbol=None, raise_on=None,
                 by_base=None, pages=None, wallet=None,
                 unsupported_categories=("spot", "option")):
        self._settle = settle_rows or {}
        if isinstance(self._settle, list):          # back-compat: USDT only
            self._settle = {"USDT": self._settle}
        self._by_symbol = by_symbol or {}
        self._by_base = by_base or {}
        self._raise_on = raise_on or set()
        self._pages = pages or {}
        self._wallet = wallet
        self._unsupported = set(unsupported_categories or ())
        self.calls: list = []
        self.symbol_reads: list = []
        self.base_reads: list = []
        self.settle_reads: list = []
        self.cursors_sent: list = []

    def get_positions(self, category=None, settleCoin=None, symbol=None,
                      baseCoin=None, cursor=None):
        self.calls.append(
            {"category": category, "settleCoin": settleCoin,
             "symbol": symbol, "baseCoin": baseCoin, "cursor": cursor})
        if cursor:
            self.cursors_sent.append(cursor)
        # A category the venue does not support for this endpoint RAISES. That
        # must land as could_not_look, never as an empty book.
        if category in self._unsupported:
            raise RuntimeError(f"category {category} unsupported for position/list")
        if symbol is not None:
            self.symbol_reads.append((category, symbol))
            if symbol in self._raise_on:
                raise RuntimeError(f"venue refused {symbol}")
            key = f"symbol:{symbol}:{category}"
            if key in self._pages:
                return self._page(key, cursor)
            return {"result": {"list": self._by_symbol.get(symbol, [])}}
        if baseCoin is not None:
            self.base_reads.append((category, baseCoin))
            if baseCoin in self._raise_on:
                raise RuntimeError(f"venue refused base {baseCoin}")
            key = f"base:{baseCoin}:{category}"
            if key in self._pages:
                return self._page(key, cursor)
            return {"result": {"list": self._by_base.get((category, baseCoin), [])}}
        self.settle_reads.append((category, settleCoin))
        if f"__settle__{settleCoin}" in self._raise_on:
            raise RuntimeError(f"settleCoin {settleCoin} query failed")
        return {"result": {"list": self._settle.get(settleCoin, [])}}

    def _page(self, key, cursor):
        pages = self._pages[key]
        idx = 0 if not cursor else int(cursor.split("-")[-1])
        rows = pages[idx]
        nxt = f"{key}-cur-{idx + 1}" if idx + 1 < len(pages) else ""
        return {"result": {"list": rows, "nextPageCursor": nxt}}

    def get_wallet_balance(self, accountType=None):
        if "__wallet__" in self._raise_on:
            raise RuntimeError("wallet read failed")
        return self._wallet or {"result": {"list": [{
            "accountType": "UNIFIED", "totalEquity": "232.20",
            "totalAvailableBalance": "", "totalInitialMargin": "27.5",
            "coin": [{"coin": "USDT", "equity": "232.20", "walletBalance": "232.20"}],
        }]}}


def _acct(**kw):
    base = {
        "account_id": "bybit_2",
        "exchange": "bybit",
        "market_type": "linear",
        "symbols": ["ETHUSDT", "XRPUSDT"],
    }
    base.update(kw)
    return base


def _install(monkeypatch, fake):
    monkeypatch.setattr(accounts_clients, "bybit_client_for", lambda a: fake)


def _pos(symbol, size, idx, side="Buy"):
    return {
        "symbol": symbol, "size": size, "positionIdx": idx, "side": side,
        "avgPrice": "2453.97", "markPrice": "2457.59",
        "stopLoss": "", "takeProfit": "", "unrealisedPnl": "0.1",
    }


def _q(out, query, category="linear"):
    for row in out["queries"]:
        if row["query"] == query and row["category"] == category:
            return row
    raise AssertionError(f"no query {query!r} on {category!r}: "
                         f"{[(r['category'], r['query']) for r in out['queries']]}")


# --------------------------------------------------------------------------
# The reductions this instrument must NOT perform
# --------------------------------------------------------------------------

def test_zero_size_rows_are_returned_not_skipped(monkeypatch):
    """THE ORIGINAL POINT. A zero-size row is what every other reader discards,
    and it distinguishes 'the venue returned a flat sibling book' from 'the
    venue returned nothing for this symbol'."""
    fake = _FakeBybit(settle_rows={"USDT": [_pos("ETHUSDT", "0", 2, "Sell")]})
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    assert out is not None
    eth = [r for r in out["rows"] if r["symbol"] == "ETHUSDT"
           and r["source_query"] == "settle_coin_usdt"]
    assert len(eth) == 1, "a zero-size row must survive to the caller"
    assert eth[0]["size"] == 0.0 and eth[0]["position_idx"] == 2
    assert out["zero_size_rows"] >= 1 and out["nonzero_size_rows"] == 0


def test_rows_are_not_deduped_by_symbol(monkeypatch):
    """Both hedge books of one symbol must BOTH appear. `_emit`'s
    `if sym in seen: return` keeps only the first, which is how a live long can
    be hidden behind a flat short."""
    fake = _FakeBybit(settle_rows={"USDT": [
        _pos("ETHUSDT", "0", 2, "Sell"),      # flat sibling, listed FIRST
        _pos("ETHUSDT", "0.04", 1, "Buy"),    # the live book
    ]})
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    eth = [r for r in out["rows"] if r["symbol"] == "ETHUSDT"
           and r["source_query"] == "settle_coin_usdt"]
    assert len(eth) == 2, "symbol dedupe would drop the live book"
    assert {r["position_idx"] for r in eth} == {1, 2}


def test_symbol_scoped_read_runs_even_when_the_page_returned_the_symbol(monkeypatch):
    """The DISAGREEMENT between views is the finding
    (BL-20260713-BYBIT2-BTC-SETTLECOIN-BLIND)."""
    fake = _FakeBybit(
        settle_rows={"USDT": [_pos("ETHUSDT", "0.04", 1)]},
        by_symbol={"ETHUSDT": [_pos("ETHUSDT", "0.04", 1)], "XRPUSDT": []},
    )
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    assert ("linear", "ETHUSDT") in fake.symbol_reads
    sources = {r["source_query"] for r in out["rows"]}
    assert "settle_coin_usdt" in sources and "symbol:ETHUSDT" in sources


def test_unparseable_size_is_not_coerced_to_flat(monkeypatch):
    """`_f`-style coercion turns an unreadable size into 0.0 — MANUFACTURING the
    flat reading this route exists to distinguish."""
    fake = _FakeBybit(settle_rows={"USDT": [_pos("ETHUSDT", "n/a", 1)]})
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    row = [r for r in out["rows"] if r["source_query"] == "settle_coin_usdt"][0]
    assert row["size"] is None and row["size_parsed"] is False
    assert row["size_raw"] == "n/a"
    assert out["unparseable_size_rows"] == 1
    # The row must be counted as UNPARSEABLE and in NEITHER size bucket:
    # `graded` excludes it, so an unreadable size can never be tallied as flat.
    assert out["zero_size_rows"] == 0, "unparseable must not be counted as flat"
    assert out["nonzero_size_rows"] == 0


# --------------------------------------------------------------------------
# The WIDENING — the three filters that hid a real position
# --------------------------------------------------------------------------

def test_usdc_settled_book_is_reached(monkeypatch):
    """FILTER 1: settleCoin was hardcoded USDT, so a USDC-settled contract — the
    SAME `linear` category — was excluded from every read in the system."""
    fake = _FakeBybit(settle_rows={
        "USDT": [],
        "USDC": [_pos("ETHPERP", "0.04", 1)],
    })
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    assert ("linear", "USDC") in fake.settle_reads, "USDC page must be requested"
    assert _q(out, "settle_coin_usdc")["query_state"] == "rows_returned"
    assert "ETHPERP" in out["distinct_nonzero_symbols"]


def test_base_coin_queries_are_sent_and_reach_a_symbol_outside_the_roster(monkeypatch):
    """FILTER 3, and the decisive one. NO position read in this repo passed
    `baseCoin` (grep: only `marketUnit="baseCoin"` on the ORDER path), so a
    symbol outside the configured roster was never asked for. On
    /v5/position/list `baseCoin` matches every contract on that base regardless
    of settlement."""
    fake = _FakeBybit(
        settle_rows={"USDT": [], "USDC": []},
        by_base={("linear", "ETH"): [_pos("ETH-28MAR26", "0.04", 1)]},
    )
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    assert ("linear", "ETH") in fake.base_reads
    assert "ETH" in out["bases_swept"] and "XRP" in out["bases_swept"]
    assert "ETH-28MAR26" in out["distinct_nonzero_symbols"], (
        "a base-coin query must reach a symbol the roster never names")


def test_every_category_is_swept_not_just_the_configured_one(monkeypatch):
    """FILTER 2: `category` came from a single configured `market_type`, so
    inverse/spot/option were never queried at all."""
    fake = _FakeBybit(settle_rows={"USDT": [], "USDC": []},
                      by_base={("inverse", "ETH"): [_pos("ETHUSD", "10", 1)]},
                      unsupported_categories=())
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    assert set(out["categories_swept"]) == {"linear", "inverse", "spot", "option"}
    assert ("inverse", "ETH") in fake.base_reads
    assert "ETHUSD" in out["distinct_nonzero_symbols"]
    assert out["declared_category"] == "linear", (
        "the configured category is recorded for contrast, not used as the bound")


def test_unsupported_category_is_could_not_look_never_no_rows(monkeypatch):
    """An unsupported (category, param) pair RAISES. Folding that into `no_rows`
    would report a book we never read as a book that is empty — the exact
    unprovenanced-negative this instrument exists to prevent."""
    fake = _FakeBybit(settle_rows={"USDT": [], "USDC": []},
                      unsupported_categories=("spot", "option"))
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    spot = [q for q in out["queries"] if q["category"] == "spot"]
    assert spot, "spot must be attempted"
    assert all(q["query_state"] == "could_not_look" for q in spot)
    assert all(q["row_count"] is None for q in spot), "never 0 when we could not look"
    assert out["queries_could_not_look"] >= len(spot)


def test_cursor_is_followed_and_reported(monkeypatch):
    """NOTHING in src/ followed `nextPageCursor` — grep returns zero hits — so
    every position read was page-1-only and a truncated page was
    indistinguishable from a complete one."""
    fake = _FakeBybit(
        settle_rows={"USDT": [], "USDC": []},
        pages={"symbol:ETHUSDT:linear": [
            [_pos("ETHUSDT", "0", 2, "Sell")],
            [_pos("ETHUSDT", "0.04", 1, "Buy")],   # the LIVE book, page 2
        ]},
    )
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    q = _q(out, "symbol:ETHUSDT")
    assert q["pages_read"] == 2, "page 2 must be fetched"
    assert q["row_count"] == 2
    assert q["next_page_cursor_seen"], "the cursor must be reported, not just used"
    assert fake.cursors_sent, "a cursor must actually be sent back to the venue"
    live = [r for r in out["rows"]
            if r["source_query"] == "symbol:ETHUSDT" and (r["size"] or 0) > 0]
    assert live, "a live position on page 2 would be invisible without the cursor"


def test_truncation_is_reported_rather_than_read_as_complete(monkeypatch):
    """The page cap is a safety bound on a live read path, not a belief about how
    many pages exist — so hitting it must be VISIBLE."""
    fake = _FakeBybit(
        settle_rows={"USDT": [], "USDC": []},
        pages={"symbol:ETHUSDT:linear": [[_pos("ETHUSDT", "0", 1)] for _ in range(25)]},
    )
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    q = _q(out, "symbol:ETHUSDT")
    assert q["pages_truncated"] is True
    assert q["pages_read"] == 10


# --------------------------------------------------------------------------
# The wallet block — the filter-independent check
# --------------------------------------------------------------------------

def test_wallet_block_preserves_empty_strings_rather_than_zeroing_them(monkeypatch):
    """`totalAvailableBalance` comes back EMPTY on the live bybit_2. An empty
    string is the venue declining to compute; coercing it to 0.0 would assert a
    reading nobody made."""
    fake = _FakeBybit(settle_rows={"USDT": [], "USDC": []})
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    assert out["wallet_state"] == "wallet_read"
    assert out["wallet"]["total_available_balance"] == "", "empty must stay empty"
    assert out["wallet"]["total_position_im"] == "27.5"


def test_wallet_failure_is_could_not_look_and_does_not_sink_the_sweep(monkeypatch):
    fake = _FakeBybit(settle_rows={"USDT": [_pos("XRPUSDT", "58.5", 1)]},
                      raise_on={"__wallet__"})
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    assert out is not None
    assert out["wallet_state"] == "could_not_look" and out["wallet"] is None
    assert out["wallet_error"]
    assert out["nonzero_size_rows"] >= 1, "the position sweep is independent"


def test_bases_are_unioned_from_the_wallet_not_bounded_by_the_roster(monkeypatch):
    """The configured roster is one of the filters that hid the position, so
    bounding the base sweep by it would reproduce the defect."""
    fake = _FakeBybit(
        settle_rows={"USDT": [], "USDC": []},
        wallet={"result": {"list": [{
            "accountType": "UNIFIED", "totalEquity": "1",
            "coin": [{"coin": "USDT"}, {"coin": "SOL"}],
        }]}},
    )
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    assert "SOL" in out["bases_swept"], "a wallet coin outside the roster must be swept"
    assert "USDT" not in out["bases_swept"], "stablecoins are not bases"
    assert ("linear", "SOL") in fake.base_reads


# --------------------------------------------------------------------------
# States and failure isolation
# --------------------------------------------------------------------------

def test_no_rows_and_could_not_look_are_different_states(monkeypatch):
    """An EMPTY answer is a positive measurement of the venue's view; a raised
    call says nothing about the world."""
    fake = _FakeBybit(settle_rows={"USDT": [], "USDC": []},
                      by_symbol={"ETHUSDT": []}, raise_on={"XRPUSDT"})
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    assert _q(out, "settle_coin_usdt")["query_state"] == "no_rows"
    assert _q(out, "symbol:ETHUSDT")["query_state"] == "no_rows"
    assert _q(out, "symbol:ETHUSDT")["row_count"] == 0
    assert _q(out, "symbol:XRPUSDT")["query_state"] == "could_not_look"
    assert _q(out, "symbol:XRPUSDT")["row_count"] is None


def test_one_failing_query_does_not_sink_the_others(monkeypatch):
    fake = _FakeBybit(settle_rows={"USDT": [_pos("ETHUSDT", "0.04", 1)]},
                      raise_on={"ETHUSDT", "XRPUSDT"})
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    assert out is not None and out["nonzero_size_rows"] >= 1


def test_non_bybit_and_missing_client_read_none(monkeypatch):
    """`None` means 'could not look' and must never present as an empty book."""
    assert accounts_clients.account_bybit_raw_positions(
        _acct(exchange="alpaca")) is None
    monkeypatch.setattr(accounts_clients, "bybit_client_for", lambda a: None)
    assert accounts_clients.account_bybit_raw_positions(_acct()) is None


def test_a_spot_account_is_no_longer_short_circuited(monkeypatch):
    """The first version returned None for a spot-pinned account, so a spot book
    could not be inspected at all. The declared category no longer bounds the
    sweep, so such an account is now swept like any other."""
    fake = _FakeBybit(settle_rows={"USDT": [], "USDC": []},
                      unsupported_categories=())
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct(market_type="spot"))
    assert out is not None, "a spot-pinned account must still be swept"
    assert out["declared_category"] == "spot"
    assert any(q["category"] == "linear" for q in out["queries"])


def test_symbols_resolve_via_the_fallback_not_the_bare_cfg_key(monkeypatch):
    """Its sibling `account_bybit_open_orders` reads `account['symbols']`
    directly and silently no-ops on a hand-built reduced cfg."""
    fake = _FakeBybit(settle_rows={"USDT": [], "USDC": []},
                      by_symbol={"ADAUSDT": []})
    _install(monkeypatch, fake)
    monkeypatch.setattr(
        accounts_clients, "_bybit_configured_symbols", lambda a: ["ADAUSDT"])
    out = accounts_clients.account_bybit_raw_positions(
        {"account_id": "bybit_2", "exchange": "bybit", "market_type": "linear"})
    assert ("linear", "ADAUSDT") in fake.symbol_reads
    assert "ADA" in out["bases_swept"]
