"""Unit tests for the selective-flip conditional re-entry (Unit A § 7.2).

Covers: the pure re-entry decision gates (evaluate_reentry), the displaced
record round-trip persistence on order_packages.meta, and the
arm-on-close → consume-on-signal lifecycle including the STALE-skip path
(never resurrect a stale signal).
"""
import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.runtime.flip_reentry import (  # noqa: E402
    STATUS_ARMED_PENDING,
    STATUS_ARMED_READY,
    STATUS_REENTERED,
    DisplacedIntent,
    arm_ready_on_scalp_close,
    consume_reentry_for_signal,
    evaluate_reentry,
    load_displaced_intent,
    persist_displaced_intent,
    set_displaced_status,
)


def _record(**kw) -> DisplacedIntent:
    base = dict(
        account="bybit_2", symbol="BTCUSDT", strategy="trend_donchian",
        side="long", entry=50_000.0, confidence=0.2, regime="trend",
        order_package_id="pkg-h", displaced_at=time.time(),
        window_bars=8.0, bar_seconds=3600.0, status=STATUS_ARMED_PENDING,
    )
    base.update(kw)
    return DisplacedIntent(**base)


class TestEvaluateReentry:
    def test_reenters_when_all_gates_pass(self):
        rec = _record()
        d = evaluate_reentry(
            rec, signal_side="long", signal_confidence=0.5,
            signal_price=50_010.0, signal_regime="trend",
        )
        assert d.reenter is True
        assert d.reason.startswith("flip_reentry_ok")

    def test_skip_no_live_signal(self):
        rec = _record()
        d = evaluate_reentry(
            rec, signal_side=None, signal_confidence=0.5,
            signal_price=50_010.0, signal_regime="trend",
        )
        assert d.reenter is False
        assert "no_live_signal" in d.reason

    def test_skip_side_changed(self):
        rec = _record(side="long")
        d = evaluate_reentry(
            rec, signal_side="short", signal_confidence=0.5,
            signal_price=50_010.0, signal_regime="trend",
        )
        assert d.reenter is False
        assert "side_changed" in d.reason

    def test_skip_out_of_zone(self, monkeypatch):
        monkeypatch.setenv("FLIP_REENTRY_ZONE_FRAC", "0.005")
        rec = _record(entry=50_000.0)
        d = evaluate_reentry(
            rec, signal_side="long", signal_confidence=0.5,
            signal_price=51_000.0, signal_regime="trend",  # 2% drift > 0.5%
        )
        assert d.reenter is False
        assert "out_of_zone" in d.reason

    def test_skip_low_confidence(self, monkeypatch):
        monkeypatch.setenv("FLIP_REENTRY_MIN_CONFIDENCE", "0.4")
        rec = _record()
        d = evaluate_reentry(
            rec, signal_side="long", signal_confidence=0.1,
            signal_price=50_010.0, signal_regime="trend",
        )
        assert d.reenter is False
        assert "low_confidence" in d.reason

    def test_skip_regime_changed(self):
        rec = _record(regime="trend")
        d = evaluate_reentry(
            rec, signal_side="long", signal_confidence=0.5,
            signal_price=50_010.0, signal_regime="range",
        )
        assert d.reenter is False
        assert "regime_changed" in d.reason

    def test_skip_window_expired(self, monkeypatch):
        monkeypatch.setenv("FLIP_REENTRY_WINDOW_BARS", "8")
        # bar_seconds=3600, window 8 bars = 8h; displaced 9h ago → expired.
        rec = _record(displaced_at=time.time() - 9 * 3600, bar_seconds=3600.0)
        d = evaluate_reentry(
            rec, signal_side="long", signal_confidence=0.5,
            signal_price=50_010.0, signal_regime="trend",
        )
        assert d.reenter is False
        assert "window_expired" in d.reason

    def test_regime_permissive_when_unknown(self):
        # A missing regime on either side never blocks an otherwise-valid re-entry.
        rec = _record(regime=None)
        d = evaluate_reentry(
            rec, signal_side="long", signal_confidence=0.5,
            signal_price=50_010.0, signal_regime=None,
        )
        assert d.reenter is True


# ---------------------------------------------------------------------------
# Persistence + lifecycle (projected onto order_packages.meta)
# ---------------------------------------------------------------------------


