"""The Alpaca close and modify_protective act on a TRADE, not on a SYMBOL.

MI-173 (#11312) established the mechanism from the code, and this is the
repair. The chain carried a per-trade quantity the whole way and then dropped
it at the venue boundary::

    order_monitor._send_close_to_exchange   qty = matched_trade["position_size"]
      -> execute.close_open_position(qty=…)  validates `if qty <= 0` …
            if exchange == "alpaca":  exchange_client.close(symbol)   <- drops it
      -> AlpacaClient.close(self, symbol)    <- no qty parameter at all

so closing a 16-share row on a symbol the account was short 72 of liquidated
all 72, and the sibling row stayed ``open`` with nothing behind it.

**The venue can express the scoped operation.** ``DELETE
/v2/positions/{symbol_or_asset_id}`` documents a ``qty`` query parameter — *"the
number of shares to liquidate. Can accept up to 9 decimal points. Cannot work
with percentage"*. Establishing that was the first step of MI-173's proposal and
the precondition for this whole file; had the answer been no, the finding would
have been the deliverable instead.

WHICH TESTS ARE EVIDENCE OF THE REPAIR, AND WHICH ARE NOT
---------------------------------------------------------
Stated explicitly because a green suite says nothing on its own. The split
below is MEASURED, not asserted: the 14 tests were run against this file's src
changes stashed, i.e. against `main`'s ``src/`` verbatim, giving **12 failed,
2 passed**. Three groups, and the middle one is easy to miscount as evidence.

**(1) TEN fail on `main` on BEHAVIOUR — these are the repair:**
  * ``test_close_of_one_trade_reduces_by_that_trades_qty``
  * ``test_close_of_one_trade_does_not_cancel_the_siblings_protection``
  * ``test_close_defers_rather_than_flattening_when_the_size_is_unreadable``
  * ``test_extended_hours_partial_defers_instead_of_liquidating_the_symbol``
  * ``test_close_open_position_forwards_the_trade_qty``
  * ``test_success_log_does_not_label_a_whole_symbol_flatten_with_a_trade_qty``
  * ``test_modify_protective_refuses_when_no_leg_matches_the_trade``
  * ``test_modify_protective_patches_only_the_named_trades_leg``
  * ``test_modify_protective_refuses_ambiguous_same_size_legs``
  * ``test_modify_open_order_forwards_the_trade_qty``

**(2) TWO fail on `main` for a SIGNATURE reason, and are NOT evidence:**
  * ``test_whole_symbol_close_is_unchanged_when_the_trade_is_the_position``
  * ``test_close_404_still_maps_to_an_idempotent_ok``

  Both raise ``TypeError: AlpacaClient.close() takes 2 positional arguments
  but 3 were given`` — they hand ``close()`` a quantity that does not exist as
  a parameter there. That is the absence of the feature, not a behavioural
  difference, and it is exactly the case where a red-before/green-after count
  flatters itself: these two PIN THAT THE WHOLE-SYMBOL PATH DID NOT CHANGE, so
  by construction they cannot demonstrate a change. They cannot be run against
  `main` verbatim at all.

**(3) TWO pass on `main` unchanged — the only true either-way controls:**
  * ``test_qty_none_is_the_legacy_unscoped_close``
  * ``test_modify_protective_without_qty_still_patches``

  These are what actually establish that a caller passing no quantity gets the
  pre-existing behaviour, because they are the only two that execute on both
  trees.

The bodies assert against the EXPECTED Alpaca contract; a sandbox cannot reach
the broker, so **live acceptance of ``?qty=`` against a real Alpaca position is
NOT established here** and remains an alpaca_paper verification step. What is
established is the documented parameter and this repo's use of it.
"""
from __future__ import annotations

import logging

import pytest

from src.units.accounts.alpaca_client import AlpacaClient
from src.units.accounts.execute import close_open_position, modify_open_order


ACCT = {"account_id": "alpaca_portfolio", "exchange": "alpaca"}

# The live precondition MI-173 measured on 2026-09-08T01:40:23Z: alpaca_portfolio
# short TLT 72, made of journal rows 5266 (16, tlt_pullback_1d) and 5414 (56,
# tlt_pullback_1h). Used as the fixture so the tests describe the real case.
SYMBOL = "TLT"
POSITION = 72.0
TRADE_A = 16.0
TRADE_B = 56.0


def _client() -> AlpacaClient:
    return AlpacaClient(api_key="k", api_secret="s", env="paper")


