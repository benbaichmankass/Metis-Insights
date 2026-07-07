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
from datetime import datetime, timedelta, timezone
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
    """Tmp trade journal + stubbed account cfg loader."""
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    # Close-on-disappear requires a SECOND confirming absent read
    # (BL-20260614-ORPHANBLIP). Set the time window to 0 so two back-to-back
    # ticks in a test confirm immediately — the 2-observation requirement still
    # holds (one absent tick arms, the next closes). The standing per-process
    # confirm-cache must also be cleared so trade ids reused across tests don't
    # carry stale arming.
    monkeypatch.setenv("RECONCILER_CLOSE_CONFIRM_SECONDS", "0")
    import src.runtime.order_monitor as _om
    _om._PENDING_ORPHAN_DISAPPEAR_CONFIRM.clear()
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


def _insert_orphan_adopt(db, *, symbol="BTCUSDT", direction="long",
                         account_id="bybit_2", size=0.003, entry=80725.9):
    """Insert an open ``orphan_adopt`` row (no strategy attribution)."""
    db.insert_trade({
        "timestamp": "2026-06-15T07:00:00+00:00",
        "symbol": symbol, "direction": direction, "entry_price": entry,
        "position_size": size, "setup_type": "adopted_orphan", "status": "open",
        "is_backtest": 0, "strategy_name": "orphan_adopt",
        "account_id": account_id, "notes": "{}",
    })


# ────────────────────────────────────────────────────────────────────
# Gate behaviour
# ────────────────────────────────────────────────────────────────────


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


def test_orphan_position_policy_defaults_to_adopt(monkeypatch):
    # Operator directive 2026-06-24: adopt (resolve) is the code default, never
    # the resting detect_only — a dropped .env var must not regress to alert-only.
    monkeypatch.delenv("ORPHAN_POSITION_POLICY", raising=False)
    assert _orphan_position_policy() == "adopt"


def test_orphan_position_policy_accepts_valid_values(monkeypatch):
    for v in ("detect_only", "adopt", "close"):
        monkeypatch.setenv("ORPHAN_POSITION_POLICY", v)
        assert _orphan_position_policy() == v


def test_orphan_position_policy_rejects_invalid_with_fallback(monkeypatch):
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "delete_everything")
    # Fallback is adopt (resolve), not the resting detect_only.
    assert _orphan_position_policy() == "adopt"


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


# ────────────────────────────────────────────────────────────────────
# Close-on-disappear — adopted_orphan rows whose exchange position
# is no longer reported by Bybit get their journal row closed.
#
# Motivating gap (operator question, 2026-05-11): the forward
# reconciler (_reconcile_open_trades) skips rows with no numeric
# trade_id in notes — which is every adopted_orphan row. So a
# row adopted in one tick would never close even after the
# exchange position is gone. The reverse reconciler is the only
# place we have both the journal-open set AND the live exchange-
# position set, so the close pass lives here.
# ────────────────────────────────────────────────────────────────────


def _adopt_via_reverse(tmp_db, monkeypatch, position):
    """Test helper: drive one tick that adopts the given Bybit position."""
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "adopt")
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: None,
    )
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[position],
    ):
        _reconcile_orphan_exchange_positions(tmp_db)


def test_close_disappear_closes_adopted_when_position_gone(tmp_db, monkeypatch):
    """Adopt a position in tick 1; tick 2 sees Bybit return [] → ARMED only
    (close-confirm, BL-20260614-ORPHANBLIP), still open; tick 3 sees [] again
    → the adopted row is closed with exit_reason='adopted_orphan_disappeared'.
    exit_price stays NULL because we don't have a Bybit-side fill for an
    order we never placed."""
    _adopt_via_reverse(tmp_db, monkeypatch, _bybit_position())
    assert _open_trade_count(tmp_db) == 1

    # First absent read — arms the close-confirm, does NOT close.
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        armed = _reconcile_orphan_exchange_positions(tmp_db)
    assert armed["pending_disappear"] == 1
    assert armed["closed_disappeared"] == 0
    assert _open_trade_count(tmp_db) == 1

    # Second confirming absent read — now closes.
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)

    assert summary["closed_disappeared"] == 1
    assert summary["errors"] == 0
    assert _open_trade_count(tmp_db) == 0

    conn = tmp_db.connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            "SELECT * FROM trades WHERE strategy_name='orphan_adopt'"
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "closed"
    assert row["exit_reason"] == "adopted_orphan_disappeared"
    assert row["exit_price"] is None
    notes = json.loads(row["notes"])
    assert notes["closed_by"] == "reverse_reconciler"
    assert "closed_at" in notes


