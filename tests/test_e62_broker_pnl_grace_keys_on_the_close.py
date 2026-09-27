"""E62: the broker-truth grace and window are measured from the CLOSE.

THE DEFECT
----------
``_sweep_local_pnl_for_unpriced`` defers a broker-reader account's row for
``_LOCAL_PNL_BROKER_DEFER_MS`` (6h) so the broker sweep can price it from venue
truth first. That grace was measured from ``created_at``, the OPEN. A position
held longer than 6h therefore reached its close with no grace left. On the very
next tick the local sweep priced it from the close path's ``verdict`` price,
before the hourly fills pull had landed the fill. That set ``pnl``, and
``_sweep_pending_pnl_from_bybit`` selects only ``pnl IS NULL``, so it never
looked. The broker sweep's own 7-day window was also keyed on the open.

Measured on the live journal (trades ids <= 6141, read 2026-09-24): all 30
Alpaca sl/tp/giveback closes since the Alpaca fills reader landed (#8111,
2026-07-31) were held >= 6h, and 0 of them carry a MEASURED exit. Their fills ARE in
the store; e.g. trade 5920 (alpaca_portfolio SPY, 7 sh) has 6 sell fills
summing to 7 sh, 0.4 s before its ``closed_at``. On ib_paper, sl/tp closes held
< 6h resolved ``ib_execution`` 7 of 8 times, and closes held >= 6h 0 of 10.

That is why D3 found 0 MEASURED Alpaca sl/tp exits, and why exit slippage on
Alpaca could not be measured however many trades accrued.

Filed 2026-09-05 as BL-20260905-LOCAL-PNL-BROKER-DEFER-GRACE-KEYS-ON-THE-OPEN
and BL-20260905-BYBIT-BROKER-PNL-SWEEP-WINDOW-ALSO-KEYS-ON-THE-OPEN. The first
row's resolution criteria name the two tests below: a row held 30 days and
closed 1 minute ago IS deferred, and a row closed 7 hours ago is NOT.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import order_monitor as om

# Every production column the two sweeps read. A missing one does not fail
# loudly: sqlite raises, the sweep's broad except swallows it, and every
# assertion below reads a false negative
# (BL-20260823-FIXTURE-MISSING-A-PRODUCTION-COLUMN-FAILS-SILENTLY).
_SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    account_id TEXT, symbol TEXT, direction TEXT,
    position_size REAL, entry_price REAL, exit_price REAL,
    pnl REAL, pnl_percent REAL, status TEXT,
    is_backtest INTEGER DEFAULT 0, setup_type TEXT,
    order_package_id TEXT, closed_at TEXT, created_at TEXT,
    exit_reason TEXT, timestamp TEXT, notes TEXT
);
"""

_ACCT = "alpaca_e62_test"
_CFG = {"account_id": _ACCT, "exchange": "alpaca", "mode": "live", "options": None}
_ENTRY, _QTY, _VERDICT_PX, _FILL_PX = 759.89, 7.0, 763.47, 763.48


class _DB:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def update_trade(self, tid, updates):
        sets = ", ".join(f"{k} = ?" for k in updates)
        c = sqlite3.connect(self.path)
        c.execute(f"UPDATE trades SET {sets} WHERE id = ?", (*updates.values(), int(tid)))
        c.commit()
        c.close()


