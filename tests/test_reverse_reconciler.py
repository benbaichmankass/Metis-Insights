"""Reverse reconciler — Bybit→DB orphan adoption.

Pins the contract for
``src.runtime.order_monitor._reconcile_orphan_exchange_positions``:
for every exchange-side open position, the DB must have a matching
``trades.status='open'`` row; when it doesn't, the row is either
inserted (``ORPHAN_POSITION_POLICY=adopt``) or alerted
(``detect_only``). Pairs with ``test_monitor_reconciler.py`` which
covers the forward direction (DB-open trade closed exchange-side).

Motivating incident (2026-05-11): trade 1145 (BTCUSDT bybit_2 vwap
LONG) stayed live on Bybit while the journal row vanished — the
forward reconciler couldn't see it because there was no DB row to
walk from.
"""
from __future__ import annotations

import json
import textwrap
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.runtime.order_monitor import (
    _adopt_orphan_position,
    _orphan_position_policy,
    _reconcile_orphan_exchange_positions,
)
from src.units.db.database import Database


_CFGS = {
    "bybit_2": {
        "account_id": "bybit_2",
        "exchange": "bybit",
        "api_key_env": "BYBIT_KEY_2",
        "api_secret_env": None,
        "mode": "live",
    },
    "bybit_dry": {
        "account_id": "bybit_dry",
        "exchange": "bybit",
        "api_key_env": "BYBIT_KEY_DRY",
        "api_secret_env": None,
        "mode": "dry_run",
    },
}


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Tmp trade journal + reconcile-enabled env + stubbed account cfg loader."""
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
    db = Database(db_path=str(db_path))

    def _fake_cfgs():
        return _CFGS

    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        _fake_cfgs,
    )
    # Stub the alert enqueue — we don't want tests to write to the
    # real PENDING_PINGS_DIR.
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: None,
    )
    yield db


def _insert_open_trade(db, *, symbol, direction, account_id="bybit_2"):
    """Insert a status='open' trade row — minimal columns."""
    db.insert_trade({
        "timestamp": "2026-05-11T07:17:27+00:00",
        "symbol": symbol,
        "direction": direction,
        "entry_price": 80725.9,
        "position_size": 0.003,
        "setup_type": "vwap",
        "status": "open",
        "is_backtest": 0,
        "strategy_name": "vwap",
        "account_id": account_id,
        "notes": "{}",
    })


def _open_trade_count(db) -> int:
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='open'"
        ).fetchone()[0]
    finally:
        conn.close()


def _bybit_position(symbol="BTCUSDT", side="Buy", size=0.003, entry=80725.9):
    """Match the shape ``account_open_positions`` returns for Bybit."""
    return {
        "symbol": symbol,
        "side": side,
        "size": size,
        "entry_price": entry,
        "unrealised_pnl": 0.0,
    }


# ────────────────────────────────────────────────────────────────────
# Gate behaviour
# ────────────────────────────────────────────────────────────────────


def test_reverse_reconciler_noop_when_disabled(tmp_db, monkeypatch):
    """MONITOR_RECONCILE_ENABLED=false → returns zero-counts dict, makes
    no exchange call, mutates nothing."""
    monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "false")
    with patch(
        "src.units.accounts.clients.account_open_positions"
    ) as mock_positions:
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["checked_accounts"] == 0
    assert summary["orphans_found"] == 0
    assert summary["adopted"] == 0
    mock_positions.assert_not_called()


def test_reverse_reconciler_skips_dry_accounts(tmp_db):
    """Dry-run accounts have no real exchange positions — must skip the
    fetch entirely so we don't hit Bybit with no-creds clients."""
    calls = []

    def _fake_positions(account):
        calls.append(account.get("account_id"))
        return []

    with patch(
        "src.units.accounts.clients.account_open_positions",
        side_effect=_fake_positions,
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    # bybit_dry has mode=dry_run; must be skipped. bybit_2 has mode=live;
    # must be polled (and return []).
    assert "bybit_dry" not in calls
    assert "bybit_2" in calls
    assert summary["checked_accounts"] == 1


def test_position_read_failure_does_not_create_orphan(tmp_db):
    """account_open_positions returns None on credential failure — that
    must NOT be treated as 'no positions, hence no orphans'; it just
    skips the account so a transient creds-read can't trigger an
    adopt."""
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=None,
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["orphans_found"] == 0
    assert summary["adopted"] == 0
    assert _open_trade_count(tmp_db) == 0


# ────────────────────────────────────────────────────────────────────
# Match / orphan resolution
# ────────────────────────────────────────────────────────────────────


def test_position_with_matching_db_row_is_not_orphan(tmp_db):
    """Bybit has a position, DB has a matching status='open' row →
    no orphan; counters reflect 1 position checked, 0 orphans."""
    _insert_open_trade(tmp_db, symbol="BTCUSDT", direction="long")
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position()],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["checked_positions"] == 1
    assert summary["orphans_found"] == 0
    assert summary["adopted"] == 0


def test_orphan_side_normalisation(tmp_db):
    """Bybit returns 'Buy' / 'Sell'; DB stores 'long' / 'short'. The
    matcher must canonicalise both sides — a Buy position WITH a
    matching 'long' DB row is NOT an orphan."""
    _insert_open_trade(tmp_db, symbol="ETHUSDT", direction="short")
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position(symbol="ETHUSDT", side="Sell")],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["orphans_found"] == 0


# ────────────────────────────────────────────────────────────────────
# Policy: detect_only
# ────────────────────────────────────────────────────────────────────