def test_close_disappear_leaves_matched_open(tmp_db, monkeypatch):
    """Adopt a position, then the same position is still reported on the
    next tick — the adopted row stays open, no spurious close."""
    pos = _bybit_position()
    _adopt_via_reverse(tmp_db, monkeypatch, pos)

    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[pos],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)

    assert summary["closed_disappeared"] == 0
    assert _open_trade_count(tmp_db) == 1


def test_close_disappear_partial(tmp_db, monkeypatch):
    """Two adopted orphans; one's position is still on Bybit, one's is
    gone — only the disappeared one closes."""
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "adopt")
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: None,
    )
    # Tick 1: both positions present → both get adopted in one pass.
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[
            _bybit_position(symbol="BTCUSDT", side="Buy", size=0.003, entry=80725.9),
            _bybit_position(symbol="ETHUSDT", side="Sell", size=0.1, entry=2500.5),
        ],
    ):
        _reconcile_orphan_exchange_positions(tmp_db)
    assert _open_trade_count(tmp_db) == 2

    # Tick 2 + 3: only BTCUSDT remains on Bybit; ETHUSDT short is gone. The
    # first absent read arms the close-confirm; the second confirms + closes
    # (BL-20260614-ORPHANBLIP). BTCUSDT stays open throughout (still reported).
    btc_only = [_bybit_position(
        symbol="BTCUSDT", side="Buy", size=0.003, entry=80725.9,
    )]
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=btc_only,
    ):
        armed = _reconcile_orphan_exchange_positions(tmp_db)
        assert armed["pending_disappear"] == 1
        assert armed["closed_disappeared"] == 0
        assert _open_trade_count(tmp_db) == 2
        summary = _reconcile_orphan_exchange_positions(tmp_db)

    assert summary["closed_disappeared"] == 1
    assert _open_trade_count(tmp_db) == 1
    # The remaining open row must be the BTCUSDT one.
    conn = tmp_db.connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            "SELECT symbol FROM trades WHERE status='open'"
        ).fetchone()
    finally:
        conn.close()
    assert row["symbol"] == "BTCUSDT"


def test_close_disappear_does_not_touch_non_adopted_rows(tmp_db, monkeypatch):
    """The forward reconciler handles vwap / turtle_soup rows via their
    trade_id. close-on-disappear must NOT close those even if the
    exchange position is missing — a real strategy-owned trade with a
    momentarily missing position read should be left for the forward
    reconciler's per-order-id check."""
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "adopt")
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: None,
    )
    # Real vwap trade — not an adopted_orphan.
    _insert_open_trade(tmp_db, symbol="BTCUSDT", direction="long")

    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)

    assert summary["closed_disappeared"] == 0
    assert _open_trade_count(tmp_db) == 1


