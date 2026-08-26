"""IB broker-side protection verification (BL-20260709-IB-BROKER-PROTECTION-
UNVERIFIED).

An IB futures/ETF position whose broker OCA bracket was never placed, got
cancelled, or was dropped during a Gateway breaker-flap keeps its journal SL/TP
— so it is broker-naked yet invisible to the DB-driven ``_check_naked_positions``
(the 2026-07-09 MGC monitor-blind incident: the alert claimed "Broker SL/TP
backstop still holds" with no way to VERIFY it). These tests cover the new IB
verification + re-arm path, mirroring the Alpaca one:

* ``IBClient.has_protective_orders`` — True / False / None(read-fail), reading
  ACCOUNT-WIDE (``reqAllOpenOrders``) not this client's ``openTrades()`` alone.
* ``_check_broker_naked_ib_positions`` — the cadence-gated monitor sweep that
  re-arms a broker-naked IB position via the shared ``_attempt_naked_autoprotect``.

Broker acceptance of the GTC OCA re-arm is the existing ``place_protective``
coverage; the sandbox can't reach a live Gateway.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.runtime import order_monitor as om
from src.units.accounts.ib_client import IBClient, IBConnectionError


# --------------------------------------------------------------- client unit
class _Contract:
    def __init__(self, symbol):
        self.symbol = symbol


class _Order:
    def __init__(self, order_type):
        self.orderType = order_type


class _Trade:
    def __init__(self, symbol, order_type):
        self.contract = _Contract(symbol)
        self.order = _Order(order_type)


class _FakeIB:
    """Minimal ib_insync stub for has_protective_orders (sync fallback path)."""

    def __init__(self, trades, req_raises=False):
        self._trades = trades
        self._req_raises = req_raises
        self.req_all_called = 0

    def reqAllOpenOrders(self):
        self.req_all_called += 1
        if self._req_raises:
            raise RuntimeError("gateway wedged — openOrderEnd never arrived")
        return []

    def openTrades(self):
        return list(self._trades)


def _ib(monkeypatch, fake):
    """An IBClient whose connect() returns *fake* (bypasses the socket flow)."""
    client = IBClient(port=4002, client_id=497, account="DUQ", symbol="MHG",
                      _ib_factory=lambda: fake)
    monkeypatch.setattr(client, "connect", lambda: fake)
    return client


def test_has_protective_orders_true_on_resting_stop(monkeypatch):
    fake = _FakeIB([_Trade("MHG", "STP")])
    c = _ib(monkeypatch, fake)
    assert c.has_protective_orders("MHG") is True
    # The verdict MUST come from an account-wide refresh, not openTrades() alone.
    assert fake.req_all_called == 1


def test_has_protective_orders_true_on_resting_limit(monkeypatch):
    fake = _FakeIB([_Trade("MHG", "LMT")])
    c = _ib(monkeypatch, fake)
    assert c.has_protective_orders("MHG") is True


def test_has_protective_orders_true_on_stop_limit(monkeypatch):
    fake = _FakeIB([_Trade("MES", "STP LMT")])
    c = _ib(monkeypatch, fake)
    assert c.has_protective_orders("MES") is True


def test_has_protective_orders_false_when_only_market_order(monkeypatch):
    # A resting MKT order is not protective; the position is broker-naked.
    fake = _FakeIB([_Trade("MHG", "MKT")])
    c = _ib(monkeypatch, fake)
    assert c.has_protective_orders("MHG") is False


def test_has_protective_orders_false_when_no_matching_symbol(monkeypatch):
    # A protective leg exists — but on a DIFFERENT symbol.
    fake = _FakeIB([_Trade("MES", "STP")])
    c = _ib(monkeypatch, fake)
    assert c.has_protective_orders("MHG") is False


def test_has_protective_orders_none_on_account_read_failure(monkeypatch):
    # reqAllOpenOrders raises → cannot CONFIRM account-wide state → None (skip).
    fake = _FakeIB([_Trade("MHG", "STP")], req_raises=True)
    c = _ib(monkeypatch, fake)
    assert c.has_protective_orders("MHG") is None


def test_has_protective_orders_none_on_connect_failure(monkeypatch):
    fake = _FakeIB([])
    c = IBClient(port=4002, client_id=497, account="DUQ", symbol="MHG",
                 _ib_factory=lambda: fake)

    def _boom():
        raise IBConnectionError("breaker open")

    monkeypatch.setattr(c, "connect", _boom)
    assert c.has_protective_orders("MHG") is None


# ----------------------------------------------------------- monitor sweep
class _FakeDB:
    def __init__(self, path):
        self.path = str(path)
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT,
                direction TEXT, position_size REAL, stop_loss REAL,
                take_profit_1 REAL, created_at TEXT, notes TEXT,
                status TEXT, is_backtest INTEGER DEFAULT 0
            );
            CREATE TABLE order_packages (
                order_package_id TEXT, symbol TEXT, direction TEXT,
                sl REAL, tp REAL, created_at TEXT
            );
            """
        )
        conn.commit()
        conn.close()

    def connect(self):
        return sqlite3.connect(self.path)