def _seed_db(tmp_path, *, op_id="pkg-h", strategy="trend_donchian",
             symbol="BTCUSDT", entry=50_000.0):
    """Create a trade_journal.db with one order_packages row to project onto."""
    from src.units.db.database import Database

    db_path = str(tmp_path / "trade_journal.db")
    db = Database(db_path=db_path)
    db.insert_order_package({
        "order_package_id": op_id,
        "strategy_name": strategy,
        "symbol": symbol,
        "direction": "long",
        "entry": entry,
        "sl": 49_500.0,
        "tp": 51_500.0,
        "status": "open",
    })
    return db_path, db


class TestPersistenceRoundTrip:
    def test_persist_and_load(self, tmp_path):
        _, db = _seed_db(tmp_path)
        rec = _record(order_package_id="pkg-h")
        assert persist_displaced_intent(rec, db=db) is True
        loaded = load_displaced_intent("pkg-h", db=db)
        assert loaded is not None
        assert loaded.strategy == "trend_donchian"
        assert loaded.side == "long"
        assert loaded.status == STATUS_ARMED_PENDING

    def test_persist_no_op_id_returns_false(self):
        rec = _record(order_package_id=None)
        assert persist_displaced_intent(rec) is False

    def test_set_status(self, tmp_path):
        _, db = _seed_db(tmp_path)
        persist_displaced_intent(_record(order_package_id="pkg-h"), db=db)
        assert set_displaced_status("pkg-h", STATUS_ARMED_READY, db=db) is True
        assert load_displaced_intent("pkg-h", db=db).status == STATUS_ARMED_READY


class TestArmAndConsumeLifecycle:
    def test_arm_ready_on_scalp_close(self, tmp_path):
        _, db = _seed_db(tmp_path)
        persist_displaced_intent(_record(order_package_id="pkg-h"), db=db)
        # Scalp closes → pending record flips to ready.
        assert arm_ready_on_scalp_close("bybit_2", "BTCUSDT", db=db) is True
        assert load_displaced_intent("pkg-h", db=db).status == STATUS_ARMED_READY

    def test_arm_no_pending_record_is_noop(self, tmp_path):
        _, db = _seed_db(tmp_path)
        # No displaced record on the row → nothing to arm.
        assert arm_ready_on_scalp_close("bybit_2", "BTCUSDT", db=db) is False

    def test_consume_reenters_and_terminalises(self, tmp_path):
        _, db = _seed_db(tmp_path)
        persist_displaced_intent(
            _record(order_package_id="pkg-h", status=STATUS_ARMED_READY), db=db)
        d = consume_reentry_for_signal(
            account="bybit_2", symbol="BTCUSDT", strategy="trend_donchian",
            signal_side="long", signal_confidence=0.5,
            signal_price=50_010.0, signal_regime="trend", db=db,
        )
        assert d is not None and d.reenter is True
        assert load_displaced_intent("pkg-h", db=db).status == STATUS_REENTERED

    def test_consume_stale_skips_and_terminalises(self, tmp_path, monkeypatch):
        # The stale-skip path: signal flipped to the wrong side → skip + terminal.
        _, db = _seed_db(tmp_path)
        persist_displaced_intent(
            _record(order_package_id="pkg-h", status=STATUS_ARMED_READY,
                    side="long"), db=db)
        d = consume_reentry_for_signal(
            account="bybit_2", symbol="BTCUSDT", strategy="trend_donchian",
            signal_side="short", signal_confidence=0.5,
            signal_price=50_010.0, signal_regime="trend", db=db,
        )
        assert d is not None and d.reenter is False
        assert "side_changed" in d.reason
        assert load_displaced_intent("pkg-h", db=db).status.startswith("skipped:")

    def test_consume_no_record_returns_none(self, tmp_path):
        _, db = _seed_db(tmp_path)
        d = consume_reentry_for_signal(
            account="bybit_2", symbol="BTCUSDT", strategy="trend_donchian",
            signal_side="long", signal_confidence=0.5,
            signal_price=50_010.0, signal_regime="trend", db=db,
        )
        assert d is None

    def test_consume_wrong_strategy_returns_none(self, tmp_path):
        _, db = _seed_db(tmp_path)
        persist_displaced_intent(
            _record(order_package_id="pkg-h", status=STATUS_ARMED_READY,
                    strategy="trend_donchian"), db=db)
        # A DIFFERENT strategy's signal must not consume this record.
        d = consume_reentry_for_signal(
            account="bybit_2", symbol="BTCUSDT", strategy="vwap",
            signal_side="long", signal_confidence=0.5,
            signal_price=50_010.0, signal_regime="trend", db=db,
        )
        assert d is None
