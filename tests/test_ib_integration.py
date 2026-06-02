"""Tests for the Interactive Brokers (MES) integration.

Covers the connection-only wiring requested 2026-05-21:
  * config/accounts.yaml — ib_paper (mode: live) + ib_live (mode: dry_run)
  * AccountProfile mapping for IB accounts
  * load_accounts() — IB accounts load configured=True with ib_* attrs
  * ib_client_for() factory — host/port/clientId/account resolution + env
  * IBClient — construction validation, connect, MES contract, bracket
    placement, status/balance, readonly guard, tick rounding
  * execute._submit_order — IB branch routing + error vocabulary
  * execute._fetch_balance — IB NetLiquidation branch
  * connection registry caching

ib_insync is NOT a hard test dependency: a fake ``ib_insync`` module is
injected into sys.modules and a fake ``IB`` is passed via the ``_ib_factory``
seam, so the full place()/balance() path runs without the real package.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

from src.core.account_profile import AccountProfile
from src.units.accounts import load_accounts
from src.units.accounts.clients import ib_client_for
from src.units.accounts.ib_client import (
    IBClient,
    IBConnectionError,
    _round_to_tick,
    get_ib_client,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACCOUNTS_YAML = os.path.join(REPO_ROOT, "config", "accounts.yaml")


# ---------------------------------------------------------------------------
# Fake ib_insync surface
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


class _FakeRow:
    def __init__(self, tag, value, currency="USD"):
        self.tag = tag
        self.value = value
        self.currency = currency


class _FakeClient:
    def __init__(self):
        self._req = 1000

    def getReqId(self):
        self._req += 1
        return self._req

    def serverVersion(self):
        return 176


class _FakeStatus:
    def __init__(self, status="Submitted", filled=0.0, avg=0.0):
        self.status = status
        self.filled = filled
        self.avgFillPrice = avg


class _FakeTrade:
    def __init__(self, order, status="Submitted", filled=0.0, avg=0.0):
        self.order = order
        self.orderStatus = _FakeStatus(status, filled, avg)


class FakeIB:
    """Stand-in for ib_insync.IB exercising the methods IBClient uses."""

    def __init__(self):
        self._connected = False
        self.client = _FakeClient()
        self.placed = []
        self.cancelled = []
        self.connect_args = None
        self._net_liq = 52345.67

    def connect(self, host, port, clientId, timeout=10.0, readonly=False):
        self.connect_args = dict(
            host=host, port=port, clientId=clientId, timeout=timeout, readonly=readonly
        )
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

    def placeOrder(self, contract, order):
        trade = _FakeTrade(order)
        self.placed.append((contract, order))
        return trade

    def openTrades(self):
        return [_FakeTrade(o) for (_, o) in self.placed]

    def trades(self):
        return [_FakeTrade(o, status="Filled", filled=1.0, avg=5300.0) for (_, o) in self.placed]

    def cancelOrder(self, order):
        self.cancelled.append(order)

    def accountSummary(self, account=None):
        return [
            _FakeRow("NetLiquidation", str(self._net_liq)),
            _FakeRow("AvailableFunds", "40000.0"),
        ]

    def managedAccounts(self):
        return ["DUQ325724"]

    def sleep(self, _t):
        return None


@pytest.fixture
def fake_ib_module(monkeypatch):
    """Inject a fake ``ib_insync`` module so in-method imports resolve."""
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


def _client(account="DUQ325724", port=7497, readonly=False):
    fake = FakeIB()
    c = IBClient(
        host="127.0.0.1",
        port=port,
        client_id=497,
        account=account,
        readonly=readonly,
        _ib_factory=lambda: fake,
    )
    return c, fake


# ---------------------------------------------------------------------------
# config/accounts.yaml
# ---------------------------------------------------------------------------


class TestAccountsYaml:
    def test_ib_accounts_present_with_requested_modes(self):
        import yaml

        with open(ACCOUNTS_YAML, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        accts = raw["accounts"]
        assert "ib_paper" in accts
        assert "ib_live" in accts

        paper = accts["ib_paper"]
        assert paper["exchange"] == "interactive_brokers"
        assert paper["mode"] == "live"          # paper → live (paper money)
        assert paper["ib_port"] == 4002         # host 4002 → gnzsnz socat relay 4004
        assert paper["ib_account"] == "DUQ325724"
        # turtle_soup/vwap/ict_scalp_5m on MES + mes_trend_long_1d (daily long-only
        # diversifier, wired 2026-06-01, PROMOTED to execution: live 2026-06-02 —
        # executes on ib_paper PAPER money; collects live-MES trend data via real
        # paper execution. Real-money IB (ib_live) stays a separate Tier-3 gate).
        assert paper["strategies"] == [
            "turtle_soup", "vwap", "ict_scalp_5m", "mes_trend_long_1d",
        ]

        live = accts["ib_live"]
        assert live["exchange"] == "interactive_brokers"
        assert live["mode"] == "dry_run"         # live → dry_run (held safe)
        assert live["ib_port"] == 7496
        assert live["ib_account"] == "U25907316"
        assert live["strategies"] == []

    def test_ib_accounts_have_no_api_key_env(self):
        import yaml

        with open(ACCOUNTS_YAML, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        for name in ("ib_paper", "ib_live"):
            assert "api_key_env" not in raw["accounts"][name]


# ---------------------------------------------------------------------------
# AccountProfile mapping
# ---------------------------------------------------------------------------


class TestAccountProfileIB:
    def test_ib_paper_profile(self):
        p = AccountProfile.from_dict("ib_paper", {"exchange": "interactive_brokers", "mode": "live"})
        assert p.is_ib is True
        assert p.dry_run is False
        assert p.account_type == "ib_live"  # exchange=ib + live → ib_live type label

    def test_ib_live_dry_run_profile(self):
        p = AccountProfile.from_dict("ib_live", {"exchange": "interactive_brokers", "mode": "dry_run"})
        assert p.is_ib is True
        assert p.dry_run is True
        assert p.account_type == "ib_paper"  # exchange=ib + dry → ib_paper type label


# ---------------------------------------------------------------------------
# load_accounts
# ---------------------------------------------------------------------------


class TestLoadAccounts:
    def test_ib_accounts_load_configured(self):
        accounts = {a.name: a for a in load_accounts(ACCOUNTS_YAML)}
        assert "ib_paper" in accounts
        assert "ib_live" in accounts

        paper = accounts["ib_paper"]
        assert paper.exchange == "interactive_brokers"
        assert paper.configured is True       # IB has no api_key_env → always configured
        assert paper.dry_run is False         # mode: live
        assert paper.ib_port == 4002          # host 4002 → gnzsnz socat relay 4004
        assert paper.ib_account == "DUQ325724"
        assert paper.ib_client_id == 497
        assert paper.strategies == [
            "turtle_soup", "vwap", "ict_scalp_5m", "mes_trend_long_1d",
        ]  # + shadow daily long-only diversifier (2026-06-01)

        live = accounts["ib_live"]
        assert live.dry_run is True           # mode: dry_run
        assert live.ib_port == 7496
        assert live.ib_account == "U25907316"


# ---------------------------------------------------------------------------
# ib_client_for factory
# ---------------------------------------------------------------------------


class TestIBClientFor:
    def test_builds_client_from_account(self):
        acct = {
            "account_id": "ib_paper",
            "exchange": "interactive_brokers",
            "ib_host": "127.0.0.1",
            "ib_port": 7497,
            "ib_account": "DUQ325724",
            "ib_client_id": 497,
        }
        c = ib_client_for(acct)
        assert isinstance(c, IBClient)
        assert c.host == "127.0.0.1"
        assert c.port == 7497
        assert c.client_id == 497
        assert c.account == "DUQ325724"

    def test_returns_none_for_non_ib(self):
        assert ib_client_for({"exchange": "bybit"}) is None

    def test_returns_none_when_port_missing(self):
        assert ib_client_for({"exchange": "interactive_brokers"}) is None

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("IB_HOST", "10.0.0.5")
        monkeypatch.setenv("IB_PORT", "4002")
        monkeypatch.setenv("IB_ACCOUNT", "DU999")
        monkeypatch.setenv("IB_CLIENT_ID", "42")
        c = ib_client_for({"exchange": "ib"})
        assert c.host == "10.0.0.5"
        assert c.port == 4002
        assert c.account == "DU999"
        assert c.client_id == 42

    def test_client_id_defaults_per_port(self, monkeypatch):
        monkeypatch.delenv("IB_CLIENT_ID", raising=False)
        c = ib_client_for({"exchange": "interactive_brokers", "ib_port": 7497})
        assert c.client_id == 497  # 7497 % 1000


# ---------------------------------------------------------------------------
# IBClient
# ---------------------------------------------------------------------------


class TestIBClient:
    def test_bad_port_raises(self):
        with pytest.raises(IBConnectionError):
            IBClient(port="not-a-port", client_id=1)

    def test_bad_client_id_raises(self):
        with pytest.raises(IBConnectionError):
            IBClient(port=7497, client_id="x")

    def test_connect_passes_params(self):
        c, fake = _client()
        c.connect()
        assert c.connected is True
        assert fake.connect_args["host"] == "127.0.0.1"
        assert fake.connect_args["port"] == 7497
        assert fake.connect_args["clientId"] == 497

    def test_connect_failure_raises_ib_error(self):
        class Boom(FakeIB):
            def connect(self, *a, **k):
                raise OSError("connection refused")

        c = IBClient(port=7497, client_id=1, _ib_factory=lambda: Boom())
        with pytest.raises(IBConnectionError):
            c.connect()

    def test_place_builds_bracket(self, fake_ib_module):
        c, fake = _client()
        resp = c.place({"symbol": "MES", "direction": "long", "qty": 2, "sl": 5290.1, "tp": 5320.4})
        assert resp["retCode"] == 0
        assert resp["result"]["orderId"]
        # parent + TP + SL = 3 orders, all stamped with the account code.
        assert len(fake.placed) == 3
        actions = [o.action for (_, o) in fake.placed]
        assert actions[0] == "BUY"           # market entry
        assert "SELL" in actions[1:]         # bracket children reverse side
        for (_, o) in fake.placed:
            assert o.account == "DUQ325724"

    def test_place_rounds_prices_to_tick(self, fake_ib_module):
        c, fake = _client()
        c.place({"symbol": "MES", "direction": "short", "qty": 1, "sl": 5300.13, "tp": 5280.06})
        # children carry the rounded prices (nearest 0.25)
        prices = [o.lmtPrice for (_, o) in fake.placed if o.lmtPrice is not None]
        for p in prices:
            assert abs((p / 0.25) - round(p / 0.25)) < 1e-9

    def test_place_rejects_non_mes(self, fake_ib_module):
        c, _ = _client()
        with pytest.raises(ValueError):
            c.place({"symbol": "BTCUSDT", "direction": "long", "qty": 1, "sl": 1, "tp": 2})

    def test_readonly_refuses_place(self, fake_ib_module):
        c, _ = _client(readonly=True)
        with pytest.raises(IBConnectionError):
            c.place({"symbol": "MES", "direction": "long", "qty": 1, "sl": 5290, "tp": 5320})

    def test_balance(self, fake_ib_module):
        c, _ = _client()
        bal = c.balance()
        assert bal["net_liquidation"] == pytest.approx(52345.67)
        assert bal["available_funds"] == pytest.approx(40000.0)
        assert bal["currency"] == "USD"

    def test_status_found_and_not_found(self, fake_ib_module):
        c, _ = _client()
        resp = c.place({"symbol": "MES", "direction": "long", "qty": 1, "sl": 5290, "tp": 5320})
        oid = resp["result"]["orderId"]
        st = c.status(oid)
        assert st["status"] == "Filled"
        missing = c.status("does-not-exist")
        assert missing["status"] == "not_found"

    def test_self_test_snapshot(self, fake_ib_module):
        c, _ = _client()
        snap = c.self_test()
        assert snap["connected"] is True
        assert snap["server_version"] == 176
        assert "DUQ325724" in snap["accounts"]
        assert snap["account"] == "…5724"      # masked

    def test_fingerprint_masks(self):
        c, _ = _client()
        assert c.fingerprint() == "5724"

    def test_round_to_tick(self):
        assert _round_to_tick(5300.13) == 5300.25
        assert _round_to_tick(5300.06) == 5300.0
        assert _round_to_tick(5300.0) == 5300.0


# ---------------------------------------------------------------------------
# Event-loop resilience (asyncio.run poisoning)
# ---------------------------------------------------------------------------


class TestEventLoopResilience:
    """ib_insync resolves the event loop afresh on every sync call. Code
    elsewhere in the process runs ``asyncio.run(...)`` (Telegram alerts),
    which sets the thread's current loop to None on exit — poisoning the next
    ib_insync call with "There is no current event loop in thread
    'MainThread'". IBClient keeps a persistent loop and re-asserts it on every
    connect() (including the cached path), so a request after a poison still
    resolves the loop the IB is bound to.
    """

    def test_connect_binds_persistent_loop(self):
        import asyncio

        c, _ = _client()
        c.connect()
        assert c._loop is not None
        assert not c._loop.is_closed()
        # the bound loop is the thread's current loop
        assert asyncio.get_event_loop_policy().get_event_loop() is c._loop

    def test_cached_connect_reasserts_same_loop_after_poison(self):
        import asyncio

        c, fake = _client()
        c.connect()
        loop1 = c._loop
        assert loop1 is not None

        # Poison exactly as alert_manager's asyncio.run(...) does: on exit it
        # calls set_event_loop(None), so the policy now raises.
        asyncio.run(asyncio.sleep(0))
        with pytest.raises(RuntimeError):
            asyncio.get_event_loop_policy().get_event_loop()

        # Cached connect (fake still "connected") must re-assert the SAME loop —
        # never a fresh one, since the IB transport lives on loop1.
        assert fake.isConnected()
        c.connect()
        assert c._loop is loop1
        assert asyncio.get_event_loop_policy().get_event_loop() is loop1

    def test_get_ohlcv_survives_loop_poison(self, fake_ib_module):
        import asyncio

        from src.exchange.ib_connector import IBMarketData

        c, fake = _client()

        # Make the fake return one bar so get_ohlcv reaches reqHistoricalData.
        class _Bar:
            date = "2026-05-22 08:00:00"
            open = high = low = close = 5300.0
            volume = 10

        fake.reqMarketDataType = lambda *_a, **_k: None
        fake.reqHistoricalData = lambda *a, **k: [_Bar()]
        fake.qualifyContracts = lambda *cs: [setattr(x, "conId", 1) or x for x in cs]

        md = IBMarketData(port=7497, client_id=498, account="DUQ325724", _client=c)
        # Poison before the call — get_ohlcv must re-assert and still return data.
        asyncio.run(asyncio.sleep(0))
        df = md.get_ohlcv("MES", "5m", limit=1)
        assert df is not None
        assert len(df) == 1
        assert df.iloc[0]["close"] == 5300.0


# ---------------------------------------------------------------------------
# Connection registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_get_ib_client_caches(self):
        a = get_ib_client(host="127.0.0.1", port=7497, client_id=1)
        b = get_ib_client(host="127.0.0.1", port=7497, client_id=1)
        assert a is b
        c = get_ib_client(host="127.0.0.1", port=7496, client_id=2)
        assert c is not a

    def test_factory_bypasses_cache(self):
        a = get_ib_client(port=7497, client_id=9, _ib_factory=lambda: FakeIB())
        b = get_ib_client(port=7497, client_id=9, _ib_factory=lambda: FakeIB())
        assert a is not b


# ---------------------------------------------------------------------------
# execute._submit_order / _fetch_balance IB branches
# ---------------------------------------------------------------------------


class TestExecutorBranch:
    def test_submit_order_routes_to_ib(self, fake_ib_module):
        from src.units.accounts.execute import _submit_order

        c, fake = _client()
        order = {
            "symbol": "MES",
            "side": "Buy",
            "direction": "long",
            "entry": 5300.0,
            "sl": 5290.0,
            "tp": 5320.0,
            "qty": 1,
            "strategy": "demo",
        }
        trade_id = _submit_order(c, order, {"exchange": "interactive_brokers", "account_id": "ib_paper"})
        assert trade_id
        assert len(fake.placed) == 3

    def test_submit_order_missing_client_raises(self):
        from src.units.accounts.execute import _submit_order

        with pytest.raises(IBConnectionError):
            _submit_order(None, {"symbol": "MES", "qty": 1}, {"exchange": "interactive_brokers", "account_id": "ib_paper"})

    def test_submit_order_wrong_client_type_raises(self):
        from src.units.accounts.execute import _submit_order

        with pytest.raises(TypeError):
            _submit_order(object(), {"symbol": "MES", "qty": 1}, {"exchange": "ib"})

    def test_fetch_balance_ib_branch(self, fake_ib_module):
        from src.units.accounts.execute import _fetch_balance

        c, _ = _client()
        bal = _fetch_balance(c, {"exchange": "interactive_brokers"})
        assert bal == pytest.approx(52345.67)
