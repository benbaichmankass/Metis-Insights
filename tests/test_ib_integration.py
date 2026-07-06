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
import time
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
        self.tif = ""  # ib_insync Order default; IBClient.place sets it explicitly


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
        # + the WS-A metals sleeve mgc_pullback_1d (MGC) / mhg_pullback_1d (MHG),
        # added 2026-06-02, execution: live on ib_paper PAPER money.
        assert paper["strategies"] == [
            "turtle_soup", "vwap", "ict_scalp_5m", "mes_trend_long_1d",
            "mgc_pullback_1d", "mhg_pullback_1d", "mgc_trend_1h",
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
            "mgc_pullback_1d", "mhg_pullback_1d", "mgc_trend_1h",
        ]  # + long-only diversifier (2026-06-01) + WS-A metals sleeve (2026-06-02)

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

    def test_place_sets_explicit_tif(self, fake_ib_module):
        # An unset TIF lets IBKR apply the account preset (DAY) and emit the
        # spurious Error 10349 cancel that caused the BL-20260612-001 desync.
        # The market entry must be DAY (fills now); the protective legs must
        # be GTC so they survive past the session for multi-day holds.
        c, fake = _client()
        c.place({"symbol": "MES", "direction": "long", "qty": 2, "sl": 5290.1, "tp": 5320.4})
        by_action = [(o.action, o.tif, o.lmtPrice, o.auxPrice) for (_, o) in fake.placed]
        # every leg carries an explicit, non-empty TIF (never "" → no preset)
        assert all(tif in ("DAY", "GTC") for (_, tif, _, _) in by_action)
        parent = fake.placed[0][1]
        assert parent.tif == "DAY"           # market entry fills immediately
        children = [o for (_, o) in fake.placed[1:]]
        assert children and all(o.tif == "GTC" for o in children)  # protective legs persist

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

    def test_place_protective_long_is_reverse_oca_gtc(self, fake_ib_module):
        # A long position is protected by a SELL stop + SELL limit, OCA-paired,
        # GTC, and crucially NO market parent (never opens/adds a position).
        c, fake = _client()
        resp = c.place_protective(
            {"symbol": "MES", "direction": "long", "qty": 2, "sl": 5290.1, "tp": 5320.4}
        )
        assert resp["retCode"] == 0
        assert len(fake.placed) == 2                      # SL + TP only, no parent
        orders = [o for (_, o) in fake.placed]
        assert all(o.action == "SELL" for o in orders)   # reverse of a long
        assert all(o.tif == "GTC" for o in orders)        # persist past the session
        groups = {o.ocaGroup for o in orders}
        assert len(groups) == 1 and next(iter(groups))    # one shared OCA group
        assert all(o.ocaType == 1 for o in orders)        # cancel-remaining-on-fill
        assert all(o.account == "DUQ325724" for o in orders)
        assert resp["result"]["ocaGroup"] == next(iter(groups))

    def test_place_protective_short_is_buy(self, fake_ib_module):
        c, fake = _client()
        c.place_protective(
            {"symbol": "MES", "direction": "short", "qty": 1, "sl": 5320, "tp": 5280}
        )
        assert all(o.action == "BUY" for (_, o) in fake.placed)  # reverse of a short

    def test_place_protective_one_leg_when_only_sl(self, fake_ib_module):
        c, fake = _client()
        resp = c.place_protective(
            {"symbol": "MES", "direction": "long", "qty": 1, "sl": 5290, "tp": None}
        )
        assert resp["retCode"] == 0
        assert len(fake.placed) == 1

    def test_place_protective_refuses_without_levels(self, fake_ib_module):
        c, fake = _client()
        resp = c.place_protective(
            {"symbol": "MES", "direction": "long", "qty": 1, "sl": None, "tp": None}
        )
        assert resp["retCode"] != 0
        assert not fake.placed                            # nothing transmitted

    def test_place_protective_floors_subcontract_qty(self, fake_ib_module):
        c, fake = _client()
        resp = c.place_protective(
            {"symbol": "MES", "direction": "long", "qty": 0.4, "sl": 5290, "tp": 5320}
        )
        assert resp["retCode"] != 0                       # <1 whole contract → refuse
        assert not fake.placed

    def test_readonly_refuses_place_protective(self, fake_ib_module):
        c, _ = _client(readonly=True)
        with pytest.raises(IBConnectionError):
            c.place_protective(
                {"symbol": "MES", "direction": "long", "qty": 1, "sl": 5290, "tp": 5320}
            )

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


