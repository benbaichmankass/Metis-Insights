"""Liveness watchdog regression tests.

Per CLAUDE.md § Architecture rules § 6 + architecture-audit-2026-05-02
P0-3, the watchdog pings the operator when N actionable signals fire
but 0 trades land in the same window. Pre-fix (BUG-034) the operator
was the watchdog — the silent-execution mode hid for an unknown
duration before they noticed it manually.

The tests pin:
  - fires when threshold breached + zero fills,
  - stays quiet on healthy or quiet windows,
  - dedupes per-slot,
  - never raises,
  - enqueues a JSON to runtime_logs/pending_pings/.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.runtime.liveness_watchdog import (
    DEFAULT_SIGNAL_THRESHOLD,
    check_liveness,
    run_liveness_watchdog,
)


@pytest.fixture()
def tmp_dirs(tmp_path, monkeypatch):
    """Tmp signal-audit + tmp trade journal + tmp pending-pings + tmp state."""
    audit = tmp_path / "signal_audit.jsonl"
    db = tmp_path / "trade_journal.db"
    # _enqueue_liveness_ping writes to ``runtime_logs_dir() / pending_pings``;
    # RUNTIME_LOGS_DIR redirects that root to tmp_path.
    pending = tmp_path / "runtime_logs" / "pending_pings"
    state = tmp_path / "liveness_watchdog_state.json"

    monkeypatch.setenv("RUNTIME_LOGS_DIR", str(tmp_path / "runtime_logs"))
    monkeypatch.setattr(
        "src.runtime.liveness_watchdog._SIGNAL_AUDIT", audit,
    )
    monkeypatch.setattr(
        "src.runtime.liveness_watchdog._STATE_FILE", state,
    )
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db))
    return {
        "audit": audit,
        "db": db,
        "pending": pending,
        "state": state,
        "now": datetime(2026, 5, 2, 21, 30, tzinfo=timezone.utc),
    }


def _write_signal_rows(audit_path: Path, *, count: int, now_utc: datetime,
                       side="buy", status="multi_account_dispatched"):
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as fh:
        for i in range(count):
            ts = (now_utc - timedelta(minutes=i + 1)).isoformat()
            row = {
                "logged_at_utc": ts,
                "event": "pipeline_result",
                "strategy": "vwap",
                "symbol": "BTCUSDT",
                "side": side,
                "status": status,
                "qty": None,
            }
            fh.write(json.dumps(row) + "\n")


def _seed_journal(db_path: Path, *, count: int, now_utc: datetime, is_backtest=0):
    """Create the trades table and insert N rows in the lookback window."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            symbol TEXT,
            direction TEXT,
            entry_price REAL,
            position_size REAL,
            status TEXT,
            is_backtest INTEGER DEFAULT 0,
            strategy_name TEXT,
            account_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    for i in range(count):
        ts = (now_utc - timedelta(minutes=i + 5)).isoformat()
        conn.execute(
            "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
            "position_size, status, is_backtest, strategy_name, account_id, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, "BTCUSDT", "long", 50000.0, 0.001, "open",
             is_backtest, "vwap", "bybit_2", ts),
        )
    conn.commit()
    conn.close()


