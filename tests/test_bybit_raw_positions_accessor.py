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

Split from the route tests deliberately, mirroring
``tests/test_bybit_open_orders_accessor.py``: those need FastAPI's TestClient,
which does not import in every environment, whereas the properties that decide
whether this instrument can be TRUSTED must be falsifiable anywhere.
"""
from __future__ import annotations

from src.units.accounts import clients as accounts_clients


class _FakeBybit:
    """pybit stand-in recording every query it was asked for."""

    def __init__(self, settle_rows=None, by_symbol=None, raise_on=None):
        self._settle = settle_rows or []
        self._by_symbol = by_symbol or {}
        self._raise_on = raise_on or set()
        self.symbol_reads: list = []
        self.settle_reads = 0

    def get_positions(self, category=None, settleCoin=None, symbol=None):
        if symbol is not None:
            self.symbol_reads.append(symbol)
            if symbol in self._raise_on:
                raise RuntimeError(f"venue refused {symbol}")
            return {"result": {"list": self._by_symbol.get(symbol, [])}}
        self.settle_reads += 1
        if "__settle__" in self._raise_on:
            raise RuntimeError("settleCoin query failed")
        return {"result": {"list": self._settle}}


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


def test_zero_size_rows_are_returned_not_skipped(monkeypatch):
    """THE WHOLE POINT. A zero-size row is what every other reader discards, and
    it is exactly what distinguishes 'the venue returned a flat sibling book'
    from 'the venue returned nothing for this symbol'."""
    fake = _FakeBybit(settle_rows=[_pos("ETHUSDT", "0", 2, "Sell")])
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
    fake = _FakeBybit(settle_rows=[
        _pos("ETHUSDT", "0", 2, "Sell"),      # flat sibling, listed FIRST
        _pos("ETHUSDT", "0.04", 1, "Buy"),    # the live book
    ])
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    eth = [r for r in out["rows"] if r["symbol"] == "ETHUSDT"
           and r["source_query"] == "settle_coin_usdt"]
    assert len(eth) == 2, "symbol dedupe would drop the live book"
    assert {r["position_idx"] for r in eth} == {1, 2}
    assert out["nonzero_size_rows"] == 1 and out["zero_size_rows"] == 1


def test_symbol_scoped_read_runs_even_when_the_page_returned_the_symbol(monkeypatch):
    """The DISAGREEMENT between the two views is the finding
    (BL-20260713-BYBIT2-BTC-SETTLECOIN-BLIND). A `sym in seen` skip would hide
    precisely the case this instrument was built to see."""
    fake = _FakeBybit(
        settle_rows=[_pos("ETHUSDT", "0.04", 1)],
        by_symbol={"ETHUSDT": [_pos("ETHUSDT", "0.04", 1)], "XRPUSDT": []},
    )
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    assert "ETHUSDT" in fake.symbol_reads, "must re-query a symbol the page returned"
    assert "XRPUSDT" in fake.symbol_reads
    sources = {r["source_query"] for r in out["rows"]}
    assert "settle_coin_usdt" in sources and "symbol:ETHUSDT" in sources


def test_no_rows_and_could_not_look_are_different_states(monkeypatch):
    """An EMPTY answer is a positive measurement of the venue's view; a raised
    call says nothing about the world. Collapsing them is the defect this
    instrument exists to stop."""
    fake = _FakeBybit(settle_rows=[], by_symbol={"ETHUSDT": []},
                      raise_on={"XRPUSDT"})
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    states = {q["query"]: q for q in out["queries"]}
    assert states["settle_coin_usdt"]["query_state"] == "no_rows"
    assert states["symbol:ETHUSDT"]["query_state"] == "no_rows"
    assert states["symbol:XRPUSDT"]["query_state"] == "could_not_look"
    assert states["symbol:XRPUSDT"]["row_count"] is None, "never 0 when we could not look"
    assert states["symbol:ETHUSDT"]["row_count"] == 0


def test_one_failing_query_does_not_sink_the_others(monkeypatch):
    """A per-symbol failure must not turn the whole read into `None` — the other
    queries still carry real evidence."""
    fake = _FakeBybit(settle_rows=[_pos("ETHUSDT", "0.04", 1)],
                      raise_on={"ETHUSDT", "XRPUSDT"})
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    assert out is not None and out["nonzero_size_rows"] == 1


def test_unparseable_size_is_not_coerced_to_flat(monkeypatch):
    """`_f`-style coercion turns an unreadable size into 0.0 — MANUFACTURING the
    flat reading this route exists to distinguish."""
    fake = _FakeBybit(settle_rows=[_pos("ETHUSDT", "n/a", 1)])
    _install(monkeypatch, fake)
    out = accounts_clients.account_bybit_raw_positions(_acct())
    row = out["rows"][0]
    assert row["size"] is None and row["size_parsed"] is False
    assert row["size_raw"] == "n/a"
    assert out["unparseable_size_rows"] == 1
    assert out["zero_size_rows"] == 0, "unparseable must not be counted as flat"


def test_non_bybit_and_missing_client_read_none(monkeypatch):
    """`None` means 'could not look' and must never present as an empty book."""
    assert accounts_clients.account_bybit_raw_positions(
        _acct(exchange="alpaca")) is None
    monkeypatch.setattr(accounts_clients, "bybit_client_for", lambda a: None)
    assert accounts_clients.account_bybit_raw_positions(_acct()) is None


def test_symbols_resolve_via_the_fallback_not_the_bare_cfg_key(monkeypatch):
    """Its sibling `account_bybit_open_orders` reads `account['symbols']`
    directly and silently no-ops on a hand-built reduced cfg. This one resolves
    through `_bybit_configured_symbols`, which falls back to accounts.yaml."""
    fake = _FakeBybit(settle_rows=[], by_symbol={"ADAUSDT": []})
    _install(monkeypatch, fake)
    monkeypatch.setattr(
        accounts_clients, "_bybit_configured_symbols", lambda a: ["ADAUSDT"])
    out = accounts_clients.account_bybit_raw_positions(
        {"account_id": "bybit_2", "exchange": "bybit", "market_type": "linear"})
    assert fake.symbol_reads == ["ADAUSDT"]
    assert out is not None
