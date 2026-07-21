"""P3 (close-first) — wire IB + Alpaca close to the strategy exit path.

Ref: docs/audits/live-trade-management-contract-2026-06-16.md § Design plan §2
+ Decision 2 (close first).

This is the live-order-path change: a strategy's CLOSE verdict now actually
reaches IB + Alpaca instead of failing ``no_client`` / ``unsupported_op``.
No kill-switch (baseline correctness, Prime Directive); no modify (deferred);
no reconciler change.

These tests pin:
  1. ``IBClient.close`` — cancels resting protective legs for the symbol AND
     places an OPPOSING reduce market order sized to the live position;
     refuses fractional/oversized closes; an error path returns retCode!=0
     (never raises).
  2. ``AlpacaClient.close`` is the native idempotent flatten (verified by the
     execute.close_open_position routing — Alpaca's own client test lives
     elsewhere); 404 → ok.
  3. ``execute.close_open_position`` routes IB / alpaca to the right client
     methods AND leaves the Bybit branch byte-unchanged.
  4. ``EXCHANGE_MANAGEMENT_CAPS`` declares ``close`` for IB + alpaca;
     ``account_supports_management(ib_cfg, "close")`` is True.
  5. ``_send_close_to_exchange`` returns ok for a mocked IB / alpaca client.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from src.runtime import order_monitor as om
from src.units.accounts import clients
from src.units.accounts.execute import close_open_position
from src.units.accounts.ib_client import IBClient, get_ib_client


# ---------------------------------------------------------------------------
# Fake ib_insync surface (mirrors tests/test_ib_integration.py so the
# in-method ``from ib_insync import ...`` resolves without the real package)
# ---------------------------------------------------------------------------


class _FakeContract:
    def __init__(self, **kw):
        self.conId = kw.get("conId", 0)
        self.exchange = kw.get("exchange")
        self.symbol = kw.get("symbol")
        self.currency = kw.get("currency")


class _FakeOrder:
    def __init__(self, action, totalQuantity, price=None):
        self.action = action
        self.totalQuantity = totalQuantity
        self.lmtPrice = price
        self.auxPrice = price
        self.orderId = 0
        self.parentId = 0
        self.transmit = True
        self.account = None
        self.tif = ""


class _FakeStatus:
    def __init__(self, status="Submitted"):
        self.status = status
        self.filled = 0.0
        self.avgFillPrice = 0.0


class _FakeTrade:
    """A resting/open trade carrying its order + contract (for symbol match)."""

    def __init__(self, order, contract, status="Submitted"):
        self.order = order
        self.contract = contract
        self.orderStatus = _FakeStatus(status)
        self.log = []


class _FakePortfolioItem:
    def __init__(self, symbol, position, account=None):
        self.contract = _FakeContract(symbol=symbol)
        self.position = position
        self.account = account
        self.averageCost = 0.0
        self.unrealizedPNL = 0.0


class _FakeClient:
    def __init__(self):
        self._req = 1000

    def getReqId(self):
        self._req += 1
        return self._req


class FakeIB:
    """Stand-in for ib_insync.IB with portfolio + symbol-bearing open trades."""

    def __init__(self, *, portfolio_items=None, open_trades=None,
                 flatten_on_close=True):
        self._connected = False
        self.client = _FakeClient()
        self._portfolio = portfolio_items or []
        self._open_trades = open_trades or []
        self.placed = []          # (contract, order)
        self.cancelled = []        # orders cancelled
        # Realistic paper fill: a placed (close) market order flattens the
        # matching-symbol portfolio item, so IBClient.close's flatten-confirm
        # poll sees the position go to zero like a real gateway. Set False to
        # simulate an accepted-but-UNFILLED close (the accepted!=flat bug that
        # left the MHG/ib_paper position open while the DB marked it closed).
        self._flatten_on_close = flatten_on_close

    def connect(self, host, port, clientId, timeout=10.0, readonly=False):
        self._connected = True

    def isConnected(self):
        return self._connected

    def disconnect(self):
        self._connected = False

    def qualifyContracts(self, *contracts):
        for c in contracts:
            if not getattr(c, "conId", 0):
                c.conId = 99999
        return list(contracts)

    def portfolio(self):
        return self._portfolio

    def openTrades(self):
        return list(self._open_trades)

    def trades(self):
        return list(self._open_trades)

    def placeOrder(self, contract, order):
        self.placed.append((contract, order))
        if self._flatten_on_close:
            sym = str(getattr(contract, "symbol", "") or "").upper()
            self._portfolio = [
                it for it in self._portfolio
                if str(getattr(getattr(it, "contract", None), "symbol", "")
                       or "").upper() != sym
            ]
        return _FakeTrade(order, contract, status="Submitted")

    def cancelOrder(self, order):
        self.cancelled.append(order)

    def sleep(self, _t=0):
        return None


@pytest.fixture(autouse=True)
def fake_ib_module(monkeypatch):
    """Inject a fake ``ib_insync`` module so IBClient.close's in-method import
    of ``MarketOrder`` resolves without the real package installed."""
    mod = types.ModuleType("ib_insync")
    mod.IB = FakeIB
    mod.ContFuture = lambda symbol, exchange, currency=None: _FakeContract(
        symbol=symbol, exchange=exchange, currency=currency
    )
    mod.Future = lambda **kw: _FakeContract(**kw)
    mod.MarketOrder = lambda action, qty: _FakeOrder(action, qty)
    mod.LimitOrder = lambda action, qty, price: _FakeOrder(action, qty, price)
    mod.StopOrder = lambda action, qty, price: _FakeOrder(action, qty, price)
    monkeypatch.setitem(sys.modules, "ib_insync", mod)
    return mod


def _ib_client_with(fake_ib, *, symbol="MGC", account="DUQ1", readonly=False):
    """Build an IBClient backed by *fake_ib*, bypassing the real socket.

    ``get_ib_client(..., _ib_factory=...)`` returns an isolated client whose
    ``connect()`` uses the injected IB. ``_build_contract`` is stubbed to a
    fake future so no real contract qualification is attempted.
    """
    client = get_ib_client(
        host="127.0.0.1", port=7497, client_id=1,
        account=account, symbol=symbol, readonly=readonly,
        _ib_factory=lambda: fake_ib,
    )
    client._build_contract = lambda sym=None: _FakeContract(  # type: ignore[method-assign]
        symbol=str(sym or symbol).upper(), conId=123
    )
    return client


# ---------------------------------------------------------------------------
# 1. IBClient.close
# ---------------------------------------------------------------------------


def test_ib_close_long_cancels_protective_then_sells_reduce():
    """A long MGC close: cancel the resting bracket legs for MGC, then place a
    single opposing SELL market order sized to the live position."""
    # Live IB position: long 3 MGC. Two resting protective legs on MGC.
    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MGC", 3, account="DUQ1")],
        open_trades=[
            _FakeTrade(MagicMock(orderId=10), _FakeContract(symbol="MGC")),
            _FakeTrade(MagicMock(orderId=11), _FakeContract(symbol="MGC")),
        ],
    )
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 3)

    assert res["retCode"] == 0, res
    # Both resting MGC protective legs cancelled before the close.
    assert len(fake_ib.cancelled) == 2
    # Exactly one opposing market order placed.
    assert len(fake_ib.placed) == 1
    _contract, order = fake_ib.placed[0]
    assert order.action == "SELL"          # reverse of long
    assert float(order.totalQuantity) == 3.0


def test_ib_close_short_buys_reduce():
    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MES", -2, account="DUQ1")],
        open_trades=[_FakeTrade(MagicMock(orderId=20), _FakeContract(symbol="MES"))],
    )
    client = _ib_client_with(fake_ib, symbol="MES")

    res = client.close("MES", "short", 2)

    assert res["retCode"] == 0
    assert len(fake_ib.cancelled) == 1
    _contract, order = fake_ib.placed[0]
    assert order.action == "BUY"           # reverse of short
    assert float(order.totalQuantity) == 2.0


def test_ib_close_clamps_to_live_qty():
    """A stale/oversized DB qty must never transmit a close larger than the
    live IB position (which on one-way futures would OPEN a reverse)."""
    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MGC", 2, account="DUQ1")],
        open_trades=[],
    )
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 9)  # DB thinks 9, IB holds 2

    assert res["retCode"] == 0
    _contract, order = fake_ib.placed[0]
    assert float(order.totalQuantity) == 2.0  # clamped to live qty


def test_ib_close_already_flat_is_ok_no_order():
    """IB reports flat → idempotent success, no opposing order placed (still
    sweeps any stray resting legs)."""
    fake_ib = FakeIB(
        portfolio_items=[],  # flat
        open_trades=[_FakeTrade(MagicMock(orderId=30), _FakeContract(symbol="MGC"))],
    )
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 3)

    assert res["retCode"] == 0
    assert res["result"]["orderId"] is None
    assert fake_ib.placed == []            # no opposing order
    assert len(fake_ib.cancelled) == 1     # stray leg still cancelled


def test_ib_close_refuses_fractional_after_clamp():
    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MGC", 0.4, account="DUQ1")],
    )
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 0.4)

    assert res["retCode"] != 0
    assert "contract" in res["retMsg"].lower()
    assert fake_ib.placed == []


def test_ib_close_readonly_refuses():
    fake_ib = FakeIB(portfolio_items=[_FakePortfolioItem("MGC", 1)])
    client = _ib_client_with(fake_ib, symbol="MGC", readonly=True)

    res = client.close("MGC", "long", 1)

    assert res["retCode"] != 0
    assert "read-only" in res["retMsg"].lower()
    assert fake_ib.placed == []


def test_ib_close_error_returns_not_ok_never_raises():
    """A placeOrder failure surfaces as retCode!=0, not an exception."""
    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MGC", 1, account="DUQ1")],
    )

    def _boom(contract, order):
        raise RuntimeError("gateway down")

    fake_ib.placeOrder = _boom  # type: ignore[assignment]
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 1)
    assert res["retCode"] != 0
    assert "gateway down" in res["retMsg"]


def test_ib_close_only_cancels_matching_symbol():
    """A resting leg on a DIFFERENT symbol must not be cancelled by an MGC
    close (no cross-symbol naked-ing)."""
    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MGC", 1, account="DUQ1")],
        open_trades=[
            _FakeTrade(MagicMock(orderId=40), _FakeContract(symbol="MGC")),
            _FakeTrade(MagicMock(orderId=41), _FakeContract(symbol="MES")),  # other sym
        ],
    )
    client = _ib_client_with(fake_ib, symbol="MGC")

    client.close("MGC", "long", 1)

    cancelled_ids = [getattr(o, "orderId", None) for o in fake_ib.cancelled]
    assert 40 in cancelled_ids
    assert 41 not in cancelled_ids


# ---------------------------------------------------------------------------
# 1b. IBClient.close flatten-confirmation (BL-20260624-MHG-CLOSE-CONFIRM)
# ---------------------------------------------------------------------------


def test_ib_close_confirmed_flat_returns_ok():
    """The happy path: the close order flattens the position → confirmed flat →
    retCode 0 (the default FakeIB flattens the matching item on placeOrder)."""
    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MGC", 3, account="DUQ1")],
    )
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 3)

    assert res["retCode"] == 0, res
    assert len(fake_ib.placed) == 1  # opposing order transmitted


def test_ib_close_accepted_but_unfilled_returns_not_ok(monkeypatch):
    """The bug regression: an accepted-but-UNFILLED close (position stays open)
    must NOT report success. Pre-fix IBClient.close returned retCode 0 the moment
    the opposing order was accepted, so the monitor marked the DB row closed while
    the real IB position lived on → orphan → flap. Now the flatten-confirm poll
    sees the position still open and returns retCode 1, so the monitor leaves the
    DB row open and retries."""
    monkeypatch.setenv("IB_CLOSE_CONFIRM_S", "0.2")  # short window for the test
    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MGC", 3, account="DUQ1")],
        flatten_on_close=False,  # close accepted but position never goes flat
    )
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 3)

    assert res["retCode"] == 1, res
    assert "not confirmed flat" in res["retMsg"]
    # The opposing order WAS transmitted (we tried to close) — the failure is
    # that it never flattened, not that we didn't attempt it.
    assert len(fake_ib.placed) == 1


def test_ib_close_confirm_disabled_restores_accept_is_success(monkeypatch):
    """IB_CLOSE_CONFIRM_S<=0 skips the flatten-confirm (legacy behaviour): an
    accepted close is reported ok even though the position is still open."""
    monkeypatch.setenv("IB_CLOSE_CONFIRM_S", "0")
    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MGC", 3, account="DUQ1")],
        flatten_on_close=False,
    )
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.close("MGC", "long", 3)

    assert res["retCode"] == 0


# ---------------------------------------------------------------------------
# 1c. place_protective accumulation guard (BL-20260624-MHG-FLIP)
# ---------------------------------------------------------------------------


def test_place_protective_cancels_resting_legs_before_arming():
    """Every re-arm must cancel the symbol's existing resting protective legs
    BEFORE placing a fresh OCA pair — else repeated re-arms across an orphan flap
    stack multiple live brackets whose stops later fire together and FLIP a flat
    position into a reverse orphan (the MHG long->short flip)."""
    fake_ib = FakeIB(
        portfolio_items=[_FakePortfolioItem("MHG", 3, account="DUQ1")],
        open_trades=[
            _FakeTrade(MagicMock(orderId=50), _FakeContract(symbol="MHG")),
            _FakeTrade(MagicMock(orderId=51), _FakeContract(symbol="MHG")),
            _FakeTrade(MagicMock(orderId=52), _FakeContract(symbol="MES")),
        ],
    )
    client = _ib_client_with(fake_ib, symbol="MHG")

    res = client.place_protective(
        {"symbol": "MHG", "direction": "long", "qty": 3, "sl": 6.04, "tp": 7.02}
    )

    assert res["retCode"] == 0, res
    cancelled_ids = [getattr(o, "orderId", None) for o in fake_ib.cancelled]
    # Both stale MHG legs cancelled; the MES leg untouched (no cross-symbol).
    assert 50 in cancelled_ids and 51 in cancelled_ids
    assert 52 not in cancelled_ids
    # The fresh OCA pair (stop + limit) was then placed.
    assert len(fake_ib.placed) == 2


def test_place_protective_pre_cancel_failure_still_arms():
    """A pre-cancel failure is best-effort — it must NOT block arming protection
    on a live naked position (a stop-less live position is the worse state)."""
    fake_ib = FakeIB(portfolio_items=[_FakePortfolioItem("MHG", 3, account="DUQ1")])

    def _boom(_ib, _sym):
        raise RuntimeError("cancel api down")

    client = _ib_client_with(fake_ib, symbol="MHG")
    client._cancel_resting_orders_for_symbol = _boom  # type: ignore[assignment]

    res = client.place_protective(
        {"symbol": "MHG", "direction": "long", "qty": 3, "sl": 6.04, "tp": 7.02}
    )

    assert res["retCode"] == 0, res
    assert len(fake_ib.placed) == 2  # bracket still armed despite cancel failure


# ---------------------------------------------------------------------------
# 2. execute.close_open_position routing
# ---------------------------------------------------------------------------


def test_close_open_position_routes_ib():
    """IB cfg → IBClient.close called with (symbol, direction, qty); ok=True
    envelope on retCode 0."""
    ib_client = MagicMock(spec=IBClient)
    ib_client.close.return_value = {
        "retCode": 0, "result": {"orderId": "IB-1"}, "retMsg": "OK",
    }
    cfg = {"account_id": "ib_paper", "exchange": "interactive_brokers"}

    res = close_open_position(ib_client, cfg, symbol="MGC", side="long", qty=3)

    ib_client.close.assert_called_once_with("MGC", "long", 3)
    assert res["ok"] is True
    assert res["exchange_order_id"] == "IB-1"


def test_close_open_position_ib_non_zero_retcode_not_ok():
    ib_client = MagicMock(spec=IBClient)
    ib_client.close.return_value = {"retCode": 1, "retMsg": "IBKR rejected"}
    cfg = {"account_id": "ib_paper", "exchange": "ib"}

    res = close_open_position(ib_client, cfg, symbol="MGC", side="long", qty=1)
    assert res["ok"] is False
    assert "rejected" in res["error"]


def test_close_open_position_ib_wrong_client_type_not_ok():
    cfg = {"account_id": "ib_paper", "exchange": "interactive_brokers"}
    res = close_open_position(object(), cfg, symbol="MGC", side="long", qty=1)
    assert res["ok"] is False
    assert "IBClient" in res["error"]


def test_close_open_position_routes_alpaca():
    """Alpaca cfg → AlpacaClient.close(symbol) (native flatten)."""
    from src.units.accounts.alpaca_client import AlpacaClient

    alp_client = MagicMock(spec=AlpacaClient)
    alp_client.close.return_value = {"retCode": 0, "result": {"orderId": "A-1"}}
    cfg = {"account_id": "alpaca_paper", "exchange": "alpaca"}

    res = close_open_position(alp_client, cfg, symbol="SPY", side="long", qty=5)

    alp_client.close.assert_called_once_with("SPY")
    assert res["ok"] is True


def test_close_open_position_alpaca_404_maps_ok():
    """The AlpacaClient maps a 404 (no open position) to retCode 0 — an
    idempotent flatten reads ok here."""
    from src.units.accounts.alpaca_client import AlpacaClient

    alp_client = MagicMock(spec=AlpacaClient)
    alp_client.close.return_value = {"retCode": 0, "result": {"note": "no open position"}}
    cfg = {"account_id": "alpaca_paper", "exchange": "alpaca"}

    res = close_open_position(alp_client, cfg, symbol="QQQ", side="short", qty=1)
    assert res["ok"] is True


def test_close_open_position_alpaca_error_not_ok():
    from src.units.accounts.alpaca_client import AlpacaClient

    alp_client = MagicMock(spec=AlpacaClient)
    alp_client.close.return_value = {"retCode": 422, "retMsg": "bad symbol"}
    cfg = {"account_id": "alpaca_paper", "exchange": "alpaca"}

    res = close_open_position(alp_client, cfg, symbol="ZZZZ", side="long", qty=1)
    assert res["ok"] is False
    assert "bad symbol" in res["error"]


# --- Bybit byte-unchanged ---------------------------------------------------


class _StubBybit:
    def __init__(self, *, ret_code=0, ret_msg="OK", order_id="CLOSE-1"):
        self._ret_code = ret_code
        self._ret_msg = ret_msg
        self._order_id = order_id
        self.place_order_calls = []

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        return {
            "retCode": self._ret_code, "retMsg": self._ret_msg,
            "result": {"orderId": self._order_id},
        }


def test_close_open_position_bybit_unchanged():
    """Bybit close path is byte-for-byte unchanged: reduce-only Market order,
    Sell on a long, qty stringified, ok envelope."""
    client = _StubBybit(order_id="BYBIT-CLOSE-1")
    cfg = {"exchange": "bybit", "market_type": "linear"}

    res = close_open_position(client, cfg, symbol="BTCUSDT", side="long", qty=0.001)

    assert res["ok"] is True
    assert res["exchange_order_id"] == "BYBIT-CLOSE-1"
    kwargs = client.place_order_calls[0]
    assert kwargs["side"] == "Sell"
    assert kwargs["reduceOnly"] is True
    assert kwargs["qty"] == "0.001"
    assert kwargs["orderType"] == "Market"


def test_close_open_position_unknown_exchange_unsupported():
    """An exchange with no close wiring is refused (the unsupported-exchange
    branch). OANDA was wired in S2 (tests/test_ltmgmt_oanda_wiring.py), so this
    uses a genuinely-unknown exchange to exercise the refusal."""
    res = close_open_position(
        object(), {"exchange": "kraken"}, symbol="XBTUSD", side="long", qty=1,
    )
    assert res["ok"] is False
    assert "kraken" in res["error"]


# ---------------------------------------------------------------------------
# 3. capability map + resolver
# ---------------------------------------------------------------------------


def test_caps_declare_close_for_ib_and_alpaca():
    assert "close" in clients.exchange_management_caps("interactive_brokers")
    assert "close" in clients.exchange_management_caps("ib")
    assert "close" in clients.exchange_management_caps("alpaca")
    # modify was wired in S2 (tests/test_ltmgmt_modify_wiring.py); partial_close
    # is still out of scope.
    assert "partial_close" not in clients.exchange_management_caps("alpaca")
    # OANDA close was wired in S2 (BL-20260616-LTMGMT-OANDA).
    assert "close" in clients.exchange_management_caps("oanda")


def test_account_supports_management_close_true_for_ib():
    assert clients.account_supports_management(
        {"exchange": "interactive_brokers"}, "close"
    ) is True
    assert clients.account_supports_management(
        {"exchange": "alpaca"}, "close"
    ) is True


def test_alpaca_open_positions_caps():
    assert "open_positions" in clients.exchange_management_caps("alpaca")


# ---------------------------------------------------------------------------
# 4. account_open_positions alpaca branch (read-only)
# ---------------------------------------------------------------------------


def test_account_open_positions_alpaca_normalises(monkeypatch):
    fake_alp = MagicMock()
    fake_alp.positions.return_value = [
        {"symbol": "SPY", "side": "buy", "qty": 5.0,
         "avg_price": 500.0, "unrealized_pnl": 12.5},
        {"symbol": "QQQ", "side": "sell", "qty": 0.0,  # flat → dropped
         "avg_price": 0.0, "unrealized_pnl": 0.0},
    ]
    monkeypatch.setattr(clients, "alpaca_client_for", lambda acc: fake_alp)

    out = clients.account_open_positions(
        {"account_id": "alpaca_paper", "exchange": "alpaca", "mode": "live"}
    )
    assert out == [
        {"symbol": "SPY", "side": "long", "size": 5.0,
         "entry_price": 500.0, "unrealised_pnl": 12.5},
    ]


def test_account_open_positions_alpaca_no_creds_returns_none(monkeypatch):
    monkeypatch.setattr(clients, "alpaca_client_for", lambda acc: None)
    out = clients.account_open_positions(
        {"account_id": "alpaca_paper", "exchange": "alpaca", "mode": "live"}
    )
    assert out is None


def test_account_open_positions_alpaca_dry_returns_none(monkeypatch):
    # Never dial a dry account from the read path.
    called = []
    monkeypatch.setattr(
        clients, "alpaca_client_for",
        lambda acc: called.append(1) or MagicMock(),
    )
    out = clients.account_open_positions(
        {"account_id": "alpaca_paper", "exchange": "alpaca", "mode": "dry_run"}
    )
    assert out is None
    assert called == []


# ---------------------------------------------------------------------------
# 5. _send_close_to_exchange reaches a mocked IB / alpaca client
# ---------------------------------------------------------------------------


def test_send_close_to_exchange_ib_ok(monkeypatch):
    ib_client = MagicMock(spec=IBClient)
    cfg = {"account_id": "ib_paper", "exchange": "interactive_brokers",
           "mode": "live", "market_type": "futures"}
    monkeypatch.setattr(om, "_build_account_client", lambda a: (ib_client, cfg))
    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        lambda client, c, *, symbol, side, qty, sl_order_id=None, tp_order_id=None: {
            "ok": True, "exchange_order_id": "IB-9", "error": None,
        },
    )

    res = om._send_close_to_exchange(
        {"account_id": "ib_paper", "symbol": "MGC", "direction": "long",
         "position_size": 3}
    )
    assert res["ok"] is True
    assert res["exchange_order_id"] == "IB-9"


def test_send_close_to_exchange_alpaca_ok(monkeypatch):
    from src.units.accounts.alpaca_client import AlpacaClient

    alp_client = MagicMock(spec=AlpacaClient)
    cfg = {"account_id": "alpaca_paper", "exchange": "alpaca", "mode": "live"}
    monkeypatch.setattr(om, "_build_account_client", lambda a: (alp_client, cfg))
    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        lambda client, c, *, symbol, side, qty, sl_order_id=None, tp_order_id=None: {
            "ok": True, "exchange_order_id": "A-9", "error": None,
        },
    )

    res = om._send_close_to_exchange(
        {"account_id": "alpaca_paper", "symbol": "SPY", "direction": "long",
         "position_size": 5}
    )
    assert res["ok"] is True
    assert res["exchange_order_id"] == "A-9"


def test_send_close_to_exchange_ib_no_client_when_gateway_down(monkeypatch):
    """IB supports close (passes the cap gate) but a down gateway means the
    factory returns None → no_client, NOT a phantom success. The monitor
    leaves the DB row open + retries."""
    cfg = {"account_id": "ib_paper", "exchange": "interactive_brokers",
           "mode": "live"}
    monkeypatch.setattr(om, "_build_account_client", lambda a: (None, cfg))

    res = om._send_close_to_exchange(
        {"account_id": "ib_paper", "symbol": "MGC", "direction": "long",
         "position_size": 3}
    )
    assert res["ok"] is False
    assert res["error"] == "no_client"


def test_send_close_to_exchange_ib_dry_run_short_circuit(monkeypatch):
    """A dry IB account short-circuits to skipped:dry_run WITHOUT calling the
    close — the existing dry/live toggle is preserved for the new path."""
    ib_client = MagicMock(spec=IBClient)
    cfg = {"account_id": "ib_live", "exchange": "interactive_brokers",
           "mode": "dry_run"}
    monkeypatch.setattr(om, "_build_account_client", lambda a: (ib_client, cfg))
    called = []
    monkeypatch.setattr(
        "src.units.accounts.execute.close_open_position",
        lambda *a, **kw: called.append(1) or {"ok": True},
    )

    res = om._send_close_to_exchange(
        {"account_id": "ib_live", "symbol": "MGC", "direction": "long",
         "position_size": 3}
    )
    assert res["ok"] is True
    assert res["skipped"] == "dry_run"
    assert called == []


# ---------------------------------------------------------------------------
# 3. Reconciler flat-close cancels resting legs (BL-20260624-MHG-FLIP, item #1)
# ---------------------------------------------------------------------------


def test_cancel_resting_protection_cancels_symbol_legs():
    """Public sweep used after a reconciler flat-close: cancel the symbol's
    resting legs (only that symbol — no cross-symbol)."""
    fake_ib = FakeIB(open_trades=[
        _FakeTrade(MagicMock(orderId=70), _FakeContract(symbol="MHG")),
        _FakeTrade(MagicMock(orderId=71), _FakeContract(symbol="MES")),
    ])
    client = _ib_client_with(fake_ib, symbol="MHG")
    res = client.cancel_resting_protection("MHG")
    assert res["retCode"] == 0, res
    cancelled = [getattr(o, "orderId", None) for o in fake_ib.cancelled]
    assert 70 in cancelled and 71 not in cancelled


def test_cancel_resting_protection_readonly_refuses():
    client = _ib_client_with(FakeIB(), symbol="MHG", readonly=True)
    res = client.cancel_resting_protection("MHG")
    assert res["retCode"] != 0 and "read-only" in res["retMsg"].lower()


def test_after_flat_helper_sweeps_ib(monkeypatch):
    seen = {}

    class _Client:
        def cancel_resting_protection(self, sym):
            seen["sym"] = sym
            return {"retCode": 0}

    monkeypatch.setattr(
        om, "_build_account_client",
        lambda aid: (_Client(), {"exchange": "interactive_brokers"}),
    )
    om._cancel_resting_protection_after_flat("ib_paper", "MHG")
    assert seen["sym"] == "MHG"


def test_after_flat_helper_noop_for_non_ib(monkeypatch):
    class _Client:  # no cancel_resting_protection method (Bybit/Alpaca/OANDA)
        pass

    monkeypatch.setattr(
        om, "_build_account_client",
        lambda aid: (_Client(), {"exchange": "bybit"}),
    )
    # Must not raise — non-IB integrations have no stranded resting legs.
    om._cancel_resting_protection_after_flat("bybit_2", "BTCUSDT")


def test_after_flat_helper_handles_no_client(monkeypatch):
    monkeypatch.setattr(om, "_build_account_client", lambda aid: (None, None))
    om._cancel_resting_protection_after_flat("x", "MHG")  # no raise
    # Also tolerant of missing args.
    om._cancel_resting_protection_after_flat(None, None)