def test_blip_then_recover_does_not_close_adopted(tmp_db, monkeypatch):
    """The MHG adopt→close→re-adopt churn (BL-20260614-ORPHANBLIP): a
    logged-out IB Gateway can return an EMPTY portfolio ([], not a read
    failure) for one tick, then report the position again. A single empty
    read must only ARM the close-confirm; when the position reads back open
    the next tick, the pending close is cleared and the adopted row survives
    — no spurious close, no re-orphan."""
    pos = _bybit_position()
    _adopt_via_reverse(tmp_db, monkeypatch, pos)
    assert _open_trade_count(tmp_db) == 1

    # Tick 2: blip — exchange returns [] (connected but no portfolio yet).
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        armed = _reconcile_orphan_exchange_positions(tmp_db)
    assert armed["pending_disappear"] == 1
    assert armed["closed_disappeared"] == 0
    assert _open_trade_count(tmp_db) == 1

    # Tick 3: gateway recovers — position reported again. Pending close clears.
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[pos],
    ):
        recovered = _reconcile_orphan_exchange_positions(tmp_db)
    assert recovered["closed_disappeared"] == 0
    assert recovered["pending_disappear"] == 0
    assert recovered["adopted"] == 0  # not re-adopted — the row is still there
    assert _open_trade_count(tmp_db) == 1

    # Tick 4: a fresh single absent read must ARM again (the prior arming was
    # cleared by the recovery), not close immediately.
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        rearmed = _reconcile_orphan_exchange_positions(tmp_db)
    assert rearmed["pending_disappear"] == 1
    assert rearmed["closed_disappeared"] == 0
    assert _open_trade_count(tmp_db) == 1


def test_position_read_failure_does_not_close_adopted(tmp_db, monkeypatch):
    """account_open_positions returns None on a transient creds failure;
    the close pass must NOT fire (otherwise a single missed read would
    eat an adopted orphan that's still very much alive on Bybit)."""
    _adopt_via_reverse(tmp_db, monkeypatch, _bybit_position())
    assert _open_trade_count(tmp_db) == 1

    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=None,
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)

    assert summary["closed_disappeared"] == 0
    assert _open_trade_count(tmp_db) == 1


def test_close_disappear_idempotent(tmp_db, monkeypatch):
    """After a close-on-disappear close, a further tick with the same empty
    positions list is a no-op (the row is now status='closed' and the close
    query filters status='open'). Tick 1 arms, tick 2 closes, tick 3 no-op."""
    _adopt_via_reverse(tmp_db, monkeypatch, _bybit_position())
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        _reconcile_orphan_exchange_positions(tmp_db)   # arm
        closed = _reconcile_orphan_exchange_positions(tmp_db)   # close
        summary3 = _reconcile_orphan_exchange_positions(tmp_db)  # no-op
    assert closed["closed_disappeared"] == 1
    assert summary3["closed_disappeared"] == 0
    assert summary3["pending_disappear"] == 0
    assert summary3["errors"] == 0


# ────────────────────────────────────────────────────────────────────
# Exit-coverage reattach-or-close (2026-06-15)
# ────────────────────────────────────────────────────────────────────


