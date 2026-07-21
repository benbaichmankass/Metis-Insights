"""S2 — wire IB + Alpaca SL/TP modify (trailing-stop) to the verdict path.

Ref: docs/audits/live-trade-management-contract-2026-06-16.md § Design plan §2
+ Decision 2 (modify follows close within P3); backlog BL-20260616-LTMGMT-MODIFY.

This is the live-order-path change: a strategy's SL/TP **modify** verdict now
reaches IB + Alpaca instead of failing ``unsupported_op:modify`` every tick
(the MGC #2597 trailing-stop no-op). No kill-switch (baseline correctness,
Prime Directive).

These tests pin:
  1. ``IBClient.modify_protective`` — cancels the resting protective legs for
     the symbol then re-arms a fresh OCA pair at the supplied levels; readonly
     refuses; a connect failure returns retCode!=0 (never raises).
  2. ``AlpacaClient.modify_protective`` — PATCHes the resting stop leg's
     stop_price (sl) / limit leg's limit_price (tp); only the changed leg is
     touched; a read failure / missing leg returns retCode!=0.
  3. ``execute.modify_open_order`` routes IB / alpaca to the right client AND
     merges cur_sl/cur_tp for the IB re-arm; the Bybit branch stays
     byte-unchanged (only sl/tp, set_trading_stop); OANDA still unsupported.
  4. ``EXCHANGE_MANAGEMENT_CAPS`` declares ``modify`` for IB + alpaca.
  5. ``_send_modify_to_exchange`` forwards side/qty/cur_sl/cur_tp and reaches a
     mocked IB / alpaca client; dry-run short-circuits.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from src.runtime import order_monitor as om
from src.units.accounts import clients
from src.units.accounts.alpaca_client import AlpacaClient
from src.units.accounts.execute import modify_open_order
from src.units.accounts.ib_client import IBClient, get_ib_client


# ---------------------------------------------------------------------------
# Fake ib_insync surface (mirrors tests/test_p3_close_wiring.py)
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
        self.transmit = True
        self.account = None
        self.tif = ""
        self.ocaGroup = ""
        self.ocaType = 0


class _FakeStatus:
    def __init__(self, status="Submitted"):
        self.status = status
        self.filled = 0.0
        self.avgFillPrice = 0.0


class _FakeTrade:
    def __init__(self, order, contract, status="Submitted"):
        self.order = order
        self.contract = contract
        self.orderStatus = _FakeStatus(status)
        self.log = []


class _FakeClient:
    def __init__(self):
        self._req = 1000

    def getReqId(self):
        self._req += 1
        return self._req


class FakeIB:
    def __init__(self, *, open_trades=None, connect_raises=None):
        self._connected = False
        self.client = _FakeClient()
        self._open_trades = open_trades or []
        self._connect_raises = connect_raises
        self.placed = []     # (contract, order)
        self.cancelled = []   # orders cancelled

    def connect(self, host, port, clientId, timeout=10.0, readonly=False):
        if self._connect_raises:
            raise self._connect_raises
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

    def openTrades(self):
        return list(self._open_trades)

    def trades(self):
        return list(self._open_trades)

    def placeOrder(self, contract, order):
        self.placed.append((contract, order))
        return _FakeTrade(order, contract, status="Submitted")

    def cancelOrder(self, order):
        self.cancelled.append(order)

    def sleep(self, _t=0):
        return None


@pytest.fixture(autouse=True)
def fake_ib_module(monkeypatch):
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
# 1. IBClient.modify_protective
# ---------------------------------------------------------------------------


def test_ib_modify_cancels_resting_then_rearms_both_legs():
    """A trailing modify on a long MGC: cancel the two resting MGC protective
    legs, then place a fresh OCA pair (stop + limit) at the merged levels."""
    fake_ib = FakeIB(
        open_trades=[
            _FakeTrade(MagicMock(orderId=10), _FakeContract(symbol="MGC")),
            _FakeTrade(MagicMock(orderId=11), _FakeContract(symbol="MGC")),
        ],
    )
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.modify_protective(
        {"symbol": "MGC", "direction": "long", "qty": 3, "sl": 1990.0, "tp": 2100.0}
    )

    assert res["retCode"] == 0, res
    # Both resting MGC legs cancelled first.
    assert len(fake_ib.cancelled) == 2
    # Fresh OCA pair re-armed: a SELL stop + a SELL limit (reverse of long).
    assert len(fake_ib.placed) == 2
    actions = {o.action for _c, o in fake_ib.placed}
    assert actions == {"SELL"}
    # OCA grouped + GTC (re-arm shape identical to place_protective).
    assert all(o.tif == "GTC" for _c, o in fake_ib.placed)
    assert len({o.ocaGroup for _c, o in fake_ib.placed}) == 1


def test_ib_modify_only_cancels_matching_symbol():
    fake_ib = FakeIB(
        open_trades=[
            _FakeTrade(MagicMock(orderId=40), _FakeContract(symbol="MGC")),
            _FakeTrade(MagicMock(orderId=41), _FakeContract(symbol="MES")),
        ],
    )
    client = _ib_client_with(fake_ib, symbol="MGC")

    client.modify_protective(
        {"symbol": "MGC", "direction": "long", "qty": 1, "sl": 1990.0, "tp": 2100.0}
    )

    cancelled_ids = [getattr(o, "orderId", None) for o in fake_ib.cancelled]
    assert 40 in cancelled_ids
    assert 41 not in cancelled_ids


def test_ib_modify_single_leg_when_only_sl():
    """Only sl supplied (no tp) → re-arm a single stop leg."""
    fake_ib = FakeIB(open_trades=[])
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.modify_protective(
        {"symbol": "MGC", "direction": "short", "qty": 2, "sl": 2200.0, "tp": None}
    )

    assert res["retCode"] == 0
    assert len(fake_ib.placed) == 1
    _c, order = fake_ib.placed[0]
    assert order.action == "BUY"  # reverse of short


def test_ib_modify_readonly_refuses():
    fake_ib = FakeIB(open_trades=[])
    client = _ib_client_with(fake_ib, symbol="MGC", readonly=True)

    res = client.modify_protective(
        {"symbol": "MGC", "direction": "long", "qty": 1, "sl": 1990.0}
    )
    assert res["retCode"] != 0
    assert "read-only" in res["retMsg"].lower()
    assert fake_ib.placed == []


def test_ib_modify_connect_failure_returns_not_ok_never_raises():
    from src.units.accounts.ib_client import IBConnectionError

    fake_ib = FakeIB(open_trades=[], connect_raises=IBConnectionError("gw down"))
    client = _ib_client_with(fake_ib, symbol="MGC")

    res = client.modify_protective(
        {"symbol": "MGC", "direction": "long", "qty": 1, "sl": 1990.0, "tp": 2100.0}
    )
    assert res["retCode"] != 0
    assert "connect" in res["retMsg"].lower()
    assert fake_ib.placed == []


# ---------------------------------------------------------------------------
# 2. AlpacaClient.modify_protective
# ---------------------------------------------------------------------------


def _alp(monkeypatch, *, requests=None):
    """Build an AlpacaClient whose _request is a recording stub.

    *requests* maps (method, path_prefix) → response env; the stub records
    every call into ``client.calls``.
    """
    client = AlpacaClient(api_key="k", api_secret="s", env="paper")
    calls = []

    def _stub(method, path, json_body=None):
        calls.append((method, path, json_body))
        for (m, prefix), resp in (requests or {}).items():
            if method == m and path.startswith(prefix):
                return resp
        return {"retCode": 0, "result": {}}

    client._request = _stub  # type: ignore[method-assign]
    client.calls = calls  # type: ignore[attr-defined]
    return client


def test_alpaca_modify_patches_stop_and_limit_legs():
    open_orders = {"retCode": 0, "result": [
        {"id": "leg-stop", "symbol": "SPY", "type": "stop"},
        {"id": "leg-tp", "symbol": "SPY", "type": "limit"},
    ]}
    client = _alp(monkeypatch=None, requests={("GET", "/v2/orders"): open_orders})

    res = client.modify_protective("SPY", sl=495.0, tp=520.0)

    assert res["retCode"] == 0, res
    patched = res["result"]["patched"]
    assert set(patched) == {"leg-stop", "leg-tp"}
    # Stop leg got stop_price, limit leg got limit_price.
    patches = {c[1]: c[2] for c in client.calls if c[0] == "PATCH"}
    assert patches["/v2/orders/leg-stop"] == {"stop_price": "495.00"}
    assert patches["/v2/orders/leg-tp"] == {"limit_price": "520.00"}


def test_alpaca_modify_only_changed_leg_touched():
    """A trailing-SL-only modify must patch the stop leg and leave the TP
    limit leg alone."""
    open_orders = {"retCode": 0, "result": [
        {"id": "leg-stop", "symbol": "SPY", "type": "stop"},
        {"id": "leg-tp", "symbol": "SPY", "type": "limit"},
    ]}
    client = _alp(monkeypatch=None, requests={("GET", "/v2/orders"): open_orders})

    res = client.modify_protective("SPY", sl=495.0)

    assert res["retCode"] == 0
    assert res["result"]["patched"] == ["leg-stop"]
    patched_ids = [c[1] for c in client.calls if c[0] == "PATCH"]
    assert patched_ids == ["/v2/orders/leg-stop"]  # TP leg untouched


def test_alpaca_modify_read_failure_not_ok():
    client = _alp(
        monkeypatch=None,
        requests={("GET", "/v2/orders"): {"retCode": 500, "retMsg": "boom"}},
    )
    res = client.modify_protective("SPY", sl=495.0)
    assert res["retCode"] != 0
    assert "open orders" in res["retMsg"]
    # Never attempted a PATCH on a read failure.
    assert [c for c in client.calls if c[0] == "PATCH"] == []


def test_alpaca_modify_no_matching_leg_not_ok():
    client = _alp(
        monkeypatch=None,
        requests={("GET", "/v2/orders"): {"retCode": 0, "result": []}},
    )
    res = client.modify_protective("SPY", sl=495.0)
    assert res["retCode"] != 0
    assert "no matching" in res["retMsg"].lower()


def test_alpaca_modify_no_levels_not_ok():
    client = _alp(monkeypatch=None, requests={})
    res = client.modify_protective("SPY")
    assert res["retCode"] != 0
    # Didn't even read open orders.
    assert client.calls == []


def test_alpaca_modify_patch_failure_not_ok():
    open_orders = {"retCode": 0, "result": [
        {"id": "leg-stop", "symbol": "SPY", "type": "stop"},
    ]}
    client = _alp(monkeypatch=None, requests={
        ("GET", "/v2/orders"): open_orders,
        ("PATCH", "/v2/orders/leg-stop"): {"retCode": 422, "retMsg": "bad price"},
    })
    res = client.modify_protective("SPY", sl=495.0)
    assert res["retCode"] != 0
    assert "leg-stop" in res["retMsg"]


# ---------------------------------------------------------------------------
# 3. execute.modify_open_order routing + cur_* merge + Bybit unchanged
# ---------------------------------------------------------------------------


def test_modify_open_order_routes_ib_merges_cur_levels():
    """Only tp changed → IB re-arm gets the new tp AND the current sl
    (cur_sl) so the stop leg isn't dropped."""
    ib_client = MagicMock(spec=IBClient)
    ib_client.modify_protective.return_value = {
        "retCode": 0, "result": {"orderId": "IB-1"}, "retMsg": "OK",
    }
    cfg = {"account_id": "ib_paper", "exchange": "interactive_brokers"}

    res = modify_open_order(
        ib_client, cfg, symbol="MGC", tp=2100.0, sl=None,
        side="long", qty=3, cur_sl=1990.0, cur_tp=2050.0,
    )

    assert res["ok"] is True
    arg = ib_client.modify_protective.call_args[0][0]
    assert arg["sl"] == 1990.0   # from cur_sl (unchanged leg preserved)
    assert arg["tp"] == 2100.0   # the verdict's new tp
    assert arg["direction"] == "long"
    assert arg["qty"] == 3