def _fmt(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make(tmp_path, *, held_days, closed_ago, stamp="verdict"):
    path = tmp_path / "j.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    closed = datetime.now(timezone.utc) - closed_ago
    created = closed - timedelta(days=held_days)
    notes = json.dumps({"exit_price_source": stamp} if stamp else {})
    conn.execute(
        "INSERT INTO trades (id, account_id, symbol, direction, position_size, "
        "entry_price, exit_price, pnl, status, is_backtest, exit_reason, "
        "closed_at, created_at, timestamp, notes) VALUES "
        "(1, ?, 'SPY', 'long', ?, ?, ?, NULL, 'closed', 0, 'sl_cross', ?, ?, ?, ?)",
        (_ACCT, _QTY, _ENTRY, _VERDICT_PX, _fmt(closed), _fmt(created), _fmt(created), notes),
    )
    conn.commit()
    conn.close()
    return _DB(path)


def _row(db):
    c = db.connect()
    r = dict(c.execute("SELECT * FROM trades WHERE id = 1").fetchone())
    c.close()
    r["notes"] = json.loads(r["notes"] or "{}")
    return r


@pytest.fixture(autouse=True)
def _cfgs(monkeypatch):
    monkeypatch.setattr(om, "_load_account_cfgs_for_reconcile", lambda: {_ACCT: dict(_CFG)})
    # The anchor must never be reached here (every row carries an exit price).
    import src.runtime.exit_anchor as EA
    monkeypatch.setattr(EA, "bar_close_at", lambda *a, **k: pytest.fail("anchor reached"))


@pytest.fixture
def fills_reader(monkeypatch):
    """The Alpaca reader as it behaves on the live store: a fill-derived exit
    price, no venue pnl (``src.runtime.fills_pnl.exit_from_fills``)."""
    import src.units.accounts.clients as C

    calls = []

    def _reader(cfg, **kw):
        calls.append(kw)
        return {"avg_exit_price": _FILL_PX, "avg_entry_price": None, "closed_pnl": None,
                "qty": _QTY, "side": "sell", "closed_at": None, "fees": 0.0,
                "source": "exchange_fill"}

    monkeypatch.setattr(C, "account_closed_pnl_for_trade", _reader)
    return calls


# ------------------------------------------------------------ local sweep
def test_long_hold_closed_a_minute_ago_is_deferred(tmp_path):
    """The resolution-criteria test. Held 30 days, closed 1 minute ago: the
    broker reader has had no window at all, so local compute must wait.
    On the open-keyed code this row was priced from the verdict immediately."""
    db = _make(tmp_path, held_days=30, closed_ago=timedelta(minutes=1))
    summary = om._sweep_local_pnl_for_unpriced(db)
    row = _row(db)
    assert summary["deferred_broker"] == 1
    assert row["pnl"] is None, "local compute pre-empted the broker reader's window"


def test_close_older_than_the_grace_is_priced(tmp_path):
    """POSITIVE CONTROL for the test above: same row, closed 7h ago. The
    grace has run out, so the fallback prices it, and the verdict stamp is kept
    rather than upgraded (it IS a verdict price)."""
    db = _make(tmp_path, held_days=30, closed_ago=timedelta(hours=7))
    summary = om._sweep_local_pnl_for_unpriced(db)
    row = _row(db)
    assert summary["deferred_broker"] == 0
    assert row["pnl"] == pytest.approx((_VERDICT_PX - _ENTRY) * _QTY)
    assert row["notes"]["exit_price_source"] == "verdict"


def test_short_hold_closed_a_minute_ago_is_still_deferred(tmp_path):
    """The behaviour that already worked (hold < grace) is unchanged."""
    db = _make(tmp_path, held_days=0.01, closed_ago=timedelta(minutes=1))
    assert om._sweep_local_pnl_for_unpriced(db)["deferred_broker"] == 1


def test_a_fill_priced_exit_is_not_held_for_the_grace(tmp_path):
    """Once the fills resolver has stamped ``exchange_fill``, that is all the
    Alpaca reader will ever supply (it returns no pnl), so the row is priced
    now instead of sitting at NULL for 6h. The stamp is kept."""
    db = _make(tmp_path, held_days=30, closed_ago=timedelta(minutes=1), stamp="exchange_fill")
    summary = om._sweep_local_pnl_for_unpriced(db)
    row = _row(db)
    assert summary["deferred_broker"] == 0
    assert row["pnl"] is not None
    assert row["notes"]["exit_price_source"] == "exchange_fill"


def test_a_bybit_exchange_stamp_still_waits_for_closed_pnl(tmp_path):
    """``exchange`` (Bybit real, stamped at close from order status) is NOT
    exempt: its fee-accurate closed-pnl record is still owed by the broker
    sweep, and local compute must not pre-empt it."""
    db = _make(tmp_path, held_days=30, closed_ago=timedelta(minutes=1), stamp="exchange")
    assert om._sweep_local_pnl_for_unpriced(db)["deferred_broker"] == 1


# ----------------------------------------------------------- broker sweep
def test_broker_sweep_asks_for_a_long_held_recent_close(tmp_path, fills_reader):
    """Held 10 days (> the 7-day window measured from the open), closed 1h
    ago. On the open-keyed code the row was never selected."""
    db = _make(tmp_path, held_days=10, closed_ago=timedelta(hours=1))
    summary = om._sweep_pending_pnl_from_bybit(db)
    row = _row(db)
    assert summary["scanned"] == 1 and len(fills_reader) == 1
    assert row["exit_price"] == pytest.approx(_FILL_PX)
    assert row["notes"]["exit_price_source"] == "exchange_fill"


def test_broker_sweep_window_ends_seven_days_after_the_close(tmp_path, fills_reader):
    """NEGATIVE CONTROL: the window still has an end."""
    db = _make(tmp_path, held_days=1, closed_ago=timedelta(days=8))
    assert om._sweep_pending_pnl_from_bybit(db)["scanned"] == 0
    assert fills_reader == []


# -------------------------------------------------------------- end to end
def test_tick_order_turns_an_sl_cross_verdict_into_a_measured_exit(tmp_path, monkeypatch):
    """The E62 outcome, reproducing the race in production tick order (broker
    sweep, then local sweep; ``run_monitor_tick``). Tick 1 is the close tick:
    the hourly fills pull has not landed the fill yet, so the reader has
    nothing. Tick 2 is after the pull. On the open-keyed code, tick 1's local
    sweep priced the row from the verdict, and tick 2's broker sweep never
    selected it again (``pnl IS NULL``). Now the row ends MEASURED."""
    import src.units.accounts.clients as C
    from src.runtime.provenance import MEASURED, classify

    store = {"landed": False}

    def _reader(cfg, **kw):
        if not store["landed"]:
            return None
        return {"avg_exit_price": _FILL_PX, "avg_entry_price": None, "closed_pnl": None,
                "qty": _QTY, "side": "sell", "closed_at": None, "fees": 0.0,
                "source": "exchange_fill"}

    monkeypatch.setattr(C, "account_closed_pnl_for_trade", _reader)
    db = _make(tmp_path, held_days=6, closed_ago=timedelta(minutes=2))
    for landed in (False, True):
        store["landed"] = landed
        om._sweep_pending_pnl_from_bybit(db)
        om._sweep_local_pnl_for_unpriced(db)
    row = _row(db)
    assert classify(row["notes"]["exit_price_source"], "exit_price_source") == MEASURED
    assert row["exit_price"] == pytest.approx(_FILL_PX)
    assert row["pnl"] == pytest.approx((_FILL_PX - _ENTRY) * _QTY)
    assert row["notes"]["pnl_source"] == "local_compute"