def test_unattributable_alive_orphan_is_flattened(tmp_db, monkeypatch):
    """An orphan_adopt row that is STILL ALIVE on the exchange but has no
    recoverable order package has no rational exit strategy → it is flattened
    (reattach-or-close), confirmed across a 2nd observation."""
    import src.runtime.order_monitor as _om
    _om._PENDING_ORPHAN_NOSTRAT_CLOSE.clear()
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "detect_only")
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_trade_close", lambda **k: None
    )
    closes: list = []
    monkeypatch.setattr(
        _om, "_send_close_to_exchange",
        lambda mt: closes.append(mt) or {"ok": True, "skipped": None},
    )
    _insert_orphan_adopt(tmp_db, symbol="BTCUSDT", direction="long")

    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position(symbol="BTCUSDT", side="Buy")],
    ):
        s1 = _reconcile_orphan_exchange_positions(tmp_db)   # alive + no pkg → arm
        assert _open_trade_count(tmp_db) == 1               # not closed yet
        assert s1.get("resolved_pending_close") == 1
        assert not closes
        s2 = _reconcile_orphan_exchange_positions(tmp_db)   # confirmed → flatten

    assert len(closes) == 1 and closes[0]["symbol"] == "BTCUSDT"
    conn = tmp_db.connect()
    try:
        row = conn.execute(
            "SELECT status, exit_reason FROM trades WHERE setup_type='adopted_orphan'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "closed" and row[1] == "exit_coverage_no_strategy"
    assert s2.get("resolved_closed") == 1


def test_alive_orphan_with_recoverable_package_reattached_not_flattened(tmp_db, monkeypatch):
    """If a live order package exists, the alive orphan is REATTACHED at the top
    of the pass (regains its strategy) and is never flattened."""
    import src.runtime.order_monitor as _om
    _om._PENDING_ORPHAN_NOSTRAT_CLOSE.clear()
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "detect_only")
    closes: list = []
    monkeypatch.setattr(
        _om, "_send_close_to_exchange",
        lambda mt: closes.append(mt) or {"ok": True},
    )
    _insert_orphan_adopt(tmp_db, symbol="BTCUSDT", direction="long",
                         entry=80725.9, size=0.003)
    # A recoverable package (same symbol+dir, entry within tolerance).
    tmp_db.insert_order_package({
        "order_package_id": "op-btc", "strategy_name": "trend_donchian",
        "symbol": "BTCUSDT", "direction": "long", "entry": 80700.0,
        "sl": 79000.0, "tp": 83000.0, "status": "closed",
        "created_at": "2026-06-15T06:00:00Z",
    })

    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position(symbol="BTCUSDT", side="Buy")],
    ):
        _reconcile_orphan_exchange_positions(tmp_db)
        _reconcile_orphan_exchange_positions(tmp_db)

    assert not closes  # reattached, never flattened
    conn = tmp_db.connect()
    try:
        strat = conn.execute(
            "SELECT strategy_name, status FROM trades WHERE setup_type='adopted_orphan'"
        ).fetchone()
    finally:
        conn.close()
    assert strat[0] == "trend_donchian" and strat[1] == "open"


# ────────────────────────────────────────────────────────────────────
# Re-adopt flap guard (BL-20260618-RECONCILE-DUP)
# ────────────────────────────────────────────────────────────────────
# One MGC position was adopted, the re-attached strategy's monitor closed the
# DB row at an sl_cross (the IB exchange position itself never closed), and the
# still-present exchange position was RE-ADOPTED next pass — 18 times, booking
# -$20,127 of phantom losses. The guard refuses to re-adopt a (account, symbol,
# direction) whose adopted_orphan closed within RECONCILER_READOPT_GUARD_SECONDS.

def _insert_closed_adopted_orphan(db, *, symbol="MGCUSD", direction="long",
                                  account_id="bybit_2", entry=4318.872,
                                  size=8.0, closed_at):
    """Insert a CLOSED adopted_orphan row with an explicit closed_at."""
    db.insert_trade({
        "timestamp": "2026-06-17T18:40:00+00:00",
        "symbol": symbol, "direction": direction, "entry_price": entry,
        "position_size": size, "setup_type": "adopted_orphan", "status": "closed",
        "exit_reason": "sl_cross", "pnl": -1100.0, "closed_at": closed_at,
        "is_backtest": 0, "strategy_name": "mgc_trend_1h",
        "account_id": account_id, "notes": '{"adopted_by": "reverse_reconciler"}',
    })


def test_readopt_guard_suppresses_flapping_position(tmp_db, monkeypatch):
    """An exchange position matching an adopted_orphan that closed seconds ago
    is a flap — it must NOT be re-adopted; suppressed + alerted instead."""
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "adopt")
    monkeypatch.setenv("RECONCILER_READOPT_GUARD_SECONDS", "300")
    enqueued: list = []
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: enqueued.append(kw),
    )
    # An adopted_orphan that closed 10s ago (well inside the 300s guard window).
    recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    _insert_closed_adopted_orphan(tmp_db, closed_at=recent)

    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position(
            symbol="MGCUSD", side="Buy", size=8.0, entry=4318.872,
        )],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)

    assert summary["readopt_suppressed"] == 1
    assert summary["adopted"] == 0
    # NO new open row created — the whole point: one position, not N phantom rows.
    assert _open_trade_count(tmp_db) == 0
    # Operator still gets an alert (detect_only) so the real exchange position
    # isn't silently dropped.
    assert len(enqueued) == 1
    assert enqueued[0]["policy"] == "detect_only"
    assert "re-adopt suppressed" in (enqueued[0]["note"] or "")


