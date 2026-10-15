"""EXIT-PATH-TELEMETRY-LIVE — the per-bar position path history.

Pinned: one row per open position per bar (a bar's many passes collapse into
one row), no rows once the package is stamped final, the retention bound, and
that a write failure neither raises nor touches the canonical upsert. The table
is created by the REAL ``Database`` DDL, not a test fixture, so a DDL/writer
drift fails here.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.runtime import position_path_history as pph
from src.runtime import position_telemetry as pt
from src.units.db.database import Database

T0 = datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "journal.db")
    Database(path)  # the production DDL
    pph._last_prune.clear()
    return path


def _candles(n_bars: int, start_px: float = 100.0):
    rng = np.random.default_rng(7)
    close = start_px + np.cumsum(rng.normal(0, 0.5, n_bars))
    ts = [T0 + timedelta(hours=i) for i in range(n_bars)]
    return pd.DataFrame({"timestamp": ts, "open": close, "high": close + 0.6,
                         "low": close - 0.6, "close": close})


def _pass(db, candles, *, pkg="pkg-A", now=None, atr=None):
    entry_ts = candles["timestamp"].iloc[40].isoformat()
    window = candles[candles["timestamp"] >= candles["timestamp"].iloc[40]]
    return pt.record_position_telemetry(
        open_pkg={"order_package_id": pkg, "symbol": "BTCUSDT",
                  "entry": float(candles["close"].iloc[40]), "tp": 110.0,
                  "account_id": "bybit_1", "strategy_name": "leg_x"},
        meta={"risk_per_unit": 2.0, "entry_time": entry_ts},
        window=window, direction="long",
        current_price=float(candles["close"].iloc[-1]),
        stop=95.0, target=110.0, strategy="leg_x",
        candles_df=candles, atr=atr, now=now, db_path=db)


def _rows(db, pkg="pkg-A"):
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(
            "SELECT * FROM position_path_history WHERE order_package_id=? "
            "ORDER BY bar_ts", (pkg,))]


def test_one_row_per_bar_for_an_open_position(db):
    full = _candles(60)
    for n in (50, 51, 52):          # three bars
        for _ in range(3):          # three exit-loop passes inside each bar
            _pass(db, full.iloc[:n].reset_index(drop=True))
    rows = _rows(db)
    assert len(rows) == 3
    assert [r["passes"] for r in rows] == [3, 3, 3]
    assert rows[0]["bar_ts"] < rows[1]["bar_ts"] < rows[2]["bar_ts"]
    r = rows[-1]
    # Projected from the canonical record, not re-derived.
    tel = pt.read_records(db_path=db)["rows"][0]
    # collapsed-state: measured — a fixture assertion on the one state this
    # anchored frame produces; the non-measured states are pinned below.
    assert r["open_r"] == tel["open_r"] and r["peak_state"] == "measured"
    assert r["mae_r"] is not None and r["mae_r"] <= r["open_r"]
    assert r["peak_r"] >= r["open_r"]
    assert r["sl_price"] == 95.0 and r["tp_price"] == 110.0
    entry = float(full["close"].iloc[40])
    assert r["sl_r"] == pytest.approx((95.0 - entry) / 2.0, abs=1e-3)
    assert r["tp_r"] == pytest.approx((110.0 - entry) / 2.0, abs=1e-3)
    assert r["atr_source"] == "computed_wilder14" and r["atr"] > 0
    assert r["adx"] is not None
    assert r["account_id"] == "bybit_1" and r["strategy"] == "leg_x"


def test_monitor_atr_wins_and_is_labelled(db):
    _pass(db, _candles(50), atr=1.234)
    r = _rows(db)[0]
    assert r["atr"] == 1.234 and r["atr_source"] == "monitor"


def test_no_rows_after_close(db):
    full = _candles(60)
    _pass(db, full.iloc[:50].reset_index(drop=True))
    with sqlite3.connect(db) as c:   # the close path's terminal stamp
        c.execute("UPDATE position_telemetry SET terminal_state='final' "
                  "WHERE order_package_id='pkg-A'")
    _pass(db, full.iloc[:51].reset_index(drop=True))
    _pass(db, full.iloc[:52].reset_index(drop=True))
    assert len(_rows(db)) == 1


def test_retention_prunes_old_rows_only(db):
    import time
    pph._last_prune[db] = time.monotonic()   # hold off the write-path prune
    old = T0 - timedelta(days=pph.RETENTION_DAYS + 5)
    _pass(db, _candles(50), pkg="pkg-old", now=old)
    _pass(db, _candles(51), pkg="pkg-new", now=T0)
    assert len(_rows(db, "pkg-old")) == 1
    with sqlite3.connect(db) as c:
        assert pph.prune_history(c, now=T0) == 1
        assert pph.prune_history(c, now=T0) == 0
    assert _rows(db, "pkg-old") == [] and len(_rows(db, "pkg-new")) == 1


def test_write_path_prunes_on_its_own(db):
    old = datetime.now(timezone.utc) - timedelta(days=pph.RETENTION_DAYS + 5)
    _pass(db, _candles(50), pkg="pkg-old", now=old)   # pruned by its own write
    assert _rows(db, "pkg-old") == []


def test_write_failure_does_not_raise_or_touch_the_upsert(db, monkeypatch):
    with sqlite3.connect(db) as c:
        c.execute("DROP TABLE position_path_history")
    rec = _pass(db, _candles(50))          # must not raise
    assert rec is not None
    assert pt.read_records(db_path=db)["count"] == 1   # canonical row landed

    def boom(*a, **k):
        raise RuntimeError("history build exploded")
    monkeypatch.setattr(pph, "build_history_row", boom)
    assert _pass(db, _candles(51)) is not None


def test_write_history_contains_a_bad_path():
    row = {"order_package_id": "p", "bar_ts": "x"}
    assert pph.write_history(row, db_path="/nonexistent/dir/x.db") is False


def test_mae_is_null_when_the_peak_was_not_measured(db):
    rec = {"order_package_id": "p", "peak_state": "unanchored",
           "direction": "long", "entry": 100.0, "risk_per_unit": 2.0}
    row = pph.build_history_row(rec, window=_candles(50), candles_df=_candles(50))
    assert row["mae_r"] is None and row["peak_state"] == "unanchored"
    for st in ("thin_window", "no_risk"):
        rec["peak_state"] = st
        assert pph.build_history_row(rec, window=_candles(50))["mae_r"] is None


def test_unkeyable_frame_yields_no_row():
    rec = {"order_package_id": "p", "peak_state": "measured", "direction": "long"}
    frame = pd.DataFrame({"high": [1.0, 2.0], "low": [0.5, 1.0], "close": [1, 2]})
    assert pph.build_history_row(rec, window=frame, candles_df=frame) is None


def test_read_history_envelope(db):
    assert pph.read_history(db_path=db)["present"] is True
    full = _candles(60)
    for n in (50, 51):
        _pass(db, full.iloc[:n].reset_index(drop=True))
    env = pph.read_history(db_path=db, order_package_id="pkg-A")
    assert env["count"] == 2 and env["packages"] == 1
    assert env["rows"][0]["bar_ts"] < env["rows"][1]["bar_ts"]


def test_read_history_absent_table_is_not_empty(tmp_path):
    p = str(tmp_path / "bare.db")
    sqlite3.connect(p).close()
    env = pph.read_history(db_path=p)
    assert env["present"] is False and env["error"]
