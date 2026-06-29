"""M18 P0a — per-trade cost capture (estimator + close-path writer)."""
from __future__ import annotations

import pytest

from src.runtime.trade_costs import estimate_roundtrip_fee_usd
from src.units.db.database import Database


class TestEstimator:
    def test_basic_notional_fee(self):
        # (7.5/1e4) * 100 * 2 * 1 = 0.15
        assert abs(estimate_roundtrip_fee_usd(entry_price=100.0, qty=2.0) - 0.15) < 1e-9

    def test_contract_value_scales_notional(self):
        # futures: cvu=5 → 5x the fee
        base = estimate_roundtrip_fee_usd(entry_price=100.0, qty=2.0, contract_value_usd=1.0)
        fut = estimate_roundtrip_fee_usd(entry_price=100.0, qty=2.0, contract_value_usd=5.0)
        assert abs(fut - 5.0 * base) < 1e-9

    def test_custom_bps(self):
        assert abs(estimate_roundtrip_fee_usd(entry_price=100.0, qty=1.0, fee_bps_roundtrip=10.0) - 0.10) < 1e-9

    def test_negative_bps_clamped(self):
        assert estimate_roundtrip_fee_usd(entry_price=100.0, qty=1.0, fee_bps_roundtrip=-5.0) == 0.0

    def test_bad_inputs_none(self):
        assert estimate_roundtrip_fee_usd(entry_price=None, qty=1.0) is None
        assert estimate_roundtrip_fee_usd(entry_price=100.0, qty=0.0) is None
        assert estimate_roundtrip_fee_usd(entry_price=-1.0, qty=1.0) is None

    def test_zero_cvu_falls_back_to_one(self):
        a = estimate_roundtrip_fee_usd(entry_price=100.0, qty=1.0, contract_value_usd=0.0)
        b = estimate_roundtrip_fee_usd(entry_price=100.0, qty=1.0, contract_value_usd=1.0)
        assert a == b


@pytest.fixture
def db(tmp_path, monkeypatch):
    p = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(p))
    return Database(db_path=str(p))


def _open_trade(db, **over):
    data = {
        "timestamp": "2026-06-29T00:00:00+00:00",
        "symbol": "BTCUSDT", "direction": "long",
        "entry_price": 100.0, "position_size": 2.0,
        "status": "open", "is_backtest": 0, "account_id": "bybit_2",
    }
    data.update(over)
    return db.insert_trade(data)


class TestMigrationAndCloseWriter:
    def test_columns_present(self, db):
        conn = db.connect()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        conn.close()
        assert {"fee_taker_usd", "fee_maker_usd", "funding_paid_usd", "cost_source"} <= cols

    def test_close_stamps_estimate(self, db):
        tid = _open_trade(db)
        db.update_trade(tid, {"status": "closed", "exit_price": 103.0, "pnl": 6.0})
        conn = db.connect()
        fee, src = conn.execute(
            "SELECT fee_taker_usd, cost_source FROM trades WHERE id = ?", (tid,)
        ).fetchone()
        conn.close()
        assert src == "estimate"
        assert abs(fee - 0.15) < 1e-6  # (7.5/1e4)*100*2*1 (BTCUSDT cvu=1)

    def test_backtest_row_not_costed(self, db):
        tid = _open_trade(db, is_backtest=1)
        db.update_trade(tid, {"status": "closed", "exit_price": 103.0, "pnl": 6.0})
        conn = db.connect()
        fee, src = conn.execute(
            "SELECT fee_taker_usd, cost_source FROM trades WHERE id = ?", (tid,)
        ).fetchone()
        conn.close()
        assert fee is None and src is None

    def test_existing_broker_cost_not_overwritten(self, db):
        tid = _open_trade(db, fee_taker_usd=0.99, cost_source="broker")
        db.update_trade(tid, {"status": "closed", "exit_price": 103.0, "pnl": 6.0})
        conn = db.connect()
        fee, src = conn.execute(
            "SELECT fee_taker_usd, cost_source FROM trades WHERE id = ?", (tid,)
        ).fetchone()
        conn.close()
        assert fee == 0.99 and src == "broker"  # broker truth preserved

    def test_non_close_update_does_not_cost(self, db):
        tid = _open_trade(db)
        db.update_trade(tid, {"stop_loss": 99.5})  # an SL move, still open
        conn = db.connect()
        fee, src = conn.execute(
            "SELECT fee_taker_usd, cost_source FROM trades WHERE id = ?", (tid,)
        ).fetchone()
        conn.close()
        assert fee is None and src is None