def test_modify_open_order_ib_no_effective_levels_not_ok():
    """A non-None-but-non-positive level with no current fallback → nothing to
    re-arm; refuse without dialing IB (the early no-sl-or-tp guard only catches
    both-None, so tp=0.0 reaches the IB branch)."""
    ib_client = MagicMock(spec=IBClient)
    cfg = {"account_id": "ib_paper", "exchange": "ib"}
    res = modify_open_order(
        ib_client, cfg, symbol="MGC", sl=None, tp=0.0, side="long", qty=1,
        cur_sl=None, cur_tp=None,
    )
    assert res["ok"] is False
    assert "effective" in res["error"]
    ib_client.modify_protective.assert_not_called()


def test_modify_open_order_ib_wrong_client_type_not_ok():
    cfg = {"account_id": "ib_paper", "exchange": "interactive_brokers"}
    res = modify_open_order(object(), cfg, symbol="MGC", sl=1990.0, tp=2100.0,
                            side="long", qty=1)
    assert res["ok"] is False
    assert "IBClient" in res["error"]


def test_modify_open_order_ib_non_zero_retcode_not_ok():
    ib_client = MagicMock(spec=IBClient)
    ib_client.modify_protective.return_value = {"retCode": 1, "retMsg": "IBKR rejected"}
    cfg = {"account_id": "ib_paper", "exchange": "interactive_brokers"}
    res = modify_open_order(ib_client, cfg, symbol="MGC", sl=1990.0,
                            side="long", qty=1, cur_tp=2100.0)
    assert res["ok"] is False
    assert "rejected" in res["error"]


