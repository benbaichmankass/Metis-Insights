"""Tests for the self-healing ``daily_risk_state`` persistence in
``src.units.accounts.risk.RiskManager`` (S-PERSIST-CANON, 2026-05-23).

Before this change nothing fed ``daily_pnl`` / ``daily_high_equity`` at
runtime — ``record_trade_result()`` / ``update_equity()`` had zero runtime
callers — so the ``daily_risk_state`` table stayed empty and the
per-account daily-loss / max-drawdown caps reset to 0 on every trader
restart (and never accumulated within a session either).

The fix rebuilds today's state from the canonical journal (realized PnL
summed from ``trades``) + the balance snapshot on init and on every gate
check, and persists it. These tests prove:

* the recompute populates ``daily_pnl`` and writes a ``daily_risk_state``
  row,
* the daily-loss cap actually engages from journal-derived PnL,
* state survives a "restart" (a fresh RiskManager instance),
* a missing journal is best-effort (no crash, in-memory),
* ``account_id=""`` disables persistence entirely (test/one-off callers).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager
from src.units.db.database import Database


def _today() -> str:
    return str(datetime.now(timezone.utc).date())


def _seed_closed_trade(db_path: str, account_id: str, pnl: float) -> None:
    db = Database(db_path=db_path)
    db.insert_trade({
        "timestamp": f"{_today()}T12:00:00+00:00",
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry_price": 50_000.0,
        "position_size": 0.01,
        "status": "closed",
        "pnl": pnl,
        "is_backtest": 0,
        "account_id": account_id,
        "created_at": f"{_today()} 12:00:00",
    })


def _order(estimated_value: float = 100.0) -> OrderPackage:
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="long",
        entry=50_000.0,
        sl=49_000.0,
        tp=52_000.0,
        confidence=0.8,
        meta={"strategy_name": "vwap", "estimated_value": estimated_value},
    )


@pytest.fixture
def journal_env(tmp_path, monkeypatch):
    """Point TRADE_JOURNAL_DB at a temp DB and DATA_DIR at an empty temp
    dir (so the balance-snapshot read finds nothing and doesn't perturb
    the PnL-only assertions)."""
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data-root"))
    return str(db_path)


def _read_daily_risk_rows(db_path: str, account_id: str):
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT account_id, date, daily_pnl FROM daily_risk_state "
            "WHERE account_id=?",
            (account_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        # No daily_risk_state table at all → persistence never ran
        # (e.g. account_id=""). That is itself "no rows".
        return []
    finally:
        conn.close()


def test_recompute_populates_daily_pnl_and_persists_row(journal_env):
    _seed_closed_trade(journal_env, "acc1", -60.0)
    _seed_closed_trade(journal_env, "acc1", -50.0)

    rm = RiskManager({"daily_usd": 100.0}, account_id="acc1")

    # daily_pnl rebuilt from the journal (−60 + −50).
    assert rm.daily_pnl == pytest.approx(-110.0)
    # And a daily_risk_state row was persisted for today.
    rows = _read_daily_risk_rows(journal_env, "acc1")
    assert len(rows) == 1
    assert rows[0][1] == _today()
    assert rows[0][2] == pytest.approx(-110.0)


def test_daily_loss_cap_engages_from_journal(journal_env):
    _seed_closed_trade(journal_env, "acc2", -150.0)  # past the 100 cap
    rm = RiskManager({"daily_usd": 100.0}, account_id="acc2")

    ok, reason = rm.evaluate(_order())
    assert ok is False
    assert reason == "DAILY_LOSS_CAP"


def test_within_cap_allows(journal_env):
    _seed_closed_trade(journal_env, "acc3", -40.0)  # under the 100 cap
    rm = RiskManager({"daily_usd": 100.0, "max_dd_pct": 0.99}, account_id="acc3")

    ok, reason = rm.evaluate(_order())
    assert ok is True
    assert reason is None


def test_state_survives_restart(journal_env):
    _seed_closed_trade(journal_env, "acc4", -75.0)
    first = RiskManager({"daily_usd": 100.0}, account_id="acc4")
    assert first.daily_pnl == pytest.approx(-75.0)

    # A fresh instance (process "restart") rebuilds the same state.
    second = RiskManager({"daily_usd": 100.0}, account_id="acc4")
    assert second.daily_pnl == pytest.approx(-75.0)


def test_missing_journal_is_best_effort(tmp_path, monkeypatch):
    """A journal path that can't be queried for trades never crashes the
    manager; daily_pnl stays at the in-memory default."""
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "nonexistent.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "empty"))
    rm = RiskManager({"daily_usd": 100.0}, account_id="acc5")
    assert rm.daily_pnl == 0.0  # no trades table to read → in-memory default


def test_empty_account_id_disables_persistence(journal_env):
    """account_id="" (tests / one-off callers) writes no row and never
    recomputes — the in-memory contract is preserved."""
    _seed_closed_trade(journal_env, "live", -500.0)
    rm = RiskManager({"daily_usd": 100.0}, account_id="")
    assert rm.daily_pnl == 0.0
    rm.record_trade_result(-30.0)
    assert rm.daily_pnl == pytest.approx(-30.0)  # pure in-memory
    assert _read_daily_risk_rows(journal_env, "") == []