class TestIBIsolationGuardRails:
    """Restart-loop incident (2026-06-05): a logged-out IB Gateway ACCEPTS the
    socket but then hangs every request forever, which used to freeze the whole
    pipeline tick (incl. Bybit) and starve the liveness heartbeat. connect()
    now (a) verifies the session with a hard-bounded liveness probe and (b)
    trips a circuit breaker so a dead gateway fast-fails instead of blocking
    the loop. These tests pin both behaviours.
    """

    def test_connect_failure_trips_breaker(self):
        # A refused socket trips the breaker; the next connect() fast-fails
        # WITHOUT building/dialling a new IB at all.
        builds = {"n": 0}

        class Boom(FakeIB):
            def connect(self, *a, **k):
                raise OSError("connection refused")

        def factory():
            builds["n"] += 1
            return Boom()

        c = IBClient(port=7497, client_id=1, _ib_factory=factory)
        with pytest.raises(IBConnectionError):
            c.connect()
        assert builds["n"] == 1
        with pytest.raises(IBConnectionError) as ei:
            c.connect()
        assert "circuit breaker OPEN" in str(ei.value)
        # Breaker short-circuited before touching the socket again.
        assert builds["n"] == 1

    def test_breaker_recovers_after_cooldown(self):
        class Boom(FakeIB):
            def connect(self, *a, **k):
                raise OSError("connection refused")

        c = IBClient(port=7497, client_id=2, _ib_factory=lambda: Boom())
        with pytest.raises(IBConnectionError):
            c.connect()
        assert c._breaker_open_until > 0
        # Simulate the cooldown elapsing — connect() must attempt again.
        c._breaker_open_until = 0.0
        with pytest.raises(IBConnectionError) as ei:
            c.connect()
        # Reached the socket again (real connect error), not the breaker arm.
        assert "circuit breaker OPEN" not in str(ei.value)

    def test_liveness_probe_timeout_is_bounded_and_trips_breaker(self, monkeypatch):
        import src.units.accounts.ib_client as mod

        monkeypatch.setattr(mod, "_IB_PROBE_TIMEOUT_S", 0.2)
        monkeypatch.setattr(mod, "_IB_PROBE_RETRY_GAP_S", 0.05)

        class HangIB(FakeIB):
            async def reqCurrentTimeAsync(self):
                import asyncio

                await asyncio.sleep(30)  # never answers within the probe bound

        inj = types.ModuleType("ib_insync")
        inj.IB = HangIB
        monkeypatch.setitem(sys.modules, "ib_insync", inj)

        # No _ib_factory → the real probe path runs. A genuine wedge never
        # answers EITHER of the two bounded attempts, so it's still caught —
        # just with the one retry's grace gap added to the bound.
        c = IBClient(port=7497, client_id=3, account="DUQ325724")
        t0 = time.monotonic()
        with pytest.raises(IBConnectionError) as ei:
            c.connect()
        elapsed = time.monotonic() - t0
        assert "liveness probe" in str(ei.value)
        assert elapsed < 5.0, f"probe was not bounded (took {elapsed:.1f}s)"
        # Breaker is now open — subsequent connect fast-fails.
        with pytest.raises(IBConnectionError) as ei2:
            c.connect()
        assert "circuit breaker OPEN" in str(ei2.value)

    def test_liveness_probe_cold_miss_then_recovers(self, monkeypatch):
        # BL-20260610-009: the first bounded attempt over a freshly-established
        # connection (the cross-host relay's cold-TCP-flow miss) times out, but
        # the gateway is genuinely healthy and answers the retry — connect()
        # must succeed and the breaker must stay closed, instead of condemning
        # a healthy session on a single missed first round-trip.
        import src.units.accounts.ib_client as mod

        monkeypatch.setattr(mod, "_IB_PROBE_TIMEOUT_S", 0.2)
        monkeypatch.setattr(mod, "_IB_PROBE_RETRY_GAP_S", 0.05)

        calls = {"n": 0}

        class ColdThenHealthyIB(FakeIB):
            async def reqCurrentTimeAsync(self):
                import asyncio

                calls["n"] += 1
                if calls["n"] == 1:
                    await asyncio.sleep(30)  # cold-start miss on attempt 1
                return 1_700_000_000  # attempt 2 answers normally

        inj = types.ModuleType("ib_insync")
        inj.IB = ColdThenHealthyIB
        monkeypatch.setitem(sys.modules, "ib_insync", inj)

        c = IBClient(port=7497, client_id=33, account="DUQ325724")
        ib = c.connect()
        assert ib is not None
        assert c.connected is True
        assert calls["n"] == 2
        assert c._breaker_open_until == 0.0
        assert c._breaker_fail_count == 0

    def test_healthy_probe_connects_and_keeps_breaker_closed(self, monkeypatch):
        class HealthyIB(FakeIB):
            async def reqCurrentTimeAsync(self):
                return 1_700_000_000

        inj = types.ModuleType("ib_insync")
        inj.IB = HealthyIB
        monkeypatch.setitem(sys.modules, "ib_insync", inj)

        c = IBClient(port=7497, client_id=4, account="DUQ325724")
        ib = c.connect()
        assert ib is not None
        assert c.connected is True
        assert c._breaker_open_until == 0.0
        assert c._breaker_fail_count == 0

    def test_stub_factory_skips_probe(self):
        # FakeIB has no reqCurrentTimeAsync; with _ib_factory set the probe is
        # skipped entirely so the existing test suite (and real stubs) connect.
        c, _ = _client()
        c.connect()
        assert c.connected is True
        assert c._breaker_open_until == 0.0