def test_modify_open_order_routes_alpaca():
    alp_client = MagicMock(spec=AlpacaClient)
    alp_client.modify_protective.return_value = {
        "retCode": 0, "result": {"orderId": "leg-stop", "patched": ["leg-stop"]},
    }
    cfg = {"account_id": "alpaca_paper", "exchange": "alpaca"}

    res = modify_open_order(alp_client, cfg, symbol="SPY", sl=495.0)

    alp_client.modify_protective.assert_called_once_with("SPY", sl=495.0, tp=None)
    assert res["ok"] is True


def test_modify_open_order_alpaca_error_not_ok():
    alp_client = MagicMock(spec=AlpacaClient)
    alp_client.modify_protective.return_value = {"retCode": 1, "retMsg": "no matching leg"}
    cfg = {"account_id": "alpaca_paper", "exchange": "alpaca"}
    res = modify_open_order(alp_client, cfg, symbol="SPY", tp=520.0)
    assert res["ok"] is False
    assert "matching" in res["error"]


def test_modify_open_order_oanda_still_unsupported():
    res = modify_open_order(object(), {"exchange": "oanda"}, symbol="EUR_USD",
                            sl=1.05, tp=1.10)
    assert res["ok"] is False
    assert "oanda" in res["error"]


# --- Bybit byte-unchanged ---------------------------------------------------


