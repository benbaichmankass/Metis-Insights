"""Tests for scripts/ops/backfill_tpsl_leg_ids.py
(BL-20260721-BYBIT2-XRP-TPSL-LEGCAP structural-fix completion for
already-open positions).
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "backfill_tpsl_leg_ids",
    Path(__file__).resolve().parents[2] / "scripts" / "ops" / "backfill_tpsl_leg_ids.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)  # type: ignore[union-attr]


def _seed_trades(db: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, account_id TEXT, "
        "status TEXT, sl_order_id TEXT, tp_order_id TEXT, is_backtest INTEGER)"
    )
    for r in rows:
        conn.execute(
            "INSERT INTO trades (id, symbol, account_id, status, sl_order_id, tp_order_id, "
            "is_backtest) VALUES (:id,:symbol,:account_id,:status,:sl_order_id,:tp_order_id,"
            ":is_backtest)",
            r,
        )
    conn.commit()
    conn.close()


def _fetch(db: Path, trade_id: int):
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT sl_order_id, tp_order_id FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    conn.close()
    return row


class _StubClient:
    def __init__(self, legs):
        self._legs = legs

    def get_open_orders(self, *, category, symbol, orderFilter):
        return {"result": {"list": self._legs}}


def _fake_account(account_id):
    return {"account_id": account_id, "exchange": "bybit", "market_type": "futures"}


@pytest.fixture(autouse=True)
def _patch_account_and_client(monkeypatch):
    monkeypatch.setattr(_MOD, "_load_account", _fake_account)
    monkeypatch.setattr(_MOD, "_category", lambda cfg: "linear")


def _sl_leg(order_id="sl-1", created="200"):
    return {"orderId": order_id, "stopOrderType": "StopLoss", "qty": "165.5",
            "triggerPrice": "1.0855", "orderStatus": "Untriggered", "createdTime": created}


def _tp_leg(order_id="tp-1", created="200"):
    return {"orderId": order_id, "stopOrderType": "TakeProfit", "qty": "165.5",
            "triggerPrice": "1.20", "orderStatus": "Untriggered", "createdTime": created}


def test_non_bybit_account_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(_MOD, "_load_account", lambda a: {"account_id": a, "exchange": "alpaca"})
    result = _MOD.backfill_leg_ids("alpaca_live", "AAPL", apply=False, db_path=str(tmp_path / "x.db"))
    assert result["ok"] is False
    assert "not Bybit" in result["detail"]


def test_flat_position_aborts(monkeypatch, tmp_path):
    monkeypatch.setattr(_MOD, "_live_position_size", lambda cfg, sym: 0.0)
    result = _MOD.backfill_leg_ids("bybit_2", "XRPUSDT", apply=False, db_path=str(tmp_path / "x.db"))
    assert result["action"] == "abort_flat"
    assert result["ok"] is False


def test_unreadable_position_aborts(monkeypatch, tmp_path):
    monkeypatch.setattr(_MOD, "_live_position_size", lambda cfg, sym: None)
    result = _MOD.backfill_leg_ids("bybit_2", "XRPUSDT", apply=False, db_path=str(tmp_path / "x.db"))
    assert result["action"] == "abort_unreadable"
    assert result["ok"] is False


def test_ambiguous_legs_aborts(monkeypatch, tmp_path):
    monkeypatch.setattr(_MOD, "_live_position_size", lambda cfg, sym: 165.5)
    monkeypatch.setattr(_MOD, "_build_client", lambda cfg: _StubClient([_sl_leg("sl-1"), _sl_leg("sl-2")]))
    result = _MOD.backfill_leg_ids("bybit_2", "XRPUSDT", apply=False, db_path=str(tmp_path / "x.db"))
    assert result["action"] == "abort_ambiguous_legs"
    assert result["ok"] is False


def test_no_legs_aborts(monkeypatch, tmp_path):
    monkeypatch.setattr(_MOD, "_live_position_size", lambda cfg, sym: 165.5)
    monkeypatch.setattr(_MOD, "_build_client", lambda cfg: _StubClient([]))
    result = _MOD.backfill_leg_ids("bybit_2", "XRPUSDT", apply=False, db_path=str(tmp_path / "x.db"))
    assert result["action"] == "abort_no_legs"
    assert result["ok"] is False


def test_no_candidate_rows_is_noop(monkeypatch, tmp_path):
    db = tmp_path / "trade_journal.db"
    _seed_trades(db, [
        {"id": 1, "symbol": "XRPUSDT", "account_id": "bybit_2", "status": "open",
         "sl_order_id": "already", "tp_order_id": "already", "is_backtest": 0},
    ])
    monkeypatch.setattr(_MOD, "_live_position_size", lambda cfg, sym: 165.5)
    monkeypatch.setattr(_MOD, "_build_client", lambda cfg: _StubClient([_sl_leg(), _tp_leg()]))
    result = _MOD.backfill_leg_ids("bybit_2", "XRPUSDT", apply=True, db_path=str(db))
    assert result["action"] == "noop_no_candidate_rows"
    assert result["ok"] is True
    assert _fetch(db, 1) == ("already", "already")


def test_ambiguous_trade_rows_aborts(monkeypatch, tmp_path):
    db = tmp_path / "trade_journal.db"
    _seed_trades(db, [
        {"id": 1, "symbol": "XRPUSDT", "account_id": "bybit_2", "status": "open",
         "sl_order_id": None, "tp_order_id": None, "is_backtest": 0},
        {"id": 2, "symbol": "XRPUSDT", "account_id": "bybit_2", "status": "open",
         "sl_order_id": None, "tp_order_id": None, "is_backtest": 0},
    ])
    monkeypatch.setattr(_MOD, "_live_position_size", lambda cfg, sym: 165.5)
    monkeypatch.setattr(_MOD, "_build_client", lambda cfg: _StubClient([_sl_leg(), _tp_leg()]))
    result = _MOD.backfill_leg_ids("bybit_2", "XRPUSDT", apply=True, db_path=str(db))
    assert result["action"] == "abort_ambiguous_trades"
    assert result["ok"] is False
    assert _fetch(db, 1) == (None, None)
    assert _fetch(db, 2) == (None, None)


def test_dry_run_writes_nothing(monkeypatch, tmp_path):
    db = tmp_path / "trade_journal.db"
    _seed_trades(db, [
        {"id": 1, "symbol": "XRPUSDT", "account_id": "bybit_2", "status": "open",
         "sl_order_id": None, "tp_order_id": None, "is_backtest": 0},
    ])
    monkeypatch.setattr(_MOD, "_live_position_size", lambda cfg, sym: 165.5)
    monkeypatch.setattr(_MOD, "_build_client", lambda cfg: _StubClient([_sl_leg("sl-9"), _tp_leg("tp-9")]))
    result = _MOD.backfill_leg_ids("bybit_2", "XRPUSDT", apply=False, db_path=str(db))
    assert result["action"] == "dry_run"
    assert result["ok"] is True
    assert result["plan"]["updates"] == {"sl_order_id": "sl-9", "tp_order_id": "tp-9"}
    assert _fetch(db, 1) == (None, None)


def test_apply_backfills_only_null_columns(monkeypatch, tmp_path):
    db = tmp_path / "trade_journal.db"
    _seed_trades(db, [
        # tp_order_id already populated — must never be overwritten.
        {"id": 1, "symbol": "XRPUSDT", "account_id": "bybit_2", "status": "open",
         "sl_order_id": None, "tp_order_id": "keep-me", "is_backtest": 0},
    ])
    monkeypatch.setattr(_MOD, "_live_position_size", lambda cfg, sym: 165.5)
    monkeypatch.setattr(_MOD, "_build_client", lambda cfg: _StubClient([_sl_leg("sl-42"), _tp_leg("tp-99")]))
    result = _MOD.backfill_leg_ids("bybit_2", "XRPUSDT", apply=True, db_path=str(db))
    assert result["action"] == "backfilled"
    assert result["ok"] is True
    assert result["plan"]["updates"] == {"sl_order_id": "sl-42"}
    assert _fetch(db, 1) == ("sl-42", "keep-me")


def test_apply_is_idempotent(monkeypatch, tmp_path):
    db = tmp_path / "trade_journal.db"
    _seed_trades(db, [
        {"id": 1, "symbol": "XRPUSDT", "account_id": "bybit_2", "status": "open",
         "sl_order_id": None, "tp_order_id": None, "is_backtest": 0},
    ])
    monkeypatch.setattr(_MOD, "_live_position_size", lambda cfg, sym: 165.5)
    monkeypatch.setattr(_MOD, "_build_client", lambda cfg: _StubClient([_sl_leg("sl-1"), _tp_leg("tp-1")]))
    first = _MOD.backfill_leg_ids("bybit_2", "XRPUSDT", apply=True, db_path=str(db))
    assert first["action"] == "backfilled"
    second = _MOD.backfill_leg_ids("bybit_2", "XRPUSDT", apply=True, db_path=str(db))
    assert second["action"] == "noop_no_candidate_rows"
    assert _fetch(db, 1) == ("sl-1", "tp-1")


def test_backtest_rows_are_excluded(monkeypatch, tmp_path):
    db = tmp_path / "trade_journal.db"
    _seed_trades(db, [
        {"id": 1, "symbol": "XRPUSDT", "account_id": "bybit_2", "status": "open",
         "sl_order_id": None, "tp_order_id": None, "is_backtest": 1},
    ])
    monkeypatch.setattr(_MOD, "_live_position_size", lambda cfg, sym: 165.5)
    monkeypatch.setattr(_MOD, "_build_client", lambda cfg: _StubClient([_sl_leg(), _tp_leg()]))
    result = _MOD.backfill_leg_ids("bybit_2", "XRPUSDT", apply=True, db_path=str(db))
    assert result["action"] == "noop_no_candidate_rows"
    assert _fetch(db, 1) == (None, None)