def test_readopt_guard_stale_close_allows_adopt(tmp_db, monkeypatch):
    """A genuinely-new position (the prior adopted_orphan closed long ago,
    outside the guard window) is adopted normally — the guard is not a
    permanent block on a symbol."""
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "adopt")
    monkeypatch.setenv("RECONCILER_READOPT_GUARD_SECONDS", "300")
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: None,
    )
    stale = (datetime.now(timezone.utc) - timedelta(seconds=3600)).isoformat()
    _insert_closed_adopted_orphan(tmp_db, closed_at=stale)

    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position(
            symbol="MGCUSD", side="Buy", size=8.0, entry=4318.872,
        )],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)

    assert summary["readopt_suppressed"] == 0
    assert summary["adopted"] == 1
    assert _open_trade_count(tmp_db) == 1


def test_readopt_guard_disabled_with_zero_window(tmp_db, monkeypatch):
    """RECONCILER_READOPT_GUARD_SECONDS=0 disables the guard (legacy
    re-adopt-immediately behaviour) — a recently-closed match is re-adopted."""
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "adopt")
    monkeypatch.setenv("RECONCILER_READOPT_GUARD_SECONDS", "0")
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: None,
    )
    recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    _insert_closed_adopted_orphan(tmp_db, closed_at=recent)

    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position(
            symbol="MGCUSD", side="Buy", size=8.0, entry=4318.872,
        )],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)

    assert summary["readopt_suppressed"] == 0
    assert summary["adopted"] == 1


def _insert_closed_strategy_trade(db, *, symbol="SLV", direction="short",
                                   account_id="bybit_2", entry=53.94,
                                   size=1360.0, exit_reason, closed_at,
                                   setup_type="slv_trend_1h"):
    """Insert a CLOSED trade that still carries its ORIGINAL strategy
    setup_type (never reclassified to 'adopted_orphan') — the shape a
    position_snapshot_reconciler / exit_coverage_resolver close produces."""
    db.insert_trade({
        "timestamp": "2026-07-07T20:27:00+00:00",
        "symbol": symbol, "direction": direction, "entry_price": entry,
        "position_size": size, "setup_type": setup_type, "status": "closed",
        "exit_reason": exit_reason, "pnl": -693.6, "closed_at": closed_at,
        "is_backtest": 0, "strategy_name": setup_type,
        "account_id": account_id,
        "notes": '{"pnl_source": "local_compute", "exit_price_source": "local_markprice"}',
    })


# BL-20260707-ALPACA-CLOSE-NOT-CONFIRMED-FLAT: the guard above only matched
# setup_type='adopted_orphan' — a STRATEGY-ATTRIBUTED row phantom-closed by
# position_snapshot_reconciler or exit_coverage_resolver (still carrying its
# original strategy's setup_type) fell straight through with zero flap
# protection, letting the SLV incident re-adopt 7 minutes after a phantom
# close with no suppression at all.

def test_readopt_guard_suppresses_phantom_closed_strategy_row_exchange_flat_reconciled(
    tmp_db, monkeypatch,
):
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "adopt")
    monkeypatch.setenv("RECONCILER_READOPT_GUARD_SECONDS", "300")
    enqueued: list = []
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: enqueued.append(kw),
    )
    recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    _insert_closed_strategy_trade(
        tmp_db, exit_reason="exchange_flat_reconciled", closed_at=recent,
    )

    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position(
            symbol="SLV", side="Sell", size=1360.0, entry=53.94,
        )],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)

    assert summary["readopt_suppressed"] == 1
    assert summary["adopted"] == 0
    assert _open_trade_count(tmp_db) == 0
    assert len(enqueued) == 1
    assert enqueued[0]["policy"] == "detect_only"


