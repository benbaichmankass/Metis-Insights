"""Tests for scripts/ops/reconcile_netting_rows.py — the general same-moment
netting partial-close reconcile (BL-20260801-NETTING-PARTIAL-CLOSE, option c+b).

The pure planner is exercised across FIFO-oldest, the leg-fired (b) preference,
the fail-safe unreadable-exchange skip, pairs exclusion, no-surplus, and the
conservative straddle (never close more than the surplus). The CLI apply is
verified on a throwaway DB (supersede + UNMEASURED + idempotent).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MOD = os.path.join(_ROOT, "scripts", "ops", "reconcile_netting_rows.py")
_spec = importlib.util.spec_from_file_location("reconcile_netting_rows", _MOD)
rnr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rnr)


def _rows(*specs):
    out = []
    for sp in specs:
        rid, size, ca, strat, sl, tp = sp[:6]
        acct = sp[6] if len(sp) > 6 else "bybit_1"
        sym = sp[7] if len(sp) > 7 else "BTCUSDT"
        dr = sp[8] if len(sp) > 8 else "buy"
        out.append({"id": rid, "account_id": acct, "symbol": sym, "direction": dr,
                    "position_size": size, "status": "open", "created_at": ca,
                    "strategy": strat, "sl_order_id": sl, "tp_order_id": tp})
    return out


G = ("bybit_1", "BTCUSDT", "buy")


def test_fifo_oldest_up_to_surplus():
    r = _rows((1, 1.0, "2026-08-01T00:00", "ict_scalp", None, None),
              (2, 0.5, "2026-08-01T01:00", "ict_scalp", None, None),
              (3, 0.01, "2026-08-01T02:00", "ict_scalp", None, None))
    tc, _ = rnr.reconcile_plan(r, {G: 0.01})
    assert sorted(c["id"] for c in tc) == [1, 2]  # newest 0.01 survives == exchange


def test_leg_fired_preference():
    r = _rows((1, 0.5, "2026-08-01T00:00", "ict_scalp", "legA", None),
              (2, 0.5, "2026-08-01T01:00", "ict_scalp", "legB", None),
              (3, 0.5, "2026-08-01T02:00", "ict_scalp", None, None))
    tc, _ = rnr.reconcile_plan(r, {G: 0.5}, resting_legs={G: ["legA"]})
    ids = [c["id"] for c in tc]
    assert 2 in ids  # legB absent from resting -> fired -> closed
    assert any("leg_fired" in c["why"] for c in tc if c["id"] == 2)


def test_unreadable_exchange_skips_group():
    r = _rows((1, 1.0, "2026-08-01T00:00", "ict_scalp", None, None))
    tc, sk = rnr.reconcile_plan(r, {G: None})
    assert tc == [] and any(s["reason"] == "exchange_unreadable" for s in sk)


def test_pairs_rows_excluded():
    r = _rows((1, 10.0, "2026-08-01T00:00", "pairs_bnb_btc_a", None, None,
               "bybit_1", "BNBUSDT", "buy"))
    tc, _ = rnr.reconcile_plan(r, {("bybit_1", "BNBUSDT", "buy"): 1.0})
    assert tc == []


def test_no_surplus():
    r = _rows((1, 0.5, "2026-08-01T00:00", "ict_scalp", None, None))
    tc, sk = rnr.reconcile_plan(r, {G: 0.5})
    assert tc == [] and any(s["reason"] == "no_surplus" for s in sk)


def test_conservative_never_closes_more_than_surplus():
    # journal 1.0(old)+0.3(new)=1.3, exchange 1.0 -> surplus 0.3; the old 1.0 row
    # would overshoot the surplus, so it is KEPT; only the 0.3 row closes.
    r = _rows((1, 1.0, "2026-08-01T00:00", "ict_scalp", None, None),
              (2, 0.3, "2026-08-01T01:00", "ict_scalp", None, None))
    tc, _ = rnr.reconcile_plan(r, {G: 1.0})
    assert [c["id"] for c in tc] == [2]


def test_cli_apply_supersedes_unmeasured_and_idempotent():
    d = tempfile.mkdtemp()
    dbp = os.path.join(d, "t.db")
    c = sqlite3.connect(dbp)
    c.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT, "
              "direction TEXT, position_size REAL, status TEXT, created_at TEXT, closed_at TEXT, "
              "strategy_name TEXT, pnl REAL, exit_price REAL, exit_reason TEXT, notes TEXT, "
              "reconcile_status TEXT, sl_order_id TEXT, tp_order_id TEXT, is_backtest INTEGER)")
    c.executemany(
        "INSERT INTO trades(id,account_id,symbol,direction,position_size,status,created_at,"
        "strategy_name,is_backtest) VALUES(?,?,?,?,?,?,?,?,0)",
        [(1, "bybit_1", "BTCUSDT", "buy", 1.0, "open", "2026-08-01T00:00", "ict_scalp"),
         (2, "bybit_1", "BTCUSDT", "buy", 0.01, "open", "2026-08-01T01:00", "ict_scalp"),
         (3, "bybit_1", "SOLUSDT", "buy", 5.0, "open", "2026-08-01", "pairs_sol_btc_a")])
    c.commit()
    c.close()
    ej = os.path.join(d, "exch.json")
    with open(ej, "w") as fh:
        json.dump({"bybit_1/BTCUSDT/buy": 0.01, "bybit_1/SOLUSDT/buy": 1.0}, fh)

    assert rnr.main(["--db", dbp, "--exchange-json", ej]) == 0          # dry-run
    assert rnr.main(["--db", dbp, "--exchange-json", ej, "--apply"]) == 0

    c = sqlite3.connect(dbp)
    c.row_factory = sqlite3.Row
    r1 = dict(c.execute("SELECT * FROM trades WHERE id=1").fetchone())
    r2 = dict(c.execute("SELECT * FROM trades WHERE id=2").fetchone())
    r3 = dict(c.execute("SELECT * FROM trades WHERE id=3").fetchone())
    assert r1["status"] == "closed" and r1["reconcile_status"] == "superseded"
    assert r1["pnl"] is None and r1["exit_price"] is None                # UNMEASURED
    assert r1["exit_reason"] == "netting_partial_reconciled" and r1["closed_at"]
    assert json.loads(r1["notes"])["netting_partial_reconcile"]["backlog_id"].startswith("BL-20260801")
    assert r2["status"] == "open"                                        # newest survives
    assert r3["status"] == "open"                                        # pairs never touched
    # idempotent
    assert rnr.main(["--db", dbp, "--exchange-json", ej, "--apply"]) == 0
    assert dict(c.execute("SELECT status FROM trades WHERE id=1").fetchone())["status"] == "closed"
    c.close()