class _StubBybit:
    def __init__(self, *, ret_code=0, ret_msg="OK"):
        self._ret_code = ret_code
        self._ret_msg = ret_msg
        self.set_trading_stop_calls = []

    def set_trading_stop(self, **kwargs):
        self.set_trading_stop_calls.append(kwargs)
        return {"retCode": self._ret_code, "retMsg": self._ret_msg}

    # get_tick_size path uses get_instruments_info on the real client;
    # execute.get_tick_size is monkeypatched in the bybit-unchanged test.


def test_modify_open_order_bybit_unchanged(monkeypatch):
    """Bybit path uses set_trading_stop with ONLY the passed sl/tp; the new
    side/qty/cur_* kwargs are ignored (byte-for-byte unchanged)."""
    monkeypatch.setattr(
        "src.units.accounts.execute.get_tick_size", lambda c, s, cat: 0.1
    )
    monkeypatch.setattr(
        "src.units.accounts.execute.quantize_price", lambda p, t: p
    )
    client = _StubBybit()
    cfg = {"exchange": "bybit", "market_type": "linear"}

    res = modify_open_order(
        client, cfg, symbol="BTCUSDT", sl=49500.0,
        # Extra S2 kwargs that must NOT leak into the Bybit call:
        side="long", qty=0.01, cur_sl=49000.0, cur_tp=51000.0,
    )

    assert res["ok"] is True
    kwargs = client.set_trading_stop_calls[0]
    # Only the changed leg (sl) is set — tp NOT sent, cur_* NOT merged in.
    assert kwargs["stopLoss"] == 49500.0
    assert "takeProfit" not in kwargs
    assert kwargs["category"] == "linear"
    assert kwargs["symbol"] == "BTCUSDT"