class TestAccountWarmup:
    """BL-20260706-IBWARMUP — connect() must BLOCK (bounded) for the FIRST
    real accountSummary/portfolio data to land before declaring success, so
    balance()/positions() never race an empty/never-populated cache and
    misreport "gateway not logged in" on a perfectly healthy gateway.
    Mirrors the shape of ``TestIBIsolationGuardRails``'s liveness-probe
    tests, but for the account/portfolio warm-up added alongside it.
    """

    def test_warmup_waits_for_delayed_data_then_succeeds(self, monkeypatch):
        # A fresh connect whose accountSummary/accountUpdates callbacks
        # answer only after a delay (well inside the bound) must still
        # succeed — connect() waits, it does not falsely condemn a healthy,
        # merely-slow-to-answer gateway.
        import src.units.accounts.ib_client as mod

        monkeypatch.setattr(mod, "_IB_ACCOUNT_WARMUP_TIMEOUT_S", 2.0)
        monkeypatch.setattr(mod, "_IB_PROBE_RETRY_GAP_S", 0.05)

        class DelayedIB(FakeIB):
            async def reqAccountSummaryAsync(self):
                import asyncio

                await asyncio.sleep(0.3)  # arrives well inside the bound
                return None

            async def reqAccountUpdatesAsync(self, account):
                import asyncio

                await asyncio.sleep(0.3)
                return None

        inj = types.ModuleType("ib_insync")
        inj.IB = DelayedIB
        monkeypatch.setitem(sys.modules, "ib_insync", inj)

        c = IBClient(port=7497, client_id=50, account="DUQ325724")
        t0 = time.monotonic()
        ib = c.connect()
        elapsed = time.monotonic() - t0
        assert ib is not None
        assert c.connected is True
        assert c._account_data_ready is True
        assert c._breaker_open_until == 0.0
        assert elapsed < 2.0, f"warm-up did not return promptly (took {elapsed:.1f}s)"

    def test_warmup_never_arrives_is_bounded_and_trips_breaker(self, monkeypatch):
        # A genuinely wedged gateway that never answers reqAccountSummary
        # must still be caught within a bounded time (not hang forever —
        # ib_insync's own RequestTimeout for this call is 0 = unbounded)
        # and must trip the circuit breaker exactly like the liveness probe.
        import src.units.accounts.ib_client as mod

        monkeypatch.setattr(mod, "_IB_ACCOUNT_WARMUP_TIMEOUT_S", 0.2)
        monkeypatch.setattr(mod, "_IB_PROBE_RETRY_GAP_S", 0.05)

        class WedgedIB(FakeIB):
            async def reqAccountSummaryAsync(self):
                import asyncio

                await asyncio.sleep(30)  # never answers within the bound

        inj = types.ModuleType("ib_insync")
        inj.IB = WedgedIB
        monkeypatch.setitem(sys.modules, "ib_insync", inj)

        c = IBClient(port=7497, client_id=51, account="DUQ325724")
        t0 = time.monotonic()
        with pytest.raises(IBConnectionError) as ei:
            c.connect()
        elapsed = time.monotonic() - t0
        assert "account/portfolio data" in str(ei.value)
        assert elapsed < 5.0, f"warm-up was not bounded (took {elapsed:.1f}s)"
        assert c._account_data_ready is False
        # Breaker now open — a subsequent connect fast-fails without
        # touching the socket again.
        with pytest.raises(IBConnectionError) as ei2:
            c.connect()
        assert "circuit breaker OPEN" in str(ei2.value)

    def test_reconnect_after_idle_drop_rewarms(self, monkeypatch):
        # A cached, still-"connected" handle must NOT re-warm on every
        # connect() call (cheap steady-state reads) — but once the socket
        # silently drops (isConnected() flips False, no exception raised,
        # the idle-timeout scenario this fix targets), the NEXT connect()
        # builds a fresh ``ib`` and MUST re-run the warm-up before
        # declaring success, so a stale/empty cache is never trusted.
        import src.units.accounts.ib_client as mod

        monkeypatch.setattr(mod, "_IB_ACCOUNT_WARMUP_TIMEOUT_S", 2.0)
        monkeypatch.setattr(mod, "_IB_PROBE_RETRY_GAP_S", 0.05)

        calls = {"n": 0}

        class ReconnectableIB(FakeIB):
            async def reqAccountSummaryAsync(self):
                calls["n"] += 1
                return None

        inj = types.ModuleType("ib_insync")
        inj.IB = ReconnectableIB
        monkeypatch.setitem(sys.modules, "ib_insync", inj)

        c = IBClient(port=7497, client_id=52, account="DUQ325724")
        ib1 = c.connect()
        assert calls["n"] == 1
        assert c._account_data_ready is True

        # A second connect() on the still-open handle must skip the
        # warm-up entirely (cheap steady-state path).
        ib_again = c.connect()
        assert ib_again is ib1
        assert calls["n"] == 1

        # Simulate a silent idle-timeout drop: the socket is dead but no
        # exception was raised anywhere — isConnected() now reports False.
        ib1._connected = False
        ib2 = c.connect()
        assert ib2 is not ib1
        assert calls["n"] == 2, "reconnect after an idle drop must re-warm"
        assert c._account_data_ready is True


