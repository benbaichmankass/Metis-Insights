"""Regression tests for scripts/ops/backfill_pnl_nulls.py.

Focus: the 2026-06-19 multiplier-correctness hardening. Before it, the
one-shot used a raw ``(exit - entry) * size`` formula with NO
``contract_value_usd`` multiplier, so it silently UNDERCOUNTED every IBKR
futures row (MES=5, MGC=10, MHG=2500). Real money (``bybit_2``, crypto,
cvu=1) was unaffected, but the historical NULL-pnl backlog is dominated by
IBKR-futures paper rows, so the undercount mattered the moment the one-shot
was pointed at them. The script now delegates to the canonical
``src.runtime.local_pnl`` helpers — the SAME maths the live per-tick
``_sweep_local_pnl_for_unpriced`` uses.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from src.runtime.local_pnl import contract_value_usd_for

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts" / "ops" / "backfill_pnl_nulls.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("backfill_pnl_nulls", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _make_db(tmp_path: Path, rows: list[dict]) -> str:
    db = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT,
            entry_price REAL, exit_price REAL, position_size REAL,
            status TEXT, pnl REAL, pnl_percent REAL, is_backtest INT,
            strategy_name TEXT, account_id TEXT, timestamp TEXT, notes TEXT
        )"""
    )
    for r in rows:
        conn.execute(
            "INSERT INTO trades (id, symbol, direction, entry_price, "
            "exit_price, position_size, status, pnl, pnl_percent, "
            "is_backtest, strategy_name, account_id, timestamp, notes) "
            "VALUES (:id,:symbol,:direction,:entry_price,:exit_price,"
            ":position_size,:status,:pnl,:pnl_percent,:is_backtest,"
            ":strategy_name,:account_id,:timestamp,:notes)",
            {
                "pnl": None, "pnl_percent": None, "is_backtest": 0,
                "strategy_name": "s", "account_id": "a", "timestamp": "t",
                "notes": None, **r,
            },
        )
    conn.commit()
    conn.close()
    return str(db)


def _run_apply(mod, db_path, monkeypatch):
    monkeypatch.setenv("TRADE_JOURNAL_DB", db_path)
    monkeypatch.setattr("sys.argv", ["backfill_pnl_nulls.py", "--apply"])
    return mod.main()


def _pnl(db_path, trade_id):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT pnl, pnl_percent FROM trades WHERE id=?", (trade_id,)
        ).fetchone()
    finally:
        conn.close()
    return row


def test_futures_row_uses_contract_multiplier(tmp_path, monkeypatch):
    """An MGC futures long is filled with the multiplier-aware PnL, not the
    pre-hardening undercount."""
    mod = _load_module()
    db = _make_db(tmp_path, [{
        "id": 1, "symbol": "MGC", "direction": "long",
        "entry_price": 4318.0, "exit_price": 4320.0, "position_size": 8.0,
        "status": "closed",
    }])
    assert _run_apply(mod, db, monkeypatch) == 0
    cvu = contract_value_usd_for("MGC")
    assert cvu > 1  # guards against an instruments.yaml regression
    pnl, _ = _pnl(db, 1)
    # multiplier-aware: (4320-4318)*8*cvu ; the OLD buggy formula gave 1/cvu of this.
    assert pnl == pytest.approx((4320.0 - 4318.0) * 8.0 * cvu)


def test_crypto_row_unchanged_by_hardening(tmp_path, monkeypatch):
    """A crypto perp (cvu=1) is unaffected — real-money bybit rows keep the
    same value the pre-hardening formula produced."""
    mod = _load_module()
    db = _make_db(tmp_path, [{
        "id": 2, "symbol": "BTCUSDT", "direction": "short",
        "entry_price": 60000.0, "exit_price": 59900.0, "position_size": 0.01,
        "status": "closed",
    }])
    assert _run_apply(mod, db, monkeypatch) == 0
    pnl, _ = _pnl(db, 2)
    assert pnl == pytest.approx((60000.0 - 59900.0) * 0.01)


def test_bybit_closed_pnl_note_preferred(tmp_path, monkeypatch):
    """The net-of-fees Bybit figure stored in notes still wins over local
    compute."""
    mod = _load_module()
    db = _make_db(tmp_path, [{
        "id": 3, "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 60000.0, "exit_price": 60100.0, "position_size": 0.01,
        "status": "closed", "notes": '{"bybit_closed_pnl": 0.87}',
    }])
    assert _run_apply(mod, db, monkeypatch) == 0
    pnl, _ = _pnl(db, 3)
    assert pnl == pytest.approx(0.87)  # net figure, not the 1.0 gross


def test_missing_exit_price_left_null(tmp_path, monkeypatch):
    """A row with no exit_price is not a candidate — stays NULL (the
    needs-historical-mark bucket the honest re-stamp handles separately)."""
    mod = _load_module()
    db = _make_db(tmp_path, [{
        "id": 4, "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 60000.0, "exit_price": None, "position_size": 0.01,
        "status": "closed",
    }])
    assert _run_apply(mod, db, monkeypatch) == 0
    pnl, _ = _pnl(db, 4)
    assert pnl is None


def test_idempotent_rerun_is_noop(tmp_path, monkeypatch):
    mod = _load_module()
    db = _make_db(tmp_path, [{
        "id": 5, "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 60000.0, "exit_price": 60100.0, "position_size": 0.01,
        "status": "closed",
    }])
    assert _run_apply(mod, db, monkeypatch) == 0
    first, _ = _pnl(db, 5)
    # second run: WHERE pnl IS NULL guard => no candidates => clean exit
    assert _run_apply(mod, db, monkeypatch) == 0
    second, _ = _pnl(db, 5)
    assert first == second
