"""M18 P0a — per-trade cost capture (estimator + close-path writer)."""
from __future__ import annotations

import pytest

from src.runtime.trade_costs import estimate_roundtrip_fee_usd
from src.units.db.database import Database


class TestVenueAwareFeeBps:
    """`roundtrip_fee_bps_for` — commission-free equities resolve to 0 bps so the
    close-path estimate doesn't over-charge them the crypto-perp default (the
    `spy_pullback_1h` net-R sign-flip artifact, M24 2026-07-29)."""

    def _resolver(self):
        import src.core.profile_loader as pl
        pl._ROUNDTRIP_FEE_BPS_CACHE = None  # force a reload of the real config
        return pl.roundtrip_fee_bps_for

    def test_alpaca_spot_equities_are_commission_free(self):
        r = self._resolver()
        # every (alpaca, spot) roster row, regardless of underlying asset_class
        for sym in ("SPY", "QQQ", "IWM", "GLD", "SLV", "TLT", "USO", "GDX"):
            assert r(sym) == 0.0, sym

    def test_other_venues_defer_to_estimator_default(self):
        r = self._resolver()
        for sym in ("BTCUSDT", "MES", "MGC", "EURUSD"):
            assert r(sym) is None, sym

    def test_unknown_and_empty_defer(self):
        r = self._resolver()
        assert r("ZZZ_NOT_A_SYMBOL") is None
        assert r("") is None
        assert r(None) is None  # type: ignore[arg-type]

    def test_composed_fee_zero_for_equity_nonzero_for_crypto(self):
        r = self._resolver()
        # equity: 0 bps → $0 fee; crypto: default 7.5 bps → real fee
        eq_bps = r("SPY")
        assert estimate_roundtrip_fee_usd(
            entry_price=550.0, qty=2.0, fee_bps_roundtrip=eq_bps) == 0.0
        assert estimate_roundtrip_fee_usd(entry_price=550.0, qty=2.0) > 0.0


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

    def test_close_stamps_zero_for_commission_free_equity(self, db):
        # SPY (alpaca spot) is commission-free → estimate fee is $0, still
        # cost_source='estimate' (a modelled-zero, not a missing cost). This is
        # the spy_pullback_1h net-R sign-flip fix (M24 2026-07-29).
        import src.core.profile_loader as pl
        pl._ROUNDTRIP_FEE_BPS_CACHE = None
        tid = _open_trade(db, symbol="SPY", account_id="alpaca_paper",
                          entry_price=550.0, position_size=2.0)
        db.update_trade(tid, {"status": "closed", "exit_price": 560.0, "pnl": 20.0})
        conn = db.connect()
        fee, src = conn.execute(
            "SELECT fee_taker_usd, cost_source FROM trades WHERE id = ?", (tid,)
        ).fetchone()
        conn.close()
        assert src == "estimate"
        assert fee == 0.0

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