class _FakePositionContract:
    """Minimal contract stand-in carrying the fields positions() reads."""

    def __init__(self, symbol, local_symbol, multiplier):
        self.symbol = symbol
        self.localSymbol = local_symbol
        self.multiplier = multiplier


class _FakePosition:
    """Stand-in for ib_insync's ``Position`` namedtuple (reqPositions())."""

    def __init__(self, account, symbol, local_symbol, avg_cost, position, multiplier="1"):
        self.account = account
        self.contract = _FakePositionContract(symbol, local_symbol, multiplier)
        self.avgCost = avg_cost
        self.position = position


class TestReadonlyAccountUpdatesCollision:
    """BL-20260706-IBACCTUPDATES-COLLISION — a readonly client (diagnostics,
    the dashboard/reconciler read path) must never subscribe to
    ``reqAccountUpdates``: it is a persistent per-account subscription the
    trader's own execution connection already holds, and a second concurrent
    subscriber for the SAME account is the documented IB-API collision that
    left the diag read client's warm-up timing out (8s + 8s retry) while the
    trader's own connection stayed healthy throughout. Readonly clients read
    positions via ``reqPositions()`` instead — a stateless, one-shot request
    safe for any number of concurrent clients.
    """

    def test_readonly_warmup_never_calls_account_updates(self, monkeypatch):
        import src.units.accounts.ib_client as mod

        monkeypatch.setattr(mod, "_IB_ACCOUNT_WARMUP_TIMEOUT_S", 2.0)
        monkeypatch.setattr(mod, "_IB_PROBE_RETRY_GAP_S", 0.05)

        class HangsIfSubscribedIB(FakeIB):
            async def reqAccountSummaryAsync(self):
                return None

            async def reqAccountUpdatesAsync(self, account):
                # A second concurrent subscriber for an account the trader's
                # own connection already holds never gets its data — this
                # simulates that hang. If the readonly warm-up ever calls
                # this, the test times out instead of finishing promptly.
                import asyncio

                await asyncio.sleep(30)

            async def reqPositionsAsync(self):
                return None

        inj = types.ModuleType("ib_insync")
        inj.IB = HangsIfSubscribedIB
        monkeypatch.setitem(sys.modules, "ib_insync", inj)

        c = IBClient(port=4002, client_id=9585, account="DUQ325724", readonly=True)
        t0 = time.monotonic()
        ib = c.connect()
        elapsed = time.monotonic() - t0
        assert ib is not None
        assert c._account_data_ready is True
        assert elapsed < 2.0, (
            f"readonly warm-up took {elapsed:.1f}s — it must never wait on "
            "reqAccountUpdates"
        )

    def test_readonly_positions_uses_req_positions_not_portfolio(self, monkeypatch):
        import src.units.accounts.ib_client as mod

        monkeypatch.setattr(mod, "_IB_ACCOUNT_WARMUP_TIMEOUT_S", 2.0)
        monkeypatch.setattr(mod, "_IB_PROBE_RETRY_GAP_S", 0.05)

        class ReqPositionsIB(FakeIB):
            def __init__(self):
                super().__init__()
                self.portfolio_called = False

            async def reqAccountSummaryAsync(self):
                return None

            async def reqPositionsAsync(self):
                return None

            def positions(self):
                return [
                    # MHG: avgCost 15989.72 = 6.39588 × 2500 multiplier.
                    _FakePosition("DUQ325724", "MHG", "MHGN6", 15989.72, 3.0, "2500"),
                    _FakePosition("DUQ325724", "MHG", "MHGN6", 0.0, 0.0, "2500"),  # flat
                    _FakePosition("OTHERACCT", "ES", "ESZ5", 100.0, 2.0, "50"),   # other acct
                ]

            def portfolio(self):
                self.portfolio_called = True
                raise AssertionError("readonly positions() must not call portfolio()")

        inj = types.ModuleType("ib_insync")
        inj.IB = ReqPositionsIB
        monkeypatch.setitem(sys.modules, "ib_insync", inj)

        c = IBClient(port=4002, client_id=9585, account="DUQ325724", readonly=True)
        out = c.positions()
        assert out == [{
            "symbol": "MHG", "side": "long", "size": 3.0,
            "entry_price": pytest.approx(6.39588, abs=1e-4),
            "unrealised_pnl": None,  # Position carries no unrealizedPNL
        }]

    def test_non_readonly_positions_unaffected_still_uses_portfolio(self, monkeypatch):
        # The trader's own execution connection keeps using portfolio()/
        # reqAccountUpdates exactly as before — only the readonly diagnostic
        # path changes.
        c, fake = _client(readonly=False)

        class _Portfolio:
            account = "DUQ325724"
            contract = _FakePositionContract("MES", "MESM6", "5")
            averageCost = 26500.0
            position = 1.0
            unrealizedPNL = 12.5

        fake.portfolio = lambda: [_Portfolio()]
        out = c.positions()
        assert out == [{
            "symbol": "MES", "side": "long", "size": 1.0,
            "entry_price": 5300.0, "unrealised_pnl": 12.5,
        }]


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