@pytest.fixture(autouse=True)
def _regular_session(monkeypatch):
    """Default every test to regular trading hours; the extended test overrides."""
    monkeypatch.setattr(
        "src.runtime.market_hours.us_equity_session", lambda *a, **k: "regular"
    )


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    monkeypatch.setattr("src.units.accounts.alpaca_client.time.sleep", lambda *_: None)


class FakeAlpaca:
    """Records every request and answers as the venue would.

    ``position_qty`` is mutated by a successful liquidation so the confirmation
    gates (flat, or reduced) see the book actually move — a fake that always
    reports the old size would let a broken confirm pass.
    """

    def __init__(self, position_qty=POSITION, legs=None, fills=True):
        self.position_qty = position_qty
        self.legs = list(legs or [])
        self.fills = fills
        self.calls: list[tuple[str, str, dict | None]] = []

    # -- helpers -----------------------------------------------------------
    def paths(self, method):
        return [p for m, p, _ in self.calls if m == method]

    @property
    def cancelled_orders(self):
        return [p for p in self.paths("DELETE") if p.startswith("/v2/orders/")]

    @property
    def liquidations(self):
        return [p for p in self.paths("DELETE") if p.startswith("/v2/positions/")]

    @property
    def patches(self):
        return [(p, b) for m, p, b in self.calls if m == "PATCH"]

    def _position_body(self):
        return {"symbol": SYMBOL, "qty": f"-{self.position_qty}",
                "qty_available": f"{self.position_qty}", "side": "short",
                "current_price": "89.10", "asset_class": "us_equity"}

    # -- the transport -----------------------------------------------------
    def __call__(self, method, path, json_body=None):
        self.calls.append((method, path, json_body))
        base = path.split("?")[0]

        if method == "GET" and base.startswith("/v2/orders"):
            return {"retCode": 0, "result": list(self.legs)}
        if method == "GET" and base == "/v2/positions":
            if self.position_qty <= 0:
                return {"retCode": 0, "result": []}
            return {"retCode": 0, "result": [self._position_body()]}
        if method == "GET" and base.startswith("/v2/positions/"):
            if self.position_qty <= 0:
                return {"retCode": 404, "retMsg": "position does not exist"}
            return {"retCode": 0, "result": self._position_body()}

        if method == "DELETE" and base.startswith("/v2/positions/"):
            if self.position_qty <= 0:
                return {"retCode": 404, "retMsg": "position does not exist"}
            qty = None
            if "qty=" in path:
                qty = float(path.split("qty=")[1].split("&")[0])
            if self.fills:
                self.position_qty = max(
                    0.0, self.position_qty - (qty if qty is not None else self.position_qty)
                )
            return {"retCode": 0, "result": {"id": "liq-1"}}

        if method == "DELETE" and base.startswith("/v2/orders/"):
            oid = base.rsplit("/", 1)[1]
            self.legs = [o for o in self.legs if str(o.get("id")) != oid]
            return {"retCode": 0, "result": {}}

        if method == "PATCH":
            return {"retCode": 0, "result": {"id": base.rsplit("/", 1)[1]}}
        if method == "POST":
            return {"retCode": 0, "result": {"id": "ord-1"}}
        return {"retCode": 0, "result": {}}


def _leg(oid, qty, otype, **extra):
    body = {"id": oid, "symbol": SYMBOL, "qty": str(qty), "type": otype}
    body.update(extra)
    return body


# ===================================================================== close
# --- repair evidence: these FAIL on today's main -----------------------------
def test_close_of_one_trade_reduces_by_that_trades_qty(monkeypatch):
    """Closing the 16-share row must liquidate 16, not the symbol's 72.

    On `main` the qty never reaches the client, so the DELETE goes out bare and
    all 72 shares leave the book.
    """
    fake = FakeAlpaca()
    c = _client()
    monkeypatch.setattr(c, "_request", fake)

    res = c.close(SYMBOL, TRADE_A)

    assert res["retCode"] == 0, res
    assert fake.liquidations == [f"/v2/positions/{SYMBOL}?qty=16"]
    # The sibling's 56 shares are still on the book.
    assert fake.position_qty == pytest.approx(TRADE_B)
    assert res["result"]["scope"] == "trade"