def _seed_rejected(db_path: Path, *, now_utc: datetime, reason: str,
                   status: str = "rejected", count: int = 1):
    """Insert N ``status='rejected'`` ``trades`` rows whose ``entry_reason``
    carries ``reason`` — the shape the order layer journals when it refuses a
    signal. Creates the table WITH the ``entry_reason`` column the real
    schema has (the minimal ``_seed_journal`` table omits it)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            symbol TEXT,
            direction TEXT,
            entry_price REAL,
            position_size REAL,
            status TEXT,
            entry_reason TEXT,
            is_backtest INTEGER DEFAULT 0,
            strategy_name TEXT,
            account_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    for i in range(count):
        ts = (now_utc - timedelta(minutes=i + 5)).isoformat()
        conn.execute(
            "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
            "position_size, status, entry_reason, is_backtest, strategy_name, "
            "account_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, "ETHUSDT", "long", 1628.0, 0.0, status,
             f"REJECTED: {reason} | trend_donchian_eth signal",
             0, "trend_donchian_eth", "bybit_2", ts),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Pure decision (check_liveness)
# ---------------------------------------------------------------------------


class TestCheckLiveness:
    def test_threshold_breached_and_zero_fills_fires(self, tmp_dirs):
        _write_signal_rows(tmp_dirs["audit"], count=10, now_utc=tmp_dirs["now"])
        result = check_liveness(now_utc=tmp_dirs["now"])
        assert result.fired is True
        assert result.signals_actionable == 10
        assert result.trades_placed == 0
        assert "liveness_alert" in result.reason

    def test_threshold_breached_with_fills_does_not_fire(self, tmp_dirs):
        _write_signal_rows(tmp_dirs["audit"], count=10, now_utc=tmp_dirs["now"])
        _seed_journal(tmp_dirs["db"], count=2, now_utc=tmp_dirs["now"])
        result = check_liveness(now_utc=tmp_dirs["now"])
        assert result.fired is False
        assert result.signals_actionable == 10
        assert result.trades_placed == 2
        assert "healthy" in result.reason

    def test_quiet_window_does_not_fire(self, tmp_dirs):
        _write_signal_rows(
            tmp_dirs["audit"], count=DEFAULT_SIGNAL_THRESHOLD - 1,
            now_utc=tmp_dirs["now"],
        )
        result = check_liveness(now_utc=tmp_dirs["now"])
        assert result.fired is False
        assert "below_threshold" in result.reason

    def test_non_actionable_status_does_not_count(self, tmp_dirs):
        # 10 signals but with status='halted' — pipeline never tried to
        # execute, so a 0-fill window is correct, not a liveness miss.
        _write_signal_rows(
            tmp_dirs["audit"], count=10, now_utc=tmp_dirs["now"],
            status="halted",
        )
        result = check_liveness(now_utc=tmp_dirs["now"])
        assert result.fired is False
        assert result.signals_actionable == 0

    def test_signals_outside_window_excluded(self, tmp_dirs):
        # 10 signals from 3 hours ago — outside the 1-hour default window.
        old_time = tmp_dirs["now"] - timedelta(hours=3)
        _write_signal_rows(tmp_dirs["audit"], count=10, now_utc=old_time)
        result = check_liveness(now_utc=tmp_dirs["now"])
        assert result.signals_actionable == 0
        assert result.fired is False

    def test_backtest_trades_dont_count_as_fills(self, tmp_dirs):
        # 10 signals + 5 backtest trade rows in the window → still fires
        # because is_backtest=1 doesn't count as a real fill.
        _write_signal_rows(tmp_dirs["audit"], count=10, now_utc=tmp_dirs["now"])
        _seed_journal(
            tmp_dirs["db"], count=5, now_utc=tmp_dirs["now"], is_backtest=1,
        )
        result = check_liveness(now_utc=tmp_dirs["now"])
        assert result.fired is True
        assert result.trades_placed == 0

    def test_held_position_netting_guard_suppresses(self, tmp_dirs):
        # 10 dispatched signals, 0 fills, but the order layer journaled a
        # netting-guard refusal (already holding the position) — the 0-fill is
        # EXPLAINED, not a silent gap. The 2026-06-29 false-positive case.
        _write_signal_rows(tmp_dirs["audit"], count=10, now_utc=tmp_dirs["now"])
        _seed_rejected(
            tmp_dirs["db"], now_utc=tmp_dirs["now"],
            reason="reentry_suppressed_netting_guard:increase",
        )
        result = check_liveness(now_utc=tmp_dirs["now"])
        assert result.fired is False
        assert result.trades_placed == 0
        assert "held_or_suppressed" in result.reason

    def test_hold_policy_flip_suppression_suppresses(self, tmp_dirs):
        # FLIP_POLICY=hold refusal (signal opposes a held position) is also an
        # intentional hold, not a silent execution gap.
        _write_signal_rows(tmp_dirs["audit"], count=10, now_utc=tmp_dirs["now"])
        _seed_rejected(
            tmp_dirs["db"], now_utc=tmp_dirs["now"],
            reason=("intent_noop:flip_suppressed_hold_policy: desired long "
                    "opposes current short (qty=18.03); holding for owner exit"),
        )
        result = check_liveness(now_utc=tmp_dirs["now"])
        assert result.fired is False
        assert "held_or_suppressed" in result.reason

    def test_error_class_rejections_still_fire(self, tmp_dirs):
        # A 0-fill window explained only by an ERROR-class refusal
        # (risk_refused — account can't size anything) is NOT an intentional
        # hold: the watchdog must still fire so the operator sees the problem.
        _write_signal_rows(tmp_dirs["audit"], count=10, now_utc=tmp_dirs["now"])
        _seed_rejected(
            tmp_dirs["db"], now_utc=tmp_dirs["now"],
            reason=("risk_refused: sized_qty=0 with balance=150.00 — check "
                    "daily-loss budget / liquidation buffer / max_borrow"),
        )
        result = check_liveness(now_utc=tmp_dirs["now"])
        assert result.fired is True
        assert "liveness_alert" in result.reason

    def test_silent_gap_with_no_journal_rows_still_fires(self, tmp_dirs):
        # No trades rows at all (neither filled nor rejected) — the true
        # silent-execution gap (BUG-034). Must still fire.
        _write_signal_rows(tmp_dirs["audit"], count=10, now_utc=tmp_dirs["now"])
        result = check_liveness(now_utc=tmp_dirs["now"])
        assert result.fired is True
        assert "liveness_alert" in result.reason


# ---------------------------------------------------------------------------
# run_liveness_watchdog — full path with side-effects
# ---------------------------------------------------------------------------


class TestRunLivenessWatchdog:
    def test_fires_enqueues_ping_and_persists_state(self, tmp_dirs):
        _write_signal_rows(tmp_dirs["audit"], count=10, now_utc=tmp_dirs["now"])

        result = run_liveness_watchdog(now_utc=tmp_dirs["now"])

        assert result.fired is True
        # Ping landed in the pending-pings inbox.
        files = list(tmp_dirs["pending"].glob("*-liveness.json"))
        assert len(files) == 1
        payload = json.loads(files[0].read_text())
        assert payload["priority"] == "urgent"
        assert "Liveness watchdog" in payload["body"]
        assert "10 actionable signals" in payload["body"]
        # State file records the slot so we don't ping twice in the
        # same hour.
        state = json.loads(tmp_dirs["state"].read_text())
        assert state["last_alert_slot"] == result.slot_key

    def test_dedupes_within_same_slot(self, tmp_dirs):
        _write_signal_rows(tmp_dirs["audit"], count=10, now_utc=tmp_dirs["now"])

        run_liveness_watchdog(now_utc=tmp_dirs["now"])
        # 5 minutes later — same hour-slot.
        run_liveness_watchdog(now_utc=tmp_dirs["now"] + timedelta(minutes=5))

        files = list(tmp_dirs["pending"].glob("*-liveness.json"))
        assert len(files) == 1, (
            f"second call in same slot must dedupe; got {len(files)} files"
        )

    def test_re_arms_in_next_slot(self, tmp_dirs):
        _write_signal_rows(tmp_dirs["audit"], count=10, now_utc=tmp_dirs["now"])

        run_liveness_watchdog(now_utc=tmp_dirs["now"])
        # 1 hour later — fresh slot, condition still holds.
        next_hour = tmp_dirs["now"] + timedelta(hours=1)
        _write_signal_rows(tmp_dirs["audit"], count=10, now_utc=next_hour)
        run_liveness_watchdog(now_utc=next_hour)

        files = list(tmp_dirs["pending"].glob("*-liveness.json"))
        assert len(files) == 2

    def test_healthy_does_not_enqueue_or_persist(self, tmp_dirs):
        _write_signal_rows(tmp_dirs["audit"], count=10, now_utc=tmp_dirs["now"])
        _seed_journal(tmp_dirs["db"], count=3, now_utc=tmp_dirs["now"])

        result = run_liveness_watchdog(now_utc=tmp_dirs["now"])

        assert result.fired is False
        assert not list(tmp_dirs["pending"].glob("*-liveness.json"))
        assert not tmp_dirs["state"].exists()

    def test_held_position_window_enqueues_nothing(self, tmp_dirs):
        # End-to-end: a held-position 0-fill window must NOT enqueue an
        # URGENT ping and must NOT persist an alert slot.
        _write_signal_rows(tmp_dirs["audit"], count=10, now_utc=tmp_dirs["now"])
        _seed_rejected(
            tmp_dirs["db"], now_utc=tmp_dirs["now"],
            reason="reentry_suppressed_netting_guard:increase",
        )
        result = run_liveness_watchdog(now_utc=tmp_dirs["now"])
        assert result.fired is False
        assert not list(tmp_dirs["pending"].glob("*-liveness.json"))
        assert not tmp_dirs["state"].exists()

    def test_never_raises_on_corrupt_audit(self, tmp_dirs):
        # Corrupt JSONL — should not crash.
        tmp_dirs["audit"].parent.mkdir(parents=True, exist_ok=True)
        tmp_dirs["audit"].write_text("this is not json\n{bad json\n")

        # Must not raise.
        result = run_liveness_watchdog(now_utc=tmp_dirs["now"])
        assert result.fired is False
        assert result.signals_actionable == 0