# ---------------------------------------------------------------------------
# BL-20260611-001 — whole-contract floor + bounded post-place rejection check
# ---------------------------------------------------------------------------


class TestPlaceWholeContractFloor:
    """IBKR futures fill whole contracts only; place() floors defensively
    and refuses sub-1-contract orders instead of transmitting an order
    that can never fill (the trade #2531 failure mode)."""

    def test_fractional_qty_is_floored(self, fake_ib_module):
        c, fake = _client()
        resp = c.place({
            "symbol": "MHG", "direction": "long", "qty": 3.643,
            "sl": 5.9415, "tp": 6.892,
        })
        assert resp["retCode"] == 0
        # Parent + both children carry the floored integer qty.
        assert [o.totalQuantity for (_, o) in fake.placed] == [3.0, 3.0, 3.0]

    def test_sub_one_contract_is_refused_nothing_transmitted(self, fake_ib_module):
        c, fake = _client()
        resp = c.place({
            "symbol": "MES", "direction": "long", "qty": 0.4,
            "sl": 5290.0, "tp": 5320.0,
        })
        assert resp["retCode"] == 1
        assert "below 1 whole contract" in resp["retMsg"]
        assert fake.placed == []

    def test_integer_qty_unchanged(self, fake_ib_module):
        c, fake = _client()
        resp = c.place({
            "symbol": "MES", "direction": "long", "qty": 2,
            "sl": 5290.0, "tp": 5320.0,
        })
        assert resp["retCode"] == 0
        assert [o.totalQuantity for (_, o) in fake.placed] == [2.0, 2.0, 2.0]