def test_close_of_one_trade_does_not_cancel_the_siblings_protection(monkeypatch):
    """The pre-cancel is a SECOND destructive step and must not run here.

    `_cancel_open_orders_for_symbol` cancels *every* resting order on the
    symbol, so on `main` a close of trade A strips trade B's stop and target
    BEFORE the flatten is attempted — leaving B naked on any path where the
    flatten then fails.
    """
    legs = [
        _leg("A-stop", TRADE_A, "stop", stop_price="90.10"),
        _leg("B-stop", TRADE_B, "stop", stop_price="82.33"),
        _leg("B-tp", TRADE_B, "limit", limit_price="74.67"),
    ]
    fake = FakeAlpaca(legs=legs)
    c = _client()
    monkeypatch.setattr(c, "_request", fake)

    c.close(SYMBOL, TRADE_A)

    assert fake.cancelled_orders == []
    assert {o["id"] for o in fake.legs} == {"A-stop", "B-stop", "B-tp"}


def test_close_defers_rather_than_flattening_when_the_size_is_unreadable(monkeypatch):
    """"We could not look" must never degrade into "liquidate everything"."""
    fake = FakeAlpaca()

    def unreadable(method, path, json_body=None):
        fake.calls.append((method, path, json_body))
        if method == "GET" and path.startswith(f"/v2/positions/{SYMBOL}"):
            return {"retCode": 500, "retMsg": "internal error"}
        return fake(method, path, json_body)

    c = _client()
    monkeypatch.setattr(c, "_request", unreadable)

    res = c.close(SYMBOL, TRADE_A)

    assert res["retCode"] == 2, res           # deferred, not failed
    assert "unreadable" in res["retMsg"]
    assert fake.liquidations == []


def test_extended_hours_partial_defers_instead_of_liquidating_the_symbol(monkeypatch):
    """Extended hours cannot scope safely, so it places NOTHING and cancels NOTHING.

    On `main` this path cancels every resting order and then sells the whole
    live position read off the venue.
    """
    monkeypatch.setattr(
        "src.runtime.market_hours.us_equity_session", lambda *a, **k: "extended"
    )
    legs = [_leg("B-stop", TRADE_B, "stop", stop_price="82.33")]
    fake = FakeAlpaca(legs=legs)
    c = _client()
    monkeypatch.setattr(c, "_request", fake)

    res = c.close(SYMBOL, TRADE_A)

    assert res["retCode"] == 2, res
    assert "DEFERRED" in res["retMsg"]
    assert fake.paths("POST") == []
    assert fake.cancelled_orders == []
    assert fake.position_qty == pytest.approx(POSITION)


def test_close_open_position_forwards_the_trade_qty(monkeypatch):
    """The wiring: the qty `close_open_position` validates is the qty it sends."""
    fake = FakeAlpaca()
    c = _client()
    monkeypatch.setattr(c, "_request", fake)

    res = close_open_position(c, ACCT, symbol=SYMBOL, side="short", qty=TRADE_A)

    assert res["ok"] is True, res
    assert fake.liquidations == [f"/v2/positions/{SYMBOL}?qty=16"]


def test_success_log_does_not_label_a_whole_symbol_flatten_with_a_trade_qty(
    monkeypatch, caplog,
):
    """Sub-class A: the only per-close record a reviewer finds must be true.

    On `main` this line reads `qty=16 → alpaca flatten` while the venue
    liquidated 72 — a per-trade number printed next to an operation that
    ignored it.
    """
    fake = FakeAlpaca()
    c = _client()
    monkeypatch.setattr(c, "_request", fake)

    with caplog.at_level(logging.INFO, logger="src.units.accounts.execute"):
        close_open_position(c, ACCT, symbol=SYMBOL, side="short", qty=POSITION)

    line = next(m for m in caplog.messages if "close_open_position:" in m)
    assert "WHOLE-SYMBOL flatten" in line
    assert "the venue closed the entire TLT position" in line
    # The requested number is still shown — but LABELLED as the request, never
    # as what the venue did.
    assert "requested_qty=72.0" in line


# --- no-regression controls: these pass BEFORE and AFTER ---------------------
def test_whole_symbol_close_is_unchanged_when_the_trade_is_the_position(monkeypatch):
    """A trade that IS the whole position keeps the old path: pre-cancel, bare
    DELETE, strict flat-confirm. This is every single-row symbol."""
    legs = [_leg("A-stop", POSITION, "stop", stop_price="90.10")]
    fake = FakeAlpaca(legs=legs)
    c = _client()
    monkeypatch.setattr(c, "_request", fake)

    res = c.close(SYMBOL, POSITION)

    assert res["retCode"] == 0, res
    assert fake.liquidations == [f"/v2/positions/{SYMBOL}"]   # bare, no ?qty=
    assert fake.cancelled_orders == ["/v2/orders/A-stop"]     # pre-cancel ran
    assert "scope" not in (res.get("result") or {})