def _insert(db, **kw):
    conn = sqlite3.connect(db.path)
    conn.execute(
        "INSERT INTO trades (id,account_id,symbol,direction,position_size,"
        "stop_loss,take_profit_1,created_at,status,is_backtest) "
        "VALUES (:id,:account_id,:symbol,:direction,:position_size,:stop_loss,"
        ":take_profit_1,:created_at,:status,0)", kw,
    )
    conn.commit()
    conn.close()


class _FakeIBClient:
    """Fake IB client for the broker-naked sweep.

    ``protected`` is the legacy boolean the fixture is written in terms of; the
    sweep now reads QUANTITY (``protection_coverage``, BL-20260814-IB-PROTECTION
    -BOOLEAN-NOT-QUANTITY), so it is translated into the equivalent coverage:
    protected → fully covered, naked → zero covered, ``None`` → could-not-read.
    ``size`` defaults to the position size these fixtures insert.
    """

    def __init__(self, protected, size=2.0):
        self._protected = protected
        self._size = size
        self.queried = []
        self.rearmed = []

    def has_protective_orders(self, symbol):
        self.queried.append(symbol)
        return self._protected

    def protection_coverage(self, symbol):
        self.queried.append(symbol)
        if self._protected is None:
            return None  # read failure ⇒ caller must skip
        return {
            "size": self._size,
            "covered_qty": self._size if self._protected else 0.0,
            "legs": 1 if self._protected else 0,
            "unknown_qty_legs": 0,
            "oca_groups": {},
            "source": "resting_legs",
        }

    def place_protective(self, order):
        self.rearmed.append(order)
        return {"retCode": 0, "result": {"orderId": "oca-x"}}


_IB_ACCTS = [{"account_id": "ib_paper", "exchange": "interactive_brokers"}]


@pytest.fixture(autouse=True)
def _reset_cadence():
    # Force the cadence gate open for each sweep test (a large negative latch
    # guarantees the window has elapsed regardless of the process's monotonic
    # clock), and clear the active-close set.
    om._LAST_IB_BROKER_NAKED_CHECK_MONO = -1e9
    om._TICK_ACTIVE_CLOSE_AT.clear()
    yield
    om._TICK_ACTIVE_CLOSE_AT.clear()


def _patch_accounts(monkeypatch, fake):
    monkeypatch.setattr(
        "src.bot.data_loaders.list_accounts", lambda: list(_IB_ACCTS)
    )
    monkeypatch.setattr(
        "src.units.accounts.clients.ib_client_for",
        lambda acc, readonly=False, **kw: fake,
    )


def test_ib_sweep_rearms_broker_naked(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="ib_paper", symbol="MHG", direction="long",
            position_size=1, stop_loss=4.0, take_profit_1=6.0,
            created_at="2026-06-25T00:00:00+00:00", status="open")
    fake = _FakeIBClient(protected=False)
    _patch_accounts(monkeypatch, fake)

    summary = om._check_broker_naked_ib_positions(db)
    assert summary["checked"] == 1
    assert summary["broker_naked"] == 1
    assert summary["rearmed"] == 1
    assert fake.queried == ["MHG"]
    assert fake.rearmed and fake.rearmed[0]["symbol"] == "MHG"
    assert fake.rearmed[0]["sl"] == 4.0 and fake.rearmed[0]["tp"] == 6.0