class _RejectingIB(FakeIB):
    """FakeIB whose parent order lands in a terminal dead state, the way
    ib_insync surfaces an immediate IBKR rejection."""

    def __init__(self, status="Inactive", message="invalid order size"):
        super().__init__()
        self._reject_status = status
        self._reject_message = message

    def placeOrder(self, contract, order):
        trade = _FakeTrade(order)
        if not self.placed:  # parent only; children irrelevant here
            trade.orderStatus.status = self._reject_status
            entry = types.SimpleNamespace(message=self._reject_message)
            trade.log = [entry]
        self.placed.append((contract, order))
        return trade


class TestPlacePostPlaceRejectionCheck:
    """place() pumps the event loop briefly after transmitting and surfaces
    an immediately-rejected parent as retCode 1 (pre-fix it returned
    retCode 0 and the journal row was orphaned 30 min later)."""

    def _rejecting_client(self, fake):
        return IBClient(
            host="127.0.0.1", port=7497, client_id=497,
            account="DUQ325724", _ib_factory=lambda: fake,
        )

    def test_rejected_parent_surfaces_as_retcode_1(self, fake_ib_module):
        fake = _RejectingIB(status="Inactive", message="invalid order size")
        c = self._rejecting_client(fake)
        resp = c.place({
            "symbol": "MES", "direction": "long", "qty": 2,
            "sl": 5290.0, "tp": 5320.0,
        })
        assert resp["retCode"] == 1
        assert "status=Inactive" in resp["retMsg"]
        assert "invalid order size" in resp["retMsg"]

    def test_cancelled_parent_surfaces_as_retcode_1(self, fake_ib_module):
        fake = _RejectingIB(status="Cancelled", message="")
        c = self._rejecting_client(fake)
        resp = c.place({
            "symbol": "MES", "direction": "long", "qty": 1,
            "sl": 5290.0, "tp": 5320.0,
        })
        assert resp["retCode"] == 1
        assert "status=Cancelled" in resp["retMsg"]

    def test_confirm_disabled_restores_fire_and_forget(self, fake_ib_module, monkeypatch):
        monkeypatch.setenv("IB_PLACE_CONFIRM_S", "0")
        fake = _RejectingIB(status="Inactive")
        c = self._rejecting_client(fake)
        resp = c.place({
            "symbol": "MES", "direction": "long", "qty": 1,
            "sl": 5290.0, "tp": 5320.0,
        })
        assert resp["retCode"] == 0

    def test_accepted_parent_returns_ok(self, fake_ib_module):
        # Default FakeIB parent status is "Submitted" → accepted instantly.
        c, fake = _client()
        resp = c.place({
            "symbol": "MES", "direction": "long", "qty": 2,
            "sl": 5290.0, "tp": 5320.0,
        })
        assert resp["retCode"] == 0

    def test_pending_at_deadline_treated_as_accepted(self, fake_ib_module, monkeypatch):
        # A parent stuck in PendingSubmit must NOT block past the bound nor
        # be reported as a failure (slow-gateway tolerance).
        monkeypatch.setenv("IB_PLACE_CONFIRM_S", "0.05")
        fake = _RejectingIB(status="PendingSubmit")
        c = self._rejecting_client(fake)
        resp = c.place({
            "symbol": "MES", "direction": "long", "qty": 1,
            "sl": 5290.0, "tp": 5320.0,
        })
        assert resp["retCode"] == 0