def test_qty_none_is_the_legacy_unscoped_close(monkeypatch):
    """A caller that passes no quantity gets exactly what it got before."""
    fake = FakeAlpaca()
    c = _client()
    monkeypatch.setattr(c, "_request", fake)

    res = c.close(SYMBOL)

    assert res["retCode"] == 0, res
    assert fake.liquidations == [f"/v2/positions/{SYMBOL}"]


def test_close_404_still_maps_to_an_idempotent_ok(monkeypatch):
    fake = FakeAlpaca(position_qty=0.0)
    c = _client()
    monkeypatch.setattr(c, "_request", fake)

    assert c.close(SYMBOL, TRADE_A)["retCode"] == 0
    assert c.close(SYMBOL)["retCode"] == 0


# ========================================================== modify_protective
# --- repair evidence: these FAIL on today's main -----------------------------
def test_modify_protective_refuses_when_no_leg_matches_the_trade(monkeypatch):
    """Only trade A's 16-share bracket rests; a modify for trade B (56) must
    NOT move it. On `main` the PATCH lands on A's stop."""
    legs = [_leg("A-stop", TRADE_A, "stop", stop_price="90.10")]
    fake = FakeAlpaca(legs=legs)
    c = _client()
    monkeypatch.setattr(c, "_request", fake)

    res = c.modify_protective(SYMBOL, sl=83.0, qty=TRADE_B)

    assert res["retCode"] == 1, res
    assert "no protective leg of qty=56" in res["retMsg"]
    assert fake.patches == []


def test_modify_protective_patches_only_the_named_trades_leg(monkeypatch):
    legs = [
        _leg("A-stop", TRADE_A, "stop", stop_price="90.10"),
        _leg("A-tp", TRADE_A, "limit", limit_price="74.67"),
        _leg("B-stop", TRADE_B, "stop", stop_price="82.33"),
        _leg("B-tp", TRADE_B, "limit", limit_price="70.00"),
    ]
    fake = FakeAlpaca(legs=legs)
    c = _client()
    monkeypatch.setattr(c, "_request", fake)

    res = c.modify_protective(SYMBOL, sl=83.5, qty=TRADE_B)

    assert res["retCode"] == 0, res
    assert [p for p, _ in fake.patches] == ["/v2/orders/B-stop"]
    assert fake.patches[0][1] == {"stop_price": "83.50"}


def test_modify_protective_refuses_ambiguous_same_size_legs(monkeypatch):
    """Two open rows of identical size cannot be told apart from the order book
    — Alpaca leg ids are never captured at entry — so this refuses rather than
    picking one trade's protection at random."""
    legs = [
        _leg("X-stop", TRADE_A, "stop", stop_price="90.10"),
        _leg("Y-stop", TRADE_A, "stop", stop_price="91.00"),
    ]
    fake = FakeAlpaca(legs=legs)
    c = _client()
    monkeypatch.setattr(c, "_request", fake)

    res = c.modify_protective(SYMBOL, sl=89.0, qty=TRADE_A)

    assert res["retCode"] == 1, res
    assert "ambiguous" in res["retMsg"]
    assert fake.patches == []


def test_modify_open_order_forwards_the_trade_qty(monkeypatch):
    legs = [
        _leg("A-stop", TRADE_A, "stop", stop_price="90.10"),
        _leg("B-stop", TRADE_B, "stop", stop_price="82.33"),
    ]
    fake = FakeAlpaca(legs=legs)
    c = _client()
    monkeypatch.setattr(c, "_request", fake)

    res = modify_open_order(c, ACCT, symbol=SYMBOL, sl=83.5, qty=TRADE_B)

    assert res["ok"] is True, res
    assert [p for p, _ in fake.patches] == ["/v2/orders/B-stop"]


# --- no-regression control: passes BEFORE and AFTER --------------------------
def test_modify_protective_without_qty_still_patches(monkeypatch):
    legs = [_leg("A-stop", TRADE_A, "stop", stop_price="90.10")]
    fake = FakeAlpaca(legs=legs)
    c = _client()
    monkeypatch.setattr(c, "_request", fake)

    res = c.modify_protective(SYMBOL, sl=89.0)

    assert res["retCode"] == 0, res
    assert [p for p, _ in fake.patches] == ["/v2/orders/A-stop"]
