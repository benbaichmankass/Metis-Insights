"""Live-path test for the options-lifecycle reconciler (Slice-4).

Pins ``order_monitor._reconcile_options_expiry_and_assignment``: an open
options-expression row whose underlying has a broker-confirmed expiry/assignment
event AND no remaining open option position is closed with activity-sourced PnL;
a still-live structure, a no-event row (position merely absent), and an equity
account are all left untouched. No network — a fake executor supplies the
activities + positions snapshots.
"""
from __future__ import annotations

import pytest

from src.runtime.order_monitor import _reconcile_options_expiry_and_assignment
from src.units.db.database import Database


_OPT_ACCT = {
    "exchange": "alpaca",
    "account_class": "paper",
    "mode": "live",
    "options": {"express_as": "debit_vertical", "max_loss_per_trade_usd": 60},
}
_EQ_ACCT = {"exchange": "alpaca", "account_class": "paper", "mode": "live"}
_ACCOUNTS = {"alpaca_options_paper": _OPT_ACCT, "alpaca_paper": _EQ_ACCT}


class _FakeExec:
    def __init__(self, activities, positions):
        self._activities = activities
        self._positions = positions

    def account_activities(self, *, activity_types=None, after=None, page_size=100):
        return {"retCode": 0, "result": self._activities}

    def option_positions(self):
        return self._positions


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    db = Database(db_path=str(db_path))
    monkeypatch.setattr(
        "src.config.accounts_loader.load_accounts_dict", lambda: dict(_ACCOUNTS),
    )
    # account_expresses_options resolves the canonical block; with the merged cfg
    # carrying the options block directly it does not need the canonical fallback.
    yield db


def _insert_open_options_row(db, *, symbol="SLV", account_id="alpaca_options_paper",
                             entry=0.60, size=2.0, strategy="slv_trend_1h"):
    db.insert_trade({
        "timestamp": "2026-06-01T07:00:00+00:00",
        "symbol": symbol,
        "direction": "long",
        "entry_price": entry,
        "position_size": size,
        "setup_type": strategy,
        "status": "open",
        "is_backtest": 0,
        "strategy_name": strategy,
        "account_id": account_id,
        "notes": "{}",
    })


def _row(db, account_id="alpaca_options_paper"):
    conn = db.connect()
    try:
        r = conn.execute(
            "SELECT * FROM trades WHERE account_id=? ORDER BY id DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _patch_exec(monkeypatch, activities, positions):
    monkeypatch.setattr(
        "src.runtime.order_monitor._options_executor_for",
        lambda cfg: _FakeExec(activities, positions),
    )


def test_concluded_structure_is_closed_with_activity_pnl(tmp_db, monkeypatch):
    _insert_open_options_row(tmp_db)
    # ITM expiry: long exercised +300, short assigned -100 → close_cash 200,
    # open_cost 0.60*100*2 = 120 → realized +80. No open option positions remain.
    acts = [
        {"id": "a1", "activity_type": "OPEXC", "symbol": "SLV260116C00025000", "net_amount": "300.00"},
        {"id": "a2", "activity_type": "OPASN", "symbol": "SLV260116C00027000", "net_amount": "-100.00"},
    ]
    _patch_exec(monkeypatch, acts, [])
    out = _reconcile_options_expiry_and_assignment(tmp_db)
    assert out["closed"] == 1
    row = _row(tmp_db)
    assert row["status"] == "closed"
    assert row["exit_reason"] == "options_expiry_assignment"
    assert row["pnl"] == 80.0
    assert "alpaca_activity" in (row["notes"] or "")


def test_worthless_expiry_closes_at_full_debit_loss(tmp_db, monkeypatch):
    _insert_open_options_row(tmp_db, entry=0.50, size=1.0)
    # An EXP event exists (so the structure is confirmed concluded) but carries no
    # cash for the legs → both expired worthless → lose the whole debit (-50).
    acts = [{"id": "e1", "activity_type": "EXP", "symbol": "SLV260116C00025000", "net_amount": "0"}]
    _patch_exec(monkeypatch, acts, [])
    out = _reconcile_options_expiry_and_assignment(tmp_db)
    assert out["closed"] == 1
    assert _row(tmp_db)["pnl"] == -50.0


def test_still_open_position_is_not_closed(tmp_db, monkeypatch):
    _insert_open_options_row(tmp_db)
    acts = [{"id": "a1", "activity_type": "OPEXC", "symbol": "SLV260116C00025000", "net_amount": "10"}]
    # Underlying STILL holds an open option leg → not concluded.
    _patch_exec(monkeypatch, acts, [{"symbol": "SLV260116C00027000"}])
    out = _reconcile_options_expiry_and_assignment(tmp_db)
    assert out["closed"] == 0
    assert _row(tmp_db)["status"] == "open"


def test_no_lifecycle_event_never_closes(tmp_db, monkeypatch):
    """Position absent but NO expiry/assignment event → row stays open (anti-incident)."""
    _insert_open_options_row(tmp_db)
    _patch_exec(monkeypatch, [], [])  # no activities, no positions
    out = _reconcile_options_expiry_and_assignment(tmp_db)
    assert out["closed"] == 0
    assert _row(tmp_db)["status"] == "open"


def test_read_failure_never_closes(tmp_db, monkeypatch):
    _insert_open_options_row(tmp_db)

    class _Broken:
        def account_activities(self, **kw):
            return {"retCode": 500, "retMsg": "boom"}

        def option_positions(self):
            return None  # read failure

    monkeypatch.setattr(
        "src.runtime.order_monitor._options_executor_for", lambda cfg: _Broken(),
    )
    out = _reconcile_options_expiry_and_assignment(tmp_db)
    assert out["closed"] == 0
    assert out["errors"] >= 1
    assert _row(tmp_db)["status"] == "open"


def test_equity_account_rows_untouched(tmp_db, monkeypatch):
    # An open row on the EQUITY account (no options block) must never be considered.
    _insert_open_options_row(tmp_db, account_id="alpaca_paper")
    acts = [{"id": "a1", "activity_type": "EXP", "symbol": "SLV260116C00025000", "net_amount": "0"}]
    _patch_exec(monkeypatch, acts, [])
    out = _reconcile_options_expiry_and_assignment(tmp_db)
    assert out["closed"] == 0
    assert _row(tmp_db, account_id="alpaca_paper")["status"] == "open"


def test_ambiguous_underlying_left_for_manual(tmp_db, monkeypatch):
    # Two open rows share SLV (two strategies) → activity cash can't be split → skip.
    _insert_open_options_row(tmp_db, strategy="slv_trend_1h")
    _insert_open_options_row(tmp_db, strategy="slv_pullback_1d")
    acts = [{"id": "a1", "activity_type": "EXP", "symbol": "SLV260116C00025000", "net_amount": "0"}]
    _patch_exec(monkeypatch, acts, [])
    out = _reconcile_options_expiry_and_assignment(tmp_db)
    assert out["closed"] == 0
    assert out["ambiguous"] == 2