def test_detect_only_alerts_but_does_not_insert(tmp_db, monkeypatch):
    """ORPHAN_POSITION_POLICY=detect_only → orphan is counted + alert is
    enqueued, but no trade row is created."""
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "detect_only")
    enqueued: list = []
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: enqueued.append(kw),
    )
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position()],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["orphans_found"] == 1
    assert summary["detect_only"] == 1
    assert summary["adopted"] == 0
    assert _open_trade_count(tmp_db) == 0
    assert len(enqueued) == 1
    assert enqueued[0]["policy"] == "detect_only"
    assert enqueued[0]["db_trade_id"] is None


# ────────────────────────────────────────────────────────────────────
# Policy: adopt (the live-VM default per 2026-05-11 operator decision)
# ────────────────────────────────────────────────────────────────────


def test_adopt_inserts_trade_row_with_orphan_metadata(tmp_db, monkeypatch):
    """ORPHAN_POSITION_POLICY=adopt → insert a status='open' trade row
    with setup_type='adopted_orphan' + strategy_name='orphan_adopt';
    SL/TP fields stay NULL; entry/size come from Bybit. The alert
    payload carries the new trade_id."""
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "adopt")
    enqueued: list = []
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: enqueued.append(kw),
    )
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position(
            symbol="BTCUSDT", side="Buy", size=0.003, entry=80725.9,
        )],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["adopted"] == 1
    assert summary["orphans_found"] == 1
    assert _open_trade_count(tmp_db) == 1

    conn = tmp_db.connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            "SELECT * FROM trades WHERE status='open'"
        ).fetchone()
    finally:
        conn.close()
    assert row["symbol"] == "BTCUSDT"
    assert row["direction"] == "long"
    assert row["entry_price"] == pytest.approx(80725.9)
    assert row["position_size"] == pytest.approx(0.003)
    assert row["setup_type"] == "adopted_orphan"
    assert row["strategy_name"] == "orphan_adopt"
    assert row["account_id"] == "bybit_2"
    assert row["stop_loss"] is None
    assert row["take_profit_1"] is None
    assert row["is_backtest"] == 0
    notes = json.loads(row["notes"])
    assert notes["adopted_by"] == "reverse_reconciler"
    assert "adopted_at" in notes
    assert notes["exchange_entry_price"] == pytest.approx(80725.9)
    assert notes["exchange_size"] == pytest.approx(0.003)

    # Alert carries the new trade_id so the operator can grep it.
    assert len(enqueued) == 1
    assert enqueued[0]["policy"] == "adopt"
    assert enqueued[0]["db_trade_id"] == row["id"]


def test_adopt_idempotent_across_two_ticks(tmp_db, monkeypatch):
    """A second tick with the same exchange position must see the
    just-adopted DB row and NOT insert a duplicate."""
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "adopt")
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: None,
    )
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position()],
    ):
        _reconcile_orphan_exchange_positions(tmp_db)
        # Second tick — same fake position still open on Bybit.
        summary2 = _reconcile_orphan_exchange_positions(tmp_db)
    assert _open_trade_count(tmp_db) == 1
    assert summary2["orphans_found"] == 0
    assert summary2["adopted"] == 0


# ────────────────────────────────────────────────────────────────────
# Policy: close (stub — should behave like detect_only until wired)
# ────────────────────────────────────────────────────────────────────


def test_close_policy_falls_back_to_detect_only_with_note(tmp_db, monkeypatch):
    """The close path is deferred (Tier-3 sensitive — sends real
    orders). Until it lands, ORPHAN_POSITION_POLICY=close must NOT
    insert OR close; surface as detect_only with a note pointing at
    the implementation gap."""
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "close")
    enqueued: list = []
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: enqueued.append(kw),
    )
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position()],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["orphans_found"] == 1
    assert summary["closed"] == 0
    assert summary["adopted"] == 0
    assert summary["detect_only"] == 1
    assert _open_trade_count(tmp_db) == 0
    assert len(enqueued) == 1
    # Alert reports the effective policy (detect_only), not the
    # requested one — so the operator knows the close didn't fire.
    assert enqueued[0]["policy"] == "detect_only"
    assert "close path is not yet implemented" in (enqueued[0]["note"] or "")


# ────────────────────────────────────────────────────────────────────
# Env-var policy helper
# ────────────────────────────────────────────────────────────────────


def test_orphan_position_policy_defaults_to_detect_only(monkeypatch):
    monkeypatch.delenv("ORPHAN_POSITION_POLICY", raising=False)
    assert _orphan_position_policy() == "detect_only"


def test_orphan_position_policy_accepts_valid_values(monkeypatch):
    for v in ("detect_only", "adopt", "close"):
        monkeypatch.setenv("ORPHAN_POSITION_POLICY", v)
        assert _orphan_position_policy() == v


def test_orphan_position_policy_rejects_invalid_with_fallback(monkeypatch):
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "delete_everything")
    assert _orphan_position_policy() == "detect_only"


# ────────────────────────────────────────────────────────────────────
# _adopt_orphan_position direct contract
# ────────────────────────────────────────────────────────────────────


def test_adopt_orphan_position_writes_expected_columns(tmp_db):
    trade_id = _adopt_orphan_position(
        db=tmp_db,
        account_id="bybit_2",
        symbol="ETHUSDT",
        direction="short",
        size=0.1,
        entry_price=2500.5,
    )
    conn = tmp_db.connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            "SELECT * FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "open"
    assert row["direction"] == "short"
    assert row["symbol"] == "ETHUSDT"
    assert row["entry_price"] == pytest.approx(2500.5)
    assert row["position_size"] == pytest.approx(0.1)
    assert row["setup_type"] == "adopted_orphan"
    assert row["strategy_name"] == "orphan_adopt"
    assert row["entry_reason"] == "reverse_reconciler_adopted_orphan_position"
