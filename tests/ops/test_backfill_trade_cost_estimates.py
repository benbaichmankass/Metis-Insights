"""Tests for scripts/ops/backfill_trade_cost_estimates.py (MB-20260629-ALLOC-COSTCAP)."""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "backfill_trade_cost_estimates",
    Path(__file__).resolve().parents[2] / "scripts" / "ops" / "backfill_trade_cost_estimates.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)  # type: ignore[union-attr]


def _seed(db: Path, rows: list[tuple]) -> None:
    """rows = [(id, status, is_backtest, entry_price, position_size, symbol,
    fee_taker_usd, cost_source), ...]."""
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT, is_backtest INT, "
        "entry_price REAL, position_size REAL, symbol TEXT, fee_taker_usd REAL, "
        "cost_source TEXT, pnl REAL)"
    )
    for r in rows:
        conn.execute(
            "INSERT INTO trades (id,status,is_backtest,entry_price,position_size,"
            "symbol,fee_taker_usd,cost_source,pnl) VALUES (?,?,?,?,?,?,?,?,?)",
            (*r, 1.23),  # a pnl the backfill must never touch
        )
    conn.commit()
    conn.close()


def _fetch(db: Path):
    conn = sqlite3.connect(str(db))
    out = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT id, fee_taker_usd, cost_source, pnl FROM trades")}
    conn.close()
    return out


def test_backfill_costs_uncosted_and_preserves_existing(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _seed(db, [
        # uncosted closed real trade -> should be backfilled
        (1, "closed", 0, 100.0, 2.0, "BTCUSDT", None, None),
        # already carries a broker-truth cost -> must NOT be overwritten
        (2, "closed", 0, 100.0, 2.0, "BTCUSDT", 0.99, "broker"),
        # backtest row -> skipped
        (3, "closed", 1, 100.0, 2.0, "BTCUSDT", None, None),
        # still open -> skipped
        (4, "open", 0, 100.0, 2.0, "BTCUSDT", None, None),
    ])
    # dry-run writes nothing.
    s = _MOD.backfill(str(db), apply=False)
    assert s["candidates"] == 1  # only id 1 qualifies
    assert s["written"] == 1
    after_dry = _fetch(db)
    assert after_dry[1][0] is None and after_dry[1][1] is None  # dry-run: untouched

    # apply writes the estimate on id 1 only.
    s2 = _MOD.backfill(str(db), apply=True)
    assert s2["candidates"] == 1 and s2["written"] == 1
    after = _fetch(db)
    fee1, src1, pnl1 = after[1]
    assert src1 == "estimate" and fee1 is not None and fee1 > 0
    assert pnl1 == 1.23  # pnl never touched
    # id 2 (broker truth) untouched; id 3/4 untouched.
    assert after[2] == (0.99, "broker", 1.23)
    assert after[3][1] is None and after[4][1] is None


def test_backfill_is_idempotent(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _seed(db, [(1, "closed", 0, 100.0, 2.0, "BTCUSDT", None, None)])
    _MOD.backfill(str(db), apply=True)
    first = _fetch(db)[1]
    # second run finds no candidates (already costed) -> no-op.
    s = _MOD.backfill(str(db), apply=True)
    assert s["candidates"] == 0 and s["written"] == 0
    assert _fetch(db)[1] == first


def test_backfill_skips_underivable(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _seed(db, [
        (1, "closed", 0, None, 2.0, "BTCUSDT", None, None),   # no entry -> uncomputable
        (2, "closed", 0, 100.0, None, "BTCUSDT", None, None),  # no qty -> uncomputable
    ])
    s = _MOD.backfill(str(db), apply=True)
    assert s["candidates"] == 2
    assert s["written"] == 0
    assert s["skipped_uncomputable"] == 2
    after = _fetch(db)
    assert after[1][1] is None and after[2][1] is None  # left NULL, not costed
