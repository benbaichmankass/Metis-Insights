"""Tests for the coordinator's per-signal account-context snapshot hook
(`src.core.coordinator._capture_account_context_snapshots`).

The hook is best-effort + flag-gated; this verifies (a) the happy path
writes one row per eligible account, (b) the kill-switch suppresses
writes, (c) absent/unreachable DB doesn't crash the dispatch.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

# Build the daily_risk_state fixture from the canonical RiskManager DDL so the
# test schema can never drift from production (the prior hand-rolled `utc_date`
# column masked the real bug where daily_state_for queried a column production
# never had — fixed in WC-4).
from src.units.accounts.risk import _CREATE_DAILY_RISK_STATE


@dataclass
class _FakePkg:
    strategy: str = "vwap"
    symbol: str = "BTCUSDT"
    direction: str = "long"


@dataclass
class _FakeAccount:
    name: str
    cached_balance_usd: float | None = None


def _seed_journal(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, "
        "status TEXT, is_backtest INT)"
    )
    conn.execute(_CREATE_DAILY_RISK_STATE)  # canonical schema (PK account_id, date)
    conn.commit()
    conn.close()


def _read_snapshots(path: Path) -> list[dict]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM account_context_snapshots ORDER BY id ASC"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [dict(r) for r in rows]


def test_hook_writes_one_snapshot_per_account(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    _seed_journal(db_path)
    # daily_risk_state seeded for one of the two accounts so we exercise
    # both populated + null branches in a single call.
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO daily_risk_state VALUES (?, ?, ?, ?)",
        ("prop_1", today, -75.0, 10250.0),
    )
    conn.executemany(
        "INSERT INTO trades(account_id, status, is_backtest) VALUES (?, ?, ?)",
        [("prop_1", "open", 0), ("prop_1", "closed", 0), ("prop_2", "open", 0)],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "src.utils.paths.trade_journal_db_path", lambda: str(db_path)
    )
    monkeypatch.delenv("ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED", raising=False)

    from src.core.coordinator import _capture_account_context_snapshots

    _capture_account_context_snapshots(
        order_package_id="pkg-abc",
        pkg=_FakePkg(),
        accounts=[
            _FakeAccount(name="prop_1", cached_balance_usd=10100.0),
            _FakeAccount(name="prop_2", cached_balance_usd=20000.0),
        ],
        live_balances={"prop_1": 10100.0},  # prop_2 falls back to cached
    )

    rows = _read_snapshots(db_path)
    assert len(rows) == 2
    by_account = {r["account_id"]: r for r in rows}

    one = by_account["prop_1"]
    assert one["order_package_id"] == "pkg-abc"
    assert one["strategy_name"] == "vwap"
    assert one["symbol"] == "BTCUSDT"
    assert one["equity"] == 10100.0
    assert one["daily_pnl_realized"] == -75.0
    assert one["daily_equity_high"] == 10250.0
    # drawdown = (10250 - 10100) / 10250 ≈ 0.01463
    assert one["daily_drawdown_pct"] == pytest.approx((10250 - 10100) / 10250)
    assert one["open_trades_count"] == 1  # 1 open + 1 closed → 1 open

    two = by_account["prop_2"]
    assert two["equity"] == 20000.0  # used cached fallback
    assert two["daily_pnl_realized"] is None  # no daily_risk_state row
    assert two["daily_equity_high"] is None
    assert two["daily_drawdown_pct"] is None  # can't compute without peak
    assert two["open_trades_count"] == 1


def test_kill_switch_suppresses_writes(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    _seed_journal(db_path)
    monkeypatch.setattr(
        "src.utils.paths.trade_journal_db_path", lambda: str(db_path)
    )
    monkeypatch.setenv("ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED", "1")

    from src.core.coordinator import _capture_account_context_snapshots
    _capture_account_context_snapshots(
        order_package_id="pkg-suppressed",
        pkg=_FakePkg(),
        accounts=[_FakeAccount(name="prop_1", cached_balance_usd=10000.0)],
        live_balances={},
    )
    assert _read_snapshots(db_path) == []  # table never created


def test_missing_db_does_not_crash(tmp_path: Path, monkeypatch):
    # Best-effort: an unreachable DB must NOT bubble — the trader's
    # dispatch loop can't be blocked on a snapshot write failure.
    monkeypatch.setattr(
        "src.utils.paths.trade_journal_db_path",
        lambda: str(tmp_path / "does-not-exist.db"),
    )
    monkeypatch.delenv("ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED", raising=False)

    from src.core.coordinator import _capture_account_context_snapshots
    _capture_account_context_snapshots(
        order_package_id="pkg-x",
        pkg=_FakePkg(),
        accounts=[_FakeAccount(name="prop_1", cached_balance_usd=1.0)],
        live_balances={},
    )
    # No raise = pass.


def test_no_accounts_is_noop(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    _seed_journal(db_path)
    monkeypatch.setattr(
        "src.utils.paths.trade_journal_db_path", lambda: str(db_path)
    )
    from src.core.coordinator import _capture_account_context_snapshots
    _capture_account_context_snapshots(
        order_package_id="pkg-empty",
        pkg=_FakePkg(),
        accounts=[],
        live_balances={},
    )
    assert _read_snapshots(db_path) == []
