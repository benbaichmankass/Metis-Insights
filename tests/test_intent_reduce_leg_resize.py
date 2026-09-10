"""MI-227 / full-system-audit **F-36** — an intent-reduce must resize the
reduced row's OWN tracked protective leg.

THE DEFECT, TRANSCRIBED FROM THE AUDIT (not re-measured here)
-------------------------------------------------------------
``apply_intent_reduce_partial_close`` decremented ``trades.position_size`` and
never touched the venue leg that row's ``sl_order_id`` points at. MEASURED LIVE
2026-09-09T16:47Z on ``bybit_1``/ADAUSDT:

    trade 5479  position_size 79227.0  -> venue leg 79227.0   correct
    trade 5417  position_size    628.0 -> venue leg 63943.0   101.82x
    journal 79227.0 + 628.0 = 79855.0 == the exchange position, EXACTLY
    63943 - 63315 = 628  == 5417's post-reduce size

The symbol reconciled perfectly while a single row was over-covered 101-fold,
which is why every symbol-level check passed. That shape is planted verbatim in
``test_the_live_f36_shape_resizes_the_oversized_row`` below, and separately in
``scripts/ops/system_invariants.py``'s self-test as the control for
``INV-PROTECT-LEG-MATCHES-ROW``.

WHAT IS DELIBERATELY NOT TESTED, BECAUSE IT IS NOT BUILT
--------------------------------------------------------
Nothing here cancels a leg. The operator's 2026-09-09 approval
(``WO-20260909-DECISION-INTENT-REDUCE-ORPHANS-A-PROTECTIVE-LEG``, option
``resize_and_invariant``) covers the resize and the detector, and explicitly
withholds any cancel — the one prior attempt at remediating an over-covered
book by cancelling cancelled the leg that MATCHED the journal
(``BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG``).
``test_fully_consumed_parent_leaves_its_leg_and_says_so`` pins that the
residual is REPORTED rather than cleared.
"""
from __future__ import annotations

import sqlite3

import pytest


_SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    symbol TEXT,
    direction TEXT,
    entry_price REAL,
    exit_price REAL,
    stop_loss REAL,
    take_profit_1 REAL,
    position_size REAL,
    setup_type TEXT,
    entry_reason TEXT,
    exit_reason TEXT,
    pnl REAL,
    pnl_percent REAL,
    status TEXT DEFAULT 'open',
    is_backtest INTEGER DEFAULT 0,
    account_class TEXT,
    is_demo INTEGER DEFAULT 0,
    strategy_name TEXT,
    account_id TEXT,
    notes TEXT,
    order_package_id TEXT,
    closed_at TEXT,
    broker_order_id TEXT,
    sl_order_id TEXT,
    tp_order_id TEXT
);
"""

# The schema a journal that predates BL-20260721-BYBIT2-XRP-TPSL-LEGCAP has:
# no leg-id columns at all. The reduce must still work there.
_SCHEMA_NO_LEG_COLUMNS = _SCHEMA.replace(
    "    broker_order_id TEXT,\n    sl_order_id TEXT,\n    tp_order_id TEXT\n",
    "    broker_order_id TEXT\n",
)


class FakeDB:
    def __init__(self, path: str, schema: str = _SCHEMA):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(schema)
        self._conn.commit()

    def connect(self):
        conn = self._conn

        class _NoCloseProxy:
            def execute(self, *a, **k):
                return conn.execute(*a, **k)

            def cursor(self):
                return conn.cursor()

            def commit(self):
                return conn.commit()

            def close(self):
                pass

        return _NoCloseProxy()

    def insert_trade(self, trade_data):
        cols = ", ".join(trade_data.keys())
        ph = ", ".join("?" for _ in trade_data)
        cur = self._conn.cursor()
        cur.execute(f"INSERT INTO trades ({cols}) VALUES ({ph})",
                    list(trade_data.values()))
        self._conn.commit()
        return cur.lastrowid

    def update_trade(self, trade_id, updates):
        row = dict(updates or {})
        if not row:
            return 0
        assignments = ", ".join(f"{k} = ?" for k in row)
        cur = self._conn.cursor()
        cur.execute(f"UPDATE trades SET {assignments} WHERE id = ?",
                    list(row.values()) + [int(trade_id)])
        self._conn.commit()
        return cur.rowcount

    def rows(self):
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM trades ORDER BY id ASC").fetchall()]


class FakeBybit:
    """Records every amend_order call. ``ret_code`` drives accept/refuse."""

    def __init__(self, ret_code=0, raises=False):
        self.amend_calls: list = []
        self.read_calls: list = []
        self.mutating_calls: list = []
        self._ret_code = ret_code
        self._raises = raises

    def amend_order(self, **kwargs):
        self.amend_calls.append(kwargs)
        if self._raises:
            raise RuntimeError("venue exploded")
        return {"retCode": self._ret_code,
                "retMsg": "OK" if self._ret_code == 0 else "Qty invalid"}

    def get_instruments_info(self, **kwargs):
        """The one READ the resize path is allowed to make.

        It reaches the venue only through ``qty_legalize.snap_artifact_qty``,
        the single seam permitted to resolve a lot rule
        (``qty-legalization-guard``). Recorded separately from the mutating
        calls so a test can tell a lookup from an order.
        """
        self.read_calls.append(("get_instruments_info", kwargs))
        return {"result": {"list": [
            {"lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1"}}]}}

    # Any MUTATING venue call other than amend_order is a hard failure, so a
    # test can assert that no cancel / set_trading_stop / place_order path was
    # taken. A resize must never become a cancel-and-replace: that is the
    # BL-20260820 defect.
    def __getattr__(self, name):
        def _recorder(**kwargs):
            self.mutating_calls.append((name, kwargs))
            raise AssertionError(
                f"unexpected venue call {name}({kwargs}) — the resize path "
                f"must only ever call amend_order")
        return _recorder


BYBIT_CFG = {"account_id": "bybit_1", "exchange": "bybit",
             "account_class": "paper", "market_type": "linear"}


@pytest.fixture()
def db(tmp_path):
    return FakeDB(str(tmp_path / "trades.db"))


@pytest.fixture(autouse=True)
def _partial_mode(monkeypatch):
    """Every test here runs under BYBIT_TPSL_MODE=partial, the live value.

    ``_bybit_category`` is stubbed to ``linear`` so no venue instrument lookup
    is attempted, and ``snap_artifact_qty`` is left REAL — the wire string this
    path sends is part of what is under test (MI-139).
    """
    monkeypatch.setenv("BYBIT_TPSL_MODE", "partial")
    from src.units.accounts import execute as execmod
    monkeypatch.setattr(execmod, "_bybit_category", lambda cfg: "linear")


def _seed(db, *, size, sl=None, tp=None, account="bybit_1",
          symbol="ADAUSDT", direction="long"):
    return db.insert_trade({
        "timestamp": "t", "symbol": symbol, "direction": direction,
        "entry_price": 1.0, "position_size": size, "status": "open",
        "is_backtest": 0, "account_id": account, "strategy_name": "vwap",
        "sl_order_id": sl, "tp_order_id": tp,
    })


# ---------------------------------------------------------------- the defect
def test_the_live_f36_shape_resizes_the_oversized_row(db):
    """The audit's own numbers. 5417 shrinks 63943 -> 628 and its leg follows.

    ⚠️ The sibling 5479 is seeded too and must be left ALONE: the whole point
    of doing this per-ROW is that another trade's leg is not reachable from
    here. A symbol-wide resize would pass a naive version of this test and be
    the BL-20260820 defect.
    """
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    sibling = _seed(db, size=79227.0, sl="sib-sl")          # trade 5479
    reduced = _seed(db, size=63943.0, sl="t5417-sl")        # trade 5417
    client = FakeBybit()

    res = apply_intent_reduce_partial_close(
        db, account_id="bybit_1", symbol="ADAUSDT",
        reduce_direction="short", reduce_qty=63315.0,
        fill_price=0.8, closed_at_iso="2026-09-04T12:27:53+00:00",
        exchange_client=client, account_cfg=BYBIT_CFG,
    )

    rows = {r["id"]: r for r in db.rows()}
    # FIFO consumes the OLDEST first, so the sibling absorbs it here. Assert on
    # whichever row actually shrank rather than assuming — the property under
    # test is "the row that shrank had ITS OWN leg resized", not an ordering.
    shrunk = [a for a in res["allocations"] if a["closed"] is False]
    assert len(shrunk) == 1
    pid = shrunk[0]["parent_id"]
    new_size = rows[pid]["position_size"]
    assert new_size == pytest.approx(79227.0 - 63315.0)

    assert len(client.amend_calls) == 1
    call = client.amend_calls[0]
    assert call["orderId"] == rows[pid]["sl_order_id"]
    assert call["symbol"] == "ADAUSDT"
    assert float(call["qty"]) == pytest.approx(new_size)
    # A resize, not a re-placement: the trigger price is untouched.
    assert "triggerPrice" not in call
    assert client.mutating_calls == []

    assert shrunk[0]["leg_resize"]["sl"]["state"] == "resized"
    # The row that did NOT shrink kept its leg id and got no venue call.
    untouched = reduced if pid == sibling else sibling
    assert rows[untouched]["position_size"] == pytest.approx(
        63943.0 if untouched == reduced else 79227.0)


def test_the_leg_that_is_resized_is_the_shrunk_row_s_own(db):
    """Explicit per-ROW control: two open rows, only one is reduced."""
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    first = _seed(db, size=100.0, sl="first-sl")
    second = _seed(db, size=50.0, sl="second-sl")
    client = FakeBybit()

    apply_intent_reduce_partial_close(
        db, account_id="bybit_1", symbol="ADAUSDT",
        reduce_direction="short", reduce_qty=40.0,
        fill_price=1.0, closed_at_iso="now",
        exchange_client=client, account_cfg=BYBIT_CFG,
    )
    assert [c["orderId"] for c in client.amend_calls] == ["first-sl"]
    rows = {r["id"]: r for r in db.rows()}
    assert rows[first]["position_size"] == pytest.approx(60.0)
    assert rows[second]["position_size"] == pytest.approx(50.0)


def test_both_legs_are_resized_when_both_are_tracked(db):
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    _seed(db, size=100.0, sl="the-sl", tp="the-tp")
    client = FakeBybit()
    res = apply_intent_reduce_partial_close(
        db, account_id="bybit_1", symbol="ADAUSDT",
        reduce_direction="short", reduce_qty=25.0,
        fill_price=1.0, closed_at_iso="now",
        exchange_client=client, account_cfg=BYBIT_CFG,
    )
    assert sorted(c["orderId"] for c in client.amend_calls) == ["the-sl", "the-tp"]
    assert all(float(c["qty"]) == pytest.approx(75.0) for c in client.amend_calls)
    lr = res["allocations"][0]["leg_resize"]
    assert lr["sl"]["state"] == "resized"
    assert lr["tp"]["state"] == "resized"


# ------------------------------------------------- the residual, not repaired
def test_fully_consumed_parent_leaves_its_leg_and_says_so(db):
    """A closed row's leg is NOT cancelled — it is COUNTED.

    Clearing it would be a cancel, which the approval withholds. The contract
    is that the residual is returned so it is countable rather than invisible.
    """
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    closed_row = _seed(db, size=30.0, sl="doomed-sl", tp="doomed-tp")
    survivor = _seed(db, size=70.0, sl="survivor-sl")
    client = FakeBybit()

    res = apply_intent_reduce_partial_close(
        db, account_id="bybit_1", symbol="ADAUSDT",
        reduce_direction="short", reduce_qty=50.0,
        fill_price=1.0, closed_at_iso="now",
        exchange_client=client, account_cfg=BYBIT_CFG,
    )
    rows = {r["id"]: r for r in db.rows()}
    assert rows[closed_row]["status"] == "closed"
    assert rows[survivor]["position_size"] == pytest.approx(50.0)

    # Only the SURVIVOR's leg was amended. Nothing was cancelled.
    assert [c["orderId"] for c in client.amend_calls] == ["survivor-sl"]
    assert client.mutating_calls == []

    assert res["legs_left_resting_on_closed_rows"] == [
        {"parent_id": closed_row, "sl_order_id": "doomed-sl",
         "tp_order_id": "doomed-tp"},
    ]


def test_closed_row_with_no_tracked_leg_is_not_listed(db):
    """The residual list counts LEGS, not closures — an untracked closed row
    contributes nothing, so the count cannot be inflated into a fake problem."""
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    _seed(db, size=30.0)          # no leg ids
    _seed(db, size=70.0, sl="survivor-sl")
    res = apply_intent_reduce_partial_close(
        db, account_id="bybit_1", symbol="ADAUSDT",
        reduce_direction="short", reduce_qty=50.0,
        fill_price=1.0, closed_at_iso="now",
        exchange_client=FakeBybit(), account_cfg=BYBIT_CFG,
    )
    assert res["legs_left_resting_on_closed_rows"] == []


# ------------------------------------------------------- the five leg states
def test_no_tracked_id_is_its_own_state_and_places_no_call(db):
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    _seed(db, size=100.0)  # pre-migration row: no leg ids
    client = FakeBybit()
    res = apply_intent_reduce_partial_close(
        db, account_id="bybit_1", symbol="ADAUSDT",
        reduce_direction="short", reduce_qty=40.0,
        fill_price=1.0, closed_at_iso="now",
        exchange_client=client, account_cfg=BYBIT_CFG,
    )
    assert client.amend_calls == []
    lr = res["allocations"][0]["leg_resize"]
    assert lr["sl"]["state"] == "no_tracked_id"
    assert lr["tp"]["state"] == "no_tracked_id"


def test_absent_client_is_not_attempted_never_a_pass(db):
    """``not_attempted`` means WE DID NOT LOOK. It must never render as though
    the leg were verified correct, and it must not collapse into
    ``no_tracked_id`` — a row WITH a leg id and no client is a different fact
    from a row with no leg id at all."""
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    _seed(db, size=100.0, sl="untouched-sl")
    res = apply_intent_reduce_partial_close(
        db, account_id="bybit_1", symbol="ADAUSDT",
        reduce_direction="short", reduce_qty=40.0,
        fill_price=1.0, closed_at_iso="now",
    )
    lr = res["allocations"][0]["leg_resize"]
    assert lr["sl"]["state"] == "not_attempted"
    assert lr["sl"]["order_id"] == "untouched-sl"
    assert lr["sl"]["state"] != "no_tracked_id"


def test_venue_refusal_is_failed_and_the_journal_shrink_still_stands(db):
    """A refused amend must be LOUD and must not unwind the partial close.

    The degraded state is exactly today's behaviour — a leg still sized for the
    pre-reduce position — so no regression is reachable from a failure here.
    """
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    tid = _seed(db, size=100.0, sl="the-sl")
    res = apply_intent_reduce_partial_close(
        db, account_id="bybit_1", symbol="ADAUSDT",
        reduce_direction="short", reduce_qty=40.0,
        fill_price=1.0, closed_at_iso="now",
        exchange_client=FakeBybit(ret_code=10001), account_cfg=BYBIT_CFG,
    )
    lr = res["allocations"][0]["leg_resize"]
    assert lr["sl"]["state"] == "failed"
    assert "Qty invalid" in (lr["sl"]["error"] or "")
    rows = {r["id"]: r for r in db.rows()}
    assert rows[tid]["position_size"] == pytest.approx(60.0)


def test_a_raising_client_never_escapes_the_partial_close(db):
    """The containment property. An exception out of here makes the CALLER
    fall back to the legacy reduce-row insert, double-booking a reduce that
    has already been applied — strictly worse than the oversized leg."""
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    tid = _seed(db, size=100.0, sl="the-sl")
    res = apply_intent_reduce_partial_close(
        db, account_id="bybit_1", symbol="ADAUSDT",
        reduce_direction="short", reduce_qty=40.0,
        fill_price=1.0, closed_at_iso="now",
        exchange_client=FakeBybit(raises=True), account_cfg=BYBIT_CFG,
    )
    assert res["allocations"][0]["leg_resize"]["sl"]["state"] == "failed"
    rows = {r["id"]: r for r in db.rows()}
    assert rows[tid]["position_size"] == pytest.approx(60.0)


def test_full_tpsl_mode_is_not_applicable_and_places_no_call(db, monkeypatch):
    """Under Full mode the stop is a field on the POSITION row, already sized
    to the netted position, so there is no per-row leg to disagree."""
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    monkeypatch.setenv("BYBIT_TPSL_MODE", "full")
    _seed(db, size=100.0, sl="the-sl")
    client = FakeBybit()
    res = apply_intent_reduce_partial_close(
        db, account_id="bybit_1", symbol="ADAUSDT",
        reduce_direction="short", reduce_qty=40.0,
        fill_price=1.0, closed_at_iso="now",
        exchange_client=client, account_cfg=BYBIT_CFG,
    )
    assert client.amend_calls == []
    assert res["allocations"][0]["leg_resize"]["sl"]["state"] == "not_applicable"


def test_non_bybit_account_is_not_applicable(db):
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    _seed(db, size=100.0, sl="ib-leg", account="ib_paper")
    client = FakeBybit()
    res = apply_intent_reduce_partial_close(
        db, account_id="ib_paper", symbol="ADAUSDT",
        reduce_direction="short", reduce_qty=40.0,
        fill_price=1.0, closed_at_iso="now",
        exchange_client=client,
        account_cfg={"account_id": "ib_paper", "exchange": "interactive_brokers"},
    )
    assert client.amend_calls == []
    assert res["allocations"][0]["leg_resize"]["sl"]["state"] == "not_applicable"


def test_spot_is_not_applicable(db, monkeypatch):
    from src.units.accounts import execute as execmod

    monkeypatch.setattr(execmod, "_bybit_category", lambda cfg: "spot")
    _seed(db, size=100.0, sl="the-sl")
    client = FakeBybit()
    res = execmod.apply_intent_reduce_partial_close(
        db, account_id="bybit_1", symbol="ADAUSDT",
        reduce_direction="short", reduce_qty=40.0,
        fill_price=1.0, closed_at_iso="now",
        exchange_client=client, account_cfg=BYBIT_CFG,
    )
    assert client.amend_calls == []
    assert res["allocations"][0]["leg_resize"]["sl"]["state"] == "not_applicable"


# ------------------------------------------------------------ the wire value
def test_the_wire_qty_is_not_an_ieee754_artifact(db):
    """MI-139's lesson applied to the resize: ``289.4 - 256.1`` must not reach
    the venue as ``33.299999999999955``, which Bybit refuses outright."""
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    _seed(db, size=289.4, sl="the-sl", symbol="SOLUSDT")
    client = FakeBybit()
    apply_intent_reduce_partial_close(
        db, account_id="bybit_1", symbol="SOLUSDT",
        reduce_direction="short", reduce_qty=256.1,
        fill_price=106.4, closed_at_iso="now",
        exchange_client=client, account_cfg=BYBIT_CFG,
    )
    assert client.amend_calls[0]["qty"] == "33.3"


# -------------------------------------------------------------- back-compat
def test_without_a_client_the_db_effect_is_byte_for_byte_the_old_behaviour(db):
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    a = _seed(db, size=0.5, symbol="BTCUSDT")
    b = _seed(db, size=0.3, symbol="BTCUSDT")
    res = apply_intent_reduce_partial_close(
        db, account_id="bybit_1", symbol="BTCUSDT",
        reduce_direction="short", reduce_qty=0.29,
        fill_price=120.0, closed_at_iso="now",
    )
    rows = {r["id"]: r for r in db.rows()}
    assert rows[a]["position_size"] == pytest.approx(0.21)
    assert rows[a]["status"] == "open"
    assert rows[b]["position_size"] == pytest.approx(0.3)
    assert res["leftover"] == pytest.approx(0.0)
    assert res["no_parent_position"] is False
    assert res["allocations"][0]["parent_id"] == a
    assert res["allocations"][0]["consumed"] == pytest.approx(0.29)


def test_a_journal_without_the_leg_columns_still_reduces(tmp_path):
    """The schema fallback. A missing column must cost the leg resize, never
    the partial close — a reduce that fails is the churn loop this whole
    function exists to stop."""
    from src.units.accounts.execute import apply_intent_reduce_partial_close

    old = FakeDB(str(tmp_path / "old.db"), schema=_SCHEMA_NO_LEG_COLUMNS)
    tid = old.insert_trade({
        "timestamp": "t", "symbol": "ADAUSDT", "direction": "long",
        "entry_price": 1.0, "position_size": 100.0, "status": "open",
        "is_backtest": 0, "account_id": "bybit_1", "strategy_name": "vwap",
    })
    client = FakeBybit()
    res = apply_intent_reduce_partial_close(
        old, account_id="bybit_1", symbol="ADAUSDT",
        reduce_direction="short", reduce_qty=40.0,
        fill_price=1.0, closed_at_iso="now",
        exchange_client=client, account_cfg=BYBIT_CFG,
    )
    rows = {r["id"]: r for r in old.rows()}
    assert rows[tid]["position_size"] == pytest.approx(60.0)
    assert client.amend_calls == []
    assert res["allocations"][0]["leg_resize"]["sl"]["state"] == "no_tracked_id"


# -------------------------------------------------- the amend helper itself
def test_amend_helper_sends_qty_alone_without_touching_the_price():
    from src.units.accounts.execute import _amend_partial_tpsl_leg

    client = FakeBybit()
    out = _amend_partial_tpsl_leg(client, "linear", "ADAUSDT", "oid", qty="628")
    assert out["ok"] is True
    assert client.amend_calls == [
        {"category": "linear", "symbol": "ADAUSDT", "orderId": "oid", "qty": "628"}]


def test_amend_helper_still_sends_price_alone_unchanged():
    from src.units.accounts.execute import _amend_partial_tpsl_leg

    client = FakeBybit()
    _amend_partial_tpsl_leg(client, "linear", "ADAUSDT", "oid", trigger_price=0.81)
    assert client.amend_calls == [
        {"category": "linear", "symbol": "ADAUSDT", "orderId": "oid",
         "triggerPrice": "0.81"}]


def test_amend_helper_refuses_an_empty_amend_locally():
    """It previously put the literal string ``"None"`` on the wire."""
    from src.units.accounts.execute import _amend_partial_tpsl_leg

    client = FakeBybit()
    out = _amend_partial_tpsl_leg(client, "linear", "ADAUSDT", "oid")
    assert out["ok"] is False
    assert client.amend_calls == []


# ---------------------------------------------------- the dry-run withholding
def test_a_dry_reduce_places_no_venue_call(tmp_path, monkeypatch):
    """A dry reduce placed no order, so the venue book never moved. Amending a
    live leg to match a journal the venue does not back is the one way this
    change could touch a real position it must not."""
    from types import SimpleNamespace

    from src.units.accounts import execute as execmod
    import src.units.db.database as dbmod
    import src.utils.paths as pathsmod

    db = FakeDB(str(tmp_path / "dry.db"))
    monkeypatch.setattr(pathsmod, "trade_journal_db_path", lambda: "ignored")
    monkeypatch.setattr(dbmod, "Database", lambda db_path=None: db)

    tid = _seed(db, size=100.0, sl="the-sl", symbol="ADAUSDT")
    pkg = SimpleNamespace(
        symbol="ADAUSDT", direction="short", entry=1.0, sl=0.9, tp=1.2,
        confidence=0.7, strategy="vwap", meta={"order_package_id": "op-1"},
    )
    client = FakeBybit()
    ok = execmod._log_trade_to_journal(
        pkg, BYBIT_CFG, {"qty": 40.0, "symbol": "ADAUSDT"},
        trade_id="ex-1", is_dry=True, intent_reduce=True,
        exchange_client=client,
    )
    assert ok is True
    assert client.amend_calls == []
    rows = {r["id"]: r for r in db.rows()}
    assert rows[tid]["position_size"] == pytest.approx(60.0)


def test_a_live_reduce_through_the_writer_does_resize(tmp_path, monkeypatch):
    """The positive control for the test above — same path, is_dry=False."""
    from types import SimpleNamespace

    from src.units.accounts import execute as execmod
    import src.units.db.database as dbmod
    import src.utils.paths as pathsmod

    db = FakeDB(str(tmp_path / "live.db"))
    monkeypatch.setattr(pathsmod, "trade_journal_db_path", lambda: "ignored")
    monkeypatch.setattr(dbmod, "Database", lambda db_path=None: db)

    _seed(db, size=100.0, sl="the-sl", symbol="ADAUSDT")
    pkg = SimpleNamespace(
        symbol="ADAUSDT", direction="short", entry=1.0, sl=0.9, tp=1.2,
        confidence=0.7, strategy="vwap", meta={"order_package_id": "op-1"},
    )
    client = FakeBybit()
    execmod._log_trade_to_journal(
        pkg, BYBIT_CFG, {"qty": 40.0, "symbol": "ADAUSDT"},
        trade_id="ex-1", is_dry=False, intent_reduce=True,
        exchange_client=client,
    )
    assert [c["orderId"] for c in client.amend_calls] == ["the-sl"]
    assert float(client.amend_calls[0]["qty"]) == pytest.approx(60.0)