# ---------------------------------------------------------------------------
# 4. capability map
# ---------------------------------------------------------------------------


def test_caps_declare_modify_for_ib_and_alpaca():
    assert "modify" in clients.exchange_management_caps("interactive_brokers")
    assert "modify" in clients.exchange_management_caps("ib")
    assert "modify" in clients.exchange_management_caps("alpaca")
    # OANDA still has no modify.
    assert "modify" not in clients.exchange_management_caps("oanda")


# ---------------------------------------------------------------------------
# 5. _send_modify_to_exchange forwards side/qty/cur_* + reaches IB/alpaca
# ---------------------------------------------------------------------------


def test_send_modify_to_exchange_ib_forwards_context(monkeypatch):
    ib_client = MagicMock(spec=IBClient)
    cfg = {"account_id": "ib_paper", "exchange": "interactive_brokers",
           "mode": "live", "market_type": "futures"}
    monkeypatch.setattr(om, "_build_account_client", lambda a: (ib_client, cfg))
    captured = {}

    def _modify(client, c, *, symbol, sl=None, tp=None,
                side=None, qty=None, cur_sl=None, cur_tp=None,
                sl_order_id=None, tp_order_id=None):
        captured.update(
            symbol=symbol, sl=sl, tp=tp, side=side, qty=qty,
            cur_sl=cur_sl, cur_tp=cur_tp,
        )
        return {"ok": True, "error": None}

    monkeypatch.setattr("src.units.accounts.execute.modify_open_order", _modify)

    res = om._send_modify_to_exchange(
        {"account_id": "ib_paper", "symbol": "MGC", "direction": "long",
         "position_size": 3},
        sl=1990.0, side="long", qty=3.0, cur_sl=1980.0, cur_tp=2100.0,
    )
    assert res["ok"] is True
    assert captured["symbol"] == "MGC"
    assert captured["sl"] == 1990.0
    assert captured["side"] == "long"
    assert captured["qty"] == 3.0
    assert captured["cur_sl"] == 1980.0
    assert captured["cur_tp"] == 2100.0


def test_send_modify_to_exchange_alpaca_ok(monkeypatch):
    alp_client = MagicMock(spec=AlpacaClient)
    cfg = {"account_id": "alpaca_paper", "exchange": "alpaca", "mode": "live"}
    monkeypatch.setattr(om, "_build_account_client", lambda a: (alp_client, cfg))
    monkeypatch.setattr(
        "src.units.accounts.execute.modify_open_order",
        lambda client, c, **kw: {"ok": True, "error": None},
    )
    res = om._send_modify_to_exchange(
        {"account_id": "alpaca_paper", "symbol": "SPY", "direction": "long",
         "position_size": 5},
        tp=520.0,
    )
    assert res["ok"] is True


def test_send_modify_to_exchange_ib_dry_run_short_circuit(monkeypatch):
    ib_client = MagicMock(spec=IBClient)
    cfg = {"account_id": "ib_live", "exchange": "interactive_brokers",
           "mode": "dry_run"}
    monkeypatch.setattr(om, "_build_account_client", lambda a: (ib_client, cfg))
    called = []
    monkeypatch.setattr(
        "src.units.accounts.execute.modify_open_order",
        lambda *a, **kw: called.append(1) or {"ok": True},
    )
    res = om._send_modify_to_exchange(
        {"account_id": "ib_live", "symbol": "MGC", "direction": "long",
         "position_size": 3},
        sl=1990.0,
    )
    assert res["ok"] is True
    assert res["skipped"] == "dry_run"
    assert called == []