def test_ib_sweep_normalises_month_coded_symbol(tmp_path, monkeypatch):
    # An adopted-orphan carries the specific contract month (MHGN6); the broker
    # read + re-arm must resolve it to the base root (MHG).
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="ib_paper", symbol="MHGN6", direction="short",
            position_size=2, stop_loss=8.0, take_profit_1=5.0,
            created_at="2026-06-25T00:00:00+00:00", status="open")
    fake = _FakeIBClient(protected=False)
    _patch_accounts(monkeypatch, fake)

    summary = om._check_broker_naked_ib_positions(db)
    assert summary["rearmed"] == 1
    assert fake.queried == ["MHG"]
    assert fake.rearmed[0]["symbol"] == "MHG"


def test_ib_sweep_skips_protected(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="ib_paper", symbol="MES", direction="long",
            position_size=1, stop_loss=5000.0, take_profit_1=5200.0,
            created_at="2026-06-25T00:00:00+00:00", status="open")
    fake = _FakeIBClient(protected=True)
    _patch_accounts(monkeypatch, fake)

    summary = om._check_broker_naked_ib_positions(db)
    assert summary["checked"] == 1
    assert summary["broker_naked"] == 0
    assert summary["rearmed"] == 0
    assert fake.rearmed == []


def test_ib_sweep_skips_on_read_failure(tmp_path, monkeypatch):
    # has_protective_orders → None (breaker open / gateway wedged): fail-safe,
    # never re-arm on an unconfirmed read (build constraint 3).
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="ib_paper", symbol="MHG", direction="long",
            position_size=1, stop_loss=4.0, take_profit_1=6.0,
            created_at="2026-06-25T00:00:00+00:00", status="open")
    fake = _FakeIBClient(protected=None)
    _patch_accounts(monkeypatch, fake)

    summary = om._check_broker_naked_ib_positions(db)
    assert summary["checked"] == 1
    assert summary["broker_naked"] == 0
    assert summary["rearmed"] == 0
    assert fake.rearmed == []


def test_ib_sweep_skips_active_close(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="ib_paper", symbol="MHG", direction="long",
            position_size=1, stop_loss=4.0, take_profit_1=6.0,
            created_at="2026-06-25T00:00:00+00:00", status="open")
    fake = _FakeIBClient(protected=False)  # broker-naked, WOULD re-arm normally
    _patch_accounts(monkeypatch, fake)
    om.mark_active_close("ib_paper", "MHG")

    summary = om._check_broker_naked_ib_positions(db)
    # Skipped before the broker read → not counted broker_naked, never re-armed.
    assert summary["broker_naked"] == 0
    assert summary["rearmed"] == 0
    assert fake.queried == []
    assert fake.rearmed == []


def test_ib_sweep_dedupes_read_but_rearms_every_uncovered_sibling(
    tmp_path, monkeypatch
):
    """One broker read per symbol, but EVERY uncovered sibling gets re-armed.

    ⚠️ This test previously asserted ``broker_naked == 1`` / ``rearmed == 1``
    with the comment "only the first re-arm (the symbol is protected after it)"
    — i.e. it encoded the BUG as the expected behaviour. Two naked MHG trades
    on a netted position: the first re-armed, the cached verdict flipped to the
    boolean ``True``, and the second trade was skipped as protected while it had
    no protection at all. A green test asserting exactly the defect is why this
    survived (BL-20260814-IB-PROTECTION-BOOLEAN-NOT-QUANTITY).

    Now the cache holds COVERAGE, and a re-arm credits only the re-armed trade's
    own qty — so the still-uncovered sibling stays visible on the same sweep.
    The read is still deduped (one account-wide call), which was the part of the
    original intent that was correct.
    """
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="ib_paper", symbol="MHG", direction="long",
            position_size=1, stop_loss=4.0, take_profit_1=6.0,
            created_at="2026-06-25T00:00:00+00:00", status="open")
    _insert(db, id=2, account_id="ib_paper", symbol="MHG", direction="long",
            position_size=1, stop_loss=4.1, take_profit_1=6.1,
            created_at="2026-06-25T00:00:00+00:00", status="open")
    # Netted position of 2 contracts (1 per trade), zero covered.
    fake = _FakeIBClient(protected=False, size=2.0)
    _patch_accounts(monkeypatch, fake)

    summary = om._check_broker_naked_ib_positions(db)
    assert summary["checked"] == 2
    assert fake.queried == ["MHG"], "the account-wide read must stay deduped"
    assert summary["broker_naked"] == 2
    assert summary["rearmed"] == 2, (
        "a sibling was left unprotected — the boolean-cache defect is back"
    )