def test_readopt_guard_suppresses_phantom_closed_strategy_row_exit_coverage(
    tmp_db, monkeypatch,
):
    """Same guard, the OTHER phantom-close source (exit_coverage_resolver) —
    the exact SLV incident: a 'no recoverable strategy' close that fabricates
    a PnL without confirming the flatten actually happened."""
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "adopt")
    monkeypatch.setenv("RECONCILER_READOPT_GUARD_SECONDS", "300")
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: None,
    )
    recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    _insert_closed_strategy_trade(
        tmp_db, exit_reason="exit_coverage_no_strategy", closed_at=recent,
        setup_type="orphan_adopt",  # exit-coverage closes carry this setup_type
    )

    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position(
            symbol="SLV", side="Sell", size=1360.0, entry=53.94,
        )],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)

    assert summary["readopt_suppressed"] == 1
    assert summary["adopted"] == 0


def test_readopt_guard_does_not_suppress_a_genuine_strategy_exit(tmp_db, monkeypatch):
    """The widening must stay NARROW: a normal, broker-CONFIRMED strategy exit
    (sl_cross/tp_cross — not a reconciler best-guess close) must NOT block a
    legitimate new position on the same symbol/direction shortly after."""
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "adopt")
    monkeypatch.setenv("RECONCILER_READOPT_GUARD_SECONDS", "300")
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_exchange_orphan_adoption",
        lambda **kw: None,
    )
    recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    _insert_closed_strategy_trade(
        tmp_db, exit_reason="sl_cross", closed_at=recent,
    )

    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position(
            symbol="SLV", side="Sell", size=1360.0, entry=53.94,
        )],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)

    assert summary["readopt_suppressed"] == 0
    assert summary["adopted"] == 1


def test_mark_orphaned_fires_orphan_red_flag(monkeypatch):
    """Operator directive: a row entering status='orphaned' via the forward
    reconciler must fire the orphan red-flag (durable log + /system-review ping),
    same as the reverse-reconciler adopt path."""
    from src.runtime import order_monitor as _om
    from src.runtime import execution_diagnostics as _ed

    calls = []
    monkeypatch.setattr(_ed, "enqueue_orphan_created_flag",
                        lambda **kw: calls.append(kw))
    # Skip the package cascade so the fake db needs only update_trade.
    monkeypatch.setattr(_om, "_resolve_linked_package_id", lambda db, tid: None)

    class _DB:
        def update_trade(self, tid, updates):
            return None

    _om._mark_orphaned(_DB(), {
        "id": 99, "account_id": "ib_paper", "symbol": "MHG",
        "direction": "long", "notes": None,
    })

    assert len(calls) == 1
    c = calls[0]
    assert c["account"] == "ib_paper" and c["symbol"] == "MHG"
    assert c["side"] == "long" and c["trade_id"] == 99
    assert c["origin"] == "forward_reconciler_orphaned"


def test_adopt_stamps_reconcile_status(tmp_db, monkeypatch):
    """A bare adopt (no recoverable order package) → reconcile_status
    'unreconciled' (the red-flag state to resolve, item #4)."""
    monkeypatch.setenv("ORPHAN_POSITION_POLICY", "adopt")
    # Don't write real orphan_events / pings from the red-flag hook.
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_orphan_created_flag",
        lambda **kw: None,
    )
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[_bybit_position()],
    ):
        summary = _reconcile_orphan_exchange_positions(tmp_db)
    assert summary["adopted"] == 1
    conn = tmp_db.connect()
    try:
        row = conn.execute(
            "SELECT reconcile_status, strategy_name FROM trades "
            "WHERE status='open'"
        ).fetchone()
    finally:
        conn.close()
    assert row[1] == "orphan_adopt"           # no package recovered → bare
    assert row[0] == "unreconciled"           # explicit red-flag terminal