def test_ib_sweep_cadence_gate_skips_second_call(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="ib_paper", symbol="MHG", direction="long",
            position_size=1, stop_loss=4.0, take_profit_1=6.0,
            created_at="2026-06-25T00:00:00+00:00", status="open")
    fake = _FakeIBClient(protected=True)
    _patch_accounts(monkeypatch, fake)

    first = om._check_broker_naked_ib_positions(db)
    assert first["checked"] == 1 and first["skipped"] == 0
    # Immediately again — within the cadence window → skipped, no broker read.
    second = om._check_broker_naked_ib_positions(db)
    assert second["skipped"] == 1
    assert second["checked"] == 0


def test_ib_sweep_disabled_when_interval_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("IB_BROKER_NAKED_CHECK_SECONDS", "0")
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="ib_paper", symbol="MHG", direction="long",
            position_size=1, stop_loss=4.0, take_profit_1=6.0,
            created_at="2026-06-25T00:00:00+00:00", status="open")
    fake = _FakeIBClient(protected=False)
    _patch_accounts(monkeypatch, fake)

    summary = om._check_broker_naked_ib_positions(db)
    assert summary["skipped"] == 1
    assert summary["checked"] == 0
    assert fake.queried == []


def test_ib_sweep_no_ib_accounts_noop(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="bybit_2", symbol="BTCUSDT", direction="long",
            position_size=1, stop_loss=4.0, take_profit_1=6.0,
            created_at="2026-06-25T00:00:00+00:00", status="open")
    monkeypatch.setattr(
        "src.bot.data_loaders.list_accounts",
        lambda: [{"account_id": "bybit_2", "exchange": "bybit"}],
    )
    summary = om._check_broker_naked_ib_positions(db)
    assert summary["checked"] == 0
    assert summary["broker_naked"] == 0


def test_rearm_threads_account_id_into_the_order_dict(tmp_path, monkeypatch):
    """The re-arm must NAME its account, or the stray-group sweep is inert.

    `PROTECTION_STRAY_GROUP_ACCOUNTS` is an allowlist whose empty/unknown case
    means NONE, so an order dict that omits `account_id` can never be
    allowlisted — the sweep would read as armed and cancel nothing. That is the
    "inert but looks live" shape, so the threading is pinned rather than
    assumed.

    ⚠️ This also guards the regression that produced it: `row` is a
    `sqlite3.Row`, which has NO `.get()`. A `row.get("account_id")` here raises
    into `_attempt_naked_autoprotect`'s broad `except` and silently fails EVERY
    naked re-arm — the safety path that re-arms an unprotected live position.
    """
    db = _FakeDB(tmp_path / "j.db")
    _insert(db, id=1, account_id="ib_paper", symbol="MHG", direction="long",
            position_size=1, stop_loss=4.0, take_profit_1=6.0,
            created_at="2026-06-25T00:00:00+00:00", status="open")
    fake = _FakeIBClient(protected=False)
    _patch_accounts(monkeypatch, fake)

    summary = om._check_broker_naked_ib_positions(db)
    assert summary["rearmed"] == 1, "the re-arm itself must still succeed"
    assert fake.rearmed[0]["account_id"] == "ib_paper"
